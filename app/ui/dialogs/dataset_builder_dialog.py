"""Dataset builder dialog — scan a folder and create a YOLO dataset structure."""
from __future__ import annotations
import random
import shutil
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QCheckBox,
    QRadioButton,
    QTextEdit,
    QDialogButtonBox,
    QFileDialog,
    QMessageBox,
    QApplication,
)

from app.models.label_manager import LabelManager
from app.utils.yaml_io import build_dataset_dict, save_dataset_yaml

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}


class DatasetBuilderDialog(QDialog):
    """Wizard for assembling a YOLO-format dataset from already-labeled images."""

    def __init__(
        self,
        default_folder: str = "",
        default_class_names: list[str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Build Dataset from Labeled Images")
        self.setMinimumWidth(540)

        # State
        self._labeled_pairs: list[tuple[Path, Path]] = []  # (img_path, label_path)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ---- Source ----
        src_group = QGroupBox("Source")
        src_grid = QGridLayout(src_group)
        src_grid.addWidget(QLabel("Image folder:"), 0, 0)
        self._folder_edit = QLineEdit(default_folder)
        self._folder_edit.setPlaceholderText("/path/to/images")
        src_grid.addWidget(self._folder_edit, 0, 1)
        browse_src = QPushButton("Browse…")
        browse_src.clicked.connect(self._browse_source)
        browse_src.setMaximumWidth(80)
        src_grid.addWidget(browse_src, 0, 2)

        self._scan_label = QLabel("Press 'Scan' to detect labeled images.")
        self._scan_label.setStyleSheet("color: #aaa; font-style: italic;")
        src_grid.addWidget(self._scan_label, 1, 0, 1, 2)

        scan_btn = QPushButton("Scan Folder")
        scan_btn.clicked.connect(self._scan_folder)
        scan_btn.setMaximumWidth(100)
        src_grid.addWidget(scan_btn, 1, 2)
        layout.addWidget(src_group)

        # ---- Class Names ----
        cls_group = QGroupBox("Class Names  (one per line — written to dataset YAML)")
        cls_layout = QVBoxLayout(cls_group)
        self._class_edit = QTextEdit()
        self._class_edit.setMaximumHeight(80)
        self._class_edit.setPlaceholderText("card\nplayer\n…")
        if default_class_names:
            self._class_edit.setPlainText("\n".join(default_class_names))
        cls_layout.addWidget(self._class_edit)
        layout.addWidget(cls_group)

        # ---- Split ----
        split_group = QGroupBox("Train / Val Split")
        split_vbox = QVBoxLayout(split_group)

        slider_row = QHBoxLayout()
        slider_row.addWidget(QLabel("Train"))
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(50, 99)
        self._slider.setValue(80)
        self._slider.valueChanged.connect(self._on_slider_changed)
        slider_row.addWidget(self._slider)
        slider_row.addWidget(QLabel("Val"))
        split_vbox.addLayout(slider_row)

        self._split_label = QLabel("80 % train  /  20 % val")
        self._split_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        split_vbox.addWidget(self._split_label)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Presets:"))
        for ratio, lbl in [(80, "80/20"), (70, "70/30"), (60, "60/40")]:
            btn = QPushButton(lbl)
            btn.setMaximumWidth(60)
            r = ratio
            btn.clicked.connect(lambda checked=False, r=r: self._slider.setValue(r))
            preset_row.addWidget(btn)
        preset_row.addStretch()
        split_vbox.addLayout(preset_row)

        self._shuffle_check = QCheckBox("Shuffle images before split")
        self._shuffle_check.setChecked(True)
        split_vbox.addWidget(self._shuffle_check)
        layout.addWidget(split_group)

        # ---- Output ----
        out_group = QGroupBox("Output")
        out_grid = QGridLayout(out_group)
        out_grid.addWidget(QLabel("Parent folder:"), 0, 0)
        self._out_folder_edit = QLineEdit()
        self._out_folder_edit.setPlaceholderText("/path/to/datasets")
        out_grid.addWidget(self._out_folder_edit, 0, 1)
        browse_out = QPushButton("Browse…")
        browse_out.clicked.connect(self._browse_output)
        browse_out.setMaximumWidth(80)
        out_grid.addWidget(browse_out, 0, 2)

        out_grid.addWidget(QLabel("Dataset name:"), 1, 0)
        self._name_edit = QLineEdit("my_dataset")
        out_grid.addWidget(self._name_edit, 1, 1, 1, 2)

        action_row = QHBoxLayout()
        self._copy_radio = QRadioButton("Copy files  (keep originals)")
        self._move_radio = QRadioButton("Move files  (faster, no duplicates)")
        self._copy_radio.setChecked(True)
        action_row.addWidget(self._copy_radio)
        action_row.addWidget(self._move_radio)
        action_row.addStretch()
        out_grid.addLayout(action_row, 2, 0, 1, 3)
        layout.addWidget(out_group)

        # ---- Buttons ----
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText("Build Dataset")
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        # Auto-scan if a folder was provided
        if default_folder:
            self._scan_folder()

    # ------------------------------------------------------------------
    # Browse helpers
    # ------------------------------------------------------------------

    def _browse_source(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Image Folder")
        if folder:
            self._folder_edit.setText(folder)
            self._scan_folder()

    def _browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Output Parent Folder")
        if folder:
            self._out_folder_edit.setText(folder)

    # ------------------------------------------------------------------
    # Slider
    # ------------------------------------------------------------------

    def _on_slider_changed(self, val: int) -> None:
        self._split_label.setText(f"{val} % train  /  {100 - val} % val")

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    def _scan_folder(self) -> None:
        folder_str = self._folder_edit.text().strip()
        if not folder_str:
            self._scan_label.setText("No folder selected.")
            return

        p = Path(folder_str)
        if not p.is_dir():
            self._scan_label.setText("⚠ Folder not found.")
            self._labeled_pairs = []
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            all_images = sorted(
                f for f in p.iterdir()
                if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
            )

            labeled: list[tuple[Path, Path]] = []
            for img in all_images:
                lp = LabelManager._derive_label_path(img)
                if lp.exists() and lp.stat().st_size > 0:
                    try:
                        content = lp.read_text(encoding="utf-8").strip()
                        if content:
                            labeled.append((img, lp))
                    except OSError:
                        pass

            self._labeled_pairs = labeled
            n_total = len(all_images)
            n_labeled = len(labeled)
            self._scan_label.setText(
                f"Found {n_total} image(s) — {n_labeled} labeled, "
                f"{n_total - n_labeled} unlabeled"
            )
            self._scan_label.setStyleSheet(
                "color: #6f6;" if n_labeled > 0 else "color: #f96;"
            )
        finally:
            QApplication.restoreOverrideCursor()

    # ------------------------------------------------------------------
    # Accept / Build
    # ------------------------------------------------------------------

    def _accept(self) -> None:
        # Validation
        if not self._labeled_pairs:
            QMessageBox.warning(
                self,
                "No labeled images",
                "No labeled images found.\nPlease select a folder and press 'Scan Folder' first.",
            )
            return

        out_folder_str = self._out_folder_edit.text().strip()
        if not out_folder_str:
            QMessageBox.warning(self, "No output folder", "Please select an output parent folder.")
            return

        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "No dataset name", "Please enter a dataset name.")
            return

        out_path = Path(out_folder_str) / name
        if out_path.exists():
            reply = QMessageBox.question(
                self,
                "Folder exists",
                f"The dataset folder already exists:\n{out_path}\n\nOverwrite / merge?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        # Build
        try:
            n_train, n_val = self._do_build(out_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Build failed", f"Failed to create dataset:\n{exc}")
            return

        QMessageBox.information(
            self,
            "Dataset created",
            f"Dataset created at:\n{out_path}\n\nTrain : {n_train} image(s)\nVal   : {n_val} image(s)",
        )
        self.accept()

    def _do_build(self, out_path: Path) -> tuple[int, int]:
        """Perform the actual file copy/move and YAML generation."""
        pairs = list(self._labeled_pairs)

        if self._shuffle_check.isChecked():
            random.shuffle(pairs)

        train_ratio = self._slider.value() / 100.0
        n_train = max(1, int(len(pairs) * train_ratio))
        train_pairs = pairs[:n_train]
        val_pairs = pairs[n_train:]

        # Create directory structure
        for split in ("train", "val"):
            (out_path / "images" / split).mkdir(parents=True, exist_ok=True)
            (out_path / "labels" / split).mkdir(parents=True, exist_ok=True)

        # Determine file action
        file_action = shutil.move if self._move_radio.isChecked() else shutil.copy2  # type: ignore[assignment]

        def _transfer(src: Path, dst: Path) -> None:
            file_action(str(src), str(dst))

        for img_path, lp in train_pairs:
            _transfer(img_path, out_path / "images" / "train" / img_path.name)
            _transfer(lp, out_path / "labels" / "train" / lp.name)

        for img_path, lp in val_pairs:
            _transfer(img_path, out_path / "images" / "val" / img_path.name)
            _transfer(lp, out_path / "labels" / "val" / lp.name)

        # Parse class names from text area
        raw = self._class_edit.toPlainText().strip()
        class_names = [n.strip() for n in raw.splitlines() if n.strip()]

        # Write dataset YAML
        data = build_dataset_dict(out_path, class_names)
        yaml_path = out_path / f"{out_path.name}.yaml"
        save_dataset_yaml(data, yaml_path)

        return len(train_pairs), len(val_pairs)
