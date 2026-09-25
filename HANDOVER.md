# HANDOVER — read this first

You are an AI (Claude Code or OpenAI Codex CLI) picking up CareerOS for Candidate Name. This file tells you what the project is, where everything lives, how to choose work, and how to leave the project so the next session (which may be the other model) can continue without you.

## 1. What this is, in three sentences

CareerOS is the candidate's local job-search automation: a verified "brain" about the candidate, a discovery pipeline for UK graduate and entry-level roles in accountancy, finance and product operations, a draft-verify-critique loop that writes applications in the candidate's voice from evidence only, form filling and (at higher autonomy levels) submission, a tracker, an inbox watcher and notifications. The full design is in `plan/PLAN.md`; the schemas, agents and guard rails are in `plan/appendix-B-architecture-detail.md`. Phase 0 and much of Phase 1 are built; `.claude-mem/STATE.md` and `TASKS.md` are the authoritative live status, including each driver's latest hand-off.

## 2. Resume procedure (every session)

1. Read `.claude-mem/STATE.md` (what was in progress, the next step, anything blocked).
2. Read `TASKS.md`. Pick the first `todo` task whose `Depends on` tasks are `done` and whose favoured model matches you, or whose alternative allows you. If a task says "skip if driver is <you>", skip it and take the next one.
3. Read only the plan sections the task points to. Do not re-read all the research.
4. Do the task. Prefer small commits. Run whatever tests or checks the task's acceptance criteria name.
5. Before you stop, for any reason: update the task's `Status`, rewrite `.claude-mem/STATE.md` (Goals / In progress / Next step / Blocked, under 40 lines), append to `.claude-mem/DECISIONS.md` if you chose between alternatives, append to `.claude-mem/GOTCHAS.md` if something cost time, then commit and push.

## 3. Usage-limit protocol

the candidate runs Claude on a Max plan and Codex on a Max 20x plan and switches between them when one hits a limit. When you see a rate-limit or usage-limit error, or you have reason to think one is close:

1. Finish the smallest coherent unit of work, or stop cleanly mid-task.
2. Write the exact next step into `.claude-mem/STATE.md`, including file paths and the command to run next.
3. Commit with the prefix `wip:` if the work is incomplete, and push.
4. Tell the candidate in one line what to say to the other model: "Continue CareerOS: read HANDOVER.md, then STATE.md, then take task T-0NN."

## 4. Who does what: Claude versus Codex

`TASKS.md` tags every task with a favoured model and effort, an alternative, and a skip rule. The logic:

- **Claude only:** anything that configures Claude Code itself (`.claude/` skills, subagents, hooks, desktop scheduled tasks, Remote Control, Claude connectors such as Gmail), and interactive sessions with the candidate where conversation matters (claims review, narrative writing).
- **Codex only:** anything that configures Codex itself (`codex exec` wrappers, `AGENTS.md` parity, Codex-side prompt files, the Codex driver mode test).
- **Either:** Python pipeline code, SQL, fetchers, parsers, the vault helper, browser recipes, research, documentation, tests. Where both are acceptable the favoured one is named for quality reasons; the other may still take it if the favoured model is unavailable.
- **Effort mapping:** Claude effort levels are `low | medium | high | xhigh | max`; Codex reasoning effort is `low | medium | high | xhigh`. the candidate usually prompts the driver (Fable 5.1, Opus 5.5 or GPT-6 Sol) at extra-high; the driver delegates subtasks at the effort in the task card. Use cheap models (Sonnet, Haiku, or Codex at low/medium) for bulk mechanical work; use the top models for anything the candidate or an employer will read.

## 5. Hard rules (apply to every task, every level)

- Never invent, round up or inflate a fact about the candidate. If a fact is missing, ask or leave it out.
- Never complete an online test, situational judgement test, game-based assessment, video interview or live interview.
- Never put a password, National Insurance number, passport number, share code or bank detail into a prompt, a log, a commit or a screenshot. The vault helper fills those; you name the key.
- Respect `settings.yaml`: autonomy level, per-action overrides and caps. One application per employer per cycle. Class-year filter. Stop rule on any CAPTCHA, verification or platform warning: park it, notify the candidate, move on.
- Where an employer prohibits AI-written content, switch to research-and-proofread mode and log it.
- LinkedIn: read-only unless settings allow more; human pace; one tab; no scraping; no anti-detection tooling ever.
- Do not run destructive git commands. Do not force-push. Do not rewrite history.

