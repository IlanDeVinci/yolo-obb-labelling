from __future__ import annotations

try:
    from app_core import *  # noqa: F401,F403
    from app_data import *  # noqa: F401,F403
except ImportError:
    from .app_core import *  # noqa: F401,F403
    from .app_data import *  # noqa: F401,F403
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
    session: SessionContext = Depends(_admin_only),
) -> dict[str, Any]:
    sort_key, sort_order = _normalize_admin_image_sort_core(sortBy, order)
    safe_limit = _clamp_admin_images_limit_core(int(limit or 5000))

    db_rows = _collect_project_image_rows_from_db(session.project_id)
    s3_rows: dict[str, dict[str, Any]] = {}
    if includeS3 and _is_s3_enabled():
        try:
            s3_rows = _collect_project_image_rows_from_s3(session.project_id)
        except Exception:
            s3_rows = {}

    status_by_name = _fetch_project_image_status_map(session.project_id)
    items = _build_admin_image_items_core(
        db_rows=db_rows,
        s3_rows=s3_rows,
        status_by_name=status_by_name,
    )
    _sort_admin_image_items_core(items, sort_key=sort_key, sort_order=sort_order)

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
    default_status = _normalize_image_status_value(payload.defaultStatus, default="in_progress")
    if default_status not in _VALID_IMAGE_STATUSES:
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






