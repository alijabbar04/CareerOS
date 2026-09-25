# Codex driver

The shared rules are in `AGENTS.md`, imported by `CLAUDE.md`. Codex CLI and the VS Code extension use the user-level default `gpt-6-sol` at xhigh; see `drivers/codex-sol.md`. The wrapper entry point `careeros.ps1` runs the resume prompt (`careeros.md`, also available as `/prompts:careeros` in Codex).

T-018 adds one prompt per Claude agent in `codex/prompts/` and one fresh `codex exec --ephemeral` process per role through `codex/invoke-agent.ps1`. Writer roles have workspace-write access; verifier, style critic, red team, grounding check and review use read-only sandboxing. Checker final messages are saved under the application's `reports/` by `--output-last-message`. The deterministic verifier runs before the independent model checker. The scripts never submit, fill, send, approve a claim, or modify `.claude/`.

Run from the repository root in PowerShell:

```powershell
.\scripts\codex-research.ps1 -Company 'Example Firm'
.\scripts\codex-draft.ps1 -Target 'posting:1234'       # or an application ID
.\scripts\codex-verify.ps1 -Draft 'brain/vault/applications/0001-example/drafts/answer-1-r1.md'
.\scripts\codex-style.ps1 -Draft 'brain/vault/applications/0001-example/drafts/answer-1-r1.md'
.\scripts\codex-red-team.ps1 -Draft 'brain/vault/applications/0001-example/drafts/answer-1-r1.md'
.\scripts\codex-grounding.ps1 -Draft 'brain/vault/applications/0001-example/drafts/answer-1-r1.md'
.\scripts\codex-review.ps1 -Application 55
.\scripts\codex-prep.ps1 -ApplicationFolder 'brain/vault/applications/0001-example'
```

Every wrapper accepts `-DryRun` to show the selected role/model/sandbox without calling a model or touching the live database. `codex-draft.ps1` coordinates at most three rounds with separate drafter and checker sessions; it refuses to work in an application folder with uncommitted files and only registers drafts after all gates pass. A failed draft remains unregistered. `codex-review.ps1` is read-only: the candidate's approval belongs in an interactive review, not a non-interactive CLI run. For second-family review of a Claude draft, use `codex-grounding.ps1` and leave the report for Claude and the candidate; a PASS is not submit permission.

Known acceptance still pending: a full application run under `driver: codex` and independent Claude review. Do not switch the shared default driver or use an application another driver is actively drafting just to satisfy a test.
