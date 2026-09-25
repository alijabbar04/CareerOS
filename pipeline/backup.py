"""T-007: nightly-style VACUUM INTO backup of the CareerOS database.

    python -m pipeline.backup                    # take a backup (also runs nightly at 23:30 via Task Scheduler)
    python -m pipeline.backup list               # newest first, with sizes
    python -m pipeline.backup restore <file>     # copy a backup into place; refuses to overwrite a live file without --force

Writes careeros-<YYYYMMDD-HHMM>.sqlite into config.BACKUP_DIR (created if
missing) and prunes everything beyond the newest KEEP backups. VACUUM INTO
produces a consistent, compacted snapshot even while the live database is
open elsewhere (see research/C_tech_stack.md section 7 on why the live file
must stay out of OneDrive; this is the backup that goes to OneDrive instead).
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from pipeline import config, db

KEEP = 14


def backup(
    db_path: Path | str | None = None,
    backup_dir: Path | str | None = None,
    keep: int = KEEP,
) -> Path:
    """Snapshot the database and prune old backups. Returns the new file's path."""
    db_path = Path(db_path) if db_path is not None else config.DB_PATH
    backup_dir = Path(backup_dir) if backup_dir is not None else config.BACKUP_DIR
    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    dest = backup_dir / f"careeros-{stamp}.sqlite"
    counter = 0
    while dest.exists():  # two backups in the same minute (e.g. a forced restore right after a backup)
        counter += 1
        dest = backup_dir / f"careeros-{stamp}-{counter}.sqlite"

    conn = db.connect(db_path)
    try:
        conn.execute("VACUUM INTO ?", (str(dest),))
    finally:
        conn.close()

    _prune(backup_dir, keep)
    print(dest)
    return dest


def _prune(backup_dir: Path, keep: int) -> None:
    backups = sorted(
        backup_dir.glob("careeros-*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for stale in backups[keep:]:
        stale.unlink()


def list_backups(backup_dir: Path | str | None = None) -> list[Path]:
    backup_dir = Path(backup_dir) if backup_dir is not None else config.BACKUP_DIR
    if not backup_dir.exists():
        return []
    return sorted(backup_dir.glob("careeros-*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)


def restore(src: Path | str, db_path: Path | str | None = None, force: bool = False) -> Path:
    """Copy a backup file into place as the live database.

    Refuses when a live file already exists unless force=True, in which case the
    existing file is backed up first. Empty or tiny files are refused outright
    because an accidental "restore" of an empty database is the failure this
    command exists to prevent.
    """
    src = Path(src)
    dest = Path(db_path) if db_path is not None else config.DB_PATH
    if not src.exists():
        raise FileNotFoundError(src)
    if src.stat().st_size < 100_000:
        raise ValueError(f"{src} is only {src.stat().st_size} bytes; not a real backup")
    if dest.exists() and not force:
        raise FileExistsError(f"{dest} exists; pass --force to replace it (the current file is backed up first)")
    if dest.exists():
        backup(dest)
    for suffix in ("-wal", "-shm"):
        side = Path(str(dest) + suffix)
        if side.exists():
            side.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    print(f"restored {src} -> {dest}")
    return dest


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m pipeline.backup", description=__doc__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("list", help="list backups, newest first")
    p_restore = sub.add_parser("restore", help="copy a backup into place as the live database")
    p_restore.add_argument("file", type=Path)
    p_restore.add_argument("--force", action="store_true", help="replace an existing live file (it is backed up first)")
    args = parser.parse_args(argv)
    if args.command == "list":
        for path in list_backups():
            print(f"{path.stat().st_size:>12,d}  {path}")
    elif args.command == "restore":
        restore(args.file, force=args.force)
    else:
        backup()


if __name__ == "__main__":
    main()
