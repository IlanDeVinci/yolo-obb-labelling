from __future__ import annotations

import unittest

try:
    from fastapi import HTTPException
    from sync_backend.sync_core import (
        estimate_b64_size_bytes,
        is_image_path,
        is_label_text_path,
        normalize_path,
        requires_explicit_lock,
        sanitize_archive_member_path,
    )
    _FASTAPI_READY = True
except ModuleNotFoundError:
    HTTPException = Exception
    _FASTAPI_READY = False


@unittest.skipUnless(_FASTAPI_READY, "FastAPI dependency not installed in this environment")
class TestSyncValidators(unittest.TestCase):
    def test_normalize_path_valid(self) -> None:
        self.assertEqual(normalize_path("images\\a\\b.jpg"), "images/a/b.jpg")

    def test_normalize_path_invalid(self) -> None:
        with self.assertRaises(HTTPException):
            normalize_path("../bad.txt")

    def test_sanitize_archive_member_path(self) -> None:
        self.assertEqual(sanitize_archive_member_path("/folder\\file.txt"), "folder/file.txt")
        with self.assertRaises(HTTPException):
            sanitize_archive_member_path("../../bad")

    def test_estimate_b64_size_bytes(self) -> None:
        self.assertEqual(estimate_b64_size_bytes(""), 0)
        self.assertEqual(estimate_b64_size_bytes("TQ=="), 1)

    def test_image_and_label_path_detection(self) -> None:
        self.assertTrue(is_image_path("images/a.PNG"))
        self.assertFalse(is_image_path("images/a.txt"))
        self.assertTrue(is_label_text_path("project/labels/a.txt"))
        self.assertFalse(is_label_text_path("project/images/a.txt"))

    def test_requires_explicit_lock(self) -> None:
        self.assertTrue(requires_explicit_lock("project/labels/item.txt"))
        self.assertFalse(requires_explicit_lock("project/images/item.jpg"))


if __name__ == "__main__":
    unittest.main()


