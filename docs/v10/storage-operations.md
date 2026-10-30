# Storage operations (Cloudflare R2)

**Status:** verified against a local S3-compatible gateway (SeaweedFS, with access keys) in Docker, and an in-process S3 mock in tests. **No R2 bucket was available.** MinIO images could not be pulled here, so SeaweedFS stood in.

Checks run with `ops.storage_setup` against the local gateway:

| Check | Result |
|---|---|
| CORS for the app origin | applied |
| Abort-incomplete-uploads lifecycle rule | applied |
| Object round trip | true |
| Anonymous read refused | true |
| Presigned GET | works |
| Presigned expiry | enforced |

The browser also uploaded directly with a presigned PUT, and thumbnails loaded through presigned redirects (browser QA V10).

## Set up R2
1. Cloudflare dashboard → R2 → Create bucket (e.g. `context-commerce-media`). Leave **public access off** and attach no custom domain: the bucket must stay private.
2. R2 → Manage API tokens → create a token with **Object Read & Write** on that bucket only. Copy the Access Key ID and Secret.
3. In `deploy/.env`:
   ```
   CC_S3_BUCKET=context-commerce-media
   CC_S3_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
   CC_S3_REGION=auto
   CC_S3_SSE=                 # R2 encrypts at rest and does not need the AES256 header
   AWS_ACCESS_KEY_ID=…
   AWS_SECRET_ACCESS_KEY=…
   ```
4. Apply CORS and lifecycle, and verify privacy (exits non-zero on any failure):
   ```bash
   docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env run --rm api \
     python -m ops.storage_setup --app-origin https://app.example.com --apply
   ```
   CORS allows only the app origin, for PUT, GET and HEAD with the `Content-Type` header. Direct uploads need this; downloads are top-level navigations and do not.

## How media is stored
- **Keys:** `uploads/…` (source videos, product images, GLB models), `frames/…` (keyframes), `analyses/…` (tracks), `outputs/…` (renders). Set `CC_S3_PREFIX` to share a bucket between environments.
- **Write-through:** every finished file is uploaded right after it is written. Any host restores a missing file on first use.
- **Reads:** the API authorises, then redirects (307) to a presigned GET valid for `CC_S3_PRESIGN_TTL` (300 s). Revoking a licence stops new links at once; a link already issued works until it expires (at most 5 minutes).
- **Direct uploads:** presigned PUT URLs live at most 1 hour and are bound to one key and one content type. Completion checks the size against the declaration (a mismatch is deleted). Abandoned sessions expire, and their objects are deleted by maintenance.
- **Deletion:** cancelled uploads, failed completions and expired sessions delete their objects. Media of an active video is never deleted automatically. Deletion of a workspace's media on request is an operator procedure ([data-protection.md](data-protection.md)).

## Local cache on the VM
Workers and the API keep local copies under `/data/{uploads,frames,analyses,outputs}`. The cache cleanup ([worker-deployment.md](worker-deployment.md)) evicts copies that are confirmed in the bucket with the same size and unused for an hour. A file whose bucket copy is missing is never evicted.

## Availability from several processes
The API and both workers in the compose stack read and write the same bucket concurrently (10-organization test). Writes go to unique keys (random suffixes, per-job output names), so concurrent processes never overwrite each other's objects.
