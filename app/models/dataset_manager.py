"""Dataset YAML + folder structure management."""
from __future__ import annotations
from pathlib import Path

from app.utils.yaml_io import load_dataset_yaml, save_dataset_yaml, build_dataset_dict

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}


class DatasetManager:
    def __init__(self) -> None:
        self.yaml_path: Path | None = None
        self.dataset_root: Path | None = None
        self.class_names: list[str] = []
        self.train_images: list[Path] = []
        self.val_images: list[Path] = []

    # ------------------------------------------------------------------
    # Create a new dataset on disk
    # ------------------------------------------------------------------

    def create_dataset(
        self,
        base_path: Path,
        name: str,
        class_names: list[str],
    ) -> Path:
        """Create the full YOLO dataset folder structure and YAML file.

        Returns the path to the created YAML file.
        """
        root = base_path / name
        for split in ("train", "val"):
            (root / "images" / split).mkdir(parents=True, exist_ok=True)
            (root / "labels" / split).mkdir(parents=True, exist_ok=True)

        data = build_dataset_dict(root, class_names)
        yaml_path = root / f"{name}.yaml"
        save_dataset_yaml(data, yaml_path)

        self.yaml_path = yaml_path
        self.dataset_root = root
        self.class_names = class_names
        self.train_images = []
        self.val_images = []
        return yaml_path

    # ------------------------------------------------------------------
    # Load existing dataset
    # ------------------------------------------------------------------

    def load_from_yaml(self, yaml_path: Path) -> None:
        """Load dataset config from a YAML file (ultralytics format)."""
        data = load_dataset_yaml(yaml_path)

        # Resolve dataset root path
        raw_path = data.get("path", "")
        if raw_path:
            root = Path(raw_path)
            if not root.is_absolute():
                root = (yaml_path.parent / root).resolve()
        else:
            root = yaml_path.parent

        self.yaml_path = yaml_path
        self.dataset_root = root

        # Parse class names — supports both dict {0: name} and list [name, ...]
        names_raw = data.get("names", {})
        if isinstance(names_raw, dict):
            max_idx = max(int(k) for k in names_raw) if names_raw else -1
            self.class_names = ["" for _ in range(max_idx + 1)]
            for k, v in names_raw.items():
                self.class_names[int(k)] = str(v)
        elif isinstance(names_raw, list):
            self.class_names = [str(n) for n in names_raw]
        else:
            self.class_names = []

        # Scan image directories
        train_rel = data.get("train", "images/train")
        val_rel = data.get("val", "images/val")
        self.train_images = self._scan_images(root / train_rel)
        self.val_images = self._scan_images(root / val_rel)

    # ------------------------------------------------------------------
    # Load from plain folder (no YAML)
    # ------------------------------------------------------------------

    def load_from_folder(self, folder: Path, class_names: list[str] | None = None) -> None:
        """Load images from an arbitrary folder without a YAML file."""
        self.yaml_path = None
        self.dataset_root = folder
        self.class_names = class_names or []
        self.train_images = self._scan_images(folder)
        self.val_images = []

    # ------------------------------------------------------------------
    # Save current class names back to YAML
    # ------------------------------------------------------------------

    def save_yaml(self) -> None:
        if self.yaml_path is None or self.dataset_root is None:
            return
        data = build_dataset_dict(self.dataset_root, self.class_names)
        save_dataset_yaml(data, self.yaml_path)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _scan_images(directory: Path) -> list[Path]:
        if not directory.is_dir():
            return []
        images = [
            p for p in directory.iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
        ]
        return sorted(images)
