"""CareerOS companion API (T-044 scaffold, endpoints completed under T-045).

    python -m app.api                # serves http://127.0.0.1:8765 (API) and the UI at /

Read-mostly over the tracker database and the application folders. Every write calls an existing pipeline function
(verify, publish, drafts init, db transitions) and is logged in `events`, so settings.yaml, hooks and the audit log
apply. Never exposes the vault, .env, never-use claims or raw source documents. Contract: app/api/openapi.yaml.

Writes need the header `X-CareerOS-Client: companion` and, when the browser sends an Origin, one that matches the
host being called. A web page the candidate happens to visit cannot therefore post approvals to this local API: the custom
header forces a CORS preflight, which this API never grants.
"""
from __future__ import annotations

import argparse
import contextlib
import difflib
import io
import json
import re
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import yaml
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from pipeline import config, db, drafts, settings, verify
from pipeline.publish import prose_body, publish_application

VERSION = "0.2.0"
_UI_ROOT = Path(__file__).resolve().parent.parent / "ui"
# The built PWA (cd app/ui && npm run build) when present, otherwise the T-044 shell.
UI_DIR = _UI_ROOT / "dist" if (_UI_ROOT / "dist" / "index.html").exists() else _UI_ROOT / "shell"
HOST, PORT = "127.0.0.1", 8765
STEM_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
CLIENT_HEADER = "x-careeros-client"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

app = FastAPI(title="CareerOS companion API", version=VERSION, docs_url="/docs", openapi_url="/openapi.json")
PUBLISH: Callable[[Path], dict[str, Any]] = publish_application   # replaced in tests so nothing is written to OneDrive


@app.middleware("http")
async def write_guard(request: Request, call_next):
    if request.method not in SAFE_METHODS:
        if request.headers.get(CLIENT_HEADER) != "companion":
            return JSONResponse({"detail": "writes need the companion app's header"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "cross-origin write refused"}, status_code=403)
    return await call_next(request)


def _connect():
    try:
        return db.connect()
    except RuntimeError as exc:  # missing live database: fail closed, say so
        raise HTTPException(status_code=503, detail=str(exc))


def _folder(app_id: int) -> Path | None:
    return drafts.find_folder(app_id)


def _stem(stem: str) -> str:
    if not STEM_RE.fullmatch(stem):
        raise HTTPException(status_code=404, detail="unknown draft")
    return stem


def _kind(stem: str) -> str:
    return re.sub(r"-r\d+$", "", stem)


def _report(folder: Path, stem: str) -> dict[str, Any]:
    path = folder / "reports" / f"verify-{stem}.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"hard_fails": data.get("hard_fails", []), "warnings": data.get("warnings", []),
            "metrics": {k: data.get("metrics", {}).get(k) for k in ("words", "claims_per_100_words", "sentence_len_sd_ratio")}}


def _verdicts(folder: Path, stem: str) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for key, prefix, pattern in (("verifier", "verifier", r"VERDICT:\s*(PASS|FAIL)"), ("style", "critic", r"STYLE:\s*(PASS|FAIL)"),
                                 ("red_team", "redteam", r"RED TEAM:\s*(PASS|FAIL)")):
        path = folder / "reports" / f"{prefix}-{stem}.md"
        m = re.search(pattern, path.read_text(encoding="utf-8")) if path.exists() else None
        out[key] = m.group(1) if m else None
    return out


def _draft(folder: Path, path: Path, approved: bool) -> dict[str, Any]:
    meta, body = prose_body(path.read_text(encoding="utf-8"))
    return {
        "stem": path.stem, "kind": meta.get("kind"), "question": meta.get("question"), "round": meta.get("round"),
        "words": len(re.findall(r"[A-Za-z']+", body)), "text": body, "verify": _report(folder, path.stem),
        "verdicts": _verdicts(folder, path.stem), "approved": approved,
    }


