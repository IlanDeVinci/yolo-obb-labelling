from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import secrets
import sqlite3
import threading
import urllib.parse
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable

try:
    from fastapi import HTTPException
except Exception:
    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str) -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

try:
    from sync_config import IMAGE_SUFFIXES
except ImportError:
    from .sync_config import IMAGE_SUFFIXES


# ---------- Validators ----------


def normalize_path(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    if not normalized or normalized.startswith("/") or ".." in normalized:
        raise HTTPException(status_code=400, detail="Invalid path")
    if "//" in normalized:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not all(c.isalnum() or c in "._-/" for c in normalized):
        raise HTTPException(status_code=400, detail="Invalid path")
    return normalized


def sanitize_archive_member_path(value: str) -> str:
    normalized = str(value or "").replace("\\", "/").strip().lstrip("/")
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid archive member path")
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        raise HTTPException(status_code=400, detail="Invalid archive member path")
    for part in parts:
        if part in {".", ".."}:
            raise HTTPException(status_code=400, detail="Invalid archive member path")
    return "/".join(parts)


def estimate_b64_size_bytes(content_b64: str) -> int:
    raw = str(content_b64 or "")
    if not raw:
        return 0
    pad = 2 if raw.endswith("==") else (1 if raw.endswith("=") else 0)
    return max(0, (len(raw) * 3) // 4 - pad)


def is_image_path(path: str) -> bool:
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def is_label_text_path(path: str) -> bool:
    lower = str(path or "").lower()
    return lower.endswith(".txt") and "/labels/" in lower


def requires_explicit_lock(path: str) -> bool:
    lower = str(path or "").lower()
    return lower.endswith(".txt") and "/labels/" in lower


# ---------- Label Utils ----------


def label_paths_for_image(image_path: str) -> list[str]:
    normalized = str(image_path or "").strip().replace("\\", "/")
    parts = [p for p in normalized.split("/") if p]
    if not parts:
        return []

    stem = Path(parts[-1]).stem
    if not stem:
        return []

    label_name = f"{stem}.txt"
    for idx, part in enumerate(parts):
        if part.lower() == "images":
            prefix = parts[:idx]
            suffix = parts[idx + 1 : -1]
            label_root = [*prefix, "labels", *suffix]
            return [
                "/".join([*label_root, label_name]),
                "/".join([*label_root, "BB", label_name]),
                "/".join([*label_root, "OBB", label_name]),
            ]

    return [f"labels/{label_name}", f"labels/BB/{label_name}", f"labels/OBB/{label_name}"]


def bbox_to_corners(x_center: float, y_center: float, width: float, height: float) -> list[float]:
    half_w = width / 2.0
    half_h = height / 2.0
    return [
        x_center - half_w,
        y_center - half_h,
        x_center + half_w,
        y_center - half_h,
        x_center + half_w,
        y_center + half_h,
        x_center - half_w,
        y_center + half_h,
    ]


def parse_yolo_label_rows(raw_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, line in enumerate(str(raw_text or "").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        try:
            class_id = int(parts[0])
        except (ValueError, IndexError):
            continue

        try:
            if len(parts) >= 9:
                rows.append({
                    "line": idx,
                    "classId": class_id,
                    "points": [float(v) for v in parts[1:9]],
                    "format": "obb",
                })
            elif len(parts) >= 5:
                rows.append(
                    {
                        "line": idx,
                        "classId": class_id,
                        "points": bbox_to_corners(
                            float(parts[1]),
                            float(parts[2]),
                            float(parts[3]),
                            float(parts[4]),
                        ),
                        "format": "bbox",
                    }
                )
        except ValueError:
            continue
    return rows


def split_label_text_by_format(raw_text: str) -> tuple[str, str, int, int]:
    bb_lines: list[str] = []
    obb_lines: list[str] = []

    for line in str(raw_text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 5:
            continue
        try:
            int(parts[0])
            [float(v) for v in parts[1:]]
        except Exception:
            continue
        if len(parts) >= 9:
            obb_lines.append(stripped)
        else:
            bb_lines.append(stripped)

    return ("\n".join(bb_lines), "\n".join(obb_lines), len(bb_lines), len(obb_lines))


def label_stem_from_path(path: str) -> str:
    return Path(str(path or "")).stem.strip()


def is_bb_label_path(path: str) -> bool:
    return "/labels/bb/" in str(path or "").lower()


def is_obb_label_path(path: str) -> bool:
    return "/labels/obb/" in str(path or "").lower()


# ---------- Security / Auth / Lifecycle ----------


def hash_password(raw_password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", raw_password.encode("utf-8"), salt, 180_000)
    return (
        "pbkdf2_sha256$180000$"
        + base64.b64encode(salt).decode("ascii")
        + "$"
        + base64.b64encode(digest).decode("ascii")
    )


def verify_password(raw_password: str, encoded: str) -> bool:
    try:
        algo, rounds_raw, salt_b64, digest_b64 = encoded.split("$")
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds_raw)
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(digest_b64.encode("ascii"))
    except Exception:
        return False

    actual = hashlib.pbkdf2_hmac("sha256", raw_password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(actual, expected)


def extract_bearer_token(authorization: str | None) -> str:
    raw = str(authorization or "")
    if not raw.startswith("Bearer "):
        raise ValueError("Missing bearer token")
    token = raw.removeprefix("Bearer ").strip()
    if not token:
        raise ValueError("Missing bearer token")
    return token


def is_valid_bootstrap_token(provided: str | None, expected: str) -> bool:
    if not expected:
        return True
    candidate = str(provided or "").strip()
    if not candidate:
        return False
    return hmac.compare_digest(candidate, expected)


def can_delete_user(
    *,
    session_username: str,
    session_role: str,
    target_username: str,
    created_by_lookup: Callable[[str], str | None],
) -> bool:
    if target_username == session_username:
        return True
    if session_role == "owner":
        return True
    if session_role != "admin":
        return False
    created_by = created_by_lookup(target_username)
    return bool(created_by is not None and created_by == session_username)


def run_startup(
    *,
    init_db: Callable[[], None],
    ensure_daily_backup: Callable[[], None],
    backup_worker: Callable[[], None],
) -> threading.Thread:
    init_db()
    ensure_daily_backup()
    thread = threading.Thread(target=backup_worker, name="sync-backup-worker", daemon=True)
    thread.start()
    return thread


def run_shutdown(*, stop_event: threading.Event) -> None:
    stop_event.set()


# ---------- Common / Limits / Modes / Status ----------


VALID_IMAGE_STATUSES: frozenset[str] = frozenset({"in_progress", "completed", "yolo", "to_rotate"})
VALID_IMAGE_ACCESS_MODES: frozenset[str] = frozenset({"local", "hybrid", "cloud_only"})
REMOTE_IMAGE_ACCESS_MODES: frozenset[str] = frozenset({"hybrid", "cloud_only"})


def clamp_sync_changes_limit(limit: int) -> int:
    return max(50, min(2500, int(limit)))


def clamp_admin_table_limit(limit: int) -> int:
    return max(1, min(int(limit), 500))


def clamp_recent_changes_limit(limit: int) -> int:
    return max(5, min(120, int(limit)))


def clamp_admin_images_limit(limit: int) -> int:
    return max(1, min(int(limit), 20000))


def normalize_storage_mode(raw: str) -> str:
    value = str(raw or "").strip().lower()
    return value if value in {"auto", "db", "s3"} else "auto"


def normalize_image_access_mode(raw: str) -> str:
    value = str(raw or "").strip().lower()
    return value if value in VALID_IMAGE_ACCESS_MODES else "local"


def normalize_image_access_mode_value(value: object, *, default: str = "local") -> str:
    normalized = str(value or default).strip().lower()
    if normalized in VALID_IMAGE_ACCESS_MODES:
        return normalized
    return default


def normalize_image_status(value: object, *, default: str = "") -> str:
    normalized = str(value or default).strip().lower()
    if normalized in VALID_IMAGE_STATUSES:
        return normalized
    return ""


def is_remote_image_access_mode(value: object) -> bool:
    return normalize_image_access_mode_value(value, default="local") in REMOTE_IMAGE_ACCESS_MODES


def is_cloud_only_image_access_mode(value: object) -> bool:
    return normalize_image_access_mode_value(value, default="local") == "cloud_only"


def project_uses_s3_images(*, access_mode: str, storage_mode: str, s3_enabled: bool) -> bool:
    access = normalize_image_access_mode(access_mode)
    storage = normalize_storage_mode(storage_mode)
    if access == "local":
        return False
    if access in {"hybrid", "cloud_only"}:
        if not s3_enabled:
            raise ValueError("Project image access mode requires S3 but S3 is not configured")
        return True
    if storage == "db":
        return False
    if storage == "s3":
        if not s3_enabled:
            raise ValueError("Project requires S3 image storage but S3 is not configured")
        return True
    return bool(s3_enabled)


def s3_object_key(s3_prefix: str, project_id: str, path: str) -> str:
    return f"{s3_prefix}/{project_id}/{path}" if s3_prefix else f"{project_id}/{path}"


def image_read_url(cloudfront_base_url: str, object_key: str) -> str:
    encoded = urllib.parse.quote(object_key, safe="/._-")
    return f"{cloudfront_base_url}/{encoded}"


def normalize_cloudfront_invalidation_paths(keys: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in keys:
        key = str(raw or "").strip().lstrip("/")
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(f"/{key}")
    if not normalized:
        return []
    if len(normalized) > 900:
        first = str(keys[0] if keys else "").strip().lstrip("/")
        project_prefix = "/".join(first.split("/")[:2]).strip("/")
        if project_prefix:
            return [f"/{project_prefix}/*"]
    return normalized


def online_users_count(*, conn: sqlite3.Connection, project_id: str) -> int:
    row = conn.execute("SELECT COUNT(*) AS c FROM sessions WHERE project_id = ?", (str(project_id),)).fetchone()
    return int(row["c"] if row else 0)


def latest_change_seq(*, conn: sqlite3.Connection, project_id: str) -> int:
    row = conn.execute("SELECT COALESCE(MAX(seq), 0) AS s FROM changes WHERE project_id = ?", (str(project_id),)).fetchone()
    return int(row["s"] if row else 0)


def latest_status_seq(*, conn: sqlite3.Connection, project_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(updated_at), 0) AS s FROM image_status WHERE project_id = ?",
        (str(project_id),),
    ).fetchone()
    return int(row["s"] if row else 0)


def validate_table_name(table: str) -> bool:
    value = str(table or "").strip()
    return bool(value and value.replace("_", "a").isalnum())


def table_exists(*, conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (str(table),),
    ).fetchone()
    return row is not None


def table_columns(*, conn: sqlite3.Connection, table: str) -> list[str]:
    col_rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [str(row["name"]) for row in col_rows if row and row["name"]]


def build_search_clause(*, search_text: str, search_column: str, columns: list[str]) -> tuple[str, list[Any]]:
    text = str(search_text or "").strip()
    column = str(search_column or "").strip()
    if not text:
        return "", []
    like_value = f"%{text}%"
    if column:
        if column not in columns:
            raise ValueError("Invalid search column")
        return f' WHERE CAST("{column}" AS TEXT) LIKE ?', [like_value]
    if not columns:
        return "", []
    parts = [f'CAST("{col}" AS TEXT) LIKE ?' for col in columns]
    return " WHERE " + " OR ".join(parts), [like_value] * len(columns)


def serialize_db_rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows:
        record: dict[str, Any] = {}
        for key in row.keys():
            value = row[key]
            if isinstance(value, bytes):
                record[str(key)] = {
                    "type": "bytes",
                    "size": len(value),
                    "previewBase64": base64.b64encode(value[:48]).decode("ascii"),
                }
            else:
                record[str(key)] = value
        items.append(record)
    return items


def normalize_admin_image_sort(sort_by: str, order: str) -> tuple[str, str]:
    sort_key = str(sort_by or "path").strip().lower()
    sort_key = sort_key if sort_key in {"path", "size", "mtime"} else "path"
    sort_order = str(order or "asc").strip().lower()
    sort_order = sort_order if sort_order in {"asc", "desc"} else "asc"
    return sort_key, sort_order


def build_admin_image_items(
    *,
    db_rows: dict[str, dict[str, Any]],
    s3_rows: dict[str, dict[str, Any]],
    status_by_name: dict[str, str],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    all_paths = sorted(set(db_rows.keys()) | set(s3_rows.keys()), key=lambda v: v.lower())
    for path in all_paths:
        db_item = db_rows.get(path)
        s3_item = s3_rows.get(path)
        size_bytes = int((s3_item or {}).get("sizeBytes") or (db_item or {}).get("sizeBytes") or 0)
        modified_ms = int((s3_item or {}).get("mtimeMs") or (db_item or {}).get("mtimeMs") or 0)
        name = Path(path).name
        items.append(
            {
                "path": path,
                "name": name,
                "sizeBytes": int(size_bytes),
                "mtimeMs": int(modified_ms),
                "updatedAt": int((db_item or {}).get("updatedAt") or 0),
                "status": status_by_name.get(name, ""),
                "indexedInDb": bool(db_item),
                "presentInS3": bool(s3_item),
            }
        )
    return items


def sort_admin_image_items(items: list[dict[str, Any]], *, sort_key: str, sort_order: str) -> None:
    if sort_key == "size":
        items.sort(key=lambda v: (int(v["sizeBytes"]), str(v["path"]).lower()), reverse=(sort_order == "desc"))
    elif sort_key == "mtime":
        items.sort(key=lambda v: (int(v["mtimeMs"]), str(v["path"]).lower()), reverse=(sort_order == "desc"))
    else:
        items.sort(key=lambda v: str(v["path"]).lower(), reverse=(sort_order == "desc"))




def __getattr__(name: str):
    try:
        from sync_core_store import __dict__ as _store_dict
        from sync_core_status import __dict__ as _status_dict
    except ImportError:
        from .sync_core_store import __dict__ as _store_dict
        from .sync_core_status import __dict__ as _status_dict

    if name in _store_dict:
        return _store_dict[name]
    if name in _status_dict:
        return _status_dict[name]
    raise AttributeError(name)
