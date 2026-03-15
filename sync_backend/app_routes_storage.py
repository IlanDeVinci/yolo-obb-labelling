from __future__ import annotations

try:
    from app_core import *  # noqa: F401,F403
    from app_data import *  # noqa: F401,F403
except ImportError:
    from .app_core import *  # noqa: F401,F403
    from .app_data import *  # noqa: F401,F403
@app.post("/api/admin/project/storage")
def set_project_storage(
    payload: ProjectStoragePayload,
    session: SessionContext = Depends(_admin_only),
) -> dict[str, Any]:
    mode = str(payload.storageMode).strip().lower()
    if mode not in {"auto", "db", "s3"}:
        raise HTTPException(status_code=400, detail="Invalid storageMode")
    if mode == "s3" and not _is_s3_enabled():
        raise HTTPException(status_code=400, detail="S3 mode requires backend S3 configuration")

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
    mode = _normalize_image_access_mode_value(payload.imageAccessMode, default="")
    if mode not in _VALID_IMAGE_ACCESS_MODES:
        raise HTTPException(status_code=400, detail="Invalid imageAccessMode")
    if _is_remote_image_access_mode(mode) and not _is_s3_enabled():
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
        status = _normalize_image_status_value(row["status"])
        if not image_name or status not in _VALID_IMAGE_STATUSES:
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

    latest_status_row = _CONN.execute(
        "SELECT COALESCE(MAX(updated_at), 0) AS s FROM image_status WHERE project_id = ?",
        (session.project_id,),
    ).fetchone()
    latest_status_seq = int(latest_status_row["s"] if latest_status_row else 0)

    return {
        "ok": True,
        "projectId": session.project_id,
        "count": len(statuses),
        "latestStatusSeq": latest_status_seq,
        "statuses": statuses,
        "items": meta,
    }


@app.post("/api/image-status")
def upsert_image_status(
    payload: ImageStatusPayload,
    session: SessionContext = Depends(_auth_from_header),
) -> dict[str, Any]:
    image_name = str(payload.imageName or "").strip()
    status = _normalize_image_status_value(payload.status)
    if not image_name:
        raise HTTPException(status_code=400, detail="imageName is required")
    if "/" in image_name or "\\" in image_name:
        raise HTTPException(status_code=400, detail="imageName must be a basename")
    if status not in _VALID_IMAGE_STATUSES:
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
            status = _normalize_image_status_value(entry.status)

            if not image_name or "/" in image_name or "\\" in image_name:
                skipped += 1
                continue
            if status not in _VALID_IMAGE_STATUSES:
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






