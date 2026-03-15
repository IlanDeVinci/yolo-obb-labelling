from __future__ import annotations

from pathlib import Path
from typing import Any


def validate_inference_run(*, inference_ok: bool, inference_error: str, model_path: str, current_image: Path | None) -> tuple[bool, str]:
    if not inference_ok:
        return False, inference_error
    if not model_path:
        return False, "model-missing"
    if current_image is None:
        return False, "image-missing"
    return True, ""


def build_inference_request(*, model_path: str, image_path: Path, conf: float, class_filter: list[int] | None, use_obb: bool) -> dict[str, Any]:
    return {
        "model_path": model_path,
        "image_path": image_path,
        "conf": conf,
        "class_filter": (class_filter or None),
        "use_obb": use_obb,
    }


def labels_added_hint(count: int) -> str:
    return f"Model added {int(count)} label(s)."
