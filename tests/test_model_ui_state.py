from __future__ import annotations

import unittest

from app.ui.model_ui_state import model_indicator_state


class TestModelUiState(unittest.TestCase):
    def test_no_model(self) -> None:
        text, tooltip = model_indicator_state("", [])
        self.assertEqual(text, "&Load Model…")
        self.assertEqual(tooltip, "No model loaded")

    def test_model_with_classes(self) -> None:
        text, tooltip = model_indicator_state("/a/b/model.pt", [1, 3])
        self.assertEqual(text, "✓ &Load Model…")
        self.assertIn("model.pt", tooltip)
        self.assertIn("1, 3", tooltip)


if __name__ == "__main__":
    unittest.main()
