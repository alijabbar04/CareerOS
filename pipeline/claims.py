"""T-003/T-004: merge claim candidates, apply the candidate's resolutions, load the ledger, export the review file.

    python -m pipeline.claims merge      # brain/claims-candidates/*.jsonl -> brain/claims-merged.json
    python -m pipeline.claims load       # merged claims -> claims table (status candidate) + brain/claims.jsonl
    python -m pipeline.claims review     # -> brain/claims-review.md for the candidate
    python -m pipeline.claims apply-review   # read ticks/edits in claims-review.md back into the table
    python -m pipeline.claims export     # claims table -> brain/claims.jsonl (git-tracked copy of the ledger)
    python -m pipeline.claims all        # merge + load + review + export

Merging rule: two candidates are the same claim when their normalised texts are near-identical
(RapidFuzz token_set_ratio >= 92) and their categories agree. The merged claim keeps the most
specific text (most numbers, then longest) and every source citation.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

import yaml
from rapidfuzz import fuzz

from pipeline import config

CANDIDATES_DIR = config.BRAIN_DIR / "claims-candidates"
MERGED_PATH = config.BRAIN_DIR / "claims-merged.json"
REVIEW_PATH = config.BRAIN_DIR / "claims-review.md"
LEDGER_PATH = config.BRAIN_DIR / "claims.jsonl"
RESOLUTIONS_PATH = config.BRAIN_DIR / "resolutions.yaml"

STRENGTH_ORDER = ["none", "participated", "completed", "supported", "assisted", "achieved", "taught", "designed", "built", "managed", "co-led", "led"]
CATEGORY_ORDER = ["education", "experience", "project", "achievement", "skill", "story", "motivation", "interest", "preference", "fact"]


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9%£$ ]+", " ", text.lower()).strip()


def specificity(text: str) -> tuple[int, int]:
    return (len(re.findall(r"\d", text)), len(text))


KEY_TOKEN_RE = re.compile(r"\b(?:\d[\d,.%]*|january|february|march|april|may|june|july|august|september|october|november|december)\b", re.I)
MERGE_THRESHOLD = 88


MONTHS = {m: str(i) for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}


def key_tokens(text: str) -> set[str]:
    """Numbers and month names, normalised so that '12/2022' and 'December 2022' agree: two texts that
    differ in these are different facts, however similar otherwise."""
    out: set[str] = set()
    for t in KEY_TOKEN_RE.findall(text):
        t = t.lower().rstrip(".,")
        if t in MONTHS:
            out.add(MONTHS[t])
            continue
        for part in re.split(r"[/.-]", t):
            part = part.replace(",", "").lstrip("0") or "0"
            if part:
                out.add(part)
    return out


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------

def load_candidates() -> list[dict]:
    out = []
    for p in sorted(CANDIDATES_DIR.glob("*.jsonl")):
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"  ! {p.name}:{n}: {exc}")
                continue
            obj["_batch"] = p.stem
            out.append(obj)
    return out


def merge(candidates: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for c in candidates:
        text = (c.get("text") or "").strip()
        if not text:
            continue
        cat = c.get("category") or "fact"
        best, best_score = None, 0
        kt = key_tokens(text)
        for m in merged:
            if m["category"] != cat:
                continue
            mkt = key_tokens(m["text"])
            if kt and mkt and kt != mkt:
                continue  # same wording but different numbers or months: a distinct (possibly conflicting) fact
            s = fuzz.token_set_ratio(norm(text), norm(m["text"]))
            if s > best_score:
                best, best_score = m, s
        src = {
            "source_id": c.get("source_id"),
            "locator": c.get("locator"),
            "quote": c.get("quote"),
            "batch": c["_batch"],
            "confidence": c.get("confidence", "verified"),
            "notes": c.get("notes"),
        }
        if best and best_score >= MERGE_THRESHOLD:
            best["sources"].append(src)
            if specificity(text) > specificity(best["text"]):
                best["variants"].append(best["text"])
                best["text"] = text
            elif norm(text) != norm(best["text"]):
                best["variants"].append(text)
            best["tags"] = sorted(set(best["tags"]) | set(c.get("tags") or []))
            best["numbers"] = sorted(set(best["numbers"]) | set(map(str, c.get("numbers") or [])))
            best["names"] = sorted(set(best["names"]) | set(c.get("names") or []))
            sv = c.get("strength_verb") or "none"
            sv_rank = STRENGTH_ORDER.index(sv) if sv in STRENGTH_ORDER else 0
            cur_rank = STRENGTH_ORDER.index(best["strength"]) if best["strength"] in STRENGTH_ORDER else 0
            if sv_rank > cur_rank:
                best["strength"] = sv
            if not best.get("date_or_range") and c.get("date_or_range"):
                best["date_or_range"] = c["date_or_range"]
        else:
            merged.append({
                "text": text,
                "category": cat,
                "subject": c.get("subject") or "",
                "date_or_range": c.get("date_or_range"),
                "numbers": sorted(set(map(str, c.get("numbers") or []))),
                "names": sorted(set(c.get("names") or [])),
                "strength": c.get("strength_verb") or "none",
                "tags": sorted(set(c.get("tags") or [])),
                "sources": [src],
                "variants": [],
                "sensitivity": "free",
                "tier": "safe",
                "status": "candidate",
                "confidence": "verified" if src["confidence"] == "verified" else "inferred",
                "usage_notes": "",
                "conflicts_with": [],
            })
    return merged


def apply_resolutions(claims: list[dict]) -> list[dict]:
    if not RESOLUTIONS_PATH.exists():
        return claims
    res = yaml.safe_load(RESOLUTIONS_PATH.read_text(encoding="utf-8")) or {}
    for rule in res.get("rules", []):
        pat = re.compile(rule["match"], re.I)
        cats = rule.get("category_in")
        for c in claims:
            if cats and c["category"] not in cats:
                continue
            if pat.search(c["text"]) or any(pat.search(s.get("quote") or "") for s in c["sources"]):
                for k, v in (rule.get("set") or {}).items():
                    c[k] = v
                c.setdefault("resolution_applied", []).append(rule["match"])
    for add in res.get("add", []):
        claims.append({
            "text": add["text"],
            "category": add.get("category", "fact"),
            "subject": add.get("subject", ""),
            "date_or_range": add.get("date_or_range"),
            "numbers": [str(n) for n in add.get("numbers", [])],
            "names": add.get("names", []),
            "strength": add.get("strength", "none"),
            "tags": add.get("tags", []),
            "sources": [{"source_id": "ali-confirmation-2026-09-21", "locator": "conversation", "quote": add["text"], "batch": "resolutions", "confidence": "user_asserted"}],
            "variants": [],
            "sensitivity": add.get("sensitivity", "free"),
            "tier": add.get("tier", "safe"),
            "status": "confirmed",
            "confidence": add.get("confidence", "user_asserted"),
            "usage_notes": add.get("usage_notes", ""),
            "conflicts_with": [],
        })
    return claims


def strip_key_tokens(text: str) -> str:
    return norm(KEY_TOKEN_RE.sub(" ", text))


def find_conflicts(claims: list[dict]) -> None:
    """Flag pairs that say the same thing about the same subject but with different numbers, dates or months.
    Two claims conflict when, with numbers and months removed, their wording is near-identical (>= 88)
    yet their numbers/months differ. Different subjects (GCSE Biology vs GCSE Chemistry) or different
    roles at the same employer are not conflicts because their remaining wording differs."""
    by_category: dict[str, list[dict]] = {}
    for c in claims:
        by_category.setdefault(c["category"], []).append(c)
    for group in by_category.values():
        for i, a in enumerate(group):
            ka, ra = key_tokens(a["text"]), set(strip_key_tokens(a["text"]).split())
            for b in group[i + 1:]:
                kb, rb = key_tokens(b["text"]), set(strip_key_tokens(b["text"]).split())
                if not ka or not kb or ka == kb or ka <= kb or kb <= ka:
                    continue  # same numbers, or one claim is simply more specific than the other
                if len(ra ^ rb) <= 1 and fuzz.token_set_ratio(" ".join(sorted(ra)), " ".join(sorted(rb))) >= 88:
                    a["conflicts_with"].append(b["id"])
                    b["conflicts_with"].append(a["id"])


def assign_ids(claims: list[dict]) -> list[dict]:
    claims.sort(key=lambda c: (CATEGORY_ORDER.index(c["category"]) if c["category"] in CATEGORY_ORDER else 99, norm(c["subject"]), -specificity(c["text"])[0]))
    for n, c in enumerate(claims, 1):
        c["id"] = f"C-{n:04d}"
    return claims


def cmd_merge() -> list[dict]:
    cands = load_candidates()
    merged = merge(cands)
    merged = apply_resolutions(merged)
    merged = assign_ids(merged)
    find_conflicts(merged)
    MERGED_PATH.write_text(json.dumps(merged, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(cands)} candidates -> {len(merged)} merged claims -> {MERGED_PATH}")
    return merged


# ---------------------------------------------------------------------------
# load / export / review
# ---------------------------------------------------------------------------

def _db():
    from pipeline import db  # imported lazily so merge/review work without the database module
    return db


def ensure_source(conn, source_id: str) -> None:
    """Register one brain/sources/<id>.md that is not in the sources table yet, so a new claim can cite it.
    INSERT OR IGNORE, never REPLACE: existing source rows (and the claims that reference them) are left alone."""
    if conn.execute("SELECT 1 FROM sources WHERE id=?", (source_id,)).fetchone():
        return
    path = config.SOURCES_DIR / f"{source_id}.md"
    if not path.is_file():
        raise SystemExit(f"source {source_id} is neither registered nor present as {path}")
    fm = yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1]) or {}
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sources)").fetchall()}
    row = {
        "id": source_id, "path": str(path.relative_to(config.REPO_ROOT)), "kind": fm.get("kind"), "title": fm.get("title"),
        "date": str(fm.get("transcribed_on") or fm.get("doc_modified") or ""),
        "sha256": fm.get("sha256_file") or fm.get("sha256"),
        "ingested_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    data = {k: v for k, v in row.items() if k in cols}
    conn.execute(f"INSERT OR IGNORE INTO sources ({', '.join(data)}) VALUES ({', '.join('?' for _ in data)})", tuple(data.values()))


def sync_sources(conn) -> int:
    """Upsert every brain/sources/*.md (by front matter) into the sources table so claims can reference them.
    Uses whichever of the expected columns the table actually has."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sources)").fetchall()}
    n = 0
    rows = []
    for p in sorted(config.SOURCES_DIR.glob("*.md")):
        if p.name == "INDEX.md":
            continue
        raw = p.read_text(encoding="utf-8")
        if not raw.startswith("---"):
            continue
        try:
            fm = yaml.safe_load(raw.split("---", 2)[1]) or {}
        except yaml.YAMLError:
            continue
        rows.append({
            "id": fm.get("id") or p.stem,
            "path": str(p.relative_to(config.REPO_ROOT)),
            "kind": fm.get("kind"),
            "title": fm.get("title"),
            "date": str(fm.get("doc_modified") or fm.get("file_modified") or fm.get("transcribed_on") or fm.get("date_range") or ""),
            "sha256": fm.get("sha256_file") or fm.get("sha256") or fm.get("sha256_text"),
            "ingested_at": str(fm.get("extracted_at") or dt.datetime.now().isoformat(timespec="seconds")),
        })
    rows.append({"id": "ali-confirmation-2026-09-21", "path": "brain/resolutions.yaml", "kind": "confirmation",
                 "title": "Facts confirmed by the candidate in conversation on 2026-09-21", "date": "2026-09-21", "sha256": None,
                 "ingested_at": dt.datetime.now().isoformat(timespec="seconds")})
    for r in rows:
        data = {k: v for k, v in r.items() if k in cols}
        keys = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        conn.execute(f"INSERT OR REPLACE INTO sources ({keys}) VALUES ({marks})", tuple(data.values()))
        n += 1
    return n


def cmd_load() -> None:
    db = _db()
    db.migrate()
    claims = json.loads(MERGED_PATH.read_text(encoding="utf-8"))
    conn = db.connect()
    print(f"synced {sync_sources(conn)} sources")
    reviewed = conn.execute("SELECT COUNT(*) FROM claims WHERE reviewed_at IS NOT NULL").fetchone()[0]
    if reviewed and "--force" not in sys.argv:
        sys.exit(f"{reviewed} claims have already been reviewed by the candidate; re-loading would reassign ids. "
                 "Edit the ledger through apply-review instead, or pass --force to wipe and reload.")
    conn.execute("DELETE FROM claim_tags WHERE claim_id IN (SELECT id FROM claims WHERE created_by='pipeline.claims')")
    conn.execute("DELETE FROM claims WHERE created_by='pipeline.claims'")  # unreviewed rows are regenerated from the batches and resolutions
    n = 0
    for c in claims:
        primary = c["sources"][0]
        row = {
            "id": c["id"],
            "text": c["text"],
            "category": c["category"],
            "subject": c["subject"],
            "source_id": primary.get("source_id"),
            "locator": primary.get("locator"),
            "quote": primary.get("quote"),
            "valid_from": None,
            "valid_to": None,
            "strength": c.get("strength"),
            "sensitivity": c.get("sensitivity", "free"),
            "tier": c.get("tier", "safe"),
            "confidence": "user_asserted" if c.get("confidence") == "user_asserted" else ("verified" if c.get("confidence") == "verified" else "inferred"),
            "status": c.get("status", "candidate"),
            "conflicts_with": json.dumps(c.get("conflicts_with", [])),
            "usage_notes": (c.get("usage_notes") or "") + (" | date: " + c["date_or_range"] if c.get("date_or_range") else "") + (" | all sources: " + json.dumps(c["sources"], ensure_ascii=False) if len(c["sources"]) > 1 else ""),
            "created_by": "pipeline.claims",
        }
        db.insert_claim(row, conn=conn, tags=c.get("tags", []))
        n += 1
    conn.commit()
    print(f"loaded {n} claims into {config.DB_PATH}")
    cmd_export()


def cmd_import(path: Path | None = None) -> None:
    """Restore the claims table from the git-tracked export brain/claims.jsonl, preserving ids, statuses,
    review timestamps and tags. Existing rows with the same id are left untouched. Use after a database loss."""
    db = _db()
    db.migrate()
    conn = db.connect()
    print(f"synced {sync_sources(conn)} sources")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(claims)").fetchall()}
    inserted = skipped = 0
    for line in (path or LEDGER_PATH).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        tags = d.pop("tags", [])
        if conn.execute("SELECT 1 FROM claims WHERE id=?", (d["id"],)).fetchone():
            skipped += 1
            continue
        data = {k: v for k, v in d.items() if k in cols}
        conn.execute(f"INSERT INTO claims ({', '.join(data)}) VALUES ({', '.join('?' for _ in data)})", tuple(data.values()))
        for t in tags:
            conn.execute("INSERT OR IGNORE INTO claim_tags (claim_id, tag) VALUES (?, ?)", (d["id"], t))
        inserted += 1
    conn.commit()
    print(f"import: inserted {inserted}, skipped {skipped} existing")


def cmd_export() -> None:
    db = _db()
    conn = db.connect()
    rows = conn.execute("SELECT * FROM claims ORDER BY id").fetchall()
    with LEDGER_PATH.open("w", encoding="utf-8") as f:
        for r in rows:
            d = dict(r)
            d["tags"] = [t[0] for t in conn.execute("SELECT tag FROM claim_tags WHERE claim_id=? ORDER BY tag", (d["id"],)).fetchall()]
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"exported {len(rows)} claims -> {LEDGER_PATH}")


def cmd_review() -> None:
    claims = json.loads(MERGED_PATH.read_text(encoding="utf-8"))
    lines = [
        "# Claims review",
        "",
        f"Generated {dt.date.today().isoformat()}. {len(claims)} claims. For each line: leave `[ ]` to confirm as written, "
        "change to `[x]` to confirm, `[-]` to reject, or add a line starting with `> fix:` underneath with the corrected wording. "
        "Claims already marked confirmed came from your answers on 2026-09-21. Then run `python -m pipeline.claims apply-review`.",
        "",
    ]
    conflicts = [c for c in claims if c.get("conflicts_with")]
    if conflicts:
        lines += ["## Conflicts to resolve first", ""]
        seen = set()
        for c in conflicts:
            key = tuple(sorted([c["id"]] + c["conflicts_with"]))
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- {c['id']} vs {', '.join(c['conflicts_with'])}: \"{c['text']}\"")
        lines.append("")
    current = None
    for c in claims:
        if c["category"] != current:
            current = c["category"]
            lines += [f"## {current}", ""]
        box = "[x]" if c.get("status") == "confirmed" else ("[-]" if c.get("status") == "retired" else "[ ]")
        flags = []
        if c.get("sensitivity") != "free":
            flags.append(c["sensitivity"])
        if c.get("tier") == "specific":
            flags.append("specific figure")
        if c.get("confidence") == "inferred":
            flags.append("inferred")
        srcs = "; ".join(f"{s.get('source_id')}" for s in c["sources"][:3])
        lines.append(f"- {box} **{c['id']}** ({c['subject']}{'; ' + ', '.join(flags) if flags else ''}) {c['text']}")
        lines.append(f"  - sources: {srcs}{' (+' + str(len(c['sources']) - 3) + ' more)' if len(c['sources']) > 3 else ''}")
        if c.get("usage_notes"):
            lines.append(f"  - note: {c['usage_notes']}")
        if c.get("variants"):
            lines.append(f"  - other wordings seen: " + " | ".join(v[:120] for v in c["variants"][:3]))
    REVIEW_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"review file -> {REVIEW_PATH}")


EXCEPTIONS_PATH = config.BRAIN_DIR / "claims-review-exceptions.md"


def cmd_review_exceptions() -> None:
    """A short review file with only the claims that need the candidate's judgement: conflicts, inferred claims,
    use-with-approval or never-use items, and anything an extractor flagged as overstated or hedged.
    Everything else came straight from the candidate's own documents and can be confirmed in bulk."""
    claims = json.loads(MERGED_PATH.read_text(encoding="utf-8"))
    flagged = []
    for c in claims:
        reasons = []
        if c.get("conflicts_with"):
            reasons.append("conflicts with " + ", ".join(c["conflicts_with"]))
        if c.get("confidence") == "inferred":
            reasons.append("inferred by the extractor, not stated")
        if c.get("sensitivity") != "free":
            reasons.append(c["sensitivity"])
        notes = " ".join((s.get("notes") or "") for s in c["sources"]).lower()
        if any(w in notes for w in ("overstat", "exagger", "broaden", "dramat", "hedge", "vague", "unclear", "cannot confirm", "guess", "contradict", "differs", "disagree", "does not mention", "neither")):
            reasons.append("extractor note: " + next((s.get("notes") for s in c["sources"] if s.get("notes")), "")[:200])
        if reasons and c.get("status") != "retired":
            flagged.append((c, reasons))
    lines = [
        "# Claims needing the candidate's judgement",
        "",
        f"Generated {dt.date.today().isoformat()}. {len(flagged)} of {len(claims)} claims. The other "
        f"{len(claims) - len(flagged)} were extracted verbatim from the candidate's own CVs, LinkedIn, answers, letters and work log with no conflicts or flags; "
        "they can be confirmed in bulk once these are settled. Reply per id: keep / drop / fix: <wording>.",
        "",
    ]
    current = None
    for c, reasons in sorted(flagged, key=lambda x: x[0]["id"]):
        if c["category"] != current:
            current = c["category"]
            lines += [f"## {current}", ""]
        lines.append(f"- **{c['id']}** ({c['subject']}) {c['text']}")
        for r in reasons:
            lines.append(f"  - {r}")
        for cid in c.get("conflicts_with", [])[:4]:
            other = next((x for x in claims if x["id"] == cid), None)
            if other:
                lines.append(f"  - {cid}: \"{other['text'][:160]}\"")
        if c.get("usage_notes"):
            lines.append(f"  - note: {c['usage_notes'][:220]}")
    EXCEPTIONS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(flagged)} exceptions -> {EXCEPTIONS_PATH}")


def cmd_apply_decisions(path: Path) -> None:
    """Apply a YAML decisions file (brain/review-decisions/*.yaml) to the claims table in place, without
    reassigning ids. Actions: keep -> confirmed; fix -> text replaced, confirmed, user_asserted; drop -> retired,
    never-use. Extra keys (usage_notes, sensitivity, tier, strength, confidence) are written as given.
    `bulk_confirm_remaining: true` confirms every other candidate. `add:` inserts new user-asserted claims."""
    db = _db()
    conn = db.connect()
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    now = dt.datetime.now().isoformat(timespec="seconds")
    counts = {"keep": 0, "fix": 0, "drop": 0, "missing": 0, "added": 0, "bulk": 0}
    for d in doc.get("decisions", []):
        cid = d["id"]
        if not conn.execute("SELECT 1 FROM claims WHERE id=?", (cid,)).fetchone():
            counts["missing"] += 1
            print(f"  ! {cid} not found")
            continue
        sets = {"reviewed_at": now}
        action = d.get("action", "keep")
        if action == "keep":
            sets["status"] = "confirmed"
        elif action == "fix":
            sets.update({"status": "confirmed", "text": d["text"], "confidence": d.get("confidence", "user_asserted")})
        elif action == "drop":
            sets.update({"status": "retired", "sensitivity": "never-use"})
        for k in ("usage_notes", "sensitivity", "tier", "strength", "confidence"):
            if k in d and not (k == "confidence" and action == "fix"):
                sets[k] = d[k]
        if "usage_notes" in sets:
            prev = conn.execute("SELECT usage_notes FROM claims WHERE id=?", (cid,)).fetchone()[0] or ""
            sets["usage_notes"] = (sets["usage_notes"] + (" | " + prev if prev else "")).strip()
        assign = ", ".join(f"{k}=?" for k in sets)
        conn.execute(f"UPDATE claims SET {assign} WHERE id=?", (*sets.values(), cid))
        counts[action] += 1
    for a in doc.get("add", []):
        if a.get("source_id"):
            ensure_source(conn, a["source_id"])
        row = {
            "text": a["text"], "category": a.get("category", "fact"), "subject": a.get("subject", ""),
            # A claim taken from one of the candidate's documents names that source; otherwise it is his confirmation in the decisions file.
            "source_id": a.get("source_id", "ali-confirmation-2026-09-21"),
            "locator": a.get("locator") or (str(path.resolve().relative_to(config.REPO_ROOT)) if path.resolve().is_relative_to(config.REPO_ROOT) else str(path)),
            "quote": a.get("quote", a["text"]),
            "strength": a.get("strength"), "sensitivity": a.get("sensitivity", "free"), "tier": a.get("tier", "safe"),
            "confidence": a.get("confidence", "user_asserted"), "status": a.get("status", "confirmed"),
            "conflicts_with": "[]", "usage_notes": a.get("usage_notes", ""), "created_by": "review-decisions", "reviewed_at": now,
        }
        db.insert_claim(row, tags=a.get("tags", []), conn=conn)
        counts["added"] += 1
    if doc.get("bulk_confirm_remaining"):
        cur = conn.execute("UPDATE claims SET status='confirmed', reviewed_at=? WHERE status='candidate'", (now,))
        counts["bulk"] = cur.rowcount
    conn.commit()
    print(f"applied {path.name}: {counts}")
    cmd_export()


def cmd_apply_review() -> None:
    db = _db()
    conn = db.connect()
    text = REVIEW_PATH.read_text(encoding="utf-8")
    current_id = None
    confirmed = rejected = fixed = 0
    for line in text.splitlines():
        m = re.match(r"- \[( |x|-)\] \*\*(C-\d{4})\*\*", line)
        if m:
            current_id = m.group(2)
            mark = m.group(1)
            if mark == "x":
                conn.execute("UPDATE claims SET status='confirmed', reviewed_at=datetime('now') WHERE id=?", (current_id,))
                confirmed += 1
            elif mark == "-":
                conn.execute("UPDATE claims SET status='retired', reviewed_at=datetime('now') WHERE id=?", (current_id,))
                rejected += 1
            continue
        f = re.match(r"\s*> fix:\s*(.+)", line)
        if f and current_id:
            conn.execute("UPDATE claims SET text=?, confidence='user_asserted', status='confirmed', reviewed_at=datetime('now') WHERE id=?", (f.group(1).strip(), current_id))
            fixed += 1
    conn.commit()
    print(f"confirmed {confirmed}, rejected {rejected}, fixed {fixed}")
    cmd_export()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["merge", "load", "review", "review-exceptions", "apply-review", "apply-decisions", "export", "import", "all"])
    ap.add_argument("path", nargs="?", type=Path, help="decisions YAML for apply-decisions")
    ap.add_argument("--force", action="store_true", help="with load: wipe reviewed claims too")
    args = ap.parse_args()
    if args.cmd == "apply-decisions":
        if not args.path:
            sys.exit("apply-decisions needs a path to a decisions YAML")
        cmd_apply_decisions(args.path)
    elif args.cmd == "import":
        cmd_import(args.path)
    elif args.cmd == "merge":
        cmd_merge()
    elif args.cmd == "load":
        cmd_load()
    elif args.cmd == "review":
        cmd_review()
    elif args.cmd == "review-exceptions":
        cmd_review_exceptions()
    elif args.cmd == "apply-review":
        cmd_apply_review()
    elif args.cmd == "export":
        cmd_export()
    else:
        cmd_merge(); cmd_load(); cmd_review(); cmd_review_exceptions()
