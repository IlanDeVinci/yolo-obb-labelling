"""Right panel: per-image label list."""
from __future__ import annotations
from typing import Callable

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
)

from app.models.obb_label import Label
from app.utils.colors import get_color


def _color_icon(class_idx: int, size: int = 12) -> QIcon:
    r, g, b = get_color(class_idx)
    pm = QPixmap(size, size)
    pm.fill(QColor(r, g, b))
    return QIcon(pm)


class LabelListPanel(QWidget):
    """Shows all labels for the current image."""

    label_selected = pyqtSignal(int)   # index into label list

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._labels: list[Label] = []
        self._class_names: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        lbl = QLabel("Labels")
        lbl.setStyleSheet("font-weight: bold; color: #ccc;")
        layout.addWidget(lbl)

        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget { background: #2a2a2a; color: #ddd; border: none; }"
            "QListWidget::item:selected { background: #3a5a8a; }"
        )
        self._list.currentRowChanged.connect(lambda r: self.label_selected.emit(r) if r >= 0 else None)
        layout.addWidget(self._list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_class_names(self, names: list[str]) -> None:
        self._class_names = names
        if self._labels:
            self.refresh(self._labels)

    def refresh(self, labels: list[Label]) -> None:
        self._labels = labels
        self._list.blockSignals(True)
        self._list.clear()
        for i, lbl in enumerate(labels):
            name = (
                self._class_names[lbl.class_idx]
                if lbl.class_idx < len(self._class_names)
                else str(lbl.class_idx)
            )
            conf_str = f" ({lbl.conf:.0%})" if lbl.is_preannoted() else ""
            item = QListWidgetItem(_color_icon(lbl.class_idx), f"#{i}  {name}{conf_str}")
            self._list.addItem(item)
        self._list.blockSignals(False)

    def clear(self) -> None:
        self._list.clear()
        self._labels = []

    def select_index(self, index: int) -> None:
        self._list.blockSignals(True)
        if 0 <= index < self._list.count():
            self._list.setCurrentRow(index)
        else:
            self._list.clearSelection()
        self._list.blockSignals(False)
