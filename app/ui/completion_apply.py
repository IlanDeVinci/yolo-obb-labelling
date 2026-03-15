from __future__ import annotations

from pathlib import Path
from typing import Callable


def apply_completion_status_to_images(
    image_paths: list[Path],
    *,
    status: str,
    set_project_completion: Callable[[str, str], None],
    persist_local_completion: Callable[[Path, str], None] | None,
    push_cloud_completion: Callable[[str, str], None] | None,
) -> None:
    for image_path in image_paths:
        image_name = str(image_path.name)
        set_project_completion(image_name, status)
        if persist_local_completion is not None:
            persist_local_completion(image_path, status)
        if push_cloud_completion is not None:
            try:
                push_cloud_completion(image_name, status)
            except Exception:
                # Keep local workflow moving even if cloud push fails.
                pass
