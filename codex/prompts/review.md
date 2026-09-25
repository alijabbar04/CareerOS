# CareerOS Codex read-only review

The input target is an application ID or `queue`. Run `python -m pipeline.drafts queue`. If `queue`, list what the candidate can review and stop. For an ID, read that application's brief and latest drafts and their reports, then present the prose in full with one concise report line per draft: deterministic verify failures/warnings, independent verifier, style critic, red team and Codex grounding result if present. Identify FACT REQUESTs and claims awaiting the candidate's approval.

This non-interactive wrapper is read-only. Do not infer the candidate's approval, copy anything to `final/`, edit claims, record decisions, fill forms, submit, send email or change application status. End by asking the candidate to review in an interactive session and choose approve, edit or reject per draft. Do not ask a question already answered in `brain/vault/profile/standard-answers.yaml` or the claims ledger.
