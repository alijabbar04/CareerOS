"""T-019: consistency checks and the question bank (temp database and folders, injected ledger history)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pipeline import config, consistency, db, drafts, qa_bank

SENT_ANSWER = """---
application_id: 10
kind: answer-1
question: "Personal Summary - tell us about yourself"
word_limit: 300
---
At the current employer I roughly halved the processing cost. At Argos I made 325 policy sales. I led a team of eight.

## Citations
- S1: C-0001
- S2: C-0002
- S3: C-0003
"""

EARLIER = """---
id: application-answers--acme
kind: application-answers
employer: Acme LLP
doc_modified: '2025-12-09'
---
I raised feedback scores to 90% across 278 customer interactions.
"""


def draft(folder: Path, body: str, cites: list[str]) -> Path:
    mapping = "\n".join(f"- S{i}: {c}" for i, c in enumerate(cites, start=1))
    path = folder / "new.md"
    path.write_text(f"---\napplication_id: 11\nkind: answer-1\n---\n{body}\n\n## Citations\n{mapping}\n", encoding="utf-8")
    return path


class ConsistencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="careeros-consistency-test-")
        root = Path(self.tmp.name)
        self.saved = (config.DB_PATH, config.SOURCES_DIR, drafts.APPLICATIONS_DIR)
        config.DB_PATH = root / "tracker.sqlite"
        config.SOURCES_DIR = root / "sources"
        drafts.APPLICATIONS_DIR = root / "applications"
        config.SOURCES_DIR.mkdir()
        (config.SOURCES_DIR / "application-answers--acme.md").write_text(EARLIER, encoding="utf-8")
        sent = drafts.APPLICATIONS_DIR / "0010-acme" / "final"
        sent.mkdir(parents=True)
        (sent / "answer-1-r1.md").write_text(SENT_ANSWER, encoding="utf-8")
        self.work = root / "work"
        self.work.mkdir()
        self.conn = db.connect(config.DB_PATH)
        db.migrate(self.conn)
        cid = self.conn.execute("INSERT INTO companies (name) VALUES ('Acme LLP')").lastrowid
        self.conn.execute("INSERT INTO applications (id, company_id, role_title, status, submitted_at) "
                          "VALUES (10, ?, 'Graduate', 'submitted', '2026-09-01T09:00:00+00:00')", (cid,))
        self.conn.execute("INSERT INTO applications (id, company_id, role_title, status) VALUES (11, ?, 'Graduate', 'draft')", (cid,))
        self.conn.commit()
        self.sent = consistency.sent_documents(self.conn, cid, "Acme LLP", exclude_application=11)

    def tearDown(self) -> None:
        self.conn.close()
        config.DB_PATH, config.SOURCES_DIR, drafts.APPLICATIONS_DIR = self.saved
        self.tmp.cleanup()

    def _flags(self, body: str, cites: list[str]) -> list[consistency.Flag]:
        return consistency.compare(consistency.parse_document(draft(self.work, body, cites)), self.sent)

    def test_sent_documents_include_finals_and_earlier_applications(self) -> None:
        labels = [d.label for d in self.sent]
        self.assertTrue(any("application 10" in l for l in labels))
        self.assertTrue(any("earlier application" in l for l in labels))

    def test_same_claim_with_a_different_figure_is_flagged(self) -> None:
        flags = self._flags("At the current employer I cut processing costs by about 40%.", ["C-0001"])
        self.assertEqual([f.kind for f in flags], ["same-claim"])
        self.assertIn("roughly halved", flags[0].message)

    def test_the_same_figure_told_again_is_consistent(self) -> None:
        self.assertEqual(self._flags("Moving to overnight batches roughly halved the cost.", ["C-0001"]), [])
        self.assertEqual(self._flags("At Argos I made 325 policy sales.", ["C-0002"]), [])

    def test_a_different_number_for_the_same_topic_in_an_earlier_uncited_application_is_flagged(self) -> None:
        flags = self._flags("My feedback scores reached 95% across all customer interactions.", ["C-0009"])
        self.assertEqual([f.kind for f in flags], ["same-topic"])

    def test_unrelated_figures_are_not_flagged(self) -> None:
        self.assertEqual(self._flags("I scored 90% on the Corporate Finance presentation.", ["C-0164"]), [])

    def test_ledger_drift_after_sending_is_reported(self) -> None:
        then = {"C-0001": {"id": "C-0001", "text": "roughly halved costs", "status": "confirmed"}}
        now = {"C-0001": {"id": "C-0001", "text": "cut costs by roughly 40%", "status": "confirmed"}}
        flags = consistency.drift(self.conn, 10, ledger_then=lambda when: then, ledger_now=lambda: now)
        self.assertEqual(len(flags), 1)
        self.assertIn("C-0001", flags[0].message)
        self.assertEqual(consistency.drift(self.conn, 11), [])     # not sent yet

    def test_qa_bank_takes_approved_answers_and_marks_outdated_ones(self) -> None:
        outdated = consistency.Flag("drift", "answer-1-r1.md cites C-0001, changed since application 10 was sent")
        self.assertEqual(qa_bank.sync(self.conn, drift=lambda conn, app_id: [outdated] if app_id == 10 else []), 1)
        row = self.conn.execute("SELECT application_id, competency, reuse_ok, quality FROM qa_bank").fetchone()
        self.assertEqual(tuple(row), (10, "summary", 0, 5))
        self.assertEqual(qa_bank.sync(self.conn, drift=lambda conn, app_id: []), 1)     # upsert, no duplicates
        self.assertEqual(self.conn.execute("SELECT COUNT(*), MAX(reuse_ok) FROM qa_bank").fetchone()[:], (1, 1))


if __name__ == "__main__":
    unittest.main()
