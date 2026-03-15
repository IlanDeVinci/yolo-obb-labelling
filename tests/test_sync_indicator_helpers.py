from __future__ import annotations

import unittest

from app.ui.sync_indicator_helpers import connected_cloud_mode_state, connected_sync_primary_text, disconnected_sync_state


class TestSyncIndicatorHelpers(unittest.TestCase):
    def test_disconnected_sync_state(self) -> None:
        out = disconnected_sync_state(error="", cloud_workflow_enabled=True)
        self.assertEqual(out["sync_text"], "SYNC: setup required")
        self.assertEqual(out["login_text"], "LOGIN: required")

    def test_connected_sync_primary_text(self) -> None:
        text, style = connected_sync_primary_text(users=2, active_file="labels/a.txt", pending_status_sync=0, status_syncing=False)
        self.assertIn("2 online", text)
        self.assertIn("#86cc9f", style)

    def test_connected_cloud_mode_state(self) -> None:
        text, style = connected_cloud_mode_state(cloud_only=True, hybrid=False)
        self.assertEqual(text, "IMAGES: Cloud-Only")
        self.assertIn("#63b38f", style)

        text, style = connected_cloud_mode_state(cloud_only=False, hybrid=True)
        self.assertEqual(text, "IMAGES: Hybrid")
        self.assertIn("#74a2d4", style)

        text, style = connected_cloud_mode_state(cloud_only=False, hybrid=False)
        self.assertEqual(text, "IMAGES: local")
        self.assertIn("#7b9db8", style)


if __name__ == "__main__":
    unittest.main()


