# Production runtime: Neon PostgreSQL, private object storage, Python 3.11+ (V9)

## Database: Neon
- **Select:** setting `DATABASE_URL` (or `CC_DATABASE_URL`) to a PostgreSQL connection string switches the whole app (API, worker, ops tools) from SQLite to PostgreSQL. Without it, SQLite in `CC_DATA_DIR` is used as before.
- **Neon setup:** create a project in the Neon console, open **Connect**, and copy the connection string (the pooled `-pooler` host is fine) into `.env`. Keep `sslmode=require`. The schema is created and migrated automatically on first start; concurrent API and worker starts serialise on an advisory lock.
- **Adapter** ([app/db.py](../../app/db.py)): application SQL stays in one portable dialect. For PostgreSQL:
  - `?` placeholders become `%s`;
  - `CURRENT_TIMESTAMP` keeps SQLite's UTC text format;
  - inserts return generated ids;
  - `INTEGER`/`REAL` map to `BIGINT`/`DOUBLE PRECISION`;
  - critical sections take advisory locks;
  - connections come from a pool, with server-side prepared statements disabled so Neon's PgBouncer pooler is safe.
- **SQLite-only constructs removed:**
  - `INSERT OR REPLACE` became `ON CONFLICT DO UPDATE`;
  - `last_insert_rowid()` became the returned id;
  - `COLLATE NOCASE` became `lower()`;
  - `CAST(x AS INTEGER)` equality became a range predicate, because PostgreSQL rounds where SQLite truncates;
  - `CASE WHEN ?` became `CASE WHEN ?=1`;
  - `PRAGMA table_info` became `information_schema`.
- **Pool size:** `CC_DB_POOL_SIZE` (default 10) per process.
- **Latency:** every request makes several round trips. Run the app in the same region as the Neon project.

### Moving existing data to Neon
```
# 1. Point DATABASE_URL at the new, empty Neon database (in .env or the shell).
# 2. Copy the SQLite database, verified table by table:
python -m ops.dbtransfer migrate --sqlite ./data/context_commerce.sqlite3 --keep-dump ./backups/pre-neon
# 3. If you use object storage, upload the existing media:
python -m ops.dbtransfer push-media --data ./data
```
`migrate` refuses a non-empty target. It preserves ids and resets identity sequences so new rows continue after the old ones. It compares row counts and an order-independent content hash for every table, and stops on any difference.

## Object storage (S3-compatible, private)
Set `CC_S3_BUCKET` and the provider's credentials (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, plus `CC_S3_ENDPOINT_URL` for R2, B2 or MinIO). Code: [app/storage.py](../../app/storage.py).
- **Write-through:** uploads, keyframes, analyses (tracks) and validated renders are stored in the bucket when they are created. Uploads fail rather than half-succeed if the bucket is unreachable. Objects use `ServerSideEncryption=AES256` unless `CC_S3_SSE` is empty.
- **No shared disk needed:** a host that lacks a file (another API replica, or a worker on another machine) restores it from the bucket on first use. The local data directory becomes a cache.
- **Delivery:** downloads, video streams and keyframes are authorised first (rights, licence, validation, organisation), then answered with a `307` redirect to a presigned URL valid for `CC_S3_PRESIGN_TTL` seconds (default 300). The bucket itself must stay private.
- **Recommended bucket settings** (not configurable from the app): block public access, enable versioning, add a lifecycle rule for old versions.

## Python runtime
- The supported runtime is **Python 3.11 or newer**; the Docker image uses 3.12.
- On this machine the suite runs on Python 3.11.0 (`.venv311`) with Pillow 13.0.0, which closes the 13 Pillow advisories open in V8.
- Python 3.10 still installs, using the environment-marked Pillow 12.3.0 pin, but is not supported for production.

## Running it
```
cp .env.example .env            # fill DATABASE_URL, CC_S3_*, GOOGLE_*, CC_SMTP_*, CC_PUBLIC_URL, CC_SIGNING_KEY
docker compose up --build       # API + worker; both read .env
```
- **Multiple hosts:** set `CC_SIGNING_KEY` to the same long random value on every host. It signs download links, sign-in codes and OAuth state.
- **HTTPS:** set `CC_SECURE_COOKIES=1`; the image starts uvicorn with `--proxy-headers`.

## Backup and recovery
| Layer | Mechanism |
|---|---|
| Neon | Point-in-time restore and branching (managed by Neon); this is the first line of recovery |
| Independent copy | `python ops/backup.py backup --data <dir> --out <dir>` runs a consistent logical export of the database plus media, with a SHA-256 manifest. With `DATABASE_URL` set it exports PostgreSQL; otherwise it copies SQLite |
| Restore | `python ops/backup.py restore --backup <dir> --data <empty dir>` verifies every hash, refuses a non-empty directory **or** database, imports, verifies every table and relocates media paths |
| Bucket | Versioning, plus provider replication if required |

**Drills executed in this environment** (local PostgreSQL 16 standing in for Neon):
- backup and restore of a rendered placement into a fresh schema, plus refusal of a corrupted backup and of a populated target (`tests/test_v8.py::test_backup_restore_round_trip` in PostgreSQL mode);
- SQLite → PostgreSQL → SQLite round trip (`tests/test_dbtransfer.py`);
- a CLI `migrate` of a copy of the local development database, which held only 6 rows (27 tables verified);
- object-storage write-through, restore on an empty disk, presigned delivery and `push-media` (`tests/test_storage.py`, in-process S3 mock).

**Not executed here:** against a real Neon project and a real bucket. No credentials were available; see the release readiness document for the commands to run once they are configured.
