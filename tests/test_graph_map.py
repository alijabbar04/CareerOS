"""Offline tests for the deterministic CareerOS Graphify map."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.graph_map import build_graph


class GraphMapTests(unittest.TestCase):
    def test_build_links_story_competency_claim_and_number(self) -> None:
        with tempfile.TemporaryDirectory(prefix="careeros-graph-test-") as raw_dir:
            root = Path(raw_dir)
            story_dir = root / "brain" / "vault" / "stories"
            source_dir = root / "brain" / "sources"
            story_dir.mkdir(parents=True)
            source_dir.mkdir(parents=True)
            (story_dir / "accuracy.md").write_text(
                """---
id: story-accuracy
kind: story
competencies: [attention-to-detail, accuracy]
claims: [C-0001]
---
# Accuracy story
## Result
I checked four models and achieved 97% [C-0001].
""",
                encoding="utf-8",
            )
            (source_dir / "cv.md").write_text(
                "# CV source\n\nThe four models produced a 97% average [C-0001].\n",
                encoding="utf-8",
            )
            output = root / "graphify-out"

            metadata = build_graph(root, output)
            graph = json.loads((output / "graph.json").read_text(encoding="utf-8"))
            labels = {node["label"] for node in graph["nodes"]}
            relations = {edge["relation"] for edge in graph["links"]}

            self.assertEqual(metadata["files"], 2)
            self.assertIn("Accuracy story", labels)
            self.assertIn("Attention to detail", labels)
            self.assertIn("Claim C-0001", labels)
            self.assertIn("Quantified result (number)", labels)
            self.assertTrue(any(label.startswith("Quantified evidence: 97%") for label in labels))
            self.assertIn("demonstrates", relations)
            self.assertIn("references_claim", relations)
            self.assertIn("states_quantified_evidence", relations)
            number_node = next(node for node in graph["nodes"] if node["label"] == "Quantified result (number)")
            story_node = next(node for node in graph["nodes"] if node["label"] == "Accuracy story")
            number_neighbors = {
                edge["source"] if edge["target"] == number_node["id"] else edge["target"]
                for edge in graph["links"]
                if number_node["id"] in (edge["source"], edge["target"])
            }
            source_result_ids = {
                node["id"]
                for node in graph["nodes"]
                if node.get("source_file") == "brain/sources/cv.md"
                and node["label"].startswith("Quantified evidence:")
            }
            self.assertTrue(number_neighbors)
            self.assertTrue(number_neighbors.isdisjoint(source_result_ids))
            self.assertTrue(any(edge["source"] == story_node["id"] or edge["target"] == story_node["id"] for edge in graph["links"]))
            report = (output / "GRAPH_REPORT.md").read_text(encoding="utf-8")
            self.assertIn("not a source of truth", report)


if __name__ == "__main__":
    unittest.main()
