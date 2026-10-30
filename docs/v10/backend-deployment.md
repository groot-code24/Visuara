# Backend deployment (API)

**Status:** the image builds and runs; the API and two workers ran under Docker Compose against PostgreSQL 16 and an S3 gateway for the browser QA and the 10-organization test. **No production VM, Neon project, R2 bucket or domain has been used.**

## Host
A Linux VM with Docker Engine + Compose v2. Start with 4 vCPU and 8 GB RAM (one API container, 1–2 workers), and 40 GB+ of disk for the media cache. Open ports 80 and 443 only (Caddy), or none (Cloudflare Tunnel).

## First-time setup
```bash
git clone <your private repo> ~/context-commerce && cd ~/context-commerce
cp deploy/env.production.example deploy/.env && chmod 600 deploy/.env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # → CC_SIGNING_KEY, and again for CC_OPS_TOKEN
# fill DATABASE_URL (Neon), CC_S3_* (R2), GOOGLE_*, CC_SMTP_*, domains — see database-operations.md, storage-operations.md
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env --profile caddy up -d --build
#   or: --profile tunnel (set CLOUDFLARE_TUNNEL_TOKEN; route api.example.com → http://api:8000 in Cloudflare)
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env run --rm api python -m ops.storage_setup --app-origin https://app.example.com --apply
curl -fsS https://api.example.com/api/health/ready
```
With `CC_ENV=production` the containers refuse to start if:
- `DATABASE_URL` or `CC_S3_BUCKET` is missing;
- the outbox mail backend is selected;
- `CC_SIGNING_KEY` is shorter than 32 characters;
- `CC_SECURE_COOKIES` is not `1`;
- `CC_PUBLIC_URL`, `CC_APP_URL` or the CORS origins are not `https://`;
- the inline worker is on.

The error lists every problem.

## Container details
- Runs as non-root (`cc`, uid 10001) under `tini`, which forwards signals.
- Uvicorn with `--proxy-headers` and a 30 s graceful shutdown. Set `FORWARDED_ALLOW_IPS` so client IPs come from the proxy.
- Limits: API 1 CPU / 1 GB; workers `CC_WORKER_CPUS` / `CC_WORKER_MEM_LIMIT`.
- Health check: `/api/health/live`, which never touches the database.
- Logs: JSON on stdout, rotated by Docker (20 MB × 5).
- Restart policy `unless-stopped`.

## Rollout, rollback, migration
- `scripts/deploy.sh <sha>`:
  1. builds an image tagged with the commit;
  2. runs `python -m ops.migrate` with the new image; a failure stops here;
  3. restarts the API and workers;
  4. waits for readiness; if it does not come, rolls back to the previous tag automatically.
- The GitHub workflow `deploy-backend.yml` runs it over SSH after CI passes on `main` (secrets `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`). **Not exercised.**
- `scripts/rollback.sh [tag]` switches back to a previous image. Migrations are additive, so older images run on a newer schema. For a destructive change, restore a Neon branch ([backup-and-recovery.md](backup-and-recovery.md)).

## Failure behaviour (verified in tests or the compose stack)
| Failure | Behaviour |
|---|---|
| Database unreachable | Readiness 503 `database: unavailable`; requests return a generic 500 with a request id; no connection string in any response |
| Storage unreachable | Readiness 503 `object_storage: unavailable`; upload completion returns 503 `STORAGE_UNAVAILABLE` (retryable) |
| SMTP failure | `502 EMAIL_SEND_FAILED` with a generic message; the challenge is voided; `email.failed` is counted in metrics |
| Google token or signature failure | Sign-in page error; `auth.google_failed` is counted |
| Worker crash or SIGTERM | Lease recovery by another worker; SIGTERM hands the job back immediately |
| FFmpeg failure or invalid output | Bounded retries, then `FAILED` or `DEAD_LETTER`; the placement is marked `RENDER_FAILED` |
