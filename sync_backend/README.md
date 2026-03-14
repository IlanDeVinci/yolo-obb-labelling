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
cp .env.example .env
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
- `SYNC_BOOTSTRAP_TOKEN` (optional but recommended in production)
- `SYNC_S3_BUCKET` (optional, enables S3 storage for image files)
- `SYNC_S3_PREFIX` (folder prefix inside bucket, default `datasets/pokemon`)
- `SYNC_S3_REGION` (optional, e.g. `eu-west-3`)

## S3 Image Storage (Cloud Mode)

When `SYNC_S3_BUCKET` is set, image files (`.jpg/.jpeg/.png/.bmp/.tiff/.tif/.webp`) are stored in S3 instead of the SQLite blob store.

Object keys are always stored under a folder prefix, never at bucket root:

```text
<SYNC_S3_PREFIX>/<project_id>/<relative_path>
```

For your bucket:

```env
SYNC_S3_BUCKET=yolo-datasets-pokemon
SYNC_S3_PREFIX=datasets/pokemon
SYNC_S3_REGION=eu-west-3
```

Example key:

```text
datasets/pokemon/yolo-pokemon/images/IMG_1023.JPG
```

Make sure the backend runtime has AWS credentials with `s3:GetObject`, `s3:PutObject`, and `s3:DeleteObject` for that prefix.

## Production publish with NPM

Use this flow on VPS when your backend is attached to shared `proxy_net`.

- Set a strong bootstrap token in compose env.
- Example: `SYNC_BOOTSTRAP_TOKEN=change-me-long-random-token`
- Deploy backend:
- `cd sync_backend`
- `docker compose up -d --build`
- In Nginx Proxy Manager, create proxy host for your sync domain.
- `Scheme`: `http`
- `Forward Hostname / IP`: `yolo-sync-backend`
- `Forward Port`: `8095`
- `Block Common Exploits`: enabled
- `Websockets`: enabled
- Get Let's Encrypt cert in NPM for that domain.
- Bootstrap first admin/project using header `X-Bootstrap-Token`.

PowerShell example:

```powershell
$headers = @{ 'X-Bootstrap-Token' = 'change-me-long-random-token' }
$body = @{
  projectId = 'my-project'
  projectPassword = 'project-secret'
  username = 'ilan'
  password = 'user-secret'
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri 'https://sync.your-domain.com/api/admin/bootstrap' -Headers $headers -Body $body -ContentType 'application/json'
```

After bootstrap succeeds, keep the token set. It still protects the endpoint against unwanted bootstrap attempts for new project IDs.

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
