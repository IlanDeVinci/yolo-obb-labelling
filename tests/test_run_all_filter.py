from __future__ import annotations

import unittest
from pathlib import Path

from app.ui.run_all_filter import filter_images_for_run_all


class TestRunAllFilter(unittest.TestCase):
    def test_filter_images_for_run_all(self) -> None:
        images = [Path("a.jpg"), Path("b.jpg"), Path("c.jpg")]
        statuses = {"a.jpg": "completed", "b.jpg": "yolo", "c.jpg": "in_progress"}
        out, skipped_completed, skipped_yolo = filter_images_for_run_all(images, lambda p: statuses.get(p.name, ""))
        self.assertEqual([p.name for p in out], ["c.jpg"])
        self.assertEqual(skipped_completed, 1)
        self.assertEqual(skipped_yolo, 1)


if __name__ == "__main__":
    unittest.main()
