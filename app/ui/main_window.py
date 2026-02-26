"""Main application window."""
from __future__ import annotations
import math
import shutil
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QSettings, QTimer
from PyQt6.QtGui import QAction, QKeySequence, QShortcut, QUndoStack
from PyQt6.QtWidgets import (
    QMainWindow,
    QSplitter,
    QFileDialog,
    QMessageBox,
    QInputDialog,
    QProgressDialog,
    QStatusBar,
    QLabel,
    QApplication,
    QWidget,
    QVBoxLayout,
)

from app.canvas.annotation_canvas import AnnotationCanvas
from app.commands.label_commands import (
    AddLabelCommand,
    AddLabelsCommand,
    DeleteLabelsCommand,
    ModifyLabelCommand,
    ToggleLabelModeCommand,
)
from app.models.dataset_manager import DatasetManager
from app.models.label_manager import LabelManager
from app.models.image_manager import ImageManager
from app.models.project import Project, ProjectManager
from app.models.obb_label import OBBLabel, BBoxLabel, Label
from app.ui.class_panel import ClassPanel
from app.ui.label_list_panel import LabelListPanel
from app.ui.image_browser_panel import ImageBrowserPanel
from app.ui.dialogs.dataset_dialog import DatasetDialog
from app.ui.dialogs.dataset_builder_dialog import DatasetBuilderDialog
from app.ui.dialogs.model_dialog import ModelDialog
from app.ui.dialogs.project_dialog import (
    NewProjectDialog,
    OpenProjectDialog,
    TeamManagerDialog,
    ProjectSettingsDialog,
)
from app.inference.yolo_predictor import (
    YoloPredictor,
    get_inference_diag_log_path,
    get_yolo_class,
    is_inference_available,
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("YOLO Labeller")
        self.resize(1400, 900)

        # Core models
        self._dataset = DatasetManager()
        self._label_mgr = LabelManager()
        self._image_mgr = ImageManager()
        self._predictor = YoloPredictor()

        # Project management (replaces old team_manager and project_state)
        self._project_mgr = ProjectManager()

        # Clipboard for copy/paste
        self._clipboard: list[Label] = []

        # All images (unfiltered) for team management
        self._all_images: list[Path] = []

        # Undo / Redo
        self._undo_stack = QUndoStack(self)

        # Auto-save: fires 3 s after the last label change
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(3000)
        self._autosave_timer.timeout.connect(self._autosave)

        # Project auto-save timer (separate from label auto-save)
        self._project_autosave_timer = QTimer(self)
        self._project_autosave_timer.setSingleShot(True)
        self._project_autosave_timer.setInterval(5000)
        self._project_autosave_timer.timeout.connect(self._autosave_project)

        # Restoring state flag
        self._restoring_state: bool = False

        # Persisted settings (app-level, not project-level)
        self._settings = QSettings("YoloLabeller", "App")

        # Get use_obb from current project or default
        self._use_obb: bool = True  # Will be updated from project
        self._show_class_names: bool = self._settings.value(
            "view/show_class_names", True, type=bool
        )
        self._accentuate_boxes: bool = self._settings.value(
            "view/accentuate_boxes", False, type=bool
        )
        self._model_path: str = ""
        self._model_conf: float = 0.7
        self._syncing_label_selection: bool = False

        # Build UI
        self._build_ui()
        self._build_menus()
        self._build_status_bar()
        self._wire_signals()

        # Restore window geometry
        geom = self._settings.value("window/geometry")
        if geom:
            self.restoreGeometry(geom)

        # Silently reopen the last project on startup
        self._try_restore_last_session()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root_layout.addWidget(splitter)

        # Left pane: classes + image browser
        left_splitter = QSplitter(Qt.Orientation.Vertical)
        left_splitter.setFixedWidth(200)

        self._class_panel = ClassPanel()
        left_splitter.addWidget(self._class_panel)

        self._browser = ImageBrowserPanel()
        self._browser.set_label_manager(self._label_mgr)
        self._browser.set_completion_provider(self._get_image_completion_status)
        left_splitter.addWidget(self._browser)
        left_splitter.setSizes([200, 500])

        splitter.addWidget(left_splitter)

        # Center: annotation canvas
        self._canvas = AnnotationCanvas(use_obb=self._use_obb)
        self._canvas.set_show_class_names(self._show_class_names)
        self._canvas.set_accentuate_boxes(self._accentuate_boxes)
        splitter.addWidget(self._canvas)
        splitter.setStretchFactor(1, 1)

        # Right pane: label list
        self._label_list = LabelListPanel()
        self._label_list.setFixedWidth(185)
        splitter.addWidget(self._label_list)

    def _build_menus(self) -> None:
        mb = self.menuBar()

        # ---- Projet ----
        proj_menu = mb.addMenu("&Projet")

        act = QAction("&Nouveau Projet...", self)
        act.setShortcut(QKeySequence("Ctrl+N"))
        act.triggered.connect(self._new_project)
        proj_menu.addAction(act)

        act = QAction("&Ouvrir Projet...", self)
        act.setShortcut(QKeySequence("Ctrl+O"))
        act.triggered.connect(self._open_project)
        proj_menu.addAction(act)

        proj_menu.addSeparator()

        act = QAction("&Sauvegarder", self)
        act.setShortcut(QKeySequence("Ctrl+S"))
        act.triggered.connect(self._save_current)
        proj_menu.addAction(act)

        proj_menu.addSeparator()

        act = QAction("&Importer des images...", self)
        act.setShortcut(QKeySequence("Ctrl+I"))
        act.triggered.connect(self._import_images)
        proj_menu.addAction(act)

        act = QAction("Importer un &dossier d'images...", self)
        act.triggered.connect(self._import_folder)
        proj_menu.addAction(act)

        act = QAction("&Ouvrir le dossier du projet", self)
        act.triggered.connect(self._open_project_folder)
        proj_menu.addAction(act)

        proj_menu.addSeparator()

        act = QAction("&Parametres du Projet...", self)
        act.triggered.connect(self._project_settings)
        proj_menu.addAction(act)

        proj_menu.addSeparator()

        act = QAction("&Quitter", self)
        act.setShortcut(QKeySequence("Alt+F4"))
        act.triggered.connect(self.close)
        proj_menu.addAction(act)

        # ---- Edit ----
        edit_menu = mb.addMenu("&Edit")

        undo_action = self._undo_stack.createUndoAction(self, "&Undo")
        undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        edit_menu.addAction(undo_action)

        redo_action = self._undo_stack.createRedoAction(self, "&Redo")
        redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        edit_menu.addAction(redo_action)

        edit_menu.addSeparator()

        act = QAction("Select &All Labels", self)
        act.setShortcut(QKeySequence.StandardKey.SelectAll)
        act.triggered.connect(self._canvas.select_all_labels)
        edit_menu.addAction(act)

        edit_menu.addSeparator()

        act = QAction("&Copy Selection", self)
        act.setShortcut(QKeySequence("Ctrl+C"))
        act.triggered.connect(self._copy_selected)
        edit_menu.addAction(act)

        act = QAction("&Paste", self)
        act.setShortcut(QKeySequence("Ctrl+V"))
        act.triggered.connect(self._paste_labels)
        edit_menu.addAction(act)

        # ---- Dataset ----
        ds_menu = mb.addMenu("&Dataset")

        act = QAction("&New Dataset…", self)
        act.triggered.connect(self._dataset_dialog)
        ds_menu.addAction(act)

        act = QAction("&Open Dataset YAML…", self)
        act.setShortcut(QKeySequence("Ctrl+Shift+O"))
        act.triggered.connect(self._open_yaml)
        ds_menu.addAction(act)

        ds_menu.addSeparator()

        act = QAction("&Build Dataset from Labeled Images…", self)
        act.triggered.connect(self._build_dataset)
        ds_menu.addAction(act)

        # ---- Annotate ----
        ann_menu = mb.addMenu("&Annotate")

        act = QAction("&Draw Mode", self)
        act.setShortcut(QKeySequence("W"))
        act.triggered.connect(lambda: self._canvas.set_mode(AnnotationCanvas.MODE_DRAW))
        ann_menu.addAction(act)

        act = QAction("&Select Mode", self)
        act.setShortcut(QKeySequence("S"))
        act.triggered.connect(lambda: self._canvas.set_mode(AnnotationCanvas.MODE_SELECT))
        ann_menu.addAction(act)

        ann_menu.addSeparator()

        self._act_toggle_obb = QAction("Toggle OBB/BBox Mode", self)
        self._act_toggle_obb.setShortcut(QKeySequence("Ctrl+B"))
        self._act_toggle_obb.triggered.connect(self._toggle_label_mode)
        ann_menu.addAction(self._act_toggle_obb)

        ann_menu.addSeparator()

        self._act_convert_to_obb = QAction("Convert Labels to &OBB", self)
        self._act_convert_to_obb.triggered.connect(lambda: self._convert_labels_to_mode(True))
        ann_menu.addAction(self._act_convert_to_obb)

        self._act_convert_to_bb = QAction("Convert Labels to &BBox", self)
        self._act_convert_to_bb.triggered.connect(lambda: self._convert_labels_to_mode(False))
        ann_menu.addAction(self._act_convert_to_bb)

        self._act_flip_orientation = QAction("Flip Selected Orientation 180°", self)
        self._act_flip_orientation.setShortcut(QKeySequence("Ctrl+Shift+L"))
        self._act_flip_orientation.triggered.connect(self._flip_selected_orientation)
        ann_menu.addAction(self._act_flip_orientation)

        self._act_rotate_sel_ccw = QAction("Rotate Selected -15°", self)
        self._act_rotate_sel_ccw.setShortcut(QKeySequence("Ctrl+Alt+Q"))
        self._act_rotate_sel_ccw.triggered.connect(lambda: self._rotate_selected_labels(-15.0))
        ann_menu.addAction(self._act_rotate_sel_ccw)

        self._act_rotate_sel_cw = QAction("Rotate Selected +15°", self)
        self._act_rotate_sel_cw.setShortcut(QKeySequence("Ctrl+Alt+E"))
        self._act_rotate_sel_cw.triggered.connect(lambda: self._rotate_selected_labels(15.0))
        ann_menu.addAction(self._act_rotate_sel_cw)

        self._act_scale_sel_up = QAction("Scale Selected +10%", self)
        self._act_scale_sel_up.setShortcut(QKeySequence("Ctrl+Alt+W"))
        self._act_scale_sel_up.triggered.connect(lambda: self._scale_selected_labels(1.10))
        ann_menu.addAction(self._act_scale_sel_up)

        self._act_scale_sel_down = QAction("Scale Selected -10%", self)
        self._act_scale_sel_down.setShortcut(QKeySequence("Ctrl+Alt+S"))
        self._act_scale_sel_down.triggered.connect(lambda: self._scale_selected_labels(0.90))
        ann_menu.addAction(self._act_scale_sel_down)

        ann_menu.addSeparator()

        act = QAction("Delete &Selected", self)
        act.setShortcut(QKeySequence("Delete"))
        act.triggered.connect(self._canvas._delete_selected)
        ann_menu.addAction(act)

        act = QAction("&Fit View", self)
        act.setShortcut(QKeySequence("F"))
        act.triggered.connect(self._canvas.fit_in_view)
        ann_menu.addAction(act)

        self._act_toggle_class_names = QAction("Show Class &Names", self)
        self._act_toggle_class_names.setCheckable(True)
        self._act_toggle_class_names.setChecked(self._show_class_names)
        self._act_toggle_class_names.setShortcut(QKeySequence("Ctrl+Shift+H"))
        self._act_toggle_class_names.triggered.connect(self._toggle_class_names)
        ann_menu.addAction(self._act_toggle_class_names)

        self._act_toggle_accentuated_boxes = QAction("Very &Accentuated Boxes", self)
        self._act_toggle_accentuated_boxes.setCheckable(True)
        self._act_toggle_accentuated_boxes.setChecked(self._accentuate_boxes)
        self._act_toggle_accentuated_boxes.setShortcut(QKeySequence("Ctrl+Shift+U"))
        self._act_toggle_accentuated_boxes.triggered.connect(self._toggle_accentuated_boxes)
        ann_menu.addAction(self._act_toggle_accentuated_boxes)

        # ---- Model ----
        model_menu = mb.addMenu("&Model")

        self._act_load_model = QAction("&Load Model…", self)
        self._act_load_model.triggered.connect(self._load_model)
        model_menu.addAction(self._act_load_model)

        model_menu.addSeparator()

        self._act_run_current = QAction("Run on &Current Image", self)
        self._act_run_current.setShortcut(QKeySequence("Ctrl+R"))
        self._act_run_current.triggered.connect(self._run_on_current)
        # Always enabled — runtime check inside _run_on_current gives a clear message
        model_menu.addAction(self._act_run_current)

        self._act_run_all = QAction("Run on &All Images", self)
        self._act_run_all.setShortcut(QKeySequence("Ctrl+Shift+R"))
        self._act_run_all.triggered.connect(self._run_on_all)
        model_menu.addAction(self._act_run_all)

        self._update_model_indicator()

        # ---- View ----
        view_menu = mb.addMenu("&View")

        act = QAction("Zoom &In", self)
        act.setShortcut(QKeySequence("Ctrl+="))
        act.triggered.connect(lambda: self._canvas.scale(1.15, 1.15))
        view_menu.addAction(act)

        act = QAction("Zoom &Out", self)
        act.setShortcut(QKeySequence("Ctrl+-"))
        act.triggered.connect(lambda: self._canvas.scale(1 / 1.15, 1 / 1.15))
        view_menu.addAction(act)

        act = QAction("&Reset Zoom", self)
        act.setShortcut(QKeySequence("Ctrl+0"))
        act.triggered.connect(self._canvas.fit_in_view)
        view_menu.addAction(act)

        # ---- Equipe ----
        team_menu = mb.addMenu("&Equipe")

        act = QAction("&Choisir mon membre...", self)
        act.setShortcut(QKeySequence("Ctrl+M"))
        act.triggered.connect(self._choose_active_member)
        team_menu.addAction(act)

        act = QAction("&Gerer les membres...", self)
        act.setShortcut(QKeySequence("Ctrl+T"))
        act.triggered.connect(self._team_dialog)
        team_menu.addAction(act)

        team_menu.addSeparator()

        act = QAction("&Distribuer les images", self)
        act.triggered.connect(self._distribute_images)
        team_menu.addAction(act)

        act = QAction("&Reassign Selected Images...", self)
        act.setShortcut(QKeySequence("Ctrl+Shift+M"))
        act.triggered.connect(self._reassign_selected_images)
        team_menu.addAction(act)

        act = QAction("&Voir toutes les images", self)
        act.triggered.connect(self._show_all_images)
        team_menu.addAction(act)

        # ---- Help ----
        help_menu = mb.addMenu("&Help")
        act = QAction("&Keyboard Shortcuts", self)
        act.triggered.connect(self._show_shortcuts)
        help_menu.addAction(act)

        act = QAction("Status Store &Health", self)
        act.triggered.connect(self._show_status_store_health)
        help_menu.addAction(act)

        # ---- Status ----
        status_menu = mb.addMenu("&Status")

        self._act_set_completed = QAction("Set as &Completed", self)
        self._act_set_completed.setShortcut(QKeySequence("Ctrl+Shift+K"))
        self._act_set_completed.triggered.connect(
            lambda: self._set_selected_images_completion("completed")
        )
        status_menu.addAction(self._act_set_completed)

        self._act_set_in_progress = QAction("Set as &In Progress", self)
        self._act_set_in_progress.setShortcut(QKeySequence("Ctrl+Shift+J"))
        self._act_set_in_progress.triggered.connect(
            lambda: self._set_selected_images_completion("in_progress")
        )
        status_menu.addAction(self._act_set_in_progress)

    def _build_status_bar(self) -> None:
        sb = QStatusBar()
        self.setStatusBar(sb)

        self._lbl_team = QLabel("")
        self._lbl_team.setStyleSheet("padding: 0 6px; color: #8be; font-weight: bold;")
        sb.addPermanentWidget(self._lbl_team)

        # Label format indicator (OBB vs BBox)
        self._lbl_format = QLabel("OBB" if self._use_obb else "BBOX")
        self._lbl_format.setStyleSheet(
            "padding: 2px 8px; font-weight: bold; border-radius: 3px; "
            + ("background: #2a82da; color: white;" if self._use_obb else "background: #da822a; color: white;")
        )
        self._lbl_format.setToolTip("Click to toggle between OBB and BBox mode (Ctrl+B)")
        self._lbl_format.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lbl_format.mousePressEvent = lambda e: self._toggle_label_mode()
        sb.addPermanentWidget(self._lbl_format)

        self._lbl_mode = QLabel("SELECT")
        self._lbl_mode.setStyleSheet("padding: 0 6px; font-weight: bold;")
        sb.addPermanentWidget(self._lbl_mode)

        self._lbl_dirty = QLabel("")
        self._lbl_dirty.setStyleSheet("padding: 0 6px; color: #f90;")
        sb.addPermanentWidget(self._lbl_dirty)

        self._lbl_index = QLabel("No images")
        self._lbl_index.setStyleSheet("padding: 0 6px;")
        sb.addPermanentWidget(self._lbl_index)

        self._lbl_hint = QLabel("")
        sb.addWidget(self._lbl_hint)

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _wire_signals(self) -> None:
        # Canvas → label manager / undo stack
        self._canvas.label_added.connect(self._on_label_added)
        self._canvas.labels_delete_requested.connect(self._on_delete_requested)
        self._canvas.label_modified.connect(self._on_label_modified)
        self._canvas.labels_changed.connect(self._on_labels_changed)
        self._canvas.status_message.connect(self._lbl_hint.setText)
        self._canvas.mode_changed.connect(self._on_mode_changed)

        # Class panel
        self._class_panel.class_selected.connect(self._canvas.set_active_class)

        # Image browser
        self._browser.image_selected.connect(self._navigate_to_image)

        # Label list <-> canvas selection sync
        self._label_list.labels_selection_changed.connect(self._on_label_list_selection_changed)
        self._canvas.label_selection_changed.connect(self._on_canvas_label_selection_changed)

        # Navigation shortcuts (window-level so they work regardless of focus)
        for key in ("A", "Left"):
            s = QShortcut(QKeySequence(key), self)
            s.activated.connect(self._navigate_prev)
        for key in ("D", "Right", "Space"):
            s = QShortcut(QKeySequence(key), self)
            s.activated.connect(self._navigate_next)
        for i in range(10):
            s = QShortcut(QKeySequence(str(i)), self)
            idx = i
            s.activated.connect(lambda _idx=idx: self._class_panel.select_class(_idx))

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _navigate_to_image(self, path: Path) -> None:
        if not self._maybe_save_before_leaving():
            return
        self._image_mgr.go_to_path(path)
        self._load_current_image()

    def _navigate_next(self) -> None:
        if not self._maybe_save_before_leaving():
            return
        self._image_mgr.next()
        self._load_current_image()

    def _navigate_prev(self) -> None:
        if not self._maybe_save_before_leaving():
            return
        self._image_mgr.prev()
        self._load_current_image()

    def _load_current_image(self) -> None:
        path = self._image_mgr.current_image
        if path is None:
            return

        # Stop any pending autosave for the previous image
        self._autosave_timer.stop()

        # Clear undo history — undo across image navigation is not supported
        self._undo_stack.clear()

        self._canvas.load_image(path)
        self._label_mgr.load_for_image(path)
        self._canvas.load_labels(self._label_mgr.labels)
        self._refresh_label_list()
        self._update_index_label()
        self._browser.select_index(self._image_mgr.current_index)
        self._update_dirty_indicator()
        self._update_completion_action()

        # Persist the new index
        self._schedule_project_autosave()

    def _maybe_save_before_leaving(self) -> bool:
        """Prompt to save if dirty. Returns False if user cancelled."""
        if not self._label_mgr.is_dirty:
            return True
        reply = QMessageBox.question(
            self,
            "Unsaved changes",
            "Save labels for the current image?",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return False
        if reply == QMessageBox.StandardButton.Yes:
            self._save_current()
        return True

    # ------------------------------------------------------------------
    # Label callbacks (canvas → undo stack)
    # ------------------------------------------------------------------

    def _on_label_added(self, label) -> None:
        """Label was drawn; wrap in an undo-able command and add to model."""
        self._label_mgr.add_label(label)
        cmd = AddLabelCommand(label, self._canvas, self._label_mgr)
        self._undo_stack.push(cmd)   # redo() skipped (first_run=True)
        self._refresh_label_list()

    def _on_delete_requested(self, labels: list) -> None:
        """Delete key pressed; wrap in an undo-able command (redo removes items)."""
        cmd = DeleteLabelsCommand(labels, self._canvas, self._label_mgr)
        self._undo_stack.push(cmd)   # redo() fires immediately → actual deletion
        self._refresh_label_list()

    def _on_label_modified(self, label, old_points, new_points) -> None:
        """A label was moved, corner-dragged, or rotated."""
        cmd = ModifyLabelCommand(label, old_points, new_points, self._canvas)
        self._undo_stack.push(cmd)
        self._refresh_label_list()

    def _on_labels_changed(self) -> None:
        self._auto_mark_current_image_in_progress()
        self._refresh_label_list()
        self._update_dirty_indicator()
        # Restart the auto-save countdown
        self._autosave_timer.start()

    def _refresh_label_list(self) -> None:
        class_names = self._class_panel.class_names() or self._dataset.class_names
        self._label_list.set_class_names(class_names)
        self._label_list.refresh(self._label_mgr.labels)

    def _on_label_list_selection_changed(self, indices: list[int]) -> None:
        if self._syncing_label_selection:
            return
        self._syncing_label_selection = True
        try:
            self._canvas.select_label_indices(indices)
        finally:
            self._syncing_label_selection = False

    def _on_canvas_label_selection_changed(self, selected_indices: list[int]) -> None:
        if self._syncing_label_selection:
            return
        self._syncing_label_selection = True
        try:
            self._label_list.select_indices(selected_indices)
        finally:
            self._syncing_label_selection = False

    # ------------------------------------------------------------------
    # Auto-save
    # ------------------------------------------------------------------

    def _autosave(self) -> None:
        """Called 3 s after the last label change. Saves and shows a status flash."""
        if not self._label_mgr.is_dirty:
            return
        self._save_current()
        self._lbl_hint.setText("Auto-saved ✓")
        QTimer.singleShot(2000, lambda: self._lbl_hint.setText(""))

    # ------------------------------------------------------------------
    # Project state persistence
    # ------------------------------------------------------------------

    def _try_restore_last_session(self) -> None:
        """Called once at startup — reopens recent project or first available."""
        project_path: Path | None = None

        last_project_str: str = self._settings.value("recent/project_file", "", type=str)
        if last_project_str:
            candidate = Path(last_project_str)
            if candidate.exists():
                project_path = candidate

        if project_path is None:
            projects = self._project_mgr.list_projects()
            if projects:
                # Prefer most recently modified project file; fallback to first listed.
                sorted_by_mtime = sorted(
                    projects,
                    key=lambda p: p[1].stat().st_mtime if p[1].exists() else 0,
                    reverse=True,
                )
                project_path = sorted_by_mtime[0][1] if sorted_by_mtime else projects[0][1]

        if project_path is None:
            return

        project = self._project_mgr.open_project(project_path)
        if not project:
            return

        self._settings.setValue("recent/project_file", str(project_path))

        self._restoring_state = True
        try:
            # Load model settings from project
            self._model_path = project.model_path
            self._model_conf = project.model_confidence

            self._apply_project_to_ui(project)
            self._lbl_hint.setText(f"Projet '{project.name}' restaure")
            QTimer.singleShot(0, self._prompt_team_member_on_startup)
        except Exception:  # noqa: BLE001 — never crash on auto-restore
            pass
        finally:
            self._restoring_state = False

    def _apply_project_to_ui(self, project: Project) -> None:
        """Apply project settings to the UI."""
        # Update window title
        self._update_window_title()

        # Set label mode
        self._use_obb = project.use_obb
        self._canvas.set_use_obb(self._use_obb)
        self._canvas.set_show_class_names(self._show_class_names)
        self._canvas.set_accentuate_boxes(self._accentuate_boxes)
        self._label_mgr.set_use_obb(self._use_obb)
        self._update_format_indicator()
        self._update_model_indicator()

        # Load dataset if specified
        if project.yaml_path and Path(project.yaml_path).is_file():
            self._load_dataset_yaml(Path(project.yaml_path))
        else:
            dataset_folder = self._resolve_project_dataset_folder(project)
            if dataset_folder and dataset_folder.is_dir():
                self._load_folder_into_ui(dataset_folder)

        # Set class names
        if project.class_names:
            self._dataset.class_names = list(project.class_names)
            self._class_panel.set_classes(project.class_names)
            self._canvas.set_class_names(project.class_names)
            self._label_list.set_class_names(project.class_names)

        # Apply team filter
        if project.active_team_member:
            self._apply_team_filter()

        # Restore position
        total = len(self._image_mgr.images)
        idx = min(project.current_index, max(0, total - 1))
        if idx > 0:
            self._image_mgr.go_to(idx)
            self._load_current_image()

    def _resolve_project_dataset_folder(self, project: Project) -> Path | None:
        resolved = self._project_mgr.resolve_dataset_folder(project)
        if resolved and resolved.is_dir():
            return resolved

        project_folder = self._project_mgr.get_project_folder()
        if project_folder:
            local_images = project_folder / "images"
            if local_images.is_dir():
                project.dataset_folder = "images"
                self._project_mgr.save_current()
                return local_images
        return None

    def _normalize_project_completion_states(self) -> None:
        """Normalize completion states for all known images in the open project.

        This is intentionally in-memory only.
        It must never write shared image-status JSON files automatically
        during startup, split changes, or folder loading.
        """
        project = self._project_mgr.current_project
        if not project:
            return

        known_images: list[Path] = []
        if self._dataset.train_images or self._dataset.val_images:
            known_images = list(self._dataset.train_images) + list(self._dataset.val_images)
        elif self._all_images:
            known_images = list(self._all_images)

        if not known_images:
            return

        known_names = {img.name for img in known_images}
        normalized: dict[str, str] = {}

        for image_name, status in project.image_completion.items():
            if image_name not in known_names:
                continue
            normalized_status = str(status).strip().lower()
            if normalized_status in {"in_progress", "completed"}:
                normalized[image_name] = normalized_status

        if normalized != project.image_completion:
            project.image_completion = normalized

    def _load_folder_into_ui(self, folder: Path) -> None:
        """Load a folder into the UI without creating a new project."""
        existing_classes = self._class_panel.class_names()
        if not existing_classes:
            existing_classes = ["object"]
            self._class_panel.set_classes(existing_classes)
            self._canvas.set_class_names(existing_classes)
            self._label_list.set_class_names(existing_classes)
        self._dataset.load_from_folder(folder, existing_classes)
        self._all_images = list(self._dataset.train_images)
        self._image_mgr.load_split(self._all_images, "")
        self._normalize_project_completion_states()
        self._browser.set_images(self._image_mgr.images)
        self._load_current_image()

    # ------------------------------------------------------------------
    # Project actions
    # ------------------------------------------------------------------

    def _new_project(self) -> None:
        """Create a new project."""
        if not self._maybe_save_before_leaving():
            return

        dlg = NewProjectDialog(self)
        if dlg.exec() != NewProjectDialog.DialogCode.Accepted:
            return

        project = self._project_mgr.create_project(dlg.project_name)
        self._update_window_title()
        self._settings.setValue("recent/project_file", str(self._project_mgr.current_path))

        # Create images subfolder in project folder
        project_folder = self._project_mgr.get_project_folder()
        if project_folder:
            images_folder = project_folder / "images"
            images_folder.mkdir(exist_ok=True)
            labels_folder = project_folder / "labels"
            labels_folder.mkdir(exist_ok=True)

            # Set the images folder as dataset folder
            project.dataset_folder = "images"
            self._project_mgr.save_current()

            # Open the project folder in explorer
            self._open_in_explorer(project_folder)

            # Load the (empty) images folder
            self._load_folder_into_ui(images_folder)

        self._lbl_hint.setText(f"Projet '{project.name}' cree - ajoutez des images via Projet > Importer des images")

    def _open_in_explorer(self, path: Path) -> None:
        """Open a folder in the system file explorer."""
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            pass  # Silently fail if we can't open explorer

    def _open_project_folder(self) -> None:
        """Open the current project folder in explorer."""
        project_folder = self._project_mgr.get_project_folder()
        if project_folder and project_folder.exists():
            self._open_in_explorer(project_folder)
        else:
            QMessageBox.information(
                self, "Pas de projet", "Creez ou ouvrez d'abord un projet."
            )

    def _import_images(self) -> None:
        """Import images into the current project."""
        project = self._project_mgr.current_project
        if not project:
            QMessageBox.information(
                self, "Pas de projet", "Creez ou ouvrez d'abord un projet."
            )
            return

        project_folder = self._project_mgr.get_project_folder()
        if not project_folder:
            return

        images_folder = project_folder / "images"
        images_folder.mkdir(exist_ok=True)

        # Ask user to select images
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Importer des images",
            "",
            "Images (*.jpg *.jpeg *.png *.bmp *.tiff *.tif *.webp);;Tous les fichiers (*.*)",
        )

        if not files:
            return

        # Copy images to project folder
        imported = 0
        skipped = 0
        progress = QProgressDialog("Import des images...", "Annuler", 0, len(files), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)

        for i, src_path in enumerate(files):
            if progress.wasCanceled():
                break
            progress.setValue(i)
            QApplication.processEvents()

            src = Path(src_path)
            dest = images_folder / src.name

            # Handle duplicate names
            if dest.exists():
                # Add number suffix
                base = dest.stem
                ext = dest.suffix
                counter = 1
                while dest.exists():
                    dest = images_folder / f"{base}_{counter}{ext}"
                    counter += 1

            try:
                shutil.copy2(src, dest)
                imported += 1
            except Exception:
                skipped += 1

        progress.setValue(len(files))

        # Update project and reload
        project.dataset_folder = "images"
        self._project_mgr.save_current()
        self._load_folder_into_ui(images_folder)

        self._lbl_hint.setText(f"{imported} image(s) importee(s)" + (f", {skipped} ignoree(s)" if skipped else ""))

    def _import_folder(self) -> None:
        """Import all images from a folder into the current project."""
        project = self._project_mgr.current_project
        if not project:
            QMessageBox.information(
                self, "Pas de projet", "Creez ou ouvrez d'abord un projet."
            )
            return

        project_folder = self._project_mgr.get_project_folder()
        if not project_folder:
            return

        # Ask user to select a folder
        source_folder = QFileDialog.getExistingDirectory(
            self, "Selectionner un dossier d'images"
        )
        if not source_folder:
            return

        images_folder = project_folder / "images"
        images_folder.mkdir(exist_ok=True)

        # Find all images in source folder
        image_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}
        source_path = Path(source_folder)
        image_files = [f for f in source_path.iterdir() if f.suffix.lower() in image_extensions]

        if not image_files:
            QMessageBox.information(self, "Aucune image", "Aucune image trouvee dans ce dossier.")
            return

        # Copy images
        imported = 0
        progress = QProgressDialog("Import des images...", "Annuler", 0, len(image_files), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)

        for i, src in enumerate(image_files):
            if progress.wasCanceled():
                break
            progress.setValue(i)
            QApplication.processEvents()

            dest = images_folder / src.name
            if dest.exists():
                base = dest.stem
                ext = dest.suffix
                counter = 1
                while dest.exists():
                    dest = images_folder / f"{base}_{counter}{ext}"
                    counter += 1

            try:
                shutil.copy2(src, dest)
                imported += 1
            except Exception:
                pass

        progress.setValue(len(image_files))

        # Update and reload
        project.dataset_folder = "images"
        self._project_mgr.save_current()
        self._load_folder_into_ui(images_folder)

        self._lbl_hint.setText(f"{imported} image(s) importee(s) depuis {source_path.name}")

    def _open_project(self) -> None:
        """Open an existing project."""
        if not self._maybe_save_before_leaving():
            return

        dlg = OpenProjectDialog(self._project_mgr, self)
        if dlg.exec() != OpenProjectDialog.DialogCode.Accepted or not dlg.selected_path:
            return

        project = self._project_mgr.open_project(dlg.selected_path)
        if not project:
            QMessageBox.warning(self, "Erreur", "Impossible d'ouvrir le projet")
            return

        # Load model settings from project
        self._model_path = project.model_path
        self._model_conf = project.model_confidence
        self._update_model_indicator()

        self._settings.setValue("recent/project_file", str(dlg.selected_path))
        self._apply_project_to_ui(project)
        self._lbl_hint.setText(f"Projet '{project.name}' ouvert")
        QTimer.singleShot(0, self._prompt_team_member_on_startup)

    def _project_settings(self) -> None:
        """Open project settings dialog."""
        project = self._project_mgr.current_project
        if not project:
            QMessageBox.information(self, "Aucun projet", "Creez ou ouvrez d'abord un projet.")
            return

        dlg = ProjectSettingsDialog(project, self)
        if dlg.exec() == ProjectSettingsDialog.DialogCode.Accepted:
            # Apply changes
            self._class_panel.set_classes(project.class_names)
            self._canvas.set_class_names(project.class_names)
            self._label_list.set_class_names(project.class_names)

            dataset_folder = self._resolve_project_dataset_folder(project)
            if dataset_folder and dataset_folder.is_dir():
                self._load_folder_into_ui(dataset_folder)

            self._update_window_title()
            self._project_mgr.save_current()

    def _save_current(self) -> None:
        """Save labels and project state."""
        # Save labels
        self._label_mgr.save()
        idx = self._image_mgr.current_index
        self._browser.refresh_item(idx)
        self._update_dirty_indicator()

        # Save project
        project = self._project_mgr.current_project
        if project:
            current_img = self._image_mgr.current_image
            if current_img is not None and self._label_mgr.labels:
                current_status = project.get_image_completion(current_img.name)
                if not current_status:
                    project.set_image_completion(current_img.name, "in_progress")
                    self._project_mgr.persist_image_completion(
                        current_img.name,
                        "in_progress",
                        current_img,
                    )

            project.current_index = self._image_mgr.current_index
            project.class_names = self._class_panel.class_names()
            project.use_obb = self._use_obb
            self._project_mgr.save_current()
            self._project_mgr.save_user_state()
            self._lbl_hint.setText("Sauvegarde effectuee")
        else:
            self._lbl_hint.setText("Labels sauvegardes")

    def _autosave_project(self) -> None:
        """Auto-save the current project."""
        project = self._project_mgr.current_project
        if project:
            project.current_index = self._image_mgr.current_index
            project.class_names = self._class_panel.class_names()
            project.use_obb = self._use_obb
            self._project_mgr.save_current()
            self._project_mgr.save_user_state()

    def _get_image_completion_status(self, image_path: Path) -> str:
        project = self._project_mgr.current_project
        if not project:
            return ""
        return project.get_image_completion(image_path.name)

    def _set_current_image_completion(self, status: str) -> None:
        project = self._project_mgr.current_project
        img = self._image_mgr.current_image
        if not project or img is None:
            return

        project.set_image_completion(img.name, status)
        self._project_mgr.persist_image_completion(img.name, status, img)
        self._project_mgr.save_user_state()
        self._browser.refresh_item(self._image_mgr.current_index)
        self._update_completion_action()
        label = "In Progress" if status == "in_progress" else "Completed"
        self._lbl_hint.setText(f"{img.name}: {label}")

    def _set_selected_images_completion(self, status: str) -> None:
        project = self._project_mgr.current_project
        if not project:
            return

        selected = self._browser.selected_images()
        if not selected:
            img = self._image_mgr.current_image
            if img is None:
                return
            selected = [img]

        selected_names = {p.name for p in selected}
        for img_path in selected:
            project.set_image_completion(img_path.name, status)
            self._project_mgr.persist_image_completion(img_path.name, status, img_path)

        self._project_mgr.save_user_state()

        for idx, img in enumerate(self._image_mgr.images):
            if img.name in selected_names:
                self._browser.refresh_item(idx)

        self._update_completion_action()
        label = "In Progress" if status == "in_progress" else "Completed"
        n = len(selected)
        if n == 1:
            self._lbl_hint.setText(f"{selected[0].name}: {label}")
        else:
            self._lbl_hint.setText(f"{n} images set as {label}")

    def _toggle_current_image_completion(self) -> None:
        img = self._image_mgr.current_image
        if img is None:
            return
        current = self._get_image_completion_status(img)
        target = "in_progress" if current == "completed" else "completed"
        self._set_current_image_completion(target)

    def _update_completion_action(self) -> None:
        has_completed_action = hasattr(self, "_act_set_completed")
        has_in_progress_action = hasattr(self, "_act_set_in_progress")
        has_toggle_action = hasattr(self, "_act_toggle_completion")
        if not (has_completed_action or has_in_progress_action or has_toggle_action):
            return

        img = self._image_mgr.current_image
        if img is None:
            if has_completed_action:
                self._act_set_completed.setEnabled(False)
            if has_in_progress_action:
                self._act_set_in_progress.setEnabled(False)
            if has_toggle_action:
                self._act_toggle_completion.setEnabled(False)
                self._act_toggle_completion.setText("Mark Current Image &Completed")
            return

        current = self._get_image_completion_status(img)

        if has_completed_action:
            self._act_set_completed.setEnabled(current != "completed")
        if has_in_progress_action:
            self._act_set_in_progress.setEnabled(current != "in_progress")

        if has_toggle_action:
            self._act_toggle_completion.setEnabled(True)
            if current == "completed":
                self._act_toggle_completion.setText("Mark Current Image &In Progress")
            else:
                self._act_toggle_completion.setText("Mark Current Image &Completed")

    def _auto_mark_current_image_in_progress(self) -> None:
        """Default completion state when first labels appear on an image."""
        project = self._project_mgr.current_project
        img = self._image_mgr.current_image
        if not project or img is None:
            return
        if not self._label_mgr.labels:
            return

        current = project.get_image_completion(img.name)
        if current:
            return

        project.set_image_completion(img.name, "in_progress")
        self._browser.refresh_item(self._image_mgr.current_index)
        self._update_completion_action()

    def _schedule_project_autosave(self) -> None:
        """Schedule a project auto-save."""
        self._project_autosave_timer.start()

    def _update_window_title(self) -> None:
        """Update window title with project name."""
        project = self._project_mgr.current_project
        if project:
            self.setWindowTitle(f"{project.name} - YOLO Labeller")
        else:
            self.setWindowTitle("YOLO Labeller")

    def _update_format_indicator(self) -> None:
        """Update the OBB/BBOX indicator in status bar."""
        self._lbl_format.setText("OBB" if self._use_obb else "BBOX")
        self._lbl_format.setStyleSheet(
            "padding: 2px 8px; font-weight: bold; border-radius: 3px; "
            + ("background: #2a82da; color: white;" if self._use_obb else "background: #da822a; color: white;")
        )

    def _open_yaml(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Dataset YAML", "", "YAML files (*.yaml *.yml)"
        )
        if not path:
            return
        self._load_dataset_yaml(Path(path))

    def _dataset_dialog(self) -> None:
        dlg = DatasetDialog(self)
        if dlg.exec() != DatasetDialog.DialogCode.Accepted:
            return
        if dlg.mode == "create":
            yaml_path = self._dataset.create_dataset(
                dlg.result_path, dlg.dataset_name, dlg.result_classes
            )
            self._class_panel.set_classes(dlg.result_classes)
            self._canvas.set_class_names(dlg.result_classes)
            self._label_list.set_class_names(dlg.result_classes)
            QMessageBox.information(self, "Dataset created", f"Dataset created at:\n{yaml_path}")
            self._load_split_images("train")
        else:
            self._load_dataset_yaml(dlg.result_path)

    def _load_dataset_yaml(self, yaml_path: Path) -> None:
        if not self._maybe_save_before_leaving():
            return
        try:
            self._dataset.load_from_yaml(yaml_path)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load YAML:\n{exc}")
            return
        self._class_panel.set_classes(self._dataset.class_names)
        self._canvas.set_class_names(self._dataset.class_names)
        self._label_list.set_class_names(self._dataset.class_names)

        # Update project if one is open
        project = self._project_mgr.current_project
        if project:
            project.yaml_path = str(yaml_path)
            project.class_names = list(self._dataset.class_names)
            self._schedule_project_autosave()

        self._load_split_images("train")

    def _switch_split(self, split: str) -> None:
        if not self._maybe_save_before_leaving():
            return
        self._load_split_images(split)

    def _load_split_images(self, split: str) -> None:
        images = self._dataset.train_images if split == "train" else self._dataset.val_images
        self._all_images = list(images)

        self._normalize_project_completion_states()

        # Apply team filter if active
        project = self._project_mgr.current_project
        if project and project.active_team_member and project.is_distributed():
            filtered = project.get_member_images(project.active_team_member, self._all_images)
            self._image_mgr.load_split(filtered, split)
            self._browser.set_images(filtered)
        else:
            self._image_mgr.load_split(images, split)
            self._browser.set_images(images)

        self._update_window_title()
        self._load_current_image()

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Dataset builder
    # ------------------------------------------------------------------

    def _build_dataset(self) -> None:
        """Open the Dataset Builder dialog."""
        project_folder = self._project_mgr.get_project_folder()
        default_folder = str(project_folder) if project_folder else ""
        project = self._project_mgr.current_project
        resolved_dataset = self._project_mgr.resolve_dataset_folder(project)
        if resolved_dataset and resolved_dataset.is_dir():
            default_folder = str(resolved_dataset)
        elif project_folder:
            images_dir = project_folder / "images"
            if images_dir.is_dir():
                default_folder = str(images_dir)
        default_names = list(self._dataset.class_names or self._class_panel.class_names())
        dlg = DatasetBuilderDialog(default_folder, default_names, self)
        dlg.exec()

    # ------------------------------------------------------------------
    # Model actions
    # ------------------------------------------------------------------

    def _load_model(self) -> None:
        project = self._project_mgr.current_project
        model_path = project.model_path if project else ""
        model_conf = project.model_confidence if project else 0.7

        dlg = ModelDialog(model_path, model_conf, self)
        if dlg.exec() != ModelDialog.DialogCode.Accepted:
            return

        # Save to project
        if project:
            project.model_path = dlg.model_path
            project.model_confidence = dlg.confidence
            self._project_mgr.save_user_state()

        self._model_path = dlg.model_path
        self._model_conf = dlg.confidence
        self._update_model_indicator()

    def _update_model_indicator(self) -> None:
        """Show a checkmark in Model menu when a model is configured."""
        if not hasattr(self, "_act_load_model"):
            return

        if self._model_path:
            model_name = Path(self._model_path).name
            self._act_load_model.setText("✓ &Load Model…")
            self._act_load_model.setToolTip(f"Loaded: {model_name}")
        else:
            self._act_load_model.setText("&Load Model…")
            self._act_load_model.setToolTip("No model loaded")

    def _run_on_current(self) -> None:
        inference_ok, inference_error = is_inference_available()
        if not inference_ok:
            self._show_inference_missing_message(inference_error)
            return
        if not self._model_path:
            QMessageBox.warning(self, "No model", "Load a model first via Model > Load Model…")
            return
        img = self._image_mgr.current_image
        if img is None:
            return

        self._lbl_hint.setText("Running model…")
        QApplication.processEvents()

        self._predictor.predict_async(
            model_path=self._model_path,
            image_path=img,
            conf=self._model_conf,
            on_done=self._on_inference_done,
            on_error=self._on_inference_error,
            use_obb=self._use_obb,
        )

    def _on_inference_done(self, labels) -> None:
        if not labels:
            self._lbl_hint.setText("Model added 0 label(s).")
            return

        for label in labels:
            self._label_mgr.add_label(label)
            self._canvas.add_label_item(label)

        cmd = AddLabelsCommand(
            labels=labels,
            canvas=self._canvas,
            label_mgr=self._label_mgr,
            action_label=f"Add {len(labels)} labels (model)",
        )
        self._undo_stack.push(cmd)

        self._auto_mark_current_image_in_progress()
        self._refresh_label_list()
        self._update_dirty_indicator()
        self._autosave_timer.start()
        self._lbl_hint.setText(f"Model added {len(labels)} label(s).")

    def _on_inference_error(self, msg: str) -> None:
        self._lbl_hint.setText("Model error.")
        QMessageBox.critical(self, "Inference error", msg)

    def _show_inference_missing_message(self, inference_error: str = "") -> None:
        details = f"\n\nDetail: {inference_error}" if inference_error else ""
        runtime = f"\n\nInterpreteur actuel:\n    {sys.executable}"
        diag_log = f"\n\nLog diagnostic:\n    {get_inference_diag_log_path()}"
        win1114_help = ""
        if "WinError 1114" in inference_error:
            win1114_help = (
                "\n\nCorrection WinError 1114 (DLL):\n"
                "1) Redemarrez VS Code puis relancez l'application avec l'interpreteur du projet (.venv).\n"
                "2) Si besoin, reinstallez torch CPU dans cet environnement:\n"
                "    python -m pip install --upgrade --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cpu\n"
                "3) Installez/reparez Microsoft Visual C++ Redistributable 2015-2022 (x64), puis redemarrez Windows."
            )
        QMessageBox.warning(
            self,
            "ultralytics indisponible",
            "Le module d'inference 'ultralytics' n'est pas disponible dans l'environnement Python actuel.\n\n"
            "Installez-le dans CE MEME environnement avec:\n"
            "    python -m pip install -r requirements-inference.txt"
            f"{runtime}{diag_log}{details}{win1114_help}",
        )

    def _run_on_all(self) -> None:
        inference_ok, inference_error = is_inference_available()
        if not inference_ok:
            self._show_inference_missing_message(inference_error)
            return
        if not self._model_path:
            QMessageBox.warning(self, "No model", "Load a model first via Model > Load Model…")
            return
        images = self._image_mgr.images
        if not images:
            return

        if not self._maybe_save_before_leaving():
            return

        progress = QProgressDialog("Running model on all images…", "Cancel", 0, len(images), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)

        try:
            yolo_class = get_yolo_class()
        except Exception as exc:
            self._show_inference_missing_message(str(exc))
            return

        from app.models.obb_label import OBBLabel as _OBBLabel, BBoxLabel as _BBoxLabel
        from app.models.label_manager import LabelManager as _LabelManager

        use_obb = self._use_obb

        for i, img_path in enumerate(images):
            if progress.wasCanceled():
                break
            progress.setValue(i)
            progress.setLabelText(f"Processing {img_path.name} ({i+1}/{len(images)})")
            QApplication.processEvents()

            try:
                model = yolo_class(self._model_path)
                results = model.predict(
                    source=str(img_path),
                    conf=self._model_conf,
                    save=False,
                    verbose=False,
                )
                labels = []

                if results:
                    result = results[0]

                    # Try OBB format first (for OBB models)
                    if hasattr(result, 'obb') and result.obb is not None and len(result.obb.cls) > 0:
                        obb = result.obb
                        coords = obb.xyxyxyxyn.cpu().numpy()
                        for j in range(len(obb.cls)):
                            pts = coords[j].reshape(8).tolist()
                            if use_obb:
                                labels.append(_OBBLabel(
                                    class_idx=int(obb.cls[j].item()),
                                    points=pts,
                                    conf=float(obb.conf[j].item()),
                                ))
                            else:
                                labels.append(_BBoxLabel.from_corners(
                                    class_idx=int(obb.cls[j].item()),
                                    corners=pts,
                                    conf=float(obb.conf[j].item()),
                                ))

                    # Try standard detection format (for regular detection models)
                    elif hasattr(result, 'boxes') and result.boxes is not None and len(result.boxes.cls) > 0:
                        boxes = result.boxes
                        coords = boxes.xywhn.cpu().numpy()
                        for j in range(len(boxes.cls)):
                            x_center, y_center, width, height = coords[j].tolist()
                            if use_obb:
                                half_w = width / 2
                                half_h = height / 2
                                pts = [
                                    x_center - half_w, y_center - half_h,
                                    x_center + half_w, y_center - half_h,
                                    x_center + half_w, y_center + half_h,
                                    x_center - half_w, y_center + half_h,
                                ]
                                labels.append(_OBBLabel(
                                    class_idx=int(boxes.cls[j].item()),
                                    points=pts,
                                    conf=float(boxes.conf[j].item()),
                                ))
                            else:
                                labels.append(_BBoxLabel(
                                    class_idx=int(boxes.cls[j].item()),
                                    x_center=x_center,
                                    y_center=y_center,
                                    width=width,
                                    height=height,
                                    conf=float(boxes.conf[j].item()),
                                ))

                lm = _LabelManager()
                lm.load_for_image(img_path)
                for lbl in labels:
                    lm.add_label(lbl)
                lm.save()
            except Exception as exc:
                QMessageBox.warning(self, "Error", f"Error on {img_path.name}:\n{exc}")

        progress.setValue(len(images))
        self._browser.set_images(images)  # refresh indicators
        self._lbl_hint.setText("Batch inference complete.")

    # ------------------------------------------------------------------
    # Copy / Paste
    # ------------------------------------------------------------------

    def _copy_selected(self) -> None:
        """Copy selected labels to the internal clipboard."""
        selected_indices = self._label_list.selected_indices()
        if selected_indices:
            selected = [
                self._label_mgr.labels[i]
                for i in selected_indices
                if 0 <= i < len(self._label_mgr.labels)
            ]
        else:
            selected = [
                item.label for item in self._canvas._label_items if item.isSelected()
            ]
        if not selected:
            self._lbl_hint.setText("Nothing selected to copy.")
            return
        # Deep-copy the labels
        self._clipboard = []
        for lbl in selected:
            if isinstance(lbl, OBBLabel):
                self._clipboard.append(OBBLabel(
                    class_idx=lbl.class_idx,
                    points=list(lbl.points),
                    conf=1.0,
                ))
            elif isinstance(lbl, BBoxLabel):
                self._clipboard.append(BBoxLabel(
                    class_idx=lbl.class_idx,
                    x_center=lbl.x_center,
                    y_center=lbl.y_center,
                    width=lbl.width,
                    height=lbl.height,
                    conf=1.0,
                ))
        self._lbl_hint.setText(f"{len(self._clipboard)} label(s) copied.")

    def _paste_labels(self) -> None:
        """Paste clipboard labels onto the current image with a small offset."""
        if not self._clipboard:
            self._lbl_hint.setText("Clipboard is empty.")
            return
        if self._image_mgr.current_image is None:
            return

        OFFSET = 0.02  # 2% offset to make pasted labels visible
        for src in self._clipboard:
            if isinstance(src, OBBLabel):
                new_points = []
                for v in src.points:
                    new_points.append(min(1.0, max(0.0, v + OFFSET)))
                label = OBBLabel(
                    class_idx=src.class_idx,
                    points=new_points,
                    conf=1.0,
                )
            elif isinstance(src, BBoxLabel):
                label = BBoxLabel(
                    class_idx=src.class_idx,
                    x_center=min(1.0, max(0.0, src.x_center + OFFSET)),
                    y_center=min(1.0, max(0.0, src.y_center + OFFSET)),
                    width=src.width,
                    height=src.height,
                    conf=1.0,
                )
            else:
                continue

            self._label_mgr.add_label(label)
            self._canvas.add_label_item(label)
            cmd = AddLabelCommand(label, self._canvas, self._label_mgr)
            self._undo_stack.push(cmd)

        self._refresh_label_list()
        self._canvas.labels_changed.emit()
        self._lbl_hint.setText(f"{len(self._clipboard)} label(s) pasted.")

    # ------------------------------------------------------------------
    # Team management
    # ------------------------------------------------------------------

    def _choose_active_member(self) -> None:
        """Quickly choose active team member used for filtering images."""
        project = self._project_mgr.current_project
        if not project:
            QMessageBox.information(
                self, "Pas de projet", "Creez ou ouvrez d'abord un projet."
            )
            return
        if not project.team_members:
            QMessageBox.information(
                self,
                "Pas de membres",
                "Ajoutez d'abord des membres via Equipe > Gerer les membres.",
            )
            return

        options = ["(Tous)", *project.team_members]
        current = project.active_team_member or "(Tous)"
        try:
            current_index = options.index(current)
        except ValueError:
            current_index = 0

        choice, ok = QInputDialog.getItem(
            self,
            "Membre actif",
            "Afficher les images de:",
            options,
            current_index,
            False,
        )
        if not ok:
            return

        project.active_team_member = "" if choice == "(Tous)" else choice
        self._project_mgr.save_user_state()
        self._apply_team_filter()

    def _reassign_selected_images(self) -> None:
        """Reassign selected images (or current image) to another team member."""
        project = self._project_mgr.current_project
        if not project:
            QMessageBox.information(
                self, "Pas de projet", "Creez ou ouvrez d'abord un projet."
            )
            return
        if not project.team_members:
            QMessageBox.information(
                self, "Pas de membres", "Ajoutez d'abord des membres via Equipe > Gerer les membres."
            )
            return

        selected_paths = self._browser.selected_images()
        if not selected_paths:
            current = self._image_mgr.current_image
            if current is not None:
                selected_paths = [current]

        if not selected_paths:
            QMessageBox.information(self, "Aucune image", "Selectionnez au moins une image.")
            return

        options = list(project.team_members)
        default_member = project.active_team_member if project.active_team_member in options else options[0]
        default_index = options.index(default_member)

        target_member, ok = QInputDialog.getItem(
            self,
            "Reassign Images",
            "Assigner a:",
            options,
            default_index,
            False,
        )
        if not ok:
            return

        selected_names = {p.name for p in selected_paths}
        moved_count = 0

        for member in project.team_members:
            current_assigned = list(project.team_assignments.get(member, []))
            filtered = [name for name in current_assigned if name not in selected_names]
            project.team_assignments[member] = filtered

        target_list = list(project.team_assignments.get(target_member, []))
        target_set = set(target_list)
        ordered = [img.name for img in self._all_images if img.name in selected_names]
        for name in ordered:
            if name not in target_set:
                target_list.append(name)
                target_set.add(name)
                moved_count += 1

        project.team_assignments[target_member] = target_list
        self._project_mgr.save_current()

        if project.active_team_member:
            self._apply_team_filter()
        else:
            self._browser.set_images(self._image_mgr.images)

        self._lbl_hint.setText(f"{moved_count} image(s) reassigned to {target_member}.")

    def _team_dialog(self) -> None:
        """Open the team members management dialog."""
        project = self._project_mgr.current_project
        if not project:
            QMessageBox.information(
                self, "Pas de projet", "Creez ou ouvrez d'abord un projet."
            )
            return

        dlg = TeamManagerDialog(project, self)
        if dlg.exec() == TeamManagerDialog.DialogCode.Accepted:
            self._apply_team_filter()
            self._project_mgr.save_current()

    def _distribute_images(self) -> None:
        """Distribute images among team members."""
        project = self._project_mgr.current_project
        if not project:
            QMessageBox.information(
                self, "Pas de projet", "Creez ou ouvrez d'abord un projet."
            )
            return
        if not project.team_members:
            QMessageBox.information(
                self, "Pas de membres", "Ajoutez d'abord des membres via Equipe > Gerer les membres."
            )
            return
        if not self._all_images:
            QMessageBox.information(
                self, "Pas d'images", "Ouvrez d'abord un dossier avec des images."
            )
            return

        # Check total percentage
        total_pct = sum(project.team_percentages.get(m, 0) for m in project.team_members)
        if total_pct <= 0:
            QMessageBox.warning(
                self,
                "Pourcentages non definis",
                "Definissez d'abord les pourcentages dans Equipe > Gerer les membres > Options avancees.\n\n"
                "Exemple: 35%, 35%, 15%, 15%"
            )
            return

        # Ask about redistribution mode
        redistribute_all = False
        if project.is_distributed():
            reply = QMessageBox.question(
                self,
                "Mode de distribution",
                "Des images sont deja distribuees.\n\n"
                "Voulez-vous redistribuer TOUTES les images selon les nouveaux pourcentages?\n\n"
                "Oui = Redistribuer tout\n"
                "Non = Distribuer uniquement les nouvelles images",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return
            redistribute_all = (reply == QMessageBox.StandardButton.Yes)

        project.distribute_images(self._all_images, redistribute_all=redistribute_all)
        self._project_mgr.save_current()

        self._lbl_hint.setText(f"Images distribuees entre {len(project.team_members)} membres.")

        if project.active_team_member:
            self._apply_team_filter()

    def _show_all_images(self) -> None:
        """Remove team filter, show all images."""
        project = self._project_mgr.current_project
        if project:
            project.active_team_member = ""
            self._project_mgr.save_user_state()
        self._apply_team_filter()

    def _toggle_class_names(self, checked: bool) -> None:
        """Show/hide class name badges on boxes."""
        self._show_class_names = bool(checked)
        self._canvas.set_show_class_names(self._show_class_names)
        self._settings.setValue("view/show_class_names", self._show_class_names)
        self._lbl_hint.setText(
            "Class names shown" if self._show_class_names else "Class names hidden"
        )

    def _toggle_accentuated_boxes(self, checked: bool) -> None:
        """Toggle stronger (but clean) box rendering for easier visual scanning."""
        self._accentuate_boxes = bool(checked)
        self._canvas.set_accentuate_boxes(self._accentuate_boxes)
        self._settings.setValue("view/accentuate_boxes", self._accentuate_boxes)
        self._lbl_hint.setText(
            "Accentuated boxes enabled" if self._accentuate_boxes else "Accentuated boxes disabled"
        )

    def _prompt_team_member_on_startup(self) -> None:
        """Ask the user to pick their team member when project opens at launch."""
        project = self._project_mgr.current_project
        if not project or not project.team_members:
            return

        options = list(project.team_members)
        current = project.active_team_member if project.active_team_member in options else ""
        current_index = options.index(current) if current else 0

        choice, ok = QInputDialog.getItem(
            self,
            "Qui etes-vous ?",
            "Selectionnez votre membre d'equipe:",
            options,
            current_index,
            False,
        )
        if not ok:
            if not current:
                project.active_team_member = options[0]
                self._project_mgr.save_user_state()
                self._apply_team_filter()
            return

        project.active_team_member = choice
        self._project_mgr.save_user_state()
        self._apply_team_filter()

    def _apply_team_filter(self) -> None:
        """Filter images shown in browser by active team member."""
        project = self._project_mgr.current_project
        if not project:
            return

        member = project.active_team_member
        if member and project.is_distributed():
            filtered = project.get_member_images(member, self._all_images)
            self._image_mgr.load_split(filtered, self._image_mgr.split)
            self._browser.set_images(filtered)
            self._lbl_team.setText(member)
            labeled, total = project.get_member_progress(
                member, self._all_images, self._label_mgr.has_labels_for
            )
            self._lbl_hint.setText(
                f"{member}: {labeled}/{total} images labellisees"
            )
        else:
            self._image_mgr.load_split(self._all_images, self._image_mgr.split)
            self._browser.set_images(self._all_images)
            self._lbl_team.setText("")
        self._update_window_title()
        self._load_current_image()

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def _on_mode_changed(self, mode: str) -> None:
        self._lbl_mode.setText(mode.upper())

    def _toggle_label_mode(self) -> None:
        """Toggle between OBB (oriented) and BBox (axis-aligned) mode.

        Existing labels are converted and the whole switch is undo/redo-able.
        """
        if not self._warn_once_before_mode_switch():
            return

        old_mode = self._use_obb
        new_mode = not old_mode
        current_image = self._image_mgr.current_image

        # Persist current mode before switching so users can go back to the
        # original annotations of each mode (OBB and BB stored separately).
        old_dirty = self._label_mgr.is_dirty
        if current_image is not None:
            if not self._label_mgr.save():
                QMessageBox.warning(
                    self,
                    "Sauvegarde impossible",
                    "Impossible de sauvegarder les labels du mode actuel avant le changement de mode.",
                )
                return
            old_dirty = False

        old_labels = self._clone_labels(self._label_mgr.labels)

        loaded_new_labels = self._load_saved_labels_for_mode(current_image, new_mode)
        if loaded_new_labels is not None:
            new_labels = loaded_new_labels
            new_dirty = False
        else:
            new_labels = self._convert_labels_for_mode(old_labels, new_mode)
            new_dirty = bool(new_labels)

        # Existing geometry commands target previous label instances; isolate
        # mode conversion as its own undo/redo unit to keep behavior coherent.
        self._undo_stack.clear()

        cmd = ToggleLabelModeCommand(
            old_use_obb=old_mode,
            new_use_obb=new_mode,
            old_labels=old_labels,
            new_labels=new_labels,
            old_dirty=old_dirty,
            new_dirty=new_dirty,
            apply_state=self._apply_label_mode_state,
        )
        self._undo_stack.push(cmd)

        mode_name = "OBB (Oriented Bounding Box)" if self._use_obb else "BBox (Axis-Aligned)"
        self._lbl_hint.setText(f"Mode: {mode_name}")

    def _convert_labels_to_mode(self, target_use_obb: bool) -> None:
        """Convert labels to target format and overwrite current in-memory state.

        Unlike mode toggle, this operation always converts the currently loaded
        labels and does not restore previously saved labels from the other mode.
        """
        source_name = "OBB" if self._use_obb else "BBox"
        target_name = "OBB" if target_use_obb else "BBox"

        if self._use_obb == target_use_obb:
            self._lbl_hint.setText(f"Already in {target_name} mode.")
            return

        reply = QMessageBox.question(
            self,
            f"Convert {source_name} -> {target_name}",
            f"Convert all current labels from {source_name} to {target_name}?\n\n"
            "This overwrites the current working labels in memory.\n"
            "You can undo with Ctrl+Z if needed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        old_mode = self._use_obb
        old_labels = self._clone_labels(self._label_mgr.labels)
        old_dirty = self._label_mgr.is_dirty

        new_labels = self._convert_labels_for_mode(old_labels, target_use_obb)
        new_dirty = True

        cmd = ToggleLabelModeCommand(
            old_use_obb=old_mode,
            new_use_obb=target_use_obb,
            old_labels=old_labels,
            new_labels=new_labels,
            old_dirty=old_dirty,
            new_dirty=new_dirty,
            apply_state=self._apply_label_mode_state,
            action_label=f"Convert {source_name} -> {target_name}",
        )
        self._undo_stack.push(cmd)

        self._lbl_hint.setText(f"Converted labels: {source_name} -> {target_name}")

    def _flip_selected_orientation(self) -> None:
        """Flip selected OBB labels by 180° (corner-order rotation).

        This keeps geometry identical on-screen while swapping orientation
        reference by cycling points [p1,p2,p3,p4] -> [p3,p4,p1,p2].
        """
        selected_items = [
            item for item in self._canvas._label_items
            if item.isSelected() and isinstance(item.label, OBBLabel)
        ]

        if not selected_items:
            self._lbl_hint.setText("No selected OBB labels to flip.")
            return

        self._undo_stack.beginMacro("Flip selected orientation 180°")
        try:
            for item in selected_items:
                label = item.label
                if not isinstance(label, OBBLabel):
                    continue

                old_points = list(label.points)
                if len(old_points) != 8:
                    continue

                new_points = old_points[4:] + old_points[:4]
                if new_points == old_points:
                    continue

                label.points = list(new_points)
                label.mark_manual()
                item.refresh_from_label()

                cmd = ModifyLabelCommand(label, old_points, new_points, self._canvas)
                self._undo_stack.push(cmd)
        finally:
            self._undo_stack.endMacro()

        self._canvas.labels_changed.emit()
        self._refresh_label_list()
        self._update_dirty_indicator()
        self._autosave_timer.start()
        self._lbl_hint.setText(f"Flipped orientation for {len(selected_items)} selected OBB label(s).")

    def _rotate_selected_labels(self, angle_deg: float) -> None:
        self._transform_selected_labels(scale_factor=None, rotate_degrees=angle_deg)

    def _scale_selected_labels(self, factor: float) -> None:
        self._transform_selected_labels(scale_factor=factor, rotate_degrees=None)

    def _transform_selected_labels(self, scale_factor: float | None, rotate_degrees: float | None) -> None:
        selected_items = [item for item in self._canvas._label_items if item.isSelected()]
        if not selected_items:
            self._lbl_hint.setText("No selected labels to transform.")
            return

        action_label = "Transform selected labels"
        self._undo_stack.beginMacro(action_label)
        changed_count = 0
        skipped_count = 0
        try:
            for item in selected_items:
                label = item.label

                if isinstance(label, OBBLabel):
                    old_points = list(label.points)
                    if len(old_points) != 8:
                        skipped_count += 1
                        continue

                    pts = [QPointF(old_points[i], old_points[i + 1]) for i in range(0, 8, 2)]
                    cx = sum(p.x() for p in pts) / 4.0
                    cy = sum(p.y() for p in pts) / 4.0
                    center = QPointF(cx, cy)

                    if rotate_degrees is not None:
                        angle = math.radians(rotate_degrees)
                        cos_a = math.cos(angle)
                        sin_a = math.sin(angle)
                        rotated: list[QPointF] = []
                        for p in pts:
                            dx = p.x() - center.x()
                            dy = p.y() - center.y()
                            rx = center.x() + dx * cos_a - dy * sin_a
                            ry = center.y() + dx * sin_a + dy * cos_a
                            rotated.append(QPointF(min(1.0, max(0.0, rx)), min(1.0, max(0.0, ry))))
                        pts = rotated

                    if scale_factor is not None:
                        scaled: list[QPointF] = []
                        for p in pts:
                            sx = center.x() + (p.x() - center.x()) * scale_factor
                            sy = center.y() + (p.y() - center.y()) * scale_factor
                            scaled.append(QPointF(min(1.0, max(0.0, sx)), min(1.0, max(0.0, sy))))
                        pts = scaled

                    new_points: list[float] = []
                    for p in pts:
                        new_points.extend([p.x(), p.y()])

                    if new_points == old_points:
                        continue

                    label.points = list(new_points)
                    label.mark_manual()
                    item.refresh_from_label()
                    self._undo_stack.push(ModifyLabelCommand(label, old_points, new_points, self._canvas))
                    changed_count += 1

                elif isinstance(label, BBoxLabel):
                    if rotate_degrees is not None:
                        skipped_count += 1
                        continue

                    old_points = label.to_corners()
                    if scale_factor is None:
                        skipped_count += 1
                        continue

                    label.width = max(1e-6, min(1.0, label.width * scale_factor))
                    label.height = max(1e-6, min(1.0, label.height * scale_factor))
                    new_points = label.to_corners()
                    if new_points == old_points:
                        continue

                    label.mark_manual()
                    item.refresh_from_label()
                    self._undo_stack.push(ModifyLabelCommand(label, old_points, new_points, self._canvas))
                    changed_count += 1
                else:
                    skipped_count += 1
        finally:
            self._undo_stack.endMacro()

        if changed_count > 0:
            self._canvas.labels_changed.emit()
            self._refresh_label_list()
            self._update_dirty_indicator()
            self._autosave_timer.start()
            if skipped_count > 0:
                self._lbl_hint.setText(f"Transformed {changed_count} labels ({skipped_count} skipped).")
            else:
                self._lbl_hint.setText(f"Transformed {changed_count} labels.")
        else:
            self._lbl_hint.setText("No labels transformed.")

    def _warn_once_before_mode_switch(self) -> bool:
        key = "warnings/mode_switch_seen"
        if self._settings.value(key, False, type=bool):
            return True

        reply = QMessageBox.warning(
            self,
            "Changement de mode OBB/BB",
            "Le mode OBB et le mode BB utilisent des fichiers de labels separes.\n\n"
            "Au changement de mode, l'application sauvegarde d'abord le mode courant,\n"
            "puis recharge les labels deja sauvegardes dans l'autre mode (ou convertit si absent).\n\n"
            "Continuer ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False

        self._settings.setValue(key, True)
        return True

    def _load_saved_labels_for_mode(self, image_path: Path | None, use_obb: bool) -> list[Label] | None:
        if image_path is None:
            return []

        obb_path, bb_path, legacy_path = LabelManager._derive_label_path_triplet(image_path)
        preferred = obb_path if use_obb else bb_path

        # For mode restoration, do not read the opposite mode file. We want
        # to restore what was explicitly saved for this mode.
        if not preferred.exists() and not legacy_path.exists():
            return None

        temp_mgr = LabelManager(use_obb=use_obb)
        temp_mgr.load_for_image(image_path)
        return self._clone_labels(temp_mgr.labels)

    def _clone_labels(self, labels: list[Label]) -> list[Label]:
        cloned: list[Label] = []
        for lbl in labels:
            if isinstance(lbl, OBBLabel):
                cloned.append(OBBLabel(class_idx=lbl.class_idx, points=list(lbl.points), conf=lbl.conf))
            else:
                cloned.append(
                    BBoxLabel(
                        class_idx=lbl.class_idx,
                        x_center=lbl.x_center,
                        y_center=lbl.y_center,
                        width=lbl.width,
                        height=lbl.height,
                        conf=lbl.conf,
                    )
                )
        return cloned

    def _convert_labels_for_mode(self, labels: list[Label], use_obb: bool) -> list[Label]:
        converted: list[Label] = []
        for lbl in labels:
            if use_obb:
                if isinstance(lbl, OBBLabel):
                    converted.append(OBBLabel(class_idx=lbl.class_idx, points=list(lbl.points), conf=lbl.conf))
                else:
                    converted.append(
                        OBBLabel(
                            class_idx=lbl.class_idx,
                            points=lbl.to_corners(),
                            conf=lbl.conf,
                        )
                    )
            else:
                if isinstance(lbl, BBoxLabel):
                    converted.append(
                        BBoxLabel(
                            class_idx=lbl.class_idx,
                            x_center=lbl.x_center,
                            y_center=lbl.y_center,
                            width=lbl.width,
                            height=lbl.height,
                            conf=lbl.conf,
                        )
                    )
                else:
                    converted.append(
                        BBoxLabel.from_corners(
                            class_idx=lbl.class_idx,
                            corners=list(lbl.points),
                            conf=lbl.conf,
                        )
                    )
        return converted

    def _apply_label_mode_state(self, use_obb: bool, labels: list[Label], mark_dirty: bool) -> None:
        self._use_obb = use_obb
        self._canvas.set_use_obb(use_obb)
        self._label_mgr.set_use_obb(use_obb)
        self._label_mgr.replace_labels(self._clone_labels(labels), mark_dirty=mark_dirty)
        self._canvas.load_labels(self._label_mgr.labels)
        self._refresh_label_list()
        self._update_dirty_indicator()
        if mark_dirty:
            self._autosave_timer.start()
        else:
            self._autosave_timer.stop()

        project = self._project_mgr.current_project
        if project:
            project.use_obb = use_obb
            self._schedule_project_autosave()

        self._update_format_indicator()

    def _update_dirty_indicator(self) -> None:
        self._lbl_dirty.setText("● unsaved" if self._label_mgr.is_dirty else "")

    def _update_index_label(self) -> None:
        total = self._image_mgr.total
        idx = self._image_mgr.current_index
        if total:
            self._lbl_index.setText(f"{idx + 1} / {total}")
        else:
            self._lbl_index.setText("No images")

    # ------------------------------------------------------------------
    # Help
    # ------------------------------------------------------------------

    def _show_shortcuts(self) -> None:
        shortcuts = """
<b>Keyboard Shortcuts</b><br><br>
<b>Mode</b><br>
W — Draw mode<br>
S — Select mode<br>
Ctrl+B — Toggle OBB/BBox mode<br>
<br>
<b>Navigation</b><br>
A / ← — Previous image<br>
D / → / Space — Next image<br>
0–9 — Select class<br>
<br>
<b>Drawing (Draw mode)</b><br>
<i>OBB mode:</i> 3-point drawing (edge + width)<br>
<i>BBox mode:</i> Click and drag corner to corner<br>
RMB — Cancel / Undo last point<br>
Esc — Cancel drawing<br>
<br>
<b>Editing (Select mode)</b><br>
Click — Select label<br>
Drag — Move label<br>
Drag corner handle — Resize label<br>
Alt/Ctrl + Drag corner/edge — Scale box up/down<br>
Shift + Drag box (OBB) — Rotate quickly<br>
Ctrl+Shift+L — Flip selected OBB orientation 180°<br>
Ctrl+Alt+Q / Ctrl+Alt+E — Rotate selected labels -15° / +15°<br>
Ctrl+Alt+S / Ctrl+Alt+W — Scale selected labels -10% / +10%<br>
Ctrl+Shift+H — Show/hide class names on boxes<br>
Ctrl+Shift+U — Toggle very accentuated boxes<br>
Del — Delete selected label<br>
Esc — Deselect<br>
<br>
<b>Undo / Redo</b><br>
Ctrl+Z — Undo last draw / delete<br>
Ctrl+Y — Redo<br>
<br>
<b>View</b><br>
Scroll wheel — Zoom<br>
Ctrl+= / Ctrl+- — Zoom in/out<br>
F / Ctrl+0 — Fit image to view<br>
<br>
<b>File</b><br>
Ctrl+C — Copy selected labels<br>
Ctrl+V — Paste labels<br>
Ctrl+S — Save labels<br>
Ctrl+O — Open folder<br>
Ctrl+Shift+O — Open dataset YAML<br>
Ctrl+Shift+E — Export project JSON<br>
Ctrl+Shift+I — Import project JSON<br>
Ctrl+R — Run model on current image<br>
Ctrl+Shift+R — Run model on all images<br>
<br>
<b>Team</b><br>
Ctrl+T — Team dialog (select member / progress)<br>
Ctrl+Shift+M — Reassign selected image(s) to a member<br>
<br>
<b>Status</b><br>
Ctrl+Shift+K — Set current image as completed<br>
Ctrl+Shift+J — Set current image as in progress<br>
        """.strip()
        QMessageBox.information(self, "Keyboard Shortcuts", shortcuts)

    def _show_status_store_health(self) -> None:
        health = self._project_mgr.get_image_status_store_health()
        if not bool(health.get("has_project")):
            QMessageBox.information(
                self,
                "Status Store Health",
                "No project is currently open.",
            )
            return

        status_dir = str(health.get("status_dir", ""))
        total_files = int(health.get("total_files", 0))
        valid_files = int(health.get("valid_files", 0))
        malformed_files = int(health.get("malformed_files", 0))
        tampered_files = int(health.get("tampered_files", 0))
        duplicate_images = int(health.get("duplicate_images", 0))

        message = (
            "Shared image-status store health\n\n"
            f"Folder: {status_dir}\n"
            f"Total files: {total_files}\n"
            f"Valid files: {valid_files}\n"
            f"Malformed files: {malformed_files}\n"
            f"Tampered files: {tampered_files}\n"
            f"Duplicate image entries: {duplicate_images}"
        )
        QMessageBox.information(self, "Status Store Health", message)

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._autosave_timer.stop()
        if self._label_mgr.is_dirty:
            reply = QMessageBox.question(
                self,
                "Unsaved changes",
                "Save labels before exiting?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if reply == QMessageBox.StandardButton.Yes:
                self._save_current()
        self._schedule_project_autosave()
        self._settings.setValue("window/geometry", self.saveGeometry())
        event.accept()
