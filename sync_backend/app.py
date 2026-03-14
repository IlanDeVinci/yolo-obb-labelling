from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field


DB_PATH = Path(os.environ.get("SYNC_DB_PATH", "./data/sync.db")).resolve()
BACKUP_DIR = Path(os.environ.get("SYNC_BACKUP_DIR", "./data/backups")).resolve()
SESSION_TTL_SECONDS = max(20, int(os.environ.get("SYNC_SESSION_TTL_SECONDS", "45")))
BACKUP_RETENTION_DAYS = max(2, int(os.environ.get("SYNC_BACKUP_RETENTION_DAYS", "14")))
MAX_FILE_BYTES = max(64 * 1024, int(os.environ.get("SYNC_MAX_FILE_BYTES", str(8 * 1024 * 1024))))

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


class BootstrapPayload(BaseModel):
    projectId: str = Field(min_length=2, max_length=120)
    projectPassword: str = Field(min_length=4, max_length=256)
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=4, max_length=256)


class LoginPayload(BaseModel):
    projectId: str
    projectPassword: str
    username: str
    password: str


class CreateUserPayload(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=4, max_length=256)
    isAdmin: bool = False


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


@dataclass
class SessionContext:
    token: str
    project_id: str
    username: str
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
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              project_id TEXT NOT NULL,
              username TEXT NOT NULL,
              password_hash TEXT NOT NULL,
              is_admin INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              UNIQUE(project_id, username)
            );
            CREATE TABLE IF NOT EXISTS sessions (
              token TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              username TEXT NOT NULL,
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
        _CONN.commit()


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
        "SELECT token, project_id, username, is_admin, active_file FROM sessions WHERE token = ?",
        (token,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    _touch_session(token)
    return SessionContext(
        token=str(row["token"]),
        project_id=str(row["project_id"]),
        username=str(row["username"]),
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
        "sessionTtlSeconds": SESSION_TTL_SECONDS,
        "backupRetentionDays": BACKUP_RETENTION_DAYS,
    }


# -------------------------
# Auth + admin
# -------------------------

@app.post("/api/admin/bootstrap")
def bootstrap(payload: BootstrapPayload) -> dict[str, Any]:
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
        if project is None or not _verify_password(payload.projectPassword, str(project["password_hash"])):
            raise HTTPException(status_code=401, detail="Invalid project credentials")

        user = _CONN.execute(
            "SELECT username, password_hash, is_admin FROM users WHERE project_id = ? AND username = ?",
            (project_id, username),
        ).fetchone()
        if user is None or not _verify_password(payload.password, str(user["password_hash"])):
            raise HTTPException(status_code=401, detail="Invalid user credentials")

        token = secrets.token_urlsafe(32)
        now = _now_ms()
        _CONN.execute(
            "INSERT INTO sessions (token, project_id, username, is_admin, active_file, created_at, last_seen) VALUES (?, ?, ?, ?, NULL, ?, ?)",
            (token, project_id, username, int(user["is_admin"]), now, now),
        )
        _CONN.commit()

    return {
        "ok": True,
        "token": token,
        "projectId": project_id,
        "username": username,
        "isAdmin": bool(user["is_admin"]),
        "sessionTtlSeconds": SESSION_TTL_SECONDS,
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
        "SELECT username, is_admin, created_at FROM users WHERE project_id = ? ORDER BY username ASC",
        (session.project_id,),
    ).fetchall()
    users = [
        {
            "username": str(row["username"]),
            "isAdmin": bool(row["is_admin"]),
            "createdAt": str(row["created_at"]),
        }
        for row in rows
    ]
    return {"ok": True, "users": users}


@app.post("/api/admin/users")
def create_user(
    payload: CreateUserPayload,
    session: SessionContext = Depends(_admin_only),
) -> dict[str, Any]:
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="username is required")

    with _db_lock:
        exists = _CONN.execute(
            "SELECT username FROM users WHERE project_id = ? AND username = ?",
            (session.project_id, username),
        ).fetchone()
        if exists is not None:
            raise HTTPException(status_code=409, detail="User already exists")

        _CONN.execute(
            "INSERT INTO users (project_id, username, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                session.project_id,
                username,
                _hash_password(payload.password),
                1 if payload.isAdmin else 0,
                _utc_now_iso(),
            ),
        )
        _CONN.commit()

    return {"ok": True, "username": username}


@app.post("/api/admin/backup-now")
def backup_now(session: SessionContext = Depends(_admin_only)) -> dict[str, Any]:
    backup_path = _backup_db("manual")
    _cleanup_old_backups()
    return {"ok": True, "backupPath": backup_path}


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

            if item.deleted:
                content_b64 = ""
                sha1 = ""
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

    changes = [
        {
            "seq": int(row["seq"]),
            "username": str(row["username"]),
            "sourceToken": str(row["source_token"]),
            "path": str(row["path"]),
            "deleted": bool(row["deleted"]),
            "mtimeMs": int(row["mtime_ms"]),
            "sha1": str(row["sha1"]),
            "contentBase64": str(row["content_base64"]),
            "createdAt": int(row["created_at"]),
        }
        for row in rows
    ]

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
        "isAdmin": session.is_admin,
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

