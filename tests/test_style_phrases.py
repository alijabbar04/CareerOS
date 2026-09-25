"""T-039: the phrase lists flag the patterns adopted from the skills audit and stay quiet on the candidate-like prose."""
from __future__ import annotations

import unittest

import yaml

from pipeline import style


class StylePhrasesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.lists = yaml.safe_load(style.PHRASES_PATH.read_text(encoding="utf-8"))

    def test_adopted_patterns_are_flagged(self) -> None:
        text = "As an AI language model I cannot say. I worked hard in order to deliver the audit on time."
        hits = style.phrase_hits(text, self.lists)
        self.assertIn("as an ai", hits["banned_hits"])
        self.assertIn("in order to", hits["discouraged_hits"])

    def test_ali_like_prose_is_clean(self) -> None:
        text = ("At Argos I sold FCA-regulated products and kept a 100% positive feedback record over 278 customer interactions. "
                "In my Corporate Finance module I analysed Porsche's AGM proposals using DCF and scenario analysis. "
                "What particularly attracts me to the firm is the breadth of its client work.")
        hits = style.phrase_hits(text, self.lists)
        self.assertEqual(hits["banned_hits"], {})
        self.assertEqual(hits["discouraged_hits"], {})

    def test_finance_nouns_are_not_banned(self) -> None:
        text = "The leverage ratios and the regression harness were part of the analysis."
        hits = style.phrase_hits(text, self.lists)
        self.assertEqual(hits["banned_hits"], {})


if __name__ == "__main__":
    unittest.main()
