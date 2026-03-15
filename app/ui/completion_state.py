from __future__ import annotations

from pathlib import Path

_STATUS_LABELS = {
    "in_progress": "In Progress",
    "completed": "Completed",
    "yolo": "YOLO",
    "to_rotate": "To Rotate",
}


def completion_status_label(status: str) -> str:
    return _STATUS_LABELS.get(str(status or ""), str(status or ""))


def toggle_completion_target(current: str) -> str:
    return "in_progress" if str(current or "") == "completed" else "completed"


def ensure_selected_images(selected: list[Path], current_image: Path | None) -> list[Path]:
    if selected:
        return list(selected)
    if current_image is None:
        return []
    return [current_image]


def selection_completion_hint(selected: list[Path], status: str) -> str:
    label = completion_status_label(status)
    n = len(selected)
    if n == 1:
        return f"{selected[0].name}: {label}"
    return f"{n} images set as {label}"
