# CareerOS Discord bot (T-013)

A tiny `discord.py` bot that lets the candidate check status and approve/reject applications
from the phone or desktop, in one dedicated channel. It calls no LLM and reads no
message content other than the fixed commands below; it is not a chat bot.

Adapted from `C:\Users\<user>\claude-discord-bot\bot.py` (kept: the `discord.Client` +
`message_content` intent setup, the UTF-8 console fix, and the gateway-resume message
dedup). Dropped: image generation and the Anthropic/OpenAI conversational loop --
none of that is needed or wanted here.

## What it does

In the single channel `DISCORD_APPROVALS_CHANNEL_ID`, from the single user
`DISCORD_OWNER_ID`, it understands plain-text commands (no prefix, case-insensitive)
and replies with the result. Messages from any other channel or any other user are
silently ignored.

| Command | Effect |
|---|---|
| `status` | Posts the `pipeline.status` report (applications by status, overdue actions, upcoming assessments, top unactioned postings, source health, cap usage, autonomy level, pause flag). |
| `digest` | Posts the `pipeline.notify.digest()` report (new postings, applications by status, assessments due, overdue actions, source failures, weekly cap usage). |
| `approve <application-id>` | Moves the application from `draft` or `ready-for-review` to `approved`. Refuses (with a clear reason) from any other status. |
| `reject <application-id> [reason]` | Moves the application to `withdrawn` from any status. The optional reason is logged. |
| `pause` | Writes `%USERPROFILE%\CareerOS-data\PAUSED`. Future pipeline runs are expected to check this flag before taking autonomous action. |
| `resume` | Removes the `PAUSED` flag. |

Every `approve`/`reject`/`pause`/`resume` writes to the `events` audit table with
`source = "discord"` (via `pipeline.db.transition_application` for the status change
itself, plus an explicit `pipeline.db.log_event(..., type="discord_command", ...)`
recording the raw command and the Discord user id).

## One-time setup

1. **Create the private Discord server** (if it does not exist yet) with three text
   channels: `#digest`, `#actions`, `#approvals`. Only the candidate needs to be a member.
2. **Reuse the existing bot application.** `C:\Users\<user>\claude-discord-bot`
   already has a registered Discord application and `DISCORD_BOT_TOKEN`. Either
   invite that same bot to the new server, or create a fresh application in the
   [Discord Developer Portal](https://discord.com/developers/applications) -- either
   way you need a bot token.
3. In the Developer Portal, under **Bot**, enable the **Message Content Intent**
   (a privileged intent -- required, since the bot reads plain-text commands). The
   existing bot already has this enabled for its current server; a new server needs
   the bot re-invited with the right scope regardless.
4. **Invite the bot** to the server with an invite URL scoped to scopes `bot` and
   permissions *View Channel*, *Send Messages*, *Read Message History* (no other
   permissions are needed -- this bot never manages the server, roles or channels).
5. **Create a webhook** on `#digest` (Channel Settings -> Integrations -> Webhooks ->
   New Webhook) and copy its URL -- this is `DISCORD_WEBHOOK_URL`, used by
   `pipeline.notify`, not by this bot directly.
6. **Get the IDs you need** (enable Discord's Developer Mode first: User Settings ->
   Advanced -> Developer Mode):
   - Right-click `#approvals` -> Copy Channel ID -> `DISCORD_APPROVALS_CHANNEL_ID`.
   - Right-click your own name -> Copy User ID -> `DISCORD_OWNER_ID`.
7. **Fill in `.env`** (repo root, git-ignored; copy from `.env.example` if you have
   not already):
   ```
   DISCORD_BOT_TOKEN=...
   DISCORD_WEBHOOK_URL=...
   DISCORD_APPROVALS_CHANNEL_ID=...
   DISCORD_OWNER_ID=...
   ```
   Values are never printed by any CareerOS script or committed to git.

## Running it

Nothing here starts the bot automatically -- there is no server to connect to until
step 1 above is done. Once `.env` is filled in:

```powershell
scripts\run-discord-bot.ps1
```

which runs `app/discord-bot/bot.py` under the project's Python
(`C:\Users\<user>\AppData\Local\Programs\Python\Python313\python.exe`) after checking
that `.env` and the interpreter both exist. Stop it with Ctrl+C. It is a long-running
foreground process; if you want it always on, wrap the script in a Scheduled Task
later (out of scope for T-013).

## Safety notes

- The bot never calls an LLM and never reads message history beyond the single
  incoming message it is handling.
- `approve`/`reject` only ever call `pipeline.db.transition_application`, which is
  guarded by the `applications_status_transition_guard` SQL trigger -- an invalid
  transition raises `sqlite3.IntegrityError` and is reported back as a normal reply,
  not a crash.
- Commands from anyone other than `DISCORD_OWNER_ID`, or sent outside
  `DISCORD_APPROVALS_CHANNEL_ID`, are ignored with no reply at all (so the bot does
  not confirm its own owner/channel configuration to a stranger).
