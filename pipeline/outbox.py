"""T-023 Gmail half: guarded drafts and sends for an already tracked application.

This module has no scheduled entry point. Call ``dispatch`` only with a final,
claim-cited email file. The default settings and the existing read-only token
refuse all sends; tests use a fake Gmail client and a temporary database.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Protocol

import yaml

from pipeline import config, drafts, google_oauth, settings, verify

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_TERMINAL_STATUSES = {"withdrawn", "rejected", "ghosted", "accepted"}


class OutboxRefused(RuntimeError):
    """A policy or grounding check prevented any Gmail write."""


class OutboxUncertain(RuntimeError):
    """A Gmail write may have happened; never retry this content automatically."""


class GmailWriter(Protocol):
    def profile(self) -> dict[str, Any]: ...
    def write(self, action: str, raw: str) -> dict[str, Any]: ...


class GmailOutboxClient:
    def __init__(self, oauth: google_oauth.GoogleOAuthSession | None = None, *, account: str | None = None) -> None:
        self.oauth = oauth or google_oauth.GoogleOAuthSession(account=account)

    def profile(self) -> dict[str, Any]:
        return self.oauth.json("GET", f"{GMAIL_BASE}/profile")

    def write(self, action: str, raw: str) -> dict[str, Any]:
        if action == "draft":
            return self.oauth.json("POST", f"{GMAIL_BASE}/drafts", json={"message": {"raw": raw}})
        if action == "send":
            return self.oauth.json("POST", f"{GMAIL_BASE}/messages/send", json={"raw": raw})
        raise ValueError("action must be draft or send")


def _email_address(value: str) -> str:
    value = value.strip()
    name, address = parseaddr(value)
    if name or address != value or not re.fullmatch(r"[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+", address):
        raise OutboxRefused("sender or recipient is not one plain email address")
    return address.casefold()


def _content(path: Path, app_id: int, company: str, drafting_mode: str) -> tuple[str, str, str]:
    folder = drafts.find_folder(app_id)
    if folder is None or path.resolve().parent != (folder / "final").resolve() or path.suffix.lower() != ".md":
        raise OutboxRefused("email must be a Markdown file in this application's final folder")
    before = path.read_bytes()
    sha256 = hashlib.sha256(before).hexdigest()
    meta, body, _, problems = verify.split_draft(before.decode("utf-8"))
    if meta.get("kind") != "email" or str(meta.get("application_id", "")) != str(app_id):
        raise OutboxRefused("email front matter must identify this application and kind: email")
    if not drafting_mode or meta.get("mode") != drafting_mode:
        raise OutboxRefused("email drafting mode must match the tracked application")
    if drafting_mode in {"proofread_only", "outline_only"} and meta.get("generated_by") != "ali":
        raise OutboxRefused("this employer requires the candidate's own email text")
    subject = str(meta.get("subject") or "").strip()
    if not subject or len(subject) > 200 or "\r" in subject or "\n" in subject:
        raise OutboxRefused("email subject is missing or invalid")
    if problems or not body:
        raise OutboxRefused("email has missing citations, a fact request, or empty prose")
    approvals = folder / "approvals.yaml"
    allowed: set[str] = set()
    if approvals.exists():
        data = yaml.safe_load(approvals.read_text(encoding="utf-8")) or {}
        allowed = {str(cid) for cid in (data.get("claims") or [])}
    report = verify.verify_draft(path, verify.load_ledger(), approved=allowed, company=company)
    if report["hard_fails"] or report["review"]["fact_requests"]:
        raise OutboxRefused("email failed the deterministic grounding or tone check")
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
        raise OutboxRefused("email changed during verification")
    return subject, body, sha256


def _reserve(
    conn: sqlite3.Connection, *, application_id: int, posting_id: int, contact_id: int,
    sender: str, recipient: str, action: str, sha256: str, now: datetime, cap: int | None,
) -> int:
    if conn.in_transaction:
        raise OutboxRefused("commit other tracker changes before reserving an email")
    try:
        conn.execute("BEGIN IMMEDIATE")
        if action == "send" and cap is not None:
            count = conn.execute(
                "SELECT COUNT(*) FROM outbound_emails WHERE action='send' "
                "AND substr(created_at,1,10)=? AND status IN ('pending','sent','uncertain')",
                (now.date().isoformat(),),
            ).fetchone()[0]
            if count >= cap:
                raise OutboxRefused(f"daily email cap of {cap} has been reached")
        cur = conn.execute(
            "INSERT INTO outbound_emails "
            "(application_id, posting_id, contact_id, sender, recipient, action, content_sha256, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (application_id, posting_id, contact_id, sender, recipient, action, sha256, now.isoformat()),
        )
        conn.commit()
        return int(cur.lastrowid)
    except sqlite3.IntegrityError:
        conn.rollback()
        raise OutboxRefused("this exact email already has a tracker record; do not retry") from None
    except Exception:
        conn.rollback()
        raise


def _finish(conn: sqlite3.Connection, row_id: int, status: str, gmail_id: str | None) -> None:
    conn.execute(
        "UPDATE outbound_emails SET status=?, gmail_id=?, completed_at=datetime('now') WHERE id=?",
        (status, gmail_id, row_id),
    )
    conn.execute(
        "INSERT INTO events (entity, entity_id, type, source, detail_json) VALUES (?, ?, ?, ?, ?)",
        ("outbound_email", row_id, f"outbox_{status}", "gmail", json.dumps({"gmail_id": gmail_id})),
    )
    conn.commit()


def dispatch(
    application_id: int,
    contact_id: int,
    email_file: Path,
    *,
    action: str,
    conn: sqlite3.Connection,
    active: settings.Settings | None = None,
    gmail: GmailWriter | None = None,
    scopes: set[str] | None = None,
    now: datetime | None = None,
) -> int:
    """Create one Gmail draft or make one send attempt; return its tracker row id.

    A transport error is recorded as ``uncertain`` and never retried automatically.
    No message content is written to events, terminal output or the tracker.
    """
    if action not in {"draft", "send"}:
        raise ValueError("action must be draft or send")
    active = active or settings.current()
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if config.PAUSED_FLAG.exists():
        raise OutboxRefused("CareerOS is paused")
    level = active.autonomy_level()
    if action == "draft" and level < 2:
        raise OutboxRefused("Gmail drafts require autonomy level 2 or higher")
    if action == "send" and (level < 3 or active.override("send_emails") != "auto"):
        raise OutboxRefused("sending requires level 3 and send_emails: auto")
    sender = _email_address(str(active.get("vault", "applications_email", default="") or ""))
    granted = scopes if scopes is not None else google_oauth.granted_scopes(sender)
    if google_oauth.GMAIL_MODIFY_SCOPE not in granted:
        raise OutboxRefused("Gmail modify consent is missing; re-authorise before writing mail")
    app = conn.execute(
        "SELECT a.company_id, a.posting_id, a.status, a.drafting_mode, c.name AS company, "
        "p.company_id AS posting_company_id FROM applications a "
        "JOIN companies c ON c.id=a.company_id LEFT JOIN postings p ON p.id=a.posting_id WHERE a.id=?",
        (application_id,),
    ).fetchone()
    if (app is None or app["posting_id"] is None or app["status"] in _TERMINAL_STATUSES
            or app["posting_company_id"] != app["company_id"]):
        raise OutboxRefused("application is missing, terminal, or has no tracked posting")
    contact = conn.execute(
        "SELECT company_id, email FROM contacts WHERE id=?", (contact_id,)
    ).fetchone()
    if contact is None or contact["company_id"] != app["company_id"] or not contact["email"]:
        raise OutboxRefused("recipient is not a contact for this application's company")
    recipient = _email_address(str(contact["email"]))
    subject, body, sha256 = _content(Path(email_file), application_id, str(app["company"]),
                                     str(app["drafting_mode"] or ""))
    client = gmail or GmailOutboxClient(account=sender)
    profile = client.profile()  # read-only; prevents writing from the wrong Google account
    if _email_address(str(profile.get("emailAddress") or "")) != sender:
        raise OutboxRefused("OAuth account does not match the configured applications sender")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
    row_id = _reserve(
        conn, application_id=application_id, posting_id=int(app["posting_id"]),
        contact_id=contact_id, sender=sender, recipient=recipient, action=action,
        sha256=sha256, now=now, cap=active.cap("daily_emails"),
    )
    try:
        response = client.write(action, raw)
        gmail_id = str(response.get("id") or "")
        if not gmail_id:
            raise ValueError("Gmail response contained no id")
    except Exception:
        try:
            _finish(conn, row_id, "uncertain", None)
        except Exception:
            pass  # the pending reservation still prevents an automatic duplicate
        raise OutboxUncertain("Gmail outcome is uncertain; inspect the mailbox before any manual retry") from None
    try:
        _finish(conn, row_id, "drafted" if action == "draft" else "sent", gmail_id)
    except Exception:
        # The reservation remains pending, so another automatic attempt is barred.
        raise OutboxUncertain("Gmail accepted the write but tracker confirmation failed; inspect both manually") from None
    return row_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.outbox")
    parser.add_argument("command", choices=("status", "auth"))
    args = parser.parse_args(argv)
    active = settings.current()
    configured = str(active.get("vault", "applications_email", default="") or "").strip()
    if args.command == "status":
        print(f"Applications sender configured: {'yes' if configured else 'no'}")
        print(f"Gmail modify consent stored: {'yes' if configured and google_oauth.GMAIL_MODIFY_SCOPE in google_oauth.granted_scopes(configured) else 'no'}")
        print(f"Autonomy level: {active.autonomy_level()}; send_emails override: {active.override('send_emails')}")
        return 0
    sender = _email_address(configured)
    inbox_account = str(active.get("inbox", "read_account", default="") or "").casefold()
    scopes = google_oauth.SCOPES if sender == inbox_account else (google_oauth.GMAIL_MODIFY_SCOPE,)
    google_oauth.authorise(account=sender, scopes=scopes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
