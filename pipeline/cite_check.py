"""Deterministic citation check: every [C-nnnn] cited in a Markdown file must exist in brain/claims.jsonl
with status 'confirmed' and must not be 'never-use'. Also reports numbers in the text that do not appear in any
cited claim (a cheap inflation guard).

    python -m pipeline.cite_check <file-or-folder> [...]
Exit code 1 on any missing, retired or never-use citation.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from pipeline import config

CITE_RE = re.compile(r"C-\d{4}")
NUM_RE = re.compile(r"(?<![A-Za-z\d-])(\d[\d,.]*%?)")
META_HEADINGS = ("notes", "citations", "do not say", "what must never appear")


def strip_meta(text: str) -> str:
    out, skipping = [], False
    for line in text.splitlines():
        if line.startswith("#"):
            skipping = any(h in line.lower() for h in META_HEADINGS)
            continue
        if not skipping:
            out.append(line)
    return "\n".join(out)


def load_ledger() -> dict[str, dict]:
    path = config.BRAIN_DIR / "claims.jsonl"
    return {r["id"]: r for r in (json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip())}


def check_file(path: Path, ledger: dict[str, dict]) -> int:
    text = path.read_text(encoding="utf-8")
    body = text.split("---", 2)[2] if text.startswith("---") else text
    cited = sorted(set(CITE_RE.findall(text)))
    problems = 0
    for cid in cited:
        row = ledger.get(cid)
        if not row:
            print(f"  MISSING   {cid}")
            problems += 1
        elif row["status"] != "confirmed":
            print(f"  {row['status'].upper():9s} {cid}: {row['text'][:80]}")
            problems += 1
        elif row["sensitivity"] == "never-use":
            print(f"  NEVER-USE {cid}: {row['text'][:80]}")
            problems += 1
    cited_text = " ".join(ledger[c]["text"] for c in cited if c in ledger)
    cited_nums = {n.rstrip(".,") for n in NUM_RE.findall(cited_text)}
    scan = CITE_RE.sub(" ", strip_meta(body))
    unexplained = sorted({n.rstrip(".,") for n in NUM_RE.findall(scan)
                          if n.rstrip(".,") not in cited_nums and not re.fullmatch(r"\d{1,2}", n.rstrip(".,")) and not re.fullmatch(r"(19|20)\d{2}", n.rstrip(".,"))})
    print(f"{path.name}: {len(cited)} citations, {problems} problems" + (f", numbers not in cited claims: {unexplained}" if unexplained else ""))
    return problems


if __name__ == "__main__":
    ledger = load_ledger()
    targets: list[Path] = []
    for arg in sys.argv[1:] or [str(config.VAULT_DIR / "narratives"), str(config.VAULT_DIR / "stories")]:
        p = Path(arg)
        targets += sorted(p.glob("*.md")) if p.is_dir() else [p]
    total = sum(check_file(p, ledger) for p in targets if p.name != "README.md")
    sys.exit(1 if total else 0)
