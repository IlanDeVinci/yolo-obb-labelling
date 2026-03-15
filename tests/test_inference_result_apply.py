from __future__ import annotations

import unittest

from app.ui.inference_result_apply import apply_inference_labels


class TestInferenceResultApply(unittest.TestCase):
    def test_apply_inference_labels(self) -> None:
        added_m = []
        added_c = []
        count = apply_inference_labels(
            [1, 2, 3],
            add_label_to_manager=added_m.append,
            add_label_to_canvas=added_c.append,
        )
        self.assertEqual(count, 3)
        self.assertEqual(added_m, [1, 2, 3])
        self.assertEqual(added_c, [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
