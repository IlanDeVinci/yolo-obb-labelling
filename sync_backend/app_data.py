from __future__ import annotations

try:
    from app_core import *  # noqa: F401,F403
    from sync_schema_sql import SYNC_SCHEMA_SQL
except ImportError:
    from .app_core import *  # noqa: F401,F403
    from .sync_schema_sql import SYNC_SCHEMA_SQL
def _init_db() -> None:
    with _db_lock:
        cur = _CONN.cursor()
        cur.executescript(SYNC_SCHEMA_SQL)
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
        now = _now_ms()
        existing_retention_unit = _CONN.execute(
            "SELECT value FROM app_settings WHERE key = 'backup_retention_unit'",
        ).fetchone()
        existing_retention_value = _CONN.execute(
            "SELECT value FROM app_settings WHERE key = 'backup_retention_value'",
        ).fetchone()
        if existing_retention_unit is None:
            _CONN.execute(
                "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES ('backup_retention_unit', ?, ?)",
                ("days", now),
            )
        if existing_retention_value is None:
            _CONN.execute(
                "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES ('backup_retention_value', ?, ?)",
                (str(BACKUP_RETENTION_DAYS), now),
            )
        _CONN.commit()
def _setting_get(key: str, default_value: str = "") -> str:
    return _setting_get_core(conn=_CONN, key=key, default_value=default_value)
def _setting_set(key: str, value: str) -> None:
    _setting_set_core(
        conn=_CONN,
        db_lock=_db_lock,
        key=key,
        value=value,
        updated_at_ms=_now_ms(),
    )
def _get_backup_retention_policy() -> tuple[int, str, int]:
    raw_unit = _setting_get("backup_retention_unit", "days")
    raw_value = _setting_get("backup_retention_value", str(BACKUP_RETENTION_DAYS))
    return _compute_backup_retention_policy_core(
        raw_unit=raw_unit,
        raw_value=raw_value,
        backup_retention_days_default=BACKUP_RETENTION_DAYS,
    )
