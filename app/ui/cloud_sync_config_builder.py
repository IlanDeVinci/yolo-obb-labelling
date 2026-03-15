from __future__ import annotations

from app.sync.realtime_sync import CloudSyncConfig


def build_cloud_sync_config(settings: dict[str, object]) -> CloudSyncConfig:
    return CloudSyncConfig(
        enabled=bool(settings.get("enabled", False)),
        server_url=str(settings.get("server_url", "")),
        project_id=str(settings.get("project_id", "")),
        project_password=str(settings.get("project_password", "")),
        username=str(settings.get("username", "")),
        user_password=str(settings.get("user_password", "")),
        poll_seconds=float(settings.get("poll_seconds", 1.2) or 1.2),
        image_cache_dir=str(settings.get("image_cache_dir", "")),
        image_cache_max_mb=int(settings.get("image_cache_max_mb", 2048) or 2048),
        image_cache_ttl_hours=int(settings.get("image_cache_ttl_hours", 24) or 24),
        image_prefetch_count=int(settings.get("image_prefetch_count", 8) or 8),
    )
