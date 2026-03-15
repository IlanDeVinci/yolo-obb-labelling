from __future__ import annotations

from pathlib import Path


def model_indicator_state(model_path: str, class_filter: list[int] | None) -> tuple[str, str]:
    if not model_path:
        return "&Load Model…", "No model loaded"

    model_name = Path(model_path).name
    text = "✓ &Load Model…"
    if class_filter:
        classes = ", ".join(str(v) for v in class_filter)
        tooltip = f"Loaded: {model_name} | Classes: {classes}"
    else:
        tooltip = f"Loaded: {model_name} | Classes: all"
    return text, tooltip
