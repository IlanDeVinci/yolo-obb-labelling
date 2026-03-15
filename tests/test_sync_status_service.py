from __future__ import annotations

import unittest

from sync_backend.sync_core import build_sync_status_response


class TestSyncStatusService(unittest.TestCase):
    def test_build_sync_status_response(self) -> None:
        def payload_builder(**kwargs):
            return kwargs

        out = build_sync_status_response(
            project_id="p1",
            username="u1",
            role="admin",
            is_admin=True,
            active_file="labels/a.txt",
            backup_dir="/backups",
            session_ttl_seconds=900,
            server_time="2026-03-15T00:00:00Z",
            list_project_locks=lambda project_id, order_desc: [{"project": project_id, "desc": order_desc}],
            online_users_count=lambda _project_id: 3,
            latest_change_seq=lambda _project_id: 10,
            latest_status_seq=lambda _project_id: 20,
            get_backup_retention_policy=lambda: (14, "days", 14),
            list_backups=lambda: [{"name": str(i)} for i in range(12)],
            get_project_image_access_mode=lambda _project_id: "hybrid",
            build_payload=payload_builder,
        )

        self.assertEqual(out["project_id"], "p1")
        self.assertEqual(out["online_users"], 3)
        self.assertEqual(out["latest_seq"], 10)
        self.assertEqual(len(out["recent_backups"]), 8)
        self.assertEqual(out["locks"][0]["project"], "p1")


if __name__ == "__main__":
    unittest.main()


