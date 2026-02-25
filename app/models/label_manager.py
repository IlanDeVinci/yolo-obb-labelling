"""Per-image label load/save with dirty tracking."""
from __future__ import annotations
from pathlib import Path
from typing import Callable

from app.models.obb_label import OBBLabel, BBoxLabel, Label

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}
OBB_SUBFOLDER = "OBB"
BB_SUBFOLDER = "BB"


class LabelManager:
    def __init__(self, use_obb: bool = True) -> None:
        self._labels: list[Label] = []
        self._label_path: Path | None = None
        self._image_path: Path | None = None
        self._dirty: bool = False
        self._on_changed: Callable[[], None] | None = None
        self._use_obb: bool = use_obb  # True = OBB mode, False = regular bbox mode

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def labels(self) -> list[Label]:
        return self._labels

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    @property
    def label_path(self) -> Path | None:
        return self._label_path

    @property
    def use_obb(self) -> bool:
        return self._use_obb

    def set_use_obb(self, use_obb: bool) -> None:
        """Switch between OBB and regular bbox mode."""
        self._use_obb = use_obb
        self._refresh_active_label_path()

    def set_on_changed(self, callback: Callable[[], None]) -> None:
        self._on_changed = callback

    def load_for_image(self, image_path: Path) -> None:
        """Load labels for the given image (clears current state first).

        Auto-detects format based on number of values per line:
        - 5 values (class + xywh) = BBoxLabel
        - 9 values (class + 8 coords) = OBBLabel
        """
        self._labels = []
        self._dirty = False
        self._image_path = image_path
        preferred, alternate, legacy = self._derive_label_paths(image_path)
        self._label_path = preferred

        source_path: Path | None = None
        for candidate in (preferred, alternate, legacy):
            if candidate.exists():
                source_path = candidate
                break

        if source_path is not None:
            try:
                lines = source_path.read_text(encoding="utf-8").splitlines()
                for line in lines:
                    line = line.strip()
                    if line:
                        try:
                            parts = line.split()
                            if len(parts) >= 9:
                                # OBB format: class_idx x1 y1 x2 y2 x3 y3 x4 y4
                                self._labels.append(OBBLabel.from_yolo_line(line))
                            elif len(parts) >= 5:
                                # Standard YOLO format: class_idx x_center y_center width height
                                self._labels.append(BBoxLabel.from_yolo_line(line))
                        except ValueError:
                            pass  # skip malformed lines
            except OSError:
                pass

        # Keep in-memory labels coherent with current mode to avoid
        # accidentally saving BB format into OBB files (or reverse).
        self._labels = self._normalize_labels_for_mode(self._labels)

        self._notify()

    def save(self) -> bool:
        """Write labels to the .txt file. Returns True on success."""
        self._refresh_active_label_path()
        if self._label_path is None:
            return False
        try:
            self._label_path.parent.mkdir(parents=True, exist_ok=True)
            # Defensive normalization: never write mismatched label format
            # into a mode-specific folder.
            self._labels = self._normalize_labels_for_mode(self._labels)
            content = "\n".join(lbl.to_yolo_line() for lbl in self._labels)
            self._label_path.write_text(content + ("\n" if content else ""), encoding="utf-8")
            self._dirty = False
            return True
        except OSError:
            return False

    def add_label(self, label: Label) -> None:
        self._labels.append(label)
        self.mark_dirty()

    def remove_label(self, label: Label) -> None:
        try:
            self._labels.remove(label)
            self.mark_dirty()
        except ValueError:
            pass

    def replace_labels(self, labels: list[Label], mark_dirty: bool = True) -> None:
        """Replace all in-memory labels at once.

        Useful for format conversions (OBB <-> BBox) that keep semantic labels
        but change representation.
        """
        self._labels = list(labels)
        if mark_dirty:
            self.mark_dirty()
        else:
            self._notify()

    def mark_dirty(self) -> None:
        self._dirty = True
        self._notify()

    def clear(self) -> None:
        self._labels = []
        self._label_path = None
        self._image_path = None
        self._dirty = False
        self._notify()

    def has_labels_for(self, image_path: Path) -> bool:
        preferred, alternate, legacy = self._derive_label_paths(image_path)
        for lp in (preferred, alternate, legacy):
            if lp.exists() and lp.stat().st_size > 0:
                return True
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _derive_label_paths(self, image_path: Path) -> tuple[Path, Path, Path]:
        """Return (preferred, alternate, legacy) label paths for the current mode."""
        obb_path, bb_path, legacy_path = self._derive_label_path_triplet(image_path)
        if self._use_obb:
            return obb_path, bb_path, legacy_path
        return bb_path, obb_path, legacy_path

    def _refresh_active_label_path(self) -> None:
        """Recompute active save path based on current image and mode."""
        if self._image_path is None:
            return
        preferred, _, _ = self._derive_label_paths(self._image_path)
        self._label_path = preferred

    def _normalize_labels_for_mode(self, labels: list[Label]) -> list[Label]:
        """Return labels converted to the active format mode."""
        normalized: list[Label] = []
        for lbl in labels:
            if self._use_obb:
                if isinstance(lbl, OBBLabel):
                    normalized.append(lbl)
                else:
                    normalized.append(
                        OBBLabel(
                            class_idx=lbl.class_idx,
                            points=lbl.to_corners(),
                            conf=lbl.conf,
                        )
                    )
            else:
                if isinstance(lbl, BBoxLabel):
                    normalized.append(lbl)
                else:
                    normalized.append(
                        BBoxLabel.from_corners(
                            class_idx=lbl.class_idx,
                            corners=lbl.points,
                            conf=lbl.conf,
                        )
                    )
        return normalized

    @staticmethod
    def _derive_label_path_triplet(image_path: Path) -> tuple[Path, Path, Path]:
        """Return (obb_path, bb_path, legacy_path)."""
        # Replace the 'images' directory component with 'labels', change ext to .txt.
        parts = image_path.parts
        for i, part in enumerate(parts):
            if part == "images":
                legacy = Path(*parts[:i], "labels", *parts[i + 1:]).with_suffix(".txt")
                obb = legacy.parent / OBB_SUBFOLDER / legacy.name
                bb = legacy.parent / BB_SUBFOLDER / legacy.name
                return obb, bb, legacy
        # Fallback: same directory, .txt extension
        legacy = image_path.with_suffix(".txt")
        obb = legacy.parent / OBB_SUBFOLDER / legacy.name
        bb = legacy.parent / BB_SUBFOLDER / legacy.name
        return obb, bb, legacy

    def _notify(self) -> None:
        if self._on_changed:
            self._on_changed()
