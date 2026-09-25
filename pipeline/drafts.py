"""T-017: application folders, drafting briefs and the review queue.

    python -m pipeline.drafts init --posting 1542            # create the application + folder + brief
    python -m pipeline.drafts init --application 57          # rebuild the brief for an existing application
    python -m pipeline.drafts register 57 drafts/cover-letter-r1.md --kind cover_letter
    python -m pipeline.drafts ready 57                       # move to ready-for-review once every draft's report passed
    python -m pipeline.drafts approve-claims 57 C-0190 C-0191
    python -m pipeline.drafts queue                          # what is waiting for the candidate

Everything the drafter is allowed to know about an application is written to
`brain/vault/applications/<NNNN>-<company>/brief.md`: the posting, the employer
policy and the AI-use wording it implies, the company note (if researched), the
track narrative, the stories to use, the most relevant confirmed claims, and the
draft file format the verifier expects. The drafter agent reads that brief and
nothing else about the candidate, which keeps every fact traceable to a claim id.

The scripts here are deterministic so Codex can run the same steps through the
`codex/` wrappers; only the writing itself is done by a model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from pipeline import config, db, registry, settings

APPLICATIONS_DIR = config.VAULT_DIR / "applications"
COMPANIES_DIR = config.VAULT_DIR / "companies"
NARRATIVES_DIR = config.VAULT_DIR / "narratives"
STORIES_DIR = config.VAULT_DIR / "stories"
VOICE_DIR = config.VAULT_DIR / "voice"

DRAFT_KINDS = ("cover-letter", "answer", "email", "cv-notes")
DOC_TYPES = {"cover-letter": "cover_letter", "answer": "answers", "email": "other", "cv-notes": "cv"}
_WORD_RE = re.compile(r"[a-z][a-z0-9+#.]{2,}")
_STOP_WORDS = {
    "and", "the", "for", "with", "that", "this", "from", "your", "you", "our", "are", "will",
    "have", "has", "into", "role", "work", "working", "team", "their", "job", "who", "but",
    "not", "all", "can", "use", "using", "skills", "experience", "ali", "also", "his", "was",
    "were", "been", "being", "which", "about", "across", "within", "including", "more",
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", (value or "").casefold()).strip("-")
    return value[:40] or "unknown"


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall((text or "").casefold()) if w not in _STOP_WORDS}


def _overlap_score(query: set[str], text: str) -> float:
    tokens = _tokens(text)
    overlap = len(query & tokens)
    return overlap / math.sqrt(max(1, len(tokens))) if overlap else 0.0


def read_front_matter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) > 2:
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                meta = {}
            return (meta if isinstance(meta, dict) else {}), parts[2].lstrip("\n")
    return {}, text


def _rel(path: Path) -> str:
    """Repo-relative path when inside the repo, otherwise absolute (tests use temp folders)."""
    resolved = path.resolve()
    return str(resolved.relative_to(config.REPO_ROOT)) if resolved.is_relative_to(config.REPO_ROOT) else str(resolved)


def folder_for(app_id: int, company_name: str) -> Path:
    return APPLICATIONS_DIR / f"{app_id:04d}-{slugify(company_name)}"


def find_folder(app_id: int) -> Path | None:
    prefix = f"{app_id:04d}-"
    if not APPLICATIONS_DIR.exists():
        return None
    for path in sorted(APPLICATIONS_DIR.iterdir()):
        if path.is_dir() and path.name.startswith(prefix):
            return path
    return None


def _registry_entry(company_name: str) -> dict[str, Any]:
    try:
        entries = registry.load_registry()
    except Exception:  # registry missing or invalid: the brief still works without it
        return {}
    wanted = (company_name or "").casefold()
    for entry in entries:
        if str(entry.get("company", "")).casefold() == wanted:
            return entry
    return {}


def drafting_mode_for(company_name: str, entry: dict[str, Any], active: settings.Settings) -> tuple[str, str]:
    """Return (mode, reason). Settings overrides win, then the registry policy, then the default."""
    overrides = active.get("employer_ai_policy", "mode_overrides", default={}) or {}
    for name, mode in overrides.items():
        if str(name).casefold() == (company_name or "").casefold():
            return str(mode), f"settings.yaml mode_overrides names {name}"
    policy = str(entry.get("ai_policy", "unknown") or "unknown").casefold()
    if any(word in policy for word in ("proof-read", "proofread", "refine", "polish")) and any(word in policy for word in ("not generate", "not to generate", "should not be used to generate", "must not generate")):
        return "proofread_only", f"registry ai_policy allows refining but not generating: {entry.get('ai_policy')}"
    if any(word in policy for word in ("prohibit", "not permitted", "must not", "no ai", "banned", "not be used to generate")):
        return "outline_only", f"registry ai_policy: {entry.get('ai_policy')}"
    if "disclos" in policy or "declare" in policy:
        return "drafting_assisted", f"registry ai_policy asks for disclosure: {entry.get('ai_policy')}"
    default = str(active.get("employer_ai_policy", "default_mode", default="drafting_assisted"))
    return default, "default mode (no employer-specific policy recorded)"


def _has_events(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone())


def _choose_track(posting_track: str | None, app_track: str | None, entry: dict[str, Any], active: settings.Settings) -> str:
    ordered = [str(t) for t in (active.get("tracks", default=[]) or [])]
    candidates = [t.strip() for t in (app_track or "").split(",") if t.strip()]
    candidates += [str(t) for t in (entry.get("tracks") or [])]
    candidates += [t.strip() for t in (posting_track or "").split(",") if t.strip()]
    for track in ordered:
        if track in candidates:
            return track
    if candidates:
        return candidates[0]
    return ordered[0] if ordered else "1-aca-acca-training-contracts"


# ---------------------------------------------------------------------------
# Brief assembly
# ---------------------------------------------------------------------------

def _load_track(track: str) -> tuple[dict[str, Any], str, Path | None]:
    path = NARRATIVES_DIR / f"track-{track}.md"
    if not path.exists():
        return {}, "", None
    meta, body = read_front_matter(path)
    return meta, body, path


def _load_stories(track_meta: dict[str, Any], query: set[str], limit_extra: int = 3) -> list[dict[str, Any]]:
    """Stories named by the track file first, then the best keyword matches."""
    if not STORIES_DIR.exists():
        return []
    named = [str(s) for s in (track_meta.get("stories") or [])]
    stories: list[dict[str, Any]] = []
    seen: set[str] = set()
    catalogue: list[tuple[float, Path, dict[str, Any], str]] = []
    for path in sorted(STORIES_DIR.glob("*.md")):
        if path.name == "README.md":
            continue
        meta, body = read_front_matter(path)
        catalogue.append((_overlap_score(query, body), path, meta, body))
    by_stem = {path.stem: (meta, body, path) for _, path, meta, body in catalogue}
    for stem in named:
        if stem in by_stem and stem not in seen:
            meta, body, path = by_stem[stem]
            stories.append(_story_summary(stem, meta, body, path, "named by the track file"))
            seen.add(stem)
    for score, path, meta, body in sorted(catalogue, key=lambda item: -item[0]):
        if len(stories) >= len(named) + limit_extra:
            break
        if path.stem not in seen and score > 0:
            stories.append(_story_summary(path.stem, meta, body, path, "keyword match with the posting"))
            seen.add(path.stem)
    return stories


def _story_summary(stem: str, meta: dict[str, Any], body: str, path: Path, why: str) -> dict[str, Any]:
    short = ""
    match = re.search(r"### Short[^\n]*\n(.*?)(?:\n### |\Z)", body, re.S)
    if match:
        short = match.group(1).strip()
    return {
        "id": stem,
        "path": _rel(path),
        "competencies": meta.get("competencies") or [],
        "claims": meta.get("claims") or [],
        "why": why,
        "short": short,
    }


def _relevant_claims(conn: sqlite3.Connection, query: set[str], limit: int = 25) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = conn.execute(
        """SELECT id, text, category, subject, sensitivity, tier, usage_notes FROM claims
           WHERE status = 'confirmed' AND COALESCE(sensitivity, 'free') != 'never-use'"""
    ).fetchall()
    ranked = sorted(rows, key=lambda r: (-_overlap_score(query, f"{r['subject'] or ''} {r['text']}"), r["id"]))
    free: list[dict[str, Any]] = []
    approval: list[dict[str, Any]] = []
    for row in ranked:
        item = {k: row[k] for k in ("id", "text", "category", "subject", "sensitivity", "usage_notes")}
        if _overlap_score(query, f"{row['subject'] or ''} {row['text']}") <= 0 and len(free) >= limit:
            break
        if row["sensitivity"] == "use-with-approval":
            if len(approval) < 10:
                approval.append(item)
        elif len(free) < limit:
            free.append(item)
    return free, approval


def _previous_documents(conn: sqlite3.Connection, company_id: int, app_id: int) -> list[str]:
    rows = conn.execute(
        """SELECT d.path, d.doc_type, a.id AS app_id, a.role_title, a.submitted_at
           FROM documents d JOIN applications a ON a.id = d.application_id
           WHERE a.company_id = ? AND a.id != ? ORDER BY d.created_at""",
        (company_id, app_id),
    ).fetchall()
    return [f"{r['doc_type']} for application {r['app_id']} ({r['role_title']}, submitted {r['submitted_at'] or 'not submitted'}): {r['path']}" for r in rows]


def _previous_sources(company_name: str) -> list[str]:
    """Source files (T-002 ingest) whose name carries the employer: earlier letters, answers and emails to the same firm."""
    if not config.SOURCES_DIR.exists():
        return []
    slug = slugify(company_name)
    parts = [x for x in slug.split("-") if len(x) > 2]
    out: list[str] = []
    for path in sorted(config.SOURCES_DIR.glob("*.md")):
        name = path.name.casefold()
        if path.name == "INDEX.md":
            continue
        if slug in name or (parts and all(x in name for x in parts)):
            out.append(_rel(path))
    return out


def _qa_bank(conn: sqlite3.Connection, query: set[str], limit: int = 5) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, question, answer, competency, word_limit FROM qa_bank WHERE reuse_ok = 1").fetchall()
    ranked = sorted(rows, key=lambda r: -_overlap_score(query, f"{r['question']} {r['answer']}"))
    return [dict(r) for r in ranked[:limit] if _overlap_score(query, f"{r['question']} {r['answer']}") > 0]


def build_brief(conn: sqlite3.Connection, app_id: int, active: settings.Settings | None = None) -> Path:
    active = active or settings.Settings()
    app = conn.execute(
        """SELECT a.*, c.name AS company_name, c.id AS cid FROM applications a
           JOIN companies c ON c.id = a.company_id WHERE a.id = ?""",
        (app_id,),
    ).fetchone()
    if app is None:
        raise ValueError(f"no application with id {app_id}")
    posting = conn.execute("SELECT * FROM postings WHERE id = ?", (app["posting_id"],)).fetchone() if app["posting_id"] else None
    entry = _registry_entry(app["company_name"])
    mode, mode_reason = drafting_mode_for(app["company_name"], entry, active)
    if app["drafting_mode"] and app["drafting_mode"] != mode:
        mode, mode_reason = app["drafting_mode"], "recorded on the application row"
    declaration = active.get("employer_ai_policy", "ai_declaration", mode, default="") or ""
    track = _choose_track(posting["track"] if posting else None, app["track"], entry, active)
    override = conn.execute("SELECT detail_json FROM events WHERE entity='application' AND entity_id=? AND type='track_override' ORDER BY id DESC LIMIT 1", (app_id,)).fetchone() if _has_events(conn) else None
    if override:
        import json as _json
        track = _json.loads(override[0]).get("track", track)
    track_meta, track_body, track_path = _load_track(track)

    posting_text = " ".join(
        str(x or "")
        for x in ((posting["title"], posting["description"], posting["requirements"]) if posting else (app["role_title"],))
    )
    query = _tokens(posting_text) | _tokens(app["role_title"])
    stories = _load_stories(track_meta, query)
    free_claims, approval_claims = _relevant_claims(conn, query)
    previous = _previous_documents(conn, app["cid"], app_id)
    qa = _qa_bank(conn, query)

    folder = find_folder(app_id) or folder_for(app_id, app["company_name"])
    for sub in ("drafts", "reports", "final"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    company_note = COMPANIES_DIR / f"{slugify(app['company_name'])}.md"
    approvals_path = folder / "approvals.yaml"
    approved_ids: list[str] = []
    if approvals_path.exists():
        approved = yaml.safe_load(approvals_path.read_text(encoding="utf-8")) or {}
        approved_ids = [str(c) for c in (approved.get("claims") or [])]
    questions_path = folder / "questions.yaml"
    questions: list[dict[str, Any]] = []
    if questions_path.exists():
        loaded = yaml.safe_load(questions_path.read_text(encoding="utf-8")) or {}
        questions = list(loaded.get("questions") or [])

    meta = {
        "application_id": app_id,
        "posting_id": app["posting_id"],
        "company": app["company_name"],
        "role": app["role_title"],
        "track": track,
        "mode": mode,
        "mode_reason": mode_reason,
        "ai_declaration": declaration,
        "deadline": (posting["closes_at"] if posting else None) or app["deadline"],
        "company_note": _rel(company_note) if company_note.exists() else None,
        "approved_claims": approved_ids,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    lines: list[str] = ["---", yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).rstrip(), "---", ""]
    lines += [f"# Drafting brief: {app['role_title']} at {app['company_name']}", ""]
    lines += [
        "Read this file, the files it names, and nothing else about the candidate. Every factual sentence you write must map to a claim id listed here or in a named file. If a fact you need is missing, write `FACT REQUEST:` lines at the end of the draft instead of guessing.",
        "",
        "## 1. The posting",
        "",
    ]
    if posting:
        lines += [
            f"- Title: {posting['title']}",
            f"- Company: {app['company_name']}",
            f"- Location: {posting['location'] or 'not stated'}",
            f"- Closing date: {posting['closes_at'] or 'not published'}",
            f"- URL: {posting['canonical_url'] or posting['url'] or 'unknown'}",
            f"- Class-year wording: {posting['class_year_rule'] or 'none recorded'}",
            f"- AI policy hint from the posting: {posting['ai_policy_hint'] or 'none recorded'}",
            "",
            "### Description (trimmed)",
            "",
            _clean(posting["description"] or "")[:3500] or "(no description captured)",
            "",
        ]
        if posting["requirements"]:
            lines += ["### Requirements", "", _clean(posting["requirements"])[:1500], ""]
    else:
        lines += [f"- Role: {app['role_title']} (no posting row; details from the candidate)", ""]

    lines += ["## 2. Employer policy and drafting mode", ""]
    lines += [
        f"- Drafting mode: **{mode}** ({mode_reason}).",
        f"- AI-use declaration to use if a form asks (never reworded): \"{declaration}\"" if declaration else "- AI-use declaration: none configured for this mode.",
        f"- Registry AI policy: {entry.get('ai_policy', 'unknown')}",
        f"- Registry class-year rule: {entry.get('class_year_rule', 'none found')}",
        f"- Academic requirement: {entry.get('academic_requirement', 'unknown')}",
        f"- Application limit: {entry.get('apply_limit', 'unknown')}",
        f"- Registry notes: {entry.get('notes', '')}".rstrip(),
        "",
    ]
    if mode == "outline_only":
        lines += ["This employer prohibits AI-written text. Produce an outline (facts, story, structure, firm research) only; the candidate writes every sentence.", ""]
    elif mode == "proofread_only":
        lines += ["the candidate writes the text; the system researches, structures and polishes. Draft only where the candidate has supplied his own wording.", ""]

    lines += ["## 3. Questions and limits", ""]
    if questions:
        for i, q in enumerate(questions, start=1):
            lines.append(f"{i}. {q.get('question')} (limit: {q.get('word_limit') or q.get('char_limit') or 'not stated'})")
    else:
        lines.append("No form questions recorded yet. Put them in `questions.yaml` (`questions: [{question, word_limit}]`) once the form has been opened; until then draft the cover letter and a 'why this firm' answer of 250 words.")
    lines.append("")

    lines += ["## 4. Company research", ""]
    if company_note.exists():
        lines += [f"From `{meta['company_note']}` (every fact carries its URL and retrieval date; use only those):", "", company_note.read_text(encoding="utf-8").strip(), ""]
    else:
        lines += [f"No company note yet. Run `/research {app['company_name']}` first; without it, write nothing firm-specific beyond the posting text.", ""]

    lines += ["## 5. Track narrative", ""]
    if track_path:
        lines += [f"From `{_rel(track_path)}` (follow it; use the goal statement for this track and no other):", "", track_body.strip(), ""]
    else:
        lines += [f"No narrative file for track {track}; use `brain/vault/narratives/master.md`.", ""]
    lines += ["Also read `brain/vault/narratives/master.md`, especially 'What must never appear'.", ""]

    lines += ["## 6. Stories to draw on", ""]
    for story in stories:
        lines += [f"### {story['id']} ({story['why']}; competencies: {', '.join(map(str, story['competencies'])) or 'n/a'}; claims: {', '.join(map(str, story['claims']))})", "", f"Path: `{story['path']}` (read the full file for the medium and long versions).", "", story["short"] or "(no short version)", ""]
    if not stories:
        lines += ["No stories matched; read `brain/vault/stories/` directly.", ""]

    lines += ["## 7. Relevant confirmed claims", "", "Free to use (cite by id):", ""]
    for c in free_claims:
        note = f" — usage note: {c['usage_notes']}" if c["usage_notes"] else ""
        lines.append(f"- {c['id']} [{c['category']}] {c['text']}{note}")
    lines.append("")
    if approval_claims:
        lines += ["Usable only with the candidate's approval (ask before citing; approved ids for this application are listed in the front matter):", ""]
        for c in approval_claims:
            flag = " (APPROVED for this application)" if c["id"] in approved_ids else ""
            lines.append(f"- {c['id']} [{c['category']}]{flag} {c['text']}")
        lines.append("")

    if qa:
        lines += ["## 8. Answers the candidate has already written (adapt, do not regenerate)", ""]
        for item in qa:
            lines += [f"**Q: {item['question']}** (competency: {item['competency'] or 'n/a'}; limit {item['word_limit'] or 'n/a'})", "", item["answer"].strip(), ""]
    earlier_sources = _previous_sources(app["company_name"])
    if previous or earlier_sources:
        lines += ["## 9. Already sent to this employer (read it; stay consistent with it and do not repeat it word for word)", ""]
        lines += [f"- {p}" for p in previous]
        lines += [f"- earlier source: `{p}`" for p in earlier_sources]
        lines.append("")

    lines += [
        "## 10. Voice",
        "",
        "Read `brain/vault/voice/style-guide.md` and pick three exemplars from `brain/vault/voice/exemplars.md` for this artefact type. Hard rules: no exclamation marks, no rhetorical questions, no em-dashes, UK spelling, no contractions in letters or emails, no bullet lists in prose, no summary closer, no phrase from the banned list in `brain/vault/voice/phrases.yaml`. Letters open \"Dear [Firm] Recruitment Team,\" and close \"Yours faithfully, Candidate Name\" unless a named contact is known.",
        "",
        "## 11. Draft file format (the verifier depends on it)",
        "",
        "Write each draft to `drafts/<kind>-r<round>.md` in this folder, where kind is one of cover-letter, answer-<n>, email, cv-notes. Front matter, then the prose, then a `## Citations` section:",
        "",
        "```",
        "---",
        f"application_id: {app_id}",
        "kind: cover-letter          # cover-letter | answer | email | cv-notes",
        "question: \"\"                # the form question, for answers",
        "word_limit: 400",
        "round: 1",
        f"mode: {mode}",
        "generated_by: drafter",
        "---",
        "Dear ... ,",
        "",
        "Prose paragraphs. No headings, no bullets.",
        "",
        "Yours faithfully,",
        "Candidate Name",
        "",
        "## Citations",
        "- S1: greeting",
        "- S2: C-0020, C-0164",
        "- S3: motivation",
        "- S4: research",
        "```",
        "",
        "Sentence numbers follow the order the verifier splits the prose (`pipeline.style.sentences`). Tags: `greeting`, `closing`, `motivation`, `opinion` (no fact asserted), `research` (fact from the company note), or one or more claim ids. A factual sentence with no id is a hard failure. Any number in a sentence must appear in a cited claim or the company note.",
        "",
    ]

    brief_path = folder / "brief.md"
    brief_path.write_text("\n".join(lines), encoding="utf-8")
    return brief_path


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _current_cycle(active: settings.Settings, today: date) -> tuple[str, str]:
    start = str(active.get("caps", "cycle_start", default=f"{today.year}-01-01"))
    year = int(start[:4])
    return start, f"{year + 1}"


def cmd_init(args: argparse.Namespace) -> int:
    active = settings.Settings()
    conn = db.connect()
    db.migrate(conn)
    today = date.today()
    cycle_start, cycle_label = _current_cycle(active, today)
    if args.application:
        app_id = int(args.application)
        if args.mode:
            conn.execute("UPDATE applications SET drafting_mode = ?, updated_at = datetime('now') WHERE id = ?", (args.mode, app_id))
            conn.commit()
            db.log_event("application", app_id, "mode_set", {"mode": args.mode}, source="agent", conn=conn)
    else:
        posting = conn.execute("SELECT p.*, c.name AS company_name FROM postings p JOIN companies c ON c.id = p.company_id WHERE p.id = ?", (int(args.posting),)).fetchone()
        if posting is None:
            print(f"no posting with id {args.posting}")
            conn.close()
            return 2
        existing = conn.execute("SELECT id, status FROM applications WHERE posting_id = ?", (posting["id"],)).fetchone()
        if existing:
            app_id = int(existing["id"])
            print(f"application {app_id} already exists for posting {posting['id']} (status {existing['status']}); rebuilding the brief")
        else:
            clash = conn.execute(
                """SELECT id, role_title, status, submitted_at FROM applications
                   WHERE company_id = ? AND (
                        (submitted_at IS NOT NULL AND date(submitted_at) >= date(?))
                     OR (submitted_at IS NULL AND status IN ('draft','ready-for-review','approved') AND date(created_at) >= date(?)))""",
                (posting["company_id"], cycle_start, cycle_start),
            ).fetchone()
            if clash and not args.force:
                print(
                    f"refused: application {clash['id']} to {posting['company_name']} ({clash['role_title']}, {clash['status']}) already exists in the cycle starting {cycle_start}. "
                    "One application per employer per cycle; pass --force only if the candidate has said so."
                )
                conn.close()
                return 3
            entry = _registry_entry(posting["company_name"])
            mode, _ = drafting_mode_for(posting["company_name"], entry, active)
            if args.mode:
                mode = args.mode
            app_id = db.create_application(int(posting["id"]), int(posting["company_id"]), str(posting["title"]), cycle_label, mode, conn=conn)
            track = _choose_track(posting["track"], None, entry, active)
            conn.execute("UPDATE applications SET track = ?, deadline = ?, updated_at = datetime('now') WHERE id = ?", (track, posting["closes_at"], app_id))
            conn.commit()
            db.log_event("application", app_id, "created", {"posting_id": posting["id"], "mode": mode, "track": track}, source="agent", conn=conn)
            print(f"created application {app_id} for posting {posting['id']} ({posting['title']} at {posting['company_name']}) in mode {mode}")
    if getattr(args, "track", None):
        conn.execute("UPDATE applications SET track = ?, updated_at = datetime('now') WHERE id = ?", (args.track, app_id))
        conn.commit()
        db.log_event("application", app_id, "track_override", {"track": args.track}, source="ali", conn=conn)
    brief = build_brief(conn, app_id, active)
    conn.close()
    print(f"brief: {brief}")
    print(f"folder: {brief.parent}")
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    conn = db.connect()
    app_id = int(args.application)
    path = Path(args.draft)
    if not path.is_absolute():
        folder = find_folder(app_id)
        candidate = (folder / path) if folder else path
        path = candidate if candidate.exists() else path
    if not path.exists():
        print(f"draft not found: {path}")
        return 2
    meta, _ = read_front_matter(path)
    kind = args.kind or str(meta.get("kind", "other")).split("-")[0]
    doc_type = DOC_TYPES.get(kind, DOC_TYPES.get(str(meta.get("kind", "")).split("-")[0], "other"))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    rel = _rel(path)
    version = 1 + conn.execute("SELECT COUNT(*) FROM documents WHERE application_id = ? AND doc_type = ?", (app_id, doc_type)).fetchone()[0]
    conn.execute(
        "INSERT INTO documents (application_id, doc_type, path, version, sha256, generated_by, model) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (app_id, doc_type, rel, version, digest, str(meta.get("generated_by", "drafter")), str(meta.get("model", "")) or None),
    )
    conn.commit()
    db.log_event("application", app_id, "document_registered", {"path": rel, "doc_type": doc_type, "version": version, "sha256": digest[:12]}, source="agent", conn=conn)
    print(f"registered {rel} as {doc_type} v{version} for application {app_id}")
    return 0


def latest_reports(folder: Path) -> dict[str, dict[str, Any]]:
    """Newest verify report per draft stem, read from reports/verify-<stem>.json."""
    out: dict[str, dict[str, Any]] = {}
    reports = folder / "reports"
    if not reports.exists():
        return out
    for path in sorted(reports.glob("verify-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out[path.stem.replace("verify-", "", 1)] = data
    return out


def latest_drafts(folder: Path) -> list[Path]:
    """The newest round of each draft kind (answer-1-r2.md supersedes answer-1-r1.md)."""
    drafts_dir = folder / "drafts"
    if not drafts_dir.exists():
        return []
    best: dict[str, tuple[int, Path]] = {}
    for path in sorted(drafts_dir.glob("*.md")):
        m = re.fullmatch(r"(.+?)-r(\d+)", path.stem)
        stem, rnd = (m.group(1), int(m.group(2))) if m else (path.stem, 0)
        if stem not in best or rnd > best[stem][0]:
            best[stem] = (rnd, path)
    return [pair[1] for _, pair in sorted(best.items())]


def cmd_ready(args: argparse.Namespace) -> int:
    conn = db.connect()
    app_id = int(args.application)
    folder = find_folder(app_id)
    if folder is None:
        print(f"no folder for application {app_id}; run init first")
        return 2
    drafts = latest_drafts(folder)
    if not drafts:
        print("no drafts to review")
        return 2
    reports = latest_reports(folder)
    blocking: list[str] = []
    for draft in drafts:
        report = reports.get(draft.stem)
        if report is None:
            blocking.append(f"{draft.name}: no verify report (run python -m pipeline.verify {draft})")
        elif report.get("hard_fails"):
            blocking.append(f"{draft.name}: {len(report['hard_fails'])} hard failure(s) in the latest report")
    if blocking and not args.force:
        print("not ready:")
        for line in blocking:
            print(f"  - {line}")
        return 3
    try:
        db.transition_application(app_id, "ready-for-review", "agent", {"drafts": [d.name for d in drafts]}, conn=conn)
    except sqlite3.IntegrityError as exc:
        print(f"transition refused: {exc}")
        return 3
    print(f"application {app_id} is ready for the candidate's review: {folder}")
    try:
        from pipeline import publish  # imported here: publish imports this module

        result = publish.publish_application(folder)
        print(f"published to {result['onedrive']} (phone) and {result['local']} (Documents)")
    except Exception as exc:  # publishing is a convenience; never block the review queue on it
        print(f"publish skipped: {type(exc).__name__}: {exc}")
    return 0


def cmd_approve_claims(args: argparse.Namespace) -> int:
    app_id = int(args.application)
    folder = find_folder(app_id)
    if folder is None:
        print(f"no folder for application {app_id}; run init first")
        return 2
    path = folder / "approvals.yaml"
    current = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    current = current or {}
    claims = sorted(set(str(c) for c in (current.get("claims") or [])) | set(args.claims))
    current["claims"] = claims
    current.setdefault("history", []).append({"date": date.today().isoformat(), "added": sorted(set(args.claims)), "by": "ali"})
    path.write_text(yaml.safe_dump(current, sort_keys=False), encoding="utf-8")
    conn = db.connect()
    db.log_event("application", app_id, "claims_approved", {"claims": sorted(set(args.claims))}, source="ali", conn=conn)
    print(f"approved for application {app_id}: {', '.join(claims)}")
    return 0


def cmd_queue(args: argparse.Namespace) -> int:
    conn = db.connect()
    rows = conn.execute(
        """SELECT a.id, a.role_title, a.status, a.deadline, a.drafting_mode, c.name AS company_name, a.updated_at
           FROM applications a JOIN companies c ON c.id = a.company_id
           WHERE a.status IN ('ready-for-review', 'draft') AND a.created_at >= date('now', '-60 days')
           ORDER BY CASE a.status WHEN 'ready-for-review' THEN 0 ELSE 1 END, a.deadline IS NULL, a.deadline, a.id"""
    ).fetchall()
    if not rows:
        print("Review queue: empty")
        return 0
    print("Review queue")
    for row in rows:
        folder = find_folder(int(row["id"]))
        reports = latest_reports(folder) if folder else {}
        drafts = latest_drafts(folder) if folder else []
        summary = []
        for draft in drafts:
            report = reports.get(draft.stem)
            if report is None:
                summary.append(f"{draft.stem}: unverified")
            else:
                summary.append(f"{draft.stem}: {'PASS' if not report.get('hard_fails') else 'FAIL'} ({len(report.get('warnings', []))} warnings)")
        print(f"- [{row['status']}] #{row['id']} {row['role_title']} at {row['company_name']} (deadline {row['deadline'] or 'n/a'}, mode {row['drafting_mode']})")
        print(f"    folder: {folder if folder else 'none yet'}")
        if summary:
            print("    drafts: " + "; ".join(summary))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.drafts", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="create the application (from a posting) and write its brief")
    group = p_init.add_mutually_exclusive_group(required=True)
    group.add_argument("--posting", type=int)
    group.add_argument("--application", type=int)
    p_init.add_argument("--mode", choices=("drafting_assisted", "proofread_only", "outline_only"))
    p_init.add_argument("--track", help="override the narrative track for this application (recorded as a track_override event; e.g. a support role at a wealth firm is 7-fintech-ops-and-analyst, not the training-contract track)")
    p_init.add_argument("--force", action="store_true", help="override the one-application-per-employer-per-cycle guard (only if the candidate said so)")
    p_init.set_defaults(func=cmd_init)

    p_reg = sub.add_parser("register", help="record a draft file in the documents table")
    p_reg.add_argument("application", type=int)
    p_reg.add_argument("draft")
    p_reg.add_argument("--kind", choices=DRAFT_KINDS)
    p_reg.set_defaults(func=cmd_register)

    p_ready = sub.add_parser("ready", help="move the application to ready-for-review when every draft's report passed")
    p_ready.add_argument("application", type=int)
    p_ready.add_argument("--force", action="store_true")
    p_ready.set_defaults(func=cmd_ready)

    p_appr = sub.add_parser("approve-claims", help="record the candidate's approval of use-with-approval claims for this application")
    p_appr.add_argument("application", type=int)
    p_appr.add_argument("claims", nargs="+")
    p_appr.set_defaults(func=cmd_approve_claims)

    p_queue = sub.add_parser("queue", help="list applications waiting for the candidate")
    p_queue.set_defaults(func=cmd_queue)

    args = parser.parse_args(argv)
    config.ensure_dirs()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
