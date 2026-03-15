from __future__ import annotations

import unittest

from app.ui.cloud_sync_config_builder import build_cloud_sync_config


class TestCloudSyncConfigBuilder(unittest.TestCase):
    def test_build_cloud_sync_config_defaults(self) -> None:
        config = build_cloud_sync_config({})
        self.assertFalse(config.enabled)
        self.assertEqual(config.server_url, "")
        self.assertEqual(config.project_id, "")
        self.assertEqual(config.poll_seconds, 1.2)
        self.assertEqual(config.image_cache_max_mb, 2048)
        self.assertEqual(config.image_cache_ttl_hours, 24)
        self.assertEqual(config.image_prefetch_count, 8)

    def test_build_cloud_sync_config_values(self) -> None:
        config = build_cloud_sync_config(
            {
                "enabled": True,
                "server_url": "https://api.example",
                "project_id": "proj-1",
                "project_password": "pp",
                "username": "u",
                "user_password": "pw",
                "poll_seconds": "2.5",
                "image_cache_dir": "C:/cache",
                "image_cache_max_mb": "512",
                "image_cache_ttl_hours": "12",
                "image_prefetch_count": "16",
            }
        )
        self.assertTrue(config.enabled)
        self.assertEqual(config.server_url, "https://api.example")
        self.assertEqual(config.project_id, "proj-1")
        self.assertEqual(config.project_password, "pp")
        self.assertEqual(config.username, "u")
        self.assertEqual(config.user_password, "pw")
        self.assertEqual(config.poll_seconds, 2.5)
        self.assertEqual(config.image_cache_dir, "C:/cache")
        self.assertEqual(config.image_cache_max_mb, 512)
        self.assertEqual(config.image_cache_ttl_hours, 12)
        self.assertEqual(config.image_prefetch_count, 16)


if __name__ == "__main__":
    unittest.main()
