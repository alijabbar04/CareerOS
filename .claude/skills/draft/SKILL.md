---
name: draft
description: Run the draft, verify, critique loop for one application (max three rounds) and place the result in the candidate's review queue. Usage /draft posting:<posting id> or /draft <application id>.
arguments: [target]
allowed-tools: Bash(python -m pipeline.*), Read, Glob, Grep, Agent
---

Draft the application for **$target** and get it ready for the candidate's review. Nothing here submits, fills a form, sends an email or edits LinkedIn.

1. Create or refresh the application and its brief:
   - `posting:<id>` -> `python -m pipeline.drafts init --posting <id>`
   - a plain number -> `python -m pipeline.drafts init --application <id>`
   If the script refuses (one application per employer per cycle, exit code 3), tell the candidate which existing application blocks it and stop; only the candidate can say `--force`.
2. Read the brief's front matter. If `company_note` is null, run the `/research` steps for that company first (launch the `researcher` subagent), then rebuild the brief with `init --application <id>`.
3. Decide what to draft: a cover letter whenever the application has a cover-letter upload or field (check `form-structure.json` in the application folder or the company note's application requirements; if unknown, draft one), plus one answer per entry in `questions.yaml` if it exists, otherwise a 250-word "why this firm and this role" answer. The letter must add what the form does not already say and never repeat the answers (the candidate, 2026-09-23; `brain/vault/profile/standard-answers.yaml` cover_letter). Answer every other form question from `standard-answers.yaml` before asking the candidate anything. If `mode` is `outline_only`, the drafter produces outlines only; if `proofread_only`, only where the candidate's own wording is supplied.
4. Round 1: launch the `drafter` subagent on model Fable 5.1 (pass model fable whatever model is driving; T-030 measured it as both better and cheaper than Opus 5.5 for employer-facing text) with the brief path, the kinds to produce and `round: 1`. It writes `drafts/<kind>-r1.md` and runs the deterministic checks itself.
5. For each draft, run `python -m pipeline.consistency check <draft path>` (T-019): any `same-claim` or `same-topic` line means the draft contradicts something this employer already has, and goes to the drafter as a diff like a FAIL; `drift` lines tell the drafter which facts the employer holds in an older version. Then launch the `verifier` subagent on Fable 5.1 (fresh context; give it only the draft path). In parallel launch `style-critic` (Sonnet 5) and `red-team` (Fable 5.1) on the same path.
6. If any of the three returns FAIL, collect their "Diff for the drafter" lists and launch the `drafter` again with the previous draft path, the diffs and `round: n+1`. Repeat steps 5 and 6 at most twice more (three rounds in total). A draft that still fails after round 3 stays in the folder marked failed and is reported to the candidate with the remaining problems; it is not registered.
7. For every passing draft: `python -m pipeline.drafts register <application id> <draft path> --kind <kind>`. Then `python -m pipeline.drafts ready <application id>` (it publishes the folder to the candidate's Documents mirror and OneDrive CareerOS folder automatically).
8. Report to the candidate: the folder, each draft's word count and the three verdicts, any `FACT REQUEST` lines, any claim ids waiting for `python -m pipeline.drafts approve-claims`, and the recruiter-rubric score from the red team. Do not paste the drafts; the candidate opens the files or uses `/review`.

Rules: the drafter, verifier, critic and red team must be separate subagents (the checker never sees the writer's reasoning). Keep parent context small: pass paths, not file contents. Log nothing about the candidate outside the application folder and the tracker.
