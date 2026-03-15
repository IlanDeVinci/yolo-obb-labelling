from __future__ import annotations

import unittest

from app.ui.cloud_menu_state import cloud_menu_status_text


class TestCloudMenuState(unittest.TestCase):
    def test_cloud_menu_status_text(self) -> None:
        kind, text = cloud_menu_status_text(connected=True, enabled=True, error="")
        self.assertEqual(kind, "apply")
        self.assertIn("connected", text)

        kind, text = cloud_menu_status_text(connected=False, enabled=True, error="oops")
        self.assertEqual(kind, "warning")
        self.assertIn("error", text)


if __name__ == "__main__":
    unittest.main()
