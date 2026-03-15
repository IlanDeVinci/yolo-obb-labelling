from __future__ import annotations

try:
    from sync_core import *  # noqa: F401,F403
except ImportError:
    from .sync_core import *  # noqa: F401,F403

# ---------- Backup ----------


def list_backups(backup_dir: Path) -> list[dict[str, Any]]:
    backups = sorted(backup_dir.glob("sync-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
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


def safe_backup_path_from_name(backup_dir: Path, backup_name: str) -> Path:
    name = str(backup_name or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        raise ValueError("Invalid backup name")
    if not name.endswith(".db") or not name.startswith("sync-"):
        raise ValueError("Invalid backup file name")
    backup_path = (backup_dir / name).resolve()
    if backup_dir not in backup_path.parents and backup_path != backup_dir:
        raise ValueError("Invalid backup path")
    if not backup_path.exists() or not backup_path.is_file():
        raise FileNotFoundError("Backup file not found")
    return backup_path


def compute_sha256_for_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def dry_run_backup_restore(backup_path: Path) -> dict[str, Any]:
    stats = backup_path.stat()
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
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return {
        "ok": bool(header_ok and quick_check_ok),
        "backupName": backup_path.name,
        "sizeBytes": int(stats.st_size),
        "modifiedAt": int(stats.st_mtime * 1000),
        "sha256": compute_sha256_for_file(backup_path),
        "header": header_text,
        "headerValid": header_ok,
        "quickCheck": quick_check_result,
        "quickCheckOk": quick_check_ok,
        "quickCheckError": quick_check_error,
    }


def cleanup_old_backups(backup_dir: Path, retention_days: int) -> None:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=int(retention_days))
    for backup in backup_dir.glob("sync-*.db"):
        try:
            modified = dt.datetime.fromtimestamp(backup.stat().st_mtime, dt.timezone.utc)
            if modified < cutoff:
                backup.unlink()
        except OSError:
            pass


# ---------- Session / Settings ----------


def cleanup_stale_sessions(
    *,
    conn: sqlite3.Connection,
    db_lock: threading.Lock,
    now_ms: int,
    session_ttl_seconds: int,
) -> None:
    cutoff = int(now_ms) - int(session_ttl_seconds) * 1000
    with db_lock:
        cur = conn.cursor()
        cur.execute("DELETE FROM sessions WHERE last_seen < ?", (cutoff,))
        cur.execute("DELETE FROM locks WHERE token NOT IN (SELECT token FROM sessions)")
        conn.commit()


def touch_session(*, conn: sqlite3.Connection, db_lock: threading.Lock, token: str, now_ms: int) -> None:
    with db_lock:
        conn.execute("UPDATE sessions SET last_seen = ? WHERE token = ?", (int(now_ms), str(token)))
        conn.commit()


def fetch_session_row(*, conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT token, project_id, username, role, is_admin, active_file, last_seen FROM sessions WHERE token = ?",
        (str(token),),
    ).fetchone()


def row_to_session_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "token": str(row["token"]),
        "project_id": str(row["project_id"]),
        "username": str(row["username"]),
        "role": str(row["role"] or "user"),
        "is_admin": bool(row["is_admin"]),
        "active_file": str(row["active_file"]) if row["active_file"] else None,
    }


def setting_get(*, conn: sqlite3.Connection, key: str, default_value: str = "") -> str:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (str(key or ""),)).fetchone()
    return str(default_value) if row is None else str(row["value"] or default_value)


def setting_set(
    *,
    conn: sqlite3.Connection,
    db_lock: threading.Lock,
    key: str,
    value: str,
    updated_at_ms: int,
) -> None:
    with db_lock:
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (str(key or ""), str(value or ""), int(updated_at_ms)),
        )
        conn.commit()


def compute_backup_retention_policy(
    *,
    raw_unit: str,
    raw_value: str,
    backup_retention_days_default: int,
) -> tuple[int, str, int]:
    unit_candidate = str(raw_unit or "days").strip().lower()
    unit = unit_candidate if unit_candidate in {"days", "months"} else "days"

    default_value = int(backup_retention_days_default)
    if unit == "months":
        default_value = max(1, min(120, int(round(default_value / 30))))

    try:
        retention_value = int(str(raw_value or str(default_value)).strip())
    except Exception:
        retention_value = default_value

    if unit == "days":
        retention_value = max(2, min(3650, retention_value))
        retention_days = retention_value
    else:
        retention_value = max(1, min(120, retention_value))
        retention_days = max(2, min(3650, retention_value * 30))

    return retention_value, unit, retention_days


def set_backup_retention_policy(
    *,
    conn: sqlite3.Connection,
    db_lock: threading.Lock,
    retention_value: int,
    retention_unit: str,
    updated_at_ms: int,
) -> dict[str, int | str]:
    unit = str(retention_unit or "days").strip().lower()
    if unit not in {"days", "months"}:
        raise ValueError("retentionUnit must be 'days' or 'months'")

    value = int(retention_value)
    if unit == "days":
        value = max(2, min(3650, value))
        days = value
    else:
        value = max(1, min(120, value))
        days = max(2, min(3650, value * 30))

    with db_lock:
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES ('backup_retention_value', ?, ?)",
            (str(value), int(updated_at_ms)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES ('backup_retention_unit', ?, ?)",
            (unit, int(updated_at_ms)),
        )
        conn.commit()

    return {"retentionValue": value, "retentionUnit": unit, "retentionDays": days}


