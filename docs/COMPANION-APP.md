# CareerOS companion app (desktop and phone)

Decided by Claude Fable on 2026-09-23 after the candidate asked for a desktop and a mobile companion, connected, dark, useful and pleasant to use, built by Codex and Claude together, with his final drafts, trackers and push notifications always within reach.

## What the candidate gets
- **Review anywhere**: every application's approved answers and latest drafts, readable on the phone, with approve / edit / reject that flow back into the pipeline (`final/`, edit distance, ledger fixes).
- **Trackers**: applications by stage with deadlines, assessments due, the daily shortlist with approve buttons, source health and the weekly cap.
- **Push notifications**: assessment invites, deadlines within 48 hours, drafts ready for review, submissions parked on a CAPTCHA.
- **What the AIs are doing**: the board (`pipeline.queue --board`), each driver's status, recent handoffs, and a live view of a run.

## Architecture (one codebase, three surfaces)
1. **Local API**: FastAPI in `app/api/` running on the laptop (`127.0.0.1:8765`), read-mostly over the SQLite tracker and the `brain/vault/applications` folders, plus the few write actions that already exist as pipeline commands (`drafts approve-claims`, `/review` approve into `final/`, `publish`, `notify`). Every write goes through the same pipeline functions the CLI uses, so hooks and autonomy rules apply.
2. **Web UI (PWA)**: `app/ui/`, a dark, phone-first single-page app (React + Vite + Tailwind, installable to the home screen), served by the API. Dark aesthetic, large tap targets, offline cache of the last-loaded drafts. Built with the `ui-ux-pro-max` skill's design system guidance; the candidate reviews the first screens before more are built.
3. **Desktop shell**: the same UI in a Tauri window (small binary, no Electron bloat) with a tray icon and native toasts; falls back to the browser tab if Tauri tooling is a problem on this machine.
4. **Phone access**: the laptop's API is reached over **Tailscale** (private network, no public exposure, MagicDNS HTTPS certificates), so the PWA works on the phone whenever the laptop is on. When it is off, the **OneDrive** folder (`OneDrive\Documents\CareerOS`, already written by `pipeline.publish`) remains the read-only fallback, and the API keeps that folder current.
5. **Push**: Web Push from the PWA service worker (VAPID keys stored in the vault, not the repo) with **Discord** (already live) and Windows toasts as the fallbacks; `pipeline.notify` gains a `push` channel so every existing alert reaches the phone.

Why not the alternatives: a public web host would expose the candidate's data; native app-store apps cost time without adding capability; an OneDrive-only "app" cannot show live tracker state or take actions.

## Security boundaries
- Nothing personal leaves the laptop except through Tailscale to the candidate's own devices and the existing OneDrive folder.
- The API never exposes the vault, `.env` or the ledger's never-use claims; writes are limited to pipeline commands that already obey `settings.yaml`.
- No credentials in the repository; VAPID keys and any API token live in the vault.

## Split of work (tasks in TASKS.md)
| Task | Owner | What |
|---|---|---|
| T-044 | Claude Fable (this doc) then Claude Opus | architecture, scaffold of `app/api` and `app/ui`, screen list and dark design tokens |
| T-045 | Codex GPT-6 Sol | FastAPI endpoints over tracker and application folders, review actions, publish trigger, tests |
| T-046 | Claude Opus (`ui-ux-pro-max`) | PWA screens: Review, Trackers, Shortlist, Board, Settings; dark theme; offline cache |
| T-047 | either | Tailscale setup notes, service worker, Web Push with VAPID in the vault, `notify` push channel |
| T-048 | Codex GPT-6 Sol | Tauri desktop shell with tray and toasts; Windows installer |
| T-025 | superseded | folded into the above |

Codex and Claude work in parallel on the API and the UI against the same OpenAPI contract, committed first as `app/api/openapi.yaml`.

## Screens (first release)
1. **Home**: what needs the candidate today (drafts to review, deadlines, assessments), one tap each.
2. **Review**: application list, then answer view with the verifier, critic and red-team verdicts, approve / edit / reject.
3. **Tracker**: applications by stage, deadlines, assessments; filters by track and firm.
4. **Shortlist**: today's ranked postings with approve and skip.
5. **Board**: drivers, queues, handoffs, last runs.
6. **Settings**: notification preferences, autonomy level view (read-only in the app), Tailscale status.

## Delivery (the candidate, 2026-09-24)
- Build now; the bulk of the work goes to Claude Opus 5.5 (API, PWA, push, desktop shell), Codex GPT-6 Sol reviews, tests and packages, Claude Fable coordinates and ships the public release (T-053).
- Done means: the desktop app installed with a shortcut on the candidate's desktop; phone install instructions delivered to his email (settings `send_emails` is `never`, so either that one message is allowed or a Gmail draft is left for him); the installer published as a GitHub release on the public CareerOS repository (after T-049's three commands).
- Subagents are allowed for all three drivers.

## Design (the candidate, 2026-09-25)
the candidate compared five dark variations on his real data (Calm, Graphite, Glass, Terminal, Editorial) and chose **Editorial**: warm charcoal (#13110f), cream text (#efe7da), serif headings and reading text (Iowan Old Style / Palatino / Georgia), amber accent (#e8a45c) for "go", pill buttons, 10 px cards. The tokens live in `app/ui/src/index.css`; there is no theme switcher.

