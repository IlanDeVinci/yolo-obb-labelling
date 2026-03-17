from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import io
import json
import mimetypes
import os
import secrets
import shutil
import sqlite3
import threading
import time
import tempfile
import urllib.parse
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

try:
    from PIL import Image, UnidentifiedImageError
except Exception:  # pragma: no cover - optional dependency in local dev
    Image = None

    class UnidentifiedImageError(Exception):
        pass

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    pillow_heif = None

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except Exception:  # pragma: no cover - optional dependency in local dev
    boto3 = None

    class BotoCoreError(Exception):
        pass

    class ClientError(Exception):
        pass


DB_PATH = Path(os.environ.get("SYNC_DB_PATH", "./data/sync.db")).resolve()
BACKUP_DIR = Path(os.environ.get("SYNC_BACKUP_DIR", "./data/backups")).resolve()
SESSION_TTL_SECONDS = max(20, int(os.environ.get("SYNC_SESSION_TTL_SECONDS", "900")))
BACKUP_RETENTION_DAYS = max(2, int(os.environ.get("SYNC_BACKUP_RETENTION_DAYS", "14")))
MAX_FILE_BYTES = max(64 * 1024, int(os.environ.get("SYNC_MAX_FILE_BYTES", str(8 * 1024 * 1024))))
MAX_ZIP_UPLOAD_BYTES = max(1024 * 1024, int(os.environ.get("SYNC_MAX_ZIP_UPLOAD_BYTES", str(512 * 1024 * 1024))))
BOOTSTRAP_TOKEN = os.environ.get("SYNC_BOOTSTRAP_TOKEN", "").strip()
REQUIRE_PROJECT_PASSWORD = str(os.environ.get("SYNC_REQUIRE_PROJECT_PASSWORD", "0")).strip().lower() in {"1", "true", "yes", "on"}
S3_BUCKET = os.environ.get("SYNC_S3_BUCKET", "").strip()
S3_PREFIX = os.environ.get("SYNC_S3_PREFIX", "datasets").strip().strip("/")
S3_REGION = os.environ.get("SYNC_S3_REGION", "").strip()
SIGNED_URL_TTL_SECONDS = max(30, min(900, int(os.environ.get("SYNC_SIGNED_URL_TTL_SECONDS", "180"))))
PREFETCH_MAX_BATCH = max(1, min(200, int(os.environ.get("SYNC_PREFETCH_MAX_BATCH", "40"))))
CLOUDFRONT_BASE_URL = os.environ.get("SYNC_CLOUDFRONT_BASE_URL", "").strip().rstrip("/")
CLOUDFRONT_DISTRIBUTION_ID = os.environ.get("SYNC_CLOUDFRONT_DISTRIBUTION_ID", "").strip()

IMAGE_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".tiff",
    ".tif",
    ".webp",
}

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="YOLO Cloud Sync Backend", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

_db_lock = threading.Lock()
_stop_event = threading.Event()
_s3_client = None
_normalize_jobs_lock = threading.Lock()
_normalize_jobs: dict[str, dict[str, Any]] = {}
_NORMALIZE_JOB_RETENTION_MS = 2 * 60 * 60 * 1000
_SESSION_CLEANUP_INTERVAL_MS = 60 * 1000
_last_session_cleanup_ms = 0


class BootstrapPayload(BaseModel):
    projectId: str = Field(min_length=2, max_length=120)
    projectPassword: str = Field(min_length=4, max_length=256)
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=4, max_length=256)


class LoginPayload(BaseModel):
    projectId: str
    projectPassword: str = ""
    username: str
    password: str


class ProjectOptionsPayload(BaseModel):
    username: str
    password: str


class CreateUserPayload(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=4, max_length=256)
    isAdmin: bool = False


class ProjectPasswordUpdatePayload(BaseModel):
    newProjectPassword: str = Field(min_length=4, max_length=256)


class AdminUserUpdatePayload(BaseModel):
    role: str | None = Field(default=None, pattern="^(admin|user)$")
    newPassword: str | None = Field(default=None, min_length=4, max_length=256)


class SignedWritePayload(BaseModel):
    path: str
    contentType: str | None = None


class SignedUploadCommitPayload(BaseModel):
    path: str
    sha1: str = ""
    sizeBytes: int = Field(default=0, ge=0)
    mtimeMs: int = Field(default=0, ge=0)


class PrefetchBatchPayload(BaseModel):
    currentPath: str | None = None
    count: int = Field(default=10, ge=1, le=200)


class ProjectImageAccessPayload(BaseModel):
    imageAccessMode: str = Field(pattern="^(local|hybrid|cloud_only)$")


class ImageStatusPayload(BaseModel):
    imageName: str = Field(min_length=1, max_length=512)
    status: str = Field(pattern="^(in_progress|completed|yolo|to_rotate)$")


class ImageStatusSyncItem(BaseModel):
    imageName: str = Field(min_length=1, max_length=512)
    status: str = Field(pattern="^(in_progress|completed|yolo|to_rotate)$")


class AdminImageStatusSyncPayload(BaseModel):
    items: list[ImageStatusSyncItem] = Field(default_factory=list, max_length=50000)


class ActivateLockPayload(BaseModel):
    path: str | None = None


