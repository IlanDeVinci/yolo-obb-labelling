from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from sync_backend.sync_core import (
    cleanup_old_backups,
    compute_sha256_for_file,
    dry_run_backup_restore,
    list_backups,
    safe_backup_path_from_name,
)


class TestSyncBackupUtils(unittest.TestCase):
    def test_safe_backup_path_from_name(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            backup_dir = Path(td)
            file_path = backup_dir / "sync-20260315-120000-daily.db"
            file_path.write_bytes(b"data")

            resolved = safe_backup_path_from_name(backup_dir, file_path.name)
            self.assertEqual(resolved, file_path.resolve())

            with self.assertRaises(ValueError):
                safe_backup_path_from_name(backup_dir, "../bad.db")
            with self.assertRaises(FileNotFoundError):
                safe_backup_path_from_name(backup_dir, "sync-20260315-120000-missing.db")

    def test_list_and_cleanup_backups(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            backup_dir = Path(td)
            file_new = backup_dir / "sync-20991231-235959-daily.db"
            file_old = backup_dir / "sync-20000101-000000-daily.db"
            file_new.write_bytes(b"n")
            file_old.write_bytes(b"o")

            now_ts = time.time()
            old_ts = now_ts - (10 * 24 * 60 * 60)
            file_new.touch()
            file_old.touch()
            # Force deterministic ages: keep one fresh and one beyond retention.
            file_new_ts = (now_ts, now_ts)
            file_old_ts = (old_ts, old_ts)
            os.utime(file_new, file_new_ts)
            os.utime(file_old, file_old_ts)

            items = list_backups(backup_dir)
            self.assertEqual(len(items), 2)
            self.assertTrue(any(item["name"] == file_new.name for item in items))

            cleanup_old_backups(backup_dir, retention_days=2)
            self.assertTrue(file_new.exists())
            self.assertFalse(file_old.exists())

    def test_compute_sha256_for_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.db"
            p.write_bytes(b"abc")
            digest = compute_sha256_for_file(p)
            self.assertEqual(len(digest), 64)

    def test_dry_run_backup_restore_sqlite_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sync-20260315-120000-daily.db"
            conn = sqlite3.connect(p)
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
            conn.commit()
            conn.close()

            result = dry_run_backup_restore(p)
            self.assertTrue(result["headerValid"])
            self.assertEqual(result["backupName"], p.name)
            self.assertEqual(len(result["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()


