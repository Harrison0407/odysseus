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

## Delivery, Installation, Inspection, and Final Acceptance milestone

Baseline verified before starting: branch `main`, HEAD =
`86c31016d057e0338e542577aa08fd3d6a7c7cb1`, clean working tree.

16. Read the updated docs (`KNOWN_LIMITATIONS.md`, `FINAL_VALIDATION_REPORT.md`,
    `REQUIREMENTS_TRACEABILITY.md`, `ui-navigation-map.md`,
    `architecture-decisions.md`, `implementation-roadmap.md`, this file)
    and inspected the existing `Delivery`/`InstallationRecord`/
    `InspectionRecord`/`AcceptanceRecord` models, the gate engine, the
    handoff service layer, `apps.inventory` (confirmed `InventoryReservation`
    unused before this milestone), and `apps.audit.Attachment` (confirmed
    unused, noted as a future evidence-attachment candidate — see
    `KNOWN_LIMITATIONS.md`).
17. Extended `apps/requests/models.py`: `MaterialRequest.area`,
    `Status.PARTIALLY_DELIVERED`; `Delivery.delivered_at`/
    `delivery_location_note`; `DeliveryLine.quantity_damaged`;
    `ProjectReceipt.confirmed_destination_area`; rewrote
    `InstallationRecord` (installer/schedule/progress/damage/rework/
    acknowledgement/supervisor-confirmation fields, `delivery_line` FK
    completing the traceability chain) and `InspectionRecord`
    (result/pass-fail/conditional, `previous_inspection` self-FK,
    technical sign-off); added `PunchListItem`; extended
    `AcceptanceRecord` with `Decision`/`conditions_note`. One migration
    (`0002_alter_delivery_options_and_more`), regenerated once cleanly
    after adding the `delivery_line` FK (nothing committed yet, so this
    was iteration, not a data-loss risk).
18. Extended `apps.workflow.gates`: `evaluate_project_delivery_to_installation`
    now also requires a positive accepted quantity;
    `evaluate_inspection_to_acceptance` rewritten to read only the most
    recent inspection's `passed` value plus all-time open *blocking*
    punch-list items — a failed inspection or an open critical defect
    both independently block the gate, and a passing reinspection
    supersedes an earlier failure without erasing it.
19. Built `apps/requests/services.py` — the quantity-invariant and
    inventory-consequence domain layer for the whole delivery →
    installation → inspection → acceptance chain (`reserve_line`,
    `create_dispatch`, `get_or_create_delivery`, `record_delivery_line`,
    `complete_delivery`, `create_project_receipt`,
    `create_installation_record`, `record_installation_progress`,
    `acknowledge_installation`, `confirm_installation_supervisor`,
    `create_inspection`, `close_punch_list_item`, `technical_sign_off`,
    `record_final_acceptance`). Validated end-to-end via a throwaway
    smoke script (reserve → dispatch → partial/damaged delivery →
    receipt → installation with over-allocation correctly blocked →
    failed inspection blocking the gate → punch-list closure →
    reinspection chaining → gate ready → final acceptance) before
    writing formal tests — zero bugs found on the first run.
20. Built the UI: `apps/requests/views.py` gained the request
    approve/reserve/dispatch actions plus full delivery/installation/
    inspection/acceptance list/detail/action views;
    `apps/requests/urls.py` extended; 8 new Spanish-first, mobile-
    responsive templates (`delivery_list/detail`,
    `installation_list/detail/create`, `inspection_list/detail`,
    `acceptance_list`); `templates/base.html` gained an "Obra" dropdown
    nav; `apps.core.views.dashboard_home` gained pending-delivery/
    incomplete-installation counts on the Obra dashboard (linking to
    filtered list views, not decorative totals) and an
    installations-awaiting-final-acceptance queue on the Dirección
    dashboard. Cross-project isolation was added via a new
    `apps.workflow.services.can_view_target`/`user_can_access_project`
    (factored out of the existing `can_view_handoff`), enforced on every
    new detail/action view and every new list queryset.
21. Wrote `apps/requests/management/commands/seed_delivery_demo_data.py`
    — an additive demo dataset (project, warehouse stock, an approved
    material request) separate from `import_live_container_fixture`,
    scoped to this milestone's screens.
