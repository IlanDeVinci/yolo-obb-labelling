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
from app.canvas.drawing_controller import DrawingController

_ZOOM_FACTOR = 1.15


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
        self._use_obb: bool = use_obb     # True = OBB mode, False = BBox mode

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

        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
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
            self.fitInView(self._image_item, Qt.AspectRatioMode.KeepAspectRatio)

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
        self.status_message.emit("Select mode  —  W = draw mode  |  Del = delete selected  |  F = fit view")
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

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def img_size(self) -> tuple[float, float]:
        return self._img_w, self._img_h

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
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
        for item in self._label_items:
            if item.isSelected():
                if not item._handles:
                    item.show_handles()
            else:
                # Don't hide handles during an active drag / rotation
                if item._handles and item._pre_modify_points is None:
                    item.hide_handles()

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

        elif key == Qt.Key.Key_W and mod == Qt.KeyboardModifier.NoModifier:
            self._set_draw_mode()

        elif key == Qt.Key.Key_S and mod == Qt.KeyboardModifier.NoModifier:
            self._set_select_mode()

        elif key == Qt.Key.Key_F and mod == Qt.KeyboardModifier.NoModifier:
            self.fit_in_view()

        elif key in (Qt.Key.Key_Equal, Qt.Key.Key_Plus):
            self.scale(_ZOOM_FACTOR, _ZOOM_FACTOR)

        elif key == Qt.Key.Key_Minus:
            self.scale(1.0 / _ZOOM_FACTOR, 1.0 / _ZOOM_FACTOR)

        elif key == Qt.Key.Key_0 and mod == Qt.KeyboardModifier.ControlModifier:
            self.resetTransform()
            self.fit_in_view()

        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Zoom
    # ------------------------------------------------------------------

    def wheelEvent(self, event: QWheelEvent) -> None:
        self._user_zoomed = True
        factor = _ZOOM_FACTOR if event.angleDelta().y() > 0 else 1.0 / _ZOOM_FACTOR

        # Pin the scene point currently under the cursor.
        # Using NoAnchor + manual translate is more reliable than AnchorUnderMouse
        # because the latter fights with scrollbar geometry changes, causing jumps.
        cursor_vp = event.position().toPoint()
        old_scene_pos = self.mapToScene(cursor_vp)

        self.scale(factor, factor)  # NoAnchor: no automatic adjustment

        # Translate to keep the same scene point under the cursor
        new_scene_pos = self.mapToScene(cursor_vp)
        delta = new_scene_pos - old_scene_pos
        self.translate(delta.x(), delta.y())
        event.accept()

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
