"""T-013: CareerOS Discord bot -- status and approvals from the phone or desktop.

Adapted from C:\\Users\\<user>\\claude-discord-bot\\bot.py: kept its Discord gateway
setup (Client + message_content intent) and its defence against a gateway resume
redelivering the same MESSAGE_CREATE event twice; dropped everything about image
generation and the Anthropic/OpenAI conversational loop, because this bot does not
call any LLM -- it only understands a fixed set of plain-text commands, sent in the
single channel DISCORD_APPROVALS_CHANNEL_ID by the single user DISCORD_OWNER_ID.
Every other channel and every other author is ignored outright.

Commands (case-insensitive, no prefix):

    status                              pipeline.status report
    digest                              pipeline.notify.digest() report
    approve <application-id>            draft/ready-for-review -> approved
    reject <application-id> [reason]    any status -> withdrawn
    pause                               write the %USERPROFILE%\\CareerOS-data\\PAUSED flag
    resume                              remove the PAUSED flag

approve/reject call pipeline.db.transition_application (which itself logs the
status-change event) and then pipeline.db.log_event(..., source="discord") for an
explicit, bot-specific audit trail entry (which Discord user, which raw command).

This module does not start itself -- see app/discord-bot/README.md and
scripts/run-discord-bot.ps1. There is no private Discord server yet, so nothing here
runs until the candidate creates it and fills in .env.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

# Windows' console defaults to cp1252, which can't encode an em dash or similar --
# force UTF-8 so console print() calls never crash on them (same fix as the base bot).
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# This script is not installed as a package and is run by path (python
# app\discord-bot\bot.py / scripts\run-discord-bot.ps1), not with `python -m`, so
# unlike the pipeline.* modules the repo root is not put on sys.path automatically.
# Add it ourselves so `from pipeline import ...` below resolves regardless of the
# current working directory.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import discord

from pipeline import config, db, notify, status

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

APPROVALS_CHANNEL_ID: int = 0
OWNER_ID: int = 0

# Defends against Discord gateway resume/reconnect delivering the same MESSAGE_CREATE
# event twice, exactly as the base bot does. Bounded so it doesn't grow unbounded over
# a long-running process.
_processed_message_ids: deque[int] = deque(maxlen=1000)


def _require_env() -> tuple[str, int, int]:
    """Read and validate the three .env values this bot cannot run without. Never
    prints their values (only whether each is present)."""
    config.load_env()
    token = os.environ.get("DISCORD_BOT_TOKEN")
    channel_id = os.environ.get("DISCORD_APPROVALS_CHANNEL_ID")
    owner_id = os.environ.get("DISCORD_OWNER_ID")
    missing = [
        name
        for name, value in (
            ("DISCORD_BOT_TOKEN", token),
            ("DISCORD_APPROVALS_CHANNEL_ID", channel_id),
            ("DISCORD_OWNER_ID", owner_id),
        )
        if not value
    ]
    if missing:
        raise SystemExit(
            "Missing .env value(s): " + ", ".join(missing) + ". Copy .env.example to "
            ".env and fill them in first -- see app/discord-bot/README.md."
        )
    try:
        return token, int(channel_id), int(owner_id)
    except ValueError:
        raise SystemExit("DISCORD_APPROVALS_CHANNEL_ID and DISCORD_OWNER_ID must be numeric Discord ids.")


def _chunks(text: str, size: int = 2000) -> list[str]:
    """Discord has a 2000-character limit per message."""
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


async def _reply(message: discord.Message, text: str) -> None:
    for chunk in _chunks(text):
        await message.channel.send(chunk)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _cmd_approve(args: list[str], author_id: int) -> str:
    if not args or not args[0].lstrip("-").isdigit():
        return "Usage: approve <application-id>"
    app_id = int(args[0])

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id, status, role_title FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        if row is None:
            return f"No application with id {app_id}."

        current_status = row["status"]
        try:
            if current_status == "draft":
                db.transition_application(app_id, "ready-for-review", "discord", conn=conn)
                db.transition_application(app_id, "approved", "discord", conn=conn)
            elif current_status == "ready-for-review":
                db.transition_application(app_id, "approved", "discord", conn=conn)
            elif current_status == "approved":
                return f"Application {app_id} ({row['role_title']}) is already approved."
            else:
                return (
                    f"Application {app_id} ({row['role_title']}) is in status "
                    f"'{current_status}'; approve only works from 'draft' or 'ready-for-review'."
                )
        except sqlite3.IntegrityError as exc:
            return f"Could not approve application {app_id}: {exc}"

        db.log_event(
            entity="application",
            entity_id=app_id,
            type="discord_command",
            detail={"command": "approve", "discord_user_id": author_id},
            source="discord",
            conn=conn,
        )
        return f"Approved application {app_id} ({row['role_title']})."
    finally:
        conn.close()


def _cmd_reject(args: list[str], author_id: int) -> str:
    if not args or not args[0].lstrip("-").isdigit():
        return "Usage: reject <application-id> [reason]"
    app_id = int(args[0])
    reason = " ".join(args[1:]).strip() or None

    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id, status, role_title FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        if row is None:
            return f"No application with id {app_id}."

        try:
            db.transition_application(
                app_id, "withdrawn", "discord",
                detail={"reason": reason} if reason else None, conn=conn,
            )
        except sqlite3.IntegrityError as exc:
            return f"Could not reject application {app_id}: {exc}"

        db.log_event(
            entity="application",
            entity_id=app_id,
            type="discord_command",
            detail={"command": "reject", "reason": reason, "discord_user_id": author_id},
            source="discord",
            conn=conn,
        )
        suffix = f" Reason: {reason}" if reason else ""
        return f"Rejected (withdrawn) application {app_id} ({row['role_title']}).{suffix}"
    finally:
        conn.close()


def _cmd_pause(author_id: int) -> str:
    config.ensure_dirs()
    config.PAUSED_FLAG.write_text(
        json.dumps(
            {"paused_at": datetime.now(timezone.utc).isoformat(), "by": "discord", "discord_user_id": author_id}
        ),
        encoding="utf-8",
    )
    db.log_event(
        entity="system", entity_id=0, type="pause",
        detail={"discord_user_id": author_id}, source="discord",
    )
    return "Paused. The pipeline will skip autonomous actions until 'resume'."


def _cmd_resume(author_id: int) -> str:
    was_paused = config.PAUSED_FLAG.exists()
    config.PAUSED_FLAG.unlink(missing_ok=True)
    db.log_event(
        entity="system", entity_id=0, type="resume",
        detail={"discord_user_id": author_id}, source="discord",
    )
    return "Resumed." if was_paused else "Was not paused."


def handle_command(content: str, author_id: int) -> str | None:
    """Parse and run one command. Returns the reply text, or None for an
    unrecognised command (stay silent rather than spam errors for stray messages)."""
    parts = content.strip().split()
    if not parts:
        return None
    cmd, args = parts[0].lower(), parts[1:]

    try:
        if cmd == "status":
            return status.build_status_text()
        if cmd == "digest":
            return notify.digest()
        if cmd == "approve":
            return _cmd_approve(args, author_id)
        if cmd == "reject":
            return _cmd_reject(args, author_id)
        if cmd == "pause":
            return _cmd_pause(author_id)
        if cmd == "resume":
            return _cmd_resume(author_id)
    except Exception as exc:  # one bad command must never take the bot process down
        return f"Error handling '{cmd}': {exc}"
    return None


# ---------------------------------------------------------------------------
# Discord events
# ---------------------------------------------------------------------------

@bot.event
async def on_ready() -> None:
    print(f"Logged in as {bot.user}. Listening in channel {APPROVALS_CHANNEL_ID} for owner {OWNER_ID}.")


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author == bot.user or message.author.bot:
        return
    if message.id in _processed_message_ids:
        return
    _processed_message_ids.append(message.id)

    if message.channel.id != APPROVALS_CHANNEL_ID:
        return
    if message.author.id != OWNER_ID:
        return  # ignore everyone else, per spec -- no reply, no error

    reply = handle_command(message.content, message.author.id)
    if reply is not None:
        await _reply(message, reply)


def main() -> None:
    global APPROVALS_CHANNEL_ID, OWNER_ID
    token, APPROVALS_CHANNEL_ID, OWNER_ID = _require_env()
    bot.run(token)


if __name__ == "__main__":
    main()
