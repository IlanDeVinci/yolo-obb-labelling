"""OBB geometry helpers — pure numpy, no Qt."""
from __future__ import annotations
import numpy as np


def compute_obb_corners(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> list[tuple[float, float]] | None:
    """Compute the four corners of an oriented rectangle.

    A and B define the base edge.
    C defines the perpendicular width (via projection onto the AB normal).

    Returns [P1, P2, P3, P4] in scene pixel coordinates, or None if the
    base edge is degenerate (< 1e-6 length).
    """
    A = np.array(a, dtype=float)
    B = np.array(b, dtype=float)
    C = np.array(c, dtype=float)

    AB = B - A
    length = float(np.linalg.norm(AB))
    if length < 1e-6:
        return None

    AB_hat = AB / length
    perp = np.array([-AB_hat[1], AB_hat[0]])   # 90° CCW rotation

    # signed width — negative C placement simply flips the rectangle
    width = float(np.dot(C - A, perp))

    P1 = A
    P2 = B
    P3 = B + width * perp
    P4 = A + width * perp

    return [tuple(P1), tuple(P2), tuple(P3), tuple(P4)]


def normalize_corners(
    corners: list[tuple[float, float]],
    img_w: float,
    img_h: float,
) -> list[float]:
    """Convert 4 scene-pixel corners to a flat YOLO-normalized list [x1,y1,...,x4,y4]."""
    result: list[float] = []
    for x, y in corners:
        result.append(x / img_w)
        result.append(y / img_h)
    return result


def denormalize_corners(
    points: list[float],
    img_w: float,
    img_h: float,
) -> list[tuple[float, float]]:
    """Convert a flat YOLO points list back to scene-pixel (x, y) tuples."""
    corners: list[tuple[float, float]] = []
    for i in range(0, 8, 2):
        corners.append((points[i] * img_w, points[i + 1] * img_h))
    return corners


def obb_area(corners: list[tuple[float, float]]) -> float:
    """Compute the area of a quadrilateral via the shoelace formula."""
    pts = np.array(corners, dtype=float)
    n = len(pts)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += pts[i, 0] * pts[j, 1]
        area -= pts[j, 0] * pts[i, 1]
    return abs(area) / 2.0


def is_valid_obb(
    corners: list[tuple[float, float]],
    min_area: float = 100.0,
) -> bool:
    """Return True if the OBB has at least min_area square pixels."""
    return obb_area(corners) >= min_area


def clamp_corners_to_image(
    corners: list[tuple[float, float]],
    img_w: float,
    img_h: float,
) -> list[tuple[float, float]]:
    """Clamp corner coordinates to image bounds."""
    return [
        (max(0.0, min(img_w, x)), max(0.0, min(img_h, y)))
        for x, y in corners
    ]
