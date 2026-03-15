"""QUndoCommand subclasses for label add/delete operations."""
from __future__ import annotations
from typing import TYPE_CHECKING, Callable

from PyQt6.QtGui import QUndoCommand

from app.models.obb_label import OBBLabel, BBoxLabel, Label

if TYPE_CHECKING:
    from app.canvas.annotation_canvas import AnnotationCanvas
    from app.models.label_manager import LabelManager


class AddLabelCommand(QUndoCommand):
    """Wraps the creation of a single label (OBBLabel or BBoxLabel).

    The label is already present in both the canvas and the label manager
    at the time the command is constructed.  The initial redo() call (made
    automatically when the command is pushed onto the stack) is therefore
    skipped via the _first_run flag.  Subsequent redo() calls (after undo)
    actually re-insert the label.
    """

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
        self._first_run = True  # label already in canvas + label_mgr at push time

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
    """Wraps the creation of multiple labels as one undo step.

    Labels are already present in canvas + label manager when command is pushed,
    so initial redo() is skipped.
    """

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


class DeleteLabelsCommand(QUndoCommand):
    """Wraps the deletion of one or more labels (OBBLabel or BBoxLabel).

    Unlike AddLabelCommand, the labels are NOT yet removed from the canvas
    or label manager when this command is constructed.  The first redo()
    call (fired automatically when the command is pushed) performs the
    actual deletion.
    """

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


class ModifyLabelCommand(QUndoCommand):
    """Wraps a move, corner-drag, or rotation of a label.

    The modification has already happened when this command is constructed,
    so the first redo() is skipped.

    Note: old_points and new_points are always in 8-float corner format,
    even for BBoxLabel (converted to corners for storage).
    """

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
        """Apply corner points to the label, converting format as needed."""
        if isinstance(self._label, OBBLabel):
            self._label.points = list(points)
        elif isinstance(self._label, BBoxLabel):
            # Convert 4-corner format to center-width-height format
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


class ToggleLabelModeCommand(QUndoCommand):
    """Undoable switch between OBB and BBox mode with label conversion."""

    def __init__(
        self,
        old_use_obb: bool,
        new_use_obb: bool,
        old_labels: list[Label],
        new_labels: list[Label],
        old_dirty: bool,
        new_dirty: bool,
        apply_state: Callable[[bool, list[Label], bool], None],
        action_label: str | None = None,
    ) -> None:
        mode_from = "OBB" if old_use_obb else "BBox"
        mode_to = "OBB" if new_use_obb else "BBox"
        super().__init__(action_label or f"Switch mode {mode_from} -> {mode_to}")
        self._old_use_obb = old_use_obb
        self._new_use_obb = new_use_obb
        self._old_labels = old_labels
        self._new_labels = new_labels
        self._old_dirty = old_dirty
        self._new_dirty = new_dirty
        self._apply_state = apply_state

    def redo(self) -> None:
        self._apply_state(self._new_use_obb, self._new_labels, self._new_dirty)

    def undo(self) -> None:
        self._apply_state(self._old_use_obb, self._old_labels, self._old_dirty)
