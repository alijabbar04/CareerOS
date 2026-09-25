"""T-017: tests for pipeline.drafts (application folders, briefs, review queue) and pipeline.verify.

Same isolation convention as tests/test_notify.py: every config path is pointed at a
temp directory in setUp, so the real %LOCALAPPDATA%\\CareerOS database and the real
brain/vault/applications folder are never touched.

Run with:  python -m unittest tests.test_drafts -v
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TEMP_DIR = tempfile.mkdtemp(prefix="careeros-drafts-test-")
os.environ["CAREEROS_LOCAL_DIR"] = _TEMP_DIR

from pipeline import config, db, drafts, settings, verify  # noqa: E402

TRACK_FILE = """---
id: narrative-track-1
kind: narrative
track: 1-aca-acca-training-contracts
claims: [C-0001, C-0002, C-0003]
stories: [sample-story]
---

# Track 1

## Long-term goal statement
To qualify as a chartered accountant.
"""

STORY_FILE = """---
id: story-sample
kind: story
competencies: [accuracy]
tracks: [1-aca-acca-training-contracts]
claims: [C-0002]
---

# Sample story

## Versions

### Short (about 60 words)
At Argos I sold regulated products and achieved 325 policy sales.

### Medium (about 150 words)
Longer version.
"""

LEDGER = {
    "C-0001": {"id": "C-0001", "text": "the candidate graduated with a 2:1 in Economics from the university in June 2025.", "status": "confirmed", "sensitivity": "free", "tier": "safe", "category": "education"},
    "C-0002": {"id": "C-0002", "text": "At Argos the candidate achieved 325 policy sales of FCA-regulated products.", "status": "confirmed", "sensitivity": "free", "tier": "safe", "category": "achievement"},
    "C-0003": {"id": "C-0003", "text": "the candidate scored 90% on his Corporate Finance presentation analysing Porsche.", "status": "confirmed", "sensitivity": "free", "tier": "safe", "category": "achievement"},
    "C-0004": {"id": "C-0004", "text": "the candidate raised classification accuracy from about 30% to about 94%.", "status": "confirmed", "sensitivity": "use-with-approval", "tier": "specific", "category": "achievement"},
    "C-0005": {"id": "C-0005", "text": "the candidate volunteered at a hospital.", "status": "retired", "sensitivity": "never-use", "tier": "safe", "category": "experience"},
}

CLEAN_DRAFT = """---
application_id: 1
kind: cover-letter
word_limit: 400
round: 1
mode: drafting_assisted
generated_by: test
---
Dear Example Firm Recruitment Team,

I am applying for the Graduate Trainee role because the work combines analysis with client decisions. I graduated with a 2:1 in Economics from the university in June 2025. At Argos I achieved 325 policy sales of FCA-regulated products, which taught me to explain regulated information carefully. What particularly attracts me to Example Firm is the emphasis on early client exposure.

Yours faithfully,
Candidate Name

