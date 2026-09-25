"""T-017: deterministic verifier for application drafts (plan/PLAN.md 4.3 step 3a, appendix B12).

    python -m pipeline.verify <draft.md> [--research brain/vault/companies/<slug>.md] [--approvals <approvals.yaml>]
    python -m pipeline.verify research <company-note.md>

A draft is the file format described in every brief: front matter, prose, then a
`## Citations` section mapping each sentence (S1, S2, ...) to claim ids or one of
the tags greeting / closing / motivation / opinion / research. This script does
the cheap, mechanical half of the verifier chain and writes a JSON + Markdown
report next to the draft (`reports/verify-<stem>.json|.md`):

hard failures (the draft cannot go to the candidate):
  - a cited claim that is missing, retired, never-use, or use-with-approval without the candidate's approval
  - a factual sentence with no mapping, or a mapping that does not match the sentence count
  - a number, percentage, money amount or date that appears in no cited claim (or in the company note for `research` sentences)
  - a banned phrase, an exclamation mark, an em-dash in a letter or email, US spelling
  - more than the word limit (letters without one: hard ceiling 400, warning above 350); a letter or email without the candidate's salutation and sign-off
  - discouraged-phrase density above 3 per 300 words (appendix B12)

warnings (the verifier agent decides): low specificity, low sentence-length variation, the firm never named,
few track claims used, capitalised names that appear in no cited source.

Everything judged here is inspectable; the model-based checks (atomic claims,
inflation judge, coverage judgement, swap-firm test, question-answered) run in
the verifier and red-team agents and read this report first.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from pipeline import config, style
from pipeline.drafts import COMPANIES_DIR, NARRATIVES_DIR, read_front_matter, slugify

CITE_RE = re.compile(r"C-\d{4}")
MAP_RE = re.compile(r"^\s*[-*]\s*S(\d+)\s*:\s*(.+?)\s*$")
NUM_RE = re.compile(r"(?<![A-Za-z\d-])(£?\d[\d,]*(?:\.\d+)?%?)")
TAGS = {"greeting", "closing", "motivation", "opinion", "research"}
FACT_TAGS_NEEDING_SUPPORT = {"research"}
SALUTATION_RE = re.compile(r"^(dear\b|hello\b|hi\b)", re.I)
SIGNOFF_RE = re.compile(r"(yours faithfully|yours sincerely|kind regards|best regards)\s*,?\s*(\n\s*)?candidate name\s*$", re.I)
KIND_TO_STYLE = {"cover-letter": "cover-letter", "answer": "answer", "email": "email", "cv-notes": "answer"}
ENTITY_ALLOWLIST = {
    "the candidate", "Name", "Candidate Name", "Dear", "Yours", "Kind", "Best", "Recruitment", "Team", "Hiring", "January", "February",
    "March", "April", "May", "June", "July", "August", "September", "October", "November", "December", "London", "UK",
    "United Kingdom", "England", "I", "The", "This", "That", "What", "As", "In", "At", "My", "For", "During", "Since", "While",
    "After", "Before", "Having", "Alongside", "Rather", "Beyond", "Both", "Each", "Every", "Through", "Over", "Under",
    "Economics", "Mathematics", "Maths", "GCSE", "A-Level", "A-level", "BSc", "ACA", "ACCA", "ATT", "CTA", "CIMA", "FCA",
    "GDPR", "DCF", "CAPM", "WACC", "Excel", "Python", "PowerShell", "JavaScript", "TypeScript", "R", "C++", "VBA", "AI",
    "QuickBooks", "Queen", "Mary", "University", "Corporate", "Finance", "Applied", "Econometrics", "Portfolio",
    "Management", "International", "Financial", "Strategy", "Industrial",
}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def load_ledger() -> dict[str, dict[str, Any]]:
    path = config.BRAIN_DIR / "claims.jsonl"
    return {r["id"]: r for r in (json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip())}


def split_draft(text: str) -> tuple[dict[str, Any], str, dict[int, list[str]], list[str]]:
    """Return (front matter, prose body, {sentence number: [ids or tags]}, mapping problems)."""
    meta: dict[str, Any] = {}
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) > 2:
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                meta = {}
            text = parts[2]
    body, _, tail = text.partition("\n## Citations")
    mapping: dict[int, list[str]] = {}
    problems: list[str] = []
    if not tail.strip():
        problems.append("no `## Citations` section")
    for line in tail.splitlines():
        m = MAP_RE.match(line)
        if not m:
            continue
        number = int(m.group(1))
        items = [x.strip() for x in re.split(r"[,;]\s*", m.group(2)) if x.strip()]
        bad = [x for x in items if not (CITE_RE.fullmatch(x) or x.casefold() in TAGS)]
        if bad:
            problems.append(f"S{number}: unrecognised mapping item(s) {bad}")
        if number in mapping:
            problems.append(f"S{number}: mapped twice")
        mapping[number] = [x if CITE_RE.fullmatch(x) else x.casefold() for x in items]
    fact_requests = [ln.strip() for ln in body.splitlines() if ln.strip().upper().startswith("FACT REQUEST")]
    body = "\n".join(ln for ln in body.splitlines() if not ln.strip().upper().startswith("FACT REQUEST")).strip("\n")
    return (meta if isinstance(meta, dict) else {}), body.strip(), mapping, problems + [f"fact request: {fr}" for fr in fact_requests]


def sentences_of(body: str) -> list[str]:
    """Sentences in verifier order: salutation and sign-off lines count as sentences too."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", body.strip()):
        para = " ".join(para.split())
        if not para:
            continue
        if SALUTATION_RE.match(para) and len(para.split()) <= 8:
            out.append(para)
            continue
        out.extend(s for s in style.sentences(para) if s.strip())
    return out


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _numbers(text: str) -> set[str]:
    """Every number token in the text, as written (money, percentages, decimals, dates, counts)."""
    return {raw.rstrip(".,") for raw in NUM_RE.findall(text) if raw.rstrip(".,")}


