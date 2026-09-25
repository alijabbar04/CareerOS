"""T-013: desktop toast plus Discord webhook notifications, and the daily digest text.

    python -m pipeline.notify "title" "body" [--level info|action|urgent] [--channel digest|actions|approvals]
    python -m pipeline.notify --digest

`notify()` is meant to be called at the end of any pipeline step that the candidate should
hear about: it (a) shows a Windows toast via win11toast when this looks like an
interactive desktop session, failing silently otherwise (a Task Scheduler run with
nobody logged on, or win11toast/WinRT missing, must never crash the caller); (b)
posts a minimal embed to the Discord webhook in DISCORD_WEBHOOK_URL if that is set;
and (c) always writes an `events` row (type "notification") so the notification
itself is part of the audit trail even if both delivery channels failed.

`digest()` builds the text of the daily digest (plan/PLAN.md 4.5) straight from the
database: new matched postings since yesterday, applications by status, assessments
due within 7 days, overdue next actions, source failures, and this week's
weekly_applications cap usage. It is read-only and returns a string; callers (the
`digest` scheduled task, the Discord bot's `digest` command) decide what to do with it.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from pipeline import config, db, settings

LEVEL_COLOURS: dict[str, int] = {
    "info": 0x3B82F6,      # blue
    "action": 0xF59E0B,    # amber
    "urgent": 0xEF4444,    # red
}
VALID_LEVELS = tuple(LEVEL_COLOURS)
VALID_CHANNELS = ("digest", "actions", "approvals")


def _interactive_session() -> bool:
    """Best-effort check for a Windows interactive desktop session (the candidate's own
    terminal, or a Task Scheduler task set to 'run only when user is logged on'),
    as distinct from a non-interactive Session 0 service context where win11toast
    has no desktop to show a toast on. Not authoritative by itself -- _toast() also
    wraps the actual call in try/except, so a wrong guess here still fails silently.
    """
    session_name = os.environ.get("SESSIONNAME", "")
    return bool(session_name) and session_name.strip().upper() != "SERVICES"


def _toast(title: str, body: str) -> bool:
    """Show a Windows toast. Returns whether it was (probably) shown. Never raises:
    a missing win11toast/WinRT stack, a non-interactive session, or any other
    failure all just mean no toast, per the T-013 spec."""
    if not _interactive_session():
        return False
    try:
        from win11toast import notify as _win11_notify
    except ImportError:
        return False
    try:
        _win11_notify(title, body)
        return True
    except Exception:
        return False


def _discord_payload(title: str, body: str, level: str, channel: str) -> dict[str, Any]:
    """A minimal embed: title, body, a level colour and a timestamp. Nothing beyond
    what the caller passed in `title`/`body` -- the intended logical channel (digest/
    actions/approvals) is recorded in the footer since only one webhook exists today
    (DISCORD_WEBHOOK_URL), so every level currently lands in whichever single Discord
    channel that webhook was created for."""
    return {
        "embeds": [
            {
                "title": title,
                "description": body,
                "color": LEVEL_COLOURS.get(level, LEVEL_COLOURS["info"]),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {"text": f"CareerOS | {level} | #{channel}"},
            }
        ]
    }


def _post_discord(title: str, body: str, level: str, channel: str) -> bool:
    """POST the embed to DISCORD_WEBHOOK_URL if set. Returns whether it was accepted.
    Never raises: a missing webhook, a network error and a non-2xx response are all
    just "not delivered" here, matching notify()'s never-block-the-caller contract."""
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        return False
    try:
        response = requests.post(
            webhook_url, json=_discord_payload(title, body, level, channel), timeout=10
        )
        return response.ok
    except requests.RequestException:
        return False


def notify(
    title: str,
    body: str,
    level: str = "info",
    channel: str = "digest",
    conn: sqlite3.Connection | None = None,
) -> dict[str, bool]:
    """Show a toast, post to Discord, and always log an events row. Delivery failures
    never raise (see _toast/_post_discord); only a database error propagates. Returns
    {"toast_sent": bool, "discord_sent": bool}.
    """
    if level not in VALID_LEVELS:
        raise ValueError(f"level must be one of {VALID_LEVELS}, got {level!r}")
    if channel not in VALID_CHANNELS:
        raise ValueError(f"channel must be one of {VALID_CHANNELS}, got {channel!r}")

    config.load_env()  # idempotent: never overrides an already-set variable
    toast_sent = _toast(title, body)
    discord_sent = _post_discord(title, body, level, channel)

    # entity_id has no real referent for a standalone notification (entity_id is
    # polymorphic/paired with `entity`, not a foreign key -- see 001_initial.sql);
    # 0 is the documented sentinel for "no specific entity".
    db.log_event(
        entity="notification",
        entity_id=0,
        type="notification",
        detail={
            "title": title,
            "body": body,
            "level": level,
            "channel": channel,
            "toast_sent": toast_sent,
            "discord_sent": discord_sent,
        },
        source="system",
        conn=conn,
    )
    return {"toast_sent": toast_sent, "discord_sent": discord_sent}