class AdminImageLabelsWritePayload(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    labelPath: str | None = Field(default=None, max_length=1024)
    lines: str = Field(default="")


class UpsertFilePayload(BaseModel):
    path: str
    deleted: bool = False
    mtimeMs: int = 0
    sha1: str = ""
    contentBase64: str = ""


class SyncUpsertPayload(BaseModel):
    updates: list[UpsertFilePayload]


class ProjectStoragePayload(BaseModel):
    storageMode: str = Field(pattern="^(auto|db|s3)$")


class AdminImageDeletePayload(BaseModel):
    paths: list[str] = Field(default_factory=list)
    deleteLabels: bool = True


class AdminImageStatusReconcilePayload(BaseModel):
    defaultStatus: str = Field(default="in_progress", pattern="^(in_progress|completed|yolo|to_rotate)$")
    removeOrphans: bool = False


class AdminNormalizeImagesPayload(BaseModel):
    paths: list[str] = Field(default_factory=list)
    quality: int = Field(default=90, ge=60, le=95)


class BackupRetentionPayload(BaseModel):
    retentionValue: int = Field(default=14, ge=1, le=3650)
    retentionUnit: str = Field(default="days", pattern="^(days|months)$")


class BackupRestorePayload(BaseModel):
    backupName: str = Field(min_length=1, max_length=255)
    confirmText: str = Field(min_length=1, max_length=300)


class BackupDryRunPayload(BaseModel):
    backupName: str = Field(min_length=1, max_length=255)


class DatabaseTableQueryPayload(BaseModel):
    table: str = Field(min_length=1, max_length=120)
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
    search: str = Field(default="", max_length=250)
    searchColumn: str = Field(default="", max_length=120)


@dataclass
class SessionContext:
    token: str
    project_id: str
    username: str
    role: str
    is_admin: bool
    active_file: str | None


# -------------------------
# DB + security helpers
# -------------------------

def _now_ms() -> int:
    return int(time.time() * 1000)


def _utc_now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _normalize_path(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    if not normalized or normalized.startswith("/") or ".." in normalized:
        raise HTTPException(status_code=400, detail="Invalid path")
    if "//" in normalized:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not all(c.isalnum() or c in "._-/" for c in normalized):
        raise HTTPException(status_code=400, detail="Invalid path")
    return normalized


def _requires_explicit_lock(path: str) -> bool:
    lower = path.lower()
    return lower.endswith(".txt") and "/labels/" in lower


def _is_image_path(path: str) -> bool:
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def _is_label_text_path(path: str) -> bool:
    lower = str(path or "").lower()
    return lower.endswith(".txt") and "/labels/" in lower


def _resolve_normalize_image_paths(project_id: str, requested_paths: list[str] | None) -> list[str]:
    requested = requested_paths or []
    if requested:
        paths: list[str] = []
        seen: set[str] = set()
        for raw in requested:
            path = _normalize_path(str(raw or ""))
            if not _is_image_path(path):
                continue
            if path in seen:
                continue
            seen.add(path)
            paths.append(path)
        return paths

    merged = _collect_project_image_rows_from_db(project_id)
    if _is_s3_enabled():
        try:
            merged.update(_collect_project_image_rows_from_s3(project_id))
        except Exception:
            pass
    return sorted(merged.keys(), key=lambda v: v.lower())


def _normalize_images_to_jpeg_core(
    *,
    project_id: str,
    username: str,
    source_token: str,
    quality: int,
    paths: list[str],
    progress_cb: Any | None = None,
    should_cancel_cb: Any | None = None,
) -> dict[str, Any]:
    if Image is None:
        raise HTTPException(status_code=501, detail="Pillow is required for image normalization")

    if not paths:
        return {
            "ok": True,
            "projectId": project_id,
            "requested": 0,
            "converted": 0,
            "failed": 0,
            "failedItems": [],
            "quality": quality,
        }

    use_s3_for_image = _project_uses_s3_images(project_id)
    converted = 0
    processed = 0
    failed_items: list[dict[str, str]] = []
    total = len(paths)
    canceled = False

    for path in paths:
        if callable(should_cancel_cb):
            try:
                if bool(should_cancel_cb()):
                    canceled = True
                    break
            except Exception:
                pass
        processed += 1
        try:
            raw: bytes
            if use_s3_for_image and _is_s3_enabled() and _s3_image_exists(project_id, path):
                raw = _s3_get_image_bytes(project_id, path)
            else:
                with _db_lock:
                    row = _CONN.execute(
                        "SELECT content_base64 FROM files WHERE project_id = ? AND path = ? AND deleted = 0",
                        (project_id, path),
                    ).fetchone()
                if row is None:
                    raise RuntimeError("Image not found in DB")
                content_b64 = str(row["content_base64"] or "")
                if not content_b64:
                    raise RuntimeError("Image bytes unavailable in DB")
                raw = base64.b64decode(content_b64.encode("ascii"), validate=False)

            image = Image.open(io.BytesIO(raw))
            image.load()
            if image.mode != "RGB":
                image = image.convert("RGB")

            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=True)
            jpeg_bytes = buffer.getvalue()
            sha1 = hashlib.sha1(jpeg_bytes).hexdigest()
            now = _now_ms()

            content_b64 = ""
            if use_s3_for_image and _is_s3_enabled():
                _s3_put_image(project_id, path, jpeg_bytes)
            else:
                content_b64 = base64.b64encode(jpeg_bytes).decode("ascii")

            with _db_lock:
                _CONN.execute(
                    """
                    INSERT INTO files (project_id, path, deleted, mtime_ms, sha1, content_base64, updated_at)
                    VALUES (?, ?, 0, ?, ?, ?, ?)
                    ON CONFLICT(project_id, path) DO UPDATE SET
                      deleted=0,
                      mtime_ms=excluded.mtime_ms,
                      sha1=excluded.sha1,
                      content_base64=excluded.content_base64,
                      updated_at=excluded.updated_at
                    """,
                    (project_id, path, now, sha1, content_b64, now),
                )
                _CONN.execute(
                    "INSERT INTO changes (project_id, username, source_token, path, deleted, mtime_ms, sha1, content_base64, created_at) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)",
                    (project_id, username, source_token, path, now, sha1, content_b64, now),
                )
                _CONN.commit()
            converted += 1
        except UnidentifiedImageError:
            if len(failed_items) < 100:
                failed_items.append({"path": path, "error": "unrecognized-image-format"})
        except Exception as exc:  # noqa: BLE001
            if len(failed_items) < 100:
                failed_items.append({"path": path, "error": str(exc)})

        if callable(progress_cb):
            try:
                progress_cb(
                    {
                        "processed": processed,
                        "total": total,
                        "converted": converted,
                        "failed": processed - converted,
                        "currentPath": path,
                    }
                )
            except Exception:
                pass

    return {
        "ok": True,
        "projectId": project_id,
        "requested": total,
        "processed": processed,
        "converted": converted,
        "failed": total - converted,
        "failedItems": failed_items,
        "quality": quality,
        "canceled": canceled,
    }


def _prune_normalize_jobs() -> None:
    now = _now_ms()
    stale_ids = [
        job_id
        for job_id, job in _normalize_jobs.items()
        if now - int(job.get("updatedAt", now)) > _NORMALIZE_JOB_RETENTION_MS
    ]
    for job_id in stale_ids:
        _normalize_jobs.pop(job_id, None)


def _sanitize_archive_member_path(value: str) -> str:
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


def _is_s3_enabled() -> bool:
    return bool(S3_BUCKET and boto3 is not None)


def _s3_object_key(project_id: str, path: str) -> str:
    # Keep all synced images inside a folder-like prefix, never at bucket root.
    return f"{S3_PREFIX}/{project_id}/{path}" if S3_PREFIX else f"{project_id}/{path}"


def _get_s3_client():
    global _s3_client
    if not _is_s3_enabled():
        return None
    if _s3_client is None:
        kwargs: dict[str, Any] = {}
        if S3_REGION:
            kwargs["region_name"] = S3_REGION
        _s3_client = boto3.client("s3", **kwargs)
    return _s3_client


def _s3_put_image(project_id: str, path: str, raw: bytes) -> None:
    client = _get_s3_client()
    if client is None:
        raise RuntimeError("S3 client unavailable")

    content_type, _ = mimetypes.guess_type(path)
    extra_args = {}
    if content_type:
        extra_args["ContentType"] = content_type

    key = _s3_object_key(project_id, path)
    client.put_object(Bucket=S3_BUCKET, Key=key, Body=raw, **extra_args)


def _s3_delete_image(project_id: str, path: str) -> None:
    client = _get_s3_client()
    if client is None:
        return
    key = _s3_object_key(project_id, path)
    client.delete_object(Bucket=S3_BUCKET, Key=key)


def _s3_get_image_base64(project_id: str, path: str) -> str:
    client = _get_s3_client()
    if client is None:
        raise RuntimeError("S3 client unavailable")

    key = _s3_object_key(project_id, path)
    response = client.get_object(Bucket=S3_BUCKET, Key=key)
    body = response.get("Body")
    raw = body.read() if body is not None else b""
    return base64.b64encode(raw).decode("ascii")


def _s3_get_image_bytes(project_id: str, path: str) -> bytes:
    client = _get_s3_client()
    if client is None:
        raise RuntimeError("S3 client unavailable")

    key = _s3_object_key(project_id, path)
    response = client.get_object(Bucket=S3_BUCKET, Key=key)
    body = response.get("Body")
    return body.read() if body is not None else b""


def _hash_password(raw_password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", raw_password.encode("utf-8"), salt, 180_000)
    return "pbkdf2_sha256$180000$" + base64.b64encode(salt).decode("ascii") + "$" + base64.b64encode(digest).decode("ascii")


def _verify_password(raw_password: str, encoded: str) -> bool:
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


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


_CONN = _connect()


def _init_db() -> None:
    with _db_lock:
        cur = _CONN.cursor()
        cur.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS projects (
              id TEXT PRIMARY KEY,
              password_hash TEXT NOT NULL,
                            storage_mode TEXT NOT NULL DEFAULT 'auto',
                            image_access_mode TEXT NOT NULL DEFAULT 'local',
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              project_id TEXT NOT NULL,
              username TEXT NOT NULL,
              password_hash TEXT NOT NULL,
              is_admin INTEGER NOT NULL DEFAULT 0,
                            role TEXT NOT NULL DEFAULT 'user',
                            created_by TEXT,
              created_at TEXT NOT NULL,
              UNIQUE(project_id, username)
            );
            CREATE TABLE IF NOT EXISTS sessions (
              token TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              username TEXT NOT NULL,
                            role TEXT NOT NULL DEFAULT 'user',
              is_admin INTEGER NOT NULL DEFAULT 0,
              active_file TEXT,
              created_at INTEGER NOT NULL,
              last_seen INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS locks (
              project_id TEXT NOT NULL,
              path TEXT NOT NULL,
              token TEXT NOT NULL,
              username TEXT NOT NULL,
              updated_at INTEGER NOT NULL,
              PRIMARY KEY(project_id, path)
            );
            CREATE TABLE IF NOT EXISTS files (
              project_id TEXT NOT NULL,
              path TEXT NOT NULL,
              deleted INTEGER NOT NULL DEFAULT 0,
              mtime_ms INTEGER NOT NULL,
              sha1 TEXT NOT NULL,
              content_base64 TEXT NOT NULL,
              updated_at INTEGER NOT NULL,
              PRIMARY KEY(project_id, path)
            );
            CREATE TABLE IF NOT EXISTS changes (
              seq INTEGER PRIMARY KEY AUTOINCREMENT,
              project_id TEXT NOT NULL,
              username TEXT NOT NULL,
              source_token TEXT NOT NULL,
              path TEXT NOT NULL,
              deleted INTEGER NOT NULL DEFAULT 0,
              mtime_ms INTEGER NOT NULL,
              sha1 TEXT NOT NULL,
              content_base64 TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
                        CREATE TABLE IF NOT EXISTS image_status (
                            project_id TEXT NOT NULL,
                            image_name TEXT NOT NULL,
                            status TEXT NOT NULL,
                            updated_at INTEGER NOT NULL,
                            updated_by TEXT NOT NULL,
                            PRIMARY KEY(project_id, image_name)
                        );
                        CREATE TABLE IF NOT EXISTS app_settings (
                            key TEXT PRIMARY KEY,
                            value TEXT NOT NULL,
                            updated_at INTEGER NOT NULL
                        );
            """
        )
        columns = _CONN.execute("PRAGMA table_info(projects)").fetchall()
        column_names = {str(row["name"]) for row in columns}
        if "storage_mode" not in column_names:
            _CONN.execute("ALTER TABLE projects ADD COLUMN storage_mode TEXT NOT NULL DEFAULT 'auto'")
        if "image_access_mode" not in column_names:
            _CONN.execute("ALTER TABLE projects ADD COLUMN image_access_mode TEXT NOT NULL DEFAULT 'local'")
            _CONN.execute(
                """
                UPDATE projects
                SET image_access_mode = CASE
                  WHEN lower(storage_mode) = 's3' THEN 'hybrid'
                  ELSE 'local'
                END
                """
            )

        user_columns = _CONN.execute("PRAGMA table_info(users)").fetchall()
        user_column_names = {str(row["name"]) for row in user_columns}
        if "role" not in user_column_names:
            _CONN.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
            _CONN.execute(
                "UPDATE users SET role = CASE WHEN is_admin = 1 THEN 'admin' ELSE 'user' END"
            )
        if "created_by" not in user_column_names:
            _CONN.execute("ALTER TABLE users ADD COLUMN created_by TEXT")

        session_columns = _CONN.execute("PRAGMA table_info(sessions)").fetchall()
        session_column_names = {str(row["name"]) for row in session_columns}
        if "role" not in session_column_names:
            _CONN.execute("ALTER TABLE sessions ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")

        project_ids = [
            str(row["id"]) for row in _CONN.execute("SELECT id FROM projects ORDER BY id ASC").fetchall()
        ]
        for project_id in project_ids:
            owner_exists = _CONN.execute(
                "SELECT 1 FROM users WHERE project_id = ? AND role = 'owner' LIMIT 1",
                (project_id,),
            ).fetchone()
            if owner_exists is None:
                oldest_admin = _CONN.execute(
                    """
                    SELECT username
                    FROM users
                    WHERE project_id = ? AND (role = 'admin' OR is_admin = 1)
                    ORDER BY created_at ASC, id ASC
                    LIMIT 1
                    """,
                    (project_id,),
                ).fetchone()
                if oldest_admin is not None:
                    _CONN.execute(
                        "UPDATE users SET role = 'owner', is_admin = 1 WHERE project_id = ? AND username = ?",
                        (project_id, str(oldest_admin["username"])),
                    )

        _CONN.execute(
            "UPDATE users SET role = CASE WHEN role IN ('owner', 'admin', 'user') THEN role ELSE 'user' END"
        )
        _CONN.execute(
            "UPDATE users SET is_admin = CASE WHEN role IN ('owner', 'admin') THEN 1 ELSE 0 END"
        )
        _CONN.commit()


def _get_project_storage_mode(project_id: str) -> str:
    row = _CONN.execute(
        "SELECT storage_mode FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    raw = str(row["storage_mode"]).strip().lower() if row and row["storage_mode"] else "auto"
    return raw if raw in {"auto", "db", "s3"} else "auto"


def _project_uses_s3_images(project_id: str) -> bool:
    access_mode = _get_project_image_access_mode(project_id)
    if access_mode == "local":
        return False
    if access_mode in {"hybrid", "cloud_only"}:
        if not _is_s3_enabled():
            raise HTTPException(status_code=400, detail="Project image access mode requires S3 but S3 is not configured")
        return True

    mode = _get_project_storage_mode(project_id)
    if mode == "db":
        return False
    if mode == "s3":
        if not _is_s3_enabled():
            raise HTTPException(status_code=400, detail="Project requires S3 image storage but S3 is not configured")
        return True
    return _is_s3_enabled()


def _get_project_image_access_mode(project_id: str) -> str:
    row = _CONN.execute(
        "SELECT image_access_mode FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    raw = str(row["image_access_mode"]).strip().lower() if row and row["image_access_mode"] else "local"
    return raw if raw in {"local", "hybrid", "cloud_only"} else "local"


def _session_can_delete_user(session: SessionContext, target_username: str) -> bool:
    if target_username == session.username:
        return True

    if session.role == "owner":
        return True

    if session.role != "admin":
        return False

    row = _CONN.execute(
        "SELECT created_by FROM users WHERE project_id = ? AND username = ?",
        (session.project_id, target_username),
    ).fetchone()
    if row is None:
        return False
    return str(row["created_by"] or "") == session.username


def _session_can_reset_user_password(session: SessionContext, target_username: str) -> bool:
    if target_username == session.username:
        return True
    if session.role == "owner":
        return True
    if session.role != "admin":
        return False
    row = _CONN.execute(
        "SELECT created_by FROM users WHERE project_id = ? AND username = ?",
        (session.project_id, target_username),
    ).fetchone()
    if row is None:
        return False
    return str(row["created_by"] or "") == session.username


def _ensure_s3_image_mode(session: SessionContext) -> None:
    mode = _get_project_image_access_mode(session.project_id)
    if mode == "local":
        raise HTTPException(status_code=400, detail="Project imageAccessMode is local")
    if not _project_uses_s3_images(session.project_id):
        raise HTTPException(status_code=400, detail="S3 image access is unavailable for this project")


def _s3_list_project_images(project_id: str) -> list[dict[str, Any]]:
    client = _get_s3_client()
    if client is None:
        raise HTTPException(status_code=400, detail="S3 client unavailable")

    prefix = _s3_object_key(project_id, "")
    entries: list[dict[str, Any]] = []
    continuation_token: str | None = None

    while True:
        kwargs: dict[str, Any] = {
            "Bucket": S3_BUCKET,
            "Prefix": prefix,
            "MaxKeys": 1000,
        }
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        result = client.list_objects_v2(**kwargs)
        for item in result.get("Contents", []):
            key = str(item.get("Key") or "")
            if not key or not key.startswith(prefix):
                continue
            rel = key[len(prefix):].lstrip("/")
            if not rel or not _is_image_path(rel):
                continue

            modified = item.get("LastModified")
            modified_ms = 0
            if modified is not None:
                try:
                    modified_ms = int(modified.timestamp() * 1000)
                except Exception:
                    modified_ms = 0

            entries.append(
                {
                    "path": rel,
                    "size": int(item.get("Size") or 0),
                    "etag": str(item.get("ETag") or "").strip('"'),
                    "lastModified": modified_ms,
                }
            )

        if not bool(result.get("IsTruncated")):
            break
        continuation_token = str(result.get("NextContinuationToken") or "") or None

    entries.sort(key=lambda v: str(v["path"]).lower())
    return entries


def _s3_signed_get_url(project_id: str, path: str, *, expires_seconds: int) -> str:
    client = _get_s3_client()
    if client is None:
        raise HTTPException(status_code=400, detail="S3 client unavailable")
    key = _s3_object_key(project_id, path)
    return str(
        client.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": S3_BUCKET, "Key": key},
            ExpiresIn=expires_seconds,
        )
    )


def _s3_signed_put_url(project_id: str, path: str, *, content_type: str | None, expires_seconds: int) -> str:
    client = _get_s3_client()
    if client is None:
        raise HTTPException(status_code=400, detail="S3 client unavailable")
    key = _s3_object_key(project_id, path)
    params: dict[str, Any] = {"Bucket": S3_BUCKET, "Key": key}
    guessed = content_type or mimetypes.guess_type(path)[0]
    if guessed:
        params["ContentType"] = guessed
    return str(
        client.generate_presigned_url(
            ClientMethod="put_object",
            Params=params,
            ExpiresIn=expires_seconds,
        )
    )


def _image_read_url(project_id: str, path: str, *, expires_seconds: int) -> str:
    if CLOUDFRONT_BASE_URL:
        key = _s3_object_key(project_id, path)
        encoded = urllib.parse.quote(key, safe="/._-")
        return f"{CLOUDFRONT_BASE_URL}/{encoded}"
    return _s3_signed_get_url(project_id, path, expires_seconds=expires_seconds)


def _cloudfront_invalidate_keys(keys: list[str], *, caller_tag: str) -> dict[str, Any]:
    if boto3 is None:
        return {"ok": False, "reason": "boto3-unavailable"}
    if not CLOUDFRONT_DISTRIBUTION_ID:
        return {"ok": False, "reason": "distribution-id-missing"}

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in keys:
        key = str(raw or "").strip().lstrip("/")
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(f"/{key}")

    if not normalized:
        return {"ok": False, "reason": "no-paths"}

    # CloudFront path count has service limits; fallback to wildcard for large jobs.
    if len(normalized) > 900:
        first = str(keys[0] if keys else "").strip().lstrip("/")
        project_prefix = "/".join(first.split("/")[:2]).strip("/")
        if project_prefix:
            normalized = [f"/{project_prefix}/*"]

    client = boto3.client("cloudfront")
    response = client.create_invalidation(
        DistributionId=CLOUDFRONT_DISTRIBUTION_ID,
        InvalidationBatch={
            "Paths": {"Quantity": len(normalized), "Items": normalized},
            "CallerReference": f"{caller_tag}-{_now_ms()}",
        },
    )
    inv = response.get("Invalidation") or {}
    return {
        "ok": True,
        "invalidationId": str(inv.get("Id") or ""),
        "status": str(inv.get("Status") or ""),
        "pathCount": len(normalized),
    }


def _s3_image_exists(project_id: str, path: str) -> bool:
    client = _get_s3_client()
    if client is None:
        return False
    key = _s3_object_key(project_id, path)
    try:
        client.head_object(Bucket=S3_BUCKET, Key=key)
        return True
    except ClientError:
        return False
    except BotoCoreError:
        return False


def _db_file_exists(project_id: str, path: str) -> bool:
    row = _CONN.execute(
        "SELECT deleted FROM files WHERE project_id = ? AND path = ?",
        (project_id, path),
    ).fetchone()
    if row is None:
        return False
    return not bool(row["deleted"])


def _estimate_b64_size_bytes(content_b64: str) -> int:
    raw = str(content_b64 or "")
    if not raw:
        return 0
    pad = 0
    if raw.endswith("=="):
        pad = 2
    elif raw.endswith("="):
        pad = 1
    return max(0, (len(raw) * 3) // 4 - pad)


def _label_paths_for_image(image_path: str) -> list[str]:
    normalized = str(image_path or "").strip().replace("\\", "/")
    parts = [p for p in normalized.split("/") if p]
    if not parts:
        return []

    filename = parts[-1]
    stem = Path(filename).stem
    if not stem:
        return []

    label_name = f"{stem}.txt"
    for idx, part in enumerate(parts):
        if part.lower() == "images":
            prefix = parts[:idx]
            suffix = parts[idx + 1:-1]
            label_root = [*prefix, "labels", *suffix]
            return [
                "/".join([*label_root, label_name]),
                "/".join([*label_root, "BB", label_name]),
                "/".join([*label_root, "OBB", label_name]),
            ]

    return [f"labels/{label_name}", f"labels/BB/{label_name}", f"labels/OBB/{label_name}"]


def _bbox_to_corners(x_center: float, y_center: float, width: float, height: float) -> list[float]:
    half_w = width / 2.0
    half_h = height / 2.0
    x1 = x_center - half_w
    y1 = y_center - half_h
    x2 = x_center + half_w
    y2 = y_center - half_h
    x3 = x_center + half_w
    y3 = y_center + half_h
    x4 = x_center - half_w
    y4 = y_center + half_h
    return [x1, y1, x2, y2, x3, y3, x4, y4]


def _parse_yolo_label_rows(raw_text: str) -> list[dict[str, Any]]:
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
                points = [float(v) for v in parts[1:9]]
                rows.append({"line": idx, "classId": class_id, "points": points, "format": "obb"})
                continue
            if len(parts) >= 5:
                x_center = float(parts[1])
                y_center = float(parts[2])
                width = float(parts[3])
                height = float(parts[4])
                rows.append(
                    {
                        "line": idx,
                        "classId": class_id,
                        "points": _bbox_to_corners(x_center, y_center, width, height),
                        "format": "bbox",
                    }
                )
                continue
        except ValueError:
            continue
    return rows


def _validate_yolo_label_text(raw_text: str) -> tuple[str, int]:
    """Validate YOLO label text and return normalized text + row count.

    Accepted line formats:
    - OBB: class x1 y1 x2 y2 x3 y3 x4 y4
    - BBox: class x_center y_center width height
    """
    out_lines: list[str] = []
    count = 0

    for idx, line in enumerate(str(raw_text or "").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) not in {5, 9}:
            raise HTTPException(status_code=400, detail=f"Invalid label format on line {idx}")

        try:
            class_id = int(parts[0])
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid class id on line {idx}") from exc
        if class_id < 0:
            raise HTTPException(status_code=400, detail=f"Class id must be >= 0 on line {idx}")

        values: list[float] = []
        try:
            values = [float(v) for v in parts[1:]]
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid numeric value on line {idx}") from exc

        for value in values:
            if value < 0.0 or value > 1.0:
                raise HTTPException(status_code=400, detail=f"Coordinates must be normalized to [0,1] on line {idx}")

        normalized_values = " ".join(f"{v:.6f}" for v in values)
        out_lines.append(f"{class_id} {normalized_values}")
        count += 1

    return ("\n".join(out_lines) + ("\n" if out_lines else ""), count)


def _fetch_project_image_status_map(project_id: str) -> dict[str, str]:
    rows = _CONN.execute(
        "SELECT image_name, status FROM image_status WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    out: dict[str, str] = {}
    for row in rows:
        image_name = str(row["image_name"] or "").strip()
        status = str(row["status"] or "").strip().lower()
        if not image_name:
            continue
        if status not in {"in_progress", "completed", "yolo", "to_rotate"}:
            continue
        out[image_name] = status
    return out


def _fetch_project_label_counts_by_stem(project_id: str) -> dict[str, int]:
    rows = _CONN.execute(
        "SELECT path, content_base64 FROM files WHERE project_id = ? AND deleted = 0 ORDER BY path ASC",
        (project_id,),
    ).fetchall()

    counts: dict[str, int] = {}
    for row in rows:
        path = str(row["path"] or "")
        if not _is_label_text_path(path):
            continue
        stem = Path(path).stem.strip()
        if not stem:
            continue

        content_b64 = str(row["content_base64"] or "")
        if not content_b64:
            counts.setdefault(stem, 0)
            continue

        try:
            raw_text = base64.b64decode(content_b64.encode("ascii"), validate=False).decode("utf-8", errors="replace")
        except Exception:
            raw_text = ""

        parsed = _parse_yolo_label_rows(raw_text)
        label_count = len(parsed)
        # Avoid double-counting when both bb/obb files exist for same image stem.
        counts[stem] = max(counts.get(stem, 0), label_count)

    return counts


def _collect_project_image_rows_from_db(project_id: str) -> dict[str, dict[str, Any]]:
    rows = _CONN.execute(
        "SELECT path, mtime_ms, updated_at, content_base64, sha1 FROM files WHERE project_id = ? AND deleted = 0 ORDER BY path ASC",
        (project_id,),
    ).fetchall()

    out: dict[str, dict[str, Any]] = {}

    for row in rows:
        path = str(row["path"] or "")
        if not path or not _is_image_path(path):
            continue
        out[path] = {
            "path": path,
            "mtimeMs": int(row["mtime_ms"] or 0),
            "updatedAt": int(row["updated_at"] or 0),
            "sha1": str(row["sha1"] or ""),
            "sizeBytes": _estimate_b64_size_bytes(str(row["content_base64"] or "")),
        }
    return out


def _collect_project_image_rows_from_s3(project_id: str) -> dict[str, dict[str, Any]]:
    if not _is_s3_enabled():
        return {}
    manifest = _s3_list_project_images(project_id)
    out: dict[str, dict[str, Any]] = {}
    for item in manifest:
        path = str(item.get("path") or "")
        if not path:
            continue
        out[path] = {
            "path": path,
            "sizeBytes": int(item.get("size") or 0),
            "mtimeMs": int(item.get("lastModified") or 0),
            "etag": str(item.get("etag") or ""),
        }
    return out


def _record_deleted_file(project_id: str, *, username: str, source_token: str, path: str, mtime_ms: int) -> None:
    _CONN.execute(
        """
        INSERT INTO files (project_id, path, deleted, mtime_ms, sha1, content_base64, updated_at)
        VALUES (?, ?, 1, ?, '', '', ?)
        ON CONFLICT(project_id, path) DO UPDATE SET
          deleted=1,
          mtime_ms=excluded.mtime_ms,
          sha1='',
          content_base64='',
          updated_at=excluded.updated_at
        """,
        (project_id, path, mtime_ms, mtime_ms),
    )
    _CONN.execute(
        "INSERT INTO changes (project_id, username, source_token, path, deleted, mtime_ms, sha1, content_base64, created_at) VALUES (?, ?, ?, ?, 1, ?, '', '', ?)",
        (project_id, username, source_token, path, mtime_ms, mtime_ms),
    )


def _cleanup_stale_sessions(force: bool = False) -> None:
    global _last_session_cleanup_ms

    now = _now_ms()
    if not force and (now - _last_session_cleanup_ms) < _SESSION_CLEANUP_INTERVAL_MS:
        return

    cutoff = now - SESSION_TTL_SECONDS * 1000
    with _db_lock:
        cur = _CONN.cursor()
        cur.execute("DELETE FROM sessions WHERE last_seen < ?", (cutoff,))
        cur.execute(
            "DELETE FROM locks WHERE token NOT IN (SELECT token FROM sessions)",
        )
        _CONN.commit()
        _last_session_cleanup_ms = now


def _touch_session(token: str) -> None:
    with _db_lock:
        _CONN.execute(
            "UPDATE sessions SET last_seen = ? WHERE token = ?",
            (_now_ms(), token),
        )
        _CONN.commit()


def _get_session_by_token(token: str) -> SessionContext:
    _cleanup_stale_sessions()
    row = _CONN.execute(
        "SELECT token, project_id, username, role, is_admin, active_file FROM sessions WHERE token = ?",
        (token,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    _touch_session(token)
    return SessionContext(
        token=str(row["token"]),
        project_id=str(row["project_id"]),
        username=str(row["username"]),
        role=str(row["role"] or "user"),
        is_admin=bool(row["is_admin"]),
        active_file=str(row["active_file"]) if row["active_file"] else None,
    )


def _backup_db(reason: str) -> str:
    stamp = dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    backup_file = BACKUP_DIR / f"sync-{stamp}-{reason}.db"
    with _db_lock:
        _CONN.commit()
        shutil.copy2(DB_PATH, backup_file)
    return str(backup_file)


def _setting_get(key: str, default_value: str = "") -> str:
    row = _CONN.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        (str(key or ""),),
    ).fetchone()
    if row is None:
        return str(default_value)
    return str(row["value"] or default_value)


def _setting_set(key: str, value: str) -> None:
    with _db_lock:
        _CONN.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (str(key or ""), str(value or ""), _now_ms()),
        )
        _CONN.commit()


def _get_backup_retention_policy() -> tuple[int, str, int]:
    raw_unit = _setting_get("backup_retention_unit", "days").strip().lower()
    unit = raw_unit if raw_unit in {"days", "months"} else "days"

    default_value = BACKUP_RETENTION_DAYS
    if unit == "months":
        default_value = max(1, min(120, int(round(BACKUP_RETENTION_DAYS / 30))))

    raw_value = _setting_get("backup_retention_value", str(default_value)).strip()
    try:
        retention_value = int(raw_value)
    except Exception:
        retention_value = default_value

    if unit == "days":
        retention_value = max(2, min(3650, retention_value))
        retention_days = retention_value
    else:
        retention_value = max(1, min(120, retention_value))
        retention_days = max(2, min(3650, retention_value * 30))

    return retention_value, unit, retention_days


def _set_backup_retention_policy(retention_value: int, retention_unit: str) -> dict[str, Any]:
    unit = str(retention_unit or "days").strip().lower()
    if unit not in {"days", "months"}:
        raise HTTPException(status_code=400, detail="retentionUnit must be 'days' or 'months'")

    value = int(retention_value)
    if unit == "days":
        value = max(2, min(3650, value))
        days = value
    else:
        value = max(1, min(120, value))
        days = max(2, min(3650, value * 30))

    _setting_set("backup_retention_value", str(value))
    _setting_set("backup_retention_unit", unit)

    return {
        "retentionValue": value,
        "retentionUnit": unit,
        "retentionDays": days,
    }


def _list_backups() -> list[dict[str, Any]]:
    backups = sorted(BACKUP_DIR.glob("sync-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    items: list[dict[str, Any]] = []
    for backup in backups:
        try:
            stats = backup.stat()
        except OSError:
            continue

        stem = backup.stem
        reason = "unknown"
        created_at = int(stats.st_mtime * 1000)
        parts = stem.split("-")
        if len(parts) >= 4 and parts[0] == "sync":
            date_part = parts[1]
            time_part = parts[2]
            reason = "-".join(parts[3:])
            try:
                parsed = dt.datetime.strptime(f"{date_part}-{time_part}", "%Y%m%d-%H%M%S")
                created_at = int(parsed.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
            except Exception:
                created_at = int(stats.st_mtime * 1000)

        items.append(
            {
                "name": backup.name,
                "sizeBytes": int(stats.st_size),
                "modifiedAt": int(stats.st_mtime * 1000),
                "createdAt": int(created_at),
                "reason": reason,
            }
        )
    return items


def _safe_backup_path_from_name(backup_name: str) -> Path:
    name = str(backup_name or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid backup name")
    if not name.endswith(".db") or not name.startswith("sync-"):
        raise HTTPException(status_code=400, detail="Invalid backup file name")

    backup_path = (BACKUP_DIR / name).resolve()
    if BACKUP_DIR not in backup_path.parents and backup_path != BACKUP_DIR:
        raise HTTPException(status_code=400, detail="Invalid backup path")
    if not backup_path.exists() or not backup_path.is_file():
        raise HTTPException(status_code=404, detail="Backup file not found")
    return backup_path


def _compute_sha256_for_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _dry_run_backup_restore(backup_path: Path) -> dict[str, Any]:
    try:
        stats = backup_path.stat()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Backup stat failed: {exc}")

    header_ok = False
    header_text = ""
    try:
        with backup_path.open("rb") as handle:
            header = handle.read(16)
        header_text = header.decode("ascii", errors="ignore")
        header_ok = header_text.startswith("SQLite format 3")
    except Exception:
        header_ok = False

    quick_check_ok = False
    quick_check_result = ""
    quick_check_error = ""
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
        row = conn.execute("PRAGMA quick_check").fetchone()
        quick_check_result = str(row[0] if row and row[0] is not None else "")
        quick_check_ok = quick_check_result.lower() == "ok"
    except Exception as exc:
        quick_check_error = str(exc)
        quick_check_ok = False
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass

    return {
        "ok": bool(header_ok and quick_check_ok),
        "backupName": backup_path.name,
        "sizeBytes": int(stats.st_size),
        "modifiedAt": int(stats.st_mtime * 1000),
        "sha256": _compute_sha256_for_file(backup_path),
        "header": header_text,
        "headerValid": header_ok,
        "quickCheck": quick_check_result,
        "quickCheckOk": quick_check_ok,
        "quickCheckError": quick_check_error,
    }


def _restore_db_from_backup(backup_path: Path) -> dict[str, Any]:
    global _CONN

    pre_restore_snapshot = Path(_backup_db("pre-restore"))

    with _db_lock:
        try:
            _CONN.commit()
        except Exception:
            pass
        try:
            _CONN.close()
        except Exception:
            pass

        shutil.copy2(backup_path, DB_PATH)
        _CONN = _connect()

    _init_db()
    _cleanup_old_backups()

    return {
        "ok": True,
        "restoredFrom": backup_path.name,
        "preRestoreSnapshot": pre_restore_snapshot.name,
    }


def _cleanup_old_backups() -> None:
    _, _, retention_days = _get_backup_retention_policy()
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=retention_days)
    for backup in BACKUP_DIR.glob("sync-*.db"):
        try:
            modified = dt.datetime.utcfromtimestamp(backup.stat().st_mtime)
            if modified < cutoff:
                backup.unlink()
        except OSError:
            pass


def _ensure_daily_backup() -> None:
    today = dt.datetime.utcnow().strftime("%Y%m%d")
    if not any(BACKUP_DIR.glob(f"sync-{today}-*.db")):
        _backup_db("daily")
    _cleanup_old_backups()


def _backup_worker() -> None:
    while not _stop_event.is_set():
        try:
            _ensure_daily_backup()
        except Exception:
            pass
        _stop_event.wait(3600)


def _auth_from_header(authorization: str | None = Header(default=None)) -> SessionContext:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return _get_session_by_token(token)


def _admin_only(session: SessionContext = Depends(_auth_from_header)) -> SessionContext:
    if not session.is_admin:
        raise HTTPException(status_code=403, detail="Admin role required")
    return session


# -------------------------
# Web UI
# -------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/public/info")
def public_info() -> dict[str, Any]:
    row = _CONN.execute("SELECT COUNT(*) AS c FROM projects").fetchone()
    has_projects = bool(row and int(row["c"]) > 0)
    return {
        "ok": True,
        "hasProjects": has_projects,
        "requireProjectPassword": REQUIRE_PROJECT_PASSWORD,
        "s3ImagesEnabled": _is_s3_enabled(),
        "s3Bucket": S3_BUCKET,
        "s3Prefix": S3_PREFIX,
        "cloudfrontBaseUrl": CLOUDFRONT_BASE_URL,
        "signedUrlTtlSeconds": SIGNED_URL_TTL_SECONDS,
        "sessionTtlSeconds": SESSION_TTL_SECONDS,
        "backupDir": str(BACKUP_DIR),
        "backupRetentionDays": BACKUP_RETENTION_DAYS,
    }


# -------------------------
# Auth + admin
# -------------------------

@app.post("/api/admin/bootstrap")
def bootstrap(
    payload: BootstrapPayload,
    x_bootstrap_token: str | None = Header(default=None),
) -> dict[str, Any]:
    if BOOTSTRAP_TOKEN:
        provided = (x_bootstrap_token or "").strip()
        if not provided or not hmac.compare_digest(provided, BOOTSTRAP_TOKEN):
            raise HTTPException(status_code=401, detail="Invalid bootstrap token")

    project_id = payload.projectId.strip()
    username = payload.username.strip()
    if not project_id or not username:
        raise HTTPException(status_code=400, detail="projectId and username are required")

    with _db_lock:
        existing = _CONN.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if existing is not None:
            raise HTTPException(status_code=409, detail="Project already exists")

        _CONN.execute(
            "INSERT INTO projects (id, password_hash, created_at) VALUES (?, ?, ?)",
            (project_id, _hash_password(payload.projectPassword), _utc_now_iso()),
        )
        _CONN.execute(
            "INSERT INTO users (project_id, username, password_hash, is_admin, created_at) VALUES (?, ?, ?, 1, ?)",
            (project_id, username, _hash_password(payload.password), _utc_now_iso()),
        )
        _CONN.execute(
            "UPDATE users SET role = 'owner', created_by = NULL WHERE project_id = ? AND username = ?",
            (project_id, username),
        )
        _CONN.commit()

    return {"ok": True, "projectId": project_id, "admin": username}


@app.post("/api/auth/login")
def login(payload: LoginPayload) -> dict[str, Any]:
    project_id = payload.projectId.strip()
    username = payload.username.strip()

    with _db_lock:
        project = _CONN.execute(
            "SELECT password_hash FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if project is None:
            raise HTTPException(status_code=401, detail="Invalid project credentials")

        provided_project_password = str(payload.projectPassword or "")
        if REQUIRE_PROJECT_PASSWORD and not provided_project_password:
            raise HTTPException(status_code=401, detail="Project password is required")

        if provided_project_password and not _verify_password(provided_project_password, str(project["password_hash"])):
            raise HTTPException(status_code=401, detail="Invalid project credentials")

        user = _CONN.execute(
            "SELECT username, password_hash, is_admin, role FROM users WHERE project_id = ? AND username = ?",
            (project_id, username),
        ).fetchone()
        if user is None or not _verify_password(payload.password, str(user["password_hash"])):
            raise HTTPException(status_code=401, detail="Invalid user credentials")

        token = secrets.token_urlsafe(32)
        now = _now_ms()
        _CONN.execute(
            "INSERT INTO sessions (token, project_id, username, role, is_admin, active_file, created_at, last_seen) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
            (token, project_id, username, str(user["role"] or "user"), int(user["is_admin"]), now, now),
        )
        _CONN.commit()

    return {
        "ok": True,
        "token": token,
        "projectId": project_id,
        "username": username,
        "role": str(user["role"] or "user"),
        "isAdmin": bool(user["is_admin"]),
        "sessionTtlSeconds": SESSION_TTL_SECONDS,
    }


@app.post("/api/auth/project-options")
def auth_project_options(payload: ProjectOptionsPayload) -> dict[str, Any]:
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="username is required")

    rows = _CONN.execute(
        "SELECT project_id, username, password_hash, is_admin, role FROM users WHERE username = ? ORDER BY project_id ASC",
        (username,),
    ).fetchall()

    projects: list[dict[str, Any]] = []
    for row in rows:
        if not _verify_password(payload.password, str(row["password_hash"])):
            continue
        projects.append(
            {
                "projectId": str(row["project_id"]),
                "username": str(row["username"]),
                "isAdmin": bool(row["is_admin"]),
                "role": str(row["role"] or "user"),
            }
        )

    if not projects:
        raise HTTPException(status_code=401, detail="Invalid user credentials")

    return {
        "ok": True,
        "requireProjectPassword": REQUIRE_PROJECT_PASSWORD,
        "projects": projects,
    }


@app.post("/api/auth/logout")
def logout(session: SessionContext = Depends(_auth_from_header)) -> dict[str, Any]:
    with _db_lock:
        _CONN.execute("DELETE FROM locks WHERE token = ?", (session.token,))
        _CONN.execute("DELETE FROM sessions WHERE token = ?", (session.token,))
        _CONN.commit()
    return {"ok": True}


@app.post("/api/auth/heartbeat")
def heartbeat(session: SessionContext = Depends(_auth_from_header)) -> dict[str, Any]:
    return {
        "ok": True,
        "projectId": session.project_id,
        "username": session.username,
        "activeFile": session.active_file,
        "sessionTtlSeconds": SESSION_TTL_SECONDS,
        "serverTime": _utc_now_iso(),
    }


@app.get("/api/admin/users")
def list_users(session: SessionContext = Depends(_admin_only)) -> dict[str, Any]:
    rows = _CONN.execute(
        "SELECT username, is_admin, role, created_by, created_at FROM users WHERE project_id = ? ORDER BY username ASC",
        (session.project_id,),
    ).fetchall()
    users = [
        {
            "username": str(row["username"]),
            "isAdmin": bool(row["is_admin"]),
            "role": str(row["role"] or "user"),
            "createdBy": str(row["created_by"] or ""),
            "canDelete": _session_can_delete_user(session, str(row["username"])),
            "canResetPassword": _session_can_reset_user_password(session, str(row["username"])),
            "canChangeRole": bool(
                session.role == "owner"
                and str(row["username"]) != session.username
                and str(row["role"] or "user") != "owner"
            ),
            "createdAt": str(row["created_at"]),
        }
        for row in rows
    ]
    return {"ok": True, "users": users, "me": session.username, "myRole": session.role}


@app.post("/api/admin/users")
def create_user(
    payload: CreateUserPayload,
    session: SessionContext = Depends(_admin_only),
) -> dict[str, Any]:
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="username is required")

    target_role = "admin" if payload.isAdmin else "user"
    if target_role == "admin" and session.role != "owner":
        raise HTTPException(status_code=403, detail="Only owners can create admin users")

    with _db_lock:
        exists = _CONN.execute(
            "SELECT username FROM users WHERE project_id = ? AND username = ?",
            (session.project_id, username),
        ).fetchone()
        if exists is not None:
            raise HTTPException(status_code=409, detail="User already exists")

        _CONN.execute(
            "INSERT INTO users (project_id, username, password_hash, is_admin, role, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                session.project_id,
                username,
                _hash_password(payload.password),
                1 if target_role in {"owner", "admin"} else 0,
                target_role,
                session.username,
                _utc_now_iso(),
            ),
        )
        _CONN.commit()

    return {"ok": True, "username": username, "role": target_role, "createdBy": session.username}


@app.post("/api/admin/project/password")
def set_project_password(
    payload: ProjectPasswordUpdatePayload,
    session: SessionContext = Depends(_admin_only),
) -> dict[str, Any]:
    if session.role != "owner":
        raise HTTPException(status_code=403, detail="Only owners can change project password")

    with _db_lock:
        _CONN.execute(
            "UPDATE projects SET password_hash = ? WHERE id = ?",
            (_hash_password(payload.newProjectPassword), session.project_id),
        )
        _CONN.commit()

    return {"ok": True, "projectId": session.project_id, "updatedBy": session.username}


@app.patch("/api/admin/users/{target_username}")
def update_user_admin(
    target_username: str,
    payload: AdminUserUpdatePayload,
    session: SessionContext = Depends(_admin_only),
) -> dict[str, Any]:
    target = target_username.strip()
    if not target:
        raise HTTPException(status_code=400, detail="username is required")

    role_value = str(payload.role or "").strip().lower()
    new_role = role_value if role_value in {"admin", "user"} else ""
    new_password = str(payload.newPassword or "")

    if not new_role and not new_password:
        raise HTTPException(status_code=400, detail="Provide role and/or newPassword")

    with _db_lock:
        row = _CONN.execute(
            "SELECT username, role FROM users WHERE project_id = ? AND username = ?",
            (session.project_id, target),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")

        current_role = str(row["role"] or "user")

        if new_role:
            if session.role != "owner":
                raise HTTPException(status_code=403, detail="Only owners can change user roles")
            if current_role == "owner":
                raise HTTPException(status_code=400, detail="Owner role cannot be changed via this endpoint")
            if target == session.username:
                raise HTTPException(status_code=400, detail="Owner cannot change their own role")

        if new_password and not _session_can_reset_user_password(session, target):
            raise HTTPException(status_code=403, detail="Not allowed to reset this user's password")

        updated_role = current_role
        if new_role:
            _CONN.execute(
                "UPDATE users SET role = ?, is_admin = ? WHERE project_id = ? AND username = ?",
                (new_role, 1 if new_role in {"owner", "admin"} else 0, session.project_id, target),
            )
            _CONN.execute(
                "UPDATE sessions SET role = ?, is_admin = ? WHERE project_id = ? AND username = ?",
                (new_role, 1 if new_role in {"owner", "admin"} else 0, session.project_id, target),
            )
            updated_role = new_role

        if new_password:
            _CONN.execute(
                "UPDATE users SET password_hash = ? WHERE project_id = ? AND username = ?",
                (_hash_password(new_password), session.project_id, target),
            )
            # Invalidate existing sessions so new password takes effect immediately.
            target_tokens = _CONN.execute(
                "SELECT token FROM sessions WHERE project_id = ? AND username = ?",
                (session.project_id, target),
            ).fetchall()
            for token_row in target_tokens:
                token = str(token_row["token"])
                _CONN.execute("DELETE FROM locks WHERE token = ?", (token,))
            _CONN.execute(
                "DELETE FROM sessions WHERE project_id = ? AND username = ?",
                (session.project_id, target),
            )

        _CONN.commit()

    return {
        "ok": True,
        "username": target,
        "role": updated_role,
        "passwordReset": bool(new_password),
        "updatedBy": session.username,
    }


@app.delete("/api/users/{target_username}")
def delete_user(target_username: str, session: SessionContext = Depends(_auth_from_header)) -> dict[str, Any]:
    target = target_username.strip()
    if not target:
        raise HTTPException(status_code=400, detail="username is required")

    if not _session_can_delete_user(session, target):
        raise HTTPException(status_code=403, detail="Not allowed to delete this user")

    with _db_lock:
        row = _CONN.execute(
            "SELECT username, role FROM users WHERE project_id = ? AND username = ?",
            (session.project_id, target),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")

        role = str(row["role"] or "user")
        if role == "owner":
            owners_row = _CONN.execute(
                "SELECT COUNT(*) AS c FROM users WHERE project_id = ? AND role = 'owner'",
                (session.project_id,),
            ).fetchone()
            owners_count = int(owners_row["c"] if owners_row else 0)
            if owners_count <= 1:
                raise HTTPException(status_code=400, detail="Cannot delete the last owner")

        target_sessions = _CONN.execute(
            "SELECT token FROM sessions WHERE project_id = ? AND username = ?",
            (session.project_id, target),
        ).fetchall()
        for token_row in target_sessions:
            token = str(token_row["token"])
            _CONN.execute("DELETE FROM locks WHERE token = ?", (token,))
            _CONN.execute("DELETE FROM sessions WHERE token = ?", (token,))

        _CONN.execute(
            "DELETE FROM users WHERE project_id = ? AND username = ?",
            (session.project_id, target),
        )
        _CONN.commit()

    return {"ok": True, "deleted": target}


@app.post("/api/admin/backup-now")
def backup_now(session: SessionContext = Depends(_admin_only)) -> dict[str, Any]:
    backup_path = _backup_db("manual")
    _cleanup_old_backups()
    return {"ok": True, "backupPath": backup_path}


@app.get("/api/admin/backups")
def admin_list_backups(session: SessionContext = Depends(_admin_only)) -> dict[str, Any]:
    retention_value, retention_unit, retention_days = _get_backup_retention_policy()
    items = _list_backups()
    return {
        "ok": True,
        "projectId": session.project_id,
        "backupDir": str(BACKUP_DIR),
        "retentionValue": retention_value,
        "retentionUnit": retention_unit,
        "retentionDays": retention_days,
        "count": len(items),
        "items": items,
    }


@app.post("/api/admin/backups/retention")
def admin_set_backup_retention(
    payload: BackupRetentionPayload,
    session: SessionContext = Depends(_admin_only),
) -> dict[str, Any]:
    result = _set_backup_retention_policy(
        retention_value=int(payload.retentionValue),
        retention_unit=str(payload.retentionUnit),
    )
    _cleanup_old_backups()
    return {
        "ok": True,
        "projectId": session.project_id,
        **result,
    }


@app.post("/api/admin/backups/cleanup-now")
def admin_cleanup_backups_now(session: SessionContext = Depends(_admin_only)) -> dict[str, Any]:
    before = len(_list_backups())
    _cleanup_old_backups()
    after_items = _list_backups()
    retention_value, retention_unit, retention_days = _get_backup_retention_policy()
    return {
        "ok": True,
        "projectId": session.project_id,
        "removed": max(0, before - len(after_items)),
        "remaining": len(after_items),
        "retentionValue": retention_value,
        "retentionUnit": retention_unit,
        "retentionDays": retention_days,
    }


@app.post("/api/admin/backups/restore/dry-run")
def admin_restore_backup_dry_run(
    payload: BackupDryRunPayload,
    session: SessionContext = Depends(_admin_only),
) -> dict[str, Any]:
    backup_name = str(payload.backupName or "").strip()
    backup_path = _safe_backup_path_from_name(backup_name)
    result = _dry_run_backup_restore(backup_path)
    return {
        **result,
        "projectId": session.project_id,
    }


@app.get("/api/admin/backups/{backup_name}/download")
def admin_download_backup(backup_name: str, session: SessionContext = Depends(_admin_only)) -> FileResponse:
    backup_path = _safe_backup_path_from_name(backup_name)
    return FileResponse(
        path=str(backup_path),
        media_type="application/x-sqlite3",
        filename=backup_path.name,
    )


@app.post("/api/admin/backups/restore")
def admin_restore_backup(
    payload: BackupRestorePayload,
    session: SessionContext = Depends(_admin_only),
) -> dict[str, Any]:
    backup_name = str(payload.backupName or "").strip()
    confirm_text = str(payload.confirmText or "").strip()
    expected = f"RESTORE {backup_name}"
    if confirm_text != expected:
        raise HTTPException(
            status_code=400,
            detail=f"Confirmation text mismatch. Expected: {expected}",
        )

    backup_path = _safe_backup_path_from_name(backup_name)
    result = _restore_db_from_backup(backup_path)
    return {
        **result,
        "projectId": session.project_id,
        "restoredBy": session.username,
        "restoredAt": _now_ms(),
    }


@app.get("/api/admin/database/tables")
def admin_database_tables(session: SessionContext = Depends(_admin_only)) -> dict[str, Any]:
    rows = _CONN.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name ASC
        """
    ).fetchall()
    tables = [str(row["name"]) for row in rows if row and row["name"]]
    return {
        "ok": True,
        "projectId": session.project_id,
        "dbPath": str(DB_PATH),
        "tables": tables,
    }


@app.post("/api/admin/database/table")
def admin_database_table_rows(
    payload: DatabaseTableQueryPayload,
    session: SessionContext = Depends(_admin_only),
) -> dict[str, Any]:
    table = str(payload.table or "").strip()
    if not table or not table.replace("_", "a").isalnum():
        raise HTTPException(status_code=400, detail="Invalid table name")

    exists = _CONN.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table,),
    ).fetchone()
    if exists is None:
        raise HTTPException(status_code=404, detail="Table not found")

    safe_limit = max(1, min(int(payload.limit or 100), 500))
    safe_offset = max(0, int(payload.offset or 0))

    col_rows = _CONN.execute(f'PRAGMA table_info("{table}")').fetchall()
    columns = [str(row["name"]) for row in col_rows if row and row["name"]]

    search_text = str(payload.search or "").strip()
    search_column = str(payload.searchColumn or "").strip()

    where_clause = ""
    where_params: list[Any] = []
    if search_text:
        like_value = f"%{search_text}%"
        if search_column:
            if search_column not in columns:
                raise HTTPException(status_code=400, detail="Invalid search column")
            where_clause = f' WHERE CAST("{search_column}" AS TEXT) LIKE ?'
            where_params.append(like_value)
        elif columns:
            parts = [f'CAST("{col}" AS TEXT) LIKE ?' for col in columns]
            where_clause = " WHERE " + " OR ".join(parts)
            where_params.extend([like_value] * len(columns))

    total_query = f'SELECT COUNT(*) AS c FROM "{table}"{where_clause}'
    total_row = _CONN.execute(total_query, tuple(where_params)).fetchone()
    total = int(total_row["c"] if total_row else 0)

    rows_query = f'SELECT * FROM "{table}"{where_clause} LIMIT ? OFFSET ?'
    rows = _CONN.execute(
        rows_query,
        tuple([*where_params, safe_limit, safe_offset]),
    ).fetchall()

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

    return {
        "ok": True,
        "projectId": session.project_id,
        "table": table,
        "columns": columns,
        "limit": safe_limit,
        "offset": safe_offset,
        "search": search_text,
        "searchColumn": search_column,
        "total": total,
        "rows": items,
    }


@app.get("/api/project/summary")
def project_summary(session: SessionContext = Depends(_auth_from_header)) -> dict[str, Any]:
    users_row = _CONN.execute(
        "SELECT COUNT(*) AS c FROM users WHERE project_id = ?",
        (session.project_id,),
    ).fetchone()
    files_row = _CONN.execute(
        "SELECT COUNT(*) AS c FROM files WHERE project_id = ? AND deleted = 0",
        (session.project_id,),
    ).fetchone()
    changes_row = _CONN.execute(
        "SELECT COUNT(*) AS c FROM changes WHERE project_id = ?",
        (session.project_id,),
    ).fetchone()
    latest_change = _CONN.execute(
        "SELECT path, username, created_at FROM changes WHERE project_id = ? ORDER BY seq DESC LIMIT 1",
        (session.project_id,),
    ).fetchone()

    return {
        "ok": True,
        "projectId": session.project_id,
        "role": session.role,
        "storageMode": _get_project_storage_mode(session.project_id),
        "imageAccessMode": _get_project_image_access_mode(session.project_id),
        "usesS3Images": _project_uses_s3_images(session.project_id),
        "requireProjectPassword": REQUIRE_PROJECT_PASSWORD,
        "totals": {
            "users": int(users_row["c"] if users_row else 0),
            "files": int(files_row["c"] if files_row else 0),
            "changes": int(changes_row["c"] if changes_row else 0),
        },
        "latestChange": {
            "path": str(latest_change["path"]),
            "username": str(latest_change["username"]),
            "createdAt": int(latest_change["created_at"]),
        }
        if latest_change
        else None,
    }


@app.get("/api/project/recent-changes")
def project_recent_changes(
    limit: int = 30,
    session: SessionContext = Depends(_auth_from_header),
) -> dict[str, Any]:
    safe_limit = max(5, min(120, int(limit)))
    rows = _CONN.execute(
        """
        SELECT seq, username, path, deleted, mtime_ms, created_at
        FROM changes
        WHERE project_id = ?
        ORDER BY seq DESC
        LIMIT ?
        """,
        (session.project_id, safe_limit),
    ).fetchall()

    items = [
        {
            "seq": int(row["seq"]),
            "username": str(row["username"]),
            "path": str(row["path"]),
            "deleted": bool(row["deleted"]),
            "mtimeMs": int(row["mtime_ms"]),
            "createdAt": int(row["created_at"]),
        }
        for row in rows
    ]
    return {"ok": True, "changes": items}


@app.get("/api/admin/project/storage")
def get_project_storage(session: SessionContext = Depends(_admin_only)) -> dict[str, Any]:
    mode = _get_project_storage_mode(session.project_id)
    return {
        "ok": True,
        "projectId": session.project_id,
        "role": session.role,
        "storageMode": mode,
        "imageAccessMode": _get_project_image_access_mode(session.project_id),
        "usesS3Images": _project_uses_s3_images(session.project_id),
        "s3Enabled": _is_s3_enabled(),
    }


@app.post("/api/admin/project/storage")
def set_project_storage(
    payload: ProjectStoragePayload,
    session: SessionContext = Depends(_admin_only),
) -> dict[str, Any]:
    mode = str(payload.storageMode).strip().lower()
    if mode not in {"auto", "db", "s3"}:
        raise HTTPException(status_code=400, detail="Invalid storageMode")
    if mode == "s3" and not _is_s3_enabled():
        raise HTTPException(status_code=400, detail="S3 storage requested but backend S3 is not configured")

    with _db_lock:
        _CONN.execute(
            "UPDATE projects SET storage_mode = ? WHERE id = ?",
            (mode, session.project_id),
        )
        _CONN.commit()

    return {
        "ok": True,
        "projectId": session.project_id,
        "storageMode": mode,
        "imageAccessMode": _get_project_image_access_mode(session.project_id),
        "usesS3Images": _project_uses_s3_images(session.project_id),
    }


@app.get("/api/admin/project/image-access")
def get_project_image_access(session: SessionContext = Depends(_admin_only)) -> dict[str, Any]:
    mode = _get_project_image_access_mode(session.project_id)
    return {
        "ok": True,
        "projectId": session.project_id,
        "imageAccessMode": mode,
        "s3Enabled": _is_s3_enabled(),
        "signedUrlTtlSeconds": SIGNED_URL_TTL_SECONDS,
        "prefetchMaxBatch": PREFETCH_MAX_BATCH,
    }


@app.post("/api/admin/project/image-access")
def set_project_image_access(
    payload: ProjectImageAccessPayload,
    session: SessionContext = Depends(_admin_only),
) -> dict[str, Any]:
    mode = str(payload.imageAccessMode).strip().lower()
    if mode not in {"local", "hybrid", "cloud_only"}:
        raise HTTPException(status_code=400, detail="Invalid imageAccessMode")
    if mode in {"hybrid", "cloud_only"} and not _is_s3_enabled():
        raise HTTPException(status_code=400, detail="Cloud image modes require backend S3 configuration")

    with _db_lock:
        _CONN.execute(
            "UPDATE projects SET image_access_mode = ? WHERE id = ?",
            (mode, session.project_id),
        )
        _CONN.commit()

    return {
        "ok": True,
        "projectId": session.project_id,
        "imageAccessMode": mode,
        "usesS3Images": _project_uses_s3_images(session.project_id),
    }


@app.get("/api/images/manifest")
def list_image_manifest(session: SessionContext = Depends(_auth_from_header)) -> dict[str, Any]:
    _ensure_s3_image_mode(session)
    try:
        items = _s3_list_project_images(session.project_id)
    except (BotoCoreError, ClientError, Exception) as exc:
        raise HTTPException(status_code=502, detail=f"Unable to list S3 image manifest: {exc}") from exc

    if items:
        sha1_by_path: dict[str, str] = {}
        try:
            rows = _CONN.execute(
                "SELECT path, sha1 FROM files WHERE project_id = ? AND deleted = 0",
                (session.project_id,),
            ).fetchall()
            sha1_by_path = {
                str(row["path"]): str(row["sha1"] or "")
                for row in rows
                if row["path"] is not None
            }
        except sqlite3.Error:
            # Keep manifest available even if local metadata lookup fails.
            sha1_by_path = {}

        for item in items:
            item["sha1"] = sha1_by_path.get(str(item["path"]), "")

    return {
        "ok": True,
        "projectId": session.project_id,
        "imageAccessMode": _get_project_image_access_mode(session.project_id),
        "manifest": items,
        "count": len(items),
    }


@app.get("/api/images/signed-read")
def get_signed_image_read_url(path: str, session: SessionContext = Depends(_auth_from_header)) -> dict[str, Any]:
    _ensure_s3_image_mode(session)
    normalized = _normalize_path(path)
    if not _is_image_path(normalized):
        raise HTTPException(status_code=400, detail="Path must reference an image file")

    try:
        url = _image_read_url(session.project_id, normalized, expires_seconds=SIGNED_URL_TTL_SECONDS)
    except (BotoCoreError, ClientError, Exception) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create signed URL: {exc}") from exc

    return {
        "ok": True,
        "path": normalized,
        "method": "GET",
        "expiresIn": SIGNED_URL_TTL_SECONDS,
        "url": url,
    }


@app.post("/api/images/signed-write")
def get_signed_image_write_url(
    payload: SignedWritePayload,
    session: SessionContext = Depends(_auth_from_header),
) -> dict[str, Any]:
    _ensure_s3_image_mode(session)
    normalized = _normalize_path(payload.path)
    if not _is_image_path(normalized):
        raise HTTPException(status_code=400, detail="Path must reference an image file")

    try:
        url = _s3_signed_put_url(
            session.project_id,
            normalized,
            content_type=payload.contentType,
            expires_seconds=SIGNED_URL_TTL_SECONDS,
        )
    except (BotoCoreError, ClientError, Exception) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create signed URL: {exc}") from exc

    return {
        "ok": True,
        "path": normalized,
        "method": "PUT",
        "expiresIn": SIGNED_URL_TTL_SECONDS,
        "url": url,
    }


@app.post("/api/images/commit-upload")
def commit_signed_image_upload(
    payload: SignedUploadCommitPayload,
    session: SessionContext = Depends(_auth_from_header),
) -> dict[str, Any]:
    _ensure_s3_image_mode(session)
    normalized = _normalize_path(payload.path)
    if not _is_image_path(normalized):
        raise HTTPException(status_code=400, detail="Path must reference an image file")

    if not _s3_image_exists(session.project_id, normalized):
        raise HTTPException(status_code=404, detail="Uploaded image was not found in S3")

    sha1 = str(payload.sha1 or "").strip().lower()
    if sha1 and not all(ch in "0123456789abcdef" for ch in sha1):
        raise HTTPException(status_code=400, detail="Invalid sha1 format")

    now = _now_ms()
    mtime_ms = int(payload.mtimeMs or now)
    if mtime_ms <= 0:
        mtime_ms = now

    with _db_lock:
        _CONN.execute(
            """
            INSERT INTO files (project_id, path, deleted, mtime_ms, sha1, content_base64, updated_at)
            VALUES (?, ?, 0, ?, ?, '', ?)
            ON CONFLICT(project_id, path) DO UPDATE SET
              deleted=0,
              mtime_ms=excluded.mtime_ms,
              sha1=excluded.sha1,
              content_base64='',
              updated_at=excluded.updated_at
            """,
            (session.project_id, normalized, mtime_ms, sha1, now),
        )
        _CONN.execute(
            "INSERT INTO changes (project_id, username, source_token, path, deleted, mtime_ms, sha1, content_base64, created_at) VALUES (?, ?, ?, ?, 0, ?, ?, '', ?)",
            (session.project_id, session.username, session.token, normalized, mtime_ms, sha1, now),
        )
        _CONN.commit()

    return {
        "ok": True,
        "projectId": session.project_id,
        "path": normalized,
        "sha1": sha1,
        "sizeBytes": int(payload.sizeBytes or 0),
        "mtimeMs": mtime_ms,
        "committedAt": now,
    }


@app.post("/api/images/prefetch")
def request_prefetch_batch(
    payload: PrefetchBatchPayload,
    session: SessionContext = Depends(_auth_from_header),
) -> dict[str, Any]:
    _ensure_s3_image_mode(session)
    manifest = _s3_list_project_images(session.project_id)
    if not manifest:
        return {"ok": True, "items": [], "count": 0}

    requested_count = max(1, min(int(payload.count), PREFETCH_MAX_BATCH))
    start_index = 0
    current = str(payload.currentPath or "").strip()
    if current:
        normalized_current = _normalize_path(current)
        for idx, item in enumerate(manifest):
            if str(item["path"]) == normalized_current:
                start_index = idx + 1
                break

    window = manifest[start_index:start_index + requested_count]
    items: list[dict[str, Any]] = []
    for item in window:
        path = str(item["path"])
        try:
            url = _image_read_url(session.project_id, path, expires_seconds=SIGNED_URL_TTL_SECONDS)
        except (BotoCoreError, ClientError, Exception):
            continue
        items.append(
            {
                **item,
                "url": url,
                "expiresIn": SIGNED_URL_TTL_SECONDS,
            }
        )

    return {
        "ok": True,
        "projectId": session.project_id,
        "startIndex": start_index,
        "count": len(items),
        "items": items,
    }


@app.get("/api/image-status")
def get_image_status_map(session: SessionContext = Depends(_auth_from_header)) -> dict[str, Any]:
    rows = _CONN.execute(
        "SELECT image_name, status, updated_at, updated_by FROM image_status WHERE project_id = ? ORDER BY image_name ASC",
        (session.project_id,),
    ).fetchall()

    statuses: dict[str, str] = {}
    meta: list[dict[str, Any]] = []
    for row in rows:
        image_name = str(row["image_name"] or "").strip()
        status = str(row["status"] or "").strip().lower()
        if not image_name or status not in {"in_progress", "completed", "yolo", "to_rotate"}:
            continue
        statuses[image_name] = status
        meta.append(
            {
                "imageName": image_name,
                "status": status,
                "updatedAt": int(row["updated_at"] or 0),
                "updatedBy": str(row["updated_by"] or ""),
            }
        )

    return {
        "ok": True,
        "projectId": session.project_id,
        "count": len(statuses),
        "statuses": statuses,
        "items": meta,
    }


@app.post("/api/image-status")
def upsert_image_status(
    payload: ImageStatusPayload,
    session: SessionContext = Depends(_auth_from_header),
) -> dict[str, Any]:
    image_name = str(payload.imageName or "").strip()
    status = str(payload.status or "").strip().lower()
    if not image_name:
        raise HTTPException(status_code=400, detail="imageName is required")
    if "/" in image_name or "\\" in image_name:
        raise HTTPException(status_code=400, detail="imageName must be a basename")
    if status not in {"in_progress", "completed", "yolo", "to_rotate"}:
        raise HTTPException(status_code=400, detail="Invalid status")

    with _db_lock:
        now = _now_ms()
        _CONN.execute(
            """
            INSERT INTO image_status (project_id, image_name, status, updated_at, updated_by)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(project_id, image_name) DO UPDATE SET
              status=excluded.status,
              updated_at=excluded.updated_at,
              updated_by=excluded.updated_by
            """,
            (session.project_id, image_name, status, now, session.username),
        )
        _CONN.commit()

    return {
        "ok": True,
        "projectId": session.project_id,
        "imageName": image_name,
        "status": status,
        "updatedAt": _now_ms(),
        "updatedBy": session.username,
    }


@app.post("/api/admin/image-status/sync-all")
def admin_sync_all_image_statuses(
    payload: AdminImageStatusSyncPayload,
    session: SessionContext = Depends(_admin_only),
) -> dict[str, Any]:
    items = payload.items or []
    if not items:
        return {
            "ok": True,
            "projectId": session.project_id,
            "received": 0,
            "upserted": 0,
            "skipped": 0,
        }

    now = _now_ms()
    upserted = 0
    skipped = 0
    seen: set[str] = set()

    with _db_lock:
        for entry in items:
            image_name = str(entry.imageName or "").strip()
            status = str(entry.status or "").strip().lower()

            if not image_name or "/" in image_name or "\\" in image_name:
                skipped += 1
                continue
            if status not in {"in_progress", "completed", "yolo", "to_rotate"}:
                skipped += 1
                continue

            key = image_name.lower()
            if key in seen:
                skipped += 1
                continue
            seen.add(key)

            _CONN.execute(
                """
                INSERT INTO image_status (project_id, image_name, status, updated_at, updated_by)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id, image_name) DO UPDATE SET
                  status=excluded.status,
                  updated_at=excluded.updated_at,
                  updated_by=excluded.updated_by
                """,
                (session.project_id, image_name, status, now, session.username),
            )
            upserted += 1

        _CONN.commit()

    return {
        "ok": True,
        "projectId": session.project_id,
        "received": len(items),
        "upserted": upserted,
        "skipped": skipped,
        "updatedBy": session.username,
        "updatedAt": now,
    }


@app.post("/api/admin/images/upload")
async def admin_upload_image(
    file: UploadFile = File(...),
    path: str = Form(""),
    expected_project_id: str = Form(""),
    overwrite: str = Form("0"),
    session: SessionContext = Depends(_auth_from_header),
) -> dict[str, Any]:
    expected = str(expected_project_id or "").strip()
    if expected and expected != session.project_id:
        raise HTTPException(status_code=409, detail="Active project changed. Refresh and retry upload.")

    requested_path = str(path or "").strip()
    candidate_path = requested_path or str(file.filename or "").strip()
    normalized = _normalize_path(candidate_path)
    if not _is_image_path(normalized):
        raise HTTPException(status_code=400, detail="Only image files are accepted")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(status_code=400, detail=f"File exceeds max size {MAX_FILE_BYTES}")

    allow_overwrite = str(overwrite).strip().lower() in {"1", "true", "yes", "on"}
    use_s3_for_image = _project_uses_s3_images(session.project_id)

    if use_s3_for_image:
        exists = _s3_image_exists(session.project_id, normalized)
    else:
        exists = _db_file_exists(session.project_id, normalized)

    if exists and not allow_overwrite:
        return {"ok": True, "path": normalized, "uploaded": False, "skipped": True, "reason": "exists"}

    sha1 = hashlib.sha1(raw).hexdigest()
    content_b64 = ""
    if use_s3_for_image:
        try:
            _s3_put_image(session.project_id, normalized, raw)
        except (BotoCoreError, ClientError, Exception) as exc:
            raise HTTPException(status_code=500, detail=f"S3 upload failed: {exc}") from exc
    else:
        content_b64 = base64.b64encode(raw).decode("ascii")

    with _db_lock:
        now = _now_ms()
        _CONN.execute(
            """
            INSERT INTO files (project_id, path, deleted, mtime_ms, sha1, content_base64, updated_at)
            VALUES (?, ?, 0, ?, ?, ?, ?)
            ON CONFLICT(project_id, path) DO UPDATE SET
              deleted=0,
              mtime_ms=excluded.mtime_ms,
              sha1=excluded.sha1,
              content_base64=excluded.content_base64,
              updated_at=excluded.updated_at
            """,
            (session.project_id, normalized, now, sha1, content_b64, now),
        )
        _CONN.execute(
            "INSERT INTO changes (project_id, username, source_token, path, deleted, mtime_ms, sha1, content_base64, created_at) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)",
            (session.project_id, session.username, session.token, normalized, now, sha1, content_b64, now),
        )
        _CONN.commit()

    return {
        "ok": True,
        "path": normalized,
        "uploaded": True,
        "skipped": False,
        "bytes": len(raw),
        "sha1": sha1,
        "overwrote": bool(exists and allow_overwrite),
    }


@app.post("/api/admin/images/upload-zip")
async def admin_upload_zip(
    archive: UploadFile = File(...),
    target_prefix: str = Form(""),
    expected_project_id: str = Form(""),
    overwrite: str = Form("0"),
    session: SessionContext = Depends(_auth_from_header),
) -> dict[str, Any]:
    expected = str(expected_project_id or "").strip()
    if expected and expected != session.project_id:
        raise HTTPException(status_code=409, detail="Active project changed. Refresh and retry upload.")

    filename = str(archive.filename or "").strip()
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip archives are accepted")

    prefix = str(target_prefix or "").strip()
    if prefix:
        prefix = _normalize_path(prefix)

    allow_overwrite = str(overwrite).strip().lower() in {"1", "true", "yes", "on"}
    use_s3_for_image = _project_uses_s3_images(session.project_id)

    tmp_path = ""
    total_read = 0
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            tmp_path = tmp.name
            while True:
                chunk = await archive.read(1024 * 1024)
                if not chunk:
                    break
                total_read += len(chunk)
                if total_read > MAX_ZIP_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Archive exceeds max size {MAX_ZIP_UPLOAD_BYTES}",
                    )
                tmp.write(chunk)

        uploaded = 0
        skipped_existing = 0
        overwritten = 0
        failed = 0
        ignored_non_images = 0
        ignored_oversize = 0
        failed_paths: list[dict[str, str]] = []

        with zipfile.ZipFile(tmp_path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue

                try:
                    relative = _sanitize_archive_member_path(info.filename)
                    target_path = _normalize_path(f"{prefix}/{relative}" if prefix else relative)
                except HTTPException:
                    failed += 1
                    if len(failed_paths) < 50:
                        failed_paths.append({"path": str(info.filename), "reason": "invalid-path"})
                    continue

                if not _is_image_path(target_path):
                    ignored_non_images += 1
                    continue

                if info.file_size > MAX_FILE_BYTES:
                    ignored_oversize += 1
                    continue

                try:
                    raw = zf.read(info)
                except Exception:
                    failed += 1
                    if len(failed_paths) < 50:
                        failed_paths.append({"path": target_path, "reason": "zip-read-failed"})
                    continue

                if not raw:
                    failed += 1
                    if len(failed_paths) < 50:
                        failed_paths.append({"path": target_path, "reason": "empty-file"})
                    continue

                if len(raw) > MAX_FILE_BYTES:
                    ignored_oversize += 1
                    continue

                exists = _s3_image_exists(session.project_id, target_path) if use_s3_for_image else _db_file_exists(session.project_id, target_path)
                if exists and not allow_overwrite:
                    skipped_existing += 1
                    continue

                sha1 = hashlib.sha1(raw).hexdigest()
                content_b64 = ""

                try:
                    if use_s3_for_image:
                        _s3_put_image(session.project_id, target_path, raw)
                    else:
                        content_b64 = base64.b64encode(raw).decode("ascii")
                except Exception:
                    failed += 1
                    if len(failed_paths) < 50:
                        failed_paths.append({"path": target_path, "reason": "storage-write-failed"})
                    continue

                with _db_lock:
                    now = _now_ms()
                    _CONN.execute(
                        """
                        INSERT INTO files (project_id, path, deleted, mtime_ms, sha1, content_base64, updated_at)
                        VALUES (?, ?, 0, ?, ?, ?, ?)
                        ON CONFLICT(project_id, path) DO UPDATE SET
                          deleted=0,
                          mtime_ms=excluded.mtime_ms,
                          sha1=excluded.sha1,
                          content_base64=excluded.content_base64,
                          updated_at=excluded.updated_at
                        """,
                        (session.project_id, target_path, now, sha1, content_b64, now),
                    )
                    _CONN.execute(
                        "INSERT INTO changes (project_id, username, source_token, path, deleted, mtime_ms, sha1, content_base64, created_at) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)",
                        (session.project_id, session.username, session.token, target_path, now, sha1, content_b64, now),
                    )
                    _CONN.commit()

                uploaded += 1
                if exists and allow_overwrite:
                    overwritten += 1

    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail=f"Invalid zip archive: {exc}") from exc
    finally:
        await archive.close()
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    return {
        "ok": True,
        "archive": filename,
        "projectId": session.project_id,
        "targetPrefix": prefix,
        "uploaded": uploaded,
        "skippedExisting": skipped_existing,
        "overwritten": overwritten,
        "failed": failed,
        "ignoredNonImages": ignored_non_images,
        "ignoredOversize": ignored_oversize,
        "maxFileBytes": MAX_FILE_BYTES,
        "maxZipBytes": MAX_ZIP_UPLOAD_BYTES,
        "failedItems": failed_paths,
    }


