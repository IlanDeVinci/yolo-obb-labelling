from __future__ import annotations


def cloud_menu_status_text(*, connected: bool, enabled: bool, error: str) -> tuple[str, str]:
    if connected:
        return "apply", "Status: connected"
    if enabled and error:
        compact_error = error if len(error) <= 60 else (error[:57] + "...")
        return "warning", f"Status: error - open settings ({compact_error})"
    if enabled:
        return "reload", "Status: connecting"
    return "cancel", "Status: disabled (configure in Cloud Sync Settings)"
