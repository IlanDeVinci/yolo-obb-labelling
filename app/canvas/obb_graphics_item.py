"""QGraphicsPolygonItem representing one label (OBB or BBox) on the canvas."""
from __future__ import annotations
import math
from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QColor, QPen, QBrush, QFont, QPolygonF, QPainterPath
from PyQt6.QtWidgets import (
    QGraphicsPolygonItem,
    QGraphicsItem,
    QStyleOptionGraphicsItem,
    QWidget,
)

from app.models.obb_label import OBBLabel, BBoxLabel, Label
from app.utils.colors import get_color

if TYPE_CHECKING:
    from app.canvas.handle_item import HandleItem, RotationHandleItem, EdgeHandleItem


class OBBGraphicsItem(QGraphicsPolygonItem):
    """Visual representation of one label (OBB or BBox).

    Coordinate convention: setPos(0, 0) always, so scene coords == item coords.
    The polygon vertices are in image-pixel (scene) coordinates.
    """

    def __init__(
        self,
        label: Label,
        img_w: float,
        img_h: float,
        class_name: str = "",
        on_modified: Callable[[Label, list, list], None] | None = None,
        use_obb: bool = True,
    ) -> None:
        r, g, b = get_color(label.class_idx)
        self._color = QColor(r, g, b)
        self._img_w = img_w
        self._img_h = img_h
        self._class_name = class_name
        self._handles: list[HandleItem | RotationHandleItem | EdgeHandleItem] = []
        self._on_modified = on_modified
        self._pre_modify_points: list[float] | None = None
        self._rebaking = False
        self._use_obb = use_obb

        poly = self._label_to_polygon(label, img_w, img_h)
        super().__init__(poly)

        self.label = label
        self.setPos(0.0, 0.0)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paint(
        self,
        painter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        selected = self.isSelected()
        pen_width = 2.5 if selected else 1.5
        pen = QPen(self._color, pen_width)
        pen.setCosmetic(True)
        painter.setPen(pen)

        fill = QColor(self._color)
        fill.setAlpha(60 if selected else 30)
        painter.setBrush(QBrush(fill))
        painter.drawPolygon(self.polygon())

        poly = self.polygon()
        if poly.count() > 0:
            p1 = poly[0]
            self._paint_text_badge(painter, p1, selected)

    def _paint_text_badge(
        self, painter, origin: QPointF, selected: bool
    ) -> None:
        conf_str = ""
        if self.label.is_preannoted():
            conf_str = f" {self.label.conf:.0%}"

        text = (self._class_name or str(self.label.class_idx)) + conf_str
        font = QFont("Arial", 9)
        font.setBold(selected)
        painter.setFont(font)

        bg = QColor(self._color)
        bg.setAlpha(180)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(bg))
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(text) + 6
        th = fm.height() + 2
        painter.drawRect(QRectF(origin.x(), origin.y() - th, tw, th))

        painter.setPen(QPen(QColor(255, 255, 255), 1))
        painter.drawText(
            QRectF(origin.x() + 3, origin.y() - th, tw, th),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            text,
        )

    # ------------------------------------------------------------------
    # Geometry overrides — include text badge in repaint area
    # ------------------------------------------------------------------

    def boundingRect(self) -> QRectF:
        r = super().boundingRect()
        return r.adjusted(-40, -25, 40, 5)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addPolygon(self.polygon())
        path.closeSubpath()
        return path

    # ------------------------------------------------------------------
    # Item change — sync label when moved
    # ------------------------------------------------------------------

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged
            and not self._rebaking
        ):
            self._sync_label_from_polygon()
            self._update_handles()
        return super().itemChange(change, value)

    def mousePressEvent(self, event) -> None:
        self._begin_modify()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        # After drag, re-center at origin so item stays at pos=(0,0)
        if self.pos() != QPointF(0, 0):
            offset = self.pos()
            new_poly = QPolygonF([p + offset for p in self.polygon()])
            self._rebaking = True
            self.setPos(0.0, 0.0)
            self._rebaking = False
            self.setPolygon(new_poly)
            self._sync_label_from_polygon()
            self._update_handles()
        self._end_modify()

    # ------------------------------------------------------------------
    # Modify tracking (for undo)
    # ------------------------------------------------------------------

    def _get_label_points(self) -> list[float]:
        """Get points from label in a consistent format (8 floats for corners)."""
        if isinstance(self.label, OBBLabel):
            return list(self.label.points)
        else:
            return list(self.label.to_corners())

    def _begin_modify(self) -> None:
        """Snapshot current label points before a drag/rotation starts."""
        if self._pre_modify_points is None:
            self._pre_modify_points = self._get_label_points()

    def _end_modify(self) -> None:
        """If points changed, notify the canvas so an undo command is created."""
        if self._pre_modify_points is None:
            return
        old = self._pre_modify_points
        new = self._get_label_points()
        self._pre_modify_points = None
        if old != new:
            self.label.mark_manual()
            if self._on_modified:
                self._on_modified(self.label, old, new)

    # ------------------------------------------------------------------
    # Polygon ↔ label sync
    # ------------------------------------------------------------------

    def _sync_label_from_polygon(self) -> None:
        poly = self.polygon()
        offset = self.pos()
        pts: list[float] = []
        for i in range(min(4, poly.count())):
            pts.append((poly[i].x() + offset.x()) / self._img_w)
            pts.append((poly[i].y() + offset.y()) / self._img_h)

        if isinstance(self.label, OBBLabel):
            self.label.points = pts
        elif isinstance(self.label, BBoxLabel):
            # Convert 4 corners back to center-width-height format
            xs = [pts[i] for i in range(0, 8, 2)]
            ys = [pts[i] for i in range(1, 8, 2)]
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)
            self.label.x_center = (x_min + x_max) / 2
            self.label.y_center = (y_min + y_max) / 2
            self.label.width = x_max - x_min
            self.label.height = y_max - y_min

    def update_corner(self, corner_idx: int, scene_pos: QPointF) -> None:
        """Move a single corner of the polygon (called by HandleItem).

        Only the dragged corner moves; the other three stay fixed.
        """
        poly = QPolygonF(self.polygon())
        offset = self.pos()
        local_pos = QPointF(scene_pos.x() - offset.x(), scene_pos.y() - offset.y())
        poly[corner_idx] = local_pos
        self.setPolygon(poly)
        self._sync_label_from_polygon()
        self._update_handles(skip_corner=corner_idx)

    def update_edge(
        self,
        idx0: int,
        idx1: int,
        pos0: QPointF,
        pos1: QPointF,
        skip_edge: int = -1,
    ) -> None:
        """Move two corners of an edge (called by EdgeHandleItem).

        pos0, pos1 are in scene coordinates.
        """
        poly = QPolygonF(self.polygon())
        offset = self.pos()
        poly[idx0] = QPointF(pos0.x() - offset.x(), pos0.y() - offset.y())
        poly[idx1] = QPointF(pos1.x() - offset.x(), pos1.y() - offset.y())
        self.setPolygon(poly)
        self._sync_label_from_polygon()
        self._update_handles(skip_edge=skip_edge)

    # ------------------------------------------------------------------
    # Handle management
    # ------------------------------------------------------------------

    def show_handles(self) -> None:
        from app.canvas.handle_item import HandleItem, RotationHandleItem, EdgeHandleItem
        self.hide_handles()
        scene = self.scene()
        if not scene:
            return
        poly = self.polygon()
        offset = self.pos()

        # In regular BBox mode, keep interactions axis-aligned and simple:
        # move whole box + edge resize handles only (no free corner drag, no rotation).
        if self._use_obb:
            # Corner handles
            for i in range(poly.count()):
                pos = QPointF(poly[i].x() + offset.x(), poly[i].y() + offset.y())
                h = HandleItem(self, i, pos)
                scene.addItem(h)
                self._handles.append(h)

        # Edge midpoint handles (diamond-shaped)
        n = poly.count()
        for i in range(n):
            j = (i + 1) % n
            mid = QPointF(
                (poly[i].x() + poly[j].x()) / 2 + offset.x(),
                (poly[i].y() + poly[j].y()) / 2 + offset.y(),
            )
            eh = EdgeHandleItem(self, i, mid)
            scene.addItem(eh)
            self._handles.append(eh)

        if self._use_obb:
            # Rotation handle on the orbit around the centroid
            center = self.centroid_scene()
            R = self.rotation_orbit_radius()
            rot_pos = QPointF(center.x(), center.y() - R)
            rh = RotationHandleItem(self, center, rot_pos, R)
            scene.addItem(rh)
            self._handles.append(rh)

    def hide_handles(self) -> None:
        scene = self.scene()
        for h in self._handles:
            if scene:
                scene.removeItem(h)
        self._handles.clear()

    def _update_handles(self, skip_corner: int = -1, skip_edge: int = -1) -> None:
        from app.canvas.handle_item import RotationHandleItem, EdgeHandleItem
        poly = self.polygon()
        offset = self.pos()
        center = self.centroid_scene()
        R = self.rotation_orbit_radius()
        n = poly.count()
        for h in self._handles:
            if isinstance(h, RotationHandleItem):
                if not h._dragging:
                    h._center = QPointF(center)
                    h._orbit_radius = R
                    h.setPos(QPointF(center.x(), center.y() - R))
            elif isinstance(h, EdgeHandleItem):
                if h.edge_idx != skip_edge and not h._dragging:
                    i = h.edge_idx
                    j = (i + 1) % n
                    mid = QPointF(
                        (poly[i].x() + poly[j].x()) / 2 + offset.x(),
                        (poly[i].y() + poly[j].y()) / 2 + offset.y(),
                    )
                    h.setPos(mid)
            elif h.corner_idx != skip_corner and 0 <= h.corner_idx < n:
                if not h._dragging:
                    h.setPos(QPointF(
                        poly[h.corner_idx].x() + offset.x(),
                        poly[h.corner_idx].y() + offset.y(),
                    ))

    def on_selected(self, selected: bool) -> None:
        if selected:
            self.show_handles()
        else:
            self.hide_handles()

    # ------------------------------------------------------------------
    # Rotation
    # ------------------------------------------------------------------

    def centroid(self) -> QPointF:
        """Centroid in item-local coordinates."""
        poly = self.polygon()
        cx, cy = 0.0, 0.0
        n = poly.count()
        for i in range(n):
            cx += poly[i].x()
            cy += poly[i].y()
        return QPointF(cx / n, cy / n) if n else QPointF(0, 0)

    def centroid_scene(self) -> QPointF:
        """Centroid in scene coordinates (accounts for pos() during drag)."""
        c = self.centroid()
        o = self.pos()
        return QPointF(c.x() + o.x(), c.y() + o.y())

    def rotation_orbit_radius(self) -> float:
        """Radius of the orbit circle for the rotation handle."""
        poly = self.polygon()
        center = self.centroid()
        max_dist = 0.0
        for i in range(poly.count()):
            d = math.hypot(poly[i].x() - center.x(), poly[i].y() - center.y())
            max_dist = max(max_dist, d)
        return max_dist + 20

    def rotate_to_angle(self, angle: float, center: QPointF) -> None:
        """Rotate all corners so the rotation handle sits at *angle* from center."""
        from app.canvas.handle_item import RotationHandleItem
        rh = None
        for h in self._handles:
            if isinstance(h, RotationHandleItem):
                rh = h
                break
        if rh is None or not rh._start_corners:
            return

        delta = angle - rh._start_angle
        cos_a = math.cos(delta)
        sin_a = math.sin(delta)
        new_pts = []
        for pt in rh._start_corners:
            dx = pt.x() - center.x()
            dy = pt.y() - center.y()
            nx = center.x() + dx * cos_a - dy * sin_a
            ny = center.y() + dx * sin_a + dy * cos_a
            new_pts.append(QPointF(nx, ny))

        self.setPolygon(QPolygonF(new_pts))
        self._sync_label_from_polygon()
        # Update corner + edge handles (rotation handle skips itself when dragging)
        self._update_handles()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _label_to_polygon(label: Label, img_w: float, img_h: float) -> QPolygonF:
        if isinstance(label, OBBLabel):
            pts = label.points
        else:
            # BBoxLabel - convert to corners
            pts = label.to_corners()
        return QPolygonF([
            QPointF(pts[i] * img_w, pts[i + 1] * img_h)
            for i in range(0, 8, 2)
        ])

    def refresh_from_label(self) -> None:
        """Rebuild polygon + handles from the current label.points."""
        poly = self._label_to_polygon(self.label, self._img_w, self._img_h)
        self.setPolygon(poly)
        self._update_handles()

    def update_image_size(self, img_w: float, img_h: float) -> None:
        self._img_w = img_w
        self._img_h = img_h
        self.refresh_from_label()

    def update_class_name(self, name: str) -> None:
        self._class_name = name
        self.update()

    def set_use_obb(self, use_obb: bool) -> None:
        """Switch interaction mode between oriented and axis-aligned behavior."""
        if self._use_obb == use_obb:
            return
        self._use_obb = use_obb
        if self.isSelected():
            self.show_handles()
