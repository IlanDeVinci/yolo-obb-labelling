"""Intuitive cloud image upload dialog with drag-and-drop, folder and zip support."""

from __future__ import annotations

import mimetypes
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStyle,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}


@dataclass
class UploadCandidate:
    source_kind: str
    source_path: Path
    relative_path: str
    size: int
    zip_entry: str | None = None

    def display_name(self) -> str:
        return Path(self.relative_path).name or self.relative_path


class _DropZone(QFrame):
    dropped_paths = pyqtSignal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            "QFrame { border: 2px dashed #5f7fa0; border-radius: 8px; background: #232b33; }"
            "QFrame:hover { border-color: #7ea7d8; background: #283341; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)

        icon_label = QLabel()
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton)
        icon_label.setPixmap(icon.pixmap(24, 24))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(icon_label)

        title = QLabel("Drop images, folders, or ZIP files here")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        title.setStyleSheet("font-weight: 600; color: #d8e6f3;")
        layout.addWidget(title)

        subtitle = QLabel("or use the add buttons below")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        subtitle.setStyleSheet("color: #9fb4ca;")
        layout.addWidget(subtitle)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        urls = event.mimeData().urls()
        paths: list[Path] = []
        for url in urls:
            if url.isLocalFile():
                paths.append(Path(url.toLocalFile()))
        if paths:
            self.dropped_paths.emit(paths)
        event.acceptProposedAction()