@app.get("/api/admin/images")
def admin_list_images(
    sortBy: str = "path",
    order: str = "asc",
    limit: int = 5000,
    includeS3: bool = True,
    session: SessionContext = Depends(_auth_from_header),
) -> dict[str, Any]:
    sort_key = str(sortBy or "path").strip().lower()
    sort_key = sort_key if sort_key in {"path", "size", "mtime"} else "path"
    sort_order = str(order or "asc").strip().lower()
    sort_order = sort_order if sort_order in {"asc", "desc"} else "asc"
    safe_limit = max(1, min(int(limit or 5000), 20000))

    db_rows = _collect_project_image_rows_from_db(session.project_id)
    s3_rows: dict[str, dict[str, Any]] = {}
    if includeS3 and _is_s3_enabled():
        try:
            s3_rows = _collect_project_image_rows_from_s3(session.project_id)
        except Exception:
            s3_rows = {}

    status_by_name = _fetch_project_image_status_map(session.project_id)
    label_counts_by_stem = _fetch_project_label_counts_by_stem(session.project_id)
    items: list[dict[str, Any]] = []
    all_paths = sorted(set(db_rows.keys()) | set(s3_rows.keys()), key=lambda v: v.lower())
    for path in all_paths:
        image_name = Path(path).name
        image_stem = Path(path).stem
        db_item = db_rows.get(path)
        s3_item = s3_rows.get(path)
        size_bytes = int((s3_item or {}).get("sizeBytes") or (db_item or {}).get("sizeBytes") or 0)
        modified_ms = int((s3_item or {}).get("mtimeMs") or (db_item or {}).get("mtimeMs") or 0)
        items.append(
            {
                "path": path,
                "name": image_name,
                "sizeBytes": int(size_bytes),
                "mtimeMs": int(modified_ms),
                "updatedAt": int((db_item or {}).get("updatedAt") or 0),
                "status": status_by_name.get(image_name, ""),
                "labelCount": int(label_counts_by_stem.get(image_stem, 0)),
                "indexedInDb": bool(db_item),
                "presentInS3": bool(s3_item),
            }
        )

    if sort_key == "size":
        items.sort(key=lambda v: (int(v["sizeBytes"]), str(v["path"]).lower()), reverse=(sort_order == "desc"))
    elif sort_key == "mtime":
        items.sort(key=lambda v: (int(v["mtimeMs"]), str(v["path"]).lower()), reverse=(sort_order == "desc"))
    else:
        items.sort(key=lambda v: str(v["path"]).lower(), reverse=(sort_order == "desc"))

    return {
        "ok": True,
        "projectId": session.project_id,
        "sortBy": sort_key,
        "order": sort_order,
        "includeS3": bool(includeS3),
        "count": len(items),
        "items": items[:safe_limit],
    }


