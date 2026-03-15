from __future__ import annotations

import sqlite3
import threading
import unittest

from sync_backend.sync_core import (
    compute_backup_retention_policy,
    set_backup_retention_policy,
    setting_get,
    setting_set,
)


class TestSyncSettingsStore(unittest.TestCase):
    def _make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)"
        )
        conn.commit()
        return conn

    def test_setting_get_set_roundtrip(self) -> None:
        conn = self._make_conn()
        lock = threading.Lock()
        self.assertEqual(setting_get(conn=conn, key="x", default_value="d"), "d")

        setting_set(conn=conn, db_lock=lock, key="x", value="v", updated_at_ms=42)
        self.assertEqual(setting_get(conn=conn, key="x", default_value="d"), "v")

    def test_compute_backup_retention_policy_days(self) -> None:
        value, unit, days = compute_backup_retention_policy(
            raw_unit="days",
            raw_value="30",
            backup_retention_days_default=14,
        )
        self.assertEqual((value, unit, days), (30, "days", 30))

    def test_compute_backup_retention_policy_invalid_input(self) -> None:
        value, unit, days = compute_backup_retention_policy(
            raw_unit="invalid",
            raw_value="abc",
            backup_retention_days_default=14,
        )
        self.assertEqual((value, unit, days), (14, "days", 14))

    def test_set_backup_retention_policy_months(self) -> None:
        conn = self._make_conn()
        lock = threading.Lock()

        result = set_backup_retention_policy(
            conn=conn,
            db_lock=lock,
            retention_value=2,
            retention_unit="months",
            updated_at_ms=123,
        )
        self.assertEqual(result["retentionValue"], 2)
        self.assertEqual(result["retentionUnit"], "months")
        self.assertEqual(result["retentionDays"], 60)
        self.assertEqual(setting_get(conn=conn, key="backup_retention_unit"), "months")
        self.assertEqual(setting_get(conn=conn, key="backup_retention_value"), "2")

    def test_set_backup_retention_policy_invalid_unit(self) -> None:
        conn = self._make_conn()
        lock = threading.Lock()
        with self.assertRaises(ValueError):
            set_backup_retention_policy(
                conn=conn,
                db_lock=lock,
                retention_value=2,
                retention_unit="weeks",
                updated_at_ms=1,
            )


if __name__ == "__main__":
    unittest.main()


