"""T-030: the calibration tabulator reads per-arm reports and folds in usage."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline import calibrate


class CalibrateTestCase(unittest.TestCase):
    def test_summary_and_markdown(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="careeros-calib-"))
        app = root / "0001-example"
        for arm, rubric, hard in (("fable", 18, 0), ("opus", 16, 1)):
            d = app / "calibration" / arm
            (d / "drafts").mkdir(parents=True)
            (d / "reports").mkdir()
            (d / "drafts" / "answer-1-r1.md").write_text("x", encoding="utf-8")
            (d / "reports" / "verify-answer-1-r1.json").write_text(json.dumps({
                "hard_fails": ["h"] * hard, "warnings": ["w"], "review": {"fact_requests": []},
                "metrics": {"words": 280, "claims_per_100_words": 2.5, "research_sentences": 3, "sentence_len_sd_ratio": 0.41},
            }), encoding="utf-8")
            (d / "reports" / "verifier-answer-1-r1.md").write_text("VERDICT: PASS\n", encoding="utf-8")
            (d / "reports" / "critic-answer-1-r1.md").write_text("STYLE: PASS\n", encoding="utf-8")
            (d / "reports" / "redteam-answer-1-r1.md").write_text(f"RED TEAM: PASS\nRubric {rubric}/25\n", encoding="utf-8")
            (d / "usage.json").write_text(json.dumps({"tokens": 1000, "rounds": {"answer-1": 1}}), encoding="utf-8")
        fable = calibrate.summarise(app / "calibration" / "fable")
        self.assertEqual(fable["rubric_mean"], 18.0)
        self.assertEqual(fable["hard_total"], 0)
        opus = calibrate.summarise(app / "calibration" / "opus")
        self.assertEqual(opus["hard_total"], 1)
        md = calibrate.markdown(app, ["fable", "opus"])
        self.assertIn("| fable | 1 | 0 | 1 | 1/1 | 1/1 | 1/1 | 18.0 |", md)
        self.assertIn("| opus | 1 | 1 | 1 |", md)


if __name__ == "__main__":
    unittest.main()