@app.post("/api/admin/images/reconcile-status")
def admin_reconcile_image_status(
    payload: AdminImageStatusReconcilePayload,
    session: SessionContext = Depends(_admin_only),
) -> dict[str, Any]:
    default_status = str(payload.defaultStatus or "in_progress").strip().lower()
    if default_status not in {"in_progress", "completed", "yolo", "to_rotate"}:
        raise HTTPException(status_code=400, detail="Invalid default status")

    image_paths: set[str] = set(_collect_project_image_rows_from_db(session.project_id).keys())
    if _is_s3_enabled():
        try:
            image_paths.update(_collect_project_image_rows_from_s3(session.project_id).keys())
        except Exception:
            pass

    image_names = {Path(path).name for path in image_paths if Path(path).name}

    current_rows = _CONN.execute(
        "SELECT image_name FROM image_status WHERE project_id = ?",
        (session.project_id,),
    ).fetchall()
    current_names = {str(row["image_name"] or "").strip() for row in current_rows if row["image_name"] is not None}

    added = 0
    removed = 0
    now = _now_ms()
    with _db_lock:
        for image_name in sorted(image_names - current_names):
            _CONN.execute(
                """
                INSERT INTO image_status (project_id, image_name, status, updated_at, updated_by)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id, image_name) DO NOTHING
                """,
                (session.project_id, image_name, default_status, now, session.username),
            )
            added += 1

        if bool(payload.removeOrphans):
            orphan_names = sorted(name for name in current_names if name and name not in image_names)
            for image_name in orphan_names:
                _CONN.execute(
                    "DELETE FROM image_status WHERE project_id = ? AND image_name = ?",
                    (session.project_id, image_name),
                )
                removed += 1

        _CONN.commit()

    return {
        "ok": True,
        "projectId": session.project_id,
        "defaultStatus": default_status,
        "imageCount": len(image_names),
        "added": added,
        "removed": removed,
    }


