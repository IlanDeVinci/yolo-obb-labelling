from __future__ import annotations

import unittest

from app.ui.inference_messages import build_missing_inference_message


class TestInferenceMessages(unittest.TestCase):
    def test_build_missing_inference_message(self) -> None:
        title, text = build_missing_inference_message(
            inference_error="WinError 1114",
            sys_executable="python",
            diag_log_path="diag.log",
        )
        self.assertEqual(title, "ultralytics indisponible")
        self.assertIn("requirements-inference.txt", text)
        self.assertIn("WinError 1114", text)


if __name__ == "__main__":
    unittest.main()
