"""Dataset YAML helpers."""
from __future__ import annotations
from pathlib import Path
import yaml


def load_dataset_yaml(yaml_path: Path) -> dict:
    with open(yaml_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_dataset_yaml(data: dict, yaml_path: Path) -> None:
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def build_dataset_dict(
    dataset_root: Path,
    class_names: list[str],
    train_rel: str = "images/train",
    val_rel: str = "images/val",
) -> dict:
    """Build the YAML dict in ultralytics format."""
    names = {i: name for i, name in enumerate(class_names)}
    return {
        "path": str(dataset_root),
        "train": train_rel,
        "val": val_rel,
        "names": names,
    }
