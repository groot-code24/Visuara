# Test evidence

Results from 3 November 2026, on the code in this repository. Nothing has been deployed to Vercel yet.

| Check | Result |
|---|---|
| Backend tests, PostgreSQL 16 | 114 passed |
| Backend tests, SQLite | The last full run failed only in `tests/test_blob.py` (a fixture gap, since fixed). That file then gave 32 passed, 1 skipped (it needs PostgreSQL) |
| `qa/browser_qa_web.py`, Chrome, sign-up to download | 12/12, no page errors |
| `qa/vercel_rehearsal.py` ([log](evidence/vercel_rehearsal.json)) | 14/14, no CSP violations or page errors |

## The rehearsal
It runs containers from `Dockerfile.vercel` with production settings: API and runner share no disk, the filesystem is read-only except `/tmp`, each has 1 vCPU and 2 GB, routing is read from `vercel.json`, and it uses HTTPS, the 4.5 MB request limit, Chrome, and Vercel's own JavaScript and Python Blob SDKs. It includes killing the runner mid-render (`claimed, lease_recovered, claimed, succeeded`) and a 36 MB upload in 5 parts.

**What stands in:** PostgreSQL for Neon, `qa/blob_stub.py` for Blob, Mailpit for email. Google was checked only as far as the redirect in this run; `qa/browser_qa_web.py` runs the whole flow against `qa/google_stub.py`.

**What it doesn't cover:**
- Chrome's HTTP/2 streaming upload to real Blob, because the stand-in forces the SDK's non-streaming path.
- The tighter content security policy used on Vercel, because the stand-in runs on a local address. Its destinations come from `@vercel/blob` 2.8.2's source.

**One earlier rehearsal run failed before the product's logic started:** the first health request timed out while the machine was slow (48 s to start, against 7.7 s on the passing run).

## Real services
Read-only checks with the real credentials, using `qa/check_env.py`:
- **Neon:** connection through the pooled address, and the advisory lock the app relies on.
- **Google:** the client ID and secret were accepted (`invalid_grant` for a code that cannot exist). A real sign-in hasn't been completed.

Blob, email delivery and Vercel itself haven't been exercised for real.

## Bugs these runs found
- **Start-up too strict:** unfinished email or Blob settings stopped the whole API, so Google sign-in failed too. Each now turns off only its own feature. The production image was run with those gaps: the API started, `/api/auth/providers` offered Google only, and `/auth/google/start` redirected to Google.
- **Lost leases:** a render renewed its lease only while processing frames, so a slow download, plan or upload could lose it, and dashboard polling would then restart the job until it was given up. The lease now renews from its own thread (`main.keep_lease`).
- **Backups:** the `schema_fingerprint` table was copied into logical backups, which broke restore and migration on PostgreSQL.

## Not repeated on this code
The 10-workspace load test and outage drill were removed along with the VM deployment they exercised. There has been no independent penetration test.
