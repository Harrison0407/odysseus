# Architecture Decision Log

Newest first.

## ADR-012 — `.dockerignore` must exclude `.env`; production DB choice guarded by `DEBUG`
**Decision:** Added `.dockerignore` excluding `.env`/`.env.*` (except
`.env.example`), and added `if DEBUG and env_bool("USE_SQLITE_FOR_TESTS")`
(previously just the env check alone) in `settings.py`.
**Why:** During production-stack validation, the local dev `.env`
(`USE_SQLITE_FOR_TESTS=1`) was copied into the Docker image by `COPY . /app/`
and silently made the containerized app run against an ephemeral
in-container SQLite file instead of the real Postgres volume — migrations
"succeeded" and queries "worked," but the actual Postgres database stayed
empty the whole time, and a restart appeared to preserve data only because
the container's writable layer (not a volume) briefly survived a `restart`.
This was caught by directly querying Postgres via `psql` and finding zero
tables. The `DEBUG` guard makes this whole class of bug structurally
impossible even if a future dev `.env` leaks into an image some other way.

## ADR-011 — `env_file:` in docker-compose.prod.yml instead of hand-enumerated `environment:` keys
**Decision:** The `web` service loads `env_file: .env.production` instead
of manually listing each variable under `environment:`.
**Why:** During validation, `DJANGO_SECURE_SSL_REDIRECT=0` was added to
`.env.production` to allow IP-only HTTP testing, but the container never
saw it because only a hand-picked subset of variables was forwarded.
Compose's `--env-file` flag only affects variable *substitution inside
the YAML itself*, not automatic container environment injection — a
subtlety easy to get wrong exactly as it was gotten wrong here.

## ADR-010 — Vendor Bootstrap/htmx locally, including source maps
**Decision:** `static/vendor/` contains `bootstrap.min.css(.map)`,
`bootstrap.bundle.min.js(.map)`, `htmx.min.js`, fetched once at build time
rather than loaded from a CDN at runtime.
**Why:** Spec requires the app to function without a cloud dependency.
Whitenoise's `CompressedManifestStaticFilesStorage` hard-fails
`collectstatic` if a vendored CSS/JS file references a `sourceMappingURL`
that doesn't exist on disk — caught during production validation and
fixed by downloading the real `.map` files (and setting
`WHITENOISE_MANIFEST_STRICT = False` as a defensive fallback for any
future vendored asset with the same issue).

## ADR-009 — Dev virtualenv lives outside the project directory
**Decision:** The local Python virtualenv used for `manage.py`/`pytest`
during development is created under the session scratchpad, not inside
`Application/.venv`.
**Why:** The repository's parent directory name contains a literal `:`
character, which both `python -m venv` and Docker's bind-mount volume
parser treat as a path separator, breaking venv creation and Caddyfile
mounting respectively. This is purely a local-filesystem quirk of this
delivery environment; a real deployment path (e.g.
`/opt/dtbeach-supply-control`) will never hit it. The Docker image itself
is unaffected since `COPY . /app/` happens inside the build context, not
via the affected bind-mount mechanism.

## ADR-008 — One Django project, 18 apps, no microservices
**Decision:** Modular monolith (see `ARCHITECTURE.md`).
**Why:** Explicit spec preference; also matches actual pilot scale (single
server, ~8 named users, one organization).

## ADR-007 — UUID primary keys everywhere
**Decision:** `apps.core.models.BaseModel` uses `UUIDField` as PK.
**Why:** Spec section 12 requires share links and QR labels to never
expose sequential IDs.

## ADR-006 — Ledger-only inventory, no direct quantity field
**Decision:** `InventoryLot` has no `quantity_on_hand` field; on-hand is
always computed from `InventoryMovement` at read time.
**Why:** Core principle 4.5. Directly demonstrated in
`tests/test_receiving_and_inventory.py::test_onhand_quantity_is_derived_from_ledger_never_edited_directly`.

## ADR-005 — Documents are immutable; replace = new version
**Decision:** `DocumentVersion` rows are never updated after creation;
`Document.current_version` always resolves the latest.
**Why:** Core principle 4.4.

## ADR-004 — Generic content-type pointers for cross-cutting concerns
**Decision:** `DocumentFieldSource`, `StageAssignment`, `Handoff`,
`ResponsibilityAssignment`, `Comment`, `Attachment`, and
`AuditEvent` all use `(content_type, object_id)` rather than a per-model
FK.
**Why:** These concerns apply identically to a `PurchaseOrder`, a
`Shipment`, a `MaterialRequest`, etc. — a per-model handoff/audit table
per domain object would multiply the model count without adding
behavior.

## ADR-003 — Official BL and internal manifest are separate tables, bridged by `ManifestVariance`
**Decision:** See `OFFICIAL_VS_OPERATIONAL_MANIFEST_ANALYSIS.md`.
**Why:** Non-negotiable per spec section 13A; verified against the real
live-container fixture, not just designed in the abstract.

## ADR-002 — Named pilot users are seed data, not code
**Decision:** `apps/accounts/management/commands/seed_pilot_data.py`
creates Harrison/Edison/Markeris/Lucía/Manuel/Óscar/Miguel/María Luisa
with configurable roles; no view or model branches on a username.
**Why:** Explicit spec requirement; also just good practice.

## ADR-001 — PostgreSQL only in Docker/production, SQLite only for a debug-gated local shortcut
**Decision:** See ADR-012 above for the guard that was added after this
was found to be under-enforced.
**Why:** Spec mandates PostgreSQL; a debug-only local shortcut is still
useful for fast iteration without Docker running.
