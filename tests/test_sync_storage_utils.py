from __future__ import annotations

import unittest

from sync_backend.sync_core import (
    image_read_url,
    normalize_cloudfront_invalidation_paths,
    s3_object_key,
)


class TestSyncStorageUtils(unittest.TestCase):
    def test_s3_object_key(self) -> None:
        self.assertEqual(s3_object_key("datasets", "p1", "images/a.jpg"), "datasets/p1/images/a.jpg")
        self.assertEqual(s3_object_key("", "p1", "images/a.jpg"), "p1/images/a.jpg")

    def test_image_read_url(self) -> None:
        out = image_read_url("https://cdn.example.com", "datasets/p 1/images/a b.jpg")
        self.assertEqual(out, "https://cdn.example.com/datasets/p%201/images/a%20b.jpg")

    def test_normalize_cloudfront_invalidation_paths(self) -> None:
        paths = normalize_cloudfront_invalidation_paths(["a/b.jpg", "/a/b.jpg", "", "a/c.jpg"])
        self.assertEqual(paths, ["/a/b.jpg", "/a/c.jpg"])

    def test_normalize_cloudfront_invalidation_paths_large(self) -> None:
        keys = [f"p1/images/{i}.jpg" for i in range(901)]
        out = normalize_cloudfront_invalidation_paths(keys)
        self.assertEqual(out, ["/p1/images/*"])


if __name__ == "__main__":
    unittest.main()


