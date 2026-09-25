# CLAUDE.md — CareerOS (Claude Code)

@AGENTS.md

## Claude-specific notes
- Use the `claude-mem` skill conventions: `.claude-mem/STATE.md`, `DECISIONS.md`, `GOTCHAS.md` are the shared project memory (Codex reads them too). Do not rely on your private auto-memory for anything the next driver needs.
- Skills live in `.claude/skills/` and agents in `.claude/agents/` (to be created in T-017). Each skill should wrap a Python script in `pipeline/` so Codex can run the same logic through `codex/` wrappers.
- Hooks in `.claude/settings.json` enforce autonomy settings, the submit guard, the sensitive-field guard and event logging (T-022). Never bypass a hook by calling the browser tool differently.
- Browser: prefer Playwright MCP with the dedicated profile under `%USERPROFILE%\CareerOS-data\browser-profile\` for ATS work; use the Claude in Chrome extension only for LinkedIn reads in the candidate's own Chrome and for ad-hoc checks.
- Connectors: the claude.ai Gmail and Google Calendar connectors are available in Claude Code sessions for interactive triage; the unattended poller uses its own OAuth client (T-015).
- Default effort for pipeline work: high. Use xhigh or max only for drafting, verification, narrative writing and security-sensitive code. Use Sonnet or Haiku subagents for bulk mechanical work.
- When a usage limit hits, follow the protocol in `HANDOVER.md` section 3.
