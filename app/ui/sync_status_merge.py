from __future__ import annotations

from typing import Any


def merge_sync_status_with_provider(status: dict[str, object], provider: Any | None) -> dict[str, object]:
    merged = dict(status)
    if provider is not None:
        merged["imageCache"] = provider.cache_stats()
        merged["imageTelemetry"] = provider.telemetry()
    return merged
