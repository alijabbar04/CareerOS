"""T-007: tests for pipeline.db and pipeline.backup.

Uses a throwaway SQLite database under a temp directory. CAREEROS_LOCAL_DIR is
set *before* pipeline.config is imported anywhere in this process, so every
path config.py derives from LOCAL_DIR (including DB_PATH) resolves inside it
and the real %LOCALAPPDATA%\\CareerOS is never touched.

Run with:  python -m unittest tests.test_db -v
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

_TEMP_DIR = tempfile.mkdtemp(prefix="careeros-test-")
os.environ["CAREEROS_LOCAL_DIR"] = _TEMP_DIR

from pipeline import backup, config, db  # noqa: E402 (must follow the env var above)


class DbTestCase(unittest.TestCase):
    """Each test gets a freshly migrated database at config.DB_PATH."""

    def setUp(self) -> None:
        # Never rely on import order: point every config path at the temp dir explicitly, so this test can
        # never touch the real %LOCALAPPDATA%\CareerOS database even if pipeline.config was imported earlier.
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

    def _make_source(self, source_id: str = "S-TEST") -> str:
        self.conn.execute(
            "INSERT INTO sources (id, path, kind) VALUES (?, ?, ?)",
            (source_id, "test.md", "note"),
        )
        self.conn.commit()
        return source_id

    # -- migrations ----------------------------------------------------

    def test_migrate_is_idempotent(self) -> None:
        applied_again = db.migrate(self.conn)
        self.assertEqual(applied_again, [])
        # One schema_migrations row per *.sql file under pipeline/migrations (not a
        # literal count, so this keeps passing as later tasks add more migrations).
        expected = len(list(config.MIGRATIONS_DIR.glob("*.sql")))
        count = self.conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        self.assertEqual(count, expected)

    # -- claims ----------------------------------------------------------

    def test_insert_and_search_claim(self) -> None:
        source_id = self._make_source()
        claim_id = db.insert_claim(
            {
                "text": "Raised AI classification accuracy from about 30% to about 94%.",
                "category": "achievement",
                "subject": "the current employer / document pipeline",
                "source_id": source_id,
                "locator": "linkedin-experience-2026-09-20",
                "quote": "Raised AI classification accuracy from ~30% to ~94%",
                "sensitivity": "free",
                "confidence": "user_asserted",
            },
            tags=["automation", "python"],
            conn=self.conn,
        )
        self.assertTrue(claim_id)

        found = db.search_claims("classification accuracy", conn=self.conn)
        self.assertTrue(any(row["id"] == claim_id for row in found))

        fetched = db.get_claim(claim_id, conn=self.conn)
        self.assertEqual(fetched["id"], claim_id)
        self.assertEqual(fetched["status"], "candidate")  # DEFAULT applied

        tags = {r[0] for r in self.conn.execute(
            "SELECT tag FROM claim_tags WHERE claim_id = ?", (claim_id,)
        ).fetchall()}
        self.assertEqual(tags, {"automation", "python"})

    def test_insert_claim_auto_assigns_sequential_id(self) -> None:
        source_id = self._make_source()
        base = {
            "category": "fact",
            "source_id": source_id,
            "locator": "loc",
            "sensitivity": "free",
            "confidence": "verified",
        }
        first = db.insert_claim({**base, "text": "First fact."}, conn=self.conn)
        second = db.insert_claim({**base, "text": "Second fact."}, conn=self.conn)
        self.assertEqual(first, "C-0001")
        self.assertEqual(second, "C-0002")

    # -- postings ----------------------------------------------------------

    def test_upsert_posting_twice_keeps_one_row(self) -> None:
        company_id = db.upsert_company("Test Co", conn=self.conn)
        posting = {
            "company_id": company_id,
            "title": "Graduate Analyst",
            "canonical_url": "https://example.com/jobs/123",
            "status": "new",
        }
        first_id = db.upsert_posting(posting, conn=self.conn)
        posting["title"] = "Graduate Analyst (updated)"
        second_id = db.upsert_posting(posting, conn=self.conn)

        self.assertEqual(first_id, second_id)
        count = self.conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
        self.assertEqual(count, 1)
        title = self.conn.execute(
            "SELECT title FROM postings WHERE id = ?", (first_id,)
        ).fetchone()[0]
        self.assertEqual(title, "Graduate Analyst (updated)")

    # -- application state machine -----------------------------------------

    def test_application_transitions_valid_and_invalid(self) -> None:
        company_id = db.upsert_company("Transition Co", conn=self.conn)
        app_id = db.create_application(
            None, company_id, "Graduate Analyst", "2027", "drafting_assisted", conn=self.conn
        )

        db.transition_application(app_id, "ready-for-review", "agent", conn=self.conn)
        db.transition_application(app_id, "approved", "ali", conn=self.conn)
        db.transition_application(app_id, "submitted", "ali", conn=self.conn)

        status = self.conn.execute(
            "SELECT status FROM applications WHERE id = ?", (app_id,)
        ).fetchone()[0]
        self.assertEqual(status, "submitted")

        # submitted -> offer skips assessment/interview: not an allowed edge.
        with self.assertRaises(sqlite3.IntegrityError):
            db.transition_application(app_id, "offer", "agent", conn=self.conn)

        # the rejected attempt must not have changed the status.
        status = self.conn.execute(
            "SELECT status FROM applications WHERE id = ?", (app_id,)
        ).fetchone()[0]
        self.assertEqual(status, "submitted")

    def test_events_row_written_on_transition(self) -> None:
        company_id = db.upsert_company("Events Co", conn=self.conn)
        app_id = db.create_application(
            None, company_id, "Graduate Analyst", "2027", "proofread_only", conn=self.conn
        )
        db.transition_application(
            app_id, "ready-for-review", "agent", detail={"note": "auto"}, conn=self.conn
        )

        row = self.conn.execute(
            "SELECT * FROM events WHERE entity = 'application' AND entity_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (app_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["from_status"], "draft")
        self.assertEqual(row["to_status"], "ready-for-review")
        self.assertEqual(row["source"], "agent")
        self.assertIn("auto", row["detail_json"])

    # -- backup ----------------------------------------------------------

    def test_backup_creates_file(self) -> None:
        backup_dir = Path(tempfile.mkdtemp(prefix="careeros-test-backup-"))
        dest = backup.backup(db_path=config.DB_PATH, backup_dir=backup_dir)
        self.assertTrue(dest.exists())
        self.assertIn(dest, list(backup_dir.glob("careeros-*.sqlite")))


if __name__ == "__main__":
    unittest.main()
