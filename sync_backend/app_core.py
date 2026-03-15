from __future__ import annotations

import base64
import datetime as dt
import hashlib
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
import zipfile
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

try:
    from sync_config import (
        BACKUP_DIR,
        BACKUP_RETENTION_DAYS,
        BOOTSTRAP_TOKEN,
        CLOUDFRONT_BASE_URL,
        CLOUDFRONT_DISTRIBUTION_ID,
        DB_PATH,
        IMAGE_SUFFIXES,
        MAX_FILE_BYTES,
        MAX_ZIP_UPLOAD_BYTES,
        PREFETCH_MAX_BATCH,
        REQUIRE_PROJECT_PASSWORD,
        S3_BUCKET,
        S3_PREFIX,
        S3_REGION,
        SESSION_TTL_SECONDS,
        SIGNED_URL_TTL_SECONDS,
        _NORMALIZE_JOB_RETENTION_MS,
    )
    from sync_models.schemas import (
        ActivateLockPayload,
        AdminImageDeletePayload,
        AdminImageStatusReconcilePayload,
        AdminImageStatusSyncPayload,
        AdminNormalizeImagesPayload,
        BackupDryRunPayload,
        BackupRestorePayload,
        BackupRetentionPayload,
        BootstrapPayload,
        CreateUserPayload,
        DatabaseTableQueryPayload,
        ImageStatusPayload,
        LoginPayload,
        PrefetchBatchPayload,
        ProjectImageAccessPayload,
        ProjectOptionsPayload,
        ProjectStoragePayload,
        SessionContext,
        SignedUploadCommitPayload,
        SignedWritePayload,
        SyncUpsertPayload,
        UpsertFilePayload,
    )
    from sync_core import (
        estimate_b64_size_bytes as _estimate_b64_size_bytes,
        is_image_path as _is_image_path,
        is_label_text_path as _is_label_text_path,
        normalize_path as _normalize_path,
        requires_explicit_lock as _requires_explicit_lock,
        sanitize_archive_member_path as _sanitize_archive_member_path,
        bbox_to_corners as _bbox_to_corners,
        is_bb_label_path as _is_bb_label_path,
        is_obb_label_path as _is_obb_label_path,
        label_paths_for_image as _label_paths_for_image,
        label_stem_from_path as _label_stem_from_path,
        parse_yolo_label_rows as _parse_yolo_label_rows,
        split_label_text_by_format as _split_label_text_by_format,
        can_delete_user as _can_delete_user_core,
        extract_bearer_token as _extract_bearer_token,
        hash_password as _hash_password,
        is_valid_bootstrap_token as _is_valid_bootstrap_token,
        run_shutdown as _run_shutdown_core,
        run_startup as _run_startup_core,
        verify_password as _verify_password,
        cleanup_old_backups as _cleanup_old_backups_core,
        dry_run_backup_restore as _dry_run_backup_restore_core,
        list_backups as _list_backups_core,
        safe_backup_path_from_name as _safe_backup_path_from_name_core,
        VALID_IMAGE_ACCESS_MODES as _VALID_IMAGE_ACCESS_MODES,
        VALID_IMAGE_STATUSES as _VALID_IMAGE_STATUSES,
        build_admin_image_items as _build_admin_image_items_core,
        build_search_clause as _build_search_clause_core,
        clamp_admin_images_limit as _clamp_admin_images_limit_core,
        clamp_admin_table_limit as _clamp_admin_table_limit_core,
        clamp_recent_changes_limit as _clamp_recent_changes_limit_core,
        clamp_sync_changes_limit as _clamp_sync_changes_limit_core,
        image_read_url as _image_read_url_core,
        is_remote_image_access_mode as _is_remote_image_access_mode,
        latest_change_seq as _latest_change_seq_core,
        latest_status_seq as _latest_status_seq_core,
        normalize_admin_image_sort as _normalize_admin_image_sort_core,
        normalize_cloudfront_invalidation_paths as _normalize_cloudfront_invalidation_paths,
        normalize_image_access_mode as _normalize_image_access_mode_value,
        normalize_image_status as _normalize_image_status_value,
        normalize_storage_mode as _normalize_storage_mode,
        online_users_count as _online_users_count_core,
        project_uses_s3_images as _project_uses_s3_images_core,
        s3_object_key as _s3_object_key_core,
        serialize_db_rows as _serialize_db_rows_core,
        sort_admin_image_items as _sort_admin_image_items_core,
        table_columns as _table_columns_core,
        table_exists as _table_exists_core,
        validate_table_name as _validate_table_name_core,
        cleanup_stale_sessions as _cleanup_stale_sessions_core,
        fetch_session_row as _fetch_session_row_core,
        row_to_session_payload as _row_to_session_payload_core,
        touch_session as _touch_session_core,
        compute_backup_retention_policy as _compute_backup_retention_policy_core,
        set_backup_retention_policy as _set_backup_retention_policy_core,
        setting_get as _setting_get_core,
        setting_set as _setting_set_core,
        collect_project_image_rows_from_db as _collect_project_image_rows_from_db_core,
        collect_project_image_rows_from_s3_manifest as _collect_project_image_rows_from_s3_manifest_core,
        fetch_project_image_status_map as _fetch_project_image_status_map_core,
        conflict_detail as _conflict_detail_core,
        find_lock_conflict as _find_lock_conflict_core,
        get_other_lock_holder as _get_other_lock_holder_core,
        holds_explicit_lock as _holds_explicit_lock_core,
        list_project_locks as _list_project_locks_core,
        release_session_locks as _release_session_locks_core,
        upsert_active_lock as _upsert_active_lock_core,
        delete_user_sessions_and_locks as _delete_user_sessions_and_locks_core,
        logout_session_token as _logout_session_token_core,
        owner_count_for_project as _owner_count_for_project_core,
        delete_image_status_for_path as _delete_image_status_for_path_core,
        insert_change_record as _insert_change_record_core,
        max_change_seq as _max_change_seq_core,
        touch_session_last_seen as _touch_session_last_seen_core,
        upsert_file_record as _upsert_file_record_core,
        fetch_changes_since as _fetch_changes_since_core,
        map_change_rows as _map_change_rows_core,
        build_sync_status_payload as _build_sync_status_payload_core,
        build_sync_status_response as _build_sync_status_response_core,
        fetch_project_summary_rows as _fetch_project_summary_rows_core,
        fetch_recent_changes as _fetch_recent_changes_core,
        map_recent_changes as _map_recent_changes_core,
    )
