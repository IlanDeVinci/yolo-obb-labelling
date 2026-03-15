from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def load_dataset_yaml_safe(dataset: Any, yaml_path: Path) -> str | None:
    try:
        dataset.load_from_yaml(yaml_path)
        return None
    except Exception as exc:  # noqa: BLE001
        return str(exc)


def apply_dataset_class_names(
    class_names: list[str],
    *,
    set_class_panel: Callable[[list[str]], None],
    set_canvas_names: Callable[[list[str]], None],
    set_label_list_names: Callable[[list[str]], None],
) -> None:
    names = list(class_names)
    set_class_panel(names)
    set_canvas_names(names)
    set_label_list_names(names)


def update_project_dataset_metadata(project: Any | None, yaml_path: Path, class_names: list[str]) -> bool:
    if project is None:
        return False
    project.yaml_path = str(yaml_path)
    project.class_names = list(class_names)
    return True


def split_images_for_key(split: str, train_images: list[Path], val_images: list[Path]) -> list[Path]:
    return list(train_images if split == "train" else val_images)
