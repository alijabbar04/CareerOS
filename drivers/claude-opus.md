# Driver: Claude Code on Opus 5.5 (`claude-opus`)

## What this model is for
Building, reviewing and running the pipeline: browser recipes and form filling (T-021), autonomy hooks (T-022), shortlist-to-applications runs (T-033), the question bank (T-019), weekly metrics (T-028), sessions where the candidate reviews drafts (`/review`), code review of Codex's modules, and fixes to anything under `pipeline/`. It is the default driver because it is near-top quality and far cheaper than Fable for this work.

## What it does not take
- Writing employer-facing text itself. When a task needs a draft, launch the `drafter`, `verifier` and `red-team` subagents on model Fable (the `/draft` skill does this) rather than drafting in-session.
- Anything marked `Skip if driver is: Claude`, anything `in-progress (Codex ...)` or `in-progress (Claude Fable ...)`.
- Security sign-offs on code it wrote itself (the other model reviews).

## Standing priorities (checked by `python -m pipeline.queue --driver claude-opus`)
1. T-021 browser profile and ATS recipes, then T-022 autonomy enforcement (with Codex doing the wrapper half).
2. T-033 shortlist to applications as soon as the candidate approves rows; T-019 question bank from the approved answers in `final/`.
3. `/review` sessions with the candidate; record his edits in the style guide and ledger as the skill says.
4. Anything Codex hands over for review in `STATE.md`.

## How to work here
- Effort high; Sonnet subagents for bulk mechanical steps.
- Every browser action obeys `settings.yaml`; nothing is submitted, sent or ticked without the level and override allowing it; LinkedIn stays read-only.
- Keep the parent context small: pass paths to subagents, not file contents.
