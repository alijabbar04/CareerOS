"""Unit tests for pipeline.claims merge logic (no database needed)."""
from __future__ import annotations

import unittest

from pipeline import claims


def cand(text, category="achievement", source="s1", **kw):
    base = {"text": text, "category": category, "subject": "Argos", "source_id": source, "locator": "p1",
            "quote": text, "numbers": [], "names": [], "strength_verb": "none", "tags": [], "confidence": "verified", "_batch": "t"}
    base.update(kw)
    return base


class MergeTests(unittest.TestCase):
    def test_near_identical_claims_merge_and_keep_most_specific(self):
        a = cand("the candidate achieved 325 policy sales at Argos", numbers=["325"], strength_verb="achieved")
        b = cand("the candidate achieved 325 policy sales at Argos.", source="s2")
        c = cand("the candidate achieved 325 policy sales while at Argos", source="s3", numbers=["325"])
        merged = claims.merge([a, b, c])
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]["sources"]), 3)
        self.assertIn("325", merged[0]["numbers"])
        self.assertEqual(merged[0]["strength"], "achieved")

    def test_different_categories_do_not_merge(self):
        a = cand("the candidate worked at Argos from June 2022 to March 2023", category="experience")
        b = cand("the candidate worked at Argos from June 2022 to March 2023", category="fact")
        self.assertEqual(len(claims.merge([a, b])), 2)

    def test_distinct_facts_stay_separate_and_conflicts_flag(self):
        a = cand("the candidate worked at Argos from June 2022 to March 2023", category="experience", date_or_range="2022-06 to 2023-03")
        b = cand("the candidate worked at Argos from June 2022 to October 2023", category="experience", date_or_range="2022-06 to 2023-10")
        merged = claims.assign_ids(claims.merge([a, b]))
        claims.find_conflicts(merged)
        self.assertEqual(len(merged), 2)
        self.assertTrue(merged[0]["conflicts_with"] or merged[1]["conflicts_with"])

    def test_ids_are_sequential_and_ordered_by_category(self):
        merged = claims.assign_ids(claims.merge([cand("x fact", category="fact"), cand("QMUL BSc Economics 2:1", category="education")]))
        self.assertEqual([c["id"] for c in merged], ["C-0001", "C-0002"])
        self.assertEqual(merged[0]["category"], "education")


if __name__ == "__main__":
    unittest.main()
