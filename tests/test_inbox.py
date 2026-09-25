"""T-015 unit tests. No Google, Ollama, Discord or Calendar traffic is made."""
from __future__ import annotations

import base64
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from pipeline import config, db, inbox


def _encoded(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _raw_message(
    message_id: str = "gmail-1",
    *,
    subject: str = "Invitation to complete your numerical assessment",
    body: str = "Acme invites you to complete the assessment by 25 September 2026.",
    sender: str = "Acme Early Careers <recruiting@acme.example>",
    labels: list[str] | None = None,
) -> dict[str, object]:
    return {
        "id": message_id,
        "threadId": "thread-1",
        "internalDate": "1789905600000",
        "labelIds": labels or ["INBOX"],
        "snippet": body[:80],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Sun, 20 Sep 2026 12:00:00 +0100"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _encoded(body)}},
                {"mimeType": "text/html", "body": {"data": _encoded(f"<p>{body}</p>")}},
            ],
        },
    }


class FakeGmail:
    def __init__(self, messages: list[dict[str, object]], *, history_expired: bool = False) -> None:
        self.messages = {str(item["id"]): item for item in messages}
        self.history_expired = history_expired
        self.list_queries: list[str] = []

    def profile(self) -> dict[str, str]:
        return {"emailAddress": "ali@example.com", "historyId": "200"}

    def list_message_ids(self, query: str) -> list[str]:
        self.list_queries.append(query)
        return list(self.messages)

    def history_message_ids(self, _start: str) -> tuple[list[str], str]:
        if self.history_expired:
            raise inbox.HistoryExpired("old")
        return list(self.messages), "201"

    def message(self, message_id: str) -> dict[str, object]:
        return self.messages[message_id]

    def attachment(self, _message_id: str, _attachment_id: str) -> str:
        raise AssertionError("fixture has no external attachment")


class FakeOAuth:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def json(self, method: str, url: str, **kwargs: object) -> dict[str, str]:
        self.calls.append((method, url, kwargs))
        return {"id": "calendar-123"}


class InboxTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="careeros-inbox-test-")
        self.root = Path(self.tmp.name)
        self.old_paths = {
            name: getattr(config, name)
            for name in ("LOCAL_DIR", "DB_PATH", "BACKUP_DIR", "LOGS_DIR", "INBOX_DIR", "PAUSED_FLAG")
        }
        config.LOCAL_DIR = self.root
        config.DB_PATH = self.root / "tracker.sqlite"
        config.BACKUP_DIR = self.root / "backups"
        config.LOGS_DIR = self.root / "logs"
        config.INBOX_DIR = self.root / "inbox"
        config.PAUSED_FLAG = self.root / "PAUSED"
        config.ensure_dirs()
        self.conn = db.connect(config.DB_PATH)
        db.migrate(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        for name, value in self.old_paths.items():
            setattr(config, name, value)
        self.tmp.cleanup()

    def _submitted_application(self) -> int:
        company_id = db.upsert_company("Acme", conn=self.conn, domain="acme.example")
        app_id = db.create_application(
            None, company_id, "Graduate Analyst", "2027", "drafting_assisted", conn=self.conn
        )
        for status in ("ready-for-review", "approved", "submitted"):
            db.transition_application(app_id, status, "test", conn=self.conn)
        return app_id

    def test_parse_multipart_prefers_plain_and_collects_link_domains(self) -> None:
        raw = _raw_message(body="Complete here: https://assess.hirevue.com/path")
        raw["payload"]["parts"][1]["body"]["data"] = _encoded(
            '<p>Complete here</p><a href="https://candidate.shl.com/start">Start</a>'
        )
        message = inbox.parse_gmail_message(raw)
        self.assertEqual(message.from_addr, "recruiting@acme.example")
        self.assertEqual(message.from_name, "Acme Early Careers")
        self.assertEqual(message.link_domains, ("assess.hirevue.com", "candidate.shl.com"))
        self.assertEqual(message.body.count("Complete here"), 1)

    def test_rules_distinguish_recorded_video_from_live_interview(self) -> None:
        recorded = inbox.parse_gmail_message(
            _raw_message(subject="Video interview", body="Please complete your on-demand video interview.")
        )
        live = inbox.parse_gmail_message(
            _raw_message(subject="Interview invitation", body="Please book a live Teams interview.")
        )
        self.assertEqual(inbox.classify_rules(recorded).category, "assessment_invite")
        self.assertEqual(inbox.classify_rules(live).category, "interview_invite")

    def test_deadline_parser_uses_uk_dates_and_relative_windows(self) -> None:
        received = "2026-09-20T11:00:00+00:00"
        due, phrase = inbox.extract_deadline("Complete by 25 September 2026 at 17:00", received)
        self.assertEqual(due, "2026-09-25T17:00:00+01:00")
        self.assertEqual(phrase, "25 September 2026")
        relative, relative_phrase = inbox.extract_deadline("Please finish within 48 hours", received)
        self.assertEqual(relative, "2026-09-22T12:00:00+01:00")
        self.assertEqual(relative_phrase, "within 48 hours")

    def test_double_extraction_flags_disagreement(self) -> None:
        message = inbox.parse_gmail_message(_raw_message(body="Complete by 25 September 2026."))

        def disagree(_message: inbox.InboxMessage, _model: str) -> dict[str, object]:
            return {
                "category": "assessment_invite",
                "employer": "Acme",
                "role": "Graduate Analyst",
                "vendor": "SHL",
                "deadline_iso": "2026-09-26T23:59:00+01:00",
                "deadline_phrase": "26 September 2026",
                "action_required": True,
                "confidence": 0.9,
            }

        result = inbox.classify_message(message, model_classifier=disagree)
        self.assertTrue(result.deadline_disagreement)
        self.assertEqual(result.deadline_confidence, "inferred")
        self.assertTrue(result.deadline_iso.startswith("2026-09-25"))

    def test_model_excerpt_removes_secret_lines_links_emails_and_codes(self) -> None:
        body = (
            "Assessment invitation.\nPassword: hunter2\nVerification code 123456\n"
            "Email ali@example.com and open https://example.com/token/abcdefghijklmno12345\n"
            "Complete by Friday."
        )
        message = inbox.parse_gmail_message(_raw_message(body=body))
        excerpt = inbox._model_excerpt(message)
        self.assertNotIn("hunter2", excerpt)
        self.assertNotIn("123456", excerpt)
        self.assertNotIn("ali@example.com", excerpt)
        self.assertNotIn("abcdefghijklmno12345", excerpt)
        self.assertIn("[redacted-secret-line]", excerpt)

    def test_verification_message_is_retained_as_a_non_action_candidate(self) -> None:
        message = inbox.parse_gmail_message(
            _raw_message(
                subject="Your verification code",
                body="Verification code: 123456",
                sender="Portal <noreply@employer.example>",
            )
        )
        result = inbox.classify_rules(message)
        self.assertTrue(result.candidate)
        self.assertEqual(result.category, "other")
        self.assertFalse(result.action_required)
        classifier = mock.Mock(side_effect=AssertionError("model must not receive credential mail"))
        inbox.classify_message(message, model_classifier=classifier)
        classifier.assert_not_called()

    def test_verification_lookup_does_not_fall_back_past_ambiguous_newest_mail(self) -> None:
        old_body = config.INBOX_DIR / "old-code.txt"
        new_body = config.INBOX_DIR / "new-code.txt"
        old_body.write_text("Verification code: 111111", encoding="utf-8")
        new_body.write_text(
            "Verification code: 222222. Backup verification code: 333333.",
            encoding="utf-8",
        )
        self.conn.executemany(
            """
            INSERT INTO emails (
              provider, message_id, received_at, from_addr, from_domain, subject,
              body_path, category, link_domains_json, deadline_confidence
            ) VALUES ('gmail', ?, ?, 'noreply@jobs.example.test',
                      'jobs.example.test', 'Verification code', ?, 'other', '[]', 'unknown')
            """,
            (
                ("old", "2026-09-22T11:00:00+00:00", str(old_body)),
                ("new", "2026-09-22T12:00:00+00:00", str(new_body)),
            ),
        )
        self.conn.commit()
        code = inbox.find_verification_code(
            "jobs.example.test",
            received_after=datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc),
            conn=self.conn,
        )
        self.assertIsNone(code)

    def test_poll_stores_once_and_advances_history_cursor(self) -> None:
        self._submitted_application()
        gmail = FakeGmail([_raw_message()])
        with mock.patch.object(inbox.notify, "notify") as send:
            first = inbox.run_poll(
                conn=self.conn, gmail=gmail, use_model=False, send_notifications=True
            )
            second = inbox.run_poll(
                conn=self.conn, gmail=gmail, use_model=False, send_notifications=True
            )
        self.assertEqual(first, {"fetched": 1, "new": 1, "relevant": 1, "paused": 0})
        self.assertEqual(second["new"], 0)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(gmail.list_queries, ["newer_than:3h"])
        state = self.conn.execute("SELECT * FROM inbox_state WHERE provider='gmail'").fetchone()
        self.assertEqual(state["history_id"], "201")

    def test_expired_history_cursor_recovers_with_seven_day_sync(self) -> None:
        self.conn.execute(
            "INSERT INTO inbox_state(provider, account, history_id) VALUES ('gmail','ali@example.com','old')"
        )
        self.conn.commit()
        gmail = FakeGmail([_raw_message()], history_expired=True)
        result = inbox.run_poll(
            conn=self.conn, gmail=gmail, use_model=False, send_notifications=False
        )
        self.assertEqual(result["new"], 1)
        self.assertEqual(gmail.list_queries, ["newer_than:7d"])
        note = self.conn.execute("SELECT notes FROM inbox_state WHERE provider='gmail'").fetchone()[0]
        self.assertIn("history id expired", note)

    def test_confirm_learns_patterns_creates_assessment_and_advances_application(self) -> None:
        app_id = self._submitted_application()
        email_id, created, _classification = inbox.process_raw_message(
            _raw_message(body="Acme assessment: complete by 25 September 2026."),
            gmail=None,
            conn=self.conn,
            use_model=False,
            send_notification=False,
        )
        self.assertTrue(created)
        result = inbox.confirm_email(email_id, conn=self.conn)
        self.assertEqual(result["application_status"], "assessment")
        self.assertIsNotNone(result["assessment_id"])
        app = self.conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
        self.assertEqual(app["status"], "assessment")
        self.assertEqual(app["next_action_due"], "2026-09-25T23:59:00+01:00")
        self.assertGreaterEqual(self.conn.execute("SELECT COUNT(*) FROM sender_patterns").fetchone()[0], 2)
        email = self.conn.execute("SELECT handled, needs_review FROM emails WHERE id = ?", (email_id,)).fetchone()
        self.assertEqual(tuple(email), (1, 0))

    def test_acknowledged_application_can_advance_to_interview_after_confirmation(self) -> None:
        app_id = self._submitted_application()
        db.transition_application(app_id, "acknowledged", "test", conn=self.conn)
        email_id, _, _ = inbox.process_raw_message(
            _raw_message(
                subject="Interview invitation",
                body="Acme would like you to book a live Teams interview by 25 September 2026.",
            ),
            gmail=None,
            conn=self.conn,
            use_model=False,
            send_notification=False,
        )
        result = inbox.confirm_email(email_id, conn=self.conn)
        self.assertEqual(result["application_status"], "interview")
        status = self.conn.execute("SELECT status FROM applications WHERE id = ?", (app_id,)).fetchone()[0]
        self.assertEqual(status, "interview")

    def test_calendar_creation_is_explicit_and_idempotent(self) -> None:
        self._submitted_application()
        email_id, _, _ = inbox.process_raw_message(
            _raw_message(), gmail=None, conn=self.conn, use_model=False, send_notification=False
        )
        assessment_id = inbox.confirm_email(email_id, conn=self.conn)["assessment_id"]
        oauth = FakeOAuth()
        event_id = inbox.create_calendar_event(assessment_id, conn=self.conn, oauth=oauth)
        again = inbox.create_calendar_event(assessment_id, conn=self.conn, oauth=oauth)
        self.assertEqual(event_id, "calendar-123")
        self.assertEqual(again, "calendar-123")
        self.assertEqual(len(oauth.calls), 1)
        method, url, kwargs = oauth.calls[0]
        self.assertEqual(method, "POST")
        self.assertIn("calendar/v3", url)
        payload = kwargs["json"]
        self.assertEqual(payload["visibility"], "private")
        self.assertEqual(payload["transparency"], "transparent")


if __name__ == "__main__":
    unittest.main()
