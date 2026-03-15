from __future__ import annotations

try:
    from app_core import *  # noqa: F401,F403
    from app_data import *  # noqa: F401,F403
except ImportError:
    from .app_core import *  # noqa: F401,F403
    from .app_data import *  # noqa: F401,F403
@app.get("/api/admin/images/view")
def admin_get_image_view(
    path: str,
    maxWidth: int = 0,
    maxHeight: int = 0,
    quality: int = 82,
    session: SessionContext = Depends(_admin_only),
) -> dict[str, Any]:
    normalized = _normalize_path(path)
    if not _is_image_path(normalized):
        raise HTTPException(status_code=400, detail="Path must reference an image file")

    requested_max_height = max(0, min(int(maxHeight or 0), 3840))
    requested_quality = max(55, min(int(quality or 82), 95))

    # Prefer S3 for latest object if available.
    if _is_s3_enabled() and _s3_image_exists(session.project_id, normalized):
        try:
            if requested_max_width > 0 and Image is not None:
                raw = _s3_get_image_bytes(session.project_id, normalized)
                try:
                    content_type, url = _optimized_image_data_url(
                        raw,
                        max_width=requested_max_width,
                        max_height=requested_max_height,
                        quality=requested_quality,
                    )
                    return {
                        "ok": True,
                        "path": normalized,
                        "source": "s3-optimized",
                        "contentType": content_type,
                        "url": url,
                        "maxWidth": requested_max_width,
                        "maxHeight": requested_max_height,
                        "quality": requested_quality,
                    }
                except Exception:
                    pass
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

    if requested_max_width > 0 and Image is not None:
        try:
            raw = base64.b64decode(content_b64.encode("ascii"), validate=False)
            content_type, url = _optimized_image_data_url(
                raw,
                max_width=requested_max_width,
                max_height=requested_max_height,
                quality=requested_quality,
            )
            return {
                "ok": True,
                "path": normalized,
                "source": "db-optimized",
                "contentType": content_type,
                "url": url,
                "maxWidth": requested_max_width,
                "maxHeight": requested_max_height,
                "quality": requested_quality,
            }
        except Exception:
            pass

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
    session: SessionContext = Depends(_admin_only),
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
        return {"ok": True, "path": normalized, "labels": [], "labelPath": label_path}

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
        "labels": labels,
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

    if not requested:
        _release_session_locks_core(conn=_CONN, db_lock=_db_lock, token=session.token)
        return {"ok": True, "activeFile": None, "released": True}

    normalized = _normalize_path(requested)
    conflict = _find_lock_conflict_core(
        conn=_CONN,
        project_id=session.project_id,
        path=normalized,
        token=session.token,
    )
    if conflict is not None:
        raise HTTPException(status_code=409, detail=_conflict_detail_core(conflict_row=conflict, path=normalized))

    _upsert_active_lock_core(
        conn=_CONN,
        db_lock=_db_lock,
        project_id=session.project_id,
        path=normalized,
        token=session.token,
        username=session.username,
        now_ms=_now_ms(),
    )

    return {"ok": True, "activeFile": normalized, "lockedBy": session.username}


