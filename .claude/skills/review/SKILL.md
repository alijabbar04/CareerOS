---
name: review
description: Open the candidate's review queue: applications whose drafts passed verification, with their reports, so the candidate can approve, edit or reject each one. Usage /review [application id].
arguments: [application]
allowed-tools: Bash(python -m pipeline.*), Read, Edit, Glob
---

Show the candidate what is waiting for him.

1. Run `python -m pipeline.drafts queue`. If `$application` is given, focus on that one; otherwise list everything and ask which to open.
2. For the chosen application, read `brief.md` front matter (company, role, deadline, mode), then for each draft in `drafts/` show the prose in full (it is his to read), followed by one line per report: verify (hard failures and warnings count), verifier verdict, style score, red-team verdict, and any `FACT REQUEST` lines or claim ids waiting for approval.
3. Ask the candidate for a decision per draft: approve as is, edit, or reject.
   - Approve: copy the draft into `final/` unchanged and say that `/approve` (when built) will unlock filling; do not submit anything. Then run `python -m pipeline.publish --application <id>` so the approved text reaches the candidate's OneDrive CareerOS folder as a Word file for his phone.
   - Edit: apply his edits to the draft file exactly as given, rerun `python -m pipeline.verify <draft>`, and classify each edit: a fact correction becomes a `brain/review-decisions/<date>-<slug>.yaml` entry for him to confirm; a deleted phrase is appended to `personal_banned` in `brain/vault/voice/phrases.yaml`; a tone or structure change is noted in `brain/vault/voice/style-guide.md` under a dated "the candidate's edits" heading. Record the edit distance (words changed / words) in `reports/edits-<stem>.md`.
   - Reject: note the reason in `reports/rejected-<stem>.md` and leave the application in `ready-for-review` for a redraft through `/draft`.
4. If he approves a claim for use (a use-with-approval id), run `python -m pipeline.drafts approve-claims <id> <claims>`.
5. Run `python -m pipeline.publish --application <id>` once more if any draft changed, then end with what changed and what is still waiting.

Never submit, fill or send anything from here.
