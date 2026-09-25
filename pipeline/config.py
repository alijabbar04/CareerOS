"""Central paths for every CareerOS script.

Everything inside the repository is text (Markdown, YAML, SQL, Python). Live data
(the SQLite database, the vault, the browser profile, exports and logs) lives outside
the repository under %USERPROFILE%\\CareerOS-data so that OneDrive never touches it.
Not under %LOCALAPPDATA%: Claude Desktop is an MSIX app and Windows silently redirects
its AppData writes to a private copy, which split the database in two on 2026-09-22/23.
Override any path with the environment variables named below.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Inside the repository (committed)
BRAIN_DIR = REPO_ROOT / "brain"
SOURCES_DIR = BRAIN_DIR / "sources"
VAULT_DIR = BRAIN_DIR / "vault"          # Markdown vault (profile, stories, narratives, voice, companies, applications)
DATA_DIR = REPO_ROOT / "data"
MIGRATIONS_DIR = REPO_ROOT / "pipeline" / "migrations"

# Outside the repository (never committed)
LOCAL_DIR = Path(
    os.environ.get("CAREEROS_LOCAL_DIR")
    or (Path.home() / "CareerOS-data")
)
DB_PATH = LOCAL_DIR / "careeros.sqlite"
LOGS_DIR = LOCAL_DIR / "logs"
EXPORTS_DIR = LOCAL_DIR / "exports"
SECRET_VAULT_DIR = LOCAL_DIR / "vault"   # encrypted identity values and portal credentials (pipeline/vault.py)
BROWSER_PROFILE_DIR = LOCAL_DIR / "browser-profile"
CACHE_DIR = LOCAL_DIR / "cache"          # ETag / Last-Modified cache for pipeline.http (T-011)
INBOX_DIR = LOCAL_DIR / "inbox"          # local email bodies; never committed (T-015)
SCREENSHOTS_DIR = LOCAL_DIR / "screenshots"  # masked form screenshots only (T-020/T-021)
PAUSED_FLAG = LOCAL_DIR / "PAUSED"       # presence = paused (pipeline/notify.py, pipeline/status.py, app/discord-bot)

# the candidate's source documents (read-only inputs; see docs/EXTERNAL-LOCATIONS.md)
CAREERS_FOLDER = Path(
    os.environ.get("CAREEROS_CAREERS_FOLDER")
    or r"C:\Users\<user>\OneDrive\Documents\<careers-folder>\<careers-folder>"
)
WORK_LOG_XLSX = Path(
    os.environ.get("CAREEROS_WORK_LOG")
    or r"C:\Users\<user>\AppData\Local\WorkHoursTracker\Work_Hours_Tracker.xlsx"
)
BACKUP_DIR = Path(
    os.environ.get("CAREEROS_BACKUP_DIR")
    or (CAREERS_FOLDER.parent / "CareerOS Backups")
)


def load_env(path: Path | None = None) -> dict[str, str]:
    """Load KEY=VALUE lines from the repository's .env (never committed) into os.environ without overriding
    variables that are already set. Returns the keys loaded. Values are never printed by any script."""
    path = path or (REPO_ROOT / ".env")
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


def ensure_dirs() -> None:
    """Create the local directories on first use."""
    for d in (LOCAL_DIR, LOGS_DIR, EXPORTS_DIR, SECRET_VAULT_DIR, BROWSER_PROFILE_DIR, CACHE_DIR, INBOX_DIR, SCREENSHOTS_DIR, SOURCES_DIR, VAULT_DIR, DATA_DIR):
        d.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    for name, value in sorted(globals().items()):
        if name.isupper() and isinstance(value, Path):
            print(f"{name:22s} {value}")
