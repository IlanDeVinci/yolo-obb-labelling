from __future__ import annotations

import unittest

from sync_backend.sync_core import (
    bbox_to_corners,
    is_bb_label_path,
    is_obb_label_path,
    label_paths_for_image,
    label_stem_from_path,
    parse_yolo_label_rows,
    split_label_text_by_format,
)


class TestSyncLabelUtils(unittest.TestCase):
    def test_label_paths_for_image(self) -> None:
        paths = label_paths_for_image("datasets/project/images/sub/a.jpg")
        self.assertIn("datasets/project/labels/sub/a.txt", paths)
        self.assertIn("datasets/project/labels/sub/BB/a.txt", paths)
        self.assertIn("datasets/project/labels/sub/OBB/a.txt", paths)

    def test_bbox_to_corners(self) -> None:
        corners = bbox_to_corners(0.5, 0.5, 0.2, 0.4)
        self.assertEqual(len(corners), 8)
        self.assertAlmostEqual(corners[0], 0.4)
        self.assertAlmostEqual(corners[1], 0.3)

    def test_parse_yolo_label_rows(self) -> None:
        raw = "0 0.5 0.5 0.2 0.4\n1 0.1 0.1 0.2 0.1 0.2 0.2 0.1 0.2"
        rows = parse_yolo_label_rows(raw)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["format"], "bbox")
        self.assertEqual(rows[1]["format"], "obb")

    def test_split_label_text_by_format(self) -> None:
        raw = "0 0.5 0.5 0.2 0.4\n1 0.1 0.1 0.2 0.1 0.2 0.2 0.1 0.2"
        bb, obb, bb_count, obb_count = split_label_text_by_format(raw)
        self.assertEqual(bb_count, 1)
        self.assertEqual(obb_count, 1)
        self.assertIn("0 0.5 0.5 0.2 0.4", bb)
        self.assertIn("1 0.1 0.1", obb)

    def test_label_stem_from_path(self) -> None:
        self.assertEqual(label_stem_from_path("labels/OBB/foo.txt"), "foo")

    def test_label_mode_path_helpers(self) -> None:
        self.assertTrue(is_bb_label_path("x/labels/BB/a.txt"))
        self.assertFalse(is_bb_label_path("x/labels/OBB/a.txt"))
        self.assertTrue(is_obb_label_path("x/labels/OBB/a.txt"))
        self.assertFalse(is_obb_label_path("x/labels/BB/a.txt"))


if __name__ == "__main__":
    unittest.main()


