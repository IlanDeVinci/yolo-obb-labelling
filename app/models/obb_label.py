from __future__ import annotations
from dataclasses import dataclass, field
from typing import Union


@dataclass
class OBBLabel:
    """One oriented bounding box annotation in YOLO OBB format.

    points: [x1,y1, x2,y2, x3,y3, x4,y4] — all normalized to [0, 1].
    conf:   1.0 for manual labels; model confidence for pre-annotations.
    """
    class_idx: int
    points: list[float]   # length 8
    conf: float = 1.0

    def to_yolo_line(self) -> str:
        coords = " ".join(f"{v:.6f}" for v in self.points)
        return f"{self.class_idx} {coords}"

    @classmethod
    def from_yolo_line(cls, line: str) -> "OBBLabel":
        parts = line.strip().split()
        if len(parts) < 9:
            raise ValueError(f"Invalid OBB label line (need >=9 values): {line!r}")
        return cls(
            class_idx=int(parts[0]),
            points=[float(x) for x in parts[1:9]],
            conf=1.0,
        )

    def mark_manual(self) -> None:
        """Reset confidence to 1.0 (treat as manually confirmed)."""
        self.conf = 1.0

    def is_preannoted(self) -> bool:
        return self.conf < 1.0


@dataclass
class BBoxLabel:
    """One axis-aligned bounding box annotation in standard YOLO format.

    x_center, y_center, width, height — all normalized to [0, 1].
    conf:   1.0 for manual labels; model confidence for pre-annotations.
    """
    class_idx: int
    x_center: float
    y_center: float
    width: float
    height: float
    conf: float = 1.0

    def to_yolo_line(self) -> str:
        return f"{self.class_idx} {self.x_center:.6f} {self.y_center:.6f} {self.width:.6f} {self.height:.6f}"

    @classmethod
    def from_yolo_line(cls, line: str) -> "BBoxLabel":
        parts = line.strip().split()
        if len(parts) < 5:
            raise ValueError(f"Invalid BBox label line (need >=5 values): {line!r}")
        return cls(
            class_idx=int(parts[0]),
            x_center=float(parts[1]),
            y_center=float(parts[2]),
            width=float(parts[3]),
            height=float(parts[4]),
            conf=1.0,
        )

    def mark_manual(self) -> None:
        """Reset confidence to 1.0 (treat as manually confirmed)."""
        self.conf = 1.0

    def is_preannoted(self) -> bool:
        return self.conf < 1.0

    def to_corners(self) -> list[float]:
        """Convert to 4-corner format [x1,y1, x2,y2, x3,y3, x4,y4] for rendering."""
        half_w = self.width / 2
        half_h = self.height / 2
        x1 = self.x_center - half_w
        y1 = self.y_center - half_h
        x2 = self.x_center + half_w
        y2 = self.y_center - half_h
        x3 = self.x_center + half_w
        y3 = self.y_center + half_h
        x4 = self.x_center - half_w
        y4 = self.y_center + half_h
        return [x1, y1, x2, y2, x3, y3, x4, y4]

    @classmethod
    def from_corners(cls, class_idx: int, corners: list[float], conf: float = 1.0) -> "BBoxLabel":
        """Create from 4-corner points by computing axis-aligned bounding box."""
        xs = [corners[i] for i in range(0, 8, 2)]
        ys = [corners[i] for i in range(1, 8, 2)]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        return cls(
            class_idx=class_idx,
            x_center=(x_min + x_max) / 2,
            y_center=(y_min + y_max) / 2,
            width=x_max - x_min,
            height=y_max - y_min,
            conf=conf,
        )


# Type alias for either label type
Label = Union[OBBLabel, BBoxLabel]