## Citations
- S1: greeting
- S2: motivation
- S3: C-0001
- S4: C-0002
- S5: motivation
- S6: closing
"""


class DraftsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="careeros-drafts-case-", dir=_TEMP_DIR))
        config.LOCAL_DIR = self.temp
        config.DB_PATH = self.temp / "test.sqlite"
        config.BACKUP_DIR = self.temp / "backups"
        config.LOGS_DIR = self.temp / "logs"
        config.PAUSED_FLAG = self.temp / "PAUSED"
        config.ensure_dirs()
        drafts.APPLICATIONS_DIR = self.temp / "applications"
        drafts.COMPANIES_DIR = self.temp / "companies"
        drafts.NARRATIVES_DIR = self.temp / "narratives"
        drafts.STORIES_DIR = self.temp / "stories"
        verify.COMPANIES_DIR = drafts.COMPANIES_DIR
        verify.NARRATIVES_DIR = drafts.NARRATIVES_DIR
        drafts.NARRATIVES_DIR.mkdir()
        drafts.STORIES_DIR.mkdir()
        (drafts.NARRATIVES_DIR / "track-1-aca-acca-training-contracts.md").write_text(TRACK_FILE, encoding="utf-8")
        (drafts.STORIES_DIR / "sample-story.md").write_text(STORY_FILE, encoding="utf-8")
        self.conn = db.connect(config.DB_PATH)
        db.migrate(self.conn)
        self.conn.execute("INSERT INTO sources (id, path, kind) VALUES ('test-source', 'test.md', 'test')")
        common = {"subject": "Test", "source_id": "test-source", "locator": "t", "quote": "q", "confidence": "verified", "status": "confirmed", "tier": "safe"}
        db.insert_claim({**common, "text": "At Argos the candidate achieved 325 policy sales of FCA-regulated products.", "category": "achievement", "sensitivity": "free"}, conn=self.conn)
        db.insert_claim({**common, "text": "the candidate raised accuracy from about 30% to about 94%.", "category": "achievement", "sensitivity": "use-with-approval"}, conn=self.conn)
        self.company_id = db.upsert_company("Example Firm", conn=self.conn, priority=1)
        self.posting_id = db.upsert_posting(
            {
                "company_id": self.company_id,
                "title": "Graduate Trainee (ACA)",
                "canonical_url": "https://example.com/jobs/1",
                "location": "London",
                "description": "Regulated products, FCA, audit and accounts trainee programme for graduates.",
                "track": "1-aca-acca-training-contracts",
                "closes_at": "2026-10-31",
                "status": "shortlisted",
            },
            conn=self.conn,
        )
        self.conn.commit()
        self.settings = settings.Settings(data={"tracks": ["1-aca-acca-training-contracts"], "caps": {"cycle_start": "2026-09-01"},
                                                "employer_ai_policy": {"default_mode": "drafting_assisted", "mode_overrides": {"BDO": "outline_only"},
                                                                       "ai_declaration": {"drafting_assisted": "I used AI tools to organise my experience."}}})

    def tearDown(self) -> None:
        self.conn.close()

    # -- drafts ---------------------------------------------------------------

    def test_init_creates_application_folder_and_brief(self) -> None:
        with mock.patch.object(settings, "Settings", return_value=self.settings):
            code = drafts.main(["init", "--posting", str(self.posting_id)])
        self.assertEqual(code, 0)
        conn = db.connect(config.DB_PATH)
        app = conn.execute("SELECT * FROM applications WHERE posting_id = ?", (self.posting_id,)).fetchone()
        self.assertIsNotNone(app)
        self.assertEqual(app["status"], "draft")
        self.assertEqual(app["drafting_mode"], "drafting_assisted")
        self.assertEqual(app["track"], "1-aca-acca-training-contracts")
        folder = drafts.find_folder(int(app["id"]))
        self.assertIsNotNone(folder)
        brief = (folder / "brief.md").read_text(encoding="utf-8")
        self.assertIn("Graduate Trainee (ACA)", brief)
        self.assertIn("Drafting mode: **drafting_assisted**", brief)
        self.assertIn("I used AI tools to organise my experience.", brief)
        self.assertIn("To qualify as a chartered accountant.", brief)          # track narrative body
        self.assertIn("### sample-story", brief)                               # story named by the track
        self.assertIn("325 policy sales", brief)                               # relevant free claim
        self.assertIn("Usable only with the candidate's approval", brief)                # use-with-approval listed separately
        self.assertIn("No company note yet", brief)
        for sub in ("drafts", "reports", "final"):
            self.assertTrue((folder / sub).is_dir())
        events = conn.execute("SELECT type FROM events WHERE entity = 'application' AND entity_id = ?", (app["id"],)).fetchall()
        self.assertIn("created", {e["type"] for e in events})
        conn.close()

    def test_init_refuses_second_application_to_same_employer_in_cycle(self) -> None:
        second = db.upsert_posting(
            {"company_id": self.company_id, "title": "Tax Trainee", "canonical_url": "https://example.com/jobs/2",
             "location": "London", "description": "Tax trainee.", "track": "1-aca-acca-training-contracts", "status": "shortlisted"},
            conn=self.conn,
        )
        self.conn.commit()
        with mock.patch.object(settings, "Settings", return_value=self.settings):
            self.assertEqual(drafts.main(["init", "--posting", str(self.posting_id)]), 0)
            self.assertEqual(drafts.main(["init", "--posting", str(second)]), 3)
            self.assertEqual(drafts.main(["init", "--posting", str(second), "--force"]), 0)

    def test_mode_override_and_registry_policy(self) -> None:
        mode, reason = drafts.drafting_mode_for("BDO", {}, self.settings)
        self.assertEqual(mode, "outline_only")
        self.assertIn("mode_overrides", reason)
        mode, _ = drafts.drafting_mode_for("Other", {"ai_policy": "AI-generated content is prohibited"}, self.settings)
        self.assertEqual(mode, "outline_only")
        mode, _ = drafts.drafting_mode_for("Other", {"ai_policy": "unknown"}, self.settings)
        self.assertEqual(mode, "drafting_assisted")

    def test_ready_requires_passing_reports_then_transitions(self) -> None:
        with mock.patch.object(settings, "Settings", return_value=self.settings):
            drafts.main(["init", "--posting", str(self.posting_id)])
        conn = db.connect(config.DB_PATH)
        app_id = int(conn.execute("SELECT id FROM applications").fetchone()["id"])
        folder = drafts.find_folder(app_id)
        draft = folder / "drafts" / "cover-letter-r1.md"
        draft.write_text(CLEAN_DRAFT, encoding="utf-8")
        self.assertEqual(drafts.main(["ready", str(app_id)]), 3)            # no report yet
        (folder / "reports" / "verify-cover-letter-r1.json").write_text(json.dumps({"hard_fails": ["x"], "warnings": []}), encoding="utf-8")
        self.assertEqual(drafts.main(["ready", str(app_id)]), 3)            # failing report
        (folder / "reports" / "verify-cover-letter-r1.json").write_text(json.dumps({"hard_fails": [], "warnings": ["w"]}), encoding="utf-8")
        self.assertEqual(drafts.main(["ready", str(app_id)]), 0)
        self.assertEqual(conn.execute("SELECT status FROM applications WHERE id = ?", (app_id,)).fetchone()["status"], "ready-for-review")
        self.assertEqual(drafts.main(["register", str(app_id), str(draft), "--kind", "cover-letter"]), 0)
        doc = conn.execute("SELECT doc_type, version FROM documents WHERE application_id = ?", (app_id,)).fetchone()
        self.assertEqual((doc["doc_type"], doc["version"]), ("cover_letter", 1))
        self.assertEqual(drafts.main(["approve-claims", str(app_id), "C-0004"]), 0)
        self.assertIn("C-0004", (folder / "approvals.yaml").read_text(encoding="utf-8"))
        conn.close()

    def test_latest_round_supersedes_earlier_rounds(self) -> None:
        folder = self.temp / "app"
        (folder / "drafts").mkdir(parents=True)
        for name in ("answer-1-r1.md", "answer-1-r2.md", "answer-2-r1.md", "cover-letter-r3.md", "cover-letter-r10.md"):
            (folder / "drafts" / name).write_text("x", encoding="utf-8")
        self.assertEqual([p.name for p in drafts.latest_drafts(folder)], ["answer-1-r2.md", "answer-2-r1.md", "cover-letter-r10.md"])

    # -- verify -----------------------------------------------------------------

    def _write(self, text: str) -> Path:
        path = self.temp / "draft.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_clean_draft_passes(self) -> None:
        result = verify.verify_draft(self._write(CLEAN_DRAFT), LEDGER, company="Example Firm", track_claims=["C-0001", "C-0002", "C-0003"])
        self.assertEqual(result["hard_fails"], [], result)
        self.assertEqual(len(result["sentences"]), 6)
        self.assertEqual(result["cited_claims"], ["C-0001", "C-0002"])
        self.assertEqual(result["metrics"]["track_claims_used"], "2/3")

    def test_unsupported_number_unmapped_sentence_and_banned_phrase_fail(self) -> None:
        bad = CLEAN_DRAFT.replace("achieved 325 policy sales", "achieved 400 policy sales")
        bad = bad.replace("- S5: motivation\n", "")
        bad = bad.replace("early client exposure.", "early client exposure and the chance to leverage my skills.")
        result = verify.verify_draft(self._write(bad), LEDGER, company="Example Firm")
        joined = "\n".join(result["hard_fails"])
        self.assertIn("400", joined)
        self.assertIn("S5 has no citation", joined)
        self.assertIn("banned phrase", joined)

    def test_never_use_and_approval_gates(self) -> None:
        draft = CLEAN_DRAFT.replace("- S4: C-0002", "- S4: C-0002, C-0005")
        result = verify.verify_draft(self._write(draft), LEDGER, company="Example Firm")
        self.assertTrue(any("C-0005" in h for h in result["hard_fails"]))
        draft = CLEAN_DRAFT.replace("- S3: C-0001", "- S3: C-0001, C-0004")
        result = verify.verify_draft(self._write(draft), LEDGER, company="Example Firm")
        self.assertTrue(any("C-0004 needs the candidate's approval" in h for h in result["hard_fails"]))
        result = verify.verify_draft(self._write(draft), LEDGER, company="Example Firm", approved={"C-0004"})
        self.assertFalse(any("C-0004" in h for h in result["hard_fails"]))

    def test_format_rules_for_letters(self) -> None:
        draft = CLEAN_DRAFT.replace("Dear Example Firm Recruitment Team,\n\n", "").replace("- S1: greeting\n", "")
        draft = draft.replace("- S2: motivation", "- S1: motivation").replace("- S3: C-0001", "- S2: C-0001").replace("- S4: C-0002", "- S3: C-0002").replace("- S5: motivation", "- S4: motivation").replace("- S6: closing", "- S5: closing")
        draft = draft.replace("I am applying", "I'm applying")
        result = verify.verify_draft(self._write(draft), LEDGER, company="Example Firm")
        joined = "\n".join(result["hard_fails"])
        self.assertIn("missing salutation", joined)
        self.assertIn("contraction", joined)

    def test_letter_without_limit_uses_the_standard_ceiling(self) -> None:
        long_body = " ".join(["I analysed data carefully and reported the results to the team."] * 40)
        draft = CLEAN_DRAFT.replace("word_limit: 400\n", "").replace(
            "What particularly attracts me to Example Firm is the emphasis on early client exposure.",
            "What particularly attracts me to Example Firm is the emphasis on early client exposure. " + long_body,
        )
        # the mapping no longer matches the sentence count, so look only at the ceiling message
        result = verify.verify_draft(self._write(draft), LEDGER, company="Example Firm")
        self.assertTrue(any("hard ceiling is 400" in h for h in result["hard_fails"]))

    def test_shared_eight_word_runs_are_reported(self) -> None:
        other_app = self.temp / "apps" / "0002-other-firm"
        (other_app / "final").mkdir(parents=True)
        (other_app / "final" / "cover-letter-r1.md").write_text(CLEAN_DRAFT.replace("Example Firm", "Other Firm"), encoding="utf-8")
        mine = self.temp / "apps" / "0003-mine"
        (mine / "drafts").mkdir(parents=True)
        path = mine / "drafts" / "cover-letter-r1.md"
        path.write_text(CLEAN_DRAFT, encoding="utf-8")
        _, body = verify.split_draft(CLEAN_DRAFT)[:2]
        hits = verify.shared_runs(body, path, n=8, roots=[self.temp / "apps"])
        self.assertTrue(hits and "0002-other-firm" in hits[0][0])
        hits_same = verify.shared_runs(body, path, n=8, roots=[mine.parent / "nothing-here"] if False else [])
        self.assertEqual(hits_same, [])

    def test_research_sentence_numbers_come_from_the_company_note(self) -> None:
        draft = CLEAN_DRAFT.replace("is the emphasis on early client exposure.", "is its 12 offices and early client exposure.").replace("- S5: motivation", "- S5: research")
        result = verify.verify_draft(self._write(draft), LEDGER, company="Example Firm", research_text="")
        self.assertTrue(any("12" in h for h in result["hard_fails"]))
        result = verify.verify_draft(self._write(draft), LEDGER, company="Example Firm", research_text="- Example Firm has 12 offices (https://example.com/about, retrieved 2026-09-22)")
        self.assertEqual(result["hard_fails"], [])
        self.assertEqual(result["metrics"]["research_sentences"], 1)

    def test_company_note_validation(self) -> None:
        note = self.temp / "example-firm.md"
        note.write_text("# Example Firm\n\n- 12 offices in the UK (https://example.com/about, retrieved 2026-09-22)\n- Founded in 1990\n", encoding="utf-8")
        result = verify.verify_research(note)
        self.assertEqual(result["facts"], 2)
        self.assertEqual(len(result["problems"]), 2)  # second bullet lacks both URL and date


if __name__ == "__main__":
    unittest.main()