# ---------- Image Inventory / Locks / User Sessions ----------


def fetch_project_image_status_map(*, conn: sqlite3.Connection, project_id: str) -> dict[str, str]:
    rows = conn.execute(
        "SELECT image_name, status FROM image_status WHERE project_id = ?",
        (str(project_id),),
    ).fetchall()
    out: dict[str, str] = {}
    for row in rows:
        image_name = str(row["image_name"] or "").strip()
        status = str(row["status"] or "").strip().lower()
        if image_name and status in VALID_IMAGE_STATUSES:
            out[image_name] = status
    return out


def collect_project_image_rows_from_db(*, conn: sqlite3.Connection, project_id: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT path, mtime_ms, updated_at, content_base64, sha1 FROM files WHERE project_id = ? AND deleted = 0 ORDER BY path ASC",
        (str(project_id),),
    ).fetchall()

    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        path = str(row["path"] or "")
        if not path or not is_image_path(path):
            continue
        out[path] = {
            "path": path,
            "mtimeMs": int(row["mtime_ms"] or 0),
            "updatedAt": int(row["updated_at"] or 0),
            "sha1": str(row["sha1"] or ""),
            "sizeBytes": estimate_b64_size_bytes(str(row["content_base64"] or "")),
        }
    return out


def collect_project_image_rows_from_s3_manifest(manifest: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
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


def release_session_locks(*, conn: sqlite3.Connection, db_lock: threading.Lock, token: str) -> None:
    with db_lock:
        conn.execute("DELETE FROM locks WHERE token = ?", (str(token),))
        conn.execute("UPDATE sessions SET active_file = NULL WHERE token = ?", (str(token),))
        conn.commit()


def find_lock_conflict(*, conn: sqlite3.Connection, project_id: str, path: str, token: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT username, token, updated_at FROM locks WHERE project_id = ? AND path = ? AND token != ?",
        (str(project_id), str(path), str(token)),
    ).fetchone()


def upsert_active_lock(
    *,
    conn: sqlite3.Connection,
    db_lock: threading.Lock,
    project_id: str,
    path: str,
    token: str,
    username: str,
    now_ms: int,
) -> None:
    with db_lock:
        conn.execute("DELETE FROM locks WHERE token = ?", (str(token),))
        conn.execute(
            "INSERT OR REPLACE INTO locks (project_id, path, token, username, updated_at) VALUES (?, ?, ?, ?, ?)",
            (str(project_id), str(path), str(token), str(username), int(now_ms)),
        )
        conn.execute(
            "UPDATE sessions SET active_file = ?, last_seen = ? WHERE token = ?",
            (str(path), int(now_ms), str(token)),
        )
        conn.commit()


def list_project_locks(*, conn: sqlite3.Connection, project_id: str, order_by_updated_desc: bool = False) -> list[dict[str, Any]]:
    order_clause = "updated_at DESC" if order_by_updated_desc else "path ASC"
    rows = conn.execute(
        f"SELECT path, username, updated_at FROM locks WHERE project_id = ? ORDER BY {order_clause}",
        (str(project_id),),
    ).fetchall()
    return [{"path": str(r["path"]), "username": str(r["username"]), "updatedAt": int(r["updated_at"])} for r in rows]


def holds_explicit_lock(*, conn: sqlite3.Connection, project_id: str, path: str, token: str) -> bool:
    row = conn.execute(
        "SELECT token FROM locks WHERE project_id = ? AND path = ?",
        (str(project_id), str(path)),
    ).fetchone()
    return bool(row is not None and str(row["token"]) == str(token))


def get_other_lock_holder(*, conn: sqlite3.Connection, project_id: str, path: str, token: str) -> str | None:
    row = conn.execute(
        "SELECT username FROM locks WHERE project_id = ? AND path = ? AND token != ?",
        (str(project_id), str(path), str(token)),
    ).fetchone()
    return None if row is None else str(row["username"])


def conflict_detail(*, conflict_row: sqlite3.Row, path: str) -> dict[str, Any]:
    return {
        "message": "File is locked by another user",
        "lockedBy": str(conflict_row["username"]),
        "path": str(path),
        "updatedAt": int(conflict_row["updated_at"]),
    }


def logout_session_token(*, conn: sqlite3.Connection, db_lock: threading.Lock | None, token: str) -> None:
    lock_ctx = db_lock if db_lock is not None else nullcontext()
    with lock_ctx:
        conn.execute("DELETE FROM locks WHERE token = ?", (str(token),))
        conn.execute("DELETE FROM sessions WHERE token = ?", (str(token),))
        conn.commit()


def delete_user_sessions_and_locks(
    *,
    conn: sqlite3.Connection,
    db_lock: threading.Lock | None,
    project_id: str,
    username: str,
) -> int:
    lock_ctx = db_lock if db_lock is not None else nullcontext()
    with lock_ctx:
        rows = conn.execute(
            "SELECT token FROM sessions WHERE project_id = ? AND username = ?",
            (str(project_id), str(username)),
        ).fetchall()
        count = 0
        for row in rows:
            token = str(row["token"])
            conn.execute("DELETE FROM locks WHERE token = ?", (token,))
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            count += 1
        conn.commit()
    return count


def owner_count_for_project(*, conn: sqlite3.Connection, project_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE project_id = ? AND role = 'owner'",
        (str(project_id),),
    ).fetchone()
    return int(row["c"] if row else 0)




