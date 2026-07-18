# Deployment

Target: one standard remote Linux server with Docker Engine + Docker
Compose installed. Everything below was executed and verified live in
this session (see `docs/FINAL_VALIDATION_REPORT.md` for the full record).

## 1. First-time deployment

```bash
# On the server, after cloning/copying the repository:
cd dt-beach-supply-control/deploy
cp .env.production.example .env.production
nano .env.production   # fill in DJANGO_SECRET_KEY, ALLOWED_HOSTS, DB password, domain

./deploy.sh            # builds images, starts db+web+caddy, waits for health
./create_admin.sh      # creates a Django superuser + seeds pilot users/roles
```

`create_admin.sh` will print each pilot user's generated password to the
terminal **once** — copy them into your password manager immediately;
they are never stored or logged anywhere by the application.

## 2. Domain and HTTPS

Edit `deploy/Caddyfile` and uncomment "Option A" with your real domain —
Caddy will automatically obtain and renew a Let's Encrypt certificate.
Until DNS is ready, "Option B" (plain `:80`, no TLS) is active by default.

**Important:** while using Option B (no domain/TLS yet), set
`DJANGO_SECURE_SSL_REDIRECT=0` in `.env.production`, otherwise Django will
redirect every request to `https://` before Caddy can serve it over plain
HTTP, producing an unreachable app. This was caught and confirmed during
live validation of this deployment package. Switch it back to `1` (or
remove the line — `1` is the default) once TLS is live.

## 3. IP-only temporary access

With Option B active and `DJANGO_ALLOWED_HOSTS` including your server's
IP, the app is reachable at `http://<SERVER_IP>/` immediately — no domain
required. This is meant to be temporary; move to Option A + a real domain
as soon as DNS is available, since plain HTTP exposes session cookies and
form submissions in transit.

## 4. Updating a running deployment

```bash
cd deploy
git pull            # or however new code arrives on the server
./update.sh         # rebuilds and recreates only the web container
```

Migrations run automatically as part of the web container's startup
command — no manual migration step is required for routine updates.

## 5. Rolling back

```bash
./rollback.sh <previous-image-tag>
```

If the rollback needs a schema *downgrade* too (not just a code
downgrade), restore a pre-migration backup instead (`restore.sh`) — see
`BACKUP_RESTORE.md`.

## 6. Firewall guidance

Only expose 80/443 (Caddy) and 22 (SSH) on the host firewall. Postgres
(5432) and the Django/Gunicorn port (8000) are never published to the
host in `docker-compose.prod.yml` — they are only reachable from Caddy
over the internal Docker network, which itself is `internal: true` (no
outbound internet access either).

## 7. File permissions

The container runs as a non-root user (`appuser`, uid 1000) — verified
in the `Dockerfile`. Docker named volumes (`postgres_data`, `static_files`,
`media_files`, `protected_documents`, `caddy_data`, `caddy_config`) are
owned by Docker and require no manual permission management on the host.

## 8. Static files

`collectstatic` runs automatically as part of the web container's
startup command, using Whitenoise's compressed manifest storage. Vendored
third-party assets (Bootstrap, htmx) are bundled under `static/vendor/`
at build time — no CDN or external network access is required at
runtime.

## 9. Disaster recovery notes

- The database is the single source of truth for all business records;
  `protected_documents/` and `media/` hold the actual uploaded files
  referenced by those records. A full disaster-recovery restore requires
  both the DB dump and the documents archive from the same backup run
  (`backup.sh` produces both together, same timestamp).
- Test the restore procedure on a non-production stack periodically —
  see `BACKUP_RESTORE.md` for the exact validated commands.
