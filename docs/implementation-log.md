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

## Milestone 2: Gate Controls and Formal Handoffs

Baseline commit verified before starting: `3aa6127b22efda46a4bb6532f319e6a83ae72043`
(branch `main`, HEAD, clean working tree — confirmed via `git status`
before any file was touched).

9. Extended `apps.workflow.models`: `GateDefinition` (8 seeded rows, one
   per required transition), `GateOverride` (immutable override record:
   reason, actor, before/after state), and extended `Handoff` with
   `gate_definition`, `project`/`organization` (denormalized for fast,
   correct inbox scoping and cross-project isolation), `readiness_ready`/
   `readiness_snapshot`, `supersedes`, and a partial unique constraint
   (`unique_active_handoff_per_target_gate`) preventing more than one open
   handoff per target+gate. Added `Role.can_override_gates`. Two
   migrations generated cleanly; one had to be deleted and regenerated
   after making a new FK nullable to avoid an interactive
   "provide a one-off default" prompt this environment can't answer —
   documented as a normal part of iterating on an uncommitted migration,
   not a data-loss risk (nothing had been committed yet).
10. Built `apps.workflow.gates` (8 evaluator functions + a `GATE_EVALUATORS`
    registry + `evaluate_gate()` dispatcher) and `apps.workflow.services`
    (`create_handoff`, `submit_handoff`, `accept_handoff`, `reject_handoff`,
    `return_for_correction`, `resubmit_handoff`, plus permission helpers
    `can_accept_handoff`/`can_override_gates`/`can_view_handoff`). Every
    mutating function wraps `select_for_update()` in a transaction.
11. Built the handoff inbox (`/flujo/`, 6 filterable views) and detail
    screen (live readiness explanation, evidence, comments, decision
    history, action buttons gated by real permission checks), wired
    "create handoff" buttons into the Shipment and Material Request
    detail pages, and changed the dashboard's handoff card to link into
    the filtered inbox instead of showing a static count.
12. Extended `seed_pilot_data` with 9 `WorkflowStage` rows and the 8
    `GateDefinition` rows, and set `can_override_gates=True` for the
    Dirección/Gerencia/Administrador roles. Extended
    `import_live_container_fixture` to create a real
    `logistics_to_receiving` handoff against the imported shipment.
    Ran from a clean database: the demonstration handoff came back
    **genuinely blocked** (4 discrepancies, 3 requiring customs review,
    1 missing requirement — a real receiving plan) purely from the
    already-imported fixture data, with no test-specific fixture rigging.
13. **Full live HTTP walkthrough** against a running dev server, using
    the actual seeded users and imported fixture (not test doubles):
    logged in as Harrison, confirmed the demonstration handoff rendered
    all its real blockers on the detail page; attempted a plain submit
    and confirmed it was rejected with a clear message and the handoff
    stayed `not_ready`; submitted again with `harrison`'s override
    permission and a written reason — confirmed a `GateOverride` row was
    created with the reason, actor, and timestamp, and the handoff moved
    to `submitted`; logged in as Manuel, confirmed the handoff appeared
    in his "para mí" inbox, accepted it, and confirmed both
    `Shipment.status` advanced to `released_to_receiving` and a new open
    `ResponsibilityAssignment` pointed at Manuel/Almacén.
14. Wrote 32 new automated tests (`tests/test_workflow_gates.py`,
    `tests/test_workflow_handoffs.py`) covering all 15 required
    scenarios: successful handoff, blocked handoff, missing evidence,
    unresolved discrepancy, quarantined inventory, official-vs-operational
    mismatch, rejection/return for correction, corrected resubmission,
    authorized override, unauthorized override, duplicate submission,
    concurrent acceptance, role-aware inbox filtering, cross-project
    isolation, and complete audit history. One test bug of my own
    (forgot to attach required evidence in the "successful lifecycle"
    test) was caught by the first run and fixed; every other test passed
    on the first attempt. Full suite: 53/53 passing (21 pre-existing + 32
    new), confirming no regression.
15. Updated the living documentation set (this file, `REQUIREMENTS_TRACEABILITY.md`,
    `ui-navigation-map.md`, `architecture-decisions.md` ADR-013 through
    ADR-016, `implementation-roadmap.md`, `KNOWN_LIMITATIONS.md`,
    `FINAL_VALIDATION_REPORT.md`).

No step in this log is aspirational — every claim above was executed and
its actual output inspected in this session.
