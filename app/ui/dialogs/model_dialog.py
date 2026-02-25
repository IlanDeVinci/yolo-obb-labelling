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
    def __init__(self, current_path: str = "", current_conf: float = 0.7, parent=None) -> None:
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
        self.accept()

    @property
    def model_path(self) -> str:
        return self._path_edit.text().strip()

    @property
    def confidence(self) -> float:
        return self._conf_spin.value()
