"""Draggable corner and rotation handles for OBBGraphicsItem."""
from __future__ import annotations
import math
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import QColor, QPen, QBrush, QPolygonF
from PyQt6.QtWidgets import QGraphicsEllipseItem, QGraphicsPolygonItem, QGraphicsItem

if TYPE_CHECKING:
    from app.canvas.obb_graphics_item import OBBGraphicsItem

_HANDLE_RADIUS = 5.0
_ROTATION_HANDLE_RADIUS = 5.0


class HandleItem(QGraphicsEllipseItem):
    """A small draggable circle at a polygon corner.

    Added directly to the scene (NOT a child of OBBGraphicsItem).
    NOT ItemIsMovable — mouse tracking is done manually to avoid
    conflicts with the view's ScrollHandDrag mode.
    """

    def __init__(
        self,
        parent_obb: "OBBGraphicsItem",
        corner_idx: int,
        scene_pos: QPointF,
    ) -> None:
        r = _HANDLE_RADIUS
        super().__init__(QRectF(-r, -r, r * 2, r * 2))
        self._parent_obb = parent_obb
        self.corner_idx = corner_idx
        self._dragging = False
        self._start_scene_points: list[QPointF] = []

        self.setPos(scene_pos)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
        )
        self.setAcceptHoverEvents(True)

        color = parent_obb._color
        lighter = QColor(color)
        lighter.setAlpha(220)
        self.setPen(QPen(QColor(255, 255, 255), 1.5))
        self.setBrush(QBrush(lighter))
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setZValue(10)

    # -- manual drag (not ItemIsMovable) --------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._parent_obb._begin_modify()
        self._dragging = True
        self._start_scene_points = self._parent_obb._scene_points()
        self._parent_obb.setSelected(True)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if not self._dragging:
            event.ignore()
            return
        scene_pos = event.scenePos()
        mods = event.modifiers()
        if mods & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ControlModifier):
            self._parent_obb.scale_uniform_from_corner(self._start_scene_points, self.corner_idx, scene_pos)
            pts = self._parent_obb._scene_points()
            if 0 <= self.corner_idx < len(pts):
                self.setPos(pts[self.corner_idx])
        else:
            self.setPos(scene_pos)
            self._parent_obb.update_corner(self.corner_idx, scene_pos)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._dragging = False
        self._start_scene_points = []
        self._parent_obb._end_modify()
        event.accept()

    # -- hover feedback ---------------------------------------------------

    def hoverEnterEvent(self, event) -> None:
        self.setPen(QPen(QColor(255, 255, 0), 2.0))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self.setPen(QPen(QColor(255, 255, 255), 1.5))
        super().hoverLeaveEvent(event)


_EDGE_HANDLE_RADIUS = 5.0


class EdgeHandleItem(QGraphicsPolygonItem):
    """Diamond-shaped handle at the midpoint of a polygon edge.

    Dragging is constrained perpendicular to the edge, preserving
    the rectangle shape.
    NOT ItemIsMovable — mouse tracking is done manually.
    """

    def __init__(
        self,
        parent_obb: "OBBGraphicsItem",
        edge_idx: int,
        scene_pos: QPointF,
    ) -> None:
        r = _EDGE_HANDLE_RADIUS
        diamond = QPolygonF([
            QPointF(0, -r), QPointF(r, 0),
            QPointF(0, r), QPointF(-r, 0),
        ])
        super().__init__(diamond)
        self._parent_obb = parent_obb
        self.edge_idx = edge_idx
        self.corner_idx = -2  # sentinel — not a corner handle
        self._dragging = False
        self._drag_origin: QPointF | None = None
        self._start_c0: QPointF | None = None
        self._start_c1: QPointF | None = None
        self._start_scene_points: list[QPointF] = []

        self.setPos(scene_pos)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
        )
        self.setAcceptHoverEvents(True)

        color = parent_obb._color
        fill = QColor(color)
        fill.setAlpha(180)
        self.setPen(QPen(QColor(255, 255, 255), 1.5))
        self.setBrush(QBrush(fill))
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setZValue(10)

    # -- manual drag (not ItemIsMovable) --------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._parent_obb._begin_modify()
        poly = self._parent_obb.polygon()
        offset = self._parent_obb.pos()
        i0 = self.edge_idx
        i1 = (self.edge_idx + 1) % 4
        self._start_c0 = QPointF(
            poly[i0].x() + offset.x(), poly[i0].y() + offset.y()
        )
        self._start_c1 = QPointF(
            poly[i1].x() + offset.x(), poly[i1].y() + offset.y()
        )
        self._drag_origin = QPointF(self.pos())
        self._start_scene_points = self._parent_obb._scene_points()
        self._dragging = True
        self._parent_obb.setSelected(True)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if not self._dragging:
            event.ignore()
            return

        scene_pos = event.scenePos()
        mods = event.modifiers()

        if mods & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ControlModifier):
            self._parent_obb.scale_uniform_from_edge(self._start_scene_points, self.edge_idx, scene_pos)
            pts = self._parent_obb._scene_points()
            if len(pts) >= 4:
                i0 = self.edge_idx % 4
                i1 = (i0 + 1) % 4
                mid = QPointF(
                    (pts[i0].x() + pts[i1].x()) / 2,
                    (pts[i0].y() + pts[i1].y()) / 2,
                )
                self.setPos(mid)
            event.accept()
            return

        delta_x = scene_pos.x() - self._drag_origin.x()
        delta_y = scene_pos.y() - self._drag_origin.y()

        # Edge direction
        ex = self._start_c1.x() - self._start_c0.x()
        ey = self._start_c1.y() - self._start_c0.y()
        e_len = math.hypot(ex, ey)
        if e_len < 1e-9:
            return

        # Perpendicular unit vector
        px, py = -ey / e_len, ex / e_len

        # Project delta onto perpendicular direction only
        proj = delta_x * px + delta_y * py
        cdx, cdy = proj * px, proj * py

        # Move handle to constrained position
        self.setPos(QPointF(
            self._drag_origin.x() + cdx,
            self._drag_origin.y() + cdy,
        ))

        # Update polygon corners
        new_c0 = QPointF(self._start_c0.x() + cdx, self._start_c0.y() + cdy)
        new_c1 = QPointF(self._start_c1.x() + cdx, self._start_c1.y() + cdy)
        i0 = self.edge_idx
        i1 = (self.edge_idx + 1) % 4
        self._parent_obb.update_edge(i0, i1, new_c0, new_c1, self.edge_idx)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._dragging = False
        self._drag_origin = None
        self._start_c0 = None
        self._start_c1 = None
        self._start_scene_points = []
        self._parent_obb._end_modify()
        event.accept()

    # -- hover feedback ---------------------------------------------------

    def hoverEnterEvent(self, event) -> None:
        self.setPen(QPen(QColor(255, 255, 0), 2.0))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self.setPen(QPen(QColor(255, 255, 255), 1.5))
        super().hoverLeaveEvent(event)


