#!/usr/bin/env bash
# Rolls the web image back to a previously built/tagged version.
#
# Usage: ./rollback.sh <image-tag>
# Assumes update.sh (or CI) tagged prior images, e.g.:
#   docker tag dtbeach-web:latest dtbeach-web:2026-07-17
set -euo pipefail
cd "$(dirname "$0")"

if [ $# -ne 1 ]; then
    echo "Usage: $0 <previous-image-tag>" >&2
    echo "List available tags with: docker images | grep dtbeach-web" >&2
    exit 1
fi
TAG="$1"

echo "==> Retagging dtbeach-web:$TAG as dtbeach-web:latest"
docker tag "dtbeach-web:$TAG" dtbeach-web:latest

echo "==> Recreating web container from rolled-back image"
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --no-deps web

echo "==> Rollback complete. Verify with: docker compose -f docker-compose.prod.yml logs -f web"
echo "NOTE: if the rollback needs a schema downgrade too, restore.sh from a matching backup instead."
