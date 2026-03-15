from __future__ import annotations

SYNC_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  password_hash TEXT NOT NULL,
  storage_mode TEXT NOT NULL DEFAULT 'auto',
  image_access_mode TEXT NOT NULL DEFAULT 'local',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL,
  username TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0,
  role TEXT NOT NULL DEFAULT 'user',
  created_by TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(project_id, username)
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  username TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  is_admin INTEGER NOT NULL DEFAULT 0,
  active_file TEXT,
  created_at INTEGER NOT NULL,
  last_seen INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS locks (
  project_id TEXT NOT NULL,
  path TEXT NOT NULL,
  token TEXT NOT NULL,
  username TEXT NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(project_id, path)
);
CREATE TABLE IF NOT EXISTS files (
  project_id TEXT NOT NULL,
  path TEXT NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0,
  mtime_ms INTEGER NOT NULL,
  sha1 TEXT NOT NULL,
  content_base64 TEXT NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(project_id, path)
);
CREATE TABLE IF NOT EXISTS changes (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL,
  username TEXT NOT NULL,
  source_token TEXT NOT NULL,
  path TEXT NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0,
  mtime_ms INTEGER NOT NULL,
  sha1 TEXT NOT NULL,
  content_base64 TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS image_label_index (
  project_id TEXT NOT NULL,
  image_stem TEXT NOT NULL,
  image_name TEXT NOT NULL,
  bb_label_path TEXT NOT NULL DEFAULT '',
  obb_label_path TEXT NOT NULL DEFAULT '',
  bb_label_text TEXT NOT NULL DEFAULT '',
  obb_label_text TEXT NOT NULL DEFAULT '',
  bb_label_rows INTEGER NOT NULL DEFAULT 0,
  obb_label_rows INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(project_id, image_stem)
);
CREATE TABLE IF NOT EXISTS image_status (
  project_id TEXT NOT NULL,
  image_name TEXT NOT NULL,
  status TEXT NOT NULL,
  updated_at INTEGER NOT NULL,
  updated_by TEXT NOT NULL,
  PRIMARY KEY(project_id, image_name)
);
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);
"""