class RotationHandleItem(QGraphicsEllipseItem):
    """A handle that orbits the parent OBB to rotate it around its centroid.

    NOT ItemIsMovable — mouse tracking is done manually so the handle stays
    on a circular orbit around the polygon centroid.
    """

    def __init__(
        self,
        parent_obb: "OBBGraphicsItem",
        center: QPointF,
        pos: QPointF,
        orbit_radius: float,
        anchor_corner_idx: int,
    ) -> None:
        r = _ROTATION_HANDLE_RADIUS
        super().__init__(QRectF(-r, -r, r * 2, r * 2))
        self._parent_obb = parent_obb
        self._center = QPointF(center)
        self._orbit_radius = orbit_radius
        self.anchor_corner_idx = anchor_corner_idx
        self._dragging = False
        self._start_angle: float = 0.0
        self._start_corners: list[QPointF] = []
        self.corner_idx = -1  # sentinel — not a corner handle

        self.setPos(pos)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
        )
        self.setAcceptHoverEvents(True)

        self.setPen(QPen(QColor(255, 235, 170), 1.2))
        self.setBrush(QBrush(QColor(245, 185, 40, 175)))
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setZValue(11)

    # -- manual drag (not ItemIsMovable) --------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._center = self._parent_obb.centroid_scene()
        self._orbit_radius = self._parent_obb.rotation_orbit_radius()
        self._start_angle = math.atan2(
            self.pos().y() - self._center.y(),
            self.pos().x() - self._center.x(),
        )
        self._start_corners = [
            QPointF(pt.x(), pt.y()) for pt in self._parent_obb.polygon()
        ]
        self._parent_obb._begin_modify()
        self._dragging = True
        # Re-select the parent (scene clears selection on non-selectable press)
        self._parent_obb.setSelected(True)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if not self._dragging:
            event.ignore()
            return
        sp = event.scenePos()
        angle = math.atan2(
            sp.y() - self._center.y(), sp.x() - self._center.x()
        )
        self._parent_obb.rotate_to_angle(angle, self._center)
        # Orbit the handle around the centroid
        R = self._orbit_radius
        self.setPos(
            self._center.x() + R * math.cos(angle),
            self._center.y() + R * math.sin(angle),
        )
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._dragging = False
        self._parent_obb._update_handles()
        self._parent_obb._end_modify()
        event.accept()

    # -- hover feedback ---------------------------------------------------

    def hoverEnterEvent(self, event) -> None:
        self.setPen(QPen(QColor(255, 248, 200), 1.8))
        self.setBrush(QBrush(QColor(250, 195, 60, 210)))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self.setPen(QPen(QColor(255, 235, 170), 1.2))
        self.setBrush(QBrush(QColor(245, 185, 40, 175)))
        super().hoverLeaveEvent(event)
