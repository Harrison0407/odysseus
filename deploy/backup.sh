#!/usr/bin/env bash
# Backs up the Postgres database and the protected document store to a
# timestamped local archive. If BACKUP_REMOTE_TARGET is set in
# .env.production, also copies the archive off-host via rsync/scp.
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env.production ]; then
    set -a; source .env.production; set +a
fi

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="./backups"
mkdir -p "$BACKUP_DIR"

DB_DUMP="$BACKUP_DIR/db-$TIMESTAMP.sql.gz"
echo "==> Dumping database to $DB_DUMP"
docker compose -f docker-compose.prod.yml --env-file .env.production exec -T db \
    pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" | gzip > "$DB_DUMP"

DOCS_ARCHIVE="$BACKUP_DIR/documents-$TIMESTAMP.tar.gz"
echo "==> Archiving protected documents to $DOCS_ARCHIVE"
docker compose -f docker-compose.prod.yml --env-file .env.production exec -T web \
    tar czf - -C /app protected_documents media > "$DOCS_ARCHIVE"

echo "==> Local backup files:"
ls -la "$BACKUP_DIR"/*"$TIMESTAMP"*

if [ -n "${BACKUP_REMOTE_TARGET:-}" ]; then
    echo "==> Copying to remote target: ${BACKUP_REMOTE_TARGET}"
    rsync -avz "$DB_DUMP" "$DOCS_ARCHIVE" "${BACKUP_REMOTE_TARGET}/"
fi

echo "==> Backup complete."
echo "NOTE: encrypt backups at rest (e.g. gpg --symmetric) before storing them long-term or off-site."
