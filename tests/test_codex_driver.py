"""Static and dry-run checks for T-018; no model, network or live database."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROLES = ("researcher", "drafter", "verifier", "style-critic", "red-team", "coach")


class CodexDriverTest(unittest.TestCase):
    def test_shared_rules_and_role_prompts(self) -> None:
        self.assertIn("@AGENTS.md", (ROOT / "CLAUDE.md").read_text(encoding="utf-8"))
        for role in ROLES:
            self.assertTrue((ROOT / "codex" / "prompts" / f"{role}.md").is_file(), role)
        self.assertIn("`gpt-6-sol`", (ROOT / "drivers" / "codex-sol.md").read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7 is not installed")
    def test_draft_coordinator_dry_run_has_no_live_side_effects(self) -> None:
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(ROOT / "scripts" / "codex-draft.ps1"),
             "-Target", "posting:1234", "-DryRun"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Would initialise posting:1234", result.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7 is not installed")
    def test_checker_dry_run_is_read_only_and_selects_model(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-test-", dir=ROOT) as temp:
            draft = Path(temp) / "drafts" / "answer-1-r1.md"
            draft.parent.mkdir()
            draft.write_text("test draft", encoding="utf-8")
            result = subprocess.run(
                ["pwsh", "-NoProfile", "-File", str(ROOT / "codex" / "invoke-agent.ps1"),
                 "-Role", "style-critic", "-Target", str(draft), "-Model", "gpt-6-luna", "-DryRun"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("model=gpt-6-luna", result.stdout)
            self.assertIn("sandbox=read-only", result.stdout)
            self.assertIn("codex-style-critic-answer-1-r1-gpt-6-luna.md", result.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell 7 is not installed")
    def test_checker_rejects_target_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-outside-") as temp:
            draft = Path(temp) / "drafts" / "answer-1-r1.md"
            draft.parent.mkdir()
            draft.write_text("test draft", encoding="utf-8")
            result = subprocess.run(
                ["pwsh", "-NoProfile", "-File", str(ROOT / "codex" / "invoke-agent.ps1"),
                 "-Role", "verifier", "-Target", str(draft), "-DryRun"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Target must exist inside CareerOS", result.stderr)


if __name__ == "__main__":
    unittest.main()
