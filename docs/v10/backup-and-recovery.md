# Backup and recovery

## What protects what
| Asset | Primary protection | Secondary |
|---|---|---|
| Database (Neon) | Neon point-in-time restore (history window per plan; the Free plan keeps about 6 h / 1 GB of changes) into a **new branch** | Logical backups with `ops/backup.py` (consistent export, SHA-256 manifest), kept off-site |
| Media (R2) | Write-once keys (new versions get new keys; nothing is overwritten) | Periodic copy of the bucket to a second bucket or provider (`rclone sync`), or provider replication |
| Configuration and secrets | `deploy/.env` kept in a password manager or secret store (never in git) | Re-creatable from the provider consoles |
| Code and images | Git history; images tagged by commit (`scripts/deploy.sh`) | `scripts/rollback.sh` |

## Logical backup and restore (any PostgreSQL, including Neon)
```bash
# backup: run with the production environment (inside the api container on the VM)
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env exec api python ops/backup.py backup --data /data --out /data/backups/$(date +%F)
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env exec api python ops/backup.py verify --backup /data/backups/<date>
# copy /data/backups/<date> off the VM (it holds customer data: encrypt it at rest)

# restore into an EMPTY database (a new Neon branch or database), never over live data
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env run --rm -e DATABASE_URL='<url of the empty branch>' api \
  python ops/backup.py restore --backup /data/backups/<date> --data /data/restored
```
`restore` verifies every hash first and refuses a non-empty target. It rewrites stored media paths to the new data directory; media itself is restored from the bucket on first use.

## Neon point-in-time restore (first choice after a bad change)
Neon console → Branches → **Restore** (or create a branch "as of" a timestamp before the incident) → point `DATABASE_URL` at the new branch's pooled URL → `scripts/deploy.sh` (or restart) → verify → make it the primary branch.

## Drill
The [test evidence](test-evidence.md) records a backup and restore drill on the local PostgreSQL 16 stack after the 10-organization workload: backup → verify → restore into a fresh database → row counts compared table by table. A drill on **Neon itself** (branch restore and logical restore into an empty branch) is part of gate D and has not been done.

Recommended schedule: a logical backup daily (cron on the VM) plus a restore drill monthly into a scratch Neon branch, recording counts and timing.
