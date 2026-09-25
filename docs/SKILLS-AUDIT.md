# External skills audit (T-039)

Decided by Claude Fable on 2026-09-23 from a Sonnet research pass (sources retrieved the same day). The question was whether community skills for Claude Code or Codex could raise CareerOS's drafting, verification, research or coaching quality without weakening two rules that are not negotiable: every factual sentence keeps its claim id, and the target voice is the candidate's measured fingerprint, not a generic "human" style.

## Decisions

| Skill or source | Licence | What it does | Decision | Why |
|---|---|---|---|---|
| humanizer (github.com/blader/humanizer) | MIT | Rewrites a draft to remove 25 AI-writing tells in five categories, citing Wikipedia's "Signs of AI writing" | Adapt the checklist; never run the rewrite | Its whole-document rewrite resequences sentences and can soften facts, which breaks the sentence-to-claim mapping the verifier depends on, and it imitates a sample's generic rhythm rather than the candidate's fingerprint. The five categories are a good detection checklist. |
| avoid-ai-writing (github.com/conorbronsdon/avoid-ai-writing) | MIT | 74-pattern catalogue with detect, edit and rewrite modes | Adapt detect-only | Same reasoning; its detection categories overlap humanizer's and are folded into the style critic's checklist. |
| interview-coach-skill (github.com/noamseg/interview-coach-skill) | MIT | Mock interviews scored on five dimensions, fact-grounded | Adapt the rubric | The coach agent already exists and cites claim ids; it gains the five-dimension scoring of mock answers. |
| fact-check v0.2 (gist, anotherpanacea-eng) | none stated | Evidence ledger with cross-claim checks (Citation Padding, Hotspot Cluster, Authority Mask) | Adapt the three checks | Useful adversarial patterns for the red team at CareerOS's per-sentence grain; unlicensed, so concepts only, no text copied. |
| Russell, Karpinska and Iyyer 2025, ACL (aclanthology.org/2025.acl-long.267) | open access | How expert readers detect AI text: lexical clues plus structure and register | Adopt as rationale | Grounds the red team's "sounds like AI" test. Detection only; CareerOS never tries to evade detectors. |
| "AI tells" rubric (gist, lmmx) | none stated | Tests text against the null hypothesis of the claimed author | Adopt the framing | Becomes the wording of the swap-firm test: would the candidate, in his situation, have written this? |
| proficiently-claude-skills | none detected | Auto-fills applications on Greenhouse, Lever and Workday | Skip | Conflicts with the review queue, the autonomy levels and one application per employer per cycle. |
| resume-cover-letter (jezweb/claude-skills) | MIT | Mirrors job-post keywords into a cover letter | Skip | No grounding in claims; reintroduces the keyword-stuffed emphasis the drafter is built to avoid. |
| anthropics/skills | Apache-2.0 | Official skills repository | Nothing to adopt | No job-application or writing-tell skill there on the retrieval date. |

## What changed in CareerOS

- `brain/vault/voice/phrases.yaml`: "as an ai" added to the banned list; "in order to" added to the discouraged list (the candidate's corpus uses it twice in 29,000 words, so it is not his habit).
- `.claude/agents/style-critic.md` step 4: a checklist of the non-lexical tells that string matching cannot catch: staged "not X, but Y" contrasts, dramatic one-line closers, generic aphorisms, rebuttals of an unstated objection, vague or unearned connections, shallow "-ing" riders, stacked qualifiers, borrowed-authority name-dropping, and chat leftovers.
- `.claude/agents/red-team.md`: the swap-firm test is reworded as a null hypothesis (would the candidate, in his actual situation, have written this?); three cross-claim checks added: citation padding (many ids on a sentence that says little), hotspot cluster (one story or claim carrying most of the answer), authority mask (a firm accolade doing the work a personal reason should do).
- `.claude/agents/coach.md` section 3: mock answers are scored on Substance, Structure, Relevance, Credibility and Differentiation, one to five each, with one line of justification.
- `tests/test_style_phrases.py`: the style check flags the new patterns.

## What was left alone, on purpose

The drafter agent, the brief format, the verifier and the deterministic checks. The voice guide stays the authority on how the candidate writes; nothing generic replaces it. No external skill is installed or invoked at draft time.
