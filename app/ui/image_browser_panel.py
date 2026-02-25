"""Left panel: image file browser with label-count indicators."""
from __future__ import annotations
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QAbstractItemView,
)

from app.models.label_manager import LabelManager


class ImageBrowserPanel(QWidget):
    """Shows the image list; emits image_selected(Path) when user clicks."""

    image_selected = pyqtSignal(object)   # Path

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._images: list[Path] = []
        self._label_manager: LabelManager | None = None
        self._completion_provider: Callable[[Path], str] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self._title = QLabel("Images (0)")
        self._title.setStyleSheet("font-weight: bold; color: #ccc;")
        layout.addWidget(self._title)

        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget { background: #2a2a2a; color: #ddd; border: none; }"
            "QListWidget::item:selected { background: #3a5a8a; }"
        )
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.currentRowChanged.connect(self._on_row_changed)
        layout.addWidget(self._list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_label_manager(self, lm: LabelManager) -> None:
        self._label_manager = lm

    def set_completion_provider(self, provider: Callable[[Path], str] | None) -> None:
        self._completion_provider = provider

    def set_images(self, images: list[Path]) -> None:
        self._images = list(images)
        self._title.setText(f"Images ({len(images)})")
        self._rebuild()

    def select_index(self, idx: int) -> None:
        self._list.blockSignals(True)
        if 0 <= idx < self._list.count():
            self._list.setCurrentRow(idx)
        self._list.blockSignals(False)

    def refresh_item(self, idx: int) -> None:
        """Re-render a single row (e.g., after saving labels)."""
        if 0 <= idx < len(self._images):
            item = self._list.item(idx)
            if item:
                self._decorate_item(item, self._images[idx])

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _rebuild(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for img in self._images:
            item = QListWidgetItem(img.name)
            self._decorate_item(item, img)
            self._list.addItem(item)
        self._list.blockSignals(False)

    def _decorate_item(self, item: QListWidgetItem, img: Path) -> None:
        has_labels = (
            self._label_manager.has_labels_for(img)
            if self._label_manager else False
        )
        completion = self._completion_provider(img) if self._completion_provider else ""
        if completion == "completed":
            item.setForeground(QColor("#90ee90"))
            status_suffix = " [DONE]"
        elif completion == "in_progress":
            item.setForeground(QColor("#f6d86b"))
            status_suffix = " [IP]"
        else:
            item.setForeground(QColor("#90ee90") if has_labels else QColor("#ddd"))
            status_suffix = " ✓" if has_labels else ""
        item.setText(img.name + status_suffix)

    def _on_row_changed(self, row: int) -> None:
        if 0 <= row < len(self._images):
            self.image_selected.emit(self._images[row])
