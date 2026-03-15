from __future__ import annotations

from app.ui.status_values import is_cloud_only_mode


def should_persist_completion_locally(cloud_image_access_mode: str) -> bool:
    return not is_cloud_only_mode(cloud_image_access_mode)


def should_auto_mark_in_progress(*, has_labels: bool, current_status: str) -> bool:
    if not has_labels:
        return False
    return not bool(str(current_status or "").strip())