@app.get("/api/locks")
def list_locks(session: SessionContext = Depends(_auth_from_header)) -> dict[str, Any]:
    _cleanup_stale_sessions()
    locks = _list_project_locks_core(conn=_CONN, project_id=session.project_id, order_by_updated_desc=False)
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
        _touch_session_last_seen_core(conn=_CONN, token=session.token, now_ms=now)

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
                if not _holds_explicit_lock_core(
                    conn=_CONN,
                    project_id=session.project_id,
                    path=path,
                    token=session.token,
                ):
                    rejected.append({"path": path, "reason": "Explicit lock required for label file"})
                    continue

            other_lock_holder = _get_other_lock_holder_core(
                conn=_CONN,
                project_id=session.project_id,
                path=path,
                token=session.token,
            )
            if other_lock_holder is not None:
                rejected.append({"path": path, "reason": f"Locked by {other_lock_holder}"})
                continue

            mtime_ms = int(item.mtimeMs or now)
            _upsert_file_record_core(
                conn=_CONN,
                project_id=session.project_id,
                path=path,
                deleted=bool(item.deleted),
                mtime_ms=mtime_ms,
                sha1=sha1,
                content_base64=content_b64,
                updated_at=now,
            )
            _insert_change_record_core(
                conn=_CONN,
                project_id=session.project_id,
                username=session.username,
                source_token=session.token,
                path=path,
                deleted=bool(item.deleted),
                mtime_ms=mtime_ms,
                sha1=sha1,
                content_base64=content_b64,
                created_at=now,
            )
            _update_image_label_index_for_label_path(
                session.project_id,
                label_path=path,
                deleted=bool(item.deleted),
                content_base64=content_b64,
                updated_at=now,
            )
            if item.deleted and is_image:
                _delete_image_status_for_path_core(conn=_CONN, project_id=session.project_id, path=path)
            applied += 1

        _CONN.commit()

        latest_seq = _max_change_seq_core(conn=_CONN, project_id=session.project_id)

    return {
        "ok": True,
        "applied": applied,
        "rejected": rejected,
        "latestSeq": int(latest_seq),
    }


@app.get("/api/sync/changes")
def sync_changes(
    since: int = 0,
    limit: int = 1200,
    session: SessionContext = Depends(_auth_from_header),
) -> dict[str, Any]:
    image_access_mode = _get_project_image_access_mode(session.project_id)
    safe_limit = _clamp_sync_changes_limit_core(limit)
    rows = _fetch_changes_since_core(
        conn=_CONN,
        project_id=session.project_id,
        since=since,
        limit=safe_limit,
    )
    try:
        changes = _map_change_rows_core(
            rows=rows,
            image_access_mode=image_access_mode,
            project_id=session.project_id,
            project_uses_s3_images=_project_uses_s3_images(session.project_id),
            is_image_path=_is_image_path,
            s3_get_image_base64=_s3_get_image_base64,
        )
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    latest_seq = _max_change_seq_core(conn=_CONN, project_id=session.project_id)

    return {
        "ok": True,
        "changes": changes,
        "latestSeq": int(latest_seq),
    }


@app.get("/api/sync/status")
def sync_status(session: SessionContext = Depends(_auth_from_header)) -> dict[str, Any]:
    _cleanup_stale_sessions()

    return _build_sync_status_response_core(
        project_id=session.project_id,
        username=session.username,
        role=session.role,
        is_admin=session.is_admin,
        active_file=session.active_file,
        backup_dir=str(BACKUP_DIR),
        session_ttl_seconds=SESSION_TTL_SECONDS,
        server_time=_utc_now_iso(),
        list_project_locks=lambda project_id, order_desc: _list_project_locks_core(
            conn=_CONN,
            project_id=project_id,
            order_by_updated_desc=order_desc,
        ),
        online_users_count=lambda project_id: _online_users_count_core(conn=_CONN, project_id=project_id),
        latest_change_seq=lambda project_id: _latest_change_seq_core(conn=_CONN, project_id=project_id),
        latest_status_seq=lambda project_id: _latest_status_seq_core(conn=_CONN, project_id=project_id),
        get_backup_retention_policy=_get_backup_retention_policy,
        list_backups=_list_backups,
        get_project_image_access_mode=_get_project_image_access_mode,
        build_payload=_build_sync_status_payload_core,
    )


@app.on_event("startup")
def on_startup() -> None:
    _run_startup_core(
        init_db=_init_db,
        ensure_daily_backup=_ensure_daily_backup,
        backup_worker=_backup_worker,
    )


@app.on_event("shutdown")
def on_shutdown() -> None:
    _run_shutdown_core(stop_event=_stop_event)







