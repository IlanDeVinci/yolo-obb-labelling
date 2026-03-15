from __future__ import annotations

import unittest

from sync_backend.sync_core import (
    normalize_image_access_mode,
    normalize_storage_mode,
    project_uses_s3_images,
)


class TestSyncProjectModes(unittest.TestCase):
    def test_mode_normalization(self) -> None:
        self.assertEqual(normalize_storage_mode("S3"), "s3")
        self.assertEqual(normalize_storage_mode("unknown"), "auto")
        self.assertEqual(normalize_image_access_mode("HYBRID"), "hybrid")
        self.assertEqual(normalize_image_access_mode("unknown"), "local")

    def test_project_uses_s3_by_access_mode(self) -> None:
        self.assertFalse(project_uses_s3_images(access_mode="local", storage_mode="s3", s3_enabled=True))
        self.assertTrue(project_uses_s3_images(access_mode="hybrid", storage_mode="db", s3_enabled=True))
        with self.assertRaises(ValueError):
            project_uses_s3_images(access_mode="cloud_only", storage_mode="auto", s3_enabled=False)

    def test_project_uses_s3_by_storage_mode(self) -> None:
        self.assertFalse(project_uses_s3_images(access_mode="invalid", storage_mode="db", s3_enabled=True))
        # Invalid access mode normalizes to local, so it disables S3 usage regardless of storage mode.
        self.assertFalse(project_uses_s3_images(access_mode="invalid", storage_mode="s3", s3_enabled=True))
        self.assertFalse(project_uses_s3_images(access_mode="invalid", storage_mode="s3", s3_enabled=False))
        self.assertFalse(project_uses_s3_images(access_mode="invalid", storage_mode="auto", s3_enabled=True))
        self.assertFalse(project_uses_s3_images(access_mode="invalid", storage_mode="auto", s3_enabled=False))

        # Storage mode decisions apply when access mode is also auto/invalid-normalized and not forcing local.
        self.assertTrue(project_uses_s3_images(access_mode="hybrid", storage_mode="s3", s3_enabled=True))


if __name__ == "__main__":
    unittest.main()


