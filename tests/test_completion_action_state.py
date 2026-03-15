from __future__ import annotations

import unittest

from app.ui.completion_action_state import completion_actions_state


class TestCompletionActionState(unittest.TestCase):
    def test_no_image_state(self) -> None:
        state = completion_actions_state(has_image=False, current_status="")
        self.assertFalse(state["set_completed_enabled"])
        self.assertFalse(state["toggle_enabled"])
        self.assertEqual(state["toggle_text"], "Mark Current Image &Completed")

    def test_completed_state(self) -> None:
        state = completion_actions_state(has_image=True, current_status="completed")
        self.assertFalse(state["set_completed_enabled"])
        self.assertTrue(state["set_in_progress_enabled"])
        self.assertEqual(state["toggle_text"], "Mark Current Image &In Progress")

    def test_in_progress_state(self) -> None:
        state = completion_actions_state(has_image=True, current_status="in_progress")
        self.assertTrue(state["set_completed_enabled"])
        self.assertFalse(state["set_in_progress_enabled"])
        self.assertEqual(state["toggle_text"], "Mark Current Image &Completed")


if __name__ == "__main__":
    unittest.main()
