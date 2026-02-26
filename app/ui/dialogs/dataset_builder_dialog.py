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
    QSpinBox,
    QComboBox,
)

from app.models.label_manager import LabelManager
from app.utils.yaml_io import build_dataset_dict, save_dataset_yaml
from app.utils.dataset_augmentation import AugmentationOptions, generate_split_augmentations

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

        self._scan_hint_label = QLabel("")
        self._scan_hint_label.setStyleSheet("color: #aaa; font-style: italic;")
        src_grid.addWidget(self._scan_hint_label, 2, 0, 1, 3)
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

        # ---- Augmentations ----
        aug_group = QGroupBox("Extra Augmentations")
        aug_grid = QGridLayout(aug_group)

        row = 0
        self._aug_brightness_check = QCheckBox("Duplicate with darkness/light filters")
        aug_grid.addWidget(self._aug_brightness_check, row, 0, 1, 2)
        self._aug_brightness_count = QSpinBox()
        self._aug_brightness_count.setRange(1, 8)
        self._aug_brightness_count.setValue(2)
        self._aug_brightness_count.setSuffix(" / image")
        aug_grid.addWidget(self._aug_brightness_count, row, 2)

        row += 1
        self._aug_crop_check = QCheckBox("Duplicate with safe random crops (never cuts boxes)")
        aug_grid.addWidget(self._aug_crop_check, row, 0, 1, 2)
        self._aug_crop_count = QSpinBox()
        self._aug_crop_count.setRange(1, 8)
        self._aug_crop_count.setValue(1)
        self._aug_crop_count.setSuffix(" / image")
        aug_grid.addWidget(self._aug_crop_count, row, 2)

        row += 1
        aug_grid.addWidget(QLabel("Max crop margin (% each side):"), row, 0, 1, 2)
        self._aug_crop_margin = QSpinBox()
        self._aug_crop_margin.setRange(5, 45)
        self._aug_crop_margin.setValue(20)
        self._aug_crop_margin.setSuffix(" %")
        aug_grid.addWidget(self._aug_crop_margin, row, 2)

        row += 1
        self._aug_cutout_check = QCheckBox("Extract random boxes and compose on random backgrounds")
        aug_grid.addWidget(self._aug_cutout_check, row, 0, 1, 2)
        self._aug_cutout_count = QSpinBox()
        self._aug_cutout_count.setRange(1, 8)
        self._aug_cutout_count.setValue(1)
        self._aug_cutout_count.setSuffix(" / image")
        aug_grid.addWidget(self._aug_cutout_count, row, 2)

        row += 1
        aug_grid.addWidget(QLabel("Objects per composite (min/max):"), row, 0, 1, 2)
        objects_row = QHBoxLayout()
        self._aug_cutout_min = QSpinBox()
        self._aug_cutout_min.setRange(1, 30)
        self._aug_cutout_min.setValue(2)
        self._aug_cutout_max = QSpinBox()
        self._aug_cutout_max.setRange(1, 40)
        self._aug_cutout_max.setValue(8)
        objects_row.addWidget(self._aug_cutout_min)
        objects_row.addWidget(QLabel("to"))
        objects_row.addWidget(self._aug_cutout_max)
        aug_grid.addLayout(objects_row, row, 2)

        row += 1
        aug_grid.addWidget(QLabel("Effects strength (%):"), row, 0, 1, 2)
        self._aug_effect_strength = QSpinBox()
        self._aug_effect_strength.setRange(5, 95)
        self._aug_effect_strength.setValue(35)
        self._aug_effect_strength.setSuffix(" %")
        aug_grid.addWidget(self._aug_effect_strength, row, 2)

        row += 1
        aug_grid.addWidget(QLabel("Background source mode:"), row, 0, 1, 2)
        self._aug_bg_mode_combo = QComboBox()
        self._aug_bg_mode_combo.addItem("Generated only", "generated")
        self._aug_bg_mode_combo.addItem("Mix folder + generated", "mix")
        self._aug_bg_mode_combo.addItem("Folder only", "folder")
        self._aug_bg_mode_combo.setCurrentIndex(1)
        aug_grid.addWidget(self._aug_bg_mode_combo, row, 2)

        row += 1
        aug_grid.addWidget(QLabel("Background images folder:"), row, 0)
        bg_img_row = QHBoxLayout()
        self._aug_backgrounds_folder_edit = QLineEdit()
        self._aug_backgrounds_folder_edit.setPlaceholderText("Optional: folder of background images/textures")
        bg_img_row.addWidget(self._aug_backgrounds_folder_edit)
        bg_img_browse = QPushButton("Browse…")
        bg_img_browse.clicked.connect(self._browse_aug_backgrounds)
        bg_img_browse.setMaximumWidth(80)
        bg_img_row.addWidget(bg_img_browse)
        aug_grid.addLayout(bg_img_row, row, 1, 1, 2)

        row += 1
        aug_grid.addWidget(QLabel("Extra random-object folder:"), row, 0)
        bg_row = QHBoxLayout()
        self._aug_objects_folder_edit = QLineEdit()
        self._aug_objects_folder_edit.setPlaceholderText("Optional: folder with phones/computers/napkins/headphones/etc")
        bg_row.addWidget(self._aug_objects_folder_edit)
        bg_browse = QPushButton("Browse…")
        bg_browse.clicked.connect(self._browse_aug_objects)
        bg_browse.setMaximumWidth(80)
        bg_row.addWidget(bg_browse)
        aug_grid.addLayout(bg_row, row, 1, 1, 2)

        row += 1
        presets_row = QHBoxLayout()
        presets_row.addWidget(QLabel("Aug presets:"))
        mild_btn = QPushButton("Mild")
        mild_btn.clicked.connect(lambda: self._apply_aug_preset("mild"))
        balanced_btn = QPushButton("Balanced")
        balanced_btn.clicked.connect(lambda: self._apply_aug_preset("balanced"))
        aggressive_btn = QPushButton("Aggressive")
        aggressive_btn.clicked.connect(lambda: self._apply_aug_preset("aggressive"))
        presets_row.addWidget(mild_btn)
        presets_row.addWidget(balanced_btn)
        presets_row.addWidget(aggressive_btn)
        presets_row.addStretch()
        aug_grid.addLayout(presets_row, row, 0, 1, 3)

        row += 1
        estimate_row = QHBoxLayout()
        estimate_row.addWidget(QLabel("Target total:"))
        self._target_total_spin = QSpinBox()
        self._target_total_spin.setRange(100, 100000)
        self._target_total_spin.setSingleStep(100)
        self._target_total_spin.setValue(1500)
        self._target_total_spin.setSuffix(" images")
        estimate_row.addWidget(self._target_total_spin)
        estimate_row.addStretch()
        aug_grid.addLayout(estimate_row, row, 0, 1, 3)

        row += 1
        self._estimate_label = QLabel("Estimated total after augmentation: ~0 images")
        self._estimate_label.setStyleSheet("color: #ddd; font-weight: 600;")
        aug_grid.addWidget(self._estimate_label, row, 0, 1, 3)

        layout.addWidget(aug_group)

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
        self._wire_estimate_signals()
        self._refresh_estimate()
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

    def _browse_aug_objects(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select random object/background folder")
        if folder:
            self._aug_objects_folder_edit.setText(folder)
            self._refresh_estimate()

    def _browse_aug_backgrounds(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select background images folder")
        if folder:
            self._aug_backgrounds_folder_edit.setText(folder)
            self._refresh_estimate()

    # ------------------------------------------------------------------
    # Slider
    # ------------------------------------------------------------------

    def _on_slider_changed(self, val: int) -> None:
        self._split_label.setText(f"{val} % train  /  {100 - val} % val")

    def _wire_estimate_signals(self) -> None:
        for widget in (
            self._aug_brightness_check,
            self._aug_crop_check,
            self._aug_cutout_check,
        ):
            widget.stateChanged.connect(self._refresh_estimate)

        for widget in (
            self._aug_brightness_count,
            self._aug_crop_count,
            self._aug_crop_margin,
            self._aug_cutout_count,
            self._aug_cutout_min,
            self._aug_cutout_max,
            self._aug_effect_strength,
            self._target_total_spin,
        ):
            widget.valueChanged.connect(self._refresh_estimate)

        self._aug_bg_mode_combo.currentIndexChanged.connect(self._refresh_estimate)
        self._aug_backgrounds_folder_edit.textChanged.connect(self._refresh_estimate)
        self._aug_objects_folder_edit.textChanged.connect(self._refresh_estimate)

    def _apply_aug_preset(self, name: str) -> None:
        if name == "mild":
            self._aug_brightness_check.setChecked(True)
            self._aug_brightness_count.setValue(1)
            self._aug_crop_check.setChecked(False)
            self._aug_crop_count.setValue(1)
            self._aug_cutout_check.setChecked(True)
            self._aug_cutout_count.setValue(1)
            self._aug_cutout_min.setValue(1)
            self._aug_cutout_max.setValue(4)
            self._aug_effect_strength.setValue(20)
            self._aug_crop_margin.setValue(12)
        elif name == "balanced":
            self._aug_brightness_check.setChecked(True)
            self._aug_brightness_count.setValue(2)
            self._aug_crop_check.setChecked(True)
            self._aug_crop_count.setValue(1)
            self._aug_cutout_check.setChecked(True)
            self._aug_cutout_count.setValue(2)
            self._aug_cutout_min.setValue(2)
            self._aug_cutout_max.setValue(8)
            self._aug_effect_strength.setValue(35)
            self._aug_crop_margin.setValue(20)
        elif name == "aggressive":
            self._aug_brightness_check.setChecked(True)
            self._aug_brightness_count.setValue(3)
            self._aug_crop_check.setChecked(True)
            self._aug_crop_count.setValue(2)
            self._aug_cutout_check.setChecked(True)
            self._aug_cutout_count.setValue(4)
            self._aug_cutout_min.setValue(3)
            self._aug_cutout_max.setValue(12)
            self._aug_effect_strength.setValue(50)
            self._aug_crop_margin.setValue(25)
        self._refresh_estimate()

    def _refresh_estimate(self) -> None:
        base = len(self._labeled_pairs)
        extra_per_image = 0
        if self._aug_brightness_check.isChecked():
            extra_per_image += self._aug_brightness_count.value()
        if self._aug_crop_check.isChecked():
            extra_per_image += self._aug_crop_count.value()
        if self._aug_cutout_check.isChecked():
            extra_per_image += self._aug_cutout_count.value()

        estimated_total = base * (1 + extra_per_image)
        target = self._target_total_spin.value()
        if estimated_total >= target:
            status = f"Target {target} reached"
            color = "#6f6"
        elif estimated_total == 0:
            status = "Scan a folder with labeled images"
            color = "#f96"
        else:
            missing = target - estimated_total
            status = f"Need about {missing} more"
            color = "#ffd166"

        self._estimate_label.setText(
            f"Estimated total after augmentation: ~{estimated_total} images "
            f"(base {base}, +~{estimated_total - base}) — {status}"
        )
        self._estimate_label.setStyleSheet(f"color: {color}; font-weight: 600;")

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    def _scan_folder(self) -> None:
        folder_str = self._folder_edit.text().strip()
        if not folder_str:
            self._scan_label.setText("No folder selected.")
            return

        source_root = Path(folder_str)
        if not source_root.is_dir():
            self._scan_label.setText("⚠ Folder not found.")
            self._scan_hint_label.setText("")
            self._labeled_pairs = []
            self._refresh_estimate()
            return

        scan_root = source_root
        if source_root.name.lower() != "images":
            images_child = source_root / "images"
            if images_child.is_dir():
                scan_root = images_child

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            all_images = sorted(
                f for f in scan_root.rglob("*")
                if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
            )

            labeled: list[tuple[Path, Path]] = []
            for img in all_images:
                obb_path, bb_path, legacy_path = LabelManager._derive_label_path_triplet(img)
                for lp in (obb_path, bb_path, legacy_path):
                    if lp.exists() and lp.stat().st_size > 0:
                        try:
                            content = lp.read_text(encoding="utf-8").strip()
                            if content:
                                labeled.append((img, lp))
                                break
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
            if scan_root != source_root:
                self._scan_hint_label.setText(f"Scanning images from: {scan_root}")
            else:
                self._scan_hint_label.setText("")
            self._refresh_estimate()
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

        if self._aug_cutout_check.isChecked():
            bg_mode = str(self._aug_bg_mode_combo.currentData() or "mix")
            bg_folder = self._aug_backgrounds_folder_edit.text().strip()
            if bg_mode == "folder" and (not bg_folder or not Path(bg_folder).is_dir()):
                QMessageBox.warning(
                    self,
                    "Background folder required",
                    "Background mode is set to 'Folder only', but no valid background folder is selected.",
                )
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
            n_train, n_val, n_augmented = self._do_build(out_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Build failed", f"Failed to create dataset:\n{exc}")
            return

        QMessageBox.information(
            self,
            "Dataset created",
            f"Dataset created at:\n{out_path}\n\n"
            f"Train : {n_train} image(s)\n"
            f"Val   : {n_val} image(s)\n"
            f"Augmented created : {n_augmented} image(s)",
        )
        self.accept()

    def _do_build(self, out_path: Path) -> tuple[int, int, int]:
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

        train_out_pairs: list[tuple[Path, Path]] = []
        for img_path, lp in train_pairs:
            out_img = out_path / "images" / "train" / img_path.name
            out_lbl = out_path / "labels" / "train" / lp.name
            _transfer(img_path, out_img)
            _transfer(lp, out_lbl)
            train_out_pairs.append((out_img, out_lbl))

        val_out_pairs: list[tuple[Path, Path]] = []
        for img_path, lp in val_pairs:
            out_img = out_path / "images" / "val" / img_path.name
            out_lbl = out_path / "labels" / "val" / lp.name
            _transfer(img_path, out_img)
            _transfer(lp, out_lbl)
            val_out_pairs.append((out_img, out_lbl))

        options = self._build_augmentation_options()
        n_augmented = 0
        if options is not None:
            n_augmented += generate_split_augmentations(
                out_path / "images" / "train",
                out_path / "labels" / "train",
                options,
            )
            n_augmented += generate_split_augmentations(
                out_path / "images" / "val",
                out_path / "labels" / "val",
                options,
            )

        # Parse class names from text area
        raw = self._class_edit.toPlainText().strip()
        class_names = [n.strip() for n in raw.splitlines() if n.strip()]

        # Write dataset YAML
        data = build_dataset_dict(out_path, class_names)
        yaml_path = out_path / f"{out_path.name}.yaml"
        save_dataset_yaml(data, yaml_path)

        return len(train_out_pairs), len(val_out_pairs), n_augmented

    def _build_augmentation_options(self) -> AugmentationOptions | None:
        brightness = self._aug_brightness_check.isChecked()
        crop = self._aug_crop_check.isChecked()
        cutout = self._aug_cutout_check.isChecked()
        if not (brightness or crop or cutout):
            return None

        min_objects = self._aug_cutout_min.value()
        max_objects = self._aug_cutout_max.value()
        if min_objects > max_objects:
            min_objects, max_objects = max_objects, min_objects

        return AugmentationOptions(
            brightness_enabled=brightness,
            brightness_count=self._aug_brightness_count.value(),
            brightness_strength=self._aug_effect_strength.value() / 100.0,
            safe_crop_enabled=crop,
            safe_crop_count=self._aug_crop_count.value(),
            safe_crop_max_ratio=self._aug_crop_margin.value() / 100.0,
            cutout_enabled=cutout,
            cutout_count=self._aug_cutout_count.value(),
            cutout_min_objects=min_objects,
            cutout_max_objects=max_objects,
            cutout_effect_strength=self._aug_effect_strength.value() / 100.0,
            background_images_dir=self._aug_backgrounds_folder_edit.text().strip(),
            background_source_mode=str(self._aug_bg_mode_combo.currentData() or "mix"),
            background_objects_dir=self._aug_objects_folder_edit.text().strip(),
        )
