"""Polling sync client for YOLO cloud backend with auth + file locking."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


_DEFAULT_INCLUDE_EXTENSIONS = {
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".tiff",
    ".tif",
    ".webp",
}

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}


@dataclass
class CloudSyncConfig:
    enabled: bool = False
    server_url: str = ""
    project_id: str = ""
    project_password: str = ""
    username: str = ""
    user_password: str = ""
    poll_seconds: float = 1.2
    image_cache_dir: str = ""
    image_cache_max_mb: int = 2048
    image_cache_ttl_hours: int = 24
    image_prefetch_count: int = 8

    def is_valid(self) -> bool:
        if not self.enabled:
            return False
        fields = [
            self.server_url.strip(),
            self.project_id.strip(),
            self.project_password.strip(),
            self.username.strip(),
            self.user_password.strip(),
        ]
        return all(fields)


class RealtimeSyncAgent:
    """Two-way filesystem sync for one project folder."""

    def __init__(
        self,
        *,
        project_root: Path,
        config: CloudSyncConfig,
        status_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._project_root = project_root.resolve()
        self._config = config
        self._server_url = self._normalize_server_url(config.server_url)
        self._poll_interval_s = max(0.5, float(config.poll_seconds or 1.2))
        self._status_callback = status_callback

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        self._state_dir = self._project_root / ".sync"
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._cursor_file = self._state_dir / "cursor.json"

        self._snapshot: dict[str, dict[str, Any]] = {}
        self._cursor = self._load_cursor()
        self._token = ""
        self._last_heartbeat_at = 0.0
        self._active_file: str | None = None
        self._pending_active_file: str | None = None
        self._inbound_apply_until: dict[Path, float] = {}
        self._initial_remote_refresh_done = False
        self._image_access_mode = "local"

        self._status: dict[str, Any] = {
            "connected": False,
            "projectId": config.project_id,
            "username": config.username,
            "role": "",
            "isAdmin": None,
            "activeFile": None,
            "cursor": self._cursor,
            "appliedLocal": 0,
            "appliedRemote": 0,
            "rejected": 0,
            "lastError": "",
            "lastSyncAt": 0,
            "onlineUsers": 0,
            "locks": [],
            "recentBackups": [],
            "imageAccessMode": "local",
        }

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._snapshot = self._build_snapshot()
        self._thread = threading.Thread(target=self._run_loop, name="yolo-sync", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 2.5) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout_s)
        self._thread = None

        if self._token:
            try:
                self._request_json("/api/auth/logout", body={})
            except Exception:
                pass
        self._token = ""
        self._initial_remote_refresh_done = False
        self._set_status(connected=False, activeFile=None)

    def set_active_file(self, relative_path: str | None) -> None:
        normalized = None
        if relative_path:
            normalized = self._normalize_rel(relative_path)
        with self._lock:
            self._pending_active_file = normalized

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._sync_once()
            except Exception as exc:  # noqa: BLE001
                self._set_status(connected=False, lastError=str(exc))
            self._stop_event.wait(self._poll_interval_s)

    def _sync_once(self) -> None:
        if not self._token:
            self._login()

        if self._image_access_mode == "local":
            try:
                summary = self._request_json("/api/project/summary")
                self._image_access_mode = str(summary.get("imageAccessMode") or "local")
                self._set_status(imageAccessMode=self._image_access_mode)
            except Exception:
                pass

        if not self._initial_remote_refresh_done:
            incoming = self._fetch_remote_changes()
            changes = incoming.get("changes") if isinstance(incoming, dict) else []
            if isinstance(changes, list) and changes:
                applied_remote = self._apply_remote_changes(changes)
                self._set_status(appliedRemote=self._status.get("appliedRemote", 0) + applied_remote)

            self._cursor = max(self._cursor, int(incoming.get("latestSeq", self._cursor)))
            self._save_cursor(self._cursor)
            self._snapshot = self._build_snapshot()
            self._initial_remote_refresh_done = True

        pending_active = None
        with self._lock:
            if self._pending_active_file != self._active_file:
                pending_active = self._pending_active_file

        if pending_active != self._active_file:
            self._activate_lock(pending_active)

        now = time.time()
        if now - self._last_heartbeat_at >= 10:
            hb = self._request_json("/api/auth/heartbeat", body={})
            self._last_heartbeat_at = now
            self._set_status(connected=True, activeFile=hb.get("activeFile"))

        updates = self._collect_local_changes()
        if updates:
            result = self._request_json("/api/sync/upsert", body={"updates": updates})
            self._cursor = max(self._cursor, int(result.get("latestSeq", self._cursor)))
            self._save_cursor(self._cursor)
            self._set_status(
                cursor=self._cursor,
                appliedLocal=self._status.get("appliedLocal", 0) + int(result.get("applied", 0)),
                rejected=self._status.get("rejected", 0) + len(result.get("rejected", [])),
            )

        incoming = self._fetch_remote_changes()
        changes = incoming.get("changes") if isinstance(incoming, dict) else []
        if isinstance(changes, list) and changes:
            applied_remote = self._apply_remote_changes(changes)
            self._set_status(appliedRemote=self._status.get("appliedRemote", 0) + applied_remote)

        self._cursor = max(self._cursor, int(incoming.get("latestSeq", self._cursor)))
        self._save_cursor(self._cursor)

        status = self._request_json("/api/sync/status")
        self._image_access_mode = str(status.get("imageAccessMode") or self._image_access_mode or "local")
        self._set_status(
            connected=True,
            activeFile=status.get("activeFile"),
            cursor=self._cursor,
            onlineUsers=int(status.get("onlineUsers", 0)),
            locks=list(status.get("locks", [])),
            recentBackups=list(status.get("recentBackups", [])),
            role=str(status.get("role", "") or ""),
            isAdmin=bool(status.get("isAdmin")) if status.get("isAdmin") is not None else None,
            imageAccessMode=self._image_access_mode,
            lastError="",
            lastSyncAt=int(time.time()),
        )

    def _login(self) -> None:
        payload = {
            "projectId": self._config.project_id,
            "projectPassword": self._config.project_password,
            "username": self._config.username,
            "password": self._config.user_password,
        }
        response = self._request_json("/api/auth/login", body=payload, include_auth=False)
        token = str(response.get("token", "")).strip()
        if not token:
            raise RuntimeError("Sync login failed: missing token")
        self._token = token
        self._initial_remote_refresh_done = False
        self._image_access_mode = "local"
        self._set_status(
            connected=True,
            projectId=self._config.project_id,
            username=self._config.username,
            role=str(response.get("role", "") or ""),
            isAdmin=bool(response.get("isAdmin")) if response.get("isAdmin") is not None else None,
        )

    def _fetch_remote_changes(self) -> dict[str, Any]:
        query = urllib.parse.urlencode({"since": str(self._cursor), "limit": "1200"})
        return self._request_json(f"/api/sync/changes?{query}")

    def _activate_lock(self, path: str | None) -> None:
        payload = {"path": path}
        response = self._request_json("/api/locks/activate", body=payload)
        with self._lock:
            self._active_file = response.get("activeFile")
            self._pending_active_file = self._active_file
        self._set_status(activeFile=self._active_file)

    def _collect_local_changes(self) -> list[dict[str, Any]]:
        current = self._build_snapshot()
        updates: list[dict[str, Any]] = []

        for rel_path, meta in current.items():
            previous = self._snapshot.get(rel_path)
            if previous == meta:
                continue
            abs_path = self._project_root / rel_path
            if self._is_recent_inbound_write(abs_path):
                continue
            try:
                data = abs_path.read_bytes()
            except OSError:
                continue
            updates.append(
                {
                    "path": rel_path.replace("\\", "/"),
                    "deleted": False,
                    "mtimeMs": int(meta["mtimeMs"]),
                    "sha1": meta["sha1"],
                    "contentBase64": base64.b64encode(data).decode("ascii"),
                }
            )

        for rel_path in self._snapshot:
            if rel_path in current:
                continue
            abs_path = self._project_root / rel_path
            if self._is_recent_inbound_write(abs_path):
                continue
            updates.append(
                {
                    "path": rel_path.replace("\\", "/"),
                    "deleted": True,
                    "mtimeMs": int(time.time() * 1000),
                    "sha1": "",
                    "contentBase64": "",
                }
            )

        self._snapshot = current
        return updates

    def _apply_remote_changes(self, changes: list[dict[str, Any]]) -> int:
        applied = 0
        for change in changes:
            source_token = str(change.get("sourceToken", ""))
            if source_token and source_token == self._token:
                continue

            rel_path = str(change.get("path", "")).strip().replace("\\", "/")
            if not rel_path:
                continue
            if self._is_image_rel_path(rel_path):
                continue

            target = (self._project_root / rel_path).resolve()
            try:
                target.relative_to(self._project_root)
            except ValueError:
                continue

            if bool(change.get("deleted")):
                try:
                    if target.exists() and target.is_file():
                        target.unlink()
                except OSError:
                    pass
                self._mark_inbound_write(target)
                applied += 1
                continue

            raw_b64 = change.get("contentBase64")
            if not isinstance(raw_b64, str):
                continue
            try:
                raw = base64.b64decode(raw_b64.encode("ascii"), validate=True)
            except Exception:
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                target.write_bytes(raw)
            except OSError:
                continue

            self._mark_inbound_write(target)
            applied += 1

        self._snapshot = self._build_snapshot()
        return applied

    def _build_snapshot(self) -> dict[str, dict[str, Any]]:
        snapshot: dict[str, dict[str, Any]] = {}
        for path in self._project_root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self._project_root)
            if self._should_skip(rel):
                continue

            suffix = path.suffix.lower()
            if suffix and suffix not in _DEFAULT_INCLUDE_EXTENSIONS:
                continue
            if suffix in _IMAGE_EXTENSIONS:
                continue

            try:
                stat = path.stat()
                payload = path.read_bytes()
            except OSError:
                continue

            sha1 = hashlib.sha1(payload).hexdigest()
            snapshot[str(rel).replace("\\", "/")] = {
                "mtimeMs": int(stat.st_mtime * 1000),
                "size": int(stat.st_size),
                "sha1": sha1,
            }

        return snapshot

    def _should_skip(self, relative_path: Path) -> bool:
        parts = {p.lower() for p in relative_path.parts}
        if ".git" in parts or ".sync" in parts:
            return True
        if "__pycache__" in parts:
            return True
        return False

    def _is_image_rel_path(self, rel_path: str) -> bool:
        suffix = Path(str(rel_path).strip().replace("\\", "/")).suffix.lower()
        return suffix in _IMAGE_EXTENSIONS

    def _request_json(
        self,
        endpoint: str,
        *,
        body: dict[str, Any] | None = None,
        include_auth: bool = True,
    ) -> dict[str, Any]:
        url = f"{self._server_url}{endpoint}"
        headers = {"Content-Type": "application/json"}
        if include_auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        payload = json.dumps(body or {}).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=payload, headers=headers)

        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=12) as response:
                    raw = response.read().decode("utf-8")
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except urllib.error.HTTPError as error:
                detail = ""
                try:
                    raw = error.read().decode("utf-8")
                    try:
                        payload = json.loads(raw)
                        detail = str(payload.get("detail") or payload.get("error") or raw)
                    except Exception:
                        detail = raw.strip() or str(error)
                except Exception:
                    detail = str(error)

                is_transient = int(error.code) in {408, 429, 500, 502, 503, 504}
                if is_transient and attempt < 2:
                    time.sleep(0.35 * (attempt + 1))
                    continue
                raise RuntimeError(f"HTTP {error.code} on {endpoint}: {detail}") from error
            except urllib.error.URLError as error:
                if attempt < 2:
                    time.sleep(0.35 * (attempt + 1))
                    continue
                raise RuntimeError(f"Request failed: {error}") from error
        return {}

    def get_image_access_mode(self) -> str:
        return str(self._image_access_mode or "local")

    def _ensure_auth(self) -> None:
        if not self._token:
            self._login()

    def get_project_summary(self) -> dict[str, Any]:
        self._ensure_auth()
        payload = self._request_json("/api/project/summary")
        mode = str(payload.get("imageAccessMode") or "local")
        self._image_access_mode = mode
        self._set_status(imageAccessMode=mode)
        return payload

    def get_image_manifest(self) -> dict[str, Any]:
        self._ensure_auth()
        return self._request_json("/api/images/manifest")

    def get_signed_image_read(self, path: str) -> dict[str, Any]:
        self._ensure_auth()
        query = urllib.parse.urlencode({"path": path})
        return self._request_json(f"/api/images/signed-read?{query}")

    def get_signed_image_write(self, path: str, content_type: str | None = None) -> dict[str, Any]:
        self._ensure_auth()
        return self._request_json(
            "/api/images/signed-write",
            body={"path": path, "contentType": content_type or ""},
        )

    def upload_image_via_signed_url(self, path: str, payload: bytes, content_type: str | None = None) -> dict[str, Any]:
        signed = self.get_signed_image_write(path, content_type=content_type)
        url = str(signed.get("url") or "")
        if not url:
            raise RuntimeError("Signed write URL not returned by backend")

        headers = {"Content-Type": content_type or "application/octet-stream"}
        for attempt in range(3):
            request = urllib.request.Request(url, data=payload, headers=headers, method="PUT")
            try:
                with urllib.request.urlopen(request, timeout=40):
                    return signed
            except urllib.error.HTTPError as error:
                transient = int(error.code) in {408, 429, 500, 502, 503, 504}
                if transient and attempt < 2:
                    time.sleep(0.35 * (attempt + 1))
                    continue
                try:
                    detail = error.read().decode("utf-8")
                except Exception:
                    detail = str(error)
                raise RuntimeError(f"Signed upload failed ({error.code}) for {path}: {detail}") from error
            except urllib.error.URLError as error:
                if attempt < 2:
                    time.sleep(0.35 * (attempt + 1))
                    continue
                raise RuntimeError(f"Signed upload request failed for {path}: {error}") from error

        raise RuntimeError(f"Signed upload failed for {path}")

    def request_image_prefetch(self, current_path: str | None, count: int) -> dict[str, Any]:
        self._ensure_auth()
        return self._request_json(
            "/api/images/prefetch",
            body={"currentPath": current_path or "", "count": int(max(1, count))},
        )

    def get_image_status_map(self) -> dict[str, Any]:
        self._ensure_auth()
        return self._request_json("/api/image-status")

    def set_image_status(self, image_name: str, status: str) -> dict[str, Any]:
        self._ensure_auth()
        return self._request_json(
            "/api/image-status",
            body={"imageName": str(image_name or ""), "status": str(status or "")},
        )

    def submit_deletions(self, paths: list[str]) -> dict[str, Any]:
        """Push explicit delete updates for project-relative paths."""
        self._ensure_auth()
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in paths:
            rel = self._normalize_rel(str(raw or ""))
            if not rel or rel in seen:
                continue
            seen.add(rel)
            normalized.append(rel)

        if not normalized:
            return {"ok": True, "applied": 0, "latestSeq": self._cursor, "rejected": []}

        now_ms = int(time.time() * 1000)
        updates = [
            {
                "path": rel,
                "deleted": True,
                "mtimeMs": now_ms,
                "sha1": "",
                "contentBase64": "",
            }
            for rel in normalized
        ]

        result = self._request_json("/api/sync/upsert", body={"updates": updates})
        self._cursor = max(self._cursor, int(result.get("latestSeq", self._cursor)))
        self._save_cursor(self._cursor)
        return result

    def _normalize_server_url(self, raw_url: str) -> str:
        value = str(raw_url or "").strip()
        if not value:
            return ""

        parsed = urllib.parse.urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            raise RuntimeError("Invalid server URL. Use format: https://your-domain")

        if parsed.scheme not in {"http", "https"}:
            raise RuntimeError("Invalid server URL scheme. Use http or https")

        # Keep only origin to avoid accidental '/api/...' path entries in settings.
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    def _load_cursor(self) -> int:
        if not self._cursor_file.exists():
            return 0
        try:
            data = json.loads(self._cursor_file.read_text(encoding="utf-8"))
            return max(0, int(data.get("cursor", 0)))
        except Exception:
            return 0

    def _save_cursor(self, cursor: int) -> None:
        payload = {"cursor": int(cursor), "updatedAt": int(time.time() * 1000)}
        try:
            self._cursor_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _normalize_rel(self, value: str) -> str:
        normalized = str(value).strip().replace("\\", "/")
        if normalized.startswith("/"):
            normalized = normalized[1:]
        return normalized

    def _mark_inbound_write(self, path: Path) -> None:
        with self._lock:
            self._inbound_apply_until[path] = time.time() + 2.5

    def _is_recent_inbound_write(self, path: Path) -> bool:
        now = time.time()
        with self._lock:
            expired = [candidate for candidate, until in self._inbound_apply_until.items() if until <= now]
            for candidate in expired:
                self._inbound_apply_until.pop(candidate, None)
            deadline = self._inbound_apply_until.get(path)
            return bool(deadline and deadline > now)

    def _set_status(self, **updates: Any) -> None:
        with self._lock:
            self._status.update(updates)
            snapshot = dict(self._status)
        if self._status_callback:
            try:
                self._status_callback(snapshot)
            except Exception:
                pass