@app.get("/api/admin/labels/summary")
def admin_label_summary(session: SessionContext = Depends(_admin_only)) -> dict[str, Any]:
    rows = _CONN.execute(
        "SELECT path, content_base64 FROM files WHERE project_id = ? AND deleted = 0 ORDER BY path ASC",
        (session.project_id,),
    ).fetchall()

    label_paths: list[str] = []
    images_with_labels: set[str] = set()
    parsed_rows = 0

    for row in rows:
        path = str(row["path"] or "")
        if not _is_label_text_path(path):
            continue
        label_paths.append(path)
        stem = Path(path).stem
        if stem:
            images_with_labels.add(stem)
        content_b64 = str(row["content_base64"] or "")
        if not content_b64:
            continue
        try:
            raw_text = base64.b64decode(content_b64.encode("ascii"), validate=False).decode("utf-8", errors="replace")
        except Exception:
            raw_text = ""
        parsed_rows += len(_parse_yolo_label_rows(raw_text))

    image_rows = _collect_project_image_rows_from_db(session.project_id)
    if _is_s3_enabled():
        try:
            image_rows.update(_collect_project_image_rows_from_s3(session.project_id))
        except Exception:
            pass

    image_stems = {Path(path).stem for path in image_rows.keys() if Path(path).stem}
    missing_labels = sum(1 for stem in image_stems if stem not in images_with_labels)

    return {
        "ok": True,
        "projectId": session.project_id,
        "totalImages": len(image_stems),
        "labelFiles": len(label_paths),
        "imagesWithLabels": len(images_with_labels),
        "imagesMissingLabels": missing_labels,
        "parsedLabelRows": parsed_rows,
        "sampleLabelPaths": label_paths[:20],
    }


