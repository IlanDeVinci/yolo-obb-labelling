from __future__ import annotations

import unittest
from pathlib import Path

from app.ui.split_visibility import visible_split_images


class TestSplitVisibility(unittest.TestCase):
    def test_visible_split_images_no_filter(self) -> None:
        images = [Path("a.jpg"), Path("b.jpg")]
        out = visible_split_images(
            images,
            active_team_member="",
            is_distributed=False,
            get_member_images=None,
        )
        self.assertEqual(out, images)

    def test_visible_split_images_with_filter(self) -> None:
        images = [Path("a.jpg"), Path("b.jpg")]

        def getter(_member: str, imgs: list[Path]) -> list[Path]:
            return [p for p in imgs if p.name == "b.jpg"]

        out = visible_split_images(
            images,
            active_team_member="alice",
            is_distributed=True,
            get_member_images=getter,
        )
        self.assertEqual([p.name for p in out], ["b.jpg"])


if __name__ == "__main__":
    unittest.main()
