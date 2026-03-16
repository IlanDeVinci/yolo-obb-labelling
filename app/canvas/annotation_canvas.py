"""Main annotation canvas — QGraphicsView with zoom/pan, draw/select modes."""
from __future__ import annotations
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt, QPoint, QPointF, pyqtSignal
from PyQt6.QtGui import QColor, QPixmap, QWheelEvent, QKeyEvent, QMouseEvent, QPainter
from PyQt6.QtWidgets import (
    QGraphicsScene,
    QGraphicsView,
    QGraphicsPixmapItem,
    QGraphicsItem,
)

from app.models.obb_label import OBBLabel, BBoxLabel, Label
from app.canvas.obb_graphics_item import OBBGraphicsItem
from app.canvas.drawing_controller import DrawingController, DrawingState
from app.utils.image_io import load_qpixmap, decode_failure_hint

_ZOOM_FACTOR = 1.15
_MIN_ZOOM = 0.05
_MAX_ZOOM = 25.0


class AnnotationCanvas(QGraphicsView):
    """Central widget for image display and annotation.

    Modes:
      SELECT — scroll-hand drag, items are movable/selectable
      DRAW   — crosshair cursor, mouse events go to DrawingController
    """

    MODE_SELECT = "select"
    MODE_DRAW = "draw"

    # Signals emitted toward MainWindow
    label_added = pyqtSignal(object)              # Label (OBBLabel or BBoxLabel)
    label_deleted = pyqtSignal(object)            # Label (kept for compatibility)
    labels_delete_requested = pyqtSignal(list)    # list[Label] — undo-aware delete
    label_modified = pyqtSignal(object, list, list)  # label, old_points, new_points
    labels_changed = pyqtSignal()
    status_message = pyqtSignal(str)
    mode_changed = pyqtSignal(str)
    label_selection_changed = pyqtSignal(list)  # list[int]

    def __init__(self, parent=None, use_obb: bool = True) -> None:
        super().__init__(parent)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self._image_item: QGraphicsPixmapItem | None = None
        self._label_items: list[OBBGraphicsItem] = []
        self._mode: str = self.MODE_SELECT
        self._class_names: list[str] = []
        self._active_class: int = 0
        self._img_w: float = 1.0
        self._img_h: float = 1.0
        self._user_zoomed: bool = False   # True after any manual wheel/key zoom
        self._wheel_zoom_accum: float = 0.0
        self._use_obb: bool = use_obb     # True = OBB mode, False = BBox mode
        self._show_class_names: bool = True
        self._accentuate_boxes: bool = False
        self._rubberband_select: bool = False

        # Middle-mouse-button pan state
        self._panning_mid: bool = False
        self._pan_last_pos: QPoint = QPoint()

        # Drawing controller
        self._draw_ctrl = DrawingController(
            canvas=self,
            on_label_created=self._on_label_created,
            on_status_changed=lambda msg: self.status_message.emit(msg),
            use_obb=use_obb,
        )

        # View settings
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        # NoAnchor lets us manually translate to pin the cursor point after scale(),
        # which is more reliable than AnchorUnderMouse when scrollbars appear/disappear.
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setBackgroundBrush(QColor(40, 40, 40))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._set_select_mode()

    # ------------------------------------------------------------------
    # Image loading
    # ------------------------------------------------------------------

    def load_image(self, image_path: Path) -> None:
        self._draw_ctrl.cancel()  # clear any in-progress preview
        self.clear_labels()
        if self._image_item:
            self._scene.removeItem(self._image_item)
            self._image_item = None

        pixmap = load_qpixmap(image_path)
        if pixmap.isNull():
            self.status_message.emit(decode_failure_hint(image_path))
            return

        self._img_w = float(pixmap.width())
        self._img_h = float(pixmap.height())

        self._image_item = self._scene.addPixmap(pixmap)
        self._image_item.setZValue(-1)
        self._scene.setSceneRect(pixmap.rect().toRectF())
        self._user_zoomed = False   # reset so resizeEvent will fit on first paint
        self.fitInView(self._image_item, Qt.AspectRatioMode.KeepAspectRatio)

    def fit_in_view(self) -> None:
        if self._image_item:
            self._user_zoomed = False
            self._wheel_zoom_accum = 0.0
            self.fitInView(self._image_item, Qt.AspectRatioMode.KeepAspectRatio)

    def capture_view_state(self) -> dict[str, object]:
        """Capture viewport state so callers can reload content without jolting the view."""
        return {
            "has_image": self._image_item is not None,
            "user_zoomed": bool(self._user_zoomed),
            "transform": self.transform(),
            "h_value": int(self.horizontalScrollBar().value()),
            "v_value": int(self.verticalScrollBar().value()),
        }

    def restore_view_state(self, state: dict[str, object] | None) -> None:
        """Restore viewport transform/scroll values captured by capture_view_state()."""
        if not state or self._image_item is None:
            return
        if not bool(state.get("has_image", False)):
            return
        transform = state.get("transform")
        if transform is None:
            return
        self.setTransform(transform)
        self.horizontalScrollBar().setValue(int(state.get("h_value", 0) or 0))
        self.verticalScrollBar().setValue(int(state.get("v_value", 0) or 0))
        self._user_zoomed = bool(state.get("user_zoomed", True))

    def has_active_interaction(self) -> bool:
        """True while drawing, panning, or modifying labels/handles."""
        if self._panning_mid:
            return True
        if self._mode == self.MODE_DRAW and self._draw_ctrl.state != DrawingState.IDLE:
            return True
        for item in self._label_items:
            if item._pre_modify_points is not None:
                return True
        return False

    def zoom_in(self) -> None:
        if self._image_item is None:
            return
        self._user_zoomed = True
        center = self.viewport().rect().center()
        self._zoom_at_viewport_pos(center, _ZOOM_FACTOR)

    def zoom_out(self) -> None:
        if self._image_item is None:
            return
        self._user_zoomed = True
        center = self.viewport().rect().center()
        self._zoom_at_viewport_pos(center, 1.0 / _ZOOM_FACTOR)

    # ------------------------------------------------------------------
    # Label management
    # ------------------------------------------------------------------

    def load_labels(self, labels: list[OBBLabel]) -> None:
        self.clear_labels()
        for label in labels:
            self._add_item_for_label(label)

    def clear_labels(self) -> None:
        for item in self._label_items:
            item.hide_handles()
            self._scene.removeItem(item)
        self._label_items.clear()

    def add_label_item(self, label: Label) -> OBBGraphicsItem:
        item = self._add_item_for_label(label)
        return item

    def remove_label_item(self, label: Label) -> None:
        for item in list(self._label_items):
            if item.label is label:
                item.hide_handles()
                self._scene.removeItem(item)
                self._label_items.remove(item)
                break

    def _add_item_for_label(self, label: Label) -> OBBGraphicsItem:
        name = self._class_names[label.class_idx] if label.class_idx < len(self._class_names) else ""
        item = OBBGraphicsItem(
            label, self._img_w, self._img_h, name,
            on_modified=self._on_label_modified,
            use_obb=self._use_obb,
            show_class_name=self._show_class_names,
            accentuate_boxes=self._accentuate_boxes,
        )
        item.setZValue(1)
        self._scene.addItem(item)
        self._label_items.append(item)
        return item

    def _on_label_modified(self, label: OBBLabel, old_points: list, new_points: list) -> None:
        self.label_modified.emit(label, old_points, new_points)
        self.labels_changed.emit()

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        if mode == self.MODE_SELECT:
            self._set_select_mode()
        else:
            self._set_draw_mode()

    def _set_select_mode(self) -> None:
        self._draw_ctrl.cancel()  # clear any in-progress preview line/polygon
        self._mode = self.MODE_SELECT
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        for item in self._label_items:
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.status_message.emit("Select mode  —  Ctrl+drag = multi-select  |  Del = delete selected  |  W = draw mode")
        self.mode_changed.emit(self.MODE_SELECT)

    def _set_draw_mode(self) -> None:
        self._mode = self.MODE_DRAW
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor)
        # Deselect all + disable move
        self._scene.clearSelection()
        for item in self._label_items:
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            item.hide_handles()
        self._draw_ctrl.cancel()
        self.mode_changed.emit(self.MODE_DRAW)

    def toggle_mode(self) -> None:
        if self._mode == self.MODE_SELECT:
            self._set_draw_mode()
        else:
            self._set_select_mode()

    # ------------------------------------------------------------------
    # Active class
    # ------------------------------------------------------------------

    def set_active_class(self, class_idx: int) -> None:
        self._active_class = class_idx
        self._draw_ctrl.set_active_class(class_idx)

    def set_class_names(self, names: list[str]) -> None:
        self._class_names = names
        for item in self._label_items:
            class_name = names[item.label.class_idx] if item.label.class_idx < len(names) else ""
            item.update_class_name(class_name)

    def set_show_class_names(self, show: bool) -> None:
        self._show_class_names = show
        for item in self._label_items:
            item.set_show_class_name(show)

    def set_accentuate_boxes(self, accentuate: bool) -> None:
        self._accentuate_boxes = accentuate
        for item in self._label_items:
            item.set_accentuate_boxes(accentuate)

    def set_use_obb(self, use_obb: bool) -> None:
        """Switch between OBB and BBox mode."""
        self._use_obb = use_obb
        self._draw_ctrl.set_use_obb(use_obb)
        for item in self._label_items:
            item.set_use_obb(use_obb)
            names = self._class_names
            name = names[item.label.class_idx] if item.label.class_idx < len(names) else ""
            item.update_class_name(name)

    # ------------------------------------------------------------------
    # Callback from DrawingController
    # ------------------------------------------------------------------

    def _on_label_created(self, label: OBBLabel) -> None:
        self.add_label_item(label)
        self.label_added.emit(label)
        self.labels_changed.emit()

    def select_all_labels(self) -> None:
        """Select all labels in current image to support bulk actions."""
        if self._mode != self.MODE_SELECT:
            self._set_select_mode()
        for item in self._label_items:
            item.setSelected(True)
        self._sync_selected_handles()

    def select_label_index(self, index: int) -> None:
        """Select one label by index and focus handles accordingly."""
        if self._mode != self.MODE_SELECT:
            self._set_select_mode()
        self._scene.clearSelection()
        if 0 <= index < len(self._label_items):
            self._label_items[index].setSelected(True)
        self._sync_selected_handles()

    def select_label_indices(self, indices: list[int]) -> None:
        """Select multiple labels by indices."""
        if self._mode != self.MODE_SELECT:
            self._set_select_mode()
        wanted = {i for i in indices if 0 <= i < len(self._label_items)}
        self._scene.clearSelection()
        for idx, item in enumerate(self._label_items):
            if idx in wanted:
                item.setSelected(True)
        self._sync_selected_handles()

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def img_size(self) -> tuple[float, float]:
        return self._img_w, self._img_h

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        # Middle mouse button: start pan (works in both modes)
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning_mid = True
            self._pan_last_pos = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        scene_pos = self.mapToScene(event.pos())

        if self._mode == self.MODE_DRAW:
            if event.button() == Qt.MouseButton.LeftButton:
                self._draw_ctrl.handle_press(scene_pos)
            elif event.button() == Qt.MouseButton.RightButton:
                self._draw_ctrl.handle_right_click()
        else:
            # SELECT mode: let Qt handle item selection + pan
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        # Middle mouse pan (works in both modes)
        if self._panning_mid:
            delta = event.pos() - self._pan_last_pos
            self._pan_last_pos = event.pos()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
            event.accept()
            return

        scene_pos = self.mapToScene(event.pos())
        if self._mode == self.MODE_DRAW:
            self._draw_ctrl.handle_move(scene_pos)
        else:
            super().mouseMoveEvent(event)
            # Track selection changes (handle drag)
            self._sync_selected_handles()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        # Middle mouse button: stop pan and restore cursor
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning_mid = False
            if self._mode == self.MODE_DRAW:
                self.setCursor(Qt.CursorShape.CrossCursor)
            else:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return

        scene_pos = self.mapToScene(event.pos())
        if self._mode == self.MODE_DRAW:
            if event.button() == Qt.MouseButton.LeftButton:
                self._draw_ctrl.handle_release(scene_pos)
        else:
            super().mouseReleaseEvent(event)
            self._sync_selected_handles()
            self.labels_changed.emit()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        # Prevent accidental double-click zoom passthrough in draw mode
        if self._mode == self.MODE_DRAW:
            return
        super().mouseDoubleClickEvent(event)

    def _sync_selected_handles(self) -> None:
        """Show handles on selected items, hide on deselected."""
        selected_indices: list[int] = []
        for idx, item in enumerate(self._label_items):
            if item.isSelected():
                selected_indices.append(idx)

        for item in self._label_items:
            if item.isSelected():
                if not item._handles:
                    item.show_handles()
            else:
                # Don't hide handles during an active drag / rotation
                if item._handles and item._pre_modify_points is None:
                    item.hide_handles()
        self.label_selection_changed.emit(selected_indices)

    # ------------------------------------------------------------------
    # Keyboard events
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        mod = event.modifiers()

        if key == Qt.Key.Key_Escape:
            if self._mode == self.MODE_DRAW:
                self._draw_ctrl.cancel()
            else:
                self._scene.clearSelection()
                self._sync_selected_handles()

        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._delete_selected()

        elif key == Qt.Key.Key_Control and self._mode == self.MODE_SELECT:
            self._rubberband_select = True
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            self.setCursor(Qt.CursorShape.ArrowCursor)

        elif key == Qt.Key.Key_W and mod == Qt.KeyboardModifier.NoModifier:
            self._set_draw_mode()

        elif key == Qt.Key.Key_S and mod == Qt.KeyboardModifier.NoModifier:
            self._set_select_mode()

        elif key == Qt.Key.Key_F and mod == Qt.KeyboardModifier.NoModifier:
            self.fit_in_view()

        elif key in (Qt.Key.Key_Equal, Qt.Key.Key_Plus):
            self.zoom_in()

        elif key == Qt.Key.Key_Minus:
            self.zoom_out()

        elif key == Qt.Key.Key_0 and mod == Qt.KeyboardModifier.ControlModifier:
            self.resetTransform()
            self.fit_in_view()

        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Control and self._mode == self.MODE_SELECT and self._rubberband_select:
            self._rubberband_select = False
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self._sync_selected_handles()
            event.accept()
            return
        super().keyReleaseEvent(event)

    # ------------------------------------------------------------------
    # Zoom
    # ------------------------------------------------------------------

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._image_item is None:
            event.ignore()
            return

        angle_y = event.angleDelta().y()
        steps = 0

        if angle_y:
            steps = int(angle_y / 120)
            if steps == 0:
                steps = 1 if angle_y > 0 else -1
        else:
            pixel_y = event.pixelDelta().y()
            if not pixel_y:
                event.accept()
                return
            # Smooth high-resolution trackpad deltas into wheel-like zoom steps.
            self._wheel_zoom_accum += pixel_y / 40.0
            steps = int(self._wheel_zoom_accum)
            if steps == 0:
                event.accept()
                return
            self._wheel_zoom_accum -= steps

        factor = (_ZOOM_FACTOR ** steps) if steps > 0 else ((1.0 / _ZOOM_FACTOR) ** (-steps))
        self._user_zoomed = True
        self._zoom_at_viewport_pos(event.position().toPoint(), factor)
        event.accept()

    def _zoom_at_viewport_pos(self, cursor_vp: QPoint, factor: float) -> None:
        if self._image_item is None:
            return

        current_scale = self.transform().m11()
        if current_scale <= 0.0:
            return

        target_scale = current_scale * factor
        if target_scale < _MIN_ZOOM:
            factor = _MIN_ZOOM / current_scale
        elif target_scale > _MAX_ZOOM:
            factor = _MAX_ZOOM / current_scale

        if abs(factor - 1.0) < 1e-6:
            return

        # Keep the same scene point pinned under the cursor while scaling.
        old_scene_pos = self.mapToScene(cursor_vp)
        self.scale(factor, factor)
        new_scene_pos = self.mapToScene(cursor_vp)
        delta = new_scene_pos - old_scene_pos
        self.translate(delta.x(), delta.y())

    # ------------------------------------------------------------------
    # Delete selected
    # ------------------------------------------------------------------

    def _delete_selected(self) -> None:
        """Collect selected labels and emit labels_delete_requested.

        The actual removal is performed by DeleteLabelsCommand.redo() so that
        the operation is undo-able.  Nothing is removed from the scene here.
        """
        labels = [item.label for item in self._label_items if item.isSelected()]
        if labels:
            self.labels_delete_requested.emit(labels)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Only auto-fit if the user hasn't manually zoomed yet.
        # Without this guard, every zoom-in triggers a viewport resize
        # (scrollbars appear) which re-fires fitInView and resets the zoom.
        if self._image_item and not self._user_zoomed:
            self.fitInView(self._image_item, Qt.AspectRatioMode.KeepAspectRatio)