except ImportError:
    from .sync_config import (
        BACKUP_DIR,
        BACKUP_RETENTION_DAYS,
        BOOTSTRAP_TOKEN,
        CLOUDFRONT_BASE_URL,
        CLOUDFRONT_DISTRIBUTION_ID,
        DB_PATH,
        IMAGE_SUFFIXES,
        MAX_FILE_BYTES,
        MAX_ZIP_UPLOAD_BYTES,
        PREFETCH_MAX_BATCH,
        REQUIRE_PROJECT_PASSWORD,
        S3_BUCKET,
        S3_PREFIX,
        S3_REGION,
        SESSION_TTL_SECONDS,
        SIGNED_URL_TTL_SECONDS,
        _NORMALIZE_JOB_RETENTION_MS,
    )
    from .sync_models.schemas import (
        ActivateLockPayload,
        AdminImageDeletePayload,
        AdminImageStatusReconcilePayload,
        AdminImageStatusSyncPayload,
        AdminNormalizeImagesPayload,
        BackupDryRunPayload,
        BackupRestorePayload,
        BackupRetentionPayload,
        BootstrapPayload,
        CreateUserPayload,
        DatabaseTableQueryPayload,
        ImageStatusPayload,
        LoginPayload,
        PrefetchBatchPayload,
        ProjectImageAccessPayload,
        ProjectOptionsPayload,
        ProjectStoragePayload,
        SessionContext,
        SignedUploadCommitPayload,
        SignedWritePayload,
        SyncUpsertPayload,
        UpsertFilePayload,
    )
    from .sync_core import (
        estimate_b64_size_bytes as _estimate_b64_size_bytes,
        is_image_path as _is_image_path,
        is_label_text_path as _is_label_text_path,
        normalize_path as _normalize_path,
        requires_explicit_lock as _requires_explicit_lock,
        sanitize_archive_member_path as _sanitize_archive_member_path,
        bbox_to_corners as _bbox_to_corners,
        is_bb_label_path as _is_bb_label_path,
        is_obb_label_path as _is_obb_label_path,
        label_paths_for_image as _label_paths_for_image,
        label_stem_from_path as _label_stem_from_path,
        parse_yolo_label_rows as _parse_yolo_label_rows,
        split_label_text_by_format as _split_label_text_by_format,
        can_delete_user as _can_delete_user_core,
        extract_bearer_token as _extract_bearer_token,
        hash_password as _hash_password,
        is_valid_bootstrap_token as _is_valid_bootstrap_token,
        run_shutdown as _run_shutdown_core,
        run_startup as _run_startup_core,
        verify_password as _verify_password,
        cleanup_old_backups as _cleanup_old_backups_core,
        dry_run_backup_restore as _dry_run_backup_restore_core,
        list_backups as _list_backups_core,
        safe_backup_path_from_name as _safe_backup_path_from_name_core,
        VALID_IMAGE_ACCESS_MODES as _VALID_IMAGE_ACCESS_MODES,
        VALID_IMAGE_STATUSES as _VALID_IMAGE_STATUSES,
        build_admin_image_items as _build_admin_image_items_core,
        build_search_clause as _build_search_clause_core,
        clamp_admin_images_limit as _clamp_admin_images_limit_core,
        clamp_admin_table_limit as _clamp_admin_table_limit_core,
        clamp_recent_changes_limit as _clamp_recent_changes_limit_core,
        clamp_sync_changes_limit as _clamp_sync_changes_limit_core,
        image_read_url as _image_read_url_core,
        is_remote_image_access_mode as _is_remote_image_access_mode,
        latest_change_seq as _latest_change_seq_core,
        latest_status_seq as _latest_status_seq_core,
        normalize_admin_image_sort as _normalize_admin_image_sort_core,
        normalize_cloudfront_invalidation_paths as _normalize_cloudfront_invalidation_paths,
        normalize_image_access_mode as _normalize_image_access_mode_value,
        normalize_image_status as _normalize_image_status_value,
        normalize_storage_mode as _normalize_storage_mode,
        online_users_count as _online_users_count_core,
        project_uses_s3_images as _project_uses_s3_images_core,
        s3_object_key as _s3_object_key_core,
        serialize_db_rows as _serialize_db_rows_core,
        sort_admin_image_items as _sort_admin_image_items_core,
        table_columns as _table_columns_core,
        table_exists as _table_exists_core,
        validate_table_name as _validate_table_name_core,
        cleanup_stale_sessions as _cleanup_stale_sessions_core,
        fetch_session_row as _fetch_session_row_core,
        row_to_session_payload as _row_to_session_payload_core,
        touch_session as _touch_session_core,
        compute_backup_retention_policy as _compute_backup_retention_policy_core,
        set_backup_retention_policy as _set_backup_retention_policy_core,
        setting_get as _setting_get_core,
        setting_set as _setting_set_core,
        collect_project_image_rows_from_db as _collect_project_image_rows_from_db_core,
        collect_project_image_rows_from_s3_manifest as _collect_project_image_rows_from_s3_manifest_core,
        fetch_project_image_status_map as _fetch_project_image_status_map_core,
        conflict_detail as _conflict_detail_core,
        find_lock_conflict as _find_lock_conflict_core,
        get_other_lock_holder as _get_other_lock_holder_core,
        holds_explicit_lock as _holds_explicit_lock_core,
        list_project_locks as _list_project_locks_core,
        release_session_locks as _release_session_locks_core,
        upsert_active_lock as _upsert_active_lock_core,
        delete_user_sessions_and_locks as _delete_user_sessions_and_locks_core,
        logout_session_token as _logout_session_token_core,
        owner_count_for_project as _owner_count_for_project_core,
        delete_image_status_for_path as _delete_image_status_for_path_core,
        insert_change_record as _insert_change_record_core,
        max_change_seq as _max_change_seq_core,
        touch_session_last_seen as _touch_session_last_seen_core,
        upsert_file_record as _upsert_file_record_core,
        fetch_changes_since as _fetch_changes_since_core,
        map_change_rows as _map_change_rows_core,
        build_sync_status_payload as _build_sync_status_payload_core,
        build_sync_status_response as _build_sync_status_response_core,
        fetch_project_summary_rows as _fetch_project_summary_rows_core,
        fetch_recent_changes as _fetch_recent_changes_core,
        map_recent_changes as _map_recent_changes_core,
    )

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except Exception:  # pragma: no cover - optional dependency in local dev
    Image = None
    ImageOps = None

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


# -------------------------
# DB + security helpers
# -------------------------

def _now_ms() -> int:
    return int(time.time() * 1000)


def _utc_now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


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


def _is_s3_enabled() -> bool:
    return bool(S3_BUCKET and boto3 is not None)


def _s3_object_key(project_id: str, path: str) -> str:
    return _s3_object_key_core(S3_PREFIX, project_id, path)


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


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


_CONN = _connect()