def _log(app_id: int, kind: str, detail: dict[str, Any]) -> None:
    conn = _connect()
    try:
        db.log_event("application", app_id, kind, detail, source="ali-companion", conn=conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": VERSION, "database": config.DB_PATH.exists()}


@app.get("/applications")
def applications(status: str | None = None) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT a.id, a.role_title, a.track, a.status, a.deadline, a.drafting_mode, c.name AS company FROM applications a "
            "JOIN companies c ON c.id = a.company_id" + (" WHERE a.status = ?" if status else "")
            + " ORDER BY a.deadline IS NULL, a.deadline, a.id DESC",
            (status,) if status else (),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        folder = _folder(int(r["id"]))
        latest = drafts.latest_drafts(folder) if folder else []
        finals = list((folder / "final").glob("*.md")) if folder and (folder / "final").exists() else []
        fact_requests = sum(1 for d in latest for ln in d.read_text(encoding="utf-8").splitlines()
                            if ln.strip().upper().startswith("FACT REQUEST"))
        out.append({"id": r["id"], "company": r["company"], "role": r["role_title"], "track": r["track"], "status": r["status"],
                    "deadline": r["deadline"], "drafting_mode": r["drafting_mode"], "drafts": len(latest), "finals": len(finals),
                    "fact_requests": fact_requests, "folder": str(folder) if folder else None})
    return out


@app.get("/applications/{app_id}")
def application(app_id: int) -> dict[str, Any]:
    rows = [a for a in applications() if a["id"] == app_id]
    if not rows:
        raise HTTPException(status_code=404, detail="unknown application")
    detail = rows[0]
    folder = _folder(app_id)
    if folder:
        brief = folder / "brief.md"
        detail["brief"] = drafts.read_front_matter(brief)[0] if brief.exists() else {}
        finals = sorted((folder / "final").glob("*.md")) if (folder / "final").exists() else []
        final_kinds = {_kind(f.stem) for f in finals}
        latest = drafts.latest_drafts(folder)
        detail["finals"] = [_draft(folder, f, True) for f in finals]
        detail["drafts"] = [_draft(folder, d, False) for d in latest if _kind(d.stem) not in final_kinds]
        detail["fact_requests"] = [ln.strip() for d in latest for ln in d.read_text(encoding="utf-8").splitlines()
                                   if ln.strip().upper().startswith("FACT REQUEST")]
    return detail


@app.get("/applications/{app_id}/drafts/{stem}")
def draft(app_id: int, stem: str) -> dict[str, Any]:
    folder = _folder(app_id)
    stem = _stem(stem)
    if not folder:
        raise HTTPException(status_code=404, detail="unknown application")
    for sub, approved in (("final", True), ("drafts", False)):
        path = folder / sub / f"{stem}.md"
        if path.exists():
            return _draft(folder, path, approved)
    raise HTTPException(status_code=404, detail="unknown draft")


@app.get("/tracker/summary")
def tracker_summary() -> dict[str, Any]:
    conn = _connect()
    try:
        by_status = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM applications GROUP BY status")}
        deadlines = conn.execute("SELECT COUNT(*) FROM applications WHERE deadline IS NOT NULL AND date(deadline) BETWEEN date('now') "
                                 "AND date('now', '+7 days') AND status IN ('draft','ready-for-review','approved')").fetchone()[0]
        assessments = conn.execute("SELECT COUNT(*) FROM assessments WHERE deadline IS NOT NULL AND date(deadline) BETWEEN date('now') "
                                   "AND date('now', '+7 days')").fetchone()[0]
        used = conn.execute("SELECT COUNT(*) FROM applications WHERE submitted_at >= date('now', '-7 days')").fetchone()[0]
        failures = conn.execute("SELECT COUNT(*) FROM source_health WHERE COALESCE(consecutive_failures, 0) > 0").fetchone()[0]
    finally:
        conn.close()
    return {"by_status": by_status, "deadlines_7d": deadlines, "assessments_due_7d": assessments,
            "weekly_cap": {"used": used, "cap": settings.Settings().cap("weekly_applications")}, "source_failures": failures}


@app.get("/assessments")
def assessments() -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT s.id, s.application_id, s.kind, s.vendor, s.deadline, s.deadline_confidence, s.status, c.name AS company, "
            "a.role_title AS role FROM assessments s LEFT JOIN applications a ON a.id = s.application_id "
            "LEFT JOIN companies c ON c.id = a.company_id ORDER BY s.deadline IS NULL, s.deadline").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


