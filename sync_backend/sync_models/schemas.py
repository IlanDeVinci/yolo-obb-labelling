from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field


class BootstrapPayload(BaseModel):
    projectId: str = Field(min_length=2, max_length=120)
    projectPassword: str = Field(min_length=4, max_length=256)
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=4, max_length=256)


class LoginPayload(BaseModel):
    projectId: str
    projectPassword: str = ""
    username: str
    password: str


class ProjectOptionsPayload(BaseModel):
    username: str
    password: str


class CreateUserPayload(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=4, max_length=256)
    isAdmin: bool = False


class SignedWritePayload(BaseModel):
    path: str
    contentType: str | None = None


class SignedUploadCommitPayload(BaseModel):
    path: str
    sha1: str = ""
    sizeBytes: int = Field(default=0, ge=0)
    mtimeMs: int = Field(default=0, ge=0)


class PrefetchBatchPayload(BaseModel):
    currentPath: str | None = None
    count: int = Field(default=10, ge=1, le=200)


class ProjectImageAccessPayload(BaseModel):
    imageAccessMode: str = Field(pattern="^(local|hybrid|cloud_only)$")


class ImageStatusPayload(BaseModel):
    imageName: str = Field(min_length=1, max_length=512)
    status: str = Field(pattern="^(in_progress|completed|yolo|to_rotate)$")


class ImageStatusSyncItem(BaseModel):
    imageName: str = Field(min_length=1, max_length=512)
    status: str = Field(pattern="^(in_progress|completed|yolo|to_rotate)$")


class AdminImageStatusSyncPayload(BaseModel):
    items: list[ImageStatusSyncItem] = Field(default_factory=list, max_length=50000)


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


class ProjectStoragePayload(BaseModel):
    storageMode: str = Field(pattern="^(auto|db|s3)$")


class AdminImageDeletePayload(BaseModel):
    paths: list[str] = Field(default_factory=list)
    deleteLabels: bool = True


class AdminImageStatusReconcilePayload(BaseModel):
    defaultStatus: str = Field(default="in_progress", pattern="^(in_progress|completed|yolo|to_rotate)$")
    removeOrphans: bool = False


class AdminNormalizeImagesPayload(BaseModel):
    paths: list[str] = Field(default_factory=list)
    quality: int = Field(default=90, ge=60, le=95)


class BackupRetentionPayload(BaseModel):
    retentionValue: int = Field(default=14, ge=1, le=3650)
    retentionUnit: str = Field(default="days", pattern="^(days|months)$")


class BackupRestorePayload(BaseModel):
    backupName: str = Field(min_length=1, max_length=255)
    confirmText: str = Field(min_length=1, max_length=300)


class BackupDryRunPayload(BaseModel):
    backupName: str = Field(min_length=1, max_length=255)


class DatabaseTableQueryPayload(BaseModel):
    table: str = Field(min_length=1, max_length=120)
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
    search: str = Field(default="", max_length=250)
    searchColumn: str = Field(default="", max_length=120)


@dataclass
class SessionContext:
    token: str
    project_id: str
    username: str
    role: str
    is_admin: bool
    active_file: str | None
