from __future__ import annotations

import os
from pathlib import Path

DB_PATH = Path(os.environ.get("SYNC_DB_PATH", "./data/sync.db")).resolve()
BACKUP_DIR = Path(os.environ.get("SYNC_BACKUP_DIR", "./data/backups")).resolve()
SESSION_TTL_SECONDS = max(20, int(os.environ.get("SYNC_SESSION_TTL_SECONDS", "900")))
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
CLOUDFRONT_DISTRIBUTION_ID = os.environ.get("SYNC_CLOUDFRONT_DISTRIBUTION_ID", "").strip()
_NORMALIZE_JOB_RETENTION_MS = 2 * 60 * 60 * 1000

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
