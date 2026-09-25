---
name: verify
description: Run the full verifier chain on one draft file (deterministic checks, then the independent verifier agent) and print the verdict. Usage /verify <path to draft.md>.
arguments: [draft]
allowed-tools: Bash(python -m pipeline.*), Read, Agent
---

Verify the draft at **$draft**.

1. Run `python -m pipeline.verify $draft` from the project root and show the "Result" line plus any hard failures and warnings (not the whole report).
2. Launch the `verifier` subagent on Fable 5.1 (pass model fable) with only the draft path. Wait for its verdict line and diff list.
3. If the candidate asked for the full picture, also launch `style-critic` and `red-team` in parallel on the same path.
4. Print: the deterministic result, the verifier verdict, and the combined diff for the drafter. If a claim id is waiting for approval, show the exact command: `python -m pipeline.drafts approve-claims <application id> <claim ids>`.

Do not edit the draft here; that is the drafter's job through `/draft`.
