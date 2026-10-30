#!/usr/bin/env bash
# Roll the API and workers back to the previously deployed image. Usage: scripts/rollback.sh [tag]
# Schema changes are additive (new tables/columns only), so an older image runs against a newer schema.
# If a release ever needs a destructive migration, restore the database from a Neon branch instead (docs/v10/backup-and-recovery.md).
set -euo pipefail
cd "$(dirname "$0")/.."
TAG="${1:-$(cat .deploy/previous_tag 2>/dev/null || true)}"
[ -n "$TAG" ] || { echo "no previous tag recorded; pass one explicitly (docker images context-commerce)" >&2; exit 1; }
docker image inspect "context-commerce:$TAG" > /dev/null
CC_IMAGE_TAG="$TAG" docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env up -d --no-build api worker
for i in $(seq 1 45); do
  curl -fsS http://127.0.0.1:8000/api/health/ready > /dev/null 2>&1 && { echo "$TAG" > .deploy/current_tag; echo "rolled back to $TAG"; exit 0; }
  sleep 2
done
echo "rollback target not ready" >&2; exit 1
