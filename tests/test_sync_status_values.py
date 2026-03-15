from __future__ import annotations

import unittest

from sync_backend.sync_core import (
    VALID_IMAGE_ACCESS_MODES,
    VALID_IMAGE_STATUSES,
    is_cloud_only_image_access_mode,
    is_remote_image_access_mode,
    normalize_image_access_mode_value as normalize_image_access_mode,
    normalize_image_status,
)


class TestSyncStatusValues(unittest.TestCase):
    def test_normalize_image_status(self) -> None:
        self.assertEqual(normalize_image_status("Completed"), "completed")
        self.assertEqual(normalize_image_status("bad"), "")
        self.assertEqual(normalize_image_status(None, default="in_progress"), "in_progress")

    def test_normalize_image_access_mode(self) -> None:
        self.assertEqual(normalize_image_access_mode("HYBRID"), "hybrid")
        self.assertEqual(normalize_image_access_mode("bad"), "local")
        self.assertEqual(normalize_image_access_mode(None, default="cloud_only"), "cloud_only")

    def test_is_remote_image_access_mode(self) -> None:
        self.assertTrue(is_remote_image_access_mode("hybrid"))
        self.assertTrue(is_remote_image_access_mode("cloud_only"))
        self.assertFalse(is_remote_image_access_mode("local"))

    def test_is_cloud_only_image_access_mode(self) -> None:
        self.assertTrue(is_cloud_only_image_access_mode("cloud_only"))
        self.assertFalse(is_cloud_only_image_access_mode("hybrid"))
        self.assertFalse(is_cloud_only_image_access_mode("local"))

    def test_exports_include_expected_values(self) -> None:
        self.assertIn("local", VALID_IMAGE_ACCESS_MODES)
        self.assertIn("in_progress", VALID_IMAGE_STATUSES)


if __name__ == "__main__":
    unittest.main()


