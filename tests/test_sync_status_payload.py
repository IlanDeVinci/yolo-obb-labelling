from __future__ import annotations

import unittest

from sync_backend.sync_core import build_sync_status_payload


class TestSyncStatusPayload(unittest.TestCase):
    def test_build_sync_status_payload(self) -> None:
        payload = build_sync_status_payload(
            project_id="p",
            username="u",
            role="admin",
            is_admin=True,
            backup_dir="/tmp/backups",
            backup_retention_days=14,
            backup_retention_value=2,
            backup_retention_unit="weeks",
            image_access_mode="hybrid",
            active_file="labels/a.txt",
            online_users=3,
            latest_seq=10,
            latest_status_seq=22,
            locks=[{"path": "x"}],
            recent_backups=[{"name": "b"}],
            session_ttl_seconds=900,
            server_time="2026-03-15T00:00:00Z",
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["projectId"], "p")
        self.assertEqual(payload["onlineUsers"], 3)
        self.assertEqual(payload["latestSeq"], 10)
        self.assertEqual(payload["locks"][0]["path"], "x")


if __name__ == "__main__":
    unittest.main()


