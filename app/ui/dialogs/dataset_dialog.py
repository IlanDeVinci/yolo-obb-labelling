"""Dialog for creating or opening a YOLO dataset."""
from __future__ import annotations
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QPlainTextEdit,
    QDialogButtonBox,
    QTabWidget,
    QWidget,
    QMessageBox,
)


class DatasetDialog(QDialog):
    """Two-tab dialog: Create new dataset  /  Open existing YAML."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Dataset")
        self.setMinimumWidth(480)

        self._result_mode: str = ""   # "create" or "open"
        self._result_path: Path | None = None
        self._result_classes: list[str] = []

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        tabs.addTab(self._build_create_tab(), "Create New")
        tabs.addTab(self._build_open_tab(), "Open Existing")

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self._tabs = tabs

    # ------------------------------------------------------------------
    # Create tab
    # ------------------------------------------------------------------

    def _build_create_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        layout.addWidget(QLabel("Parent folder:"))
        row = QHBoxLayout()
        self._create_folder = QLineEdit()
        self._create_folder.setPlaceholderText("e.g. C:/datasets")
        row.addWidget(self._create_folder)
        btn = QPushButton("Browse…")
        btn.clicked.connect(self._browse_create_folder)
        row.addWidget(btn)
        layout.addLayout(row)

        layout.addWidget(QLabel("Dataset name:"))
        self._create_name = QLineEdit("my_dataset")
        layout.addWidget(self._create_name)

        layout.addWidget(QLabel("Class names (one per line):"))
        self._create_classes = QPlainTextEdit()
        self._create_classes.setPlaceholderText("card\njoker\nace_of_spades")
        self._create_classes.setMaximumHeight(120)
        layout.addWidget(self._create_classes)
        layout.addStretch()
        return w

    def _browse_create_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select parent folder")
        if folder:
            self._create_folder.setText(folder)

    # ------------------------------------------------------------------
    # Open tab
    # ------------------------------------------------------------------

    def _build_open_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        layout.addWidget(QLabel("Dataset YAML file:"))
        row = QHBoxLayout()
        self._open_yaml = QLineEdit()
        self._open_yaml.setPlaceholderText("path/to/dataset.yaml")
        row.addWidget(self._open_yaml)
        btn = QPushButton("Browse…")
        btn.clicked.connect(self._browse_yaml)
        row.addWidget(btn)
        layout.addLayout(row)
        layout.addStretch()
        return w

    def _browse_yaml(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open Dataset YAML", "", "YAML files (*.yaml *.yml)")
        if path:
            self._open_yaml.setText(path)

    # ------------------------------------------------------------------
    # Accept
    # ------------------------------------------------------------------

    def _accept(self) -> None:
        tab = self._tabs.currentIndex()
        if tab == 0:
            folder = self._create_folder.text().strip()
            name = self._create_name.text().strip()
            classes_text = self._create_classes.toPlainText()
            classes = [c.strip() for c in classes_text.splitlines() if c.strip()]
            if not folder or not name:
                QMessageBox.warning(self, "Missing input", "Please fill in the folder and dataset name.")
                return
            if not classes:
                QMessageBox.warning(self, "Missing input", "Please enter at least one class name.")
                return
            self._result_mode = "create"
            self._result_path = Path(folder)
            self._result_classes = classes
            self._dataset_name = name
        else:
            yaml_path = self._open_yaml.text().strip()
            if not yaml_path or not Path(yaml_path).is_file():
                QMessageBox.warning(self, "Missing input", "Please select a valid YAML file.")
                return
            self._result_mode = "open"
            self._result_path = Path(yaml_path)
            self._result_classes = []
        self.accept()

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------

    @property
    def mode(self) -> str:
        return self._result_mode

    @property
    def result_path(self) -> Path | None:
        return self._result_path

    @property
    def result_classes(self) -> list[str]:
        return self._result_classes

    @property
    def dataset_name(self) -> str:
        return getattr(self, "_dataset_name", "")
