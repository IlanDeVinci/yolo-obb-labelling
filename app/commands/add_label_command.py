"""Undo commands for adding labels."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtGui import QUndoCommand

from app.models.obb_label import Label

if TYPE_CHECKING:
    from app.canvas.annotation_canvas import AnnotationCanvas
    from app.models.label_manager import LabelManager


class AddLabelCommand(QUndoCommand):
    """Wraps the creation of a single label (OBBLabel or BBoxLabel)."""

    def __init__(
        self,
        label: Label,
        canvas: "AnnotationCanvas",
        label_mgr: "LabelManager",
    ) -> None:
        super().__init__(f"Add label (class {label.class_idx})")
        self._label = label
        self._canvas = canvas
        self._label_mgr = label_mgr
        self._first_run = True

    def redo(self) -> None:
        if self._first_run:
            self._first_run = False
            return
        self._canvas.add_label_item(self._label)
        self._label_mgr.add_label(self._label)
        self._canvas.labels_changed.emit()

    def undo(self) -> None:
        self._canvas.remove_label_item(self._label)
        self._label_mgr.remove_label(self._label)
        self._canvas.labels_changed.emit()


class AddLabelsCommand(QUndoCommand):
    """Wraps the creation of multiple labels as one undo step."""

    def __init__(
        self,
        labels: list[Label],
        canvas: "AnnotationCanvas",
        label_mgr: "LabelManager",
        action_label: str | None = None,
    ) -> None:
        n = len(labels)
        super().__init__(action_label or f"Add {n} label{'s' if n != 1 else ''}")
        self._labels = list(labels)
        self._canvas = canvas
        self._label_mgr = label_mgr
        self._first_run = True

    def redo(self) -> None:
        if self._first_run:
            self._first_run = False
            return
        for lbl in self._labels:
            self._canvas.add_label_item(lbl)
            self._label_mgr.add_label(lbl)
        self._canvas.labels_changed.emit()

    def undo(self) -> None:
        for lbl in self._labels:
            self._canvas.remove_label_item(lbl)
            self._label_mgr.remove_label(lbl)
        self._canvas.labels_changed.emit()