@app.get("/settings")
def settings_view() -> dict[str, Any]:
    """Read-only view of the autonomy rules; never webhook URLs, keys or anything from .env."""
    active = settings.Settings()
    autonomy = active.get("autonomy", default={}) or {}
    caps = {name: active.cap(name) for name in ("weekly_applications", "per_employer_per_cycle", "daily_emails", "daily_linkedin_actions")}
    return {"level": autonomy.get("level"), "overrides": autonomy.get("overrides", {}) or {},
            "recall_window_minutes": autonomy.get("recall_window_minutes"),
            "caps": {k: v for k, v in caps.items() if v is not None},
            "discord": bool(__import__("os").environ.get("DISCORD_WEBHOOK_URL")), "version": VERSION}


@app.get("/board")
def board() -> dict[str, Any]:
    from pipeline import queue

    tasks = queue.parse_tasks(queue.TASKS_PATH.read_text(encoding="utf-8"))
    return {"drivers": {d: {"go": [{"id": t["id"], "title": t["title"]} for t in queue.evaluate(tasks, d)["go"]]}
                        for d in ("claude-fable", "claude-opus", "codex-sol")},
            "waiting_on_ali": queue.waiting_on_ali(tasks), "board_text": queue.board(tasks)}


@app.get("/notifications")
def notifications(limit: int = 50) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT id, occurred_at, type, detail_json FROM events WHERE type = 'notification' "
                            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    return [{"id": r["id"], "at": r["occurred_at"], "detail": json.loads(r["detail_json"]) if r["detail_json"] else {}} for r in rows]


