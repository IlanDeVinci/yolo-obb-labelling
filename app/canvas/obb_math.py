"""Pure geometry helpers for OBB item transforms."""
from __future__ import annotations

import math
from typing import Iterable

from PyQt6.QtCore import QPointF


def centroid(points: Iterable[QPointF]) -> QPointF:
    pts = list(points)
    if not pts:
        return QPointF(0.0, 0.0)
    sx = sum(p.x() for p in pts)
    sy = sum(p.y() for p in pts)
    return QPointF(sx / len(pts), sy / len(pts))


def distance(a: QPointF, b: QPointF) -> float:
    return math.hypot(a.x() - b.x(), a.y() - b.y())


def clamp_scale(value: float, min_scale: float = 0.05, max_scale: float = 20.0) -> float:
    return max(min_scale, min(max_scale, value))


def scale_points_about(points: Iterable[QPointF], center: QPointF, scale: float) -> list[QPointF]:
    out: list[QPointF] = []
    for p in points:
        dx = p.x() - center.x()
        dy = p.y() - center.y()
        out.append(QPointF(center.x() + dx * scale, center.y() + dy * scale))
    return out


def rotate_points_about(points: Iterable[QPointF], center: QPointF, delta_radians: float) -> list[QPointF]:
    cos_a = math.cos(delta_radians)
    sin_a = math.sin(delta_radians)
    out: list[QPointF] = []
    for p in points:
        dx = p.x() - center.x()
        dy = p.y() - center.y()
        nx = center.x() + dx * cos_a - dy * sin_a
        ny = center.y() + dx * sin_a + dy * cos_a
        out.append(QPointF(nx, ny))
    return out


def orbit_radius(points: Iterable[QPointF], center: QPointF, padding: float = 20.0) -> float:
    max_dist = 0.0
    for p in points:
        d = distance(p, center)
        if d > max_dist:
            max_dist = d
    return max_dist + padding
