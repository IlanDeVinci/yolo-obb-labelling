from __future__ import annotations

import unittest
from pathlib import Path

from app.ui.completion_state import (
    completion_status_label,
    ensure_selected_images,
    selection_completion_hint,
    toggle_completion_target,
)


class TestCompletionState(unittest.TestCase):
    def test_completion_status_label(self) -> None:
        self.assertEqual(completion_status_label("completed"), "Completed")
        self.assertEqual(completion_status_label("unknown"), "unknown")

    def test_toggle_completion_target(self) -> None:
        self.assertEqual(toggle_completion_target("completed"), "in_progress")
        self.assertEqual(toggle_completion_target("in_progress"), "completed")

    def test_ensure_selected_images(self) -> None:
        a = Path("a.jpg")
        b = Path("b.jpg")
        self.assertEqual(ensure_selected_images([a, b], None), [a, b])
        self.assertEqual(ensure_selected_images([], a), [a])
        self.assertEqual(ensure_selected_images([], None), [])

    def test_selection_completion_hint(self) -> None:
        a = Path("a.jpg")
        b = Path("b.jpg")
        self.assertEqual(selection_completion_hint([a], "completed"), "a.jpg: Completed")
        self.assertEqual(selection_completion_hint([a, b], "completed"), "2 images set as Completed")


if __name__ == "__main__":
    unittest.main()