class CloudUploadDialog(QDialog):
    uploads_completed = pyqtSignal(dict)

    def __init__(
        self,
        *,
        sync_agent,
        existing_paths: set[str] | None = None,
        default_prefix: str = "images",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cloud Upload Center")
        self.setMinimumSize(900, 620)

        self._sync_agent = sync_agent
        self._existing_paths = {self._normalize_posix(p) for p in (existing_paths or set()) if p}
        self._candidates: list[UploadCandidate] = []
        self._resolved_targets: list[str] = []
        self._summary: dict[str, int] = {"uploaded": 0, "skipped": 0, "failed": 0}
        self._thumbnail_cache: dict[str, QIcon] = {}

        # Keep only the drop zone dashed; suppress native dotted focus rectangles elsewhere.
        self.setStyleSheet(
            "QPushButton:focus, QToolButton:focus, QComboBox:focus, QLineEdit:focus, "
            "QTreeWidget:focus, QTreeWidget::item:focus { outline: none; }"
        )

        root = QVBoxLayout(self)

        header = QLabel("Upload images to your cloud project")
        header.setStyleSheet("font-size: 16px; font-weight: 700; color: #d9e7f5;")
        root.addWidget(header)

        hint = QLabel(
            "Supports files, full folders, and ZIP archives. "
            "Use drag-and-drop for the fastest workflow."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9fb2c5;")
        root.addWidget(hint)

        self._drop_zone = _DropZone()
        self._drop_zone.dropped_paths.connect(self._add_paths)
        root.addWidget(self._drop_zone)

        actions_row = QHBoxLayout()
        self._btn_add_files = QPushButton("Add Files")
        self._btn_add_files.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
        self._btn_add_files.clicked.connect(self._pick_files)
        actions_row.addWidget(self._btn_add_files)

        self._btn_add_folder = QPushButton("Add Folder")
        self._btn_add_folder.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self._btn_add_folder.clicked.connect(self._pick_folder)
        actions_row.addWidget(self._btn_add_folder)

        self._btn_add_zip = QPushButton("Add ZIP")
        self._btn_add_zip.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DriveFDIcon))
        self._btn_add_zip.clicked.connect(self._pick_zip)
        actions_row.addWidget(self._btn_add_zip)

        self._btn_clear = QPushButton("Clear")
        self._btn_clear.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogResetButton))
        self._btn_clear.clicked.connect(self._clear_all)
        actions_row.addWidget(self._btn_clear)
        actions_row.addStretch(1)
        root.addLayout(actions_row)

        options_row = QHBoxLayout()
        self._preserve_paths = QCheckBox("Preserve folder structure")
        self._preserve_paths.setChecked(True)
        self._preserve_paths.toggled.connect(self._rebuild_preview)
        options_row.addWidget(self._preserve_paths)

        self._overwrite_existing = QCheckBox("Overwrite existing cloud files")
        self._overwrite_existing.setChecked(False)
        self._overwrite_existing.toggled.connect(self._rebuild_preview)
        options_row.addWidget(self._overwrite_existing)
        options_row.addStretch(1)

        self._advanced_toggle = QToolButton(self)
        self._advanced_toggle.setText("Advanced")
        self._advanced_toggle.setCheckable(True)
        self._advanced_toggle.setChecked(False)
        self._advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._advanced_toggle.toggled.connect(self._toggle_advanced_options)
        options_row.addWidget(self._advanced_toggle)
        root.addLayout(options_row)

        self._advanced_panel = QWidget(self)
        self._advanced_panel.setVisible(False)
        adv_layout = QHBoxLayout(self._advanced_panel)
        adv_layout.setContentsMargins(0, 0, 0, 0)

        adv_layout.addWidget(QLabel("Cloud subfolder:"))
        self._prefix_preset = QComboBox(self)
        self._prefix_preset.addItems(["images", "images/inbox", "images/review", "Custom..."])
        self._prefix_preset.currentTextChanged.connect(self._on_prefix_preset_changed)
        adv_layout.addWidget(self._prefix_preset)

        self._prefix_edit = QLineEdit(default_prefix)
        self._prefix_edit.setPlaceholderText("images")
        self._prefix_edit.textChanged.connect(self._rebuild_preview)
        adv_layout.addWidget(self._prefix_edit, stretch=1)

        root.addWidget(self._advanced_panel)

        self._tree = QTreeWidget()
        self._tree.setAlternatingRowColors(True)
        self._tree.setIconSize(QPixmap(56, 56).size())
        self._tree.setHeaderLabels(["Preview", "Name", "Source", "Type", "Size", "Cloud Path", "Status"])
        self._tree.setRootIsDecorated(False)
        self._tree.setStyleSheet(
            "QTreeWidget { outline: none; border: 1px solid #3a4652; border-radius: 6px; }"
            "QTreeWidget:focus { outline: none; }"
            "QTreeWidget::item { border: none; }"
            "QTreeWidget::item:focus { outline: none; }"
            "QTreeView::item:selected { background: #35506e; }"
        )
        root.addWidget(self._tree, stretch=1)

        self._summary_label = QLabel("No upload items selected yet.")
        self._summary_label.setStyleSheet("color: #98afc6;")
        root.addWidget(self._summary_label)

        self._progress = QProgressBar()
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        root.addWidget(self._progress)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close,
            parent=self,
        )
        self._upload_button = QPushButton("Upload to Cloud")
        self._upload_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon))
        self._upload_button.clicked.connect(self._perform_upload)
        buttons.addButton(self._upload_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def upload_summary(self) -> dict[str, int]:
        return dict(self._summary)

    def _pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select images",
            "",
            "Images (*.jpg *.jpeg *.png *.bmp *.tiff *.tif *.webp);;All files (*.*)",
        )
        self._add_paths([Path(p) for p in paths])

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select folder")
        if folder:
            self._add_paths([Path(folder)])

    def _pick_zip(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Select ZIP archives", "", "ZIP (*.zip)")
        self._add_paths([Path(p) for p in paths])

    def _add_paths(self, paths: Iterable[Path]) -> None:
        added = 0
        for raw in paths:
            path = Path(raw)
            if not path.exists():
                continue
            if path.is_dir():
                added += self._add_folder(path)
            elif path.suffix.lower() == ".zip":
                added += self._add_zip(path)
            elif path.suffix.lower() in _IMAGE_SUFFIXES:
                self._candidates.append(
                    UploadCandidate(
                        source_kind="file",
                        source_path=path,
                        relative_path=path.name,
                        size=self._safe_size(path),
                    )
                )
                added += 1

        if added <= 0:
            QMessageBox.information(
                self,
                "No images found",
                "No supported image files were found in your selection.",
            )
        self._rebuild_preview()

    def _add_folder(self, folder: Path) -> int:
        count = 0
        for child in folder.rglob("*"):
            if not child.is_file() or child.suffix.lower() not in _IMAGE_SUFFIXES:
                continue
            rel = child.relative_to(folder).as_posix()
            self._candidates.append(
                UploadCandidate(
                    source_kind="folder",
                    source_path=child,
                    relative_path=rel,
                    size=self._safe_size(child),
                )
            )
            count += 1
        return count

    def _add_zip(self, zip_path: Path) -> int:
        count = 0
        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    suffix = Path(member.filename).suffix.lower()
                    if suffix not in _IMAGE_SUFFIXES:
                        continue
                    self._candidates.append(
                        UploadCandidate(
                            source_kind="zip",
                            source_path=zip_path,
                            relative_path=member.filename,
                            size=int(member.file_size),
                            zip_entry=member.filename,
                        )
                    )
                    count += 1
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "ZIP read error", f"Cannot read ZIP '{zip_path.name}': {exc}")
        return count

    def _clear_all(self) -> None:
        self._candidates.clear()
        self._resolved_targets.clear()
        self._summary = {"uploaded": 0, "skipped": 0, "failed": 0}
        self._thumbnail_cache.clear()
        self._rebuild_preview()

    def _toggle_advanced_options(self, show: bool) -> None:
        self._advanced_panel.setVisible(show)
        self._advanced_toggle.setArrowType(
            Qt.ArrowType.DownArrow if show else Qt.ArrowType.RightArrow
        )

    def _on_prefix_preset_changed(self, text: str) -> None:
        value = str(text or "").strip()
        if value and value != "Custom...":
            self._prefix_edit.setText(value)

    def _rebuild_preview(self) -> None:
        self._tree.clear()
        self._resolved_targets = []

        if not self._candidates:
            self._summary_label.setText("No upload items selected yet.")
            self._progress.setValue(0)
            self._progress.setMaximum(1)
            return

        preserve = self._preserve_paths.isChecked()
        overwrite = self._overwrite_existing.isChecked()
        prefix = self._prefix_edit.text().strip() or "images"
        taken_paths: set[str] = set()

        cloud_conflicts = 0
        total_size = 0

        for candidate in self._candidates:
            target = self._build_target_path(candidate, prefix, preserve, taken_paths)
            self._resolved_targets.append(target)
            total_size += int(candidate.size)

            in_cloud = target in self._existing_paths
            if in_cloud:
                cloud_conflicts += 1

            item = QTreeWidgetItem()
            item.setIcon(0, self._thumbnail_for_candidate(candidate))
            item.setText(1, candidate.display_name())
            item.setText(2, candidate.source_path.name)
            item.setText(3, candidate.source_kind.upper())
            item.setText(4, self._format_bytes(candidate.size))
            item.setText(5, target)

            if in_cloud and not overwrite:
                item.setText(6, "Will skip (already exists)")
                item.setForeground(6, self.palette().link())
            elif in_cloud and overwrite:
                item.setText(6, "Will overwrite")
            else:
                item.setText(6, "Ready")

            icon = self._icon_for_candidate(candidate)
            item.setIcon(1, icon)
            self._tree.addTopLevelItem(item)

        self._tree.resizeColumnToContents(0)
        self._tree.resizeColumnToContents(3)
        self._tree.resizeColumnToContents(4)
        self._tree.resizeColumnToContents(6)

        self._summary_label.setText(
            f"{len(self._candidates)} items selected, {self._format_bytes(total_size)} total"
            + (f", {cloud_conflicts} existing in cloud" if cloud_conflicts else "")
        )

    def _perform_upload(self) -> None:
        if not self._candidates:
            QMessageBox.information(self, "Cloud Upload", "Add files, folders, or ZIP archives first.")
            return

        if self._sync_agent is None:
            QMessageBox.warning(self, "Cloud Upload", "Cloud sync is not connected.")
            return

        preserve = self._preserve_paths.isChecked()
        overwrite = self._overwrite_existing.isChecked()
        prefix = self._prefix_edit.text().strip() or "images"

        taken_paths: set[str] = set()
        targets = [
            self._build_target_path(candidate, prefix, preserve, taken_paths)
            for candidate in self._candidates
        ]

        total = len(self._candidates)
        uploaded = 0
        skipped = 0
        failed = 0

        self._progress.setMaximum(total)
        self._progress.setValue(0)
        self._upload_button.setEnabled(False)

        for index, candidate in enumerate(self._candidates, start=1):
            target = targets[index - 1]
            self._progress.setValue(index - 1)
            self._summary_label.setText(f"Uploading {index}/{total}: {candidate.display_name()}")
            QApplication.processEvents()

            if target in self._existing_paths and not overwrite:
                skipped += 1
                self._set_row_status(index - 1, "Skipped (already exists)")
                continue

            try:
                payload = self._read_payload(candidate)
                content_type = mimetypes.guess_type(str(candidate.display_name()))[0] or "application/octet-stream"
                self._sync_agent.upload_image_via_signed_url(target, payload, content_type=content_type)
                self._existing_paths.add(target)
                uploaded += 1
                self._set_row_status(index - 1, "Uploaded")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                self._set_row_status(index - 1, f"Failed: {exc}")

        self._progress.setValue(total)
        self._upload_button.setEnabled(True)

        self._summary = {"uploaded": uploaded, "skipped": skipped, "failed": failed}
        self.uploads_completed.emit(dict(self._summary))
        self._summary_label.setText(
            f"Upload complete: {uploaded} uploaded, {skipped} skipped, {failed} failed"
        )
        QMessageBox.information(
            self,
            "Upload Summary",
            f"Uploaded: {uploaded}\nSkipped: {skipped}\nFailed: {failed}",
        )

    def _set_row_status(self, index: int, text: str) -> None:
        item = self._tree.topLevelItem(index)
        if item is None:
            return
        item.setText(6, text)

    def _build_target_path(
        self,
        candidate: UploadCandidate,
        prefix: str,
        preserve: bool,
        taken_paths: set[str],
    ) -> str:
        prefix_clean = self._normalize_posix(prefix)
        rel = candidate.relative_path if preserve else Path(candidate.relative_path).name
        rel_clean = self._sanitize_relative(rel)
        if not rel_clean:
            rel_clean = Path(candidate.display_name()).name or "image"

        target = f"{prefix_clean}/{rel_clean}" if prefix_clean else rel_clean
        target = self._normalize_posix(target)

        # Ensure unique output path when duplicates are selected in one batch.
        if target not in taken_paths:
            taken_paths.add(target)
            return target

        target_path = PurePosixPath(target)
        stem = target_path.stem or "image"
        suffix = target_path.suffix
        parent = str(target_path.parent)
        if parent == ".":
            parent = ""

        counter = 2
        while True:
            candidate_target = f"{parent}/{stem}_{counter}{suffix}" if parent else f"{stem}_{counter}{suffix}"
            candidate_target = self._normalize_posix(candidate_target)
            if candidate_target not in taken_paths:
                taken_paths.add(candidate_target)
                return candidate_target
            counter += 1

    def _read_payload(self, candidate: UploadCandidate) -> bytes:
        if candidate.source_kind == "zip" and candidate.zip_entry:
            with zipfile.ZipFile(candidate.source_path, "r") as archive:
                return archive.read(candidate.zip_entry)
        return candidate.source_path.read_bytes()

    def _sanitize_relative(self, value: str) -> str:
        path = PurePosixPath(str(value).replace("\\", "/"))
        parts = [p for p in path.parts if p not in {"", ".", ".."}]
        return "/".join(parts)

    def _normalize_posix(self, value: str) -> str:
        return str(value or "").replace("\\", "/").strip().strip("/")

    def _icon_for_candidate(self, candidate: UploadCandidate):
        if candidate.source_kind == "folder":
            return self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        if candidate.source_kind == "zip":
            return self.style().standardIcon(QStyle.StandardPixmap.SP_DriveFDIcon)
        return self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)

    def _thumbnail_for_candidate(self, candidate: UploadCandidate) -> QIcon:
        key = f"{candidate.source_path}|{candidate.zip_entry or ''}|{candidate.source_kind}"
        cached = self._thumbnail_cache.get(key)
        if cached is not None:
            return cached

        pixmap = QPixmap()
        if candidate.source_kind == "zip" and candidate.zip_entry:
            try:
                with zipfile.ZipFile(candidate.source_path, "r") as archive:
                    payload = archive.read(candidate.zip_entry)
                pixmap.loadFromData(payload)
            except Exception:
                pixmap = QPixmap()
        else:
            pixmap.load(str(candidate.source_path))

        if pixmap.isNull():
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
            self._thumbnail_cache[key] = icon
            return icon

        scaled = pixmap.scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        icon = QIcon(scaled)
        self._thumbnail_cache[key] = icon
        return icon

    def _safe_size(self, path: Path) -> int:
        try:
            return int(path.stat().st_size)
        except OSError:
            return 0

    def _format_bytes(self, size: int) -> str:
        size = float(max(0, int(size)))
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024.0 or unit == "TB":
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024.0
        return "0 B"