def _normalise_number(token: str) -> str:
    return token.replace(",", "").lstrip("£").rstrip("%").rstrip(".")


def _number_set(text: str) -> set[str]:
    return {_normalise_number(t) for t in _numbers(text)}


def _entities(sentence: str) -> set[str]:
    """Capitalised phrases after the first word (candidate organisation, person, product and module names)."""
    words = sentence.split()
    found: set[str] = set()
    joiners = {"of", "and", "&", "for", "the"}
    i = 1
    while i < len(words):
        w = words[i].strip(",.;:()\"'")
        if not re.match(r"^[A-Z][A-Za-z&'’.-]+$", w):
            i += 1
            continue
        phrase = [w]
        j = i + 1
        while j < len(words) and not words[j - 1].endswith((".", ",", ";", ":")):
            nxt = words[j].strip(",.;:()\"'")
            if re.match(r"^[A-Z][A-Za-z&'’.-]+$", nxt) or nxt.casefold() in joiners:
                phrase.append(nxt)
                j += 1
            else:
                break
        while phrase and phrase[-1].casefold() in joiners:
            phrase.pop()
        text = " ".join(phrase)
        if text and text not in ENTITY_ALLOWLIST and not all(p in ENTITY_ALLOWLIST or p.casefold() in joiners for p in phrase):
            found.add(text)
        i = max(j, i + 1)
    return found


