#!/usr/bin/env bash
# Production rollout on the backend VM (run from the repository checkout). Usage: scripts/deploy.sh [git-sha]
#  1. checks out the requested commit and builds an image tagged with it;
#  2. runs the schema migration with the NEW image (a failure stops the rollout before anything changes);
#  3. restarts the API and workers on the new image; workers finish or hand back their current job (graceful stop);
#  4. waits for /api/health/ready; if the new version is not ready in time, rolls back to the previous image.
# The previous tag is kept in .deploy/previous_tag for scripts/rollback.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
SHA="${1:-$(git rev-parse HEAD)}"
COMPOSE=(docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env)
mkdir -p .deploy
PREV="$(cat .deploy/current_tag 2>/dev/null || true)"

git fetch --quiet origin || true
git checkout --quiet "$SHA"
TAG="$(git rev-parse --short=12 HEAD)"
echo "deploying $TAG (previous: ${PREV:-none})"
CC_IMAGE_TAG="$TAG" "${COMPOSE[@]}" build --quiet api
docker tag "context-commerce:$TAG" context-commerce:candidate

echo "migrating schema with the new image"
CC_IMAGE_TAG="$TAG" "${COMPOSE[@]}" run --rm --no-deps api python -m ops.migrate

CC_IMAGE_TAG="$TAG" "${COMPOSE[@]}" up -d --no-build api worker
for i in $(seq 1 45); do
  if curl -fsS http://127.0.0.1:8000/api/health/ready > /dev/null 2>&1; then
    echo "$TAG" > .deploy/current_tag; [ -n "$PREV" ] && echo "$PREV" > .deploy/previous_tag
    echo "deployed $TAG"; exit 0
  fi
  sleep 2
done
echo "new version not ready after 90 s" >&2
if [ -n "$PREV" ]; then
  echo "rolling back to $PREV" >&2
  CC_IMAGE_TAG="$PREV" "${COMPOSE[@]}" up -d --no-build api worker
fi
exit 1
