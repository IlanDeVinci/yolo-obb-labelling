from __future__ import annotations

VALID_COMPLETION_STATUSES: frozenset[str] = frozenset({
    "in_progress",
    "completed",
    "yolo",
    "to_rotate",
})

CLOUD_IMAGE_ACCESS_MODES: frozenset[str] = frozenset({
    "local",
    "hybrid",
    "cloud_only",
})

REMOTE_IMAGE_ACCESS_MODES: frozenset[str] = frozenset({"hybrid", "cloud_only"})


def normalize_completion_status(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in VALID_COMPLETION_STATUSES:
        return normalized
    return ""


def normalize_image_access_mode(value: object) -> str:
    normalized = str(value or "local").strip().lower()
    if normalized in CLOUD_IMAGE_ACCESS_MODES:
        return normalized
    return "local"


def is_remote_image_mode(value: object) -> bool:
    return normalize_image_access_mode(value) in REMOTE_IMAGE_ACCESS_MODES


def is_cloud_only_mode(value: object) -> bool:
    return normalize_image_access_mode(value) == "cloud_only"


def is_hybrid_image_mode(value: object) -> bool:
    return normalize_image_access_mode(value) == "hybrid"
