"""T-019: never tell an employer two versions of the same fact.

    python -m pipeline.consistency check <draft.md> [--application ID]
        compares a new document with everything already sent to that employer
    python -m pipeline.consistency sent <application id>
        lists facts cited in a sent application that the ledger has changed since it was sent

"Sent" means an application whose status is submitted or later (its `final/` files, with their claim citations) and
the candidate's earlier applications to the same employer kept in `brain/sources` (front matter `employer`, no citations).
Three checks, all deterministic:
  1. same claim, different figures: a sentence in the new document and one already sent cite the same claim but state
     different numbers ("roughly halved" against "about 40%");
  2. the same figure topic with a different number in an uncited earlier document ("325 policy sales" against "350");
  3. ledger drift: a claim cited in a sent application whose text changed after the send date (read from git history of
     brain/claims.jsonl), so the employer holds the old version.
Exit code 0 when nothing is flagged, 1 when something is.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from pipeline import config, db, drafts, verify

SENT_STATUSES = ("submitted", "acknowledged", "assessment", "interview", "offer", "accepted", "rejected", "ghosted")
SOURCE_KINDS = ("application-answers", "cover-letter", "speculative-email")
NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
    "eighteen nineteen twenty".split())}
NUMBER_WORDS.update({"thirty": "30", "forty": "40", "fifty": "50", "hundred": "100"})
FRACTIONS = {"half": "1/2", "halved": "1/2", "halving": "1/2", "halve": "1/2", "third": "1/3", "quarter": "1/4",
             "double": "x2", "doubled": "x2", "tripled": "x3"}
STOP = set("the a an and or of to in on at for with by from as that this was were is are be been it its my his her their "
           "i we you he she they over into than more most less about roughly around nearly almost up down per each "
           "which who whom also after before during while so".split())


@dataclass
class Sentence:
    text: str
    claims: set[str] = field(default_factory=set)


@dataclass
class SentDoc:
    label: str
    when: str
    sentences: list[Sentence]
    application_id: int | None = None


@dataclass
class Flag:
    kind: str       # same-claim | same-topic | drift
    message: str


def figures(text: str) -> set[str]:
    """Numbers as written plus number words and fraction words ("two", "halved"), normalised."""
    found = set(verify._number_set(text))
    for word in re.findall(r"[A-Za-z]+", text.casefold()):
        if word in NUMBER_WORDS:
            found.add(NUMBER_WORDS[word])
        elif word in FRACTIONS:
            found.add(FRACTIONS[word])
    return found


def _topics(text: str) -> dict[str, set[str]]:
    """For each figure, the content words within four tokens of it (its topic)."""
    tokens = re.findall(r"£?\d[\d,]*(?:\.\d+)?%?|[A-Za-z][A-Za-z'-]*", text)
    out: dict[str, set[str]] = {}
    for i, tok in enumerate(tokens):
        low = tok.casefold()
        fig = (verify._normalise_number(tok) if tok[0].isdigit() or tok[0] == "£"
               else NUMBER_WORDS.get(low) or FRACTIONS.get(low))
        if not fig:
            continue
        window = tokens[max(0, i - 4): i] + tokens[i + 1: i + 5]
        words = {w.casefold() for w in window if w[0].isalpha() and len(w) > 3 and w.casefold() not in STOP
                 and w.casefold() not in NUMBER_WORDS and w.casefold() not in FRACTIONS}
        out.setdefault(fig, set()).update(words)
    return out


def parse_document(path: Path) -> list[Sentence]:
    _meta, body, mapping, _problems = verify.split_draft(path.read_text(encoding="utf-8"))
    return [Sentence(s, {x for x in mapping.get(i, []) if x.startswith("C-")})
            for i, s in enumerate(verify.sentences_of(body), start=1)]


def _front_matter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    try:
        return yaml.safe_load(text.split("---", 2)[1]) or {}
    except yaml.YAMLError:
        return {}


def _company(conn: sqlite3.Connection, app_id: int) -> tuple[int, str]:
    row = conn.execute("SELECT c.id, c.name FROM applications a JOIN companies c ON c.id = a.company_id WHERE a.id=?",
                       (app_id,)).fetchone()
    if row is None:
        raise SystemExit(f"no application {app_id}")
    return row["id"], row["name"]


def sent_documents(conn: sqlite3.Connection, company_id: int, company_name: str,
                   exclude_application: int | None = None) -> list[SentDoc]:
    docs: list[SentDoc] = []
    marks = ",".join("?" for _ in SENT_STATUSES)
    for row in conn.execute(f"SELECT id, submitted_at FROM applications WHERE company_id=? AND status IN ({marks})",
                            (company_id, *SENT_STATUSES)):
        if row["id"] == exclude_application:
            continue
        folder = drafts.find_folder(row["id"])
        if folder is None or not (folder / "final").is_dir():
            continue
        for path in sorted((folder / "final").glob("*.md")):
            docs.append(SentDoc(f"application {row['id']} {path.name}", row["submitted_at"] or "", parse_document(path), row["id"]))
    wanted = company_name.casefold()
    for path in sorted(config.SOURCES_DIR.glob("*.md")):
        fm = _front_matter(path)
        employer = str(fm.get("employer") or "").casefold()
        if fm.get("kind") in SOURCE_KINDS and employer and (employer == wanted or wanted.startswith(employer)
                                                            or employer.startswith(wanted)):
            body = path.read_text(encoding="utf-8").split("---", 2)[-1]
            sentences = [Sentence(s) for s in verify.sentences_of(body) if not s.startswith("#")]
            docs.append(SentDoc(f"earlier application ({path.name})", str(fm.get("doc_modified") or ""), sentences))
    return docs


def compare(new: list[Sentence], sent: list[SentDoc]) -> list[Flag]:
    flags: list[Flag] = []
    seen: set[tuple[str, str]] = set()
    for sentence in new:
        new_figs = figures(sentence.text)
        if not new_figs:
            continue
        new_topics = _topics(sentence.text)
        for doc in sent:
            for old in doc.sentences:
                old_figs = figures(old.text)
                if not old_figs or new_figs & old_figs:
                    continue
                key = (sentence.text, old.text)
                if key in seen:
                    continue
                shared = sentence.claims & old.claims
                if shared:
                    seen.add(key)
                    flags.append(Flag("same-claim",
                                      f"{', '.join(sorted(shared))}: {doc.label} said \"{old.text}\" but this says \"{sentence.text}\""))
                    continue
                if old.claims:
                    continue       # both cited and no shared claim: different facts
                old_topics = _topics(old.text)
                for fig, words in new_topics.items():
                    overlap = next(((ofig, words & owords) for ofig, owords in old_topics.items()
                                    if ofig != fig and len(words & owords) >= 2), None)
                    if overlap:
                        seen.add(key)
                        flags.append(Flag("same-topic",
                                          f"{doc.label} gave {overlap[0]} for \"{' '.join(sorted(overlap[1]))}\" (\"{old.text}\"); "
                                          f"this gives {fig} (\"{sentence.text}\")"))
                        break
    return flags


def claims_at(when: str) -> dict[str, dict]:
    """The ledger as committed at `when` (the last commit of brain/claims.jsonl before it)."""
    rev = subprocess.run(["git", "rev-list", "-1", f"--before={when}", "HEAD", "--", "brain/claims.jsonl"],
                         cwd=config.REPO_ROOT, capture_output=True, text=True).stdout.strip()
    if not rev:
        return {}
    text = subprocess.run(["git", "show", f"{rev}:brain/claims.jsonl"], cwd=config.REPO_ROOT, capture_output=True,
                          text=True, encoding="utf-8").stdout
    return {r["id"]: r for r in (json.loads(l) for l in text.splitlines() if l.strip())}


def drift(conn: sqlite3.Connection, app_id: int,
          ledger_then: Callable[[str], dict[str, dict]] = claims_at,
          ledger_now: Callable[[], dict[str, dict]] = verify.load_ledger) -> list[Flag]:
    row = conn.execute("SELECT status, submitted_at FROM applications WHERE id=?", (app_id,)).fetchone()
    if row is None or row["status"] not in SENT_STATUSES or not row["submitted_at"]:
        return []
    folder = drafts.find_folder(app_id)
    if folder is None:
        return []
    then, now = ledger_then(row["submitted_at"]), ledger_now()
    flags: list[Flag] = []
    for path in sorted((folder / "final").glob("*.md")):
        cited = sorted({c for s in parse_document(path) for c in s.claims})
        for cid in cited:
            old, new = then.get(cid), now.get(cid)
            if old is None or new is None:
                continue
            if old.get("text") != new.get("text") or new.get("status") == "retired" or new.get("sensitivity") == "never-use":
                flags.append(Flag("drift", f"{path.name} cites {cid}, changed since application {app_id} was sent: "
                                           f"was \"{old.get('text')}\"; now \"{new.get('text')}\" ({new.get('status')})"))
    return flags


def check_document(conn: sqlite3.Connection, draft: Path, app_id: int | None = None) -> list[Flag]:
    meta = _front_matter(draft)
    app_id = app_id or meta.get("application_id")
    if not app_id:
        raise SystemExit("give --application or put application_id in the draft's front matter")
    company_id, name = _company(conn, int(app_id))
    sent = sent_documents(conn, company_id, name, exclude_application=int(app_id))
    flags = compare(parse_document(draft), sent)
    for application in sorted({d.application_id for d in sent if d.application_id}):
        flags.extend(drift(conn, application))
    return flags


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.consistency", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check"); c.add_argument("draft", type=Path); c.add_argument("--application", type=int)
    s = sub.add_parser("sent"); s.add_argument("application", type=int)
    args = parser.parse_args(argv)
    conn = db.connect()
    try:
        flags = check_document(conn, args.draft, args.application) if args.command == "check" else drift(conn, args.application)
    finally:
        conn.close()
    if not flags:
        print("consistent: nothing already sent to this employer is contradicted")
        return 0
    for flag in flags:
        print(f"[{flag.kind}] {flag.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
