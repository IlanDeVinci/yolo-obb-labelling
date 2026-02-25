"""Drawing state machine with rubber-band preview.

Supports two modes:
- OBB mode: 3-point drawing (base edge + width)
- BBox mode: 2-point click-drag (top-left to bottom-right)
"""
from __future__ import annotations
from enum import Enum, auto
from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QColor, QPen, QPolygonF, QBrush
from PyQt6.QtWidgets import QGraphicsLineItem, QGraphicsPolygonItem, QGraphicsRectItem

from app.models.obb_label import OBBLabel, BBoxLabel, Label
from app.utils.geometry import compute_obb_corners, normalize_corners, is_valid_obb
from app.utils.colors import get_color

if TYPE_CHECKING:
    from app.canvas.annotation_canvas import AnnotationCanvas


class DrawingState(Enum):
    IDLE = auto()
    DRAWING_EDGE = auto()    # OBB: A placed; waiting for B
    DRAWING_WIDTH = auto()   # OBB: A and B placed; waiting for C (width)
    DRAWING_RECT = auto()    # BBox: dragging from corner to corner


_MIN_DRAG_PX = 5.0   # pixels moved before drag-mode auto-sets B


class DrawingController:
    """Handles mouse events when the canvas is in DRAW mode.

    Usage:
        controller.handle_press(scene_pos, active_class)
        controller.handle_move(scene_pos)
        controller.handle_release(scene_pos)
        controller.handle_right_click()
        controller.cancel()

    Calls on_label_created(Label) when a box is successfully finalized.
    Calls on_status_changed(str) with a hint for the status bar.
    """

    def __init__(
        self,
        canvas: "AnnotationCanvas",
        on_label_created: Callable[[Label], None],
        on_status_changed: Callable[[str], None],
        use_obb: bool = True,
    ) -> None:
        self._canvas = canvas
        self._on_label_created = on_label_created
        self._on_status_changed = on_status_changed
        self._use_obb = use_obb

        self._state = DrawingState.IDLE
        self._active_class: int = 0
        self._point_a: QPointF | None = None
        self._point_b: QPointF | None = None
        self._press_pos: QPointF | None = None  # to detect drag vs click

        # Preview scene items
        self._preview_line: QGraphicsLineItem | None = None
        self._preview_poly: QGraphicsPolygonItem | None = None
        self._preview_rect: QGraphicsRectItem | None = None

        self._emit_status()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def state(self) -> DrawingState:
        return self._state

    @property
    def use_obb(self) -> bool:
        return self._use_obb

    def set_use_obb(self, use_obb: bool) -> None:
        """Switch between OBB (3-point) and BBox (2-point) drawing mode."""
        if self._use_obb != use_obb:
            self.cancel()  # Cancel any in-progress drawing
            self._use_obb = use_obb
            self._emit_status()

    def set_active_class(self, class_idx: int) -> None:
        self._active_class = class_idx

    # ------------------------------------------------------------------
    # Mouse event handlers (called by AnnotationCanvas)
    # ------------------------------------------------------------------

    def handle_press(self, scene_pos: QPointF) -> None:
        if self._use_obb:
            self._handle_press_obb(scene_pos)
        else:
            self._handle_press_bbox(scene_pos)

    def handle_move(self, scene_pos: QPointF) -> None:
        if self._use_obb:
            self._handle_move_obb(scene_pos)
        else:
            self._handle_move_bbox(scene_pos)

    def handle_release(self, scene_pos: QPointF) -> None:
        if self._use_obb:
            self._handle_release_obb(scene_pos)
        else:
            self._handle_release_bbox(scene_pos)

    def handle_right_click(self) -> None:
        if self._use_obb:
            if self._state == DrawingState.DRAWING_WIDTH:
                # Step back: keep A, clear B
                self._point_b = None
                self._clear_preview()
                self._state = DrawingState.DRAWING_EDGE
                self._emit_status()
            elif self._state == DrawingState.DRAWING_EDGE:
                self.cancel()
        else:
            self.cancel()

    def cancel(self) -> None:
        self._point_a = None
        self._point_b = None
        self._press_pos = None
        self._clear_preview()
        self._state = DrawingState.IDLE
        self._emit_status()

    # ------------------------------------------------------------------
    # OBB mode handlers (3-point drawing)
    # ------------------------------------------------------------------

    def _handle_press_obb(self, scene_pos: QPointF) -> None:
        if self._state == DrawingState.IDLE:
            self._point_a = scene_pos
            self._press_pos = scene_pos
            self._state = DrawingState.DRAWING_EDGE
            self._emit_status()

        elif self._state == DrawingState.DRAWING_EDGE:
            # Second explicit click → set B
            self._set_b(scene_pos)

        elif self._state == DrawingState.DRAWING_WIDTH:
            # Third click → finalize
            self._finalize_obb(scene_pos)

    def _handle_move_obb(self, scene_pos: QPointF) -> None:
        if self._state == DrawingState.DRAWING_EDGE:
            # Check if the user is dragging (auto-set B on release)
            self._update_preview_line(self._point_a, scene_pos)

        elif self._state == DrawingState.DRAWING_WIDTH:
            corners = compute_obb_corners(
                (self._point_a.x(), self._point_a.y()),
                (self._point_b.x(), self._point_b.y()),
                (scene_pos.x(), scene_pos.y()),
            )
            if corners:
                self._update_preview_polygon(corners)

    def _handle_release_obb(self, scene_pos: QPointF) -> None:
        if self._state == DrawingState.DRAWING_EDGE and self._press_pos is not None:
            # If dragged far enough, auto-set B at release point
            drag_dist = (scene_pos - self._press_pos).manhattanLength()
            if drag_dist >= _MIN_DRAG_PX:
                self._set_b(scene_pos)

    # ------------------------------------------------------------------
    # BBox mode handlers (2-point click-drag)
    # ------------------------------------------------------------------

    def _handle_press_bbox(self, scene_pos: QPointF) -> None:
        if self._state == DrawingState.IDLE:
            self._point_a = scene_pos
            self._press_pos = scene_pos
            self._state = DrawingState.DRAWING_RECT
            self._emit_status()

    def _handle_move_bbox(self, scene_pos: QPointF) -> None:
        if self._state == DrawingState.DRAWING_RECT and self._point_a is not None:
            self._update_preview_rect(self._point_a, scene_pos)

    def _handle_release_bbox(self, scene_pos: QPointF) -> None:
        if self._state == DrawingState.DRAWING_RECT and self._point_a is not None:
            drag_dist = (scene_pos - self._point_a).manhattanLength()
            if drag_dist >= _MIN_DRAG_PX:
                self._finalize_bbox(scene_pos)
            self.cancel()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _set_b(self, scene_pos: QPointF) -> None:
        a = self._point_a
        # Reject degenerate edge
        if (scene_pos - a).manhattanLength() < 1.0:
            return
        self._point_b = scene_pos
        self._clear_preview()
        self._state = DrawingState.DRAWING_WIDTH
        # Show initial preview with zero width
        self._update_preview_polygon(
            compute_obb_corners(
                (a.x(), a.y()),
                (scene_pos.x(), scene_pos.y()),
                (scene_pos.x(), scene_pos.y()),
            ) or []
        )
        self._emit_status()

    def _finalize_obb(self, scene_pos: QPointF) -> None:
        corners = compute_obb_corners(
            (self._point_a.x(), self._point_a.y()),
            (self._point_b.x(), self._point_b.y()),
            (scene_pos.x(), scene_pos.y()),
        )
        self._clear_preview()

        if corners and is_valid_obb(corners, min_area=25.0):
            img_w, img_h = self._canvas.img_size()
            pts = normalize_corners(corners, img_w, img_h)
            label = OBBLabel(class_idx=self._active_class, points=pts, conf=1.0)
            self._on_label_created(label)

        # Reset regardless of success
        self._point_a = None
        self._point_b = None
        self._press_pos = None
        self._state = DrawingState.IDLE
        self._emit_status()

    def _finalize_bbox(self, scene_pos: QPointF) -> None:
        """Finalize a 2-point bounding box."""
        a = self._point_a
        b = scene_pos

        # Calculate normalized coordinates
        img_w, img_h = self._canvas.img_size()
        x1 = min(a.x(), b.x()) / img_w
        y1 = min(a.y(), b.y()) / img_h
        x2 = max(a.x(), b.x()) / img_w
        y2 = max(a.y(), b.y()) / img_h

        width = x2 - x1
        height = y2 - y1

        # Check minimum area (normalized)
        if width * img_w * height * img_h < 25.0:
            return

        x_center = (x1 + x2) / 2
        y_center = (y1 + y2) / 2

        label = BBoxLabel(
            class_idx=self._active_class,
            x_center=x_center,
            y_center=y_center,
            width=width,
            height=height,
            conf=1.0,
        )
        self._on_label_created(label)

    # ------------------------------------------------------------------
    # Preview rendering
    # ------------------------------------------------------------------

    def _update_preview_line(self, a: QPointF, b: QPointF) -> None:
        scene = self._canvas.scene()
        if self._preview_poly:
            scene.removeItem(self._preview_poly)
            self._preview_poly = None

        if self._preview_line is None:
            self._preview_line = QGraphicsLineItem()
            pen = QPen(QColor(255, 255, 255, 200), 1.5, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            self._preview_line.setPen(pen)
            self._preview_line.setZValue(100)
            self._preview_line.setEnabled(False)   # no mouse events on preview
            scene.addItem(self._preview_line)

        self._preview_line.setLine(a.x(), a.y(), b.x(), b.y())

    def _update_preview_polygon(self, corners: list) -> None:
        scene = self._canvas.scene()
        if self._preview_line:
            scene.removeItem(self._preview_line)
            self._preview_line = None

        if not corners:
            return

        r, g, b = get_color(self._active_class)
        color = QColor(r, g, b)

        poly = QPolygonF([QPointF(x, y) for x, y in corners])

        if self._preview_poly is None:
            self._preview_poly = QGraphicsPolygonItem()
            pen = QPen(color, 1.5, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            self._preview_poly.setPen(pen)
            fill = QColor(r, g, b, 60)
            self._preview_poly.setBrush(QBrush(fill))
            self._preview_poly.setZValue(100)
            self._preview_poly.setEnabled(False)   # no mouse events on preview
            scene.addItem(self._preview_poly)

        self._preview_poly.setPolygon(poly)
        # Update color in case active class changed
        pen = self._preview_poly.pen()
        pen.setColor(color)
        self._preview_poly.setPen(pen)
        self._preview_poly.setBrush(QBrush(QColor(r, g, b, 60)))

    def _update_preview_rect(self, a: QPointF, b: QPointF) -> None:
        """Update preview rectangle for bbox mode."""
        from PyQt6.QtCore import QRectF

        scene = self._canvas.scene()
        r, g, bc = get_color(self._active_class)
        color = QColor(r, g, bc)

        x1 = min(a.x(), b.x())
        y1 = min(a.y(), b.y())
        w = abs(b.x() - a.x())
        h = abs(b.y() - a.y())
        rect = QRectF(x1, y1, w, h)

        if self._preview_rect is None:
            self._preview_rect = QGraphicsRectItem()
            pen = QPen(color, 1.5, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            self._preview_rect.setPen(pen)
            fill = QColor(r, g, bc, 60)
            self._preview_rect.setBrush(QBrush(fill))
            self._preview_rect.setZValue(100)
            self._preview_rect.setEnabled(False)
            scene.addItem(self._preview_rect)

        self._preview_rect.setRect(rect)
        # Update color in case active class changed
        pen = self._preview_rect.pen()
        pen.setColor(color)
        self._preview_rect.setPen(pen)
        self._preview_rect.setBrush(QBrush(QColor(r, g, bc, 60)))

    def _clear_preview(self) -> None:
        scene = self._canvas.scene()
        if scene is None:
            return
        if self._preview_line:
            scene.removeItem(self._preview_line)
            self._preview_line = None
        if self._preview_poly:
            scene.removeItem(self._preview_poly)
            self._preview_poly = None
        if self._preview_rect:
            scene.removeItem(self._preview_rect)
            self._preview_rect = None

    # ------------------------------------------------------------------
    # Status messages
    # ------------------------------------------------------------------

    _OBB_MESSAGES = {
        DrawingState.IDLE:          "OBB Draw  —  click to place first point  |  S = select mode",
        DrawingState.DRAWING_EDGE:  "Click or drag to set base edge  |  RMB = cancel  |  Esc = cancel",
        DrawingState.DRAWING_WIDTH: "Click to set width  |  RMB = undo edge  |  Esc = cancel all",
        DrawingState.DRAWING_RECT:  "",  # Not used in OBB mode
    }

    _BBOX_MESSAGES = {
        DrawingState.IDLE:          "BBox Draw  —  click and drag to draw box  |  S = select mode",
        DrawingState.DRAWING_RECT:  "Drag to set box size  |  RMB = cancel  |  Esc = cancel",
        DrawingState.DRAWING_EDGE:  "",  # Not used in BBox mode
        DrawingState.DRAWING_WIDTH: "",  # Not used in BBox mode
    }

    def _emit_status(self) -> None:
        messages = self._OBB_MESSAGES if self._use_obb else self._BBOX_MESSAGES
        self._on_status_changed(messages.get(self._state, ""))
