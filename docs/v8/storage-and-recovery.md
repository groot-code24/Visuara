# Storage, delivery and recovery

## What is implemented
- **Private by default.** All media (uploads, derived assets, 3D models, analyses, renders) live under `CC_DATA_DIR`. No static route exposes it. Every read goes through an authorised endpoint.
- **Original creator footage.** It is streamed only to the owning organization and to organizations holding an *active* licence grant (`GET /api/videos/{id}/stream`). Marketplace visitors see analysed keyframes only.
- **Derived assets.** Product originals are never modified. A derived sRGB PNG and an asset report are stored alongside them.
- **Short-lived links.** `POST /api/jobs/{id}/download-link` returns `/api/media/<token>`, an HMAC-SHA256 token bound to job, user and placement version, valid for `CC_DOWNLOAD_LINK_TTL` (default 300 s). Every delivery gate (rights, approvals, licence, validation, version) is **re-checked when the link is used**. Tokens are redacted from uvicorn access logs. The key comes from `CC_SIGNING_KEY`, or a random 32-byte key in `CC_DATA_DIR/.signing_key`, created owner-only where the OS allows.
- **Temporary render files** are named per lease and removed on every failure path.

## Not implemented (blocked)
- S3-compatible object storage, server-side encryption, object versioning, lifecycle and retention rules. Docker was not running and no cloud credentials were provided, so no object-store adapter could be written *and verified*. The plan-based renderer and `ops/backup.py` keep file I/O in a few places to ease the migration.
- Encryption at rest for the local disk (use full-disk encryption on the host).
- Retention and deletion policies for creator media after licence expiry (manual today).

## Backup and restore (local procedure, tested)
```bash
python ops/backup.py backup  --data ./data --out ./backups/$(date +%F)       # safe while API and workers run
python ops/backup.py verify  --backup ./backups/2026-10-30                     # re-hash every file
python ops/backup.py restore --backup ./backups/2026-10-30 --data ./data-restored
```
- **Database:** SQLite online-backup API on a read-only connection, so a consistent snapshot is taken even during writes.
- **Media:** `uploads/`, `outputs/`, `frames/`, `analyses/` copied with SHA-256 in `manifest.json`. In-progress `*.part.mp4` files are excluded.
- **Restore:** verifies every hash first (any mismatch aborts) and refuses a non-empty target. It rewrites stored absolute paths from the old data directory to the new one, then runs `PRAGMA integrity_check`.
- **Signing key:** `.signing_key` is deliberately **not** backed up. After a restore, outstanding links become invalid and new ones are issued.

**Evidence:** `tests/test_v8.py::test_backup_restore_round_trip` covers the full sequence: real data via the API, then backup, then detection of a corrupted file (restore refused), then a clean restore. The relocated output's SHA-256 equals the original download, and user records are intact. This is a **locally tested** recovery, not a verified production recovery.

**Recommended frequency (not automated here):** database hourly, media daily with 30-day retention, plus a monthly restore drill into a scratch directory.
