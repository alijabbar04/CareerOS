"""T-043: publishing application material to the Documents mirror and the OneDrive folder."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TEMP_DIR = tempfile.mkdtemp(prefix="careeros-publish-test-")
os.environ["CAREEROS_LOCAL_DIR"] = _TEMP_DIR

from pipeline import config, publish  # noqa: E402

DRAFT = """---
application_id: 7
kind: answer-1
question: "Why this firm?"
word_limit: 300
round: 2
mode: drafting_assisted
---
First paragraph of prose with a fact. Second sentence.

Second paragraph.
FACT REQUEST: something for the candidate.

## Citations
- S1: C-0001
- S2: motivation
- S3: C-0002
"""


class PublishTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="careeros-publish-case-", dir=_TEMP_DIR))
        config.LOCAL_DIR = self.temp
        config.DB_PATH = self.temp / "missing.sqlite"  # no database: publish must still work from the folder alone
        self.app = self.temp / "0007-example-firm"
        (self.app / "drafts").mkdir(parents=True)
        (self.app / "final").mkdir()
        (self.app / "reports").mkdir()
        (self.app / "drafts" / "answer-1-r1.md").write_text(DRAFT.replace("round: 2", "round: 1"), encoding="utf-8")
        (self.app / "drafts" / "answer-1-r2.md").write_text(DRAFT, encoding="utf-8")
        (self.app / "drafts" / "answer-2-r1.md").write_text(DRAFT.replace("answer-1", "answer-2"), encoding="utf-8")
        (self.app / "final" / "answer-1-r2.md").write_text(DRAFT, encoding="utf-8")
        (self.app / "reports" / "verify-answer-1-r2.md").write_text("# report", encoding="utf-8")
        (self.app / "brief.md").write_text("---\nmode: drafting_assisted\ncompany: Example Firm\n---\n# brief\n", encoding="utf-8")
        (self.app / "prep-assessment.md").write_text("# Prep\n\nSome notes.\n", encoding="utf-8")
        self.local = self.temp / "docs-mirror"
        self.onedrive = self.temp / "onedrive"

    def test_prose_body_strips_front_matter_citations_and_fact_requests(self) -> None:
        meta, body = publish.prose_body(DRAFT)
        self.assertEqual(meta["kind"], "answer-1")
        self.assertIn("First paragraph", body)
        self.assertNotIn("Citations", body)
        self.assertNotIn("FACT REQUEST", body)
        self.assertNotIn("C-0001", body)

    def test_publish_application_writes_both_targets(self) -> None:
        with mock.patch.object(publish, "_app_row", return_value=None):
            result = publish.publish_application(self.app, self.local, self.onedrive)
        local_dir = Path(result["local"])
        self.assertTrue((local_dir / "drafts" / "answer-1-r2.md").exists())
        self.assertTrue((local_dir / "reports" / "verify-answer-1-r2.md").exists())
        self.assertTrue((local_dir / "brief.md").exists())
        od = Path(result["onedrive"])
        self.assertTrue((od / "final" / "answer-1-r2.md").exists())
        self.assertTrue((od / "drafts" / "answer-2-r1.md").exists())          # latest round of the unapproved kind
        self.assertFalse((od / "drafts" / "answer-1-r2.md").exists())        # approved kinds are not duplicated as drafts
        self.assertFalse((od / "drafts" / "answer-1-r1.md").exists())        # superseded rounds never reach the phone
        self.assertTrue((od / "prep-assessment.md").exists())
        readme = (od / "README.md").read_text(encoding="utf-8")
        self.assertIn("the candidate must answer", readme)
        self.assertIn("something for the candidate", readme)
        if publish._docx_available():
            self.assertTrue((od / "final" / "answer-1-r2.docx").exists())
            self.assertTrue((local_dir / "drafts" / "answer-1-r2.docx").exists())

    def test_index_lists_applications(self) -> None:
        with mock.patch.object(publish, "_app_row", return_value=None):
            result = publish.publish_application(self.app, self.local, self.onedrive)
            index = publish.write_index([result], self.onedrive)
        text = index.read_text(encoding="utf-8")
        self.assertIn("0007-example-firm", text)
        self.assertIn("| ? | ? | 1 |", text)


if __name__ == "__main__":
    unittest.main()
