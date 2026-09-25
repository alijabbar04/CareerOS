"""T-022 approval-gated submission: pipeline.submit and pinpoint.submit_application (temp database, no network)."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from pipeline import config, db, drafts, settings, submit, vault
from pipeline.recipes import pinpoint

A = "application_form[application]"
NOW = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)

FORM_YAML = """
form_url: https://jobs.example.test/apply/new
personal:
  first_name: {value: "the candidate"}
  email: {vault: identity.email}
answers:
  1: {file: final/answer-1.md}
  27: {file: upload.pdf}
declarations:
  process_information: {value: tick}
"""

ANSWER = "---\nkind: answer-1\n---\nA synthetic answer.\n\n## Citations\n- S1: C-0001\n"

PAGE = f"""<!doctype html><html><body><h1>Apply</h1><form action="/apply/submit" method="post" enctype="multipart/form-data">
<input name="{A}[first_name]"><input type="email" name="{A}[email]">
<input type="hidden" name="{A}[answers_attributes][1][question_type]" value="long_text">
<textarea name="{A}[answers_attributes][1][text_answer]"></textarea>
<input type="hidden" name="{A}[answers_attributes][27][question_type]" value="document">
<input type="file" name="{A}[answers_attributes][27][document]">
<input type="checkbox" name="application[process_information]" value="1">
<button type="submit">Submit Application</button></form>
<script>/* like Rails direct upload: the file input is swapped for a hidden input with the blob id once uploaded */
document.querySelector('input[type=file]').addEventListener('change', e => {{ const f = e.target;
  setTimeout(() => {{ const h = document.createElement('input'); h.type = 'hidden'; h.name = f.name; h.value = 'signed-blob-id';
    f.replaceWith(h); }}, 300); }});</script></body></html>"""
THANKS = "<!doctype html><html><body><h1>Thank you for applying</h1><p>Your application has been submitted.</p></body></html>"
ERRORS = PAGE.replace("<h1>Apply</h1>", '<h1>Apply</h1><div class="error">Please correct the errors below</div>')


class MemoryCredentialStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


def _result(filled: int = 4, missing: list[str] | None = None, failed: list[str] | None = None) -> pinpoint.Result:
    return pinpoint.Result("dry-run", filled, missing or [], failed or [], None)


class SubmitTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="careeros-submit-test-")
        root = Path(self.tmp.name)
        self.saved = {n: getattr(config, n) for n in ("LOCAL_DIR", "DB_PATH", "SCREENSHOTS_DIR", "PAUSED_FLAG",
                                                        "SECRET_VAULT_DIR", "LOGS_DIR", "BACKUP_DIR", "INBOX_DIR")}
        self.saved_apps = drafts.APPLICATIONS_DIR
        config.LOCAL_DIR = root
        config.DB_PATH = root / "tracker.sqlite"
        config.SCREENSHOTS_DIR = root / "screenshots"
        config.PAUSED_FLAG = root / "PAUSED"
        config.SECRET_VAULT_DIR = root / "vault"
        config.LOGS_DIR = root / "logs"
        config.BACKUP_DIR = root / "backups"
        config.INBOX_DIR = root / "inbox"
        drafts.APPLICATIONS_DIR = root / "applications"
        self.folder = drafts.APPLICATIONS_DIR / "0099-example"
        (self.folder / "final").mkdir(parents=True)
        (self.folder / "form.yaml").write_text(FORM_YAML, encoding="utf-8")
        (self.folder / "final" / "answer-1.md").write_text(ANSWER, encoding="utf-8")
        (self.folder / "upload.pdf").write_bytes(b"%PDF-1.4 synthetic certificate")
        self.conn = db.connect(config.DB_PATH)
        db.migrate(self.conn)
        company = self.conn.execute("INSERT INTO companies (name) VALUES ('Example LLP')").lastrowid
        self.conn.execute("INSERT INTO applications (id, company_id, role_title, status, deadline) "
                          "VALUES (99, ?, 'Graduate Programme', 'ready-for-review', '2026-10-31')", (company,))
        self.conn.commit()
        self.active = settings.Settings(data={"autonomy": {"level": 1, "recall_window_minutes": 15,
                                                           "overrides": {"submit_applications": "ask"}}})
        self.scheduled: list[tuple[int, datetime]] = []
        self.notes: list[str] = []

    def tearDown(self) -> None:
        self.conn.close()
        for name, value in self.saved.items():
            setattr(config, name, value)
        drafts.APPLICATIONS_DIR = self.saved_apps
        self.tmp.cleanup()

    def _approve(self, **kw) -> datetime:
        return submit.approve(99, conn=self.conn, now=NOW, dry_run=kw.get("dry_run", lambda _id: _result()),
                              schedule=kw.get("schedule", lambda i, w: self.scheduled.append((i, w))), active=self.active)

    def _run(self, when: datetime, outcome: str = "submitted") -> str:
        calls = []

        def fake_submitter(app_id: int) -> pinpoint.Submission:
            calls.append(app_id)
            return pinpoint.Submission(outcome, "https://jobs.example.test/done", "shot.png", 0 if outcome == "submitted" else 2)

        result = submit.run(99, conn=self.conn, now=when, submitter=fake_submitter,
                            notifier=lambda title, body, **kw: self.notes.append(title),
                            unschedule=lambda _id: None, active=self.active)
        self.submit_calls = calls
        return result

    def _status(self) -> str:
        return self.conn.execute("SELECT status FROM applications WHERE id=99").fetchone()["status"]

    def test_approval_schedules_after_the_recall_window_and_then_submits_once(self) -> None:
        when = self._approve()
        self.assertEqual(when, NOW + timedelta(minutes=15))
        self.assertEqual(self.scheduled, [(99, when)])
        self.assertEqual(self._status(), "approved")
        with self.assertRaisesRegex(submit.SubmitRefused, "recall window"):
            self._run(NOW + timedelta(minutes=5))
        self.assertEqual(self._run(when), "submitted")
        self.assertEqual(self.submit_calls, [99])
        row = self.conn.execute("SELECT status, submitted_by, submitted_at FROM applications WHERE id=99").fetchone()
        self.assertEqual((row["status"], row["submitted_by"]), ("submitted", "system-with-approval"))
        self.assertTrue(row["submitted_at"])
        self.assertTrue(self.notes and self.notes[-1].startswith("Submitted"))
        with self.assertRaisesRegex(submit.SubmitRefused, "no live approval"):
            self._run(when + timedelta(minutes=1))           # never a second submission

    def test_changed_content_after_approval_is_refused(self) -> None:
        when = self._approve()
        (self.folder / "final" / "answer-1.md").write_text(ANSWER.replace("synthetic", "edited"), encoding="utf-8")
        with self.assertRaisesRegex(submit.SubmitRefused, "content changed"):
            self._run(when)

    def test_cancel_within_the_window_stops_the_submission(self) -> None:
        when = self._approve()
        self.assertTrue(submit.cancel(99, conn=self.conn, unschedule=lambda _id: None))
        with self.assertRaisesRegex(submit.SubmitRefused, "no live approval"):
            self._run(when)

    def test_unconfirmed_outcome_ends_the_approval_without_marking_submitted(self) -> None:
        when = self._approve()
        self.assertEqual(self._run(when, outcome="unclear"), "unclear")
        self.assertEqual(self._status(), "approved")
        self.assertEqual(submit.latest_event(self.conn, 99)["type"], "submission_failed")
        with self.assertRaisesRegex(submit.SubmitRefused, "no live approval"):
            self._run(when)

    def test_incomplete_dry_run_or_pause_blocks(self) -> None:
        with self.assertRaisesRegex(submit.SubmitRefused, "did not fill everything"):
            self._approve(dry_run=lambda _id: _result(missing=["answer 27"]))
        self.assertEqual(self._status(), "ready-for-review")
        when = self._approve()
        config.PAUSED_FLAG.write_text("paused", encoding="utf-8")
        with self.assertRaisesRegex(submit.SubmitRefused, "paused"):
            self._run(when)

    def test_failed_scheduling_cancels_the_approval(self) -> None:
        def broken(_id: int, _when: datetime) -> None:
            raise OSError("no task scheduler")
        with self.assertRaisesRegex(submit.SubmitRefused, "could not be scheduled"):
            self._approve(schedule=broken)
        self.assertEqual(submit.latest_event(self.conn, 99)["type"], "submission_cancelled")

    def test_approving_needs_ali_at_the_terminal_or_his_quoted_words(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = submit.main(["approve", "99"], stdin_isatty=lambda: False)
        self.assertEqual(code, 3)
        self.assertIn("--ali-said", out.getvalue())
        submit.approve(99, conn=self.conn, now=NOW, dry_run=lambda _id: _result(), schedule=lambda i, w: None,
                       active=self.active, source="ali-chat", ali_said="yes submit it")
        event = submit.latest_event(self.conn, 99)
        self.assertEqual((event["source"], json.loads(event["detail_json"])["ali_said"]), ("ali-chat", "yes submit it"))

    def _submit_in_browser(self, response_html: str) -> pinpoint.Submission:
        store = MemoryCredentialStore()
        v = vault.Vault(config.SECRET_VAULT_DIR, credential_store=store, enforce_windows_backend=False, enforce_acl=False)
        v.set("identity.email", "synthetic@example.test", metadata={"kind": "identity", "field": "email"})
        posts: list[str] = []

        def handler(route) -> None:
            if route.request.method == "POST" and route.request.url.endswith("/apply/submit"):
                posts.append(route.request.url)
                route.fulfill(body=response_html, content_type="text/html")
            else:
                route.fulfill(body=PAGE, content_type="text/html")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(viewport=pinpoint.VIEWPORT)
                ctx.route("https://jobs.example.test/**", handler)
                result = pinpoint.submit_application(99, folder=self.folder, context=ctx, vault=v, active_settings=self.active)
            finally:
                browser.close()
        self.assertEqual(len(posts), 1)                    # Submit pressed exactly once
        return result

    def test_browser_submission_reads_the_confirmation_page(self) -> None:
        self.assertEqual(self._submit_in_browser(THANKS).outcome, "submitted")

    def test_browser_submission_with_form_errors_is_not_counted_as_submitted(self) -> None:
        self.assertEqual(self._submit_in_browser(ERRORS).outcome, "failed")


if __name__ == "__main__":
    unittest.main()
