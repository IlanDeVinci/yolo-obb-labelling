"""Left panel: class list with color indicators."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QHBoxLayout,
    QInputDialog,
)

from app.utils.colors import get_color


def _color_icon(class_idx: int, size: int = 14) -> QIcon:
    r, g, b = get_color(class_idx)
    pm = QPixmap(size, size)
    pm.fill(QColor(r, g, b))
    return QIcon(pm)


class ClassPanel(QWidget):
    """Displays class names; emits class_selected(int) when user clicks."""

    class_selected = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._class_names: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        lbl = QLabel("Classes")
        lbl.setStyleSheet("font-weight: bold; color: #ccc;")
        layout.addWidget(lbl)

        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget { background: #2a2a2a; color: #ddd; border: none; }"
            "QListWidget::item:selected { background: #3a5a8a; }"
        )
        self._list.currentRowChanged.connect(self._on_row_changed)
        layout.addWidget(self._list)

        btn_row = QHBoxLayout()
        self._add_btn = QPushButton("+")
        self._add_btn.setToolTip("Add class")
        self._add_btn.setFixedWidth(28)
        self._add_btn.clicked.connect(self._add_class)
        btn_row.addWidget(self._add_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_classes(self, names: list[str]) -> None:
        self._class_names = list(names)
        self._rebuild()

    def class_names(self) -> list[str]:
        return list(self._class_names)

    def selected_class(self) -> int:
        return max(0, self._list.currentRow())

    def select_class(self, idx: int) -> None:
        if 0 <= idx < self._list.count():
            self._list.setCurrentRow(idx)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _rebuild(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for i, name in enumerate(self._class_names):
            item = QListWidgetItem(_color_icon(i), f"{i}: {name}")
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)
        self._list.blockSignals(False)

    def _on_row_changed(self, row: int) -> None:
        if row >= 0:
            self.class_selected.emit(row)

    def _add_class(self) -> None:
        name, ok = QInputDialog.getText(self, "Add class", "Class name:")
        if ok and name.strip():
            self._class_names.append(name.strip())
            self._rebuild()
            self._list.setCurrentRow(len(self._class_names) - 1)