22. Wrote 40 automated tests
    (`tests/test_delivery_installation_acceptance.py`) covering all 27
    required scenarios (see `REQUIREMENTS_TRACEABILITY.md` for the exact
    mapping). Full suite: 93/93 passing (53 pre-existing + 40 new).
23. **Full live HTTP walkthrough** against a running dev server (fresh
    SQLite DB, migrated from empty, seeded via `seed_pilot_data` +
    `seed_delivery_demo_data`), using real cookies + CSRF tokens, no
    test-client shortcuts: as Miguel — approved the request via direct
    ORM inspection of the seeded data, reserved and dispatched the full
    quantity, recorded the delivery line as fully accepted, completed
    the delivery, created the project receipt, created the installation
    record, recorded full installation progress, acknowledged as
    installer, confirmed as supervisor, created and submitted the
    `installation_to_inspection` handoff and accepted it, recorded a
    **failed** inspection with 2 blocking punch-list defects, created
    the `inspection_to_acceptance` handoff and confirmed it genuinely
    blocked (both "inspection not approved" and "open blocking defects"
    reasons rendered), confirmed a plain submit was rejected, and
    confirmed a direct-POST unauthorized override attempt was denied
    server-side with the exact permission-denied message (not merely
    hidden in the UI); closed both punch-list defects; recorded a
    passing reinspection; re-submitted the handoff (now ready). As
    Harrison — accepted the handoff via the generic, reused accept
    endpoint, then recorded the final-acceptance detail ("Aceptado");
    confirmed a duplicate final-accept submission was caught gracefully
    (exactly one `AcceptanceRecord` in the database, confirmed via
    direct query). As Markeris (Compras, no project access to the demo
    project) — confirmed a direct URL hit on the installation detail
    page returned 302, and the record was absent from his own
    installation list view.
24. During this walkthrough, an accidental duplicate `curl` POST
    revealed a real gap: `create_installation_record` had no
    duplicate-submission protection and created two rows for the same
    delivered line. Fixed with an idempotent
    `select_for_update`-guarded lookup (same pattern already used by
    `get_or_create_delivery`/`create_handoff`), applied preemptively to
    `create_project_receipt` too; added regression tests
    (`test_21c`/`test_21d`) and an ADR (ADR-018). A second real gap was
    found the same way: the first version of the final-acceptance view
    called `accept_handoff` itself, which meant accepting the same
    handoff through the generic inbox button first left the domain
    decision permanently unrecordable — fixed by making the
    domain-specific screen strictly additive to (never a replacement
    for) the generic accept transition (ADR-021). Both fixes were
    re-verified with a fresh walkthrough afterward.
25. Updated the living documentation set (this file,
    `REQUIREMENTS_TRACEABILITY.md`, `ui-navigation-map.md`,
    `architecture-decisions.md` ADR-017 through ADR-021,
    `implementation-roadmap.md`, `ASSUMPTIONS.md`, `KNOWN_LIMITATIONS.md`,
    `SECURITY.md`, `FINAL_VALIDATION_REPORT.md`).

## Continuing autonomous session (Priority 0 completion + beyond)

Baseline verified before starting: branch `main`, HEAD = `c531ff7`, clean
working tree.

26. **Evidence/photo upload**, closing Priority 0 gap "Dispatch/Delivery
    UI... evidence upload not wired." Added
    `apps.audit.services.attach_evidence`/`list_evidence` (ADR-022),
    reusing `apps.documents.views.DocumentUploadForm` as-is; wired an
    "Adjuntar evidencia" form + list (`templates/requests/_evidence.html`)
    into the Delivery/InstallationRecord/InspectionRecord detail pages,
    gated by the existing `_deny_cross_project` check. Added 4 tests
    (`TestEvidenceUpload`): attachment creation, SHA-256 duplicate
    detection, cross-project denial, and detail-page rendering — 97/97
    passing (93 pre-existing + 4 new). Verified live: a real multipart
    HTTP upload of a `.jpg` through a running dev server, followed by a
    download of the exact same bytes (`diff` confirmed byte-identical).
