"""Cloud image manifest/cache provider for cloud-only mode."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}


@dataclass
class ImageManifestEntry:
    path: str
    size: int
    etag: str
    last_modified: int


class LocalFilesystemImageProvider:
    """Simple provider for local/hybrid images already available on disk."""

    def resolve_for_open(
        self,
        virtual_path: Path,
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> Path:
        if progress_callback:
            progress_callback(1, 1)
        return virtual_path

    def prefetch_after(self, current_virtual_path: Path | None, count: int) -> None:
        _ = (current_virtual_path, count)

    def clear_cache(self) -> None:
        return

    def cache_stats(self) -> dict[str, Any]:
        return {
            "cacheDir": "",
            "maxBytes": 0,
            "ttlSeconds": 0,
            "entries": 0,
            "usedBytes": 0,
        }

    def telemetry(self) -> dict[str, Any]:
        return {
            "downloads": 0,
            "cacheHits": 0,
            "cacheMisses": 0,
            "cacheHitRatio": 1.0,
            "failures": 0,
            "avgDownloadLatencyMs": 0,
        }


class CloudImageCache:
    def __init__(self, cache_dir: Path, *, max_bytes: int, ttl_seconds: int) -> None:
        self._cache_dir = cache_dir.resolve()
        self._max_bytes = max(128 * 1024 * 1024, int(max_bytes))
        self._ttl_seconds = max(60, int(ttl_seconds))
        self._index_path = self._cache_dir / "index.json"
        self._lock = threading.Lock()
        self._index: dict[str, dict[str, Any]] = {}
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    def get(self, key: str, *, etag: str, last_modified: int) -> Path | None:
        now = int(time.time())
        with self._lock:
            entry = self._index.get(key)
            if not entry:
                return None
            file_path = self._cache_dir / str(entry.get("file", ""))
            if not file_path.exists():
                self._index.pop(key, None)
                self._save_locked()
                return None

            entry_etag = str(entry.get("etag") or "")
            entry_last_modified = int(entry.get("lastModified") or 0)
            fetched_at = int(entry.get("fetchedAt") or 0)
            if etag and entry_etag and entry_etag != etag:
                return None
            if last_modified and entry_last_modified and entry_last_modified < last_modified:
                return None
            if fetched_at and now - fetched_at > self._ttl_seconds:
                return None

            entry["accessedAt"] = now
            self._save_locked()
            return file_path

    def put(self, key: str, *, payload: bytes, etag: str, last_modified: int, suffix: str) -> Path:
        safe_suffix = suffix if suffix in _IMAGE_SUFFIXES else ".img"
        stamp = str(int(time.time() * 1000))
        rel = Path("objects") / f"{stamp}-{abs(hash(key)) % 100000000}{safe_suffix}"
        target = self._cache_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

        now = int(time.time())
        with self._lock:
            old = self._index.get(key)
            if old:
                old_path = self._cache_dir / str(old.get("file", ""))
                try:
                    old_path.unlink(missing_ok=True)
                except OSError:
                    pass

            self._index[key] = {
                "file": rel.as_posix(),
                "etag": etag,
                "lastModified": int(last_modified or 0),
                "size": len(payload),
                "fetchedAt": now,
                "accessedAt": now,
            }
            self._evict_locked()
            self._save_locked()

        return target

    def clear(self) -> None:
        with self._lock:
            for entry in self._index.values():
                file_path = self._cache_dir / str(entry.get("file", ""))
                try:
                    file_path.unlink(missing_ok=True)
                except OSError:
                    pass
            self._index.clear()
            self._save_locked()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            used = 0
            count = 0
            for entry in self._index.values():
                file_path = self._cache_dir / str(entry.get("file", ""))
                if file_path.exists():
                    used += int(file_path.stat().st_size)
                    count += 1
            return {
                "cacheDir": str(self._cache_dir),
                "maxBytes": self._max_bytes,
                "ttlSeconds": self._ttl_seconds,
                "entries": count,
                "usedBytes": used,
            }

    def _evict_locked(self) -> None:
        candidates: list[tuple[str, dict[str, Any], Path]] = []
        total = 0
        now = int(time.time())

        for key, entry in list(self._index.items()):
            file_path = self._cache_dir / str(entry.get("file", ""))
            if not file_path.exists():
                self._index.pop(key, None)
                continue
            size = int(file_path.stat().st_size)
            total += size
            candidates.append((key, entry, file_path))

            fetched_at = int(entry.get("fetchedAt") or 0)
            if fetched_at and now - fetched_at > self._ttl_seconds:
                try:
                    file_path.unlink(missing_ok=True)
                except OSError:
                    pass
                self._index.pop(key, None)
                total -= size

        if total <= self._max_bytes:
            return

        candidates.sort(key=lambda item: int(item[1].get("accessedAt") or 0))
        for key, _, file_path in candidates:
            if total <= self._max_bytes:
                break
            if key not in self._index:
                continue
            try:
                size = int(file_path.stat().st_size)
            except OSError:
                size = int(self._index[key].get("size") or 0)
            try:
                file_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._index.pop(key, None)
            total -= size

    def _load(self) -> None:
        try:
            raw = json.loads(self._index_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._index = {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}
        except Exception:
            self._index = {}

    def _save_locked(self) -> None:
        try:
            self._index_path.parent.mkdir(parents=True, exist_ok=True)
            self._index_path.write_text(json.dumps(self._index, indent=2), encoding="utf-8")
        except OSError:
            pass


class CloudImageProvider:
    def __init__(
        self,
        *,
        sync_agent,
        project_root: Path,
        cache_dir: Path,
        cache_max_mb: int,
        cache_ttl_hours: int,
    ) -> None:
        self._sync_agent = sync_agent
        self._project_root = project_root.resolve()
        self._manifest: list[ImageManifestEntry] = []
        self._manifest_map: dict[str, ImageManifestEntry] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="cloud-image")
        self._cache = CloudImageCache(
            cache_dir,
            max_bytes=max(128, int(cache_max_mb)) * 1024 * 1024,
            ttl_seconds=max(1, int(cache_ttl_hours)) * 3600,
        )
        self._telemetry: dict[str, float] = {
            "downloads": 0,
            "cacheHits": 0,
            "cacheMisses": 0,
            "failures": 0,
            "downloadLatencyMs": 0,
        }

    def stop(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def refresh_manifest(self) -> list[ImageManifestEntry]:
        payload = self._sync_agent.get_image_manifest()
        items = payload.get("manifest") if isinstance(payload, dict) else []
        parsed: list[ImageManifestEntry] = []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                rel = str(item.get("path") or "").strip().replace("\\", "/")
                if not rel or Path(rel).suffix.lower() not in _IMAGE_SUFFIXES:
                    continue
                parsed.append(
                    ImageManifestEntry(
                        path=rel,
                        size=int(item.get("size") or 0),
                        etag=str(item.get("etag") or ""),
                        last_modified=int(item.get("lastModified") or 0),
                    )
                )

        with self._lock:
            self._manifest = parsed
            self._manifest_map = {entry.path: entry for entry in parsed}
        return parsed

    def manifest_virtual_paths(self) -> list[Path]:
        with self._lock:
            entries = list(self._manifest)
        return [self._project_root / entry.path for entry in entries]

    def resolve_for_open(
        self,
        virtual_path: Path,
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> Path:
        rel = self._to_relative_path(virtual_path)
        entry = self._manifest_map.get(rel)
        if entry is None:
            raise RuntimeError(f"Image not in cloud manifest: {rel}")

        cached = self._cache.get(rel, etag=entry.etag, last_modified=entry.last_modified)
        if cached is not None:
            self._telemetry["cacheHits"] += 1
            return cached

        self._telemetry["cacheMisses"] += 1
        signed = self._sync_agent.get_signed_image_read(rel)
        url = str(signed.get("url") or "")
        if not url:
            self._telemetry["failures"] += 1
            raise RuntimeError("Unable to request signed image URL")

        started = time.time()
        payload = self._download_with_retry(url, progress_callback=progress_callback)
        if payload is None:
            stale = self._cache.get(rel, etag="", last_modified=0)
            if stale is not None:
                return stale
            self._telemetry["failures"] += 1
            raise RuntimeError("Offline or network error, and image is not in cache")

        out = self._cache.put(
            rel,
            payload=payload,
            etag=entry.etag,
            last_modified=entry.last_modified,
            suffix=Path(rel).suffix.lower(),
        )
        elapsed_ms = int((time.time() - started) * 1000)
        self._telemetry["downloads"] += 1
        self._telemetry["downloadLatencyMs"] += elapsed_ms
        return out

    def prefetch_after(self, current_virtual_path: Path | None, count: int) -> None:
        current = None
        if current_virtual_path is not None:
            try:
                current = self._to_relative_path(current_virtual_path)
            except Exception:
                current = None

        requested = max(1, int(count))
        self._pool.submit(self._prefetch_worker, current, requested)

    def clear_cache(self) -> None:
        self._cache.clear()

    def cache_stats(self) -> dict[str, Any]:
        return self._cache.stats()

    def telemetry(self) -> dict[str, Any]:
        total_requests = int(self._telemetry["cacheHits"] + self._telemetry["cacheMisses"])
        ratio = (self._telemetry["cacheHits"] / total_requests) if total_requests else 0.0
        avg_latency = (self._telemetry["downloadLatencyMs"] / self._telemetry["downloads"]) if self._telemetry["downloads"] else 0.0
        return {
            "downloads": int(self._telemetry["downloads"]),
            "cacheHits": int(self._telemetry["cacheHits"]),
            "cacheMisses": int(self._telemetry["cacheMisses"]),
            "cacheHitRatio": round(ratio, 3),
            "failures": int(self._telemetry["failures"]),
            "avgDownloadLatencyMs": round(avg_latency, 2),
        }

    def _prefetch_worker(self, current: str | None, count: int) -> None:
        try:
            payload = self._sync_agent.request_image_prefetch(current, count)
            items = payload.get("items") if isinstance(payload, dict) else []
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                rel = str(item.get("path") or "").strip().replace("\\", "/")
                if not rel:
                    continue
                etag = str(item.get("etag") or "")
                last_modified = int(item.get("lastModified") or 0)
                cached = self._cache.get(rel, etag=etag, last_modified=last_modified)
                if cached is not None:
                    continue
                url = str(item.get("url") or "")
                if not url:
                    continue
                payload_bytes = self._download_with_retry(url, progress_callback=None)
                if payload_bytes is None:
                    self._telemetry["failures"] += 1
                    continue
                self._cache.put(
                    rel,
                    payload=payload_bytes,
                    etag=etag,
                    last_modified=last_modified,
                    suffix=Path(rel).suffix.lower(),
                )
        except Exception:
            self._telemetry["failures"] += 1

    def _download_with_retry(
        self,
        url: str,
        *,
        progress_callback: Callable[[int, int], None] | None,
    ) -> bytes | None:
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(request, timeout=20) as response:
                    total = int(response.headers.get("Content-Length", "0") or 0)
                    parts: list[bytes] = []
                    downloaded = 0
                    while True:
                        chunk = response.read(128 * 1024)
                        if not chunk:
                            break
                        parts.append(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total)
                    return b"".join(parts)
            except (urllib.error.URLError, urllib.error.HTTPError):
                if attempt >= 2:
                    return None
                time.sleep(0.35 * (attempt + 1))
        return None

    def _to_relative_path(self, virtual_path: Path) -> str:
        resolved = virtual_path.resolve()
        try:
            rel = resolved.relative_to(self._project_root)
        except Exception as exc:
            raise RuntimeError("Invalid image path outside project") from exc
        normalized = rel.as_posix().lstrip("/")
        if ".." in normalized or normalized.startswith("/"):
            raise RuntimeError("Invalid normalized image path")
        return normalized
