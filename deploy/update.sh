#!/usr/bin/env bash
# Controlled update: pulls new code (assumes it's already on the server,
# e.g. via git pull done by the operator), rebuilds, and restarts with
# zero manual DB steps (migrations run automatically on container start).
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Rebuilding web image"
docker compose -f docker-compose.prod.yml --env-file .env.production build web

echo "==> Recreating web container (db and caddy untouched)"
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --no-deps web

echo "==> Update complete. Check logs with: docker compose -f docker-compose.prod.yml logs -f web"