# ---------------------------------------------------------------------------
# Daily digest
# ---------------------------------------------------------------------------

def _open(conn: sqlite3.Connection | None) -> tuple[sqlite3.Connection, bool]:
    """Return (connection, owns_it). Mirrors pipeline.db's own conn-or-open-one
    pattern without reaching into its private helper."""
    if conn is not None:
        return conn, False
    return db.connect(), True


def digest(conn: sqlite3.Connection | None = None) -> str:
    """Build the daily digest text straight from the database. Read-only."""
    c, owns_it = _open(conn)
    try:
        now = datetime.now(timezone.utc)
        week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d 00:00:00")

        new_postings = c.execute(
            """
            SELECT p.title, COALESCE(co.name, 'unknown company') AS company_name, p.track
            FROM postings p
            LEFT JOIN companies co ON co.id = p.company_id
            WHERE p.first_seen >= datetime('now', '-1 day') AND p.status NOT IN ('ignored', 'rejected')
            ORDER BY p.first_seen DESC
            """
        ).fetchall()

        by_status = c.execute(
            "SELECT status, COUNT(*) AS n FROM applications GROUP BY status ORDER BY status"
        ).fetchall()

        due_assessments = c.execute(
            """
            SELECT a.kind, a.deadline, ap.role_title, COALESCE(co.name, 'unknown company') AS company_name
            FROM assessments a
            JOIN applications ap ON ap.id = a.application_id
            LEFT JOIN companies co ON co.id = ap.company_id
            WHERE a.status IN ('invited', 'scheduled')
              AND a.deadline IS NOT NULL AND a.deadline <= datetime('now', '+7 days')
            ORDER BY a.deadline
            """
        ).fetchall()

        overdue = c.execute(
            """
            SELECT ap.role_title, COALESCE(co.name, 'unknown company') AS company_name,
                   ap.next_action, ap.next_action_due
            FROM applications ap
            LEFT JOIN companies co ON co.id = ap.company_id
            WHERE ap.next_action_due IS NOT NULL AND ap.next_action_due < datetime('now')
            ORDER BY ap.next_action_due
            """
        ).fetchall()

        failing_sources = c.execute(
            "SELECT source, consecutive_failures, last_ok FROM source_health "
            "WHERE consecutive_failures > 0 ORDER BY consecutive_failures DESC"
        ).fetchall()

        submitted_this_week = c.execute(
            "SELECT COUNT(*) AS n FROM applications WHERE submitted_at >= ?", (week_start,)
        ).fetchone()["n"]
    finally:
        if owns_it:
            c.close()

    weekly_cap = settings.Settings().cap("weekly_applications") or 25

    lines: list[str] = [f"CareerOS daily digest - {now.strftime('%Y-%m-%d %H:%M UTC')}", ""]

    lines.append(f"New matched postings (last 24h): {len(new_postings)}")
    for p in new_postings[:10]:
        lines.append(f"  - {p['title']} @ {p['company_name']} ({p['track'] or 'no track'})")
    if len(new_postings) > 10:
        lines.append(f"  ... and {len(new_postings) - 10} more")
    lines.append("")

    lines.append("Applications by status:")
    if by_status:
        for row in by_status:
            lines.append(f"  {row['status']}: {row['n']}")
    else:
        lines.append("  (none yet)")
    lines.append("")

    lines.append(f"Assessments due within 7 days: {len(due_assessments)}")
    for a in due_assessments[:10]:
        lines.append(f"  - {a['kind']} for {a['role_title']} @ {a['company_name']} due {a['deadline']}")
    lines.append("")

    lines.append(f"Overdue next actions: {len(overdue)}")
    for o in overdue[:10]:
        action = o["next_action"] or "no action set"
        lines.append(f"  - {o['role_title']} @ {o['company_name']}: {action} (was due {o['next_action_due']})")
    lines.append("")

    lines.append(f"Source failures: {len(failing_sources)}")
    for s in failing_sources:
        lines.append(
            f"  - {s['source']}: {s['consecutive_failures']} consecutive failures (last ok {s['last_ok'] or 'never'})"
        )
    lines.append("")

    lines.append(f"Cap usage this week: {submitted_this_week} / {weekly_cap} applications submitted")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m pipeline.notify", description=__doc__)
    parser.add_argument("title", nargs="?")
    parser.add_argument("body", nargs="?")
    parser.add_argument("--digest", action="store_true", help="build and send the current daily digest")
    parser.add_argument("--level", default="info", choices=VALID_LEVELS)
    parser.add_argument("--channel", default="digest", choices=VALID_CHANNELS)
    args = parser.parse_args(argv)

    config.ensure_dirs()
    if args.digest:
        if args.title is not None or args.body is not None:
            parser.error("--digest cannot be combined with title or body")
        title = "CareerOS daily digest"
        body = digest()
    else:
        if args.title is None or args.body is None:
            parser.error("title and body are required unless --digest is used")
        title, body = args.title, args.body
    result = notify(title, body, level=args.level, channel=args.channel)
    print(f"toast_sent={result['toast_sent']} discord_sent={result['discord_sent']}")


if __name__ == "__main__":
    main()
