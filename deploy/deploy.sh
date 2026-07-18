#!/usr/bin/env bash
# First-time production deployment on a remote Linux server with Docker
# Engine + Docker Compose already installed.
#
# Usage (run from the deploy/ directory on the server, after copying the
# repository there and creating deploy/.env.production from the example):
#   ./deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env.production ]; then
    echo "Missing deploy/.env.production — copy .env.production.example and fill in real values first." >&2
    exit 1
fi

echo "==> Building images"
docker compose -f docker-compose.prod.yml --env-file .env.production build

echo "==> Starting stack"
docker compose -f docker-compose.prod.yml --env-file .env.production up -d

echo "==> Waiting for web service to become healthy"
for i in $(seq 1 30); do
    status=$(docker compose -f docker-compose.prod.yml ps --format json web 2>/dev/null | grep -o '"Health":"[a-z]*"' | cut -d'"' -f4 || echo "")
    if [ "$status" = "healthy" ]; then
        echo "web is healthy"
        break
    fi
    sleep 2
done

echo "==> Migrations already run automatically by the web container command."
echo "==> Create the first administrator with: ./create_admin.sh"
echo "==> Deployment complete."
