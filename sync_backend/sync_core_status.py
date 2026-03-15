from __future__ import annotations

try:
    from sync_core import *  # noqa: F401,F403
    from sync_core_store import *  # noqa: F401,F403
except ImportError:
    from .sync_core import *  # noqa: F401,F403
    from .sync_core_store import *  # noqa: F401,F403

# ---------- Change Feed / Store / Summary ----------


def touch_session_last_seen(*, conn: sqlite3.Connection, token: str, now_ms: int) -> None:
    conn.execute("UPDATE sessions SET last_seen = ? WHERE token = ?", (int(now_ms), str(token)))


def upsert_file_record(
    *,
    conn: sqlite3.Connection,
    project_id: str,
    path: str,
    deleted: bool,
    mtime_ms: int,
    sha1: str,
    content_base64: str,
    updated_at: int,
) -> None:
    conn.execute(
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
            str(project_id),
            str(path),
            1 if deleted else 0,
            int(mtime_ms),
            str(sha1 or ""),
            str(content_base64 or ""),
            int(updated_at),
        ),
    )


def insert_change_record(
    *,
    conn: sqlite3.Connection,
    project_id: str,
    username: str,
    source_token: str,
    path: str,
    deleted: bool,
    mtime_ms: int,
    sha1: str,
    content_base64: str,
    created_at: int,
) -> None:
    conn.execute(
        "INSERT INTO changes (project_id, username, source_token, path, deleted, mtime_ms, sha1, content_base64, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(project_id),
            str(username),
            str(source_token),
            str(path),
            1 if deleted else 0,
            int(mtime_ms),
            str(sha1 or ""),
            str(content_base64 or ""),
            int(created_at),
        ),
    )


def delete_image_status_for_path(*, conn: sqlite3.Connection, project_id: str, path: str) -> bool:
    image_name = Path(str(path)).name.strip()
    if not image_name:
        return False
    conn.execute(
        "DELETE FROM image_status WHERE project_id = ? AND image_name = ?",
        (str(project_id), image_name),
    )
    return True


def max_change_seq(*, conn: sqlite3.Connection, project_id: str) -> int:
    return latest_change_seq(conn=conn, project_id=project_id)


def fetch_changes_since(*, conn: sqlite3.Connection, project_id: str, since: int, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT seq, username, source_token, path, deleted, mtime_ms, sha1, content_base64, created_at
        FROM changes
        WHERE project_id = ? AND seq > ?
        ORDER BY seq ASC
        LIMIT ?
        """,
        (str(project_id), int(max(0, since)), int(limit)),
    ).fetchall()


def map_change_rows(
    *,
    rows: list[sqlite3.Row],
    image_access_mode: str,
    project_id: str,
    project_uses_s3_images: bool,
    is_image_path: Callable[[str], bool],
    s3_get_image_base64: Callable[[str, str], str],
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for row in rows:
        path = str(row["path"])
        deleted = bool(row["deleted"])
        content_b64 = str(row["content_base64"])

        if is_cloud_only_image_access_mode(image_access_mode) and is_image_path(path):
            content_b64 = ""
        elif project_uses_s3_images and is_image_path(path) and not deleted:
            try:
                content_b64 = s3_get_image_base64(project_id, path)
            except Exception as exc:
                raise ValueError(f"S3 read failed for {path}: {exc}") from exc

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
    return changes


def fetch_project_summary_rows(*, conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    users_row = conn.execute("SELECT COUNT(*) AS c FROM users WHERE project_id = ?", (str(project_id),)).fetchone()
    files_row = conn.execute(
        "SELECT COUNT(*) AS c FROM files WHERE project_id = ? AND deleted = 0",
        (str(project_id),),
    ).fetchone()
    changes_row = conn.execute("SELECT COUNT(*) AS c FROM changes WHERE project_id = ?", (str(project_id),)).fetchone()
    latest_change = conn.execute(
        "SELECT path, username, created_at FROM changes WHERE project_id = ? ORDER BY seq DESC LIMIT 1",
        (str(project_id),),
    ).fetchone()

    return {
        "users": int(users_row["c"] if users_row else 0),
        "files": int(files_row["c"] if files_row else 0),
        "changes": int(changes_row["c"] if changes_row else 0),
        "latestChange": {
            "path": str(latest_change["path"]),
            "username": str(latest_change["username"]),
            "createdAt": int(latest_change["created_at"]),
        }
        if latest_change
        else None,
    }


def fetch_recent_changes(*, conn: sqlite3.Connection, project_id: str, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT seq, username, path, deleted, mtime_ms, created_at
        FROM changes
        WHERE project_id = ?
        ORDER BY seq DESC
        LIMIT ?
        """,
        (str(project_id), int(limit)),
    ).fetchall()


def map_recent_changes(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [
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


# ---------- Status Payload Builders ----------


def build_sync_status_payload(
    *,
    project_id: str,
    username: str,
    role: str,
    is_admin: bool,
    backup_dir: str,
    backup_retention_days: int,
    backup_retention_value: int,
    backup_retention_unit: str,
    image_access_mode: str,
    active_file: str | None,
    online_users: int,
    latest_seq: int,
    latest_status_seq: int,
    locks: list[dict[str, Any]],
    recent_backups: list[dict[str, Any]],
    session_ttl_seconds: int,
    server_time: str,
) -> dict[str, Any]:
    return {
        "ok": True,
        "projectId": str(project_id),
        "username": str(username),
        "role": str(role),
        "isAdmin": bool(is_admin),
        "dailyBackupEnabled": True,
        "backupDir": str(backup_dir),
        "backupRetentionDays": int(backup_retention_days),
        "backupRetentionValue": int(backup_retention_value),
        "backupRetentionUnit": str(backup_retention_unit),
        "imageAccessMode": str(image_access_mode),
        "activeFile": active_file,
        "onlineUsers": int(online_users),
        "latestSeq": int(latest_seq),
        "latestStatusSeq": int(latest_status_seq),
        "locks": list(locks),
        "recentBackups": list(recent_backups),
        "sessionTtlSeconds": int(session_ttl_seconds),
        "serverTime": str(server_time),
    }


def build_sync_status_response(
    *,
    project_id: str,
    username: str,
    role: str,
    is_admin: bool,
    active_file: str | None,
    backup_dir: str,
    session_ttl_seconds: int,
    server_time: str,
    list_project_locks: Callable[[str, bool], list[dict[str, Any]]],
    online_users_count: Callable[[str], int],
    latest_change_seq: Callable[[str], int],
    latest_status_seq: Callable[[str], int],
    get_backup_retention_policy: Callable[[], tuple[int, str, int]],
    list_backups: Callable[[], list[dict[str, Any]]],
    get_project_image_access_mode: Callable[[str], str],
    build_payload: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    locks = list_project_locks(project_id, True)
    online_users = online_users_count(project_id)
    latest_seq = latest_change_seq(project_id)
    latest_status = latest_status_seq(project_id)
    retention_value, retention_unit, retention_days = get_backup_retention_policy()
    backup_items = list_backups()[:8]

    return build_payload(
        project_id=project_id,
        username=username,
        role=role,
        is_admin=is_admin,
        backup_dir=backup_dir,
        backup_retention_days=retention_days,
        backup_retention_value=retention_value,
        backup_retention_unit=retention_unit,
        image_access_mode=get_project_image_access_mode(project_id),
        active_file=active_file,
        online_users=online_users,
        latest_seq=latest_seq,
        latest_status_seq=latest_status,
        locks=locks,
        recent_backups=backup_items,
        session_ttl_seconds=session_ttl_seconds,
        server_time=server_time,
    )


