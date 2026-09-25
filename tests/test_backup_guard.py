"""The live database must never be silently recreated empty; restores are deliberate.

Isolation as in tests/test_notify.py: every config path is pointed at a temp folder.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TEMP_DIR = tempfile.mkdtemp(prefix="careeros-guard-test-")
os.environ["CAREEROS_LOCAL_DIR"] = _TEMP_DIR

from pipeline import backup, config, db  # noqa: E402


class GuardTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="careeros-guard-case-", dir=_TEMP_DIR))
        config.LOCAL_DIR = self.temp
        config.DB_PATH = self.temp / "careeros.sqlite"
        config.BACKUP_DIR = self.temp / "backups"
        config.LOGS_DIR = self.temp / "logs"
        config.PAUSED_FLAG = self.temp / "PAUSED"
        config.ensure_dirs()

    def test_connect_refuses_to_create_the_live_database_implicitly(self) -> None:
        with mock.patch.dict(os.environ, {"CAREEROS_ALLOW_NEW_DB": ""}):
            with self.assertRaisesRegex(RuntimeError, "refusing to create"):
                db.connect()
        self.assertFalse(config.DB_PATH.exists())

    def test_explicit_path_or_env_flag_allows_creation(self) -> None:
        conn = db.connect(config.DB_PATH)  # explicit path: tests and deliberate setups
        conn.close()
        self.assertTrue(config.DB_PATH.exists())
        config.DB_PATH.unlink()
        with mock.patch.dict(os.environ, {"CAREEROS_ALLOW_NEW_DB": "1"}):
            conn = db.connect()
            conn.close()
        self.assertTrue(config.DB_PATH.exists())

    def test_restore_refuses_tiny_files_and_existing_live_without_force(self) -> None:
        conn = db.connect(config.DB_PATH)
        db.migrate(conn)
        conn.execute("INSERT INTO sources (id, path, kind) VALUES ('s', 'p', 'k')")
        conn.commit()
        conn.close()
        made = backup.backup()
        self.assertTrue(made.exists())
        self.assertEqual(backup.list_backups()[0], made)

        tiny = self.temp / "tiny.sqlite"
        tiny.write_bytes(b"\0" * 4096)
        with self.assertRaises(ValueError):
            backup.restore(tiny, force=True)
        with self.assertRaises(FileExistsError):
            backup.restore(made)

        config.DB_PATH.unlink()
        restored = backup.restore(made)
        self.assertEqual(restored, config.DB_PATH)
        conn = db.connect(config.DB_PATH)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0], 1)
        conn.close()
        # force path: the current live file is backed up before being replaced
        before = len(backup.list_backups())
        backup.restore(made, force=True)
        self.assertEqual(len(backup.list_backups()), before + 1)


if __name__ == "__main__":
    unittest.main()
