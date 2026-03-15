from __future__ import annotations

import unittest
from pathlib import Path

from app.ui.inference_flow import build_inference_request, labels_added_hint, validate_inference_run


class TestInferenceFlow(unittest.TestCase):
    def test_validate_inference_run(self) -> None:
        ok, reason = validate_inference_run(inference_ok=False, inference_error="bad", model_path="m", current_image=Path("x.jpg"))
        self.assertFalse(ok)
        self.assertEqual(reason, "bad")

        ok, reason = validate_inference_run(inference_ok=True, inference_error="", model_path="", current_image=Path("x.jpg"))
        self.assertFalse(ok)
        self.assertEqual(reason, "model-missing")

        ok, reason = validate_inference_run(inference_ok=True, inference_error="", model_path="m", current_image=None)
        self.assertFalse(ok)
        self.assertEqual(reason, "image-missing")

        ok, reason = validate_inference_run(inference_ok=True, inference_error="", model_path="m", current_image=Path("x.jpg"))
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_build_request_and_hint(self) -> None:
        req = build_inference_request(model_path="m", image_path=Path("x.jpg"), conf=0.7, class_filter=[1], use_obb=True)
        self.assertEqual(req["model_path"], "m")
        self.assertEqual(req["class_filter"], [1])
        self.assertEqual(labels_added_hint(5), "Model added 5 label(s).")


if __name__ == "__main__":
    unittest.main()
