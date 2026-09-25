"""T-013/T-016: tests for pipeline.notify (notify, digest) and pipeline.settings.

Uses a throwaway SQLite database under a temp directory, the same convention as
tests/test_db.py: CAREEROS_LOCAL_DIR is set *before* pipeline.config is imported
anywhere in this process, so DB_PATH resolves inside it and the real
%LOCALAPPDATA%\\CareerOS is never touched. DISCORD_WEBHOOK_URL is force-set to an
empty string for the same reason -- tests must never make a real network call or
depend on whatever happens to be in the repo's real .env.

Run with:  python -m unittest tests.test_notify -v
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TEMP_DIR = tempfile.mkdtemp(prefix="careeros-test-")
os.environ["CAREEROS_LOCAL_DIR"] = _TEMP_DIR
os.environ["DISCORD_WEBHOOK_URL"] = ""  # falsy: _post_discord must treat this as "not set"

from pipeline import config, db, notify, settings  # noqa: E402 (must follow the env vars above)


class NotifyTestCase(unittest.TestCase):
    """Each test gets a freshly migrated database at config.DB_PATH."""

    def setUp(self) -> None:
        # Point every config path at the temp dir explicitly (import order must never matter).
        self.temp = Path(_TEMP_DIR)
        config.LOCAL_DIR = self.temp
        config.DB_PATH = self.temp / "test.sqlite"
        config.BACKUP_DIR = self.temp / "backups"
        config.LOGS_DIR = self.temp / "logs"
        config.PAUSED_FLAG = self.temp / "PAUSED"
        config.ensure_dirs()
        self.conn = db.connect(config.DB_PATH)
        db.migrate(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(config.DB_PATH) + suffix)
            if p.exists() and str(p).startswith(str(self.temp)):
                p.unlink()

    # -- notify() ------------------------------------------------------------

    def test_notify_with_no_webhook_still_writes_events_row(self) -> None:
        before = self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE type = 'notification'"
        ).fetchone()[0]

        # _toast is mocked so the test never pops a real Windows toast and behaves
        # the same whether or not this happens to run in an interactive session.
        with mock.patch.object(notify, "_toast", return_value=False) as mock_toast:
            result = notify.notify(
                "Test title", "Test body", level="urgent", channel="approvals", conn=self.conn
            )
        mock_toast.assert_called_once_with("Test title", "Test body")

        self.assertEqual(result, {"toast_sent": False, "discord_sent": False})

        after = self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE type = 'notification'"
        ).fetchone()[0]
        self.assertEqual(after, before + 1)

        row = self.conn.execute(
            "SELECT * FROM events WHERE type = 'notification' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(row["entity"], "notification")
        self.assertEqual(row["entity_id"], 0)
        self.assertEqual(row["source"], "system")
        self.assertIn("Test title", row["detail_json"])
        self.assertIn("urgent", row["detail_json"])

    def test_notify_rejects_unknown_level_or_channel(self) -> None:
        with self.assertRaises(ValueError):
            notify.notify("t", "b", level="not-a-level", conn=self.conn)
        with self.assertRaises(ValueError):
            notify.notify("t", "b", channel="not-a-channel", conn=self.conn)

    # -- digest() --------------------------------------------------------------

    def test_digest_handles_empty_database(self) -> None:
        text = notify.digest(conn=self.conn)
        self.assertIn("New matched postings (last 24h): 0", text)
        self.assertIn("(none yet)", text)
        self.assertIn("Assessments due within 7 days: 0", text)
        self.assertIn("Overdue next actions: 0", text)
        self.assertIn("Source failures: 0", text)
        self.assertIn("Cap usage this week: 0 / 25 applications submitted", text)

    def test_digest_sections_reflect_inserted_rows(self) -> None:
        company_id = db.upsert_company("Digest Co", conn=self.conn, priority=1)
        posting_id = db.upsert_posting(
            {
                "company_id": company_id,
                "title": "Graduate Analyst",
                "canonical_url": "https://example.com/jobs/digest-1",
                "status": "shortlisted",
                "track": "1-aca-acca-training-contracts",
            },
            conn=self.conn,
        )
        app_id = db.create_application(
            posting_id, company_id, "Graduate Analyst", "2027", "drafting_assisted", conn=self.conn
        )
        db.transition_application(app_id, "ready-for-review", "agent", conn=self.conn)

        self.conn.execute(
            "UPDATE applications SET next_action = ?, next_action_due = datetime('now', '-1 day') WHERE id = ?",
            ("Chase recruiter", app_id),
        )
        self.conn.execute(
            "INSERT INTO assessments (application_id, kind, status, deadline) "
            "VALUES (?, 'numerical', 'invited', datetime('now', '+2 days'))",
            (app_id,),
        )
        db.record_source_health("greenhouse", ok=False, note="HTTP 500", conn=self.conn)
        self.conn.commit()

        text = notify.digest(conn=self.conn)

        self.assertIn("New matched postings (last 24h): 1", text)
        self.assertIn("Graduate Analyst @ Digest Co", text)
        self.assertIn("ready-for-review: 1", text)
        self.assertIn("Assessments due within 7 days: 1", text)
        self.assertIn("numerical for Graduate Analyst @ Digest Co", text)
        self.assertIn("Overdue next actions: 1", text)
        self.assertIn("Chase recruiter", text)
        self.assertIn("Source failures: 1", text)
        self.assertIn("greenhouse", text)
        self.assertIn("Cap usage this week: 0 / 25 applications submitted", text)


class SettingsTestCase(unittest.TestCase):
    def test_accessors_read_example_file(self) -> None:
        s = settings.Settings(path=settings.EXAMPLE_SETTINGS_PATH)
        self.assertEqual(s.path, settings.EXAMPLE_SETTINGS_PATH)
        self.assertEqual(s.driver(), "claude")
        self.assertEqual(s.autonomy_level(), 1)
        self.assertEqual(s.override("submit_applications"), "ask")
        self.assertEqual(s.override("fill_identity_fields"), "auto")
        self.assertEqual(s.override("send_emails"), "never")
        self.assertEqual(s.override("an_action_not_listed_anywhere"), "ask")
        self.assertEqual(s.cap("weekly_applications"), 25)
        self.assertEqual(s.cap("per_employer_per_cycle"), 1)
        self.assertIsNone(s.cap("an_unknown_cap"))

    def test_default_construction_currently_falls_back_to_example_file(self) -> None:
        # settings.yaml is git-ignored (see .gitignore) and absent from this checkout,
        # so today Settings() with no path resolves the same as the explicit example.
        self.assertFalse(settings.SETTINGS_PATH.exists())
        default = settings.Settings()
        example = settings.Settings(path=settings.EXAMPLE_SETTINGS_PATH)
        self.assertEqual(default.path, settings.EXAMPLE_SETTINGS_PATH)
        self.assertEqual(default.driver(), example.driver())
        self.assertEqual(default.cap("weekly_applications"), example.cap("weekly_applications"))


if __name__ == "__main__":
    unittest.main()
