"""T-007: connect to and migrate the CareerOS SQLite database; small helpers over it.

    python -m pipeline.db migrate               # apply pending migrations
    python -m pipeline.db status                 # row counts for every table
    python -m pipeline.db search-claims <query>   # FTS5 search over the claims ledger

Every helper below takes an optional trailing `conn`: pass one to reuse an
open connection (and its transaction) across several calls, or omit it and
the helper opens and closes its own. `pipeline.claims` already relies on this
(`db.migrate()` and `db.insert_claim(row, conn=conn, tags=[...])`), so both
call shapes must keep working.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pipeline import config


# ---------------------------------------------------------------------------
# Connection and migrations
# ---------------------------------------------------------------------------

def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a connection with the pragmas every caller needs (WAL, foreign keys,
    dict-like rows). Creates the parent directory if missing. Does not migrate;
    call migrate() explicitly, typically right after connecting.
    """
    path = Path(db_path) if db_path is not None else config.DB_PATH
    if db_path is None and not path.exists() and not os.environ.get("CAREEROS_ALLOW_NEW_DB"):
        # Fail closed: the live tracker vanished twice on 2026-09-22/23 and a silent
        # empty replacement hid it for hours. Restoring is a deliberate act.
        raise RuntimeError(
            f"live database missing at {path}; refusing to create an empty one. "
            "Restore the newest backup with 'python -m pipeline.backup restore <file>' "
            "(see 'python -m pipeline.backup list'), or set CAREEROS_ALLOW_NEW_DB=1 "
            "only for a deliberately fresh database."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def _connection(conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    """Yield `conn` if given, else a fresh one that is closed again on exit."""
    owns_it = conn is None
    active = conn if conn is not None else connect()
    try:
        yield active
    finally:
        if owns_it:
            active.close()


def migrate(conn: sqlite3.Connection | None = None) -> list[str]:
    """Apply migrations from config.MIGRATIONS_DIR in filename order, skipping
    any version already recorded in schema_migrations. Returns the versions
    applied this call (empty if the database was already up to date, so
    calling this twice in a row is a no-op the second time).
    """
    with _connection(conn) as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        already_applied = {row["version"] for row in c.execute("SELECT version FROM schema_migrations")}

        applied_now = []
        for path in sorted(config.MIGRATIONS_DIR.glob("*.sql")):
            version = path.stem
            if version in already_applied:
                continue
            c.executescript(path.read_text(encoding="utf-8"))
            c.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
            c.commit()
            applied_now.append(version)
        return applied_now


def status(conn: sqlite3.Connection | None = None) -> dict[str, int]:
    """Return {table_name: row_count} for every real table (shadow tables that
    back the FTS5 indexes and sqlite's own internal tables are excluded).
    """
    with _connection(conn) as c:
        tables = c.execute(
            "SELECT name FROM pragma_table_list "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return {
            row["name"]: c.execute(f'SELECT COUNT(*) AS n FROM "{row["name"]}"').fetchone()["n"]
            for row in tables
        }


# ---------------------------------------------------------------------------
# Claims ledger
# ---------------------------------------------------------------------------

def _next_claim_id(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT MAX(CAST(SUBSTR(id, 3) AS INTEGER)) AS n FROM claims").fetchone()
    return f"C-{(row['n'] or 0) + 1:04d}"


def insert_claim(
    claim: dict[str, Any],
    tags: list[str] | None = None,
    conn: sqlite3.Connection | None = None,
) -> str:
    """Insert a claims row. `claim` must at least supply text, category,
    source_id, locator, sensitivity and confidence (the NOT NULL columns);
    'id' is auto-assigned as the next 'C-XXXX' if not given. Returns the id.
    """
    with _connection(conn) as c:
        data = dict(claim)
        data.setdefault("id", _next_claim_id(c))
        columns = list(data.keys())
        placeholders = ", ".join(f":{col}" for col in columns)
        c.execute(f"INSERT INTO claims ({', '.join(columns)}) VALUES ({placeholders})", data)
        for tag in tags or []:
            c.execute("INSERT OR IGNORE INTO claim_tags (claim_id, tag) VALUES (?, ?)", (data["id"], tag))
        c.commit()
        return data["id"]


def get_claim(claim_id: str, conn: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    with _connection(conn) as c:
        return c.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()


def search_claims(
    query: str, limit: int = 20, conn: sqlite3.Connection | None = None
) -> list[sqlite3.Row]:
    """Full-text search over claims (text, quote, subject) via FTS5, best match first."""
    with _connection(conn) as c:
        return c.execute(
            """
            SELECT claims.*
            FROM claims_fts
            JOIN claims ON claims.rowid = claims_fts.rowid
            WHERE claims_fts MATCH ?
            ORDER BY claims_fts.rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()


# ---------------------------------------------------------------------------
# Companies and postings
# ---------------------------------------------------------------------------

def upsert_company(name: str, conn: sqlite3.Connection | None = None, **fields: Any) -> int:
    """Insert a company, or update one already matched case-insensitively on name."""
    with _connection(conn) as c:
        data = dict(fields)
        data["name"] = name
        columns = list(data.keys())
        placeholders = ", ".join(f":{col}" for col in columns)
        set_clause = ", ".join(f"{col} = excluded.{col}" for col in columns if col != "name")
        set_clause = (f"{set_clause}, " if set_clause else "") + "updated_at = datetime('now')"
        row = c.execute(
            f"INSERT INTO companies ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(name) DO UPDATE SET {set_clause} "
            "RETURNING id",
            data,
        ).fetchone()
        c.commit()
        return row["id"]


def upsert_posting(posting: dict[str, Any], conn: sqlite3.Connection | None = None) -> int:
    """Insert a posting, or update one already matched on canonical_url."""
    canonical_url = posting.get("canonical_url")
    if not canonical_url:
        raise ValueError("upsert_posting requires a canonical_url")
    with _connection(conn) as c:
        data = dict(posting)
        columns = list(data.keys())
        placeholders = ", ".join(f":{col}" for col in columns)
        set_clause = ", ".join(f"{col} = excluded.{col}" for col in columns if col != "canonical_url")
        set_clause = (f"{set_clause}, " if set_clause else "") + "updated_at = datetime('now'), last_seen = datetime('now')"
        row = c.execute(
            f"INSERT INTO postings ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(canonical_url) DO UPDATE SET {set_clause} "
            "RETURNING id",
            data,
        ).fetchone()
        c.commit()
        return row["id"]


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

def create_application(
    posting_id: int | None,
    company_id: int,
    role_title: str,
    cycle: str,
    drafting_mode: str,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Create a new application in status 'draft'. Returns the new id."""
    with _connection(conn) as c:
        cur = c.execute(
            "INSERT INTO applications (posting_id, company_id, role_title, cycle, drafting_mode, status) "
            "VALUES (?, ?, ?, ?, ?, 'draft')",
            (posting_id, company_id, role_title, cycle, drafting_mode),
        )
        c.commit()
        return cur.lastrowid


def transition_application(
    app_id: int,
    new_status: str,
    source: str,
    detail: dict[str, Any] | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Move an application to `new_status`. The applications_status_transition_guard
    trigger rejects disallowed transitions by raising sqlite3.IntegrityError, in
    which case nothing changes; on success applications_status_event writes the
    matching `events` row (with this call's `source`/`detail`) automatically.
    """
    detail_json = json.dumps(detail) if detail is not None else None
    with _connection(conn) as c:
        cur = c.execute(
            "UPDATE applications "
            "SET status = ?, last_transition_source = ?, last_transition_detail_json = ? "
            "WHERE id = ?",
            (new_status, source, detail_json, app_id),
        )
        if cur.rowcount == 0:
            raise ValueError(f"no application with id {app_id}")
        c.commit()


# ---------------------------------------------------------------------------
# Events and source health
# ---------------------------------------------------------------------------

def log_event(
    entity: str,
    entity_id: int,
    type: str,
    detail: dict[str, Any] | None = None,
    source: str = "system",
    conn: sqlite3.Connection | None = None,
) -> int:
    """Append a row to the audit log. Returns the new event id."""
    with _connection(conn) as c:
        cur = c.execute(
            "INSERT INTO events (occurred_at, entity, entity_id, type, source, detail_json) "
            "VALUES (datetime('now'), ?, ?, ?, ?, ?)",
            (entity, entity_id, type, source, json.dumps(detail) if detail is not None else None),
        )
        c.commit()
        return cur.lastrowid


def record_source_health(
    source: str,
    ok: bool,
    note: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Upsert a source_health row: always stamps last_run; on success stamps
    last_ok and resets consecutive_failures, otherwise increments the streak.
    """
    with _connection(conn) as c:
        now = c.execute("SELECT datetime('now') AS now").fetchone()["now"]
        c.execute(
            """
            INSERT INTO source_health (source, last_run, last_ok, consecutive_failures, notes)
            VALUES (:source, :now, CASE WHEN :ok THEN :now ELSE NULL END, CASE WHEN :ok THEN 0 ELSE 1 END, :note)
            ON CONFLICT(source) DO UPDATE SET
                last_run = :now,
                last_ok = CASE WHEN :ok THEN :now ELSE source_health.last_ok END,
                consecutive_failures = CASE WHEN :ok THEN 0 ELSE source_health.consecutive_failures + 1 END,
                notes = COALESCE(:note, source_health.notes)
            """,
            {"source": source, "now": now, "ok": 1 if ok else 0, "note": note},
        )
        c.commit()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m pipeline.db", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply pending migrations")
    sub.add_parser("status", help="print row counts for every table")
    search_cmd = sub.add_parser("search-claims", help="full-text search over the claims ledger")
    search_cmd.add_argument("query")

    args = parser.parse_args(argv)
    config.ensure_dirs()

    if args.command == "migrate":
        applied = migrate()
        print(f"Applied: {', '.join(applied)}" if applied else "Already up to date.")
    elif args.command == "status":
        for name, count in status().items():
            print(f"{name:28s} {count}")
    elif args.command == "search-claims":
        rows = search_claims(args.query)
        if not rows:
            print("No matches.")
        for row in rows:
            print(f"{row['id']}  [{row['category']}] {row['text']}")


if __name__ == "__main__":
    main()
