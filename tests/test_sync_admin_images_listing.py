from __future__ import annotations

import unittest

from sync_backend.sync_core import (
    build_admin_image_items,
    normalize_admin_image_sort,
    sort_admin_image_items,
)


class TestSyncAdminImagesListing(unittest.TestCase):
    def test_normalize_and_build_and_sort(self) -> None:
        sort_key, order = normalize_admin_image_sort("mtime", "desc")
        self.assertEqual((sort_key, order), ("mtime", "desc"))

        items = build_admin_image_items(
            db_rows={"images/a.jpg": {"mtimeMs": 1, "sizeBytes": 10, "updatedAt": 2}},
            s3_rows={"images/b.jpg": {"mtimeMs": 3, "sizeBytes": 5}},
            status_by_name={"a.jpg": "completed"},
        )
        self.assertEqual(len(items), 2)
        sort_admin_image_items(items, sort_key="mtime", sort_order="desc")
        self.assertEqual(items[0]["path"], "images/b.jpg")


if __name__ == "__main__":
    unittest.main()