## 6. Conventions

- **Centre point:** this folder. Anything saved elsewhere on the machine must be listed in `docs/EXTERNAL-LOCATIONS.md` with its purpose.
- **Commits:** author is the candidate (`<github-user> <candidate@example.com>`, already the global git identity). Do **not** add Co-Authored-By or any AI attribution lines. Conventional prefixes: `feat:`, `fix:`, `docs:`, `chore:`, `wip:`.
- **Branch:** `main` only until Phase 2; feature branches after that if parallel drivers are working.
- **Keep the repository lean:** the laptop has about 19 GB free. No binaries, no browser builds, no models, no databases, no node_modules or venvs in git. Extracted text yes, original PDFs and DOCX no (they stay in the <careers-folder> folder).
- **Language:** UK English. Dates as `2026-09-21`. Money in GBP.
- **Research citations:** every claim about an employer, deadline or platform rule carries a URL and a retrieval date; mark anything unverified as UNVERIFIED.
- **Memory:** `.claude-mem/` is the shared project memory for both models. Claude's own auto-memory directory is separate and not part of the repo.

## 7. Where things are (short version; full list in `docs/EXTERNAL-LOCATIONS.md`)

- the candidate's source documents: `C:\Users\<user>\OneDrive\Documents\<careers-folder>\<careers-folder>` (CVs, cover letters, question banks, speculative emails) and `C:\Users\<user>\AppData\Local\WorkHoursTracker\Work_Hours_Tracker.xlsx` (the current employer work log).
- Live data: `%USERPROFILE%\CareerOS-data\` holds `careeros.sqlite` and will hold `vault\`, `browser-profile\`, `exports\` and `logs\` as their tasks land. Never use the live database in tests.
- the candidate's other tools on this machine: AI Account Manager (`C:\Users\<user>\Documents\Projects\AI Account Manager`, an Electron app that isolates Claude Code accounts by config directory and tracks Codex usage and API keys), a Discord bot (`C:\Users\<user>\claude-discord-bot`, with a `.env` holding the bot token), and ApplAI v2 (`C:\Users\<user>\Documents\Projects\ApplAI\ApplAI-v2`, an earlier Electron/TypeScript attempt with encrypted claims storage worth reading before building the vault).

## 8. Open questions for the candidate (do not guess these)

See `plan/PLAN.md` section 9 "Still open": which conflicting facts are correct; the wording for "did you use AI?"; unique versus single password for portals; which AI Account Manager accounts may be rotated; which OpenRouter free models are allowed; which Ollama models can be deleted to free disk.

## Two drivers at once (claim protocol, added 2026-09-23)

the candidate may run Claude Code and Codex on this repository at the same time. Start with `/careeros` (Claude) or `/prompts:careeros` / `.\codex\careeros.ps1` (Codex); both do the same thing:

1. `git pull --rebase origin main` first. Unstaged files you did not create belong to the other driver: never stash, reset or edit them.
2. Pick a task by its `Models` line (`Prefer: Claude`, `Prefer: Codex` or `either`), never one marked `Skip if driver is: <you>` and never one that is `in-progress (<other driver>, ...)`.
3. Claim it before working: set `Status: in-progress (<driver and model>, <date>)`, for example `Claude Fable`, `Claude Opus` or `Codex Sol`, so two Claude sessions can tell each other apart, commit only `TASKS.md` (`claim T-0xx (<driver>)`) and push. A rejected push means pull --rebase and check the claim again.
4. Commit only your own files with `git add <paths>`; pull --rebase before each push; no `git add -A`.
5. Before stopping: Status line, `STATE.md`, `DECISIONS.md` / `GOTCHAS.md`, commit, push. `STATE.md` is rewritten by whoever stops last; keep the other driver's in-progress lines.

## The board (three drivers, added 2026-09-23)

- `python -m pipeline.queue --board` shows every driver's queue, each driver's own status file, the last handoffs and what waits on the candidate. Start there; `/careeros` runs it for you.
- `.claude-mem/status/<driver id>.md` is written only by that driver, at every stop. `STATE.md` keeps shared facts (database, decisions, dates); nobody rewrites another driver's lines.
- `.claude-mem/handoffs.md` is append-only: when you finish something another driver waits for, add one line (`date time from -> to: what, where to look`). The receiving driver reads it before picking a task.
- Honesty rule: a driver with nothing offered says so and stops. Idle is correct; invented work is not.