@app.post("/api/admin/images/normalize-jpeg")
def admin_normalize_images_to_jpeg(
    payload: AdminNormalizeImagesPayload,
    session: SessionContext = Depends(_admin_only),
) -> dict[str, Any]:
    quality = max(60, min(int(payload.quality or 90), 95))
    paths = _resolve_normalize_image_paths(session.project_id, payload.paths)
    return _normalize_images_to_jpeg_core(
        project_id=session.project_id,
        username=session.username,
        source_token=session.token,
        quality=quality,
        paths=paths,
    )


@app.post("/api/admin/images/normalize-jpeg/start")
def admin_start_normalize_images_to_jpeg(
    payload: AdminNormalizeImagesPayload,
    session: SessionContext = Depends(_admin_only),
) -> dict[str, Any]:
    quality = max(60, min(int(payload.quality or 90), 95))
    requested_paths = list(payload.paths or [])
    job_id = secrets.token_hex(12)
    now = _now_ms()

    with _normalize_jobs_lock:
        _prune_normalize_jobs()
        _normalize_jobs[job_id] = {
            "jobId": job_id,
            "projectId": session.project_id,
            "createdBy": session.username,
            "status": "running",
            "requested": 0,
            "processed": 0,
            "converted": 0,
            "failed": 0,
            "quality": quality,
            "currentPath": "",
            "startedAt": now,
            "updatedAt": now,
            "finishedAt": 0,
            "result": None,
            "error": "",
            "cancelRequested": False,
            "cancelRequestedBy": "",
            "canceledAt": 0,
        }

    def _progress(update: dict[str, Any]) -> None:
        with _normalize_jobs_lock:
            job = _normalize_jobs.get(job_id)
            if not job:
                return
            job["processed"] = int(update.get("processed", 0) or 0)
            job["requested"] = int(update.get("total", job.get("requested", 0)) or 0)
            job["converted"] = int(update.get("converted", 0) or 0)
            job["failed"] = int(update.get("failed", 0) or 0)
            job["currentPath"] = str(update.get("currentPath") or "")
            job["updatedAt"] = _now_ms()

    def _worker() -> None:
        try:
            paths = _resolve_normalize_image_paths(session.project_id, requested_paths)
            with _normalize_jobs_lock:
                job = _normalize_jobs.get(job_id)
                if job:
                    job["requested"] = len(paths)
                    job["updatedAt"] = _now_ms()

            def _should_cancel() -> bool:
                with _normalize_jobs_lock:
                    job = _normalize_jobs.get(job_id)
                    if not job:
                        return True
                    return bool(job.get("cancelRequested", False))

            result = _normalize_images_to_jpeg_core(
                project_id=session.project_id,
                username=session.username,
                source_token=session.token,
                quality=quality,
                paths=paths,
                progress_cb=_progress,
                should_cancel_cb=_should_cancel,
            )

            if _is_s3_enabled() and int(result.get("converted", 0) or 0) > 0:
                converted_paths = [
                    _s3_object_key(session.project_id, p)
                    for p in list(result.get("convertedPaths") or [])
                    if str(p or "").strip()
                ]
                if converted_paths:
                    try:
                        result["cloudfrontInvalidation"] = _cloudfront_invalidate_keys(
                            converted_paths,
                            caller_tag=f"normalize-{job_id}",
                        )
                    except Exception as inv_exc:  # noqa: BLE001
                        result["cloudfrontInvalidation"] = {
                            "ok": False,
                            "reason": str(inv_exc),
                        }

            with _normalize_jobs_lock:
                job = _normalize_jobs.get(job_id)
                if job:
                    job["status"] = "canceled" if bool(result.get("canceled", False)) else "done"
                    job["result"] = result
                    job["processed"] = int(result.get("processed", 0) or 0)
                    job["requested"] = int(result.get("requested", 0) or 0)
                    job["converted"] = int(result.get("converted", 0) or 0)
                    job["failed"] = int(max(0, result.get("processed", 0) - result.get("converted", 0)) or 0)
                    job["finishedAt"] = _now_ms()
                    job["updatedAt"] = job["finishedAt"]
                    if job["status"] == "canceled" and not int(job.get("canceledAt") or 0):
                        job["canceledAt"] = job["finishedAt"]
        except Exception as exc:  # noqa: BLE001
            with _normalize_jobs_lock:
                job = _normalize_jobs.get(job_id)
                if job:
                    job["status"] = "error"
                    job["error"] = str(exc)
                    job["finishedAt"] = _now_ms()
                    job["updatedAt"] = job["finishedAt"]

    thread = threading.Thread(target=_worker, name=f"normalize-job-{job_id}", daemon=True)
    thread.start()

    return {
        "ok": True,
        "jobId": job_id,
        "status": "running",
        "requested": 0,
        "quality": quality,
    }


