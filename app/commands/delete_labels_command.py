"""Undo command for deleting labels."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtGui import QUndoCommand

from app.models.obb_label import Label

if TYPE_CHECKING:
    from app.canvas.annotation_canvas import AnnotationCanvas
    from app.models.label_manager import LabelManager


class DeleteLabelsCommand(QUndoCommand):
    """Wraps the deletion of one or more labels."""

    def __init__(
        self,
        labels: list[Label],
        canvas: "AnnotationCanvas",
        label_mgr: "LabelManager",
    ) -> None:
        n = len(labels)
        super().__init__(f"Delete {n} label{'s' if n != 1 else ''}")
        self._labels = list(labels)
        self._canvas = canvas
        self._label_mgr = label_mgr

    def redo(self) -> None:
        for lbl in self._labels:
            self._canvas.remove_label_item(lbl)
            self._label_mgr.remove_label(lbl)
        self._canvas.labels_changed.emit()

    def undo(self) -> None:
        for lbl in self._labels:
            self._canvas.add_label_item(lbl)
            self._label_mgr.add_label(lbl)
        self._canvas.labels_changed.emit()
