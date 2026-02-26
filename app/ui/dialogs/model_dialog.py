"""Dialog for loading a YOLO OBB model and setting confidence threshold."""
from __future__ import annotations
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QDoubleSpinBox,
    QFileDialog,
    QDialogButtonBox,
    QMessageBox,
)


class ModelDialog(QDialog):
    def __init__(
        self,
        current_path: str = "",
        current_conf: float = 0.7,
        current_class_filter: list[int] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Load YOLO OBB Model")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Model path (.pt file):"))
        row = QHBoxLayout()
        self._path_edit = QLineEdit(current_path)
        self._path_edit.setPlaceholderText("path/to/model.pt")
        row.addWidget(self._path_edit)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)

        layout.addWidget(QLabel("Confidence threshold:"))
        self._conf_spin = QDoubleSpinBox()
        self._conf_spin.setRange(0.01, 1.0)
        self._conf_spin.setSingleStep(0.05)
        self._conf_spin.setDecimals(2)
        self._conf_spin.setValue(current_conf)
        layout.addWidget(self._conf_spin)

        layout.addWidget(QLabel("Class filter (optional IDs, e.g. 0,2,5):"))
        self._class_filter_edit = QLineEdit(self._format_class_filter(current_class_filter))
        self._class_filter_edit.setPlaceholderText("All classes")
        layout.addWidget(self._class_filter_edit)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select YOLO model", "", "PyTorch models (*.pt)"
        )
        if path:
            self._path_edit.setText(path)

    def _accept(self) -> None:
        p = self._path_edit.text().strip()
        if not p or not Path(p).is_file():
            QMessageBox.warning(self, "Invalid path", "Please select a valid .pt model file.")
            return
        try:
            self._parse_class_filter(self._class_filter_edit.text())
        except ValueError:
            QMessageBox.warning(
                self,
                "Invalid class filter",
                "Use comma/space-separated non-negative integers, e.g. 0,2,5",
            )
            return
        self.accept()

    @property
    def model_path(self) -> str:
        return self._path_edit.text().strip()

    @property
    def confidence(self) -> float:
        return self._conf_spin.value()

    @property
    def class_filter(self) -> list[int] | None:
        return self._parse_class_filter(self._class_filter_edit.text())

    @staticmethod
    def _format_class_filter(class_filter: list[int] | None) -> str:
        if not class_filter:
            return ""
        return ",".join(str(v) for v in class_filter)

    @staticmethod
    def _parse_class_filter(raw: str) -> list[int] | None:
        text = raw.strip()
        if not text:
            return None
        tokens = text.replace(";", ",").replace(" ", ",").split(",")
        values: list[int] = []
        for token in tokens:
            part = token.strip()
            if not part:
                continue
            value = int(part)
            if value < 0:
                raise ValueError("class id must be >= 0")
            values.append(value)
        if not values:
            return None
        return sorted(set(values))
