# Driver: Codex CLI on GPT-6 Sol (`codex-sol`)

Default CLI model id: `gpt-6-sol` (`model_reasoning_effort = "xhigh"` in `%USERPROFILE%\.codex\config.toml`, verified 2026-09-23). The official [GPT-6 Sol model page](https://developers.openai.com/api/docs/models/gpt-6-sol) and [Codex config guide](https://learn.chatgpt.com/docs/config-file/config-basic) use this id. A running Codex session may keep the model with which it started; open a new session to pick up the default.

CLI 0.146 rejected Sol with a model-not-supported error on the ChatGPT login. Upgrading to 0.156.1 per the [Codex changelog](https://learn.chatgpt.com/docs/changelog) fixed the live read-only `codex exec` call. Check `codex --version` if this reappears; do not silently fall back to 5.6.

## What this model is for
Everything that configures Codex itself and the modules Codex built: T-018 Codex driver mode (prompt files mirroring each Claude agent, `codex exec` wrappers for `/draft`, `/verify`, `/review`, the Sol-versus-Luna critic half of T-030), T-015 Gmail poller live acceptance, T-035 morning run acceptance, and the wrapper half of T-022. Codex is also the independent second model family: style critic and second grounding check on final drafts when Claude drives.

## What it does not take
- Anything under `.claude/`, desktop scheduled tasks, Remote Control, Claude connectors, or LinkedIn edits (Claude only).
- Anything `in-progress (Claude ...)`.
- Security sign-off on its own vault or fill code (Claude reviews).
- Model Astra unless the candidate asks; GPT-6 Luna or Terra for bulk mechanical steps; never fall back to the 5.6 tiers (the candidate, 2026-09-23).

## Standing priorities (checked by `python -m pipeline.queue --driver codex-sol`)
1. T-018 Codex driver mode, now unblocked.
2. T-015 live acceptance after the candidate's Google consent; T-035 first unattended weekday run.
3. The wrapper checks of T-022 once Opus lands the hooks.
4. Reviews Claude requests in `STATE.md`.

## How to work here
- `git pull --rebase` first; claim in `TASKS.md` as `in-progress (Codex Sol, <date>)` and push before editing anything else; commit only your own files.
- The live data folder is `%USERPROFILE%\CareerOS-data` (never `%LOCALAPPDATA%`; Claude Desktop redirects that path).
- Before believing the database is absent, print `config.DB_PATH` and a claims count.