def verify_draft(
    path: Path,
    ledger: dict[str, dict[str, Any]],
    research_text: str = "",
    approved: set[str] | None = None,
    track_claims: list[str] | None = None,
    company: str | None = None,
) -> dict[str, Any]:
    approved = approved or set()
    text = path.read_text(encoding="utf-8")
    meta, body, mapping, problems = split_draft(text)
    kind = str(meta.get("kind", "cover-letter")).split("-")[0] if str(meta.get("kind", "")).startswith("answer") else str(meta.get("kind", "cover-letter"))
    style_kind = KIND_TO_STYLE.get(kind, "answer")
    sents = sentences_of(body)
    hard: list[str] = []
    warnings: list[str] = []
    review: dict[str, Any] = {"unverified_entities": [], "motivation_sentences": [], "opinion_sentences": [], "fact_requests": []}
    for p in problems:
        (review["fact_requests"] if p.startswith("fact request") else hard).append(p)

    # 1. citation validity
    cited_all = sorted({c for items in mapping.values() for c in items if CITE_RE.fullmatch(c)})
    for cid in cited_all:
        row = ledger.get(cid)
        if row is None:
            hard.append(f"{cid} is not in the ledger")
        elif row.get("status") != "confirmed":
            hard.append(f"{cid} is {row.get('status')} (\"{row.get('text', '')[:70]}\")")
        elif row.get("sensitivity") == "never-use":
            hard.append(f"{cid} is never-use")
        elif row.get("sensitivity") == "use-with-approval" and cid not in approved:
            hard.append(f"{cid} needs the candidate's approval before use (python -m pipeline.drafts approve-claims <id> {cid})")

    # 2. sentence mapping and per-sentence facts
    if mapping and sents and max(mapping) != len(sents):
        hard.append(f"mapping covers S1..S{max(mapping)} but the prose has {len(sents)} sentences; renumber (verifier order below)")
    sentence_rows: list[dict[str, Any]] = []
    research_numbers = _number_set(research_text) if research_text else set()
    research_lower = research_text.casefold()
    for n, sentence in enumerate(sents, start=1):
        items = mapping.get(n, [])
        ids = [x for x in items if CITE_RE.fullmatch(x)]
        tags = [x for x in items if x in TAGS]
        row: dict[str, Any] = {"n": n, "text": sentence, "claims": ids, "tags": tags, "issues": []}
        is_greeting = bool(SALUTATION_RE.match(sentence)) or bool(re.match(r"^(yours|kind regards|best regards)", sentence, re.I)) or sentence.strip() in {"Candidate Name", "Candidate Name."}
        if not items:
            if is_greeting:
                row["tags"] = ["greeting"]
            else:
                row["issues"].append("no mapping")
                hard.append(f"S{n} has no citation or tag: \"{sentence[:80]}\"")
        supporting = " ".join(ledger[c]["text"] for c in ids if c in ledger)
        if "research" in tags:
            supporting += " " + research_text
        nums = _numbers(sentence)
        if nums and not is_greeting:
            available = _number_set(supporting)
            if "research" in tags:
                available |= research_numbers
            missing = sorted(n_ for n_ in nums if _normalise_number(n_) not in available)
            if missing:
                if ids or "research" in tags:
                    row["issues"].append(f"numbers not in cited sources: {missing}")
                    hard.append(f"S{n}: number(s) {missing} appear in no cited claim{' or the company note' if 'research' in tags else ''}")
                elif set(tags) & {"motivation", "opinion"}:
                    row["issues"].append(f"numbers in a motivation/opinion sentence: {missing}")
                    hard.append(f"S{n}: a motivation/opinion sentence asserts number(s) {missing}; cite a claim or remove them")
        if "motivation" in tags:
            review["motivation_sentences"].append({"n": n, "text": sentence})
        if "opinion" in tags:
            review["opinion_sentences"].append({"n": n, "text": sentence})
        if "research" in tags and research_text:
            row["research_backed"] = True
        # capitalised names that no cited source mentions
        if not is_greeting:
            haystack = (supporting + " " + (company or "")).casefold()
            for ent in sorted(_entities(sentence)):
                if ent.casefold() not in haystack and ent.casefold() not in research_lower:
                    review["unverified_entities"].append({"n": n, "entity": ent})
        sentence_rows.append(row)

    # 3. style and format
    measures = style.measure(body)
    lists = yaml.safe_load(style.PHRASES_PATH.read_text(encoding="utf-8")) if style.PHRASES_PATH.exists() else {}
    measures.update(style.phrase_hits(body, lists))
    measures.pop("top_openers", None)
    if measures["banned_hits"]:
        hard.append(f"banned phrase(s): {measures['banned_hits']}")
    if measures["discouraged_per_300_words"] > 3:
        hard.append(f"discouraged-phrase density {measures['discouraged_per_300_words']} per 300 words (limit 3): {measures['discouraged_hits']}")
    if measures["exclamations"]:
        hard.append("exclamation mark present; the candidate uses none")
    if measures["question_marks"] and style_kind != "answer":
        hard.append("question mark present; the candidate does not use rhetorical questions")
    if measures["em_dashes"] and style_kind in {"cover-letter", "email"}:
        hard.append("em-dash present in a letter or email; the candidate uses none")
    elif measures["em_dashes"]:
        warnings.append("em-dash present; the candidate rarely uses them")
    if measures["us_spellings"]:
        hard.append(f"{measures['us_spellings']} US spelling(s); UK spelling is required")
    if style_kind in {"cover-letter", "email"} and measures["contractions_per_1k"] > 3.5:
        hard.append(f"contractions {measures['contractions_per_1k']} per 1k words in a letter or email; the candidate's letters average about 3 (an occasional I'm), never more")
    elif style_kind in {"cover-letter", "email"} and measures["contractions_per_1k"] > 0:
        warnings.append(f"contraction(s) present ({measures['contractions_per_1k']} per 1k words); the candidate allows himself an occasional I'm in a letter, never in the evidence paragraphs")
    if style_kind == "answer" and measures["contractions_per_1k"] > 15:
        warnings.append(f"contractions {measures['contractions_per_1k']} per 1k words; the candidate's answers have about 12 (measured 2026-09-23 with curly apostrophes counted)")
    word_limit = meta.get("word_limit")
    if word_limit:
        if measures["words"] > int(word_limit):
            hard.append(f"{measures['words']} words exceeds the limit of {word_limit}")
        elif measures["words"] > int(word_limit) * 0.95:
            warnings.append(f"{measures['words']} words is within 5% of the {word_limit} limit")
    if style_kind == "cover-letter" and not word_limit:
        if measures["words"] > 400:
            hard.append(f"{measures['words']} words; the cover-letter standard's hard ceiling is 400 (revised 2026-09-24)")
        elif measures["words"] > 380:
            warnings.append(f"{measures['words']} words; the cover-letter standard is 300 to 400, so this is close to the ceiling")
        elif measures["words"] < 300:
            warnings.append(f"{measures['words']} words; the cover-letter standard is 300 to 400 (the candidate, 2026-09-24: fewer points, developed further)")
    for other_path, run in shared_runs(body, path, n=8):
        warnings.append(f"eight-word run shared with {other_path}: \"{run}\" (template sentences are an AI tell; rephrase)")
    if style_kind in {"cover-letter", "email"}:
        if not SALUTATION_RE.match(body.strip()):
            hard.append("missing salutation (\"Dear ...,\")")
        if not SIGNOFF_RE.search(body.strip()):
            hard.append("missing sign-off (\"Yours faithfully, Candidate Name\" or the email equivalent)")
    if measures["sentences"] >= 4 and measures["sentence_len_sd_ratio"] < 0.3:
        warnings.append(f"sentence-length variation is low (sd/mean {measures['sentence_len_sd_ratio']}; the candidate's is about 0.45)")
    if re.search(r"^\s*[-*]\s", body, re.M) and style_kind != "answer":
        hard.append("bullet list inside prose")
    if re.search(r"^#", body, re.M):
        hard.append("heading inside the prose")

    # 4. specificity, firm naming, coverage
    distinct_claims = sorted(set(cited_all))
    per_100 = round(len(distinct_claims) / max(1, measures["words"]) * 100, 2)
    measures["claims_per_100_words"] = per_100
    if measures["words"] >= 80 and per_100 < 2:
        warnings.append(f"specificity {per_100} distinct claims per 100 words (target at least 2)")
    if company and style_kind == "cover-letter" and company.casefold() not in body.casefold():
        warnings.append(f"the firm ({company}) is never named; the swap-firm test would pass, which is bad")
    research_sentences = sum(1 for r in sentence_rows if "research" in r["tags"])
    measures["research_sentences"] = research_sentences
    if style_kind == "cover-letter" and research_sentences == 0:
        warnings.append("no sentence relies on the company note; the letter has nothing firm-specific")
    if track_claims:
        used = sorted(set(track_claims) & set(distinct_claims))
        measures["track_claims_used"] = f"{len(used)}/{len(track_claims)}"
        if len(track_claims) >= 5 and len(used) < max(2, len(track_claims) // 5):
            warnings.append(f"only {len(used)} of the track narrative's {len(track_claims)} lead claims are used")

    return {
        "draft": str(path),
        "kind": kind,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "hard_fails": hard,
        "warnings": warnings,
        "review": review,
        "metrics": measures,
        "sentences": sentence_rows,
        "cited_claims": distinct_claims,
    }


def shared_runs(body: str, this_path: Path, n: int = 8, roots: list[Path] | None = None) -> list[tuple[str, str]]:
    """Eight-word runs this draft shares with any other final or latest draft in the vault (uniformity is an AI tell)."""
    from pipeline.drafts import APPLICATIONS_DIR, latest_drafts, read_front_matter

    roots = roots if roots is not None else ([APPLICATIONS_DIR] if APPLICATIONS_DIR.exists() else [])
    words = re.findall(r"[A-Za-z0-9'’-]+", body.casefold())
    mine = {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}
    if not mine:
        return []
    this_kind = this_path.stem.split("-r")[0]
    this_app = this_path.parent.parent if this_path.parent.name in {"drafts", "final"} else this_path.parent
    hits: list[tuple[str, str]] = []
    for root in roots:
        for app in sorted(p for p in root.iterdir() if p.is_dir()):
            candidates = list((app / "final").glob("*.md")) if (app / "final").exists() else []
            candidates += latest_drafts(app)
            for other in candidates:
                if other.resolve() == this_path.resolve():
                    continue
                if app.resolve() == this_app.resolve() and other.stem.split("-r")[0] == this_kind:
                    continue  # earlier rounds of the same answer are expected to overlap
                _, other_body = read_front_matter(other)
                other_body = other_body.split("\n## Citations", 1)[0]
                ow = re.findall(r"[A-Za-z0-9'’-]+", other_body.casefold())
                theirs = {" ".join(ow[i:i + n]) for i in range(max(0, len(ow) - n + 1))}
                common = mine & theirs
                if common:
                    hits.append((str(other.relative_to(root)) if other.is_relative_to(root) else str(other), sorted(common)[0]))
                    break
    return hits


def report_markdown(result: dict[str, Any]) -> str:
    lines = [f"# Verify report: {Path(result['draft']).name}", "", f"Checked: {result['checked_at']}", ""]
    lines += [f"**Result: {'FAIL' if result['hard_fails'] else 'PASS'}** ({len(result['hard_fails'])} hard, {len(result['warnings'])} warnings)", ""]
    if result["hard_fails"]:
        lines += ["## Hard failures", ""] + [f"- {h}" for h in result["hard_fails"]] + [""]
    if result["warnings"]:
        lines += ["## Warnings", ""] + [f"- {w}" for w in result["warnings"]] + [""]
    rv = result["review"]
    if rv["unverified_entities"]:
        lines += ["## Names to verify (appear in no cited claim or the company note)", ""] + [f"- S{e['n']}: {e['entity']}" for e in rv["unverified_entities"]] + [""]
    if rv["motivation_sentences"] or rv["opinion_sentences"]:
        lines += ["## Motivation and opinion sentences (inflation judge: does any imply a fact, scale or skill the ledger does not support?)", ""]
        lines += [f"- S{s['n']}: {s['text']}" for s in rv["motivation_sentences"] + rv["opinion_sentences"]] + [""]
    if rv["fact_requests"]:
        lines += ["## Fact requests from the drafter", ""] + [f"- {f}" for f in rv["fact_requests"]] + [""]
    m = result["metrics"]
    lines += ["## Metrics", "", f"- words {m['words']}, sentences {m['sentences']}, mean length {m['sentence_len_mean']}, sd/mean {m['sentence_len_sd_ratio']}",
              f"- distinct claims {len(result['cited_claims'])} ({m.get('claims_per_100_words')} per 100 words); research-backed sentences {m.get('research_sentences', 0)}; track claims used {m.get('track_claims_used', 'n/a')}",
              f"- contractions/1k {m['contractions_per_1k']}, discouraged/300 {m['discouraged_per_300_words']}, US spellings {m['us_spellings']}", ""]
    lines += ["## Sentences (verifier order)", ""]
    for row in result["sentences"]:
        mark = ", ".join(row["claims"] + row["tags"]) or "UNMAPPED"
        issue = f"  <- {'; '.join(row['issues'])}" if row["issues"] else ""
        lines.append(f"- S{row['n']} [{mark}] {row['text']}{issue}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Company-note validation
# ---------------------------------------------------------------------------

URL_RE = re.compile(r"https?://\S+")
DATE_RE = re.compile(r"(retrieved|accessed)\s+\d{4}-\d{2}-\d{2}", re.I)


def verify_research(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    _, body = read_front_matter(path)
    problems: list[str] = []
    facts = 0
    for i, line in enumerate(body.splitlines(), start=1):
        if not re.match(r"^\s*[-*]\s+", line):
            continue
        facts += 1
        if not URL_RE.search(line):
            problems.append(f"line {i}: fact without a URL: {line.strip()[:90]}")
        if not DATE_RE.search(line):
            problems.append(f"line {i}: fact without 'retrieved YYYY-MM-DD': {line.strip()[:90]}")
    if facts == 0:
        problems.append("no fact bullets found (each fact is a bullet with its URL and retrieval date)")
    if re.search(r"\bunverified\b|\buncertain\b|\bpossibly\b|\bprobably\b", text, re.I):
        pass  # allowed: uncertainty must be marked, not hidden
    return {"note": str(path), "facts": facts, "problems": problems}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _track_claims_for(draft: Path) -> tuple[list[str], str | None]:
    brief = draft.parent.parent / "brief.md"
    if not brief.exists():
        return [], None
    meta, _ = read_front_matter(brief)
    track = meta.get("track")
    company = meta.get("company")
    if not track:
        return [], company
    track_path = NARRATIVES_DIR / f"track-{track}.md"
    if not track_path.exists():
        return [], company
    tmeta, _ = read_front_matter(track_path)
    return [str(c) for c in (tmeta.get("claims") or [])], company


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "research":
        parser = argparse.ArgumentParser(prog="python -m pipeline.verify research")
        parser.add_argument("cmd")
        parser.add_argument("note", type=Path)
        args = parser.parse_args(argv)
        result = verify_research(args.note)
        print(json.dumps(result, indent=2))
        return 1 if result["problems"] else 0

    parser = argparse.ArgumentParser(prog="python -m pipeline.verify", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("draft", type=Path)
    parser.add_argument("--research", type=Path, help="company note; defaults to brain/vault/companies/<company>.md from the brief")
    parser.add_argument("--approvals", type=Path, help="approvals.yaml; defaults to the application folder's file")
    parser.add_argument("--no-report", action="store_true", help="print only; do not write reports/")
    args = parser.parse_args(argv)

    draft = args.draft
    if not draft.exists():
        print(f"draft not found: {draft}")
        return 2
    folder = draft.parent.parent if draft.parent.name == "drafts" else draft.parent
    track_claims, company = _track_claims_for(draft)
    research_path = args.research
    if research_path is None and company:
        candidate = COMPANIES_DIR / f"{slugify(company)}.md"
        research_path = candidate if candidate.exists() else None
    research_text = research_path.read_text(encoding="utf-8") if research_path and research_path.exists() else ""
    approvals_path = args.approvals or (folder / "approvals.yaml")
    approved: set[str] = set()
    if approvals_path.exists():
        data = yaml.safe_load(approvals_path.read_text(encoding="utf-8")) or {}
        approved = {str(c) for c in (data.get("claims") or [])}

    result = verify_draft(draft, load_ledger(), research_text=research_text, approved=approved, track_claims=track_claims, company=company)
    md = report_markdown(result)
    print(md)
    if not args.no_report:
        reports = folder / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / f"verify-{draft.stem}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        (reports / f"verify-{draft.stem}.md").write_text(md, encoding="utf-8")
        print(f"report: {reports / f'verify-{draft.stem}.md'}")
    return 1 if result["hard_fails"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
