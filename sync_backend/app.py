from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
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
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

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
SESSION_TTL_SECONDS = max(20, int(os.environ.get("SYNC_SESSION_TTL_SECONDS", "45")))
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


class SignedWritePayload(BaseModel):
    path: str
    contentType: str | None = None


class PrefetchBatchPayload(BaseModel):
    currentPath: str | None = None
    count: int = Field(default=10, ge=1, le=200)


class ProjectImageAccessPayload(BaseModel):
    imageAccessMode: str = Field(pattern="^(local|hybrid|cloud_only)$")


class ActivateLockPayload(BaseModel):
    path: str | None = None


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


def _cleanup_stale_sessions() -> None:
    cutoff = _now_ms() - SESSION_TTL_SECONDS * 1000
    with _db_lock:
        cur = _CONN.cursor()
        cur.execute("DELETE FROM sessions WHERE last_seen < ?", (cutoff,))
        cur.execute(
            "DELETE FROM locks WHERE token NOT IN (SELECT token FROM sessions)",
        )
        _CONN.commit()


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


def _cleanup_old_backups() -> None:
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=BACKUP_RETENTION_DAYS)
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
    items = _s3_list_project_images(session.project_id)
    if items:
        rows = _CONN.execute(
            "SELECT path, sha1 FROM files WHERE project_id = ? AND deleted = 0",
            (session.project_id,),
        ).fetchall()
        sha1_by_path = {
            str(row["path"]): str(row["sha1"] or "")
            for row in rows
            if row["path"] is not None
        }
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
    if session.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Only admin/owner can request image upload URLs")

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


@app.post("/api/admin/images/upload")
async def admin_upload_image(
    file: UploadFile = File(...),
    path: str = Form(""),
    expected_project_id: str = Form(""),
    overwrite: str = Form("0"),
    session: SessionContext = Depends(_admin_only),
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
    session: SessionContext = Depends(_admin_only),
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

