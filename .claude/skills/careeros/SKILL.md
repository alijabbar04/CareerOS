---
name: careeros
description: Resume CareerOS work as the Claude driver without a hand-written prompt: pull, read the handover and state, pick or take a task, claim it, do it, hand off. Usage /careeros, /careeros T-021, /careeros status, /careeros review.
arguments: [focus]
allowed-tools: Bash(git *), Bash(python -m pipeline.*), Read, Edit, Write, Glob, Grep, Agent
---

Resume CareerOS as the **Claude** driver. Codex, and possibly another Claude session on a different model, may be running at the same time, so follow the claim protocol exactly. Focus argument: **$focus** (empty means "pick the next task").

1. Identify your model from your own system prompt (Fable 5.1, Opus 5.5, Sonnet 5) and set your driver id: `claude-fable`, `claude-opus` or `claude-sonnet`. If you cannot tell, ask the candidate before doing anything else. Read `drivers/<driver id>.md`: it says what this model takes and what it leaves to the others.
2. Sync: `git pull --rebase origin main`. If it refuses because of unstaged changes, they belong to another driver: do not stash, reset or touch them; note which files and work around them.
3. Read, in this order: `HANDOVER.md`, `.claude-mem/STATE.md`, the other drivers' `.claude-mem/status/*.md`, `.claude-mem/handoffs.md`, the last three entries of `.claude-mem/GOTCHAS.md` and `.claude-mem/DECISIONS.md`. Then run `python -m pipeline.queue --driver <driver id>`.
4. Act on the queue output:
   - `$focus` = `status`: report `python -m pipeline.status`, the review queue (`python -m pipeline.drafts queue`), the queue output for all drivers (`python -m pipeline.queue --all`) and what is waiting on the candidate, then stop.
   - `$focus` = `review`: run the `/review` skill and stop when the candidate is done.
   - `$focus` = a task id: take that task only if the queue lists it under GO for your driver; if it is blocked or belongs to another driver, say which session must run first and stop.
   - otherwise take the first GO task whose work does not itself wait on the candidate. If the output says WAIT, or every GO item is waiting on the candidate, be honest: run `python -m pipeline.queue --board`, give the candidate the strategic picture in a few lines (what is blocked on whom, what only he can unblock, which session should run next), and stop. Idle is the correct outcome. Never invent work, refactor, tidy or "improve" anything the queue did not offer.
   Claim before working: set the task's Status to `in-progress (Claude <Model>, <today>)` in `TASKS.md`, commit only that file with the message `claim T-0xx (Claude <Model>)`, and push. If the push is rejected, pull --rebase and re-run the queue; if the task is now claimed by someone else, take the next GO task.
5. Do the work with the effort on the task card and the rules in your `drivers/` file. Use Sonnet or Haiku subagents for bulk mechanical work; keep the parent context small (pass paths, not file contents). Obey the hard rules in `AGENTS.md`: no invented facts about the candidate, nothing submitted, sent or ticked beyond `settings.yaml`, no secrets in prompts or commits, LinkedIn read-only unless approved, Git author is the candidate only with no AI attribution lines.
6. Commit only files you changed, with `git add <paths>` (never `git add -A`); pull --rebase before every push.
7. Before stopping, whether finished, blocked or out of usage: update the task's Status line; rewrite your own `.claude-mem/status/<driver id>.md` (Now, Done this session, Blocked / waiting on the candidate, Handoffs made); append one line to `.claude-mem/handoffs.md` for anything another driver was waiting on (`date time from -> to: what, where`); edit `.claude-mem/STATE.md` only for shared facts (database, decisions, dates) and never rewrite another driver's lines; append to `DECISIONS.md` if you chose between alternatives and to `GOTCHAS.md` if something cost time; commit, push.
8. Report to the candidate in a few sentences: what changed, what he needs to do, and what the next driver should take.

If the database ever looks empty or absent, verify with `python -c "from pipeline import config; print(config.DB_PATH)"` and a claims count from a shell where `CAREEROS_LOCAL_DIR` is unset before believing it.
