#!/usr/bin/env bash
# Restores a database dump produced by backup.sh into a RUNNING stack.
# WARNING: this overwrites the current database contents.
#
# Usage: ./restore.sh backups/db-20260718-120000.sql.gz
set -euo pipefail
cd "$(dirname "$0")"

if [ $# -ne 1 ]; then
    echo "Usage: $0 <path-to-db-dump.sql.gz>" >&2
    exit 1
fi
DUMP_FILE="$1"
if [ ! -f "$DUMP_FILE" ]; then
    echo "File not found: $DUMP_FILE" >&2
    exit 1
fi

if [ -f .env.production ]; then
    set -a; source .env.production; set +a
fi

read -r -p "This will REPLACE the current database ${POSTGRES_DB}. Type 'yes' to continue: " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted."
    exit 1
fi

echo "==> Stopping web to avoid writes during restore"
docker compose -f docker-compose.prod.yml --env-file .env.production stop web

echo "==> Dropping and recreating database"
docker compose -f docker-compose.prod.yml --env-file .env.production exec -T db \
    psql -U "${POSTGRES_USER}" -d postgres -c "DROP DATABASE IF EXISTS ${POSTGRES_DB};"
docker compose -f docker-compose.prod.yml --env-file .env.production exec -T db \
    psql -U "${POSTGRES_USER}" -d postgres -c "CREATE DATABASE ${POSTGRES_DB};"

echo "==> Restoring dump"
gunzip -c "$DUMP_FILE" | docker compose -f docker-compose.prod.yml --env-file .env.production exec -T db \
    psql -U "${POSTGRES_USER}" "${POSTGRES_DB}"

echo "==> Restarting web"
docker compose -f docker-compose.prod.yml --env-file .env.production up -d web

echo "==> Restore complete."
