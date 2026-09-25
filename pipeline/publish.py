"""T-043: publish application material where the candidate can read it, on the laptop and on his phone.

    python -m pipeline.publish --application 55      # one application
    python -m pipeline.publish --all                 # every application folder under brain/vault/applications

Two targets, both outside the repository:
- Documents mirror (`%USERPROFILE%\\Documents\\CareerOS\\Applications\\<NNNN Firm - Role>\\`): everything
  generated for the application (brief, drafts, reports, final, prep packs), as Markdown plus a .docx
  for every prose file, so the whole history is browsable in Explorer.
- OneDrive folder (`%USERPROFILE%\\OneDrive\\Documents\\CareerOS\\<NNNN Firm - Role>\\`): only what the candidate needs
  on his phone: the approved final texts, the latest round of each draft, the outline or prep packs,
  and a one-page `README.md` per application with role, deadline, status and what he must do. Each
  .docx strips the front matter and the citation mapping and ends with a small provenance footer.
  A root `INDEX.md` lists every application with its deadline and status.

Override the targets with CAREEROS_PUBLISH_LOCAL and CAREEROS_PUBLISH_ONEDRIVE (tests do).
Nothing here touches the database except to read application status and deadline.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline import config, db
from pipeline.drafts import APPLICATIONS_DIR, latest_drafts, read_front_matter

LOCAL_ROOT = Path(os.environ.get("CAREEROS_PUBLISH_LOCAL") or Path.home() / "Documents" / "CareerOS")
ONEDRIVE_ROOT = Path(os.environ.get("CAREEROS_PUBLISH_ONEDRIVE") or Path.home() / "OneDrive" / "Documents" / "CareerOS")
PROSE_KINDS = ("drafts", "final")


def _docx_available() -> bool:
    try:
        import docx  # noqa: F401
    except ImportError:
        return False
    return True


def prose_body(text: str) -> tuple[dict[str, Any], str]:
    """Front matter dict and the prose without the citation mapping or FACT REQUEST lines."""
    meta: dict[str, Any] = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) > 2:
            import yaml
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                meta = {}
            body = parts[2]
    body = body.split("\n## Citations", 1)[0]
    body = "\n".join(ln for ln in body.splitlines() if not ln.strip().upper().startswith("FACT REQUEST"))
    return (meta if isinstance(meta, dict) else {}), body.strip()


def write_docx(md_path: Path, out_path: Path, title: str, footer: str) -> bool:
    if not _docx_available():
        return False
    from docx import Document
    from docx.shared import Pt

    meta, body = prose_body(md_path.read_text(encoding="utf-8"))
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    doc.add_heading(title, level=1)
    question = meta.get("question")
    if question:
        doc.add_paragraph(str(question)).runs[0].italic = True
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if not para:
            continue
        if para.startswith("#"):
            doc.add_heading(para.lstrip("# ").strip(), level=2)
        elif re.match(r"^\s*[-*]\s", para):
            for ln in para.splitlines():
                doc.add_paragraph(re.sub(r"^\s*[-*]\s", "", ln), style="List Bullet")
        else:
            lines = [" ".join(ln.split()) for ln in para.splitlines() if ln.strip()]
            if len(lines) > 1 and re.match(r"^(Yours (faithfully|sincerely)|Kind regards|Best regards),?$", lines[0]):
                sp = doc.add_paragraph(lines[0])  # sign-off: the name goes on its own line (the candidate, 2026-09-24)
                for ln in lines[1:]:
                    sp.add_run().add_break(); sp.add_run(ln)
            else:
                doc.add_paragraph(" ".join(lines))
    p = doc.add_paragraph()
    run = p.add_run(footer)
    run.font.size = Pt(8)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return True


def _app_row(app_id: int) -> sqlite3.Row | None:
    try:
        conn = db.connect()
    except RuntimeError:
        return None
    try:
        return conn.execute(
            "SELECT a.id, a.role_title, a.status, a.deadline, a.drafting_mode, c.name AS company FROM applications a JOIN companies c ON c.id = a.company_id WHERE a.id = ?",
            (app_id,),
        ).fetchone()
    finally:
        conn.close()


def _folder_label(folder: Path, row: sqlite3.Row | None) -> str:
    if row is not None:
        role = re.sub(r"[^\w\s&()-]", "", str(row["role_title"])).strip()[:60]
        return f"{int(row['id']):04d} {row['company']} - {role}"
    return folder.name


def _copy_md_and_docx(src: Path, dest_dir: Path, title: str, footer: str) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest_dir / src.name)
    write_docx(src, dest_dir / (src.stem + ".docx"), title, footer)


def publish_application(folder: Path, local_root: Path | None = None, onedrive_root: Path | None = None) -> dict[str, Any]:
    local_root = local_root or LOCAL_ROOT
    onedrive_root = onedrive_root or ONEDRIVE_ROOT
    m = re.match(r"(\d{4})-", folder.name)
    app_id = int(m.group(1)) if m else None
    row = _app_row(app_id) if app_id else None
    label = _folder_label(folder, row)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    footer = f"CareerOS, application {app_id or '?'}: every factual sentence traces to a confirmed claim in the candidate's ledger. Published {stamp}."

    # Documents mirror: everything, Markdown copied as is, .docx beside prose files
    local_dir = local_root / "Applications" / label
    copied = 0
    for sub in ("", "drafts", "final", "reports", "calibration"):
        src_dir = folder / sub if sub else folder
        if not src_dir.exists():
            continue
        for src in sorted(src_dir.rglob("*") if sub == "calibration" else src_dir.glob("*")):
            if src.is_dir():
                continue
            rel = src.relative_to(folder)
            dest = local_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            copied += 1
            if src.suffix == ".md" and rel.parts[0] in PROSE_KINDS:
                write_docx(src, dest.with_suffix(".docx"), f"{label}: {src.stem}", footer)

    # OneDrive: finals, latest drafts, packs, README
    od_dir = onedrive_root / label
    od_dir.mkdir(parents=True, exist_ok=True)
    finals = sorted((folder / "final").glob("*.md")) if (folder / "final").exists() else []
    for src in finals:
        _copy_md_and_docx(src, od_dir / "final", f"{label}: {src.stem} (approved)", footer)
    for src in latest_drafts(folder):
        if not any(f.stem.split("-r")[0] == src.stem.split("-r")[0] for f in finals):
            _copy_md_and_docx(src, od_dir / "drafts", f"{label}: {src.stem} (draft)", footer)
    for src in sorted(folder.glob("prep-*.md")):
        _copy_md_and_docx(src, od_dir, f"{label}: {src.stem}", footer)
    readme = [f"# {label}", ""]
    if row is not None:
        readme += [f"- Status: {row['status']}", f"- Deadline: {row['deadline'] or 'not published'}", f"- Drafting mode: {row['drafting_mode']}"]
    brief = folder / "brief.md"
    if brief.exists():
        meta, _ = read_front_matter(brief)
        if meta.get("mode") == "proofread_only":
            readme += ["", "This employer allows research and proofreading only: the candidate writes the answer from the outline pack in `drafts`, then CareerOS fact-checks and proofreads it."]
    fact_requests = []
    for src in latest_drafts(folder):
        for ln in src.read_text(encoding="utf-8").splitlines():
            if ln.strip().upper().startswith("FACT REQUEST"):
                fact_requests.append(f"- {src.stem}: {ln.strip()[:200]}")
    readme += ["", "## Files", "- `final/`: approved texts (.docx and .md)", "- `drafts/`: latest round of anything not yet approved", "- `prep-*`: interview and assessment packs"]
    if fact_requests:
        readme += ["", "## the candidate must answer", *fact_requests]
    readme += ["", f"Published {stamp}."]
    (od_dir / "README.md").write_text("\n".join(readme), encoding="utf-8")
    return {"application": app_id, "label": label, "local": str(local_dir), "onedrive": str(od_dir), "files_mirrored": copied, "finals": len(finals)}


def write_index(results: list[dict[str, Any]], onedrive_root: Path | None = None) -> Path:
    onedrive_root = onedrive_root or ONEDRIVE_ROOT
    onedrive_root.mkdir(parents=True, exist_ok=True)
    lines = ["# CareerOS applications", "", f"Updated {datetime.now().strftime('%Y-%m-%d %H:%M')}. One folder per application; `final/` holds approved text.", ""]
    lines += ["| Application | Status | Deadline | Approved texts |", "|---|---|---|---|"]
    for r in results:
        row = _app_row(r["application"]) if r["application"] else None
        status = row["status"] if row is not None else "?"
        deadline = (row["deadline"] or "not published")[:10] if row is not None else "?"
        lines.append(f"| {r['label']} | {status} | {deadline} | {r['finals']} |")
    path = onedrive_root / "INDEX.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.publish", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--application", type=int)
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)
    if not APPLICATIONS_DIR.exists():
        print("no applications folder")
        return 2
    folders = sorted(p for p in APPLICATIONS_DIR.iterdir() if p.is_dir() and re.match(r"\d{4}-", p.name))
    if args.application:
        folders = [p for p in folders if p.name.startswith(f"{args.application:04d}-")]
    if not folders:
        print("nothing to publish")
        return 2
    results = [publish_application(f) for f in folders]
    for r in results:
        print(f"{r['label']}: {r['files_mirrored']} files mirrored to {r['local']}; {r['finals']} approved texts to {r['onedrive']}")
    if args.all or len(results) > 1:
        print(f"index: {write_index(results)}")
    else:
        all_folders = sorted(p for p in APPLICATIONS_DIR.iterdir() if p.is_dir() and re.match(r"\d{4}-", p.name))
        write_index([{"application": int(p.name[:4]), "label": _folder_label(p, _app_row(int(p.name[:4]))), "finals": len(list((p / 'final').glob('*.md'))) if (p / 'final').exists() else 0} for p in all_folders])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
