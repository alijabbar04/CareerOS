# drivers/

One file per model that can drive CareerOS. `/careeros` (Claude) and `/prompts:careeros` or `codex\careeros.ps1` (Codex) read the file for the model they are running as, then run `python -m pipeline.queue --driver <id>` to learn what that model may take now. The queue is computed from `TASKS.md`: a task is offered when its dependencies are done, its `Prefer` names this model or its family or says either, nobody else has claimed it, and `Skip if driver is` does not name it. When nothing is unblocked, the command names the model that should run first, and the driver tells the candidate instead of guessing.

| Driver id | Session | Takes |
|---|---|---|
| `claude-fable` | Claude Code on Fable 5.1 | employer-facing text (drafting, verification, red team, coaching), security reviews, anything the candidate or an employer reads |
| `claude-opus` | Claude Code on Opus 5.5 | building and reviewing code, hooks, browser recipes, pipeline runs, the candidate's review sessions |
| `codex-sol` | Codex CLI on GPT-6 Sol | Codex-side tooling, second-model critic and grounding check, the modules it built (poller, vault, morning run) |

`python -m pipeline.queue --all` prints the matrix for the candidate.
