"""T-019: the question bank, filled from the candidate's approved answers.

    python -m pipeline.qa_bank sync      # load every approved or sent final answer into qa_bank
    python -m pipeline.qa_bank list

Only text the candidate approved goes in: the `final/` answers and employment entries of applications that are approved or later.
An answer is marked `reuse_ok = 0` when a claim it cites has changed in the ledger since it was sent (see
`pipeline.consistency.drift`), so drafting briefs, which read `reuse_ok = 1` rows only, never offer an outdated figure.
"""
from __future__ import annotations

import argparse
import sqlite3
from typing import Callable

from pipeline import consistency, db, drafts, verify

APPROVED_OR_LATER = ("approved",) + consistency.SENT_STATUSES
COMPETENCIES = (  # first match wins
    ("employment", ("tasks undertaken", "employment history")),
    ("gap", ("gap year", "not been employed", "since graduation")),
    ("motivation-career", ("career in", "considering a career")),
    ("motivation-firm", ("working at", "why do you want", "why us", "join us", "considering working")),
    ("skills", ("skills", "awards", "positions of responsibility")),
    ("summary", ("personal summary", "about yourself", "tell us about you")),
)


def competency(question: str) -> str | None:
    q = question.casefold()
    return next((name for name, cues in COMPETENCIES if any(cue in q for cue in cues)), None)


def sync(conn: sqlite3.Connection,
         drift: Callable[[sqlite3.Connection, int], list[consistency.Flag]] = consistency.drift) -> int:
    marks = ",".join("?" for _ in APPROVED_OR_LATER)
    rows = conn.execute(f"SELECT id, status FROM applications WHERE status IN ({marks})", APPROVED_OR_LATER).fetchall()
    written = 0
    for app in rows:
        folder = drafts.find_folder(app["id"])
        if folder is None or not (folder / "final").is_dir():
            continue
        changed = {f.message.split(" cites ")[0] for f in drift(conn, app["id"])}   # file names with outdated claims
        for path in sorted((folder / "final").glob("*.md")):
            meta, body, _mapping, _problems = verify.split_draft(path.read_text(encoding="utf-8"))
            kind = str(meta.get("kind") or "")
            if not (kind.startswith("answer") or kind.startswith("employment")) or not meta.get("question"):
                continue
            question = str(meta["question"])
            conn.execute("DELETE FROM qa_bank WHERE application_id=? AND question=?", (app["id"], question))
            conn.execute(
                "INSERT INTO qa_bank (question, answer, application_id, competency, word_limit, reuse_ok, quality) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (question, body.strip(), app["id"], competency(question), meta.get("word_limit"),
                 0 if path.name in changed else 1, 5 if app["status"] in consistency.SENT_STATUSES else 4),
            )
            written += 1
    conn.commit()
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.qa_bank", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["sync", "list"])
    args = parser.parse_args(argv)
    conn = db.connect()
    try:
        if args.command == "sync":
            print(f"qa_bank: {sync(conn)} approved answers loaded")
        for row in conn.execute("SELECT application_id, competency, reuse_ok, question FROM qa_bank ORDER BY application_id, id"):
            flag = "" if row["reuse_ok"] else "  [outdated: a cited fact changed after sending]"
            print(f"  {row['application_id']:>3} {row['competency'] or '-':<18} {row['question'][:70]}{flag}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
