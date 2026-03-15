from __future__ import annotations

from pathlib import Path


def disconnected_sync_state(*, error: str, cloud_workflow_enabled: bool) -> dict[str, str]:
    has_error = bool(str(error).strip())
    return {
        "sync_text": "SYNC: setup required" if not has_error else "SYNC: error",
        "sync_style": "padding: 0 8px; color: #de7f7f;",
        "cloud_mode_text": "IMAGES: local",
        "cloud_mode_style": "padding: 0 8px; color: #7b9db8;",
        "login_text": (
            ("LOGIN: required" if not has_error else "LOGIN: failed")
            if cloud_workflow_enabled
            else "LOGIN: local"
        ),
        "login_style": (
            "padding: 0 8px; color: #de7f7f; font-weight: bold;"
            if cloud_workflow_enabled
            else "padding: 0 8px; color: #8fa6b8;"
        ),
    }


def connected_sync_primary_text(*, users: int, active_file: str, pending_status_sync: int, status_syncing: bool) -> tuple[str, str]:
    active = str(active_file or "").strip()
    suffix = f" [{Path(active).name}]" if active else ""
    if bool(status_syncing) or int(pending_status_sync) > 0:
        return (
            f"SYNC: syncing statuses ({int(pending_status_sync)} pending){suffix}",
            "padding: 0 8px; color: #74a2d4;",
        )
    return (f"SYNC: live ({int(users)} online){suffix}", "padding: 0 8px; color: #86cc9f;")


def connected_cloud_mode_state(*, cloud_only: bool, hybrid: bool) -> tuple[str, str]:
    if cloud_only:
        return ("IMAGES: Cloud-Only", "padding: 0 8px; color: #63b38f;")
    if hybrid:
        return ("IMAGES: Hybrid", "padding: 0 8px; color: #74a2d4;")
    return ("IMAGES: local", "padding: 0 8px; color: #7b9db8;")
