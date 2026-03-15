from __future__ import annotations

from typing import Any, Callable


def apply_inference_labels(
    labels: list[Any],
    *,
    add_label_to_manager: Callable[[Any], None],
    add_label_to_canvas: Callable[[Any], None],
) -> int:
    for label in labels:
        add_label_to_manager(label)
        add_label_to_canvas(label)
    return len(labels)
