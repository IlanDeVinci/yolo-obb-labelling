from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from app.ui.image_sorting import image_sort_label, sorted_image_paths


class TestImageSorting(unittest.TestCase):
    def test_image_sort_label(self) -> None:
        self.assertEqual(image_sort_label("name_asc"), "Name A-Z")
        self.assertEqual(image_sort_label("mtime_desc"), "Newest First")
        self.assertEqual(image_sort_label("unknown"), "Name A-Z")

    def test_sorted_image_paths_name(self) -> None:
        imgs = [Path("b.jpg"), Path("a.jpg")]
        out = sorted_image_paths(imgs, mode="name_asc", project_relative_path_for_sync=lambda p: p.as_posix())
        self.assertEqual([p.name for p in out], ["a.jpg", "b.jpg"])

    def test_sorted_image_paths_size_with_cloud_meta(self) -> None:
        imgs = [Path("b.jpg"), Path("a.jpg")]
        meta = {"b.jpg": (20, 2), "a.jpg": (10, 1)}
        out = sorted_image_paths(
            imgs,
            mode="size_asc",
            project_relative_path_for_sync=lambda p: p.as_posix(),
            cloud_meta=meta,
        )
        self.assertEqual([p.name for p in out], ["a.jpg", "b.jpg"])

    def test_sorted_image_paths_mtime_local(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / "a.jpg"
            b = Path(td) / "b.jpg"
            a.write_bytes(b"a")
            b.write_bytes(b"b")
            now = time.time()
            a.touch()
            b.touch()
            # Make b newer than a.
            a_ts = (now - 10, now - 10)
            b_ts = (now, now)
            import os

            os.utime(a, a_ts)
            os.utime(b, b_ts)
            out = sorted_image_paths(
                [a, b],
                mode="mtime_desc",
                project_relative_path_for_sync=lambda p: p.name,
            )
            self.assertEqual([p.name for p in out], ["b.jpg", "a.jpg"])


if __name__ == "__main__":
    unittest.main()
