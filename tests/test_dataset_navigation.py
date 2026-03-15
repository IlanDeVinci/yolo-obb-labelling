from __future__ import annotations

import unittest
from pathlib import Path

from app.ui.dataset_navigation import (
    apply_dataset_class_names,
    load_dataset_yaml_safe,
    split_images_for_key,
    update_project_dataset_metadata,
)


class _DatasetOk:
    def __init__(self) -> None:
        self.loaded: Path | None = None

    def load_from_yaml(self, yaml_path: Path) -> None:
        self.loaded = yaml_path


class _DatasetFail:
    def load_from_yaml(self, yaml_path: Path) -> None:
        raise RuntimeError(f"bad: {yaml_path}")


class _Project:
    def __init__(self) -> None:
        self.yaml_path = ""
        self.class_names: list[str] = []


class TestDatasetNavigation(unittest.TestCase):
    def test_load_dataset_yaml_safe(self) -> None:
        ds = _DatasetOk()
        err = load_dataset_yaml_safe(ds, Path("data.yaml"))
        self.assertIsNone(err)

        err2 = load_dataset_yaml_safe(_DatasetFail(), Path("bad.yaml"))
        self.assertIn("bad:", str(err2))

    def test_apply_dataset_class_names(self) -> None:
        panel: list[list[str]] = []
        canvas: list[list[str]] = []
        label: list[list[str]] = []
        apply_dataset_class_names(
            ["a", "b"],
            set_class_panel=panel.append,
            set_canvas_names=canvas.append,
            set_label_list_names=label.append,
        )
        self.assertEqual(panel[0], ["a", "b"])
        self.assertEqual(canvas[0], ["a", "b"])
        self.assertEqual(label[0], ["a", "b"])

    def test_update_project_dataset_metadata(self) -> None:
        p = _Project()
        changed = update_project_dataset_metadata(p, Path("x.yaml"), ["c"])
        self.assertTrue(changed)
        self.assertEqual(p.yaml_path, "x.yaml")
        self.assertEqual(p.class_names, ["c"])
        self.assertFalse(update_project_dataset_metadata(None, Path("x.yaml"), ["c"]))

    def test_split_images_for_key(self) -> None:
        train = [Path("t1.jpg")]
        val = [Path("v1.jpg")]
        self.assertEqual(split_images_for_key("train", train, val), train)
        self.assertEqual(split_images_for_key("val", train, val), val)


if __name__ == "__main__":
    unittest.main()
