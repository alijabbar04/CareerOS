"""T-045 companion API: reads, guarded writes, events, and nothing outside the app (temp database and folders)."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import main as api
from pipeline import config, db, drafts

W = {"X-CareerOS-Client": "companion"}
DRAFT = """---
application_id: 7
kind: answer-1
question: "Why us?"
word_limit: 300
round: 2
mode: drafting_assisted
generated_by: drafter
model: test
---
I want to train here because the work is varied and the clients are people.

## Citations
- S1: motivation
"""


class CompanionApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="careeros-api-test-")
        root = Path(self.tmp.name)
        self.saved = (config.DB_PATH, config.DATA_DIR, drafts.APPLICATIONS_DIR, api.PUBLISH)
        config.DB_PATH = root / "tracker.sqlite"
        config.DATA_DIR = root / "data"
        config.DATA_DIR.mkdir()
        drafts.APPLICATIONS_DIR = root / "applications"
        self.published: list[Path] = []
        api.PUBLISH = lambda folder: self.published.append(folder) or {"published": str(folder)}
        self.folder = drafts.APPLICATIONS_DIR / "0007-acme"
        for sub in ("drafts", "reports", "final"):
            (self.folder / sub).mkdir(parents=True)
        (self.folder / "drafts" / "answer-1-r2.md").write_text(DRAFT, encoding="utf-8")
        (self.folder / "reports" / "verify-answer-1-r2.json").write_text(json.dumps({"hard_fails": [], "warnings": []}), encoding="utf-8")
        (self.folder / "final" / "answer-1-r1.md").write_text(DRAFT.replace("round: 2", "round: 1"), encoding="utf-8")
        conn = db.connect(config.DB_PATH)
        db.migrate(conn)
        cid = conn.execute("INSERT INTO companies (name) VALUES ('Acme LLP')").lastrowid
        conn.execute("INSERT INTO applications (id, company_id, role_title, status, deadline) VALUES (7, ?, 'Graduate', 'ready-for-review', ?)",
                     (cid, (date.today() + timedelta(days=3)).isoformat()))
        conn.execute("INSERT INTO assessments (application_id, kind, deadline) VALUES (7, 'numerical', ?)",
                     ((date.today() + timedelta(days=2)).isoformat(),))
        self.posting = conn.execute("INSERT INTO postings (company_id, source, url, title, fingerprint) "
                                    "VALUES (?, 'rss', 'https://jobs.example.test/1', 'Audit trainee', 'fp1')", (cid,)).lastrowid
        conn.commit()
        conn.close()
        (config.DATA_DIR / "shortlist-2026-09-24.md").write_text(
            "# Daily shortlist\n\n## 1. Acme LLP: Audit trainee — Board (95/100)\n\n- Location: London\n- Closing date: Not published\n"
            "- Source: [rss](https://jobs.example.test/1)\n", encoding="utf-8")
        self.client = TestClient(api.app)

    def tearDown(self) -> None:
        config.DB_PATH, config.DATA_DIR, drafts.APPLICATIONS_DIR, api.PUBLISH = self.saved
        self.tmp.cleanup()

    def _events(self, kind: str) -> list[dict]:
        conn = db.connect(config.DB_PATH)
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM events WHERE type=?", (kind,))]
        finally:
            conn.close()

    def test_reads_and_home(self) -> None:
        self.assertEqual(self.client.get("/health").json()["status"], "ok")
        home = self.client.get("/home").json()
        self.assertEqual([a["id"] for a in home["to_review"]], [7])
        self.assertEqual(len(home["assessments_due"]), 1)
        detail = self.client.get("/applications/7").json()
        self.assertEqual([d["stem"] for d in detail["finals"]], ["answer-1-r1"])

    def test_nothing_outside_the_app_is_served(self) -> None:
        for path in ("/vault", "/vault/index.json", "/.env", "/applications/7/drafts/..%2f..%2fbrief",
                     "/applications/7/drafts/answer-1-r2%2e%2e"):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_writes_need_the_app_header_and_same_origin(self) -> None:
        url = "/applications/7/drafts/answer-1-r2/approve"
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(self.client.post(url, headers={**W, "Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.published, [])

    def test_approve_moves_the_draft_into_final_and_logs_it(self) -> None:
        r = self.client.post("/applications/7/drafts/answer-1-r2/approve", headers={**W, "Origin": "http://testserver"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(sorted(p.name for p in (self.folder / "final").glob("*.md")), ["answer-1-r2.md"])
        self.assertEqual(r.json()["status_change"], "approved")
        self.assertEqual(len(self._events("draft_approved")), 1)
        self.assertEqual(self.published, [self.folder])

    def test_approve_refuses_a_draft_with_hard_failures(self) -> None:
        (self.folder / "reports" / "verify-answer-1-r2.json").write_text(json.dumps({"hard_fails": ["uncited number"]}), encoding="utf-8")
        self.assertEqual(self.client.post("/applications/7/drafts/answer-1-r2/approve", headers=W).status_code, 409)

    def test_edit_writes_a_new_round_verifies_and_records_the_distance(self) -> None:
        r = self.client.post("/applications/7/drafts/answer-1-r2/edit", headers=W,
                             json={"text": "I want to train here because the clients are people and families.", "note": "shorter"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["stem"], "answer-1-r3")
        self.assertGreater(body["words_changed"], 0)
        self.assertTrue((self.folder / "drafts" / "answer-1-r3.md").exists())
        self.assertTrue((self.folder / "reports" / "edits-answer-1-r3.md").exists())
        self.assertTrue((self.folder / "reports" / "verify-answer-1-r3.json").exists())
        self.assertEqual(len(self._events("draft_edited")), 1)

    def test_reject_records_the_reason(self) -> None:
        self.assertEqual(self.client.post("/applications/7/drafts/answer-1-r2/reject", headers=W, json={"reason": "too generic"}).status_code, 200)
        self.assertIn("too generic", (self.folder / "reports" / "rejected-answer-1-r2.md").read_text(encoding="utf-8"))
        self.assertEqual(len(self._events("draft_rejected")), 1)

    def test_shortlist_rows_map_to_postings_and_skip_is_logged(self) -> None:
        rows = self.client.get("/shortlist/latest").json()
        self.assertEqual((rows[0]["posting_id"], rows[0]["company"], rows[0]["closes_at"]), (self.posting, "Acme LLP", None))
        self.assertEqual(self.client.post(f"/shortlist/{self.posting}/skip", headers=W).status_code, 200)
        conn = db.connect(config.DB_PATH)
        try:
            self.assertEqual(conn.execute("SELECT status FROM postings WHERE id=?", (self.posting,)).fetchone()[0], "ignored")
        finally:
            conn.close()
        self.assertEqual(len(self._events("shortlist_skipped")), 1)

    def test_shortlist_approve_obeys_one_application_per_employer(self) -> None:
        r = self.client.post(f"/shortlist/{self.posting}/approve", headers=W)
        self.assertEqual(r.status_code, 409, r.text)          # Acme already has application 7 this cycle
        self.assertIn("One application per employer", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
