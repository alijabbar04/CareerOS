# CareerOS

A local, AI-driven job-search system for one person: a verified "brain" of facts about the candidate, discovery of graduate and entry-level roles from employer career sites and job boards, drafting in the candidate's own voice with every factual sentence traced to a confirmed claim, independent verification and red-teaming, a tracker, an inbox watcher, notifications, and a companion app. It runs on a laptop under Claude Code and Codex CLI, which act as two drivers working from the same task list.

This public repository holds the **code, agents, skills and design documents**. The candidate's data (the claims ledger, source documents, narratives, application drafts, company notes, tracker database and settings) lives in a private repository and on the laptop, and is never published. Names, e-mail addresses, machine paths and employer names in this copy are placeholders produced by the export script (`scripts/publish_public.py`).

## What is here
- `pipeline/`: Python 3.13 modules. Ingest sources, extract and review claims, measure the candidate's writing fingerprint (`style.py`), fetch postings from fourteen source types with a polite HTTP client, dedupe and score them, build drafting briefs (`drafts.py`), verify drafts sentence by sentence against the ledger (`verify.py`), publish approved texts to a Documents mirror and OneDrive (`publish.py`), compute which AI driver should work on what (`queue.py`), notify (Windows toast, Discord), poll Gmail, keep credentials in an encrypted vault, fill forms deterministically, and back up the SQLite tracker.
- `.claude/agents/`: subagent definitions (researcher, drafter, verifier, style critic, red team, coach) with restricted tools and models; `.claude/skills/`: the `/careeros`, `/research`, `/draft`, `/verify` and `/review` commands.
- `codex/`: the Codex driver's prompt and wrappers; `drivers/`: one file per model describing what it takes and leaves.
- `docs/`: the companion-app design, the skills audit, the Gmail label taxonomy, the cover-letter standard's rationale and other decisions.
- `tests/`: unit tests using temporary databases and synthetic fixtures.

## Design rules that shape the code
- No invented, rounded-up or inflated facts: every factual sentence in application text cites a confirmed claim id, and a deterministic verifier checks numbers, names, phrases and limits before any model judges quality.
- Employer AI policies are respected: where a firm allows research and proofreading only, the system produces an outline for the candidate to write from and logs the mode.
- Nothing is submitted, sent or ticked beyond the autonomy level in settings; CAPTCHAs and verification steps stop the run.
- Two drivers, several models: tasks carry one Claude model and one Codex model with a preference; a driver claims a task before working and stops honestly when nothing is offered.

## Running it
Python 3.13 with the packages in `requirements.txt`; copy `settings.example.yaml` to `settings.yaml` and `.env.example` to `.env`; `python -m pipeline.db migrate` creates the tracker under `%USERPROFILE%\CareerOS-data`. Claude Code reads `CLAUDE.md` (which imports `AGENTS.md`); Codex reads `AGENTS.md` directly.

## Licence
MIT. Contributions are welcome as issues and pull requests against the code; the design documents describe one person's system and are shared as an example, not a template that fits everyone.
