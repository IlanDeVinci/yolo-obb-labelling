"""Class color palette — 10 visually distinct colors cycling for up to 80 classes."""
from __future__ import annotations

# BGR-friendly palette (also works well on light/dark backgrounds)
_PALETTE: list[tuple[int, int, int]] = [
    (255,  80,  80),   # 0  red
    ( 80, 180, 255),   # 1  sky blue
    ( 80, 255, 100),   # 2  green
    (255, 200,  50),   # 3  yellow
    (200,  80, 255),   # 4  purple
    ( 50, 220, 220),   # 5  cyan
    (255, 140,  50),   # 6  orange
    (255, 100, 200),   # 7  pink
    (180, 255,  80),   # 8  lime
    (120, 120, 255),   # 9  violet
]


def get_color(class_idx: int) -> tuple[int, int, int]:
    """Return an (R, G, B) tuple for the given class index."""
    return _PALETTE[class_idx % len(_PALETTE)]