27. **Multi-lot/split dispatch**, closing the remaining Priority 0
    dispatch gap. Added `DispatchLine.reservation` FK (migration
    `requests.0003_dispatchline_reservation`) and
    `reservation_remaining_quantity()`; rewrote the request detail page
    to list every active reservation per line (lot, remaining, editable
    quantity) inside one form, and `request_dispatch` to build explicit
    per-reservation dispatch tuples from whatever was submitted, instead
    of assuming "first reservation, full remaining." Extended
    `seed_delivery_demo_data` with a second demo lot so the split path is
    exercisable live, not just in tests. A live HTTP run of the exact
    split scenario (6 units from one lot + 4 from a second, dispatched in
    one submission) surfaced two real bugs, both fixed and covered by new
    tests before being considered done (ADR-023):
    - `create_dispatch` silently clobbered one entry's
      `quantity_dispatched` update with a second entry's stale in-memory
      copy of the same `MaterialRequestLine` — fixed by re-fetching each
      line with `select_for_update()` per iteration (also closes a
      concurrency gap for two simultaneous dispatch calls on the same
      line).
    - The split-reservation quantity `<input>` rendered its value with a
      localized comma decimal (`"6,000"`), which is invalid for an
      HTML5 `number` input and would silently fail to populate in a real
      browser — fixed with `{% load l10n %}{{ remaining|unlocalize }}`.
    4 new tests (`TestMultiLotSplitDispatch`) — 101/101 passing (97
    pre-existing + 4 new). Verified live end-to-end afterward: reserved
    6+4 units from two lots, dispatched both in one request, confirmed
    two distinct `DispatchLine` rows against the correct lots via a
    direct database query.
28. **Department/project-scoped assignment controls.** Added
    `apps.requests.views._department_for_gate`/`_assignable_users`
    (ADR-024): the installer-assignment dropdown is now scoped to active
    users holding a role in the department configured (via the existing
    `installation_to_inspection` `GateDefinition`, never a hard-coded
    name) to do installation work, further narrowed to users with
    project access when the project has any explicit grants. 2 new
    tests confirm an Obra user with access to the target project
    appears, while a Compras user, a Dirección user, and an Obra user
    scoped only to a *different* project are all excluded — 103/103
    passing (101 pre-existing + 2 new).
29. **Structural mobile-responsive audit.** Wrote a small audit script
    checking every `<table>` in `templates/` for a preceding
    `table-responsive` wrapper; found 19 missing across 9 templates that
    predated this milestone (`cost/list.html`, `cost/detail.html`,
    `documents/list.html`, `documents/detail.html`,
    `inventory/locations.html`, `inventory/lot_detail.html`,
    `procurement/po_list.html`, `procurement/po_detail.html`,
    `receiving/list.html`, `shipments/list.html`, `shipments/detail.html`
    ×3, `workflow/inbox.html`), plus 1 inside this milestone's own
    `installation_detail.html` and one in `requests/list.html`. Fixed all
    of them. `reports/snapshot_shipment.html` (the self-contained HTML
    snapshot export, which loads no Bootstrap) got a `.table-scroll` CSS
    rule instead, plus a viewport meta tag it was missing entirely.
    Verified every edited file has balanced `<div>`/`</div>` and
    `<table>`/`</table>` tag counts (a script-based check, since no
    headless browser or screenshot tool is available in this
    environment — recorded honestly as a standing environment
    limitation, not a skipped task), and re-ran the full test suite
    (103/103 passing, unchanged — several of the touched templates,
    including `procurement:po-list`/`po-detail` and `workflow:inbox`,
    are directly exercised via the Django test client elsewhere in the
    suite, confirming they still render without error).
30. **`purchasing_to_finance`/`finance_to_logistics` create-handoff
    button**, closing the last Priority 0 completion item. Wired the
    identical `{% url 'workflow:create' ... %}` button pattern already
    used on the Shipment/Material Request/Delivery/Installation detail
    pages into `procurement/po_detail.html`; `apps.procurement.views.po_detail`
    now also passes `available_gates`/`existing_handoffs`, mirroring the
    other detail views exactly — no new create-handoff mechanism.
    3 new tests confirm the button appears for both gates and that the
    full create → submit → accept lifecycle works end-to-end through the
    generic, already-tested handoff endpoints — 106/106 passing (103
    pre-existing + 3 new). Verified live: an authenticated Markeris
    clicked the real button on a running dev server and it created a
    handoff correctly evaluated as `ready_for_submission` for an
    approved PO.
