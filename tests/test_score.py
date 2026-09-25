"""T-012 offline tests for deduplication, fit rules and shortlist output."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from pipeline import config, db, dedupe, score, settings


class DedupeUnitTests(unittest.TestCase):
    def test_hashed_embeddings_are_stable_and_discriminating(self) -> None:
        left = dedupe.text_embedding("Graduate finance analyst using Excel and financial modelling")
        same = dedupe.text_embedding("Graduate finance analyst using Excel and financial modelling")
        different = dedupe.text_embedding("Senior veterinary surgeon for night and weekend emergencies")
        self.assertAlmostEqual(dedupe.cosine_similarity(left, same), 1.0, places=5)
        self.assertLess(dedupe.cosine_similarity(left, different), 0.50)

    def test_duplicate_requires_two_signals(self) -> None:
        base = {
            "source": "greenhouse",
            "external_id": "123",
            "canonical_url": "https://boards.example/jobs/123",
            "company_name": "Example Ltd",
            "title": "Graduate Finance Analyst",
            "description": "Analyse monthly performance using Excel and explain results to colleagues.",
        }
        url_only = dict(
            base,
            title="Head Veterinary Surgeon",
            description="Lead a clinical team providing emergency animal care.",
        )
        decision = dedupe.compare(base, url_only)
        self.assertFalse(decision.duplicate)
        self.assertEqual(decision.signals, ("url_identity",))

        near_copy = dict(
            base,
            source="lever",
            external_id="other-456",
            canonical_url="https://jobs.example/other-456",
            description=base["description"],
        )
        decision = dedupe.compare(base, near_copy)
        self.assertTrue(decision.duplicate)
        self.assertIn("title_company", decision.signals)
        self.assertIn("description_embedding", decision.signals)


class ScoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="careeros-score-test-")
        self.db_path = Path(self.temp_dir.name) / "test.sqlite"
        self.conn = db.connect(self.db_path)
        db.migrate(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.temp_dir.cleanup()

    def _posting(
        self,
        *,
        company: str = "Monzo",
        title: str = "Graduate Finance Analyst",
        location: str = "London, United Kingdom",
        description: str = "Entry-level analysis using Excel, SQL and financial reporting.",
        external_id: str = "job-1",
        url: str = "https://example.test/jobs/job-1",
        class_year_rule: str | None = None,
        closes_at: str | None = "2026-10-15",
    ) -> int:
        company_id = db.upsert_company(company, conn=self.conn)
        return db.upsert_posting(
            {
                "company_id": company_id,
                "source": "greenhouse",
                "external_id": external_id,
                "canonical_url": url,
                "url": url,
                "title": title,
                "location": location,
                "description": description,
                "track": "7-fintech-ops-and-analyst",
                "source_match": 1,
                "class_year_rule": class_year_rule,
                "closes_at": closes_at,
            },
            conn=self.conn,
        )

    def test_dedupe_prefers_london_copy(self) -> None:
        first = self._posting(
            location="Manchester, United Kingdom",
            external_id="one",
            url="https://example.test/jobs/one",
        )
        second = self._posting(
            location="London, United Kingdom",
            external_id="two",
            url="https://other.test/jobs/two",
        )
        result = dedupe.dedupe(self.conn)
        self.assertEqual(result["duplicates"], 1)
        kept = self.conn.execute("SELECT duplicate_of, status FROM postings WHERE id = ?", (second,)).fetchone()
        dropped = self.conn.execute("SELECT duplicate_of, status FROM postings WHERE id = ?", (first,)).fetchone()
        self.assertIsNone(kept["duplicate_of"])
        self.assertEqual(dropped["duplicate_of"], second)
        self.assertEqual(dropped["status"], "ignored")

    def test_rules_reject_explicit_wrong_class_year(self) -> None:
        posting_id = self._posting(class_year_rule="Open only to students graduating in 2026 or 2027")
        row = self.conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
        result = score.rule_score(
            row,
            today=date(2026, 9, 22),
            ordered_tracks=["7-fintech-ops-and-analyst"],
            company_priority=1,
            graduation_year=2025,
            salary_floor_gbp=None,
            requires_sponsorship=None,
            applied_company_ids=set(),
        )
        self.assertFalse(result.eligible)
        self.assertIn("excludes a 2025 graduate", result.reasons[0])
        self.assertIn("ask the recruiter", result.missing_requirements[0])

    def test_rules_do_not_infer_salary_or_sponsorship_preferences(self) -> None:
        posting_id = self._posting()
        row = self.conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
        result = score.rule_score(
            row,
            today=date(2026, 9, 22),
            ordered_tracks=["7-fintech-ops-and-analyst"],
            company_priority=1,
            graduation_year=2025,
            salary_floor_gbp=None,
            requires_sponsorship=None,
            applied_company_ids=set(),
        )
        self.assertTrue(result.eligible)
        self.assertGreaterEqual(result.score, 80)
        self.assertTrue(any("sponsorship requirement is not configured" in item for item in result.missing_requirements))
        self.assertTrue(any("salary floor is not configured" in item for item in result.missing_requirements))

    def test_rules_reject_non_uk_and_senior_roles(self) -> None:
        non_uk_id = self._posting(location="Paris, France", description="Hybrid finance analyst role.")
        senior_id = self._posting(
            external_id="job-2",
            url="https://example.test/jobs/job-2",
            title="Senior Finance Analyst",
        )
        for posting_id, expected in ((non_uk_id, "no UK"), (senior_id, "senior")):
            row = self.conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
            result = score.rule_score(
                row,
                today=date(2026, 9, 22),
                ordered_tracks=["7-fintech-ops-and-analyst"],
                company_priority=1,
                graduation_year=2025,
                salary_floor_gbp=None,
                requires_sponsorship=None,
                applied_company_ids=set(),
            )
            self.assertFalse(result.eligible)
            self.assertIn(expected, result.reasons[0])

    def test_generic_analyst_gets_less_credit_than_explicit_graduate(self) -> None:
        graduate_id = self._posting()
        analyst_id = self._posting(
            external_id="job-2",
            url="https://example.test/jobs/job-2",
            title="Finance Analyst",
        )
        results = []
        for posting_id in (graduate_id, analyst_id):
            row = self.conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
            results.append(
                score.rule_score(
                    row,
                    today=date(2026, 9, 22),
                    ordered_tracks=["7-fintech-ops-and-analyst"],
                    company_priority=1,
                    graduation_year=2025,
                    salary_floor_gbp=None,
                    requires_sponsorship=None,
                    applied_company_ids=set(),
                )
            )
        self.assertGreater(results[0].score, results[1].score)
        self.assertTrue(any("seniority needs confirmation" in item for item in results[1].missing_requirements))

    @unittest.skipUnless((config.DATA_DIR / "registry.yaml").exists(), "needs the private registry")
    def test_score_and_export_shortlist(self) -> None:
        posting_id = self._posting()
        active = settings.Settings(
            data={
                "tracks": ["7-fintech-ops-and-analyst"],
                "caps": {"cycle_start": "2026-09-01"},
                "scoring": {
                    "candidate_graduation_year": 2025,
                    "salary_floor_gbp": None,
                    "requires_sponsorship": None,
                    "shortlist_threshold": 60,
                    "shortlist_limit": 25,
                },
            }
        )
        result = score.score_postings(
            self.conn,
            today=date(2026, 9, 22),
            active_settings=active,
        )
        self.assertEqual(result["eligible"], 1)
        row = self.conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
        self.assertEqual(row["status"], "shortlisted")
        self.assertGreaterEqual(row["rule_score"], 60)

        output = Path(self.temp_dir.name) / "shortlist.md"
        score.export_shortlist(
            self.conn,
            run_date=date(2026, 9, 22),
            active_settings=active,
            path=output,
        )
        markdown = output.read_text(encoding="utf-8")
        self.assertIn("Graduate Finance Analyst", markdown)
        self.assertIn("Duplicate rows are excluded", markdown)
        self.assertIn("Rule-only score", markdown)

    @unittest.skipUnless((config.DATA_DIR / "registry.yaml").exists(), "needs the private registry")
    def test_registry_class_year_rule_fills_missing_posting_field(self) -> None:
        posting_id = self._posting(
            company="Zopa",
            title="2027 Graduate Analyst",
            class_year_rule=None,
        )
        active = settings.Settings(
            data={
                "tracks": ["7-fintech-ops-and-analyst"],
                "caps": {"cycle_start": "2026-09-01"},
                "scoring": {
                    "candidate_graduation_year": 2025,
                    "salary_floor_gbp": None,
                    "requires_sponsorship": None,
                    "shortlist_threshold": 60,
                },
            }
        )
        score.score_postings(
            self.conn,
            today=date(2026, 9, 22),
            active_settings=active,
        )
        row = self.conn.execute("SELECT status, rule_reasons FROM postings WHERE id = ?", (posting_id,)).fetchone()
        self.assertEqual(row["status"], "ignored")
        self.assertIn("excludes a 2025 graduate", row["rule_reasons"])

    def test_evidence_rubric_rejects_unknown_claim_id(self) -> None:
        class FakeClient:
            def generate_json(self, prompt: str) -> dict[str, object]:
                return {
                    "fit": 95,
                    "reasons": ["Invented support"],
                    "missing_requirements": [],
                    "cited_claims": ["C-9999"],
                }

        posting = {
            "title": "Graduate Analyst",
            "company_name": "Example",
            "location": "London",
            "description": "Analyse data.",
        }
        with self.assertRaisesRegex(ValueError, "not supplied"):
            score.evidence_rubric(
                FakeClient(),  # type: ignore[arg-type]
                posting,
                [{"id": "C-0019", "text": "Confirmed degree fact.", "subject": "Education"}],
            )

    def test_claim_retrieval_excludes_application_specific_motivation(self) -> None:
        common = {
            "subject": "Example",
            "source_id": "test-source",
            "locator": "test",
            "quote": "test evidence",
            "sensitivity": "free",
            "tier": "safe",
            "confidence": "verified",
            "status": "confirmed",
        }
        self.conn.execute("INSERT INTO sources (id, path, kind) VALUES ('test-source', 'test.md', 'test')")
        self.conn.commit()
        portable_id = db.insert_claim(
            {**common, "text": "the candidate used Excel to analyse financial data.", "category": "skill"},
            conn=self.conn,
        )
        motivation_id = db.insert_claim(
            {**common, "text": "the candidate wanted to join Example Firm for its culture.", "category": "motivation"},
            conn=self.conn,
        )
        approval_id = db.insert_claim(
            {**common, "text": "the candidate raised accuracy to a specific figure.", "category": "achievement",
             "sensitivity": "use-with-approval"},
            conn=self.conn,
        )
        ids = {claim["id"] for claim in score._load_safe_claims(self.conn)}
        self.assertIn(portable_id, ids)
        self.assertNotIn(motivation_id, ids)
        # Sensitivity is a boundary too: use-with-approval facts never reach a model prompt unasked.
        self.assertNotIn(approval_id, ids)

    def test_migration_preserves_old_match_signal(self) -> None:
        # A database stopped at migration 002 should retain its T-011 1/0 value
        # when migration 003 moves it to source_match.
        legacy_path = Path(self.temp_dir.name) / "legacy.sqlite"
        legacy = db.connect(legacy_path)
        try:
            migration_paths = sorted(Path("pipeline/migrations").glob("00[12]_*.sql"))
            legacy.execute(
                "CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT DEFAULT (datetime('now')))"
            )
            for path in migration_paths:
                legacy.executescript(path.read_text(encoding="utf-8"))
                legacy.execute("INSERT INTO schema_migrations (version) VALUES (?)", (path.stem,))
            company_id = legacy.execute("INSERT INTO companies(name) VALUES ('Legacy Co') RETURNING id").fetchone()[0]
            legacy.execute(
                "INSERT INTO postings(company_id, canonical_url, title, rule_score) VALUES (?, ?, ?, 1)",
                (company_id, "https://example.test/legacy", "Graduate Analyst"),
            )
            legacy.commit()
            db.migrate(legacy)
            migrated = legacy.execute("SELECT source_match, rule_score FROM postings").fetchone()
            self.assertEqual(migrated["source_match"], 1)
            self.assertIsNone(migrated["rule_score"])
        finally:
            legacy.close()


if __name__ == "__main__":
    unittest.main()
