# AGENTS.md — shared rules for Claude Code and Codex

This file is read by Codex directly and imported by `CLAUDE.md` for Claude Code, so both models work to the same rules.

## Before doing anything
1. Read `HANDOVER.md`.
2. Read `.claude-mem/STATE.md`.
3. Open `TASKS.md`, pick the next task that fits you (favoured model, or alternative if the favoured one is unavailable; never a task marked "skip if driver is <you>").

## Hard rules
- No invented, rounded-up or inflated facts about the candidate; every factual sentence in any application text cites a confirmed claim ID.
- No completing assessments, tests or interviews of any kind.
- No secrets in prompts, logs, commits or screenshots; the vault helper fills identity and credential fields by key name.
- Obey `settings.yaml` (autonomy level, overrides, caps). One application per employer per cycle. Class-year filter. Stop rule on CAPTCHAs, verification steps and platform warnings: park, notify, continue with other work.
- LinkedIn: read-only unless settings allow; human pace; one tab; no scraping; no anti-detection tooling.
- Employer AI policy: research-and-proofread mode where AI-written content is prohibited; log the mode.
- Git: author is the candidate only, no AI attribution lines, no destructive commands, no force-push.
- Never ask the candidate the same question twice: before asking, check `brain/vault/profile/standard-answers.yaml`, the claims ledger and `brain/vault/voice/style-guide.md`. Record every new answer in the same session (facts through `brain/review-decisions/` into the ledger, policies and preferences in `standard-answers.yaml` or the style guide).

## Conventions
- Whenever a driver needs something from the candidate (a decision, a fact, an approval, an action only he can take), phrase it as a direct question, one per item, never as a statement in a waiting list (the candidate, 2026-09-25: "phrase it as a question so it's clear you need my input").
- UK English, ISO dates, GBP.
- Project folder is the centre; anything stored elsewhere is listed in `docs/EXTERNAL-LOCATIONS.md`.
- Keep the repository lean (no binaries, models, databases, browser profiles, node_modules, venvs).
- Cite URLs and retrieval dates for any employer, deadline or platform fact; mark UNVERIFIED when unverified.
- Two drivers may run at once: pull --rebase first; pick tasks by the `Models` line; claim a task by setting `Status: in-progress (<driver and model>, <date>)` (for example `Claude Opus`, `Claude Fable`, `Codex Sol`) in `TASKS.md` and pushing that commit before any other edit; never take a task in progress for the other driver; commit only your own files, never `git add -A`; verify the database with a claims count before believing it is absent.
- Before stopping: update `TASKS.md` status, rewrite `.claude-mem/STATE.md`, append `DECISIONS.md` / `GOTCHAS.md` as needed, commit, push.

## Layout you will build
- `brain/` (vault of Markdown, sources, claims export, graph output) · `data/` (registry.yaml, shortlists, review files) · `pipeline/` (Python: ingest, db, sources, scan, dedupe, score, inbox, notify, vault, fill, style) · `.claude/` (Claude skills, agents, hooks) · `codex/` (Codex prompt files and wrappers) · `app/` (Discord bot, later web UI) · live data outside the repo under `%USERPROFILE%\CareerOS-data\`.

## Tooling on this machine (verified 2026-09-21)
Python 3.13 (`C:\Users\<user>\AppData\Local\Programs\Python\Python313\python.exe`) with playwright 1.60, python-docx, PyMuPDF, openpyxl, anthropic, requests, RapidFuzz, keyring and pyrage 1.4; Node 24; git 2.54; `gh` logged in as <github-user>; Claude Code 2.1.263; Codex CLI 0.156.1 (ChatGPT login; GPT-6 Sol is the default from 2026-09-23, with GPT-6 Luna and Terra for lighter work and Astra only as a last resort); graphify 0.9.22 (skill 0.9.19, run `graphify install`); Ollama with qwen3:8b, gemma3:12b, mistral:7b, deepseek-r1:8b, llama3.2:3b; about 19 GB free on C:.
