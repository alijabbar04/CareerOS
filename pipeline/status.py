"""T-016: a plain-text pipeline status report, for the terminal or the Discord bot's
`status` command.

    python -m pipeline.status

Prints, in under 60 lines and with no colour codes: applications by status, next
actions due (overdue first), assessments due in the next 7 days, matched postings not
yet actioned (top 10 by priority), source health failures, this week's cap usage, the
autonomy level and driver from settings, and the pause flag. Read-only -- assumes the
database is already migrated (run `python -m pipeline.db migrate` first if not).
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone

from pipeline import config, db, settings

# Hard ceiling on printed lines (the spec asks for "under 60"); each list section is
# also capped on its own below, so this is a last-resort safety net, not the main plan.
MAX_LINES = 58


def _open(conn: sqlite3.Connection | None) -> tuple[sqlite3.Connection, bool]:
    if conn is not None:
        return conn, False
    return db.connect(), True


def build_status_text(conn: sqlite3.Connection | None = None) -> str:
    """Build the status report as a single string. Read-only."""
    c, owns_it = _open(conn)
    try:
        now = datetime.now(timezone.utc)
        week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d 00:00:00")

        by_status = c.execute(
            "SELECT status, COUNT(*) AS n FROM applications GROUP BY status ORDER BY status"
        ).fetchall()

        # "Overdue first" falls out of the ascending sort: a past due date is always
        # less than now, so overdue rows come before upcoming ones.
        next_actions = c.execute(
            """
            SELECT ap.id, ap.role_title, COALESCE(co.name, 'unknown company') AS company_name,
                   ap.next_action, ap.next_action_due
            FROM applications ap
            LEFT JOIN companies co ON co.id = ap.company_id
            WHERE ap.next_action_due IS NOT NULL
            ORDER BY ap.next_action_due ASC
            LIMIT 5
            """
        ).fetchall()
        next_actions_total = c.execute(
            "SELECT COUNT(*) AS n FROM applications WHERE next_action_due IS NOT NULL"
        ).fetchone()["n"]

        due_assessments = c.execute(
            """
            SELECT a.kind, a.deadline, ap.role_title, COALESCE(co.name, 'unknown company') AS company_name
            FROM assessments a
            JOIN applications ap ON ap.id = a.application_id
            LEFT JOIN companies co ON co.id = ap.company_id
            WHERE a.status IN ('invited', 'scheduled')
              AND a.deadline IS NOT NULL AND a.deadline <= datetime('now', '+7 days')
            ORDER BY a.deadline
            LIMIT 5
            """
        ).fetchall()

        # "Priority" for a posting = its company's priority band (1 highest, per
        # data/registry.yaml), broken by the best available current fit score.
        top_postings = c.execute(
            """
            SELECT p.title, COALESCE(co.name, 'unknown company') AS company_name,
                   COALESCE(co.priority, 99) AS company_priority,
                   CASE
                     WHEN p.model_fingerprint = p.fingerprint AND p.llm_score IS NOT NULL AND p.local_model_score IS NOT NULL
                       THEN ROUND(COALESCE(p.rule_score, 0) * 0.35 + p.local_model_score * 0.20 + p.llm_score * 0.45)
                     WHEN p.model_fingerprint = p.fingerprint AND p.llm_score IS NOT NULL
                       THEN ROUND(COALESCE(p.rule_score, 0) * 0.45 + p.llm_score * 0.55)
                     WHEN p.model_fingerprint = p.fingerprint AND p.local_model_score IS NOT NULL
                       THEN ROUND(COALESCE(p.rule_score, 0) * 0.70 + p.local_model_score * 0.30)
                     ELSE COALESCE(p.rule_score, 0)
                   END AS score
            FROM postings p
            LEFT JOIN companies co ON co.id = p.company_id
            WHERE p.status = 'shortlisted'
              AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.posting_id = p.id)
            ORDER BY company_priority ASC, score DESC
            LIMIT 10
            """
        ).fetchall()

        failing_sources = c.execute(
            "SELECT source, consecutive_failures FROM source_health "
            "WHERE consecutive_failures > 0 ORDER BY consecutive_failures DESC LIMIT 8"
        ).fetchall()

        submitted_this_week = c.execute(
            "SELECT COUNT(*) AS n FROM applications WHERE submitted_at >= ?", (week_start,)
        ).fetchone()["n"]
    finally:
        if owns_it:
            c.close()

    cfg = settings.Settings()
    weekly_cap = cfg.cap("weekly_applications") or 25
    paused = config.PAUSED_FLAG.exists()

    lines: list[str] = [f"CareerOS status - {now.strftime('%Y-%m-%d %H:%M UTC')}"]
    lines.append(
        f"driver: {cfg.driver()} | autonomy level: {cfg.autonomy_level()} | "
        f"cap this week: {submitted_this_week}/{weekly_cap} | paused: {'YES' if paused else 'no'}"
    )
    lines.append("")

    lines.append("Applications by status:")
    if by_status:
        for row in by_status:
            lines.append(f"  {row['status']}: {row['n']}")
    else:
        lines.append("  (none yet)")
    lines.append("")

    lines.append(f"Next actions due (overdue first) [{next_actions_total} total]:")
    if next_actions:
        for a in next_actions:
            overdue = " OVERDUE" if a["next_action_due"] < now.strftime("%Y-%m-%d %H:%M:%S") else ""
            action = a["next_action"] or "no action set"
            lines.append(f"  - #{a['id']} {a['role_title']} @ {a['company_name']}: {action} due {a['next_action_due']}{overdue}")
        if next_actions_total > len(next_actions):
            lines.append(f"  ... and {next_actions_total - len(next_actions)} more")
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append(f"Assessments due in next 7 days [{len(due_assessments)}]:")
    if due_assessments:
        for a in due_assessments:
            lines.append(f"  - {a['kind']} for {a['role_title']} @ {a['company_name']} due {a['deadline']}")
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append(f"Matched postings not yet actioned, top {len(top_postings)} by priority:")
    if top_postings:
        for p in top_postings:
            lines.append(f"  - {p['title']} @ {p['company_name']} (priority {p['company_priority']}, score {p['score']})")
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append(f"Source health failures [{len(failing_sources)}]:")
    if failing_sources:
        for s in failing_sources:
            lines.append(f"  - {s['source']}: {s['consecutive_failures']} consecutive failures")
    else:
        lines.append("  (none)")

    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES] + ["... (truncated to stay under 60 lines)"]

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m pipeline.status", description=__doc__)
    parser.parse_args(argv)
    config.ensure_dirs()
    print(build_status_text())


if __name__ == "__main__":
    main()
