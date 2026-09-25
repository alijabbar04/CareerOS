"""T-035 morning discovery, scoring and shortlist notification routine.

    python -m pipeline.morning
    python -m pipeline.morning --no-models
    python -m pipeline.morning --date 2026-09-23

The routine is the single entry point used by both the Claude Desktop scheduled
task and the Windows Task Scheduler fallback. It runs discovery before scoring,
uses the optional Ollama passes only when ``ollama ps`` reports no loaded model,
posts a compact top-ten shortlist plus the normal operational digest, and writes
one ``job_scan_run`` event with the outcome.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from pipeline import config, db, notify, scan, score

_HEADING_RE = re.compile(r"^##\s+(\d+)\.\s+(.+)$", re.MULTILINE)
_SOURCE_RE = re.compile(r"^- Source:\s+\[[^]]*]\(([^)]+)\)\s*$", re.MULTILINE)
_CLOSING_RE = re.compile(r"^- Closing date:\s+(.+)$", re.MULTILINE)
_DISCORD_BODY_LIMIT = 3_900
_SHORTLIST_SECTION_LIMIT = 2_800


def ollama_is_idle(
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    """Return true only when ``ollama ps`` succeeds and has no model rows.

    Ollama prints one header line even when nothing is loaded. Any error,
    timeout, missing executable or extra non-empty line is treated as busy so
    the morning scan always has a safe deterministic fallback.
    """
    try:
        result = runner(
            ["ollama", "ps"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return len(lines) <= 1


def _shortlist_rows(path: Path, limit: int = 10) -> list[tuple[int, str, str | None, str | None]]:
    """Read rank, heading, closing date and URL from a generated shortlist."""
    text = path.read_text(encoding="utf-8")
    matches = list(_HEADING_RE.finditer(text))
    rows: list[tuple[int, str, str | None, str | None]] = []
    for index, match in enumerate(matches[:limit]):
        block_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.end() : block_end]
        closing_match = _CLOSING_RE.search(block)
        source_match = _SOURCE_RE.search(block)
        rows.append(
            (
                int(match.group(1)),
                match.group(2).strip(),
                closing_match.group(1).strip() if closing_match else None,
                source_match.group(1).strip() if source_match else None,
            )
        )
    return rows


def format_shortlist(path: Path, *, limit: int = 10, max_chars: int = _SHORTLIST_SECTION_LIMIT) -> tuple[str, int]:
    """Format the first shortlist rows for one bounded Discord embed section."""
    rows = _shortlist_rows(path, limit=limit)
    if not rows:
        return "Top shortlist: no postings currently meet the threshold.", 0

    lines = [f"Top {len(rows)} shortlist:"]
    for rank, heading, closing, url in rows:
        details: list[str] = []
        if closing and closing.casefold() != "not published":
            details.append(f"closes {closing}")
        if url:
            details.append(f"[open role]({url})")
        suffix = f" — {' · '.join(details)}" if details else ""
        line = f"{rank}. **{heading}**{suffix}"
        # One pathological advert URL/title must not crowd out the other nine.
        lines.append(line if len(line) <= 260 else line[:257] + "...")

    body = "\n".join(lines)
    if len(body) > max_chars:
        body = body[: max_chars - 3].rstrip() + "..."
    return body, len(rows)


def _record_run(event_logger: Callable[..., int], detail: dict[str, Any]) -> None:
    event_logger(
        entity="routine",
        entity_id=0,
        type="job_scan_run",
        detail=detail,
        source="system",
    )


def run_morning(
    *,
    run_date: date | None = None,
    allow_models: bool = True,
    scan_main: Callable[[list[str] | None], None] = scan.main,
    score_main: Callable[[list[str] | None], None] = score.main,
    idle_check: Callable[[], bool] = ollama_is_idle,
    digest_builder: Callable[[], str] = notify.digest,
    notifier: Callable[..., dict[str, bool]] = notify.notify,
    event_logger: Callable[..., int] = db.log_event,
) -> dict[str, Any]:
    """Run the morning workflow and return its small machine-readable outcome."""
    config.ensure_dirs()
    day = run_date or date.today()

    if config.PAUSED_FLAG.exists():
        delivery = notifier(
            "CareerOS scan skipped",
            "The morning job scan is paused.",
            level="info",
            channel="digest",
        )
        detail = {"status": "paused", "date": day.isoformat(), **delivery}
        _record_run(event_logger, detail)
        return detail

    try:
        scan_main([])
    except Exception as exc:  # noqa: BLE001 -- scheduled run must notify and stop cleanly
        delivery = notifier(
            "CareerOS scan failed",
            f"Discovery stopped with {type(exc).__name__}. Check the local job-scan log.",
            level="urgent",
            channel="digest",
        )
        detail = {"status": "scan_failed", "date": day.isoformat(), "error_type": type(exc).__name__, **delivery}
        _record_run(event_logger, detail)
        return detail

    use_models = bool(allow_models and idle_check())
    score_args = ["--date", day.isoformat()]
    if use_models:
        score_args.append("--models")
    try:
        score_main(score_args)
    except Exception as exc:  # noqa: BLE001 -- scheduled run must notify and stop cleanly
        delivery = notifier(
            "CareerOS scoring failed",
            f"Scoring stopped with {type(exc).__name__}. Check the local job-scan log.",
            level="urgent",
            channel="digest",
        )
        detail = {
            "status": "score_failed",
            "date": day.isoformat(),
            "models_used": use_models,
            "error_type": type(exc).__name__,
            **delivery,
        }
        _record_run(event_logger, detail)
        return detail

    shortlist_path = config.DATA_DIR / f"shortlist-{day.isoformat()}.md"
    try:
        shortlist_body, top_count = format_shortlist(shortlist_path)
    except (OSError, UnicodeError) as exc:
        delivery = notifier(
            "CareerOS shortlist failed",
            f"Scoring finished but the shortlist could not be read ({type(exc).__name__}). Check the local job-scan log.",
            level="urgent",
            channel="digest",
        )
        detail = {
            "status": "shortlist_failed",
            "date": day.isoformat(),
            "models_used": use_models,
            "error_type": type(exc).__name__,
            **delivery,
        }
        _record_run(event_logger, detail)
        return detail

    operational = digest_builder()
    body = f"{shortlist_body}\n\n{operational}"
    if len(body) > _DISCORD_BODY_LIMIT:
        body = body[: _DISCORD_BODY_LIMIT - 3].rstrip() + "..."
    delivery = notifier(
        f"CareerOS morning shortlist — {day.isoformat()}",
        body,
        level="info",
        channel="digest",
    )
    detail = {
        "status": "completed",
        "date": day.isoformat(),
        "models_used": use_models,
        "top_count": top_count,
        "shortlist_path": shortlist_path.relative_to(config.REPO_ROOT).as_posix(),
        **delivery,
    }
    _record_run(event_logger, detail)
    return detail


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m pipeline.morning", description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--no-models", action="store_true", help="skip Ollama even if no model is loaded")
    args = parser.parse_args(argv)
    result = run_morning(run_date=args.date, allow_models=not args.no_models)
    print(f"morning: {result}")
    if result["status"] in {"scan_failed", "score_failed", "shortlist_failed"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
