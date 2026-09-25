# Driver: Claude Code on Fable 5.1 (`claude-fable`)

## What this model is for
Everything an employer or the candidate will read, and every judgement about facts: drafting (`/draft`), verification and red team, coaching packs, claims review sessions, narrative writing, security reviews, and the decisions in `DECISIONS.md`. T-030 (2026-09-23) measured Fable ahead of Opus 5.5 on every drafting check and cheaper in tokens, so drafter, verifier and red-team subagents run on Fable even when another model drives.

## What it does not take
- Building or refactoring pipeline code when `claude-opus` is available (Opus is the default builder).
- Anything marked `Skip if driver is: Claude`, anything `in-progress (Codex ...)` or `in-progress (Claude Opus ...)`.
- Bulk mechanical work: delegate to Sonnet or Haiku subagents.

## Standing priorities (checked by `python -m pipeline.queue --driver claude-fable`)
1. Applications with deadlines: T-036 (Firm B, 1 October, proofread only), T-032 (Firm A, 31 October).
2. T-024 coaching packs once an assessment or interview invite exists.
3. New drafts under T-033 once the candidate approves shortlist rows (Opus creates the applications; Fable drafts).
4. Reviews Codex or Opus request in `STATE.md` (security-sensitive code, rubric changes).

## How to work here
- Read the brief, the vault files it names, and nothing else about the candidate; every factual sentence maps to a claim id.
- Effort xhigh for drafting and verification; subagents for the checks so the checker never sees the writer's reasoning.
- Ask the candidate for a fact once, in a FACT REQUEST line, never by guessing.
