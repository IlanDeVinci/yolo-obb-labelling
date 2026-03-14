# Cloud-Only Images Mode

## Overview

Cloud-only mode keeps image source-of-truth in S3 and avoids permanent local project copies. Label files and sync metadata remain in the existing sync flow.

## Backend changes

- Added project setting: `imageAccessMode = local|hybrid|cloud_only`.
- Added signed URL endpoints:
  - `GET /api/images/signed-read?path=...`
  - `POST /api/images/signed-write`
- Added image listing endpoint:
  - `GET /api/images/manifest`
- Added prefetch endpoint:
  - `POST /api/images/prefetch`
- Added auth enforcement per session/project for all image URL APIs.
- Preserved lock semantics for label edits.
- Optional CloudFront read URL mode via `SYNC_CLOUDFRONT_BASE_URL`.

## Desktop changes

- Added cloud image provider abstraction + cache layer.
- In `cloud_only`, image browser can load from manifest even with no local image files.
- On image open, app downloads to temp cache and renders from cache.
- Background prefetch for next image batch.
- Offline + non-cached image shows clear non-blocking error.
- Cache controls:
  - `Cloud Sync Settings...` for location/max size/TTL/prefetch count.
  - `Cloud > Clear Cloud Image Cache`.
- Added `Cloud > Upload Local Images to S3` for one-shot migration.
- Cache is kept outside project folders by default.

## Security notes

- Signed URLs only, short expiry (`SYNC_SIGNED_URL_TTL_SECONDS`).
- Client does not require long-lived bucket credentials.
- Path normalization is enforced on backend (`_normalize_path`).

## Migration notes

- Existing databases auto-migrate on backend startup.
- Added columns:
  - `projects.image_access_mode`
  - `users.role`, `users.created_by`
  - `sessions.role`
- Projects without an owner get oldest admin promoted to owner.
- Existing storage mode values are used to infer initial image access mode.

## Recommended IAM scope

Limit backend role to project prefix only:

- `s3:GetObject`
- `s3:PutObject`
- `s3:DeleteObject`
- `s3:ListBucket` (prefix-scoped)

## Test plan

1. Online baseline

- Login as standard user in `cloud_only` project.
- Confirm manifest image list loads without local image files.
- Open several images; verify download progress and rendering.

1. Offline behavior

- Open one image online first (ensure cached).
- Disconnect network.
- Re-open cached image: should succeed.
- Open never-cached image: should show clear non-blocking error.

1. Stale cache refresh

- Replace an image in S3 (etag/lastModified changes).
- Open image again and verify cache is refreshed.

1. Concurrent editing

- Two users edit labels and switch active files.
- Verify explicit lock behavior remains unchanged.

1. Large dataset

- Use large manifest (thousands of images).
- Verify listing/prefetch remain responsive.

1. Permission model

- User deletes self account: should succeed.
- Admin deletes a user they created: should succeed.
- Admin deletes unrelated user: should fail.
- Owner deletes any non-last-owner user: should succeed.
