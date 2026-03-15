from __future__ import annotations

import math
import unittest

from PyQt6.QtCore import QPointF

from app.canvas.obb_math import (
    centroid,
    clamp_scale,
    orbit_radius,
    rotate_points_about,
    scale_points_about,
)


class TestObbMath(unittest.TestCase):
    def test_centroid_square(self) -> None:
        pts = [QPointF(0, 0), QPointF(2, 0), QPointF(2, 2), QPointF(0, 2)]
        c = centroid(pts)
        self.assertAlmostEqual(c.x(), 1.0)
        self.assertAlmostEqual(c.y(), 1.0)

    def test_scale_points_about_center(self) -> None:
        pts = [QPointF(1, 1), QPointF(3, 1), QPointF(3, 3), QPointF(1, 3)]
        c = QPointF(2, 2)
        out = scale_points_about(pts, c, 0.5)
        expected = [QPointF(1.5, 1.5), QPointF(2.5, 1.5), QPointF(2.5, 2.5), QPointF(1.5, 2.5)]
        for got, exp in zip(out, expected):
            self.assertAlmostEqual(got.x(), exp.x(), places=6)
            self.assertAlmostEqual(got.y(), exp.y(), places=6)

    def test_rotate_points_about_ninety_deg(self) -> None:
        pts = [QPointF(1, 0)]
        c = QPointF(0, 0)
        out = rotate_points_about(pts, c, math.pi / 2)
        self.assertAlmostEqual(out[0].x(), 0.0, places=6)
        self.assertAlmostEqual(out[0].y(), 1.0, places=6)

    def test_clamp_scale(self) -> None:
        self.assertEqual(clamp_scale(0.01), 0.05)
        self.assertEqual(clamp_scale(100.0), 20.0)
        self.assertEqual(clamp_scale(2.5), 2.5)

    def test_orbit_radius(self) -> None:
        pts = [QPointF(1, 0), QPointF(-1, 0), QPointF(0, 1), QPointF(0, -1)]
        r = orbit_radius(pts, QPointF(0, 0), padding=5.0)
        self.assertAlmostEqual(r, 6.0)


if __name__ == "__main__":
    unittest.main()
