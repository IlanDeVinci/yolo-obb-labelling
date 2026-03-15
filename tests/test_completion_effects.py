from __future__ import annotations

import unittest

from app.ui.completion_effects import (
    should_auto_mark_in_progress,
    should_persist_completion_locally,
)


class TestCompletionEffects(unittest.TestCase):
    def test_should_persist_completion_locally(self) -> None:
        self.assertTrue(should_persist_completion_locally("local"))
        self.assertTrue(should_persist_completion_locally("hybrid"))
        self.assertFalse(should_persist_completion_locally("cloud_only"))

    def test_should_auto_mark_in_progress(self) -> None:
        self.assertFalse(should_auto_mark_in_progress(has_labels=False, current_status=""))
        self.assertFalse(should_auto_mark_in_progress(has_labels=True, current_status="completed"))
        self.assertTrue(should_auto_mark_in_progress(has_labels=True, current_status=""))


if __name__ == "__main__":
    unittest.main()
