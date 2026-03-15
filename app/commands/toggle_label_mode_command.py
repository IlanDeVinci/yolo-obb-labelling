"""Undo command for toggling label mode."""
from __future__ import annotations

from typing import Callable

from PyQt6.QtGui import QUndoCommand

from app.models.obb_label import Label


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
