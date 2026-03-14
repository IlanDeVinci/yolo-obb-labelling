# YOLO Sync Backend

Standalone backend for `yolo-obb-labelling` collaborative sync.

Features:

- Project auth (`project_id` + `project password`)
- Per-user auth (`username` + `password`)
- Per-file locking with active file lease
- Incremental change feed for clients
- Daily SQLite backups + retention cleanup
- Minimal web admin UI

## Run with Docker

```bash
cd sync_backend
docker compose up -d --build
```

Backend will listen on `http://localhost:8095`.

Open UI:

```text
http://localhost:8095/
```

## Environment variables

- `SYNC_DB_PATH` (default `/data/sync.db` in container)
- `SYNC_BACKUP_DIR` (default `/data/backups`)
- `SYNC_SESSION_TTL_SECONDS` (default `45`)
- `SYNC_BACKUP_RETENTION_DAYS` (default `14`)
- `SYNC_MAX_FILE_BYTES` (default `8388608`)

## API used by desktop app

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `POST /api/auth/heartbeat`
- `POST /api/locks/activate`
- `GET /api/locks`
- `POST /api/sync/upsert`
- `GET /api/sync/changes`
- `GET /api/sync/status`

## Lock behavior

- A user has one active lock at a time.
- Activating a new file lock releases previous one.
- Locks auto-release when session heartbeat expires.
- Label files (`.../labels/.../*.txt`) require explicit lock ownership to push changes.
