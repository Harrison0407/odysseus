# Implementation Log

Chronological, factual log of this delivery pass.

1. Read `00_READ_ME_FIRST_LIVE_CONTAINER_CASE.md`, the full locked prompt,
   both business-context `.docx` files (converted via `textutil`), and
   every file in `Seed_docs/Live_container/` (PDFs read natively, XLSX
   via `openpyxl` after installing it in an isolated venv — the parent
   directory's `:` character broke a system-wide `venv`, so the venv was
   created under the session scratchpad instead; documented in
   `architecture-decisions.md` ADR-009).
2. Wrote `SOURCE_DOCUMENT_ANALYSIS.md` capturing every concrete fact,
   cross-reference, and unresolved ambiguity found (W5057/W5097, the
   Sole-26 labeling conflict, the unattributed accessories line, the
   19-vs-20 quartz slab packed-quantity conflict, the "RECEIVED IN FULL"
   stamps on all 5 local POs).
3. Scaffolded the Django project (`config` + 18 apps under `apps/`),
   implemented the full canonical data model, generated and applied
   migrations from an empty database — passed cleanly first try after
   one fix (a `lambda` default on `SecureShareLink.token` could not be
   serialized into a migration; replaced with a module-level function).
4. Built the Priority 0 vertical-slice UI: login, persona dashboards,
   document upload/detail/download, purchase order list/detail, the
   dual-manifest shipment detail screen, receiving line-posting, the
   inventory ledger view, material request create/detail, landed-cost
   list/detail, and the HTML snapshot + secure share link generator.
   Verified live via a running dev server and `curl`-driven login +
   navigation smoke test across every route.
5. Wrote `seed_pilot_data` (organization, departments, roles, document
   taxonomy, the 8 named pilot users with randomly generated passwords)
   and `import_live_container_fixture` (the actual BL/PIs/POs/CI-PL data
   from step 2, including the ambiguities as ambiguities). Both ran
   cleanly on first execution.
6. Wrote 21 automated tests (`pytest-django`) covering the fixture import,
   document upload/duplicate-detection/download-authorization, receiving
   posting service (inventory movement creation, damage quarantine,
   discrepancy creation), and organization-scoped permission checks. All
   21 passed.
7. Built the production deployment package (`Dockerfile`,
   `docker-compose.prod.yml`, `Caddyfile`, `.env.production.example`,
   `deploy.sh`/`update.sh`/`backup.sh`/`restore.sh`/`rollback.sh`/
   `create_admin.sh`) and **actually ran it** (Docker Desktop was started
   for this purpose): built the image, brought up Postgres + Gunicorn +
   Caddy, and found three real bugs during that live validation:
   - `collectstatic` failing on a dangling `sourceMappingURL` reference
     in vendored Bootstrap CSS (fixed: downloaded the real `.map` files,
     added `WHITENOISE_MANIFEST_STRICT = False` as a safety net).
   - `DJANGO_SECURE_SSL_REDIRECT` (and any other prod-only env var) never
     reaching the container because `docker-compose.prod.yml` only
     forwarded a hand-picked subset of variables (fixed: switched to
     `env_file:`).
   - The single biggest catch: a local dev `.env`
     (`USE_SQLITE_FOR_TESTS=1`) got baked into the image by `COPY . /app/`
     and silently ran the entire "production" container against an
     ephemeral in-container SQLite file instead of the mounted Postgres
     volume — every earlier "successful" migration/seed/restart-persistence
     check in that session had actually been testing SQLite, not
     Postgres. Caught by directly querying Postgres via `psql` and
     finding zero tables despite Django reporting real data. Fixed with
     `.dockerignore` plus a `DEBUG`-gated guard in `settings.py`, then
     the **entire validation sequence was re-run from a clean rebuild**
     against genuine Postgres: migrate, seed, fixture import, full
     container down+up persistence check (1 shipment / 9 users survived),
     backup (verified the dump actually contains 183 `CREATE TABLE` /
     183 `COPY` statements this time), and restore (proved by creating a
     throwaway shipment after the backup, restoring, and confirming it
     was gone).
   - A fourth, smaller bug in `backup.sh` itself (a broken `docker run`
     volume-mount invocation for archiving documents) was found and
     fixed by archiving via `docker compose exec ... tar` instead.
8. Wrote the full `docs/` set (this file included) and the top-level
   `README.md`/`ASSUMPTIONS.md`.

No step in this log is aspirational — every claim above was executed and
its actual output inspected in this session.
