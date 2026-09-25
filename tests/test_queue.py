"""T-031: the per-driver queue computed from TASKS.md."""
from __future__ import annotations

import unittest

from pipeline import queue

TASKS = """# TASKS

## Phase 1

### T-001 Base
- Status: done (2026-09-21)

### T-002 Build thing
- Status: todo
- Models: Claude Opus 5.5 high · Codex Sol high · Prefer: either · Skip if driver is: —
- Depends on: T-001

### T-003 Draft thing
- Status: todo
- Models: Claude Fable 5.1 xhigh · Codex n/a · Prefer: Claude Fable · Skip if driver is: Codex
- Depends on: T-002

### T-004 Codex tooling
- Status: in-progress (Codex Sol, 2026-09-23) — half done
- Models: Claude n/a · Codex Sol xhigh · Prefer: Codex · Skip if driver is: Claude
- Depends on: T-001

### T-005 Hooks
- Status: todo
- Models: Claude Opus 5.5 high (hooks) · Codex Sol high (wrappers) · Prefer: both · Skip if driver is: —
- Depends on: T-004

### T-006 Mine
- Status: in-progress (Claude Fable, 2026-09-23)
- Models: Claude Fable 5.1 high · Codex Sol high · Prefer: Claude · Skip if driver is: —
- Depends on: T-001
"""


class QueueTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tasks = queue.parse_tasks(TASKS)

    def test_parse(self) -> None:
        by_id = {t["id"]: t for t in self.tasks}
        self.assertEqual(by_id["T-004"]["owner"], "codex-sol")
        self.assertEqual(by_id["T-006"]["owner"], "claude-fable")
        self.assertEqual(by_id["T-003"]["prefer"], "Claude Fable")
        self.assertEqual(by_id["T-003"]["skip"], "Codex")
        self.assertEqual(by_id["T-005"]["depends"], ["T-004"])

    def test_fable_queue(self) -> None:
        r = queue.evaluate(self.tasks, "claude-fable")
        self.assertEqual([t["id"] for t in r["go"]], ["T-002", "T-006"])
        blocked = {t["id"]: t["reasons"] for t in r["blocked"]}
        self.assertIn("T-003", blocked)            # waits for T-002
        self.assertIn("T-005", blocked)            # waits for Codex's T-004
        self.assertIn("codex-sol", blocked["T-005"][0])

    def test_opus_cannot_take_fable_task_or_fable_claim(self) -> None:
        r = queue.evaluate(self.tasks, "claude-opus")
        ids = [t["id"] for t in r["go"]] + [t["id"] for t in r["blocked"]]
        self.assertNotIn("T-003", ids)
        self.assertNotIn("T-006", ids)
        self.assertIn("T-002", [t["id"] for t in r["go"]])

    def test_codex_queue_and_verdict(self) -> None:
        r = queue.evaluate(self.tasks, "codex-sol")
        self.assertEqual([t["id"] for t in r["go"]], ["T-002", "T-004"])
        text = queue.verdict(self.tasks, "codex-sol")
        self.assertTrue(text.startswith("Driver: codex-sol\nGO: T-002"))
        wait = queue.verdict([t for t in self.tasks if t["id"] in {"T-001", "T-003"}], "codex-sol")
        self.assertIn("WAIT", wait)


if __name__ == "__main__":
    unittest.main()
