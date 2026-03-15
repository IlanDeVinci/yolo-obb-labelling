"""Undo command for label geometry modification."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtGui import QUndoCommand

from app.models.obb_label import BBoxLabel, OBBLabel, Label

if TYPE_CHECKING:
    from app.canvas.annotation_canvas import AnnotationCanvas


class ModifyLabelCommand(QUndoCommand):
    """Wraps a move, corner-drag, or rotation of a label."""

    def __init__(
        self,
        label: Label,
        old_points: list[float],
        new_points: list[float],
        canvas: "AnnotationCanvas",
    ) -> None:
        super().__init__("Modify label")
        self._label = label
        self._old = list(old_points)
        self._new = list(new_points)
        self._canvas = canvas
        self._first_run = True

    def _apply_points(self, points: list[float]) -> None:
        if isinstance(self._label, OBBLabel):
            self._label.points = list(points)
        elif isinstance(self._label, BBoxLabel):
            xs = [points[i] for i in range(0, 8, 2)]
            ys = [points[i] for i in range(1, 8, 2)]
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)
            self._label.x_center = (x_min + x_max) / 2
            self._label.y_center = (y_min + y_max) / 2
            self._label.width = x_max - x_min
            self._label.height = y_max - y_min

    def redo(self) -> None:
        if self._first_run:
            self._first_run = False
            return
        self._apply_points(self._new)
        self._refresh()

    def undo(self) -> None:
        self._apply_points(self._old)
        self._refresh()

    def _refresh(self) -> None:
        for item in self._canvas._label_items:
            if item.label is self._label:
                item.refresh_from_label()
                break
        self._canvas.labels_changed.emit()