@app.get("/api/admin/images/normalize-jpeg/jobs/{job_id}")
def admin_get_normalize_images_job(
    job_id: str,
    session: SessionContext = Depends(_admin_only),
) -> dict[str, Any]:
    with _normalize_jobs_lock:
        _prune_normalize_jobs()
        job = _normalize_jobs.get(str(job_id or ""))
        if not job or str(job.get("projectId") or "") != session.project_id:
            raise HTTPException(status_code=404, detail="Normalization job not found")
        return {
            "ok": True,
            "jobId": str(job.get("jobId") or ""),
            "status": str(job.get("status") or "running"),
            "projectId": str(job.get("projectId") or ""),
            "requested": int(job.get("requested") or 0),
            "processed": int(job.get("processed") or 0),
            "converted": int(job.get("converted") or 0),
            "failed": int(job.get("failed") or 0),
            "quality": int(job.get("quality") or 90),
            "currentPath": str(job.get("currentPath") or ""),
            "error": str(job.get("error") or ""),
            "startedAt": int(job.get("startedAt") or 0),
            "updatedAt": int(job.get("updatedAt") or 0),
            "finishedAt": int(job.get("finishedAt") or 0),
            "result": job.get("result"),
            "cancelRequested": bool(job.get("cancelRequested", False)),
            "cancelRequestedBy": str(job.get("cancelRequestedBy") or ""),
            "canceledAt": int(job.get("canceledAt") or 0),
        }


@app.post("/api/admin/images/normalize-jpeg/jobs/{job_id}/cancel")
def admin_cancel_normalize_images_job(
    job_id: str,
    session: SessionContext = Depends(_admin_only),
) -> dict[str, Any]:
    with _normalize_jobs_lock:
        _prune_normalize_jobs()
        job = _normalize_jobs.get(str(job_id or ""))
        if not job or str(job.get("projectId") or "") != session.project_id:
            raise HTTPException(status_code=404, detail="Normalization job not found")

        status = str(job.get("status") or "running")
        if status in {"done", "error", "canceled"}:
            return {
                "ok": True,
                "jobId": str(job.get("jobId") or ""),
                "status": status,
                "cancelRequested": bool(job.get("cancelRequested", False)),
                "cancelRequestedBy": str(job.get("cancelRequestedBy") or ""),
                "canceledAt": int(job.get("canceledAt") or 0),
                "alreadyFinished": True,
            }

        job["cancelRequested"] = True
        job["cancelRequestedBy"] = session.username
        if not int(job.get("canceledAt") or 0):
            job["canceledAt"] = _now_ms()
        job["updatedAt"] = _now_ms()
        return {
            "ok": True,
            "jobId": str(job.get("jobId") or ""),
            "status": str(job.get("status") or "running"),
            "cancelRequested": True,
            "cancelRequestedBy": session.username,
            "canceledAt": int(job.get("canceledAt") or 0),
        }


