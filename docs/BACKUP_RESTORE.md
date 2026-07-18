# Backup and Restore

Both procedures below were executed against the real production Docker
stack in this session (Postgres + Gunicorn + Caddy), not merely written.
See `docs/FINAL_VALIDATION_REPORT.md` for the full transcript summary.

## Backup

```bash
cd deploy
./backup.sh
```

Produces two timestamped files under `deploy/backups/`:

- `db-<timestamp>.sql.gz` — a `pg_dump` of the full database, gzip-compressed.
- `documents-<timestamp>.tar.gz` — a tar of `/app/protected_documents` and `/app/media` from the running web container.

If `BACKUP_REMOTE_TARGET` is set in `.env.production` (e.g.
`user@backup-host:/srv/backups/dtbeach`), both files are also `rsync`'d
off-host automatically.

**Encrypt backups before long-term/off-site storage** — the script prints
a reminder but does not encrypt automatically:
```bash
gpg --symmetric --cipher-algo AES256 deploy/backups/db-<timestamp>.sql.gz
```

## Restore

```bash
cd deploy
./restore.sh backups/db-<timestamp>.sql.gz
```

This **replaces** the current database contents — it prompts for a typed
`yes` confirmation, stops the web container to prevent writes during the
restore, drops and recreates the database, loads the dump, then restarts
web.

## Validated in this session

1. Ran `backup.sh` against the live stack with the imported live-container
   fixture loaded (1 shipment, 9 users, full manifest/variance/discrepancy
   data). Confirmed the resulting dump actually contains real content
   (183 `CREATE TABLE` statements, 183 `COPY` statements) — not an empty
   schema-only dump.
2. Created a throwaway `Shipment(reference="SHOULD-DISAPPEAR-AFTER-RESTORE")`
   *after* the backup was taken (bringing the count to 2).
3. Ran `restore.sh` against that backup.
4. Confirmed the shipment count returned to 1 and the throwaway record
   was gone, while the original fixture data (`MEDUWY575021`) remained
   intact and the app stayed reachable and healthy through Caddy
   immediately after.

This is a genuine round-trip verification, not a description of intended
behavior — see `docs/implementation-log.md` step 7 for the two real bugs
that were found and fixed to make this actually true (a broken `tar`
invocation in `backup.sh`, and — more seriously — a stray dev `.env` file
that had been silently making the entire "production" container run
against SQLite instead of Postgres, which would have made *any* restore
test pass trivially and incorrectly if it hadn't been caught).

## Document-store-only restore

If only files (not the database) need recovering, extract the documents
archive directly into the running container's volume:
```bash
docker compose -f docker-compose.prod.yml --env-file .env.production exec -T web \
    tar xzf - -C /app < deploy/backups/documents-<timestamp>.tar.gz
```
