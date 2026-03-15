from __future__ import annotations

from pathlib import Path
from typing import Callable


def filter_images_for_run_all(images: list[Path], get_status: Callable[[Path], str]) -> tuple[list[Path], int, int]:
    filtered: list[Path] = []
    skipped_completed = 0
    skipped_yolo = 0
    for img in images:
        status = str(get_status(img) or "").strip().lower()
        if status == "completed":
            skipped_completed += 1
            continue
        if status == "yolo":
            skipped_yolo += 1
            continue
        filtered.append(img)
    return filtered, skipped_completed, skipped_yolo