@app.get("/api/admin/images/view")
def admin_get_image_view(
    path: str,
    session: SessionContext = Depends(_auth_from_header),
) -> dict[str, Any]:
    normalized = _normalize_path(path)
    if not _is_image_path(normalized):
        raise HTTPException(status_code=400, detail="Path must reference an image file")

    # Prefer S3 for latest object if available.
    if _is_s3_enabled() and _s3_image_exists(session.project_id, normalized):
        try:
            return {
                "ok": True,
                "path": normalized,
                "source": "s3",
                "url": _s3_signed_get_url(session.project_id, normalized, expires_seconds=SIGNED_URL_TTL_SECONDS),
                "expiresIn": SIGNED_URL_TTL_SECONDS,
            }
        except (BotoCoreError, ClientError, Exception) as exc:
            raise HTTPException(status_code=500, detail=f"Failed to resolve image URL: {exc}") from exc

    row = _CONN.execute(
        "SELECT content_base64 FROM files WHERE project_id = ? AND path = ? AND deleted = 0",
        (session.project_id, normalized),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Image not found")

    content_b64 = str(row["content_base64"] or "")
    if not content_b64:
        raise HTTPException(status_code=404, detail="Image content is unavailable for this path")

    content_type = mimetypes.guess_type(normalized)[0] or "application/octet-stream"
    return {
        "ok": True,
        "path": normalized,
        "source": "db",
        "contentType": content_type,
        "url": f"data:{content_type};base64,{content_b64}",
    }


@app.get("/api/admin/images/labels")
def admin_get_image_labels(
    path: str,
    session: SessionContext = Depends(_auth_from_header),
) -> dict[str, Any]:
    normalized = _normalize_path(path)
    if not _is_image_path(normalized):
        raise HTTPException(status_code=400, detail="Path must reference an image file")

    candidates = _label_paths_for_image(normalized)
    if not candidates:
        return {"ok": True, "path": normalized, "labels": [], "labelPath": ""}

    placeholders = ", ".join(["?"] * len(candidates))
    params: list[Any] = [session.project_id, *candidates]
    rows = _CONN.execute(
        f"SELECT path, content_base64 FROM files WHERE project_id = ? AND deleted = 0 AND path IN ({placeholders})",
        params,
    ).fetchall()

    if not rows:
        return {"ok": True, "path": normalized, "labels": [], "labelPath": ""}

    def _priority(label_path: str) -> int:
        lower = label_path.lower()
        if "/labels/obb/" in lower:
            return 0
        if "/labels/bb/" in lower:
            return 1
        return 2

    selected = sorted(
        (
            (
                str(row["path"] or ""),
                str(row["content_base64"] or ""),
            )
            for row in rows
        ),
        key=lambda item: (_priority(item[0]), item[0].lower()),
    )[0]

    label_path, content_b64 = selected
    if not content_b64:
        return {"ok": True, "path": normalized, "labels": [], "labelPath": label_path, "rawText": ""}

    try:
        raw_text = base64.b64decode(content_b64.encode("ascii"), validate=False).decode("utf-8", errors="replace")
    except Exception:
        raw_text = ""

    labels = _parse_yolo_label_rows(raw_text)
    return {
        "ok": True,
        "path": normalized,
        "labelPath": label_path,
        "count": len(labels),
        "rawText": raw_text,
        "labels": labels,
    }


@app.post("/api/admin/images/labels/write")
def admin_write_image_labels(
    payload: AdminImageLabelsWritePayload,
    session: SessionContext = Depends(_auth_from_header),
) -> dict[str, Any]:
    image_path = _normalize_path(payload.path)
    if not _is_image_path(image_path):
        raise HTTPException(status_code=400, detail="Path must reference an image file")

    normalized_text, row_count = _validate_yolo_label_text(payload.lines)
    candidates = _label_paths_for_image(image_path)
    if not candidates:
        raise HTTPException(status_code=400, detail="Could not determine label path for image")

    chosen_label_path = ""
    provided = str(payload.labelPath or "").strip()
    if provided:
        normalized_candidate = _normalize_path(provided)
        if normalized_candidate not in candidates:
            raise HTTPException(status_code=400, detail="labelPath does not match image label candidates")
        chosen_label_path = normalized_candidate
    else:
        placeholders = ", ".join(["?"] * len(candidates))
        params: list[Any] = [session.project_id, *candidates]
        rows = _CONN.execute(
            f"SELECT path FROM files WHERE project_id = ? AND deleted = 0 AND path IN ({placeholders})",
            params,
        ).fetchall()

        if rows:
            def _priority(label_path: str) -> int:
                lower = label_path.lower()
                if "/labels/obb/" in lower:
                    return 0
                if "/labels/bb/" in lower:
                    return 1
                return 2

            chosen_label_path = sorted(
                [str(row["path"] or "") for row in rows],
                key=lambda item: (_priority(item), item.lower()),
            )[0]
        else:
            chosen_label_path = candidates[0]

    now = _now_ms()
    update = UpsertFilePayload(
        path=chosen_label_path,
        deleted=False,
        mtimeMs=now,
        sha1=hashlib.sha1(normalized_text.encode("utf-8")).hexdigest(),
        contentBase64=base64.b64encode(normalized_text.encode("utf-8")).decode("ascii"),
    )
    result = sync_upsert(SyncUpsertPayload(updates=[update]), session)
    rejected = result.get("rejected") if isinstance(result, dict) else None
    if rejected:
        reason = str(rejected[0].get("reason") if isinstance(rejected[0], dict) else "Rejected by sync layer")
        status = 409 if "lock" in reason.lower() else 400
        raise HTTPException(status_code=status, detail=reason)

    return {
        "ok": True,
        "path": image_path,
        "labelPath": chosen_label_path,
        "count": row_count,
        "latestSeq": int(result.get("latestSeq", 0) if isinstance(result, dict) else 0),
    }


@app.post("/api/admin/images/delete")
def admin_delete_images(
    payload: AdminImageDeletePayload,
    session: SessionContext = Depends(_admin_only),
) -> dict[str, Any]:
    requested_paths = payload.paths or []
    if not requested_paths:
        raise HTTPException(status_code=400, detail="paths is required")

    delete_labels = bool(payload.deleteLabels)
    unique_images: list[str] = []
    seen: set[str] = set()
    for raw in requested_paths:
        path = _normalize_path(str(raw or ""))
        if not _is_image_path(path):
            raise HTTPException(status_code=400, detail=f"Not an image path: {path}")
        if path in seen:
            continue
        seen.add(path)
        unique_images.append(path)

    use_s3_for_image = _project_uses_s3_images(session.project_id)
    failed_s3: list[dict[str, str]] = []
    affected: set[str] = set()

    with _db_lock:
        now = _now_ms()
        for image_path in unique_images:
            if use_s3_for_image:
                try:
                    _s3_delete_image(session.project_id, image_path)
                except (BotoCoreError, ClientError, Exception) as exc:
                    failed_s3.append({"path": image_path, "error": str(exc)})
                    continue

            delete_paths = [image_path]
            if delete_labels:
                delete_paths.extend(_label_paths_for_image(image_path))

            for path in delete_paths:
                if path in affected:
                    continue
                affected.add(path)
                _record_deleted_file(
                    session.project_id,
                    username=session.username,
                    source_token=session.token,
                    path=path,
                    mtime_ms=now,
                )

            _CONN.execute(
                "DELETE FROM image_status WHERE project_id = ? AND image_name = ?",
                (session.project_id, Path(image_path).name),
            )

        _CONN.commit()

    return {
        "ok": True,
        "projectId": session.project_id,
        "requested": len(unique_images),
        "deletedImageCount": len(unique_images) - len(failed_s3),
        "deletedFileRecords": len(affected),
        "deleteLabels": delete_labels,
        "failedS3": failed_s3,
    }


@app.post("/api/admin/labels/purge-mode-files")
def admin_purge_mode_label_files(session: SessionContext = Depends(_admin_only)) -> dict[str, Any]:
    rows = _CONN.execute(
        "SELECT path FROM files WHERE project_id = ? AND deleted = 0",
        (session.project_id,),
    ).fetchall()

    to_delete: list[str] = []
    for row in rows:
        path = str(row["path"] or "")
        lower = path.lower()
        if not lower.endswith(".txt"):
            continue
        if "/labels/" not in lower:
            continue
        if "/bb/" not in lower and "/obb/" not in lower:
            continue
        to_delete.append(path)

    if not to_delete:
        return {"ok": True, "projectId": session.project_id, "purged": 0}

    with _db_lock:
        now = _now_ms()
        for path in to_delete:
            _record_deleted_file(
                session.project_id,
                username=session.username,
                source_token=session.token,
                path=path,
                mtime_ms=now,
            )
        _CONN.commit()

    return {
        "ok": True,
        "projectId": session.project_id,
        "purged": len(to_delete),
    }


# -------------------------
# Locks + sync
# -------------------------

@app.post("/api/locks/activate")
def activate_lock(
    payload: ActivateLockPayload,
    session: SessionContext = Depends(_auth_from_header),
) -> dict[str, Any]:
    requested = payload.path.strip() if isinstance(payload.path, str) else ""

    with _db_lock:
        if not requested:
            _CONN.execute("DELETE FROM locks WHERE token = ?", (session.token,))
            _CONN.execute("UPDATE sessions SET active_file = NULL WHERE token = ?", (session.token,))
            _CONN.commit()
            return {"ok": True, "activeFile": None, "released": True}

        normalized = _normalize_path(requested)
        conflict = _CONN.execute(
            "SELECT username, token, updated_at FROM locks WHERE project_id = ? AND path = ? AND token != ?",
            (session.project_id, normalized, session.token),
        ).fetchone()
        if conflict is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "File is locked by another user",
                    "lockedBy": str(conflict["username"]),
                    "path": normalized,
                    "updatedAt": int(conflict["updated_at"]),
                },
            )

        now = _now_ms()
        _CONN.execute("DELETE FROM locks WHERE token = ?", (session.token,))
        _CONN.execute(
            "INSERT OR REPLACE INTO locks (project_id, path, token, username, updated_at) VALUES (?, ?, ?, ?, ?)",
            (session.project_id, normalized, session.token, session.username, now),
        )
        _CONN.execute(
            "UPDATE sessions SET active_file = ?, last_seen = ? WHERE token = ?",
            (normalized, now, session.token),
        )
        _CONN.commit()

    return {"ok": True, "activeFile": normalized, "lockedBy": session.username}


@app.get("/api/locks")
def list_locks(session: SessionContext = Depends(_auth_from_header)) -> dict[str, Any]:
    _cleanup_stale_sessions()
    rows = _CONN.execute(
        "SELECT path, username, updated_at FROM locks WHERE project_id = ? ORDER BY path ASC",
        (session.project_id,),
    ).fetchall()
    locks = [
        {"path": str(row["path"]), "username": str(row["username"]), "updatedAt": int(row["updated_at"])}
        for row in rows
    ]
    return {"ok": True, "locks": locks}


@app.post("/api/sync/upsert")
def sync_upsert(
    payload: SyncUpsertPayload,
    session: SessionContext = Depends(_auth_from_header),
) -> dict[str, Any]:
    updates = payload.updates or []
    applied = 0
    rejected: list[dict[str, Any]] = []

    with _db_lock:
        now = _now_ms()
        _CONN.execute("UPDATE sessions SET last_seen = ? WHERE token = ?", (now, session.token))

        for item in updates:
            try:
                path = _normalize_path(item.path)
            except HTTPException as exc:
                rejected.append({"path": item.path, "reason": exc.detail})
                continue

            is_image = _is_image_path(path)
            use_s3_for_image = is_image and _project_uses_s3_images(session.project_id)

            if item.deleted:
                content_b64 = ""
                sha1 = ""
                if use_s3_for_image:
                    try:
                        _s3_delete_image(session.project_id, path)
                    except (BotoCoreError, ClientError, Exception) as exc:
                        rejected.append({"path": path, "reason": f"S3 delete failed: {exc}"})
                        continue
            else:
                content_b64 = item.contentBase64 or ""
                sha1 = item.sha1 or ""
                if not content_b64:
                    rejected.append({"path": path, "reason": "Missing file content"})
                    continue
                estimate = int(len(content_b64) * 0.75)
                if estimate > MAX_FILE_BYTES:
                    rejected.append({"path": path, "reason": f"File exceeds max size {MAX_FILE_BYTES}"})
                    continue

                if use_s3_for_image:
                    try:
                        raw = base64.b64decode(content_b64.encode("ascii"), validate=True)
                    except Exception:
                        rejected.append({"path": path, "reason": "Invalid base64 content"})
                        continue
                    try:
                        _s3_put_image(session.project_id, path, raw)
                    except (BotoCoreError, ClientError, Exception) as exc:
                        rejected.append({"path": path, "reason": f"S3 upload failed: {exc}"})
                        continue
                    # Image payload is stored in S3; keep DB rows lightweight.
                    content_b64 = ""

            if _requires_explicit_lock(path):
                lock_row = _CONN.execute(
                    "SELECT token, username FROM locks WHERE project_id = ? AND path = ?",
                    (session.project_id, path),
                ).fetchone()
                if lock_row is None or str(lock_row["token"]) != session.token:
                    rejected.append({"path": path, "reason": "Explicit lock required for label file"})
                    continue

            other_lock = _CONN.execute(
                "SELECT username FROM locks WHERE project_id = ? AND path = ? AND token != ?",
                (session.project_id, path, session.token),
            ).fetchone()
            if other_lock is not None:
                rejected.append({"path": path, "reason": f"Locked by {str(other_lock['username'])}"})
                continue

            mtime_ms = int(item.mtimeMs or now)
            _CONN.execute(
                """
                INSERT INTO files (project_id, path, deleted, mtime_ms, sha1, content_base64, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, path) DO UPDATE SET
                  deleted=excluded.deleted,
                  mtime_ms=excluded.mtime_ms,
                  sha1=excluded.sha1,
                  content_base64=excluded.content_base64,
                  updated_at=excluded.updated_at
                """,
                (
                    session.project_id,
                    path,
                    1 if item.deleted else 0,
                    mtime_ms,
                    sha1,
                    content_b64,
                    now,
                ),
            )
            _CONN.execute(
                "INSERT INTO changes (project_id, username, source_token, path, deleted, mtime_ms, sha1, content_base64, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session.project_id,
                    session.username,
                    session.token,
                    path,
                    1 if item.deleted else 0,
                    mtime_ms,
                    sha1,
                    content_b64,
                    now,
                ),
            )
            if item.deleted and is_image:
                image_name = Path(path).name.strip()
                if image_name:
                    _CONN.execute(
                        "DELETE FROM image_status WHERE project_id = ? AND image_name = ?",
                        (session.project_id, image_name),
                    )
            applied += 1

        _CONN.commit()

        last_seq_row = _CONN.execute(
            "SELECT COALESCE(MAX(seq), 0) AS s FROM changes WHERE project_id = ?",
            (session.project_id,),
        ).fetchone()

    return {
        "ok": True,
        "applied": applied,
        "rejected": rejected,
        "latestSeq": int(last_seq_row["s"] if last_seq_row else 0),
    }


@app.get("/api/sync/changes")
def sync_changes(
    since: int = 0,
    limit: int = 1200,
    session: SessionContext = Depends(_auth_from_header),
) -> dict[str, Any]:
    image_access_mode = _get_project_image_access_mode(session.project_id)
    safe_limit = max(50, min(2500, int(limit)))
    rows = _CONN.execute(
        """
        SELECT seq, username, source_token, path, deleted, mtime_ms, sha1, content_base64, created_at
        FROM changes
        WHERE project_id = ? AND seq > ?
        ORDER BY seq ASC
        LIMIT ?
        """,
        (session.project_id, int(max(0, since)), safe_limit),
    ).fetchall()

    changes: list[dict[str, Any]] = []
    for row in rows:
        path = str(row["path"])
        deleted = bool(row["deleted"])
        content_b64 = str(row["content_base64"])

        if image_access_mode == "cloud_only" and _is_image_path(path):
            # Cloud-only clients fetch images through signed URLs + cache,
            # so change feed keeps metadata only.
            content_b64 = ""
        elif _project_uses_s3_images(session.project_id) and _is_image_path(path) and not deleted:
            try:
                content_b64 = _s3_get_image_base64(session.project_id, path)
            except (BotoCoreError, ClientError, Exception) as exc:
                raise HTTPException(status_code=500, detail=f"S3 read failed for {path}: {exc}") from exc

        changes.append(
            {
                "seq": int(row["seq"]),
                "username": str(row["username"]),
                "sourceToken": str(row["source_token"]),
                "path": path,
                "deleted": deleted,
                "mtimeMs": int(row["mtime_ms"]),
                "sha1": str(row["sha1"]),
                "contentBase64": content_b64,
                "createdAt": int(row["created_at"]),
            }
        )

    max_row = _CONN.execute(
        "SELECT COALESCE(MAX(seq), 0) AS s FROM changes WHERE project_id = ?",
        (session.project_id,),
    ).fetchone()

    return {
        "ok": True,
        "changes": changes,
        "latestSeq": int(max_row["s"] if max_row else 0),
    }


@app.get("/api/sync/status")
def sync_status(session: SessionContext = Depends(_auth_from_header)) -> dict[str, Any]:
    _cleanup_stale_sessions()

    lock_rows = _CONN.execute(
        "SELECT path, username, updated_at FROM locks WHERE project_id = ? ORDER BY updated_at DESC",
        (session.project_id,),
    ).fetchall()
    lock_items = [
        {"path": str(row["path"]), "username": str(row["username"]), "updatedAt": int(row["updated_at"])}
        for row in lock_rows
    ]

    users_online_row = _CONN.execute(
        "SELECT COUNT(*) AS c FROM sessions WHERE project_id = ?",
        (session.project_id,),
    ).fetchone()
    change_row = _CONN.execute(
        "SELECT COALESCE(MAX(seq), 0) AS s FROM changes WHERE project_id = ?",
        (session.project_id,),
    ).fetchone()

    backups = sorted(BACKUP_DIR.glob("sync-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    backup_items = [
        {
            "name": path.name,
            "sizeBytes": path.stat().st_size,
            "modifiedAt": int(path.stat().st_mtime * 1000),
        }
        for path in backups[:8]
    ]

    return {
        "ok": True,
        "projectId": session.project_id,
        "username": session.username,
        "role": session.role,
        "isAdmin": session.is_admin,
        "dailyBackupEnabled": True,
        "backupDir": str(BACKUP_DIR),
        "backupRetentionDays": BACKUP_RETENTION_DAYS,
        "imageAccessMode": _get_project_image_access_mode(session.project_id),
        "activeFile": session.active_file,
        "onlineUsers": int(users_online_row["c"] if users_online_row else 0),
        "latestSeq": int(change_row["s"] if change_row else 0),
        "locks": lock_items,
        "recentBackups": backup_items,
        "sessionTtlSeconds": SESSION_TTL_SECONDS,
        "serverTime": _utc_now_iso(),
    }


@app.on_event("startup")
def on_startup() -> None:
    _init_db()
    _ensure_daily_backup()
    thread = threading.Thread(target=_backup_worker, name="sync-backup-worker", daemon=True)
    thread.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    _stop_event.set()

