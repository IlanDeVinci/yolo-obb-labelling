from __future__ import annotations

try:
    from app_core import *  # noqa: F401,F403
    from app_data import *  # noqa: F401,F403
except ImportError:
    from .app_core import *  # noqa: F401,F403
    from .app_data import *  # noqa: F401,F403
@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/public/info")
def public_info() -> dict[str, Any]:
    row = _CONN.execute("SELECT COUNT(*) AS c FROM projects").fetchone()
    has_projects = bool(row and int(row["c"]) > 0)
    retention_value, retention_unit, retention_days = _get_backup_retention_policy()
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
        "backupRetentionDays": retention_days,
        "backupRetentionValue": retention_value,
        "backupRetentionUnit": retention_unit,
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
        if not _is_valid_bootstrap_token(x_bootstrap_token, BOOTSTRAP_TOKEN):
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
    _logout_session_token_core(conn=_CONN, db_lock=_db_lock, token=session.token)
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
            owners_count = _owner_count_for_project_core(conn=_CONN, project_id=session.project_id)
            if owners_count <= 1:
                raise HTTPException(status_code=400, detail="Cannot delete the last owner")

        _delete_user_sessions_and_locks_core(
            conn=_CONN,
            db_lock=None,
            project_id=session.project_id,
            username=target,
        )

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
    if not _validate_table_name_core(table):
        raise HTTPException(status_code=400, detail="Invalid table name")

    if not _table_exists_core(conn=_CONN, table=table):
        raise HTTPException(status_code=404, detail="Table not found")

    safe_limit = _clamp_admin_table_limit_core(int(payload.limit or 100))
    safe_offset = max(0, int(payload.offset or 0))

    columns = _table_columns_core(conn=_CONN, table=table)

    search_text = str(payload.search or "").strip()
    search_column = str(payload.searchColumn or "").strip()

    try:
        where_clause, where_params = _build_search_clause_core(
            search_text=search_text,
            search_column=search_column,
            columns=columns,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    total_query = f'SELECT COUNT(*) AS c FROM "{table}"{where_clause}'
    total_row = _CONN.execute(total_query, tuple(where_params)).fetchone()
    total = int(total_row["c"] if total_row else 0)

    rows_query = f'SELECT * FROM "{table}"{where_clause} LIMIT ? OFFSET ?'
    rows = _CONN.execute(
        rows_query,
        tuple([*where_params, safe_limit, safe_offset]),
    ).fetchall()

    items = _serialize_db_rows_core(rows)

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
    summary_rows = _fetch_project_summary_rows_core(conn=_CONN, project_id=session.project_id)

    return {
        "ok": True,
        "projectId": session.project_id,
        "role": session.role,
        "storageMode": _get_project_storage_mode(session.project_id),
        "imageAccessMode": _get_project_image_access_mode(session.project_id),
        "usesS3Images": _project_uses_s3_images(session.project_id),
        "requireProjectPassword": REQUIRE_PROJECT_PASSWORD,
        "totals": {
            "users": int(summary_rows["users"]),
            "files": int(summary_rows["files"]),
            "changes": int(summary_rows["changes"]),
        },
        "latestChange": summary_rows["latestChange"],
    }


@app.get("/api/project/recent-changes")
def project_recent_changes(
    limit: int = 30,
    session: SessionContext = Depends(_auth_from_header),
) -> dict[str, Any]:
    safe_limit = _clamp_recent_changes_limit_core(limit)
    rows = _fetch_recent_changes_core(conn=_CONN, project_id=session.project_id, limit=safe_limit)
    items = _map_recent_changes_core(rows)
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




