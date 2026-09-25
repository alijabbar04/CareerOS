"""T-022 (first part): CareerOS submits an application itself once the candidate has approved it.

    python -m pipeline.submit approve 55                        # the candidate at his own terminal
    python -m pipeline.submit approve 55 --ali-said "<his words>" # a driver, when the candidate approved in conversation
    python -m pipeline.submit cancel 55    # within the recall window (anyone may cancel; cancelling is always safe)
    python -m pipeline.submit status 55
    python -m pipeline.submit run 55       # run by the scheduled task when the recall window ends

the candidate decides what is submitted (2026-09-24: "I check your work and when I approve it you submit"). He approves either
at his terminal (id typed back) or in conversation, in which case the driver records his words verbatim with
`--ali-said`; a driver never approves without an explicit approval from the candidate for that application. Approval runs a dry
run on the live form (nothing sent), records a
SHA-256 of every file the submission will use, and schedules the submission for when the recall window
(`recall_window_minutes`, default 15) ends. `run` refuses unless the latest approval is still live, the window has
passed, the content is byte-for-byte what the candidate approved, settings allow it (`submit_applications` not `never`, level at
least 1, not paused, weekly cap not reached) and the application is still `approved`. It presses Submit at most once:
any outcome other than a confirmed submission ends the approval and tells the candidate, so nothing is ever sent twice.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from pipeline import config, consistency, db, drafts, notify, settings
from pipeline.recipes import pinpoint

APPROVAL_EVENTS = ("submission_approved", "submission_cancelled", "submission_done", "submission_failed")
TASK_NAME = "CareerOS submit application {app_id}"
PYTHON = r"C:\Users\<user>\AppData\Local\Programs\Python\Python313\python.exe"


class SubmitRefused(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _folder(app_id: int) -> Path:
    folder = drafts.find_folder(app_id)
    if folder is None:
        raise SubmitRefused(f"application {app_id} has no folder")
    return folder


def content_files(folder: Path) -> list[Path]:
    """form.yaml plus every text file and upload it points to."""
    form = pinpoint.load_form(folder)
    files = [folder / "form.yaml"]
    specs = [((form.get("profile") or {}).get("summary") or {})] + [s or {} for s in (form.get("answers") or {}).values()]
    for spec in specs:
        if spec.get("file"):
            path = Path(spec["file"])
            files.append(path if path.is_absolute() else folder / path)
    return files


def content_hash(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in content_files(folder):
        digest.update(str(path.relative_to(folder) if path.is_relative_to(folder) else path.name).encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def latest_event(conn: sqlite3.Connection, app_id: int) -> sqlite3.Row | None:
    marks = ",".join("?" for _ in APPROVAL_EVENTS)
    return conn.execute(
        f"SELECT * FROM events WHERE entity='application' AND entity_id=? AND type IN ({marks}) ORDER BY id DESC LIMIT 1",
        (app_id, *APPROVAL_EVENTS),
    ).fetchone()


def _status(conn: sqlite3.Connection, app_id: int) -> str:
    row = conn.execute("SELECT status FROM applications WHERE id=?", (app_id,)).fetchone()
    if row is None:
        raise SubmitRefused(f"no application {app_id} in the tracker")
    return row["status"]


def schedule_task(app_id: int, when: datetime) -> None:
    local = when.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    script = (
        f"$a = New-ScheduledTaskAction -Execute '{PYTHON}' -Argument '-m pipeline.submit run {app_id}' "
        f"-WorkingDirectory '{config.REPO_ROOT}'; "
        f"$t = New-ScheduledTaskTrigger -Once -At ([datetime]'{local}'); "
        "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; "
        f"Register-ScheduledTask -TaskName '{TASK_NAME.format(app_id=app_id)}' -Action $a -Trigger $t -Settings $s -Force | Out-Null"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True, timeout=60)


def unschedule_task(app_id: int) -> None:
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"Unregister-ScheduledTask -TaskName '{TASK_NAME.format(app_id=app_id)}' -Confirm:$false -ErrorAction SilentlyContinue"],
                   capture_output=True, timeout=60)


def approve(app_id: int, *, conn: sqlite3.Connection, source: str = "ali-terminal", now: datetime | None = None,
            dry_run: Callable[[int], pinpoint.Result] = pinpoint.dry_run,
            schedule: Callable[[int, datetime], None] = schedule_task,
            active: settings.Settings | None = None, ali_said: str | None = None,
            note: str | None = None,
            consistency_check: Callable[..., list[consistency.Flag]] = consistency.check_document) -> datetime:
    """Record the candidate's approval of exactly the current content and schedule the submission.

    `ali_said` is the candidate's own approving words when he approves in conversation with a driver (source `ali-chat`); they are
    stored verbatim in the approval event so the record shows where the authority came from."""
    active = active or settings.Settings()
    now = now or _now()
    if active.override("submit_applications") == "never":
        raise SubmitRefused("settings say submit_applications: never")
    status = _status(conn, app_id)
    if status not in ("ready-for-review", "approved"):
        raise SubmitRefused(f"application {app_id} is '{status}'; only ready-for-review or approved can be submitted")
    folder = _folder(app_id)
    contradictions = [f for path in content_files(folder) if path.suffix == ".md" and path.parent.name == "final"
                      for f in consistency_check(conn, path, app_id) if f.kind != "drift"]
    if contradictions:
        raise SubmitRefused("this contradicts what the employer already has: " + "; ".join(f.message for f in contradictions))
    result = dry_run(app_id)
    if result.failed or result.missing:
        raise SubmitRefused("the dry run did not fill everything: " + ", ".join(result.failed + result.missing))
    digest = content_hash(folder)
    if status == "ready-for-review":
        db.transition_application(app_id, "approved", source, {"content_sha256": digest}, conn=conn)
    window = int(active.get("autonomy", "recall_window_minutes", default=None)
                 or active.get("recall_window_minutes", default=None) or 15)
    submit_after = now + timedelta(minutes=window)
    detail = {"content_sha256": digest, "submit_after": submit_after.isoformat(), "fields": result.filled}
    if ali_said:
        detail["ali_said"] = ali_said[:500]
    if note:
        detail["note"] = note[:300]
    event_id = db.log_event("application", app_id, "submission_approved", detail, source=source, conn=conn)
    try:
        schedule(app_id, submit_after)
    except Exception as exc:
        db.log_event("application", app_id, "submission_cancelled",
                     {"approval_event": event_id, "reason": "could not schedule the submission"}, source="system", conn=conn)
        raise SubmitRefused(f"the submission could not be scheduled ({type(exc).__name__}); nothing will be sent") from None
    return submit_after


def cancel(app_id: int, *, conn: sqlite3.Connection, source: str = "ali",
           unschedule: Callable[[int], None] = unschedule_task) -> bool:
    event = latest_event(conn, app_id)
    unschedule(app_id)
    if event is None or event["type"] != "submission_approved":
        return False
    db.log_event("application", app_id, "submission_cancelled", {"approval_event": event["id"]}, source=source, conn=conn)
    return True


def _weekly_count(conn: sqlite3.Connection, now: datetime) -> int:
    since = (now - timedelta(days=7)).isoformat()
    return conn.execute("SELECT COUNT(*) FROM applications WHERE submitted_at IS NOT NULL AND submitted_at >= ?",
                        (since,)).fetchone()[0]


def run(app_id: int, *, conn: sqlite3.Connection, now: datetime | None = None,
        submitter: Callable[[int], pinpoint.Submission] = pinpoint.submit_application,
        notifier: Callable[..., Any] = notify.notify,
        unschedule: Callable[[int], None] = unschedule_task,
        active: settings.Settings | None = None) -> str:
    """Submit if, and only if, every condition still holds. Returns the outcome."""
    active = active or settings.Settings()
    now = now or _now()
    event = latest_event(conn, app_id)
    if event is None or event["type"] != "submission_approved":
        raise SubmitRefused("there is no live approval for this application")
    detail = json.loads(event["detail_json"] or "{}")
    submit_after = datetime.fromisoformat(detail["submit_after"])
    if now < submit_after:
        raise SubmitRefused(f"the recall window is open until {submit_after.astimezone():%H:%M}")
    if _status(conn, app_id) != "approved":
        raise SubmitRefused("the application is no longer 'approved'")
    if content_hash(_folder(app_id)) != detail["content_sha256"]:
        raise SubmitRefused("the content changed after the candidate approved it; approve again")
    if active.override("submit_applications") == "never":
        raise SubmitRefused("settings say submit_applications: never")
    if int(active.get("autonomy", "level", default=1) or 0) < 1:
        raise SubmitRefused("autonomy level 0 (observe)")
    if config.PAUSED_FLAG.exists():
        raise SubmitRefused("CareerOS is paused")
    cap = active.cap("weekly_applications")
    if cap is not None and _weekly_count(conn, now) >= cap:
        raise SubmitRefused(f"the weekly cap of {cap} submissions is reached")

    role = conn.execute("SELECT a.role_title, c.name FROM applications a JOIN companies c ON c.id = a.company_id "
                        "WHERE a.id=?", (app_id,)).fetchone()
    label = f"{role['name']}: {role['role_title']}" if role else f"application {app_id}"
    try:
        sub = submitter(app_id)
    except pinpoint.Parked as exc:
        db.log_event("application", app_id, "submission_failed", {"outcome": "parked", "reason": str(exc)},
                     source="system-with-approval", conn=conn)
        notifier(f"Parked: {label}", f"{exc}. Nothing was submitted.", level="urgent", channel="actions")
        unschedule(app_id)
        return "parked"
    except Exception as exc:  # the submit press may or may not have happened; never retry automatically
        db.log_event("application", app_id, "submission_failed",
                     {"outcome": "error", "reason": f"{type(exc).__name__}: {str(exc)[:200]}"},
                     source="system-with-approval", conn=conn)
        notifier(f"Submission problem: {label}",
                 "Something went wrong during the submission. Check the confirmation email before trying again.",
                 level="urgent", channel="actions")
        unschedule(app_id)
        return "error"

    if sub.outcome == "submitted":
        db.transition_application(app_id, "submitted", "system-with-approval",
                                  {"url": sub.url, "screenshot": Path(sub.screenshot).name}, conn=conn)
        conn.execute("UPDATE applications SET submitted_by='system-with-approval', submitted_at=? WHERE id=?",
                     (now.isoformat(), app_id))
        conn.commit()
        db.log_event("application", app_id, "submission_done",
                     {"url": sub.url, "screenshot": Path(sub.screenshot).name}, source="system-with-approval", conn=conn)
        notifier(f"Submitted: {label}", "CareerOS submitted your approved application. A confirmation email should follow.",
                 level="action", channel="actions")
    else:
        db.log_event("application", app_id, "submission_failed",
                     {"outcome": sub.outcome, "errors": sub.errors, "url": sub.url,
                      "screenshot": Path(sub.screenshot).name}, source="system-with-approval", conn=conn)
        notifier(f"Check submission: {label}",
                 f"Submit was pressed but the result is '{sub.outcome}'. Check the screenshot "
                 f"{Path(sub.screenshot).name} and your email before doing anything else.",
                 level="urgent", channel="actions")
    unschedule(app_id)
    return sub.outcome


def _summary(conn: sqlite3.Connection, app_id: int) -> str:
    row = conn.execute("SELECT a.role_title, a.deadline, c.name FROM applications a "
                       "JOIN companies c ON c.id = a.company_id WHERE a.id=?", (app_id,)).fetchone()
    files = [p.name for p in content_files(_folder(app_id))[1:]]
    return (f"Application {app_id}: {row['name']}, {row['role_title']} (deadline {row['deadline']})\n"
            f"Content: {', '.join(files)}")


def main(argv: list[str] | None = None, *, stdin_isatty: Callable[[], bool] = lambda: sys.stdin.isatty()) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.submit", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["approve", "cancel", "status", "run"])
    parser.add_argument("application", type=int)
    parser.add_argument("--ali-said", help="the candidate's approving words, verbatim, when he approved in conversation with a driver")
    parser.add_argument("--note", help="why this approval was recorded, for example a re-approval after a failed attempt")
    args = parser.parse_args(argv)
    conn = db.connect()
    try:
        if args.command == "approve":
            print(_summary(conn, args.application))
            if args.ali_said:
                source = "ali-chat"
            elif stdin_isatty():
                source = "ali-terminal"
                typed = input(f"Type {args.application} to approve this exact content for submission: ").strip()
                if typed != str(args.application):
                    print("Not approved; nothing changed.")
                    return 3
            else:
                print("error: approval needs the candidate, either at his terminal or quoted with --ali-said")
                return 3
            print("Running a dry run on the live form first (nothing is sent)...")
            when = approve(args.application, conn=conn, source=source, ali_said=args.ali_said, note=args.note)
            print(f"Approved. CareerOS will submit at {when.astimezone():%H:%M} unless you run "
                  f"`python -m pipeline.submit cancel {args.application}` before then.")
        elif args.command == "cancel":
            print("Cancelled; nothing will be submitted." if cancel(args.application, conn=conn)
                  else "There was no pending submission.")
        elif args.command == "status":
            event = latest_event(conn, args.application)
            print(f"status: {_status(conn, args.application)}; last submission event: "
                  + (f"{event['type']} at {event['occurred_at']} {event['detail_json'] or ''}" if event else "none"))
        else:
            print(f"outcome: {run(args.application, conn=conn)}")
        return 0
    except SubmitRefused as exc:
        print(f"refused: {exc}")
        if args.command == "run":
            notify.notify(f"Submission not sent: application {args.application}", str(exc), level="action", channel="actions")
        return 3
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
