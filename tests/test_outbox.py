"""T-023 Gmail guard tests: temporary SQLite and a fake client, never live mail."""
from __future__ import annotations

import base64
import tempfile
import unittest
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest import mock

from pipeline import config, db, drafts, google_oauth, outbox, settings, verify

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
EMAIL = """---
application_id: 99
kind: email
mode: drafting_assisted
subject: Graduate programme follow-up
---
Dear Ms Recruiter,

I am writing to follow up on my application to Example LLP.

Kind regards,
Candidate Name

## Citations
- S1: greeting
- S2: C-0001
- S3: closing
"""


class FakeGmail:
    def __init__(self, *, account: str = "ali@example.test", fail: bool = False) -> None:
        self.account = account
        self.fail = fail
        self.writes: list[tuple[str, str]] = []
        self.profile_calls = 0

    def profile(self) -> dict[str, str]:
        self.profile_calls += 1
        return {"emailAddress": self.account}

    def write(self, action: str, raw: str) -> dict[str, str]:
        self.writes.append((action, raw))
        if self.fail:
            raise OSError("synthetic transport failure")
        return {"id": "fake-gmail-id"}


class OutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="careeros-outbox-test-")
        self.root = Path(self.tmp.name)
        self.old_apps = drafts.APPLICATIONS_DIR
        self.old_paused = config.PAUSED_FLAG
        drafts.APPLICATIONS_DIR = self.root / "applications"
        config.PAUSED_FLAG = self.root / "PAUSED"
        self.folder = drafts.APPLICATIONS_DIR / "0099-example-llp"
        (self.folder / "final").mkdir(parents=True)
        self.email_file = self.folder / "final" / "email-followup-r1.md"
        self.email_file.write_text(EMAIL, encoding="utf-8")
        self.conn = db.connect(self.root / "tracker.sqlite")
        db.migrate(self.conn)
        company_id = self.conn.execute("INSERT INTO companies (name) VALUES ('Example LLP')").lastrowid
        posting_id = self.conn.execute(
            "INSERT INTO postings (company_id, title) VALUES (?, 'Graduate Programme')", (company_id,)
        ).lastrowid
        self.conn.execute(
            "INSERT INTO applications (id, company_id, posting_id, role_title, status, drafting_mode) "
            "VALUES (99, ?, ?, 'Graduate Programme', 'approved', 'drafting_assisted')", (company_id, posting_id)
        )
        self.contact_id = self.conn.execute(
            "INSERT INTO contacts (company_id, name, email) VALUES (?, 'Ms Recruiter', 'recruiter@example.test')",
            (company_id,),
        ).lastrowid
        self.conn.commit()
        self.active = settings.Settings(data={
            "autonomy": {"level": 3, "overrides": {"send_emails": "auto"}},
            "vault": {"applications_email": "ali@example.test"},
            "caps": {"daily_emails": 1},
        })
        self.gmail = FakeGmail()
        self.ledger = {"C-0001": {"id": "C-0001", "status": "confirmed", "sensitivity": "safe",
                                   "text": "I applied to the Example LLP graduate programme."}}
        self.ledger_patch = mock.patch.object(verify, "load_ledger", return_value=self.ledger)
        self.ledger_patch.start()

    def tearDown(self) -> None:
        self.ledger_patch.stop()
        self.conn.close()
        drafts.APPLICATIONS_DIR = self.old_apps
        config.PAUSED_FLAG = self.old_paused
        self.tmp.cleanup()

    def dispatch(self, action: str = "send", **kwargs: object) -> int:
        return outbox.dispatch(
            99, self.contact_id, self.email_file, action=action, conn=self.conn,
            active=kwargs.pop("active", self.active), gmail=kwargs.pop("gmail", self.gmail),
            scopes=kwargs.pop("scopes", {google_oauth.GMAIL_MODIFY_SCOPE}), now=NOW,
            **kwargs,
        )

    def test_level_three_send_is_tracked_and_capped(self) -> None:
        row_id = self.dispatch()
        row = self.conn.execute("SELECT * FROM outbound_emails WHERE id=?", (row_id,)).fetchone()
        self.assertEqual((row["status"], row["recipient"], row["posting_id"]),
                         ("sent", "recruiter@example.test", 1))
        action, raw = self.gmail.writes[0]
        self.assertEqual(action, "send")
        message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        self.assertEqual(message["To"], "recruiter@example.test")
        self.assertEqual(message["From"], "ali@example.test")
        self.assertEqual(message["Subject"], "Graduate programme follow-up")
        self.assertNotIn("## Citations", message.get_content())
        with self.assertRaisesRegex(outbox.OutboxRefused, "daily email cap"):
            self.dispatch()
        self.assertEqual(len(self.gmail.writes), 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE type='outbox_sent'").fetchone()[0], 1)

    def test_level_two_can_draft_but_cannot_send(self) -> None:
        level_two = settings.Settings(data={
            "autonomy": {"level": 2, "overrides": {"send_emails": "never"}},
            "vault": {"applications_email": "ali@example.test"},
        })
        row_id = self.dispatch("draft", active=level_two)
        self.assertEqual(self.conn.execute("SELECT status FROM outbound_emails WHERE id=?", (row_id,)).fetchone()[0], "drafted")
        with self.assertRaisesRegex(outbox.OutboxRefused, "level 3"):
            self.dispatch("send", active=level_two)
        self.assertEqual([action for action, _ in self.gmail.writes], ["draft"])

    def test_default_sender_and_missing_scope_refuse_before_gmail(self) -> None:
        no_sender = settings.Settings(data={"autonomy": {"level": 3, "overrides": {"send_emails": "auto"}}})
        with self.assertRaises(outbox.OutboxRefused):
            self.dispatch(active=no_sender)
        with self.assertRaisesRegex(outbox.OutboxRefused, "modify consent"):
            self.dispatch(scopes=set())
        self.assertEqual(self.gmail.profile_calls, 0)

    def test_wrong_account_or_untracked_recipient_never_writes(self) -> None:
        wrong = FakeGmail(account="work@example.test")
        with self.assertRaisesRegex(outbox.OutboxRefused, "OAuth account"):
            self.dispatch(gmail=wrong)
        other_company = self.conn.execute("INSERT INTO companies (name) VALUES ('Other')").lastrowid
        other_contact = self.conn.execute(
            "INSERT INTO contacts (company_id, name, email) VALUES (?, 'Other Recruiter', 'other@example.test')",
            (other_company,),
        ).lastrowid
        self.conn.commit()
        with self.assertRaisesRegex(outbox.OutboxRefused, "not a contact"):
            outbox.dispatch(99, other_contact, self.email_file, action="send", conn=self.conn,
                            active=self.active, gmail=self.gmail, scopes={google_oauth.GMAIL_MODIFY_SCOPE}, now=NOW)
        self.assertEqual(wrong.writes, [])
        self.assertEqual(self.gmail.writes, [])

    def test_grounding_and_pause_fail_closed(self) -> None:
        config.PAUSED_FLAG.touch()
        with self.assertRaisesRegex(outbox.OutboxRefused, "paused"):
            self.dispatch()
        config.PAUSED_FLAG.unlink()
        self.email_file.write_text(EMAIL.replace("- S2: C-0001", ""), encoding="utf-8")
        with self.assertRaisesRegex(outbox.OutboxRefused, "grounding"):
            self.dispatch()
        self.assertEqual(self.gmail.writes, [])

    def test_proofread_only_requires_ali_authored_text(self) -> None:
        self.conn.execute("UPDATE applications SET drafting_mode='proofread_only' WHERE id=99")
        self.conn.commit()
        self.email_file.write_text(EMAIL.replace("mode: drafting_assisted", "mode: proofread_only"), encoding="utf-8")
        with self.assertRaisesRegex(outbox.OutboxRefused, "the candidate's own"):
            self.dispatch()
        self.email_file.write_text(
            EMAIL.replace("mode: drafting_assisted", "mode: proofread_only\ngenerated_by: ali"),
            encoding="utf-8",
        )
        self.assertIsInstance(self.dispatch(), int)

    def test_duplicate_content_does_not_write_twice_even_without_a_cap(self) -> None:
        uncapped = settings.Settings(data={
            "autonomy": {"level": 3, "overrides": {"send_emails": "auto"}},
            "vault": {"applications_email": "ali@example.test"},
            "caps": {"daily_emails": 10},
        })
        self.dispatch(active=uncapped)
        with self.assertRaisesRegex(outbox.OutboxRefused, "already has a tracker record"):
            self.dispatch(active=uncapped)
        self.assertEqual(len(self.gmail.writes), 1)

    def test_readonly_token_metadata_never_grants_modify(self) -> None:
        with mock.patch.object(google_oauth, "_load_token", return_value={
            "scope": google_oauth.GMAIL_READONLY_SCOPE + " " + google_oauth.CALENDAR_EVENTS_SCOPE,
            "scopes": [google_oauth.GMAIL_READONLY_SCOPE],
        }):
            self.assertNotIn(google_oauth.GMAIL_MODIFY_SCOPE, google_oauth.granted_scopes("ali@example.test"))

    def test_gmail_writer_uses_documented_endpoints_without_content_in_url(self) -> None:
        class FakeOAuth:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, dict[str, object]]] = []

            def json(self, method: str, url: str, **kwargs: object) -> dict[str, str]:
                self.calls.append((method, url, kwargs))
                return {"id": "fake-id"}

        oauth = FakeOAuth()
        client = outbox.GmailOutboxClient(oauth=oauth)
        client.write("draft", "raw-message")
        client.write("send", "raw-message")
        self.assertEqual([call[1] for call in oauth.calls], [
            "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        ])
        self.assertEqual(oauth.calls[0][2], {"json": {"message": {"raw": "raw-message"}}})
        self.assertEqual(oauth.calls[1][2], {"json": {"raw": "raw-message"}})

    def test_uncertain_send_is_logged_and_never_retried(self) -> None:
        failing = FakeGmail(fail=True)
        with self.assertRaises(outbox.OutboxUncertain):
            self.dispatch(gmail=failing)
        row = self.conn.execute("SELECT status FROM outbound_emails").fetchone()
        self.assertEqual(row[0], "uncertain")
        with self.assertRaises(outbox.OutboxRefused):
            self.dispatch(gmail=failing)
        self.assertEqual(len(failing.writes), 1)
