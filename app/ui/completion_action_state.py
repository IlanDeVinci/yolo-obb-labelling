from __future__ import annotations


def completion_actions_state(*, has_image: bool, current_status: str) -> dict[str, object]:
    if not has_image:
        return {
            "set_completed_enabled": False,
            "set_in_progress_enabled": False,
            "set_yolo_enabled": False,
            "toggle_enabled": False,
            "toggle_text": "Mark Current Image &Completed",
        }

    current = str(current_status or "")
    toggle_text = (
        "Mark Current Image &In Progress"
        if current == "completed"
        else "Mark Current Image &Completed"
    )
    return {
        "set_completed_enabled": current != "completed",
        "set_in_progress_enabled": current != "in_progress",
        "set_yolo_enabled": current != "yolo",
        "toggle_enabled": True,
        "toggle_text": toggle_text,
    }