def _set_backup_retention_policy(retention_value: int, retention_unit: str) -> dict[str, Any]:
    try:
        return _set_backup_retention_policy_core(
            conn=_CONN,
            db_lock=_db_lock,
            retention_value=retention_value,
            retention_unit=retention_unit,
            updated_at_ms=_now_ms(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
def _list_backups() -> list[dict[str, Any]]:
    return _list_backups_core(BACKUP_DIR)
def _safe_backup_path_from_name(backup_name: str) -> Path:
    try:
        return _safe_backup_path_from_name_core(BACKUP_DIR, backup_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup file not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
def _dry_run_backup_restore(backup_path: Path) -> dict[str, Any]:
    try:
        return _dry_run_backup_restore_core(backup_path)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Backup stat failed: {exc}")
def _restore_db_from_backup(backup_path: Path) -> dict[str, Any]:
    global _CONN
    pre_restore_snapshot = Path(
        _backup_db("pre-restore")
    )
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
def _get_project_storage_mode(project_id: str) -> str:
    row = _CONN.execute(
        "SELECT storage_mode FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    raw = str(row["storage_mode"]).strip().lower() if row and row["storage_mode"] else "auto"
    return _normalize_storage_mode(raw)
def _project_uses_s3_images(project_id: str) -> bool:
    access_mode = _get_project_image_access_mode(project_id)
    mode = _get_project_storage_mode(project_id)
    try:
        return _project_uses_s3_images_core(
            access_mode=access_mode,
            storage_mode=mode,
            s3_enabled=_is_s3_enabled(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
def _get_project_image_access_mode(project_id: str) -> str:
    row = _CONN.execute(
        "SELECT image_access_mode FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    raw = str(row["image_access_mode"]).strip().lower() if row and row["image_access_mode"] else "local"
    return _normalize_image_access_mode_value(raw, default="local")
def _session_can_delete_user(session: SessionContext, target_username: str) -> bool:
    def lookup_created_by(username: str) -> str | None:
        row = _CONN.execute(
            "SELECT created_by FROM users WHERE project_id = ? AND username = ?",
            (session.project_id, username),
        ).fetchone()
        if row is None:
            return None
        return str(row["created_by"] or "")
    return _can_delete_user_core(
        session_username=session.username,
        session_role=session.role,
        target_username=target_username,
        created_by_lookup=lookup_created_by,
    )
def _ensure_s3_image_mode(session: SessionContext) -> None:
    mode = _get_project_image_access_mode(session.project_id)
    if mode not in _VALID_IMAGE_ACCESS_MODES or not _is_remote_image_access_mode(mode):
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
        return _image_read_url_core(CLOUDFRONT_BASE_URL, key)
    return _s3_signed_get_url(project_id, path, expires_seconds=expires_seconds)
def _optimized_image_data_url(
    raw: bytes,
    *,
    max_width: int,
    max_height: int = 0,
    quality: int,
) -> tuple[str, str]:
    if Image is None:
        raise RuntimeError("Pillow unavailable")
    img = Image.open(io.BytesIO(raw))
    img.load()
    if ImageOps is not None:
        img = ImageOps.exif_transpose(img)
    if img.mode not in {"RGB", "L"}:
        img = img.convert("RGB")
    width, height = img.size
    target_width = max(320, min(int(max_width), 3840))
    target_height_limit = max(0, min(int(max_height or 0), 3840))
    scale_candidates: list[float] = []
    if width > target_width:
        scale_candidates.append(float(target_width) / float(max(1, width)))
    if target_height_limit > 0 and height > target_height_limit:
        scale_candidates.append(float(target_height_limit) / float(max(1, height)))
    if scale_candidates:
        ratio = min(scale_candidates)
        resized_width = max(1, int(round(float(width) * ratio)))
        resized_height = max(1, int(round(float(height) * ratio)))
        img = img.resize((resized_width, resized_height), Image.Resampling.LANCZOS)
    q = max(55, min(int(quality), 95))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=q, optimize=True, progressive=True)
    b64 = base64.b64encode(out.getvalue()).decode("ascii")
    return "image/jpeg", f"data:image/jpeg;base64,{b64}"
    if boto3 is None:
        return {"ok": False, "reason": "boto3-unavailable"}
    if not CLOUDFRONT_DISTRIBUTION_ID:
        return {"ok": False, "reason": "distribution-id-missing"}
    normalized = _normalize_cloudfront_invalidation_paths(keys)
    if not normalized:
        return {"ok": False, "reason": "no-paths"}
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
def _update_image_label_index_for_label_path(
    project_id: str,
    *,
    label_path: str,
    deleted: bool,
    content_base64: str,
    updated_at: int,
) -> None:
    if not _is_label_text_path(label_path):
        return
    image_stem = _label_stem_from_path(label_path)
    if not image_stem:
        return
    row = _CONN.execute(
        "SELECT image_name, bb_label_path, obb_label_path, bb_label_text, obb_label_text, bb_label_rows, obb_label_rows FROM image_label_index WHERE project_id = ? AND image_stem = ?",
        (project_id, image_stem),
    ).fetchone()
    image_name = image_stem
    bb_label_path = ""
    obb_label_path = ""
    bb_label_text = ""
    obb_label_text = ""
    bb_label_rows = 0
    obb_label_rows = 0
    if row is not None:
        image_name = str(row["image_name"] or image_stem)
        bb_label_path = str(row["bb_label_path"] or "")
        obb_label_path = str(row["obb_label_path"] or "")
        bb_label_text = str(row["bb_label_text"] or "")
        obb_label_text = str(row["obb_label_text"] or "")
        bb_label_rows = int(row["bb_label_rows"] or 0)
        obb_label_rows = int(row["obb_label_rows"] or 0)
    path_norm = str(label_path).strip().replace("\\", "/")
    is_bb = _is_bb_label_path(path_norm)
    is_obb = _is_obb_label_path(path_norm)
    if deleted:
        if is_bb:
            bb_label_path = ""
            bb_label_text = ""
            bb_label_rows = 0
        elif is_obb:
            obb_label_path = ""
            obb_label_text = ""
            obb_label_rows = 0
        else:
            bb_label_path = ""
            obb_label_path = ""
            bb_label_text = ""
            obb_label_text = ""
            bb_label_rows = 0
            obb_label_rows = 0
    else:
        raw_text = ""
        if content_base64:
            try:
                raw_text = base64.b64decode(content_base64.encode("ascii"), validate=False).decode("utf-8", errors="replace")
            except Exception:
                raw_text = ""
        split_bb_text, split_obb_text, split_bb_rows, split_obb_rows = _split_label_text_by_format(raw_text)
        if is_bb:
            bb_label_path = path_norm
            bb_label_text = raw_text
            bb_label_rows = int(split_bb_rows)
        elif is_obb:
            obb_label_path = path_norm
            obb_label_text = raw_text
            obb_label_rows = int(split_obb_rows)
        else:
            if split_bb_rows > 0:
                bb_label_path = path_norm
                bb_label_text = split_bb_text
                bb_label_rows = int(split_bb_rows)
            if split_obb_rows > 0:
                obb_label_path = path_norm
                obb_label_text = split_obb_text
                obb_label_rows = int(split_obb_rows)
    if not bb_label_path and not obb_label_path and bb_label_rows <= 0 and obb_label_rows <= 0:
        _CONN.execute(
            "DELETE FROM image_label_index WHERE project_id = ? AND image_stem = ?",
            (project_id, image_stem),
        )
        return
    _CONN.execute(
        """
        INSERT INTO image_label_index (
            project_id,
            image_stem,
            image_name,
            bb_label_path,
            obb_label_path,
            bb_label_text,
            obb_label_text,
            bb_label_rows,
            obb_label_rows,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id, image_stem) DO UPDATE SET
            image_name=excluded.image_name,
            bb_label_path=excluded.bb_label_path,
            obb_label_path=excluded.obb_label_path,
            bb_label_text=excluded.bb_label_text,
            obb_label_text=excluded.obb_label_text,
            bb_label_rows=excluded.bb_label_rows,
            obb_label_rows=excluded.obb_label_rows,
            updated_at=excluded.updated_at
        """,
        (
            project_id,
            image_stem,
            image_name,
            bb_label_path,
            obb_label_path,
            bb_label_text,
            obb_label_text,
            int(bb_label_rows),
            int(obb_label_rows),
            int(updated_at or _now_ms()),
        ),
    )
def _fetch_project_image_status_map(project_id: str) -> dict[str, str]:
    return _fetch_project_image_status_map_core(conn=_CONN, project_id=project_id)
def _collect_project_image_rows_from_db(project_id: str) -> dict[str, dict[str, Any]]:
    return _collect_project_image_rows_from_db_core(conn=_CONN, project_id=project_id)
def _collect_project_image_rows_from_s3(project_id: str) -> dict[str, dict[str, Any]]:
    if not _is_s3_enabled():
        return {}
    manifest = _s3_list_project_images(project_id)
    return _collect_project_image_rows_from_s3_manifest_core(manifest)
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
    _update_image_label_index_for_label_path(
        project_id,
        label_path=path,
        deleted=True,
        content_base64="",
        updated_at=mtime_ms,
    )
def _cleanup_stale_sessions() -> None:
    _cleanup_stale_sessions_core(
        conn=_CONN,
        db_lock=_db_lock,
        now_ms=_now_ms(),
        session_ttl_seconds=SESSION_TTL_SECONDS,
    )
def _touch_session(token: str) -> None:
    _touch_session_core(conn=_CONN, db_lock=_db_lock, token=token, now_ms=_now_ms())
def _get_session_by_token(token: str) -> SessionContext:
    _cleanup_stale_sessions()
    row = _fetch_session_row_core(conn=_CONN, token=token)
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    _touch_session(token)
    payload = _row_to_session_payload_core(row)
    return SessionContext(**payload)
def _backup_db(reason: str) -> str:
    stamp = dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    backup_file = BACKUP_DIR / f"sync-{stamp}-{reason}.db"
    with _db_lock:
        _CONN.commit()
        shutil.copy2(DB_PATH, backup_file)
    return str(backup_file)
def _cleanup_old_backups() -> None:
    _, _, retention_days = _get_backup_retention_policy()
    _cleanup_old_backups_core(BACKUP_DIR, retention_days)
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
    try:
        token = _extract_bearer_token(authorization)
    except ValueError:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return _get_session_by_token(token)
def _admin_only(session: SessionContext = Depends(_auth_from_header)) -> SessionContext:
    if not session.is_admin:
        raise HTTPException(status_code=403, detail="Admin role required")
    return session