def _parked() -> list[dict[str, Any]]:
    """Applications whose latest submission event is a failure or a parked run."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT e.entity_id AS application_id, e.type, e.occurred_at, e.detail_json FROM events e "
            "WHERE e.entity = 'application' AND e.id = (SELECT MAX(id) FROM events x WHERE x.entity = 'application' "
            "AND x.entity_id = e.entity_id AND x.type IN ('submission_approved','submission_cancelled','submission_done','submission_failed'))"
        ).fetchall()
    finally:
        conn.close()
    return [{"application_id": r["application_id"], "at": r["occurred_at"], "detail": json.loads(r["detail_json"] or "{}")}
            for r in rows if r["type"] == "submission_failed"]


@app.get("/home")
def home() -> dict[str, Any]:
    apps = applications()
    to_review = [a for a in apps if a["status"] == "ready-for-review"]
    deadlines = [{"application_id": a["id"], "company": a["company"], "role": a["role"], "deadline": a["deadline"]}
                 for a in apps if a["deadline"] and a["status"] in ("draft", "ready-for-review", "approved")]
    horizon = date.today() + timedelta(days=7)
    due = [a for a in assessments() if a["deadline"] and a["status"] in ("invited", "scheduled")
           and date.fromisoformat(a["deadline"][:10]) <= horizon]
    return {"to_review": to_review, "deadlines": sorted(deadlines, key=lambda d: d["deadline"])[:10], "assessments_due": due,
            "parked": _parked(), "unread_notifications": len(notifications(20))}


# ---------------------------------------------------------------------------
# Writes (each one logged in events)
# ---------------------------------------------------------------------------

def _draft_path(app_id: int, stem: str) -> tuple[Path, Path]:
    folder = _folder(app_id)
    stem = _stem(stem)
    if not folder:
        raise HTTPException(status_code=404, detail="unknown application")
    path = folder / "drafts" / f"{stem}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail="unknown draft")
    return folder, path


def _run_verify(path: Path) -> dict[str, Any]:
    with contextlib.redirect_stdout(io.StringIO()):
        verify.main([str(path)])
    return _report(path.parent.parent, path.stem)


@app.post("/applications/{app_id}/drafts/{stem}/approve")
def approve_draft(app_id: int, stem: str) -> dict[str, Any]:
    folder, path = _draft_path(app_id, stem)
    report = _report(folder, path.stem)
    if report.get("hard_fails"):
        raise HTTPException(status_code=409, detail={"hard_fails": report["hard_fails"]})
    final = folder / "final"
    final.mkdir(exist_ok=True)
    for old in final.glob(f"{_kind(path.stem)}-r*.md"):   # one final per kind; earlier rounds stay in drafts/
        if old.name != path.name:
            old.unlink()
    shutil.copy2(path, final / path.name)
    _log(app_id, "draft_approved", {"stem": path.stem})
    status_change = None
    kinds_left = {_kind(d.stem) for d in drafts.latest_drafts(folder)} - {_kind(f.stem) for f in final.glob("*.md")}
    conn = _connect()
    try:
        status = conn.execute("SELECT status FROM applications WHERE id=?", (app_id,)).fetchone()["status"]
        if not kinds_left and status == "ready-for-review":
            db.transition_application(app_id, "approved", "ali-companion", {"via": "companion app"}, conn=conn)
            status_change = "approved"
    finally:
        conn.close()
    return {"approved": path.stem, "final": str(final / path.name), "status_change": status_change,
            "published": PUBLISH(folder)}


def edit_distance(old: str, new: str) -> tuple[int, int]:
    """(words changed, words in the original), word level."""
    a, b = old.split(), new.split()
    kept = sum(block.size for block in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_matching_blocks())
    return max(len(a), len(b)) - kept, len(a)


@app.post("/applications/{app_id}/drafts/{stem}/edit")
def edit_draft(app_id: int, stem: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="text is required")
    folder, path = _draft_path(app_id, stem)
    raw = path.read_text(encoding="utf-8")
    meta, old_body, _mapping, _problems = verify.split_draft(raw)
    citations = raw.split("\n## Citations", 1)[1] if "\n## Citations" in raw else ""
    round_no = int(meta.get("round") or 1) + 1
    meta.update({"round": round_no, "generated_by": "ali-edit"})
    if payload.get("note"):
        meta["note"] = str(payload["note"])[:300]
    new_stem = f"{_kind(path.stem)}-r{round_no}"
    new_path = folder / "drafts" / f"{new_stem}.md"
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    new_path.write_text(f"---\n{front}\n---\n{text}\n" + (f"\n## Citations{citations}" if citations else ""), encoding="utf-8")
    changed, total = edit_distance(old_body, text)
    reports = folder / "reports"
    reports.mkdir(exist_ok=True)
    (reports / f"edits-{new_stem}.md").write_text(
        f"# the candidate's edit: {path.stem} -> {new_stem} ({datetime.now(timezone.utc):%Y-%m-%d})\n\n"
        f"Edit distance: {changed} words changed of {total} ({(changed / total * 100 if total else 0):.0f}%).\n"
        + (f"\nNote: {payload['note']}\n" if payload.get("note") else "")
        + "\nClassify in /review: fact corrections go to brain/review-decisions, deleted phrases to personal_banned, "
          "tone changes to the style guide.\n", encoding="utf-8")
    result = _run_verify(new_path)
    _log(app_id, "draft_edited", {"from": path.stem, "to": new_stem, "words_changed": changed, "words": total})
    return {"stem": new_stem, "words_changed": changed, "words": total, "verify": result}


@app.post("/applications/{app_id}/drafts/{stem}/reject")
def reject_draft(app_id: int, stem: str, payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    folder, path = _draft_path(app_id, stem)
    reason = str((payload or {}).get("reason") or "no reason given")[:1000]
    reports = folder / "reports"
    reports.mkdir(exist_ok=True)
    (reports / f"rejected-{path.stem}.md").write_text(
        f"# Rejected by the candidate: {path.stem} ({datetime.now(timezone.utc):%Y-%m-%d})\n\n{reason}\n", encoding="utf-8")
    _log(app_id, "draft_rejected", {"stem": path.stem, "reason": reason})
    return {"rejected": path.stem}


@app.post("/applications/{app_id}/publish")
def publish(app_id: int) -> dict[str, Any]:
    folder = _folder(app_id)
    if not folder:
        raise HTTPException(status_code=404, detail="unknown application")
    result = PUBLISH(folder)
    _log(app_id, "published", {"via": "companion app"})
    return result


# ---------------------------------------------------------------------------
# Shortlist
# ---------------------------------------------------------------------------

ROW_RE = re.compile(r"^## (\d+)\. (.+?): (.+?) — .*\((\d+)/100\)\s*$")


def latest_shortlist() -> Path | None:
    files = sorted(p for p in config.DATA_DIR.glob("shortlist-*.md") if re.fullmatch(r"shortlist-\d{4}-\d{2}-\d{2}\.md", p.name))
    return files[-1] if files else None


@app.get("/shortlist/latest")
def shortlist_latest() -> list[dict[str, Any]]:
    path = latest_shortlist()
    if path is None:
        return []
    rows: list[dict[str, Any]] = []
    for block in re.split(r"\n(?=## \d+\. )", path.read_text(encoding="utf-8")):
        m = ROW_RE.match(block.splitlines()[0]) if block.startswith("## ") else None
        if not m:
            continue
        field = lambda name: (re.search(rf"^- {name}: (.+)$", block, re.M) or [None, None])[1]
        url = (re.search(r"\((https?://[^)]+)\)", field("Source") or "") or [None, None])[1]
        closes = field("Closing date")
        rows.append({"rank": int(m.group(1)), "company": m.group(2), "title": m.group(3), "score": int(m.group(4)),
                     "location": field("Location"), "closes_at": None if not closes or "not published" in closes.lower() else closes,
                     "url": url, "posting_id": None, "application_id": None})
    conn = _connect()
    try:
        for row in rows:
            if row["url"]:
                hit = conn.execute("SELECT id FROM postings WHERE url = ? OR canonical_url = ? ORDER BY id DESC LIMIT 1",
                                   (row["url"], row["url"])).fetchone()
                row["posting_id"] = hit["id"] if hit else None
            if row["posting_id"]:
                app_row = conn.execute("SELECT id FROM applications WHERE posting_id = ?", (row["posting_id"],)).fetchone()
                row["application_id"] = app_row["id"] if app_row else None
    finally:
        conn.close()
    return rows


def _posting_exists(posting_id: int) -> None:
    conn = _connect()
    try:
        if conn.execute("SELECT 1 FROM postings WHERE id=?", (posting_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="unknown posting")
    finally:
        conn.close()


@app.post("/shortlist/{posting_id}/approve")
def shortlist_approve(posting_id: int) -> dict[str, Any]:
    _posting_exists(posting_id)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = drafts.cmd_init(argparse.Namespace(application=None, posting=posting_id, mode=None, force=False, track=None))
    if code == 3:
        raise HTTPException(status_code=409, detail=out.getvalue().strip())
    if code != 0:
        raise HTTPException(status_code=500, detail=out.getvalue().strip())
    conn = _connect()
    try:
        app_id = conn.execute("SELECT id FROM applications WHERE posting_id=?", (posting_id,)).fetchone()["id"]
        conn.execute("UPDATE postings SET status='shortlisted', updated_at=datetime('now') WHERE id=?", (posting_id,))
        conn.commit()
        db.log_event("posting", posting_id, "shortlist_approved", {"application_id": app_id}, source="ali-companion", conn=conn)
    finally:
        conn.close()
    folder = _folder(app_id)
    return {"application_id": app_id, "folder": str(folder) if folder else None}


@app.post("/shortlist/{posting_id}/skip")
def shortlist_skip(posting_id: int) -> dict[str, Any]:
    _posting_exists(posting_id)
    conn = _connect()
    try:
        conn.execute("UPDATE postings SET status='ignored', updated_at=datetime('now') WHERE id=?", (posting_id,))
        conn.commit()
        db.log_event("posting", posting_id, "shortlist_skipped", {}, source="ali-companion", conn=conn)
    finally:
        conn.close()
    return {"skipped": posting_id}


if UI_DIR.exists():
    app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")
else:
    @app.get("/")
    def root() -> JSONResponse:
        return JSONResponse({"message": "CareerOS companion API", "docs": "/docs"})


def main() -> None:
    import uvicorn

    uvicorn.run("app.api.main:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()
