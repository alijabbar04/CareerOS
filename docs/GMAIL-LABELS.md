# Gmail labels for career mail (T-042)

Parent label `CareerOS`, sub-labels applied one per thread:

| Label | What goes there |
|---|---|
| CareerOS/Applications | confirmations, acknowledgements, portal accounts, form receipts |
| CareerOS/Assessments | online tests, video interviews, assessment centres and their deadlines |
| CareerOS/Interviews | interview invitations, scheduling, prep material from the firm |
| CareerOS/Outcomes | rejections, offers, feedback |
| CareerOS/Job alerts | LinkedIn, Indeed, Reed, Adzuna, Gradcracker, Bright Network, ICAEW, ACCA and similar alerts and newsletters |
| CareerOS/Recruiters | agencies and headhunters |

Rules: the system labels and writes notes; it never archives, deletes, marks spam or replies. Personal mail is never labelled. The first pass on 2026-09-23 used the claude.ai Gmail connector; from then on the T-015 poller applies labels as it classifies (needs the `gmail.modify` scope at consent) and writes a one-line note per email into the tracker's `notes` table and the daily digest.
