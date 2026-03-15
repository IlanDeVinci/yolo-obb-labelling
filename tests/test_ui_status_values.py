from __future__ import annotations

import unittest

from app.ui.status_values import (
    CLOUD_IMAGE_ACCESS_MODES,
    VALID_COMPLETION_STATUSES,
    is_cloud_only_mode,
    is_hybrid_image_mode,
    is_remote_image_mode,
    normalize_completion_status,
    normalize_image_access_mode,
)


class TestUiStatusValues(unittest.TestCase):
    def test_normalize_completion_status(self) -> None:
        self.assertEqual(normalize_completion_status("YOLO"), "yolo")
        self.assertEqual(normalize_completion_status("unknown"), "")

    def test_normalize_image_access_mode(self) -> None:
        self.assertEqual(normalize_image_access_mode("CLOUD_ONLY"), "cloud_only")
        self.assertEqual(normalize_image_access_mode("invalid"), "local")

    def test_is_remote_image_mode(self) -> None:
        self.assertTrue(is_remote_image_mode("cloud_only"))
        self.assertTrue(is_remote_image_mode("hybrid"))
        self.assertFalse(is_remote_image_mode("local"))

    def test_cloud_only_and_hybrid_predicates(self) -> None:
        self.assertTrue(is_cloud_only_mode("cloud_only"))
        self.assertFalse(is_cloud_only_mode("hybrid"))
        self.assertTrue(is_hybrid_image_mode("hybrid"))
        self.assertFalse(is_hybrid_image_mode("local"))

    def test_exports_include_expected_values(self) -> None:
        self.assertIn("hybrid", CLOUD_IMAGE_ACCESS_MODES)
        self.assertIn("completed", VALID_COMPLETION_STATUSES)


if __name__ == "__main__":
    unittest.main()
