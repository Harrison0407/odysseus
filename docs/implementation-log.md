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
31. **Rate limiting on login and share-link endpoints**, closing the
    last remaining Priority 0 gap explicitly listed in
    `KNOWN_LIMITATIONS.md`. Added `apps.core.ratelimit` — a small
    fixed-window counter on Django's cache framework, no Redis/Celery
    dependency (per `ASSUMPTIONS.md` A3). `apps.accounts.views.RateLimitedLoginView`
    (wired into `config/urls.py` in place of the bare
    `auth_views.LoginView`) blocks further attempts after 10 failed
    logins/5 minutes per IP; `apps.reports.views.shared_view` returns
    429 after 30 requests/minute per IP, checked before the token is
    even looked up. Updated `templates/registration/login.html` to
    surface the rate-limit message distinctly from the ordinary
    "usuario o contraseña incorrectos" text. 4 new tests
    (`tests/test_rate_limiting.py`) — including one that proves even a
    *correct* password is rejected while blocked, and one confirming a
    different IP is unaffected — 110/110 passing (106 pre-existing + 4
    new). Documented honestly in `KNOWN_LIMITATIONS.md`/`SECURITY.md`
    that the default `LocMemCache` backend enforces this per Gunicorn
    worker process, not globally across a multi-worker deployment.
32. **Detailed internal receiving manifest (spec 13A.8).** Checked
    whether the governing specification's exact 13A.8 text was
    available to re-read in this session — it was not (only derived
    docs are stored in `Application/`); confirmed the one independently
    verifiable fact (`OFFICIAL_VS_OPERATIONAL_MANIFEST_ANALYSIS.md`
    cites the variance matrix as "spec 13A.8, item 10") and built a
    genuinely useful 10-section document from `BUSINESS_REQUIREMENTS.md`
    and the existing receiving/shipment data model rather than either
    fabricating spec text or refusing the feature — recorded as
    `ASSUMPTIONS.md` A16. Factored `_save_html_snapshot` out of
    `shipment_snapshot` (ADR-025) so the new
    `receiving_manifest_snapshot` reuses the exact same
    `ReportVersion`/`Document`/`DocumentVersion` persistence path, not a
    second reporting mechanism. 4 new tests
    (`tests/test_receiving_manifest_snapshot.py`) — 114/114 passing (110
    pre-existing + 4 new). Verified live against the actual imported
    MEDUWY575021 fixture: created a real `Receipt` for the fixture's
    container, downloaded the manifest, and confirmed all 10 sections
    rendered with the fixture's real 11 internal manifest lines and 11
    variances, and that a real `ReportVersion`/`Document` with a genuine
    SHA-256 was persisted.
33. **Landed-cost allocation-run trigger UI — and the calculation engine
    itself**, closing the last Priority 0 gap. Discovered that
    `apps.cost` had no `services.py` at all before this session — the
    calculation logic did not exist anywhere, only the
    `CostAllocationRun`/`CostAllocationLine`/`LandedCostVersion`/
    `LandedCostLine` data model did. Built `apps.cost.services`:
    `run_allocation` (quantity/product-value/gross-weight/net-weight/
    CBM/package/container/manual-percentage/manual-amount, last line
    absorbs rounding so allocated amounts always sum exactly to the
    charge), `calculate_landed_cost` (aggregates every allocation into
    per-unit freight/local/other buckets, traces original unit price
    through `ManifestLineSource → PurchaseOrderLine`, converts to base
    currency via `ExchangeRate` only when one is on file — `None`,
    never a fabricated rate, otherwise), `finalize_landed_cost` (ADR-026
    for both design decisions). Built `/costos/embarque/<id>/` (linked
    from the shipment detail page) with "ejecutar asignación"/"calcular
    nueva versión" actions, and a "finalizar" action on the version
    detail page; added missing organization-scoping to the two
    pre-existing `cost` views while in the same file. 14 new tests
    (`tests/test_cost_allocation.py`) — 128/128 passing (114
    pre-existing + 14 new). Verified live against the real imported
    MEDUWY575021 fixture: allocated its actual USD 6,900 ocean-freight
    charge by CBM across its 11 real manifest lines through the actual
    UI buttons, calculated a real `LandedCostVersion` with genuine
    per-unit freight costs, and finalized it.

## Priority 1 (continuing autonomous session, first item)

All Priority 0 gaps from `KNOWN_LIMITATIONS.md` are now closed as of the
previous entry. Proceeding through Priority 1 in the roadmap's
documented order.

34. **CONFOTUR reconciliation UI** (spec section 25). `apps.customs` had
    no `views.py`/`urls.py` wired in at all before this session — built
    the full vertical slice. `apps.customs.services.detect_duplicate_candidates`
    groups still-unresolved `ConfoturLine` rows by shared `quotation` or
    `manifest_line`; `confirm_duplicate` sets the pre-existing
    `is_duplicate_of` field (modeled since Priority 0, never previously
    populated by any code path). Deliberately no "dismiss" action —
    candidates stay visible until a human explicitly confirms one as a
    duplicate of another (ADR-027), matching the project-wide rule that
    flagged risks are never silently closed. `/aduanas/confotur/`
    list/detail/reconciliation screens, linked from the main nav. 10 new
    tests (`tests/test_confotur_reconciliation.py`), including
    cross-organization isolation and an HTTP round-trip confirming a
    resolved pair drops out of the candidate list — 138/138 passing (128
    pre-existing + 10 new).
35. **Tool custody UI.** `apps.tools` had no `views.py`/`urls.py` wired
    in either — built `apps.tools.services` (`checkout_tool`/
    `return_tool`/`record_repair`, ADR-028): the one invariant the data
    model implied but no code ever checked (a tool cannot be checked
    out twice at once) is now enforced. `/herramientas/` list/detail
    screens, linked from the main nav and the Almacén dashboard.
    **While building this, found and fixed a real, previously
    undetected production bug:** `apps.core.views.dashboard_home`
    crashed with `FieldError: Cannot resolve keyword
    'actual_return_date'` for any almacen/recepcion-role user, ever
    since it was written in the Priority 0 pass — confirmed by directly
    reproducing it in a shell before touching anything. It filtered
    `ToolCheckout` by a field that only exists on the related
    `ToolReturn` model. No test had ever rendered an authenticated
    almacen-role dashboard before now (the one existing dashboard-home
    test only checks the anonymous-redirect case). Fixed
    (`tool_return__isnull=True, expected_return_date__lt=today`, which
    also corrects the semantics to genuinely "overdue," not just
    "still checked out") and covered by a dedicated regression test. 11
    new tests (`tests/test_tool_custody.py`) — 149/149 passing (138
    pre-existing + 11 new).
36. **Cycle-count UI.** Built `apps.inventory.services`
    (`start_cycle_count`/`record_physical_count`/`approve_adjustment`,
    A19 for the one deliberate small duplication of a ledger helper):
    a count is seeded from the ledger-derived on-hand quantity per lot
    (never a stored figure); supports "blind" counting (system quantity
    hidden from the counter in the UI until they enter a physical
    count); tracks recounts (`recounted` flag) without overwriting the
    prior count's variance; posts any approved variance as a real
    `InventoryMovement` (`MovementType.ADJUSTMENT`) — never a silent
    stock edit, matching the same ledger discipline as every other
    inventory-affecting action in the system. `/almacen/conteos/`
    list/create/detail screens, linked from the main nav. 12 new tests
    (`tests/test_cycle_count.py`), including an HTTP-level check that
    the blind-count UI genuinely hides the system quantity until
    counted — 161/161 passing (149 pre-existing + 12 new).

## Priority 1 (one-shot completion run, resumed from verified baseline e9c24fd)

37. **Storage capacity/suitability warnings.** `LocationSuitability`,
    `LocationCapacity` (both OneToOne to `WarehouseLocation`), and
    `ProductRiskProfile` (OneToOne to `ProductCategory`) were all
    already modeled but had zero calling code anywhere before this
    entry — built `apps.inventory.services
    .check_location_suitability`/`enforce_location_suitability`
    (ADR-029) as the single point where a proposed
    location/item/quantity is checked. A configured, explicit
    restriction (`WarehouseLocation.allowed_categories` violated, or a
    hard volume/weight ceiling exceeded) is blocking; a sensitive
    material (`ProductRiskProfile.risk_level == HIGH`) placed somewhere
    lacking covered/dry/secure conditions, or with flood/leak risk, is
    warning-only (A21) — the absence of a suitability/capacity row is
    never itself blocking (A22). `Item` has no per-unit volume/weight
    field, so utilization is derived from the most recently linked
    `ManifestLine.cbm`/`gross_weight_kg` ÷ quantity, reported as
    "Desconocida" rather than a fabricated zero when not derivable
    (A20). Wired into the two workflows that actually create/move
    inventory: `apps.receiving.services.post_receipt_line` (put-away —
    **found and fixed a real pre-existing bug while doing this:**
    `apps.receiving.views.receipt_line_update` called
    `WarehouseLocation.objects.first()` as a placeholder; no receiving
    location was ever genuinely user-selected before this change) and
    the newly-activated `apps.requests.services.transfer_lot`, which
    wires up the `Transfer` model (lot/from_location/to_location/
    quantity/reason) that had no calling code anywhere in the
    codebase before this. Authorized overrides reuse
    `apps.workflow.services.can_override_gates` + written reason +
    `AuditEvent.Action.WAIVER` verbatim from the Milestone 3
    installation quantity-guard override — no new authorization
    concept. New `/almacen/ubicaciones/<id>/` detail screen shows
    capacity, live utilization, suitability conditions, a live
    suitability checker (GET form), assigned inventory (ledger-derived
    on-hand per lot at this location), pending inbound quantities
    (from `ReceivingPlanLine`, filtered to lines with no positive
    receipt yet), the site's `custodian` as responsible personnel, and
    a transfer-in form; `location_list` was also fixed to be
    organization-scoped (it was previously completely unscoped — found
    while working in this exact area). A lot with positive balance at
    more than one origin location is refused with an explicit error by
    the transfer screen rather than silently guessing a source (A23).
    20 new tests (`tests/test_storage_suitability.py`), covering
    capacity within/exceeded limits, category-restriction blocking,
    warning-only sensitive-material placement, authorized/unauthorized
    override (with audit-log verification), cross-organization
    isolation (404, not a leaked 403), transfer quantity-invariant
    enforcement, and the receiving put-away integration (both the
    blocked-with-no-inventory-consequence case and the
    authorized-override-succeeds case) — 181/181 passing (161
    pre-existing + 20 new). `manage.py check` clean; `makemigrations
    --check` reports no changes (no model fields were added — every
    piece of this reuses pre-existing model structure).
38. **External storage comparison calculator.**
    `AlternativeStorageOption` was modeled since Priority 0
    (`receiving_plan` FK) but had zero calling code anywhere — confirmed
    before making any schema change, so the re-parenting to a new
    `StorageComparisonScenario` (ADR-030) carried no data-migration
    risk. `StorageComparisonScenario` uses the same `version_number` +
    `is_current` pattern as `ReleasePacketVersion`/`LandedCostVersion`:
    a new comparison always creates a new version rather than editing a
    decided one, and once `status=FINALIZED`,
    `apps.receiving.services.add_storage_option`/`update_storage_option`
    both refuse further edits. `compare_scenario_options` computes a
    guaranteed comparable total per option (storage + handling +
    inbound/outbound transport + insurance, floored by
    `minimum_commitment_amount` when higher) and deliberately excludes
    `demurrage_penalty_estimated_cost` from it — shown alongside as
    contingent exposure, not blended into a misleadingly certain single
    number (A24). Currency conversion reuses
    `apps.cost.services.convert_to_base_currency` (renamed from the
    private `_convert_to_base_currency` so both features share one
    never-fabricate-a-rate implementation): an option in a currency
    with no `ExchangeRate` on file is still shown in the comparison
    table, just with `converted_total=None` and an explanatory note,
    never assumed 1:1. An `internal_baseline` option can link to a real
    `WarehouseLocation` and reuses its already-registered
    `LocationSuitability` — no duplicated capacity/suitability entry
    for our own warehouse. New `/recepcion/planes/<id>/` (receiving-plan
    overview, linked from the Shipment detail page) and
    `/recepcion/comparaciones/<id>/` (comparison table, ranked results,
    add-option/finalize forms, printable HTML export reusing the exact
    same `_save_html_snapshot`/`ReportVersion` mechanism as the
    receiving manifest snapshot) screens. 21 new tests
    (`tests/test_storage_comparison.py`), covering complete/incomplete
    cost calculation, minimum-commitment flooring, demurrage-exposure
    exclusion, multi-currency comparison without a rate, a
    provenance-recorded conversion, multi-option ranking, suitability
    scoring (including the internal-baseline/`LocationSuitability`
    reuse path), immutable scenario versioning, cross-organization
    isolation, and a full create→add-option→finalize HTTP lifecycle —
    202/202 passing (181 pre-existing + 21 new). Verified migrations
    apply cleanly from an empty test database (`pytest --create-db`);
    `manage.py check` and `makemigrations --check` both clean.
39. **Supplier claim package generation.** Unlike the two prior
    Priority 1 features, `SupplierClaim` did not exist in any form
    before this entry — confirmed by a repo-wide search for any
    `Claim` model — so this is the one greenfield data model among the
    four Priority 1 features. New `apps.claims` app: `SupplierClaim`
    (ADR-031) links via optional FKs to every real record that can
    justify a claim (`Supplier`, `PurchaseOrder`/`PurchaseOrderLine`,
    `Item`, `Shipment`, `Container`, `ManifestVariance`, `Receipt`/
    `ReceiptLine`, `Discrepancy`, `QuarantineRecord`, `Inspection`,
    `ReplacementCase`) — every field a genuine link, never a
    re-entered copy; a claim referencing the Official-vs-Operational
    `ManifestVariance` only ever points at that already-immutable
    record, confirmed by a dedicated test that the variance's own
    quantities are untouched after linking a claim to it. Lifecycle:
    `DRAFT -> APPROVED -> SUBMITTED -> SUPPLIER_RESPONDED -> RESOLVED -> CLOSED`,
    each transition guarded in `apps.claims.services` so it can never
    run out of order or repeat (a second `submit_claim` call on an
    already-`SUBMITTED` claim is refused, not silently reposted — the
    duplicate-submission-prevention requirement). `claim_number` is
    generated organization-and-year-scoped
    (`CLM-{year}-{sequence:04d}`, A28). Approval requires at least one
    evidence attachment — a conservative operational rule, not a legal
    one (A27). Evidence reuses `apps.audit.services.attach_evidence`/
    `Attachment` (ADR-022) rather than a new `ClaimEvidence` model. The
    printable/downloadable claim package (cover summary, supplier/PO
    info, shipment/container refs, discrepancy detail, quantity/value
    calculation, full chronology from `AuditEvent`, evidence index,
    receiving/inspection findings, requested remedy, contacts,
    provenance/timestamp, and any missing-document warnings rendered
    directly into the document) reuses
    `apps.reports._save_html_snapshot`/`ReportVersion` — extended with
    one new optional `content_object` parameter so the resulting
    `ReportVersion` links back to the specific claim, rather than
    adding a dedicated per-claim package-version model;
    `ReportVersion.ReportType.CLAIM_PACKAGE` already existed, unused,
    anticipating exactly this feature. No email is ever sent
    automatically. `/reclamos/` list/create/detail screens, linked
    from the main nav. 21 new tests
    (`tests/test_supplier_claims.py`), covering sequential claim
    numbering, a required non-blank reason, the official/operational
    variance-preservation guarantee, missing-evidence warnings,
    approval blocked without evidence, the full lifecycle to closure,
    every out-of-order transition rejected, complete audit chronology,
    package generation content, cross-organization isolation, and a
    full HTTP lifecycle including a duplicate-submission attempt —
    223/223 passing (202 pre-existing + 21 new). Verified migrations
    apply cleanly from an empty test database; `manage.py check` and
    `makemigrations --check` both clean.
40. **QR labels and controlled scanning** — the fourth and final
    Priority 1 feature. New `apps.labels` app: `QRLabel` (opaque,
    unguessable `token` via `secrets.token_urlsafe`, same pattern as
    `apps.reports.models.SecureShareLink` — never the underlying
    object's real UUID), `QRLabelPrintEvent` (reprint history),
    `QRScanEvent` (scan audit history, including denied
    cross-organization attempts). One small entity registry
    (`apps.labels.services._ENTITY_REGISTRY`, ADR-032) covers all 8
    required entity types (inventory lot, warehouse location, receipt,
    dispatch, delivery, installation record, tool, container) with
    three pure functions each (organization resolver, human-label/
    context resolver, target-URL resolver) rather than 8 separate
    bespoke view/service implementations. The QR image is a
    self-contained base64 PNG data URI (via the `qrcode` package,
    added to `requirements.txt`) encoding only `/qr/<token>/` — never
    the raw ID, never a secret. `qr_scan_landing`
    (`/qr/<token>/`) is `login_required`, so an unauthenticated scan
    is sent to log in before anything about the label resolves; once
    authenticated it re-checks organization membership (logging a
    denied cross-organization attempt rather than silently allowing
    or silently dropping it) and then simply redirects into the
    entity's own existing, already-permission-checked detail page —
    the scan performs no consequential action of its own and
    introduces no second, parallel permission system. Individual print
    (with a "cantidad" field, logged only on an explicit POST, never
    on a bare page view — A29) and batch print (checkbox selection,
    wired into the location detail screen's assigned-inventory table)
    are both supported; invalidating a label never deletes it, only
    flags it and links it to a new, incremented-version replacement.
    Print-link UI coverage: 7 of 8 entity types got a visible
    "Imprimir etiqueta QR" link on their existing detail page; `Dispatch`
    has no own detail screen in this system (summarized inline on its
    parent Material Request, not individually browsable) so its label
    support is fully implemented and tested at the service/URL layer
    but not yet linked from a template (A30). 22 new tests
    (`tests/test_qr_labels.py`), covering repeated-call idempotency,
    opaque-payload verification (the raw object UUID never appears in
    the QR data URI), batch generation (including silent exclusion of
    another organization's entities from a batch), reprint history
    accumulation, the full invalidate-and-replace lifecycle,
    authenticated/unauthenticated/cross-organization/invalidated-token
    scan handling, direct-object-access prevention for both the print
    page and an unsupported entity type, and a full HTTP
    print→invalidate→reprint lifecycle — 245/245 passing (223
    pre-existing + 22 new). Verified migrations apply cleanly from an
    empty test database; `manage.py check` and `makemigrations --check`
    both clean. This closes the last of the four Priority 1 features
    from the one-shot completion run.

## Physical property / field operations release (one-shot, resumed from e9c24fd-descendant baseline 4440b8a)

41. **Physical building/floor/apartment hierarchy.** Read all 3 supplied
    source PDFs directly (`imports/buildings/`): the small "DT Beach
    Building Apartments.xlsx.pdf" gave a real, structured per-building
    floor/apartment template (floor, letter, apartment number, internal/
    terrace/total area, bedrooms, bathrooms, service room) for MARE B,
    SOLE B, SOLE A (PH), SOLE 26, ARENA T1, and PALMERA; the large
    "Buildings Plans Main.pdf" gave a numbered site-plan building index
    confirming exactly which physical building numbers belong to each
    family (ARENA T1 = 1,2,3,4,9,10,11,12 — independently matching the
    8 buildings named in the governing instruction). `BuildingFamily`
    (new model, ADR-033) sits above the pre-existing `Building` model
    (extended: `family`, `building_number`); `Unit` gained a stored,
    never-regenerated `permanent_code` plus real measurement fields.
    `apps.projects.services.import_physical_property_master` is fully
    idempotent (matches by `permanent_code`) with a `--dry-run` mode;
    `import_buildings_and_units --organization <name>` seeds it.
    Imported 684 real units across 24 physical buildings (verified
    against the source: e.g. `ARENA-T1-B11-A3`, `ARENA-T1-B12-A3`,
    `MARE-B-B12-C4`, and `PALMERA-417` — 3 of the release's own 4
    illustrative example codes resolved to genuine imported units,
    cross-confirming the two source documents agree with each other;
    see A31-A35 for the one example that didn't and why). MARE A/
    ARENA T2/ARENA T3 exist as inactive catalog entries with no
    buildings yet, exactly as instructed. `apps.workflow.services
    .resolve_project` was extended with `unit`/`building` fallbacks so
    the existing project-isolation mechanism (`user_can_access_project`)
    covers the new models without a second permission engine.
    `/propiedades/` family catalog → building detail → unit detail →
    search screens, linked from the main nav. **A real bug was found
    and fixed while live-validating this milestone against a
    multi-organization dev database:** `import_buildings_and_units`
    used `Organization.objects.first()` (matching existing precedent in
    `import_live_container_fixture.py`), which is non-deterministic
    once more than one `Organization` row exists (Django orders by
    UUID PK, not creation order, absent an explicit ordering) — added
    an optional `--organization <name>` flag (A35), matching the
    pattern `seed_delivery_demo_data` already established. 20 new tests
    (`tests/test_property_master.py`), covering permanent-code format
    for both the building-segment and no-building-segment cases,
    full-import counts, idempotency, dry-run (commits nothing), the
    known-example cross-check, global code uniqueness, the inactive-
    family catalog-only state, the exact Arena T1 building roster, the
    real floor-1-has-no-unit-D quirk, and cross-organization/cross-
    project HTTP isolation (404, management-role bypass, explicit
    `UserProjectAccess` grant) — 266/266 passing (246 pre-existing + 20
    new). Verified migrations apply cleanly from an empty database;
    `manage.py check`/`makemigrations --check` both clean; live HTTP
    walkthrough via a real browser session confirmed the family
    catalog, building detail (showing real permanent codes), and unit
    search all render correctly against the imported data.
42. **Drawing and floor-plan register.** New `apps.drawings` app:
    `Drawing` wraps the pre-existing `apps.documents.Document`/
    `DocumentVersion` provenance system (SHA-256, duplicate detection)
    rather than a second file-storage mechanism, adding
    building/floor/unit/discipline/type/status/revision metadata
    (ADR-034). `supersede_drawing` always creates a brand-new `Drawing`
    row linked via `supersedes`; the prior row's `source_document` and
    every other field are left untouched, only `status`/`is_current`
    flip — so any future FK elsewhere (order allocation, installation,
    walkthrough, field issue) pointing at a specific historical
    `Drawing` can never be silently redirected to a newer revision.
    `approve_drawing` reuses the exact same senior-authorization
    permission (`can_override_gates`) every other approval gate in this
    system already uses. `register_source_drawings --organization
    <name>` registers all 3 supplied source PDFs: the site-plan and
    apartment-typology-table PDFs (each spanning all 5 projects) get
    one `Drawing` row per covered project sharing the same underlying
    `Document` (A38); the Palmera-specific plan set is linked directly
    to Palmera's one physical building, registered as `DRAFT` status
    since its own title block explicitly states the plans are still in
    process (verified live: 11 drawings registered — 5+5+1). No
    graphical/spatial floor-plan schematic was built, since the only
    available per-unit data is a letter-grid table with no real
    coordinates (A39) — the actual architectural PDFs remain linked and
    downloadable per building/project instead. `/planos/` list/detail
    screens, linked from the main nav and from building/unit detail
    pages. 12 new tests (`tests/test_drawing_register.py`), covering
    drawing registration, permission-gated approval (including refusal
    for an unauthorized user and for an already-superseded drawing),
    the full supersede lifecycle (old row preserved exactly, new
    revision numbered correctly, a second supersede attempt on an
    already-superseded drawing rejected), a dedicated test proving a
    historical reference to an old drawing's primary key is never
    silently redirected after a supersede, cross-organization/cross-
    project HTTP isolation, and a full HTTP supersede round-trip —
    278/278 passing (266 pre-existing + 12 new). Verified migrations
    apply cleanly from an empty database; live HTTP walkthrough
    confirmed the drawing list, detail pages, and the building-detail
    "linked drawings" section all render correctly with the real
    registered source documents.
43. **Order destination allocation and purchased spares.**
    `PurchaseOrderLine` already had a single `destination_scope`/
    `building` pair (no split-allocation support) — extended
    `apps.procurement` with `OrderLineAllocation` (split allocation
    across building family/physical building/floor/unit/common area,
    with an optional linked `Drawing` revision) and `PurchasedSpare`
    (ADR-035). `PurchasedSpare` is deliberately an authorization record
    only — no quantity-tracking fields of its own; `InventoryLot`
    gained one nullable `purchased_spare` FK, and
    `apps.procurement.services.spare_inventory_summary` computes
    received/available/reserved entirely from the existing
    `InventoryMovement`/`InventoryReservation` ledger, so a spare is
    ordinary inventory with an extra provenance link, never a second
    balance. `allocate_order_line`/`confirm_purchased_spare` both
    enforce "allocated + confirmed spares never exceed ordered
    quantity" on every write; `confirm_purchased_spare` requires the
    same senior-authorization permission (`can_override_gates`) used
    throughout this system. `reassign_allocation` never edits or
    deletes the original allocation — it flips `is_active` and creates
    a new row linked via `reassigned_from`, preserving the original
    planned destination in full. A visible warning banner (not a hard
    block — this codebase has no existing "approve this PO" workflow
    transition to attach a gate to, A40) appears on the PO detail page
    when any line has unresolved unallocated quantity. New
    `/compras/lineas/<id>/asignacion/` (per-line summary, allocations,
    spares, add-allocation/confirm-spare/reassign forms),
    `/compras/asignaciones/` (all allocations by destination), and
    `/compras/repuestos/` (all confirmed spares with ledger-derived
    availability) screens. 20 new tests
    (`tests/test_order_allocation_spares.py`), covering split
    allocation across destinations, unallocated/shortage calculation
    (shortage reported as unknown, never a fabricated zero, when
    `required_quantity` was never recorded), the allocated-plus-spares
    ceiling, authorized/unauthorized spare confirmation (with audit
    verification), spare receipt/availability/consumption entirely
    through real `InventoryMovement` rows, destination-reassignment
    history preservation (including refusing to reassign an
    already-reassigned allocation), cross-organization HTTP isolation,
    and a full HTTP allocate→confirm-spare workflow — 298/298 passing
    (278 pre-existing + 20 new). Verified migrations apply cleanly from
    an empty database; live HTTP walkthrough against a real imported
    fixture PO line (`DT-BEACH804`, 40 units) confirmed a 30-unit
    allocation plus a 10-unit spare confirmation correctly reduced the
    unallocated badge to 0.
44. **Field issue reporting and corrective-action tracking.** New
    `apps.fieldissues` app (ADR-036): `FieldIssue` requires only
    `building` at creation — floor/unit/room are all optional and
    refinable later without touching the original `created_at`/
    `created_by`. Full lifecycle
    (REPORTED→ASSIGNED→IN_PROGRESS→CORRECTION_COMPLETED→
    READY_FOR_VERIFICATION→VERIFIED_CLOSED, plus
    RETURNED_FOR_CORRECTION→RESUBMITTED→REINSPECTION looping back)
    implemented in `apps.fieldissues.services`, each transition
    server-side guarded against running out of order. Comments reuse
    the existing generic `apps.audit.Comment`; evidence reuses
    `apps.audit.services.attach_evidence` wrapped by one new
    `FieldIssueEvidence` (`stage`: before/during/after — the one piece
    of metadata the generic `Attachment` doesn't carry).
    `verify_and_close_issue` requires `can_override_gates` — the same
    permission every other approval-style action in this release
    uses — checked unconditionally regardless of who performed the
    correction, so completing the work never grants closure authority
    by itself (A42/A43). Closure is blocked without a corrective
    description, a responsible party, a completion timestamp, before
    evidence (or an authorized waiver reason), and after evidence.
    `IssueCategory` mirrors the existing `DocumentType`
    configurable-taxonomy pattern (A44). New `/incidencias/` report
    form (mobile-first, only building required, direct photo capture)
    and filtered dashboard views (reported-by-me, assigned-to-me/team,
    overdue, awaiting correction/verification, rejected/reopened,
    closed), linked from the main nav. 20 new tests
    (`tests/test_field_issues.py`), covering mobile creation with only
    a building, duplicate-click idempotency, evidence provenance
    (including duplicate-content detection), assignment/reassignment,
    correction blocked without before-evidence-or-waiver, closure
    blocked without after-evidence, unauthorized closure attempt
    (including by the same person who performed the correction),
    full closure by an authorized independent verifier, rejection
    preserving all prior evidence/history, resubmission adding new
    evidence without erasing the old, a full reject→resubmit→
    reinspection→verify cycle, cross-organization/cross-project HTTP
    isolation, and a full HTTP report→list workflow with duplicate-
    submission protection — 318/318 passing (298 pre-existing + 20
    new). Verified migrations apply cleanly from an empty database.
    Live HTTP walkthrough against the real imported ARENA T1 Building
    11 confirmed the complete lifecycle end-to-end: reported → assigned
    → in-progress → correction recorded with real before/after photo
    uploads (SHA-256 hashed) → ready for verification → verified and
    closed by Harrison — plus a genuine second-organization user denied
    (404) on direct URL access to the issue.
45. **Lawson training and reference-installation sessions.** New
    `apps.training` app (ADR-037): `TrainingSession` mirrors
    `FieldIssue`'s location shape exactly (building required, floor/
    unit/room optional) and reuses the same stage-tagged evidence
    wrapper pattern (`TrainingEvidence`: before/during/after, via
    `attach_evidence`). `trainer`/`participants` are ordinary user
    references — nothing in `apps.training.services` branches on a
    specific name, and a dedicated test creates a session with Manuel
    as trainer and Manuel+Miguel as participants specifically to prove
    this. `supervisor_sign_off`/`approve_as_reference_installation`
    both require `can_override_gates` — the same senior-authorization
    permission used throughout this release —
    and approval hard-requires sign-off to have already happened
    (A45). `TrainingParticipantAcknowledgement` is recorded by the
    participant themselves, not on their behalf (A46), and is
    validated against the session's actual participant list.
    `FieldIssue.training_session` (new FK) lets
    `create_issue_from_training` produce a genuinely linked
    `FieldIssue` through the existing `report_issue` service — never a
    disconnected copy. New `/capacitaciones/` create/list/detail
    screens (list supports `?reference=1` to show only approved
    reference installations), linked from the main nav. 18 new tests
    (`tests/test_training_sessions.py`), covering the configured-
    trainer-not-hard-coded guarantee, session start/finish ordering,
    checklist and before/during/after evidence recording,
    participant-acknowledgement validation (including rejecting a
    non-participant's attempt), supervisor sign-off permission and
    ordering checks, reference-installation approval permission and
    ordering checks, the create-issue-from-training relationship, and
    cross-organization HTTP isolation — 336/336 passing (318
    pre-existing + 18 new). Verified migrations apply cleanly from an
    empty database; live HTTP walkthrough created a real training
    session in the actual imported ARENA T1 Building 11.
46. **Apartment walkthroughs and corrective actions.** New
    `apps.walkthroughs` app (ADR-038): `Walkthrough` requires only
    `building`; floor/unit/`units` (M2M, for a deliberately-selected
    group of apartments)/area are all optional (A48), so the same
    model/service/view code already covers every configured active
    building without any per-building branching — proven directly by a
    dedicated test that creates walkthroughs against two independently
    created buildings and asserts identical behavior. `purpose` is a
    fixed 6-value enum (construction progress, quality control,
    training/reference, pre-delivery final, final handover/delivery,
    reinspection — A47) since the release names exactly these 6 with
    distinct business logic (only the last two purposes trigger
    delivery-readiness control), while `category` (windows, doors,
    kitchens, ...) is `WalkthroughCategory`, a fully configurable
    taxonomy mirroring `IssueCategory`/`TrainingCategory`.
    `WalkthroughChecklistTemplateItem` seeds the real 16-item window/
    sliding-door checklist (`seed_walkthrough_checklist_templates`) as
    genuinely editable data, not a hard-coded branch;
    `populate_checklist_from_template` bulk-creates one
    `WalkthroughItem` per template entry. Each item records checklist
    result, measurement + unit, digital-level reading, level/plumb/
    square/operational-test conditions, and — critically —
    `condition_found`/`adjustment_performed`/
    `condition_after_adjustment` as three *distinct* fields, never one
    overwriting another (verified by a dedicated test). Evidence reuses
    the same stage-tagged wrapper pattern as field issues/training
    (`WalkthroughItemEvidence`, via `attach_evidence`). A defect becomes
    a real, fully-lifecycled `FieldIssue`
    (`WalkthroughItem.field_issue` / `FieldIssue.walkthrough_item`,
    ADR-038) — the entire reject/resubmit/reinspect/verify cycle is
    `apps.fieldissues.services`'s, exercised end-to-end through a
    linked issue in a dedicated test, never duplicated in
    `apps.walkthroughs`. `delivery_readiness_summary` computes total/
    passed/conditional/failed items, open issues, blocking defects,
    overdue corrective actions, missing-evidence items, and pending-
    verification issues entirely by querying the linked `FieldIssue`
    rows' real status (A50) — never a second, driftable "resolved"
    flag. `mark_delivery_decision` refuses a READY decision while
    blocked unless an authorized override
    (`can_override_gates` + written reason, logged as
    `AuditEvent.Action.WAIVER`) is supplied, matching the override
    shape used throughout this release. `create_reinspection_walkthrough`
    creates a brand-new linked `Walkthrough` (`previous_walkthrough`)
    without ever touching the original's own items/evidence;
    `create_next_sequential_walkthrough` copies building/floor/
    category/purpose/inspector for the next unit in a floor/building
    sweep, satisfying "efficient sequential walkthroughs...without
    repeatedly re-entering the same information" (A49) as a data-
    copying convenience rather than one `Walkthrough` spanning an
    entire floor. New `/recorridos/` create/list/detail screens (list
    filterable by purpose/building), linked from the main nav. 18 new
    tests (`tests/test_walkthroughs.py`), covering building-agnostic
    creation across two separate buildings, all 6 purposes, every
    walkthrough-scope option (building-only/floor/unit/selected
    group), checklist-template population, digital-level measurement
    recording, the condition-found-vs-after-adjustment preservation
    guarantee, staged evidence, the corrective-issue-linkage
    relationship, a full reject→resubmit→reinspect→verify cycle
    through the linked issue, delivery-readiness blocking/authorized-
    override/unauthorized-override/clear-to-proceed cases,
    reinspection-walkthrough history preservation, sequential-
    walkthrough creation, cross-organization HTTP isolation, and a
    full HTTP create→add-item→record-result workflow — 354/354 passing
    (336 pre-existing + 18 new). Verified migrations apply cleanly from
    an empty database; live HTTP walkthrough seeded the real 16-item
    checklist template and created a genuine construction-progress
    walkthrough in the actual imported ARENA T1 Building 9 (a
    different building than the training milestone's Building 11,
    demonstrating the building-agnostic design live, not just in
    tests).
47. **Unclassified Evidence Inbox.** New `apps.evidenceinbox` app
    (ADR-039), for Lawson's historical photographs and any future field
    evidence whose exact building/apartment/issue isn't known yet.
    `UnclassifiedEvidence` wraps the existing `Document`/
    `DocumentVersion` mechanism directly (SHA-256 hashing, duplicate
    detection, immutable version history — mirrored rather than reused
    via `attach_evidence` since there is deliberately no target to
    attach to yet); only an optional coarse project/building guess,
    `date_taken`, and notes accompany it, and nothing about the
    original upload is ever modified again. `EvidenceClassification`
    (generic content_type/object_id, same shape as `Attachment`/
    `AuditEvent`, ADR-004) is a separate, reassignable pointer covering
    building/floor/unit/walkthrough/walkthrough item/training session/
    field issue/product/supplier/purchase order line/shipment/
    container/installation/inspection record (`CLASSIFIABLE_TARGETS`);
    `classification_status` (unclassified/partially classified/
    classified, A53) is derived from whether the classified target is
    a "leaf" record or a coarser one. Reclassifying never deletes or
    edits the old classification — it is marked `is_active=False` and
    linked via `superseded_by` (A52), the same versioned-immutable-row
    pattern as `Drawing.supersedes`/`OrderLineAllocation
    .reassigned_from`, and requires a written reason. While building
    the classify/reclassify/batch-classify views, a genuine
    organization-isolation gap was caught and fixed before any commit:
    the classification target was being resolved by `ContentType` + pk
    alone, with no check that it belonged to the requester's
    organization. Fixed via a shared `_resolve_target_or_none()` helper
    that verifies the target through `apps.workflow.services
    .resolve_organization()` — which itself needed new `shipment`/
    `purchase_order`/`walkthrough` fallback chains (A55) to correctly
    resolve organization for `Container`, `PurchaseOrderLine`, and
    `WalkthroughItem`, none of which carry a direct organization field.
    New `/evidencias-sin-clasificar/` list/upload/detail screens
    (list supports batch-classifying multiple selected items at once),
    linked from the main nav. 14 new tests
    (`tests/test_evidence_inbox.py`), covering upload provenance
    (uploader/timestamp/filename/checksum) being established
    immediately, duplicate-content detection, classification to both
    coarse and leaf targets and the resulting status transitions,
    batch classification, reclassification requiring a reason and
    preserving full history, refusing to reclassify an already-
    superseded classification, organization isolation for both the
    evidence record itself and the classification target (including a
    dedicated test that a cross-organization classification attempt is
    silently refused rather than linking across organizations), and a
    full HTTP upload→classify→reclassify workflow — 368/368 passing
    (354 pre-existing + 14 new). Verified migrations apply cleanly from
    an empty database; live HTTP walkthrough uploaded a real
    historical-style photograph and classified it to a genuine unit
    (Apto A1) in the actual imported ARENA T1 Building 11, confirming
    the checksum and "Sin clasificar" state immediately after upload
    and the "Clasificado" state with the correct target after
    classification.
48. **M8 final multi-building validation.** Live-validated the full
    release across buildings beyond the Lawson-training pair used in
    earlier milestones: a quality-control walkthrough and a full
    corrective-issue lifecycle (report→assign→before/after evidence→
    correct→verify-close) end-to-end in SOLE 26 — a different family
    entirely from ARENA T1 — followed by a reinspection walkthrough
    created from it; a pre-delivery-final walkthrough in the real
    imported MARE B Building 25 (20 real units), including a blocked
    READY attempt, a correctly-refused unauthorized path, and a
    written-reason authorized override producing a real
    `AuditEvent.Action.WAIVER` row. Cross-building filtering
    (`?purpose=`) and the unfiltered dashboard were confirmed to
    surface ARENA T1 Building 9, SOLE 26, and MARE B Building 25
    side-by-side with no per-building branching. Two genuine defects
    were found and fixed during this pass, both from real HTTP
    requests, not from the test suite:
    (1) `apps.walkthroughs.views.item_create_issue` did not catch
    `apps.fieldissues.services.FieldIssueError` the way every sibling
    lifecycle view does, so submitting the corrective-issue form
    without a title crashed with an uncaught 500 instead of a friendly
    form error — fixed by wrapping the call and redirecting with
    `messages.error`, matching the existing pattern; a new HTTP-level
    regression test (`test_http_create_issue_without_title_shows_error_not_500`)
    confirms a 302 with no `FieldIssue` created rather than a 500.
    (2) `apps.walkthroughs.services.mark_delivery_decision` did not
    validate `decision` against `Walkthrough.DeliveryDecision`'s valid
    values before assigning it, so an invalid or missing decision
    value reached `walkthrough.save()` and raised a raw
    `IntegrityError` on the NOT NULL column instead of a clean
    `WalkthroughError` — fixed by validating up front; a new test
    (`test_invalid_decision_value_rejected_cleanly`) covers both
    `None` and an unrecognized string. 370/370 passing (368
    pre-existing + 2 new). `manage.py check` and
    `makemigrations --check --dry-run` both clean (no schema changes
    were needed for either fix).
49. **Interactive Apartment Plan / Room-Zone layer.** New
    `apps.unitplans` app (ADR-040). Before writing any code, directly
    inspected all 16 pages of the one architectural source PDF
    (`imports/buildings/PALMERA - PLANOS 13.11.2025.pdf`, rendered at
    150dpi via `pdftoppm`) to determine, honestly, which families
    actually have a per-unit-type floor plan: only PALMERA does
    (sheets H-05/06/07/08, "APARTAMENTO TIPO A/B/C/D", furnished and
    dimensioned) plus a real APT-number occupancy table (H-09, all 104
    units) that was cross-checked against — and found fully consistent
    with — the already-imported `apartment_letter` values from M1.
    ARENA T1, MARE B, SOLE, SOLE PH, and SOLE 26 have no per-unit
    drawing anywhere in the supplied sources (only the site-plan legend
    and the typology spreadsheet, both already used for the M1 import).
    `UnitPlanTemplate` resolves deterministically from family +
    `apartment_letter` + a computed floor-variant (first floor/upper
    floor/all floors/penthouse duplex — derived from the already-
    imported `Floor.level`/`Unit.is_penthouse`, A57); `PlanZone` holds
    room/zone type, relative rect/polygon coordinates, and its own
    validation lifecycle; `UnitPlanAssignment` links each `Unit` to its
    current effective template. Both template and zone reuse the exact
    versioned-immutable-row `supersedes`/`is_current` pattern as
    `Drawing`/`OrderLineAllocation`/`EvidenceClassification` — a
    `UniqueConstraint` scoped to `is_current=True` (not a plain
    unique-together) lets a superseded row keep its human-meaningful
    code without colliding with its replacement, mirroring
    `UnitPlanAssignment`'s existing one-current-per-unit constraint.
    `management/commands/seed_unit_plan_templates.py` seeds every
    family/letter/floor-variant slot actually present among imported
    units as `Missing Source`, then upgrades exactly the 4 real PALMERA
    templates with a genuine derived crop (cropped via Pillow from the
    150dpi render, uploaded through the normal Document/DocumentVersion
    mechanism — checksum-tracked like every other upload in this
    system) and AI-proposed rectangular zones, `Draft`/`Needs Review`
    by construction, never presented as architect-approved (the source
    sheet itself is stamped "PLANOS AUN EN PROCESO"). Interactive
    viewer (`/propiedades/unidades/<id>/plano/`, linked from the
    existing unit-detail page) shows the effective plan with clickable
    zone overlays (percentage-positioned `<div>`s over the derived
    image, no canvas/SVG library needed), per-zone open-issue counts,
    and create-issue/add-photo/create-walkthrough-item actions — each
    one prefilling building/floor/unit/room and storing a direct
    `plan_template`/`plan_zone` FK (added to `FieldIssue`,
    `WalkthroughItem`, and, for future display/traceability only,
    `InstallationRecord`/`InspectionRecord`, A62) so that record's
    "exact source-location reference" survives any later plan
    revision. The photo action reuses the Unclassified Evidence
    Inbox's upload-then-classify mechanism directly (`unitplans
    .planzone` added to `CLASSIFIABLE_TARGETS`); no new evidence model.
    A secure admin mapping screen (`/propiedades/admin-planos/`) lets
    an authorized user (`can_override_gates`) upload/replace a derived
    plan, add/edit zones (editing always supersedes rather than
    mutating in place — a second real gap caught and fixed this
    milestone, see below), validate zones, and approve or supersede a
    template — with an operational review queue surfacing every
    non-approved template, non-validated zone, and unmapped unit.
    24 new tests (`tests/test_unit_plans.py`), covering floor-variant
    resolution (first/upper/all/penthouse-duplex), template code
    determinism, mirrored-orientation recording, duplex per-level zone
    association, source-drawing/page provenance, polygon coordinate
    persistence, zone-click prefilling an issue and a walkthrough item
    with full traceability, supersede history preservation (both
    template- and zone-level), permission enforcement (unauthorized
    approve/validate/supersede all denied), cross-organization
    isolation for both the viewer and the admin screen, responsive
    markup presence, and idempotent re-seeding (a second command run
    creates zero duplicate templates/assignments/zones).

    Two genuine defects were found and fixed while building this, both
    from real execution, not from the test suite: (1) the original
    `UnitPlanTemplate` uniqueness was a plain `unique_together`
    (organization, code), which broke the very versioning pattern the
    model exists to support — superseding a template tried to insert a
    second row sharing the still-occupied code and raised a raw
    `IntegrityError`; fixed by scoping the constraint to
    `is_current=True`, matching `UnitPlanAssignment`'s existing
    pattern. (2) `supersede_template` created the new row *before*
    marking the old one non-current, so both briefly held
    `is_current=True` at once and hit the same constraint from the
    other direction — fixed by reordering the two writes. A third,
    behavioral gap (not a crash) was caught during live HTTP validation:
    superseding a template left every currently-assigned unit still
    pointed at the now-`SUPERSEDED` row, so the interactive viewer kept
    showing an explicitly-replaced plan by default — fixed by having
    `supersede_template` move every current assignment onto the new
    revision automatically (A60), verified live by superseding a
    template with 48 real assigned PALMERA units and confirming all 48
    moved while an unrelated, already-created `FieldIssue`'s own
    `plan_template`/`plan_zone` FKs stayed exactly on the superseded
    row. 394/394 tests passing (370 pre-existing + 24 new). Verified
    migrations apply cleanly to an empty database; live HTTP validation
    covered MARE B Building 25 Floor 4 Apartment C4 (correctly
    resolving to a Missing Source slot — no invented rooms — with
    building/floor/apartment still accurately displayed), a full real
    click-room → create-issue → upload-photo → confirm-full-provenance
    → open-issue-from-plan-and-highlight-room cycle against a real
    PALMERA Tipo B unit (the one family with genuine zones), repeated
    effective-plan resolution across SOLE Building 17, SOLE PH
    Building 18 (both its regular floors and its duplex penthouse),
    SOLE 26 Building 26, and ARENA T1 Building 9, and cross-organization
    denial (404) on the interactive viewer, the admin template screen,
    and the field issue itself.
50. **Controlled Transparency, Commercial Confidentiality &
    Authorization Foundation.** New `apps.governance` app (ADR-041):
    `Party` (wraps an existing `Organization` or `Supplier` rather than
    duplicating identity — never permanently hard-coded as "Factory"/
    "Trader"/"Seller"), `PartyMembership` (which logged-in users act for
    a Party — deliberately separate from `UserProfile.organization`, so
    Edison keeps his ordinary DT Beach login for every pre-existing
    module while separately holding a China-operator role for this new
    relationship, A66), `RoleAssignment` (package/project/organization-
    scoped, versioned, effective-dated), and `CapabilityGrant` — every
    APPROVE_*/AUTHORIZE_*/EXPORT_*/VIEW_PRIVILEGED_AUDIT-type action
    requires an explicit grant, never a role-implied default (A67).
    `Classification` (`OPERATIONAL_SHARED` default, preserving every
    pre-existing Document/Quotation/PurchaseOrder's current behavior
    exactly) and `VisibilityMode` (`CONTROLLED_CONFIDENTIALITY` default
    for a new package) gate read access through
    `CLASSIFICATION_REQUIRED_CAPABILITY`. `ProcurementPackage` (new, in
    `apps.procurement`) is hosted by one administering organization
    while cross-organization participants are represented purely
    through package-scoped role assignments — every package view/service
    authorizes through `governance.services`, never a bare organization-
    equality check (A64). Six commercial-layer objects stay genuinely
    distinct: `FactoryRFQ` (new); Factory Quote reuses the existing
    `Quotation`/`QuotationLine` directly; `InternalCommercialSheet` (new,
    full landed-cost breakdown + markup/margin); `ClientQuote` (new —
    structurally cannot expose factory identity/cost/markup/margin,
    those fields don't exist on the model); Client PO and Upstream
    Factory PO both reuse the existing `PurchaseOrder` via a new
    `po_kind` field. `client_safe_site_alias` computes a package-scoped
    "Verified Production Site N" alias on demand, never a persisted,
    cross-package-correlatable id (A70). `VerificationAssertion` refuses
    to attach an unverified `EvidenceBundle` (A69). `DisclosureGrant`
    supports partial, capability-gated, revocable field-level disclosure
    (revocation preserves history, never deletes it).
    `apps.audit.EvidenceBundle`/`EvidenceItem` extend the existing
    generic `Attachment` primitive: upload never implies verification,
    and `verify_evidence_item` hard-enforces uploader ≠ verifier plus
    the bundle's required capability. `governance.DerivedArtifact` +
    `create_derived_artifact` structurally enforce authorization-before-
    transformation — the injected `transform_fn` (standing in for a real
    translation/AI provider) receives only the already-authorized
    projection dict, proven with a spying test double that records
    exactly what it was given. `apps.workflow.GateOverride` (the
    existing exception mechanism) is reused unchanged, hardened with
    `expires_at`/`revoked_at`/`revoked_by`. `ChangeRequest` only applies
    against an already-frozen package, immediately places it on hold,
    and requires a field-specific capability to approve
    (`APPROVE_VISIBILITY_CHANGE` for visibility mode,
    `APPROVE_ROLE_CHANGE` for critical roles). `RiskFlag` is a
    foundation-only signal (STANDARD/CONTROLLED_OPAQUE/HIGH_RISK) that
    never declares legality or approves an opaque transaction.

    Full HTTP surface (`apps/procurement/package_views.py`,
    `apps/governance/views.py`): package list/detail with role-based
    projection (factory-quote and internal-cost sections render only
    for capability-holding users — never hidden client-side), factory-
    quote/internal-sheet/client-quote creation, client-quote approval
    (enforcing prepare/approve separation live), package freeze, Change
    Request create/approve/reject, Disclosure Grant create/revoke,
    Evidence Bundle create/upload/verify, and Party/privileged-audit
    admin screens gated by the same `can_override_gates` senior
    permission used everywhere else in this system.

    A genuine bug was found and fixed during live HTTP validation, not
    from the test suite: six permission-gated service functions wrapped
    their *entire* body — including the capability check and its
    denial-audit-log call — in one `@transaction.atomic`, so logging a
    `PRIVILEGED_ACCESS_DENIED` event immediately before raising was
    rolled back along with the (never-attempted) mutation; denied
    attempts silently never reached the audit trail. Fixed by moving the
    atomic boundary to start only after the permission check (A71).

    62 new tests across `tests/test_governance.py` (10),
    `tests/test_procurement_confidentiality.py` (11),
    `tests/test_evidence_and_disclosure.py` (13),
    `tests/test_derived_artifacts.py` (12),
    `tests/test_workflow_template_preservation.py` (3), and
    `tests/test_confidentiality_http.py` (12), plus one field-level
    diligence check (`address` added to `Supplier`) — covering Party/
    Role/Capability separation, classification-gated visibility, the
    full commercial-layer lifecycle with prepare/approve separation,
    evidence-bundle uploader/verifier separation and missing-requirement
    detection, verification-assertion/disclosure-grant mechanics
    (partial scope, expiration, revocation-preserves-history),
    authorization-before-transformation with a spying mock, package
    freeze/change-request/hold, cross-organization 404 denial (with no
    leak through the package list or the API), and denied attempts now
    correctly appearing in the audit trail. 455/455 tests passing
    overall. Migrations apply cleanly to an empty database.

    Live HTTP validation performed end-to-end via
    `manage.py seed_confidentiality_demo` (DT Beach buyer org, China
    Trading Co, Edison as China procurement operator, a hidden China
    tile factory, a DT Beach client user) plus real curl-driven
    workflow: factory quote submitted, internal commercial sheet
    prepared (landed cost/margin computed correctly), client quote
    prepared by Edison and approved by a *separate*, explicitly-granted
    buyer-approver user (Edison's own attempt to approve his own
    quote was denied and left the quote in Draft), an evidence bundle
    uploaded and — critically — still `incomplete`/unverified until an
    independent authorized user (not the uploader) verified it, a
    client-safe verification assertion created from that now-verified
    bundle, the DT Beach client viewing the approved client quote and
    the verification statement while the factory-quote and internal-
    cost sections were completely absent from the rendered page (not
    merely hidden by CSS) and the upstream factory PO returned zero
    results through the existing `/api/v1/purchase-orders/` endpoint, a
    partial Disclosure Grant revealing only the manufacturer name
    (confirmed address/cost/markup/margin still hidden) followed by
    revocation and confirmed future-access removal, package freeze
    capturing a full role/term snapshot, an unauthorized change-request
    approval attempt correctly denied, an authorized approval releasing
    the hold and applying the change, and a fully unrelated
    cross-organization user receiving 404 on the package detail page
    with no trace of the package's existence in their own package list.

## Gate 0 — Baseline & Documentation Reconciliation

51. **Completed Gate 0 as a documentation-only repository reconciliation.**
    Read the approved governing roadmap at
    `docs/MARKETMATCH_ARCHITECTURE_RECONCILIATION_AND_ROADMAP.md` and retained
    its milestone order and architecture decisions without redesign. Fresh Git
    evidence established branch
    `integration/dt-beach-supply-control-1.0.0`, pre-reconciliation HEAD
    `5cd0df64edc5baf89a0e3e4e3efe8e1fc78d0b2c`, upstream
    `origin/integration/dt-beach-supply-control-1.0.0`, and ahead/behind `0/0`
    after fetching. The initial working tree was clean with no tracked changes
    and no untracked files; a push dry-run reported `Everything up-to-date`.

    Migration verification passed: `manage.py check` found no issues,
    `makemigrations --check --dry-run` found no model changes,
    `migrate --check` found no unapplied migrations, and every migration in
    `showmigrations --plan` was applied. The full regression suite collected
    455 tests and passed **455/455** with zero failures in 71.78 seconds.

    Reconciled `README.md`, `DT_BEACH_CURRENT_STATE.md`,
    `DT_BEACH_SOURCE_OF_TRUTH_INDEX.md`, `REQUIREMENTS_TRACEABILITY.md`, and
    `KNOWN_LIMITATIONS.md` to the fresh evidence. The primary corrected drift
    was the recorded HEAD (`8b7e102` → verified pre-Gate-0 `5cd0df6`), the
    evidence-package state (already tracked, not untracked), the README's
    historical 20-app/246-test counts (27 app directories/455 passing tests),
    and stale traceability statements that still called the landed-cost UI and
    receiving manifest planned after their later implementation.

    Evidence-package disposition: retained and committed
    `DT_BEACH_CURRENT_STATE.md`, `DT_BEACH_SOURCE_OF_TRUTH_INDEX.md`,
    `Fable Interactive Plans Final Report 90874ed.md`,
    `Fable Property Master Final Report 69f89a3.md`, and
    `Git Evidence 90874ed.txt`; committed the newly supplied governing roadmap
    in `docs/`. No evidence was deleted, ignored, relocated, or archived.

    `ASSUMPTIONS.md`, `architecture-decisions.md`, and `SECURITY.md` required no
    factual change. No application code, migrations, behavior, templates, or
    tests changed. Gate 0 ends with one documentation-only commit pushed to the
    configured upstream and a clean working tree.

    Latest completed product milestone remains **Controlled Transparency,
    Commercial Confidentiality & Authorization Foundation**. Exact next action:
    **Milestone 1 — Configurable Procurement Gates A1–A6**. Milestone 1 was not
    started during Gate 0.

## Controlled Transparency / Confidentiality foundation remediation

52. **Remediated the accepted integration-level foundation findings without
    beginning Milestone 1.** Work began from clean, synchronized HEAD
    `9f0b52d9c74ef1c1f5f8f7710794f28e355bd44e` on
    `integration/dt-beach-supply-control-1.0.0`. No A1–A6 models, policy
    versions, definitions, evaluations, or fields were introduced, and no
    migration was required.

    Package discovery now requires an active package role, an active
    package-scoped capability, or the existing same-tenant executive
    `can_override_gates` authority; bare hosting-organization equality no
    longer grants access. Package list/detail and direct mutation routes return
    non-disclosing 404 responses for unrelated same-organization and
    different-organization users.

    Evidence create/add/verify/reject authorization is derived from the
    persisted EvidenceBundle target. A posted package identifier is only a
    consistency assertion and cannot lend authority from another package.
    Privileged denials are durable. Classified document list/detail/download
    querysets apply package participation and classification before metadata
    retrieval or storage open. Temporary-storage tests prove authorized byte
    delivery and zero storage reads for unauthorized requests.

    Restricted participants now receive only approved/sent ClientQuotes and
    current, non-revoked VerificationAssertions; separately authorized
    internal users retain draft access. Quote preparers cannot approve their
    own quote even when explicitly granted the approval capability.
    DisclosureGrant creation freezes an allowlisted projection, package-facing
    rendering consumes only live field-scoped values, and expiry/revocation
    removes them from later responses. Revocation requires target-package
    `AUTHORIZE_DISCLOSURE` and denial is audited.

    Change Request approval and rejection use the same field-specific
    capability and reject unsupported route decisions. Package hold state is
    recomputed transactionally across all pending Change Requests and
    unresolved non-standard RiskFlags. Risk create/resolve uses the narrowly
    extended package-scoped `MANAGE_RISK_FLAGS` capability, validates state,
    rejects duplicate active flags/repeated resolution, and preserves denial
    auditing. VerificationAssertion revocation is capability-gated and audited.

    The currently exposed package-associated PurchaseOrder API and linked
    ManifestLine API now authorize and classify before queryset evaluation,
    list counts, serialization, filters, or detail retrieval. This is the
    accepted narrow repair, not generalized Milestone 3 API convergence.
    VisibilityMode remains versioned policy metadata: neither value bypasses
    authorization; controlled-transparency field release is executable only
    through active Disclosure Grants.

    Added `tests/test_foundation_remediation.py` with 17 adversarial tests for
    same-organization, different-organization, and cross-package actors;
    direct identifiers; server-side sentinel absence; API counts and detail;
    disclosure expiry/revocation; Change Request holds; risk state; quote
    separation of duties; assertion validity/revocation; and temporary-storage
    document bytes. Updated the pre-existing evidence tests so every uploader
    has actual `CREATE_EVIDENCE` authority and corrected the client projection
    test to use an approved quote.

    Final local evidence: `manage.py check` passed; migration drift and
    application checks passed with no new migration; the focused
    foundation/remediation suite (`pytest tests/test_foundation_remediation.py
    tests/test_procurement_confidentiality.py tests/test_confidentiality_http.py
    tests/test_evidence_and_disclosure.py`) passed **53/53**; the complete
    suite passed **472/472**. This is local executable evidence, not
    deployed-runtime or production-operations evidence. Exact next action:
    independent revalidation of this remediation. Milestone 1 remains
    prohibited until that control point accepts the foundation.

    *Correction, foundation correction cycle 2 (see entry 53 below):* this
    entry originally reported the focused-suite figure as "78/78". That
    figure was not reproducible against the exact command above and has
    been corrected to the true, reproducible result, **53/53**
    (CTCF-DOC-020).

53. **Foundation correction cycle 2 — closed CTCF-AUDIT-017 and
    CTCF-CR-PROJ-018, corrected the CTCF-DOC-020 test-count discrepancy,
    recorded the CTCF-ASSERT-HTTP-019 boundary.** Work began from clean,
    synchronized HEAD `a89a9f714684515be1b2de704bf816611e094540` on
    `integration/dt-beach-supply-control-1.0.0` — the exact commit an
    independent Fable revalidation of entry 52 confirmed closed all 14
    non-deferred originally-accepted findings, while separately
    discovering two new gaps outside that original set. No A1–A6 or
    Milestone 2+ work was introduced; no migration was required.

    **CTCF-AUDIT-017.** `governance.views.privileged_audit` previously
    required only `can_override_gates` (no explicit `VIEW_PRIVILEGED_AUDIT`
    capability check anywhere in the codebase, despite that capability
    already existing in `ALL_CAPABILITY_CODES` for exactly this purpose)
    and applied no organization/package scope to the underlying
    `AuditEvent` queryset — any senior role-holder in any tenant
    organization could read every organization's privileged audit
    summaries, which embedded hidden-factory names, package identities,
    and Disclosure Grant field scopes via unrestricted model `__str__`
    rendering. Access now requires an explicit, currently-active
    `VIEW_PRIVILEGED_AUDIT` `CapabilityGrant`
    (`governance.services.authorized_privileged_audit_scopes`); the
    `AuditEvent` queryset is scoped before any event is treated as visible
    (`privileged_audit_queryset`), resolving each event's target through
    existing relationships (reusing `apps.audit.services
    .evidence_bundle_package` for evidence targets — no second resolver);
    an unresolvable target is excluded, never included. The rendered
    projection (`privileged_audit_projection`) shows only action type,
    actor, and timestamp with a fixed generic description — never a raw
    `__str__` or `AuditEvent.metadata`. A denied access attempt is now
    itself durably audited. See ADR-042.

    **CTCF-CR-PROJ-018.** `package_detail` previously placed every
    `ChangeRequest` for a package into the template context
    unconditionally — package participation alone, not any field-specific
    decision authority, controlled what a viewer saw, including
    `field_name`/`frozen_current_value`/`proposed_new_value`/`reason`.
    `governance.services.change_request_projection` now distinguishes
    knowing a request exists, reading its raw values, and deciding it —
    reusing the existing `CHANGE_REQUEST_APPROVAL_CAPABILITY` mapping as
    the same authority required for detailed read access, plus an
    exception for the requester and the decider. Every other
    package-authorized viewer receives a safe, generic projection. The
    queryset uses `.only()` on non-sensitive columns so the sensitive text
    fields are not fetched for rows that end up projected as safe-only.
    Decision-button visibility (`can_decide`) is computed independently
    and never substitutes for read authorization. See ADR-042.

    **CTCF-DOC-020.** Corrected the unreproducible "78/78" focused-suite
    figure (entry 52 above, and `DT_BEACH_CURRENT_STATE.md`) to the true,
    reproducible **53/53**.

    **CTCF-ASSERT-HTTP-019.** No HTTP route was added for
    `procurement.services.revoke_verification_assertion` this cycle — it
    remains service-only, by explicit recorded decision. Whether
    `CREATE_COMMERCIAL_DOCUMENT` is the correct authority for that
    revocation remains an open question requiring a future owner decision
    or direct documentary evidence before any interface is connected to
    that service; no interface was connected this cycle.

    Added `tests/test_privileged_audit_scope.py` (15 adversarial tests:
    authority matrix including `can_override_gates`-alone denial, expired/
    revoked/future grants, cross-organization and cross-package isolation,
    unresolvable-target fail-closed behavior, information-absence, and
    denial-audit durability) and `tests/test_change_request_projection.py`
    (9 tests: restricted-viewer safe projection, requester/decider
    exceptions, one-field-capability-does-not-reveal-another-field,
    package-B-authority-does-not-reveal-package-A, and
    approve/reject/hold-recomputation regression). Updated
    `tests/test_confidentiality_http.py::test_authorized_admin_can_view_governance_screens`
    to grant the explicit `VIEW_PRIVILEGED_AUDIT` capability the new
    authorization model requires (the prior version relied on
    `can_override_gates` alone, which is exactly the defect closed here).
    Updated `apps.procurement.management.commands.seed_confidentiality_demo`
    to seed an organization-scoped `VIEW_PRIVILEGED_AUDIT` grant for the
    live-validation scenario's senior user.

    Final local evidence: `manage.py check` passed; migration drift and
    unapplied-migration checks passed with no new migration (72/72 applied);
    the focused foundation/remediation suite passed **53/53** (48.93s); the
    complete suite passed **496/496** (111.11s) — 472 prior plus 24 new.
    All figures were produced by the exact commands recorded in
    `DT_BEACH_CURRENT_STATE.md`. This is local SQLite executable evidence
    only — PostgreSQL was not available in this session (no Docker
    daemon), and this correction has **not yet been independently
    revalidated**. Exact next action: run a new, independent Fable 5
    revalidation session against the resulting commit. Milestone 1 remains
    prohibited until that revalidation accepts the foundation.

54. **Foundation correction cycle 3 — closed CTCF-AUDIT-SCOPE-021,
    CTCF-AUDIT-RETRIEVAL-022, and CTCF-AUDIT-WINDOW-023, all three found
    and reproduced with direct evidence during an independent Fable
    revalidation of foundation correction cycle 2's own commit
    (`84b12a2187d93f2ccd9992780a5a4b73e54e7cc6`).** No A1–A6 or Milestone
    2+ work was introduced; no migration was required.

    **CTCF-AUDIT-SCOPE-021.** `governance.services._resolve_scope_for_target`
    did not follow a `CapabilityGrant` target's `role_assignment` when the
    grant's own `package`/`organization` columns were unset — a valid,
    pre-existing pattern (`grant_capability(..., role_assignment=assignment)`
    without also passing `package=`/`organization=`) that this same
    codebase's fixtures and tests already used elsewhere. Such a
    `CAPABILITY_GRANT` audit event resolved to no scope at all and was
    excluded even for an otherwise fully-authorized viewer — a fail-closed,
    under-inclusion defect, reproduced directly during the revalidation
    (`_resolve_scope_for_target(grant)` returned `(None, None)` against an
    expected `(organization_id, package_id)`). The resolver now explicitly
    checks `CapabilityGrant.role_assignment` as a third resolution step,
    after direct `package` and direct `organization`. `RoleAssignment.project`
    is not consulted: `RoleAssignment.organization_context` is a required
    field, so it always resolves first, making a project-based fallback
    structurally unreachable (A77).

    **CTCF-AUDIT-RETRIEVAL-022.** `privileged_audit_queryset`'s candidate
    scan previously selected full `AuditEvent` rows — including `summary`
    and `metadata`, columns the safe projection never uses — for every
    candidate, authorized or not, before the per-row scope decision,
    confirmed by direct `.query` SQL inspection during the revalidation.
    The function now selects only `id`/`action`/`occurred_at`/`actor_id`/
    `content_type_id`/`object_id` at every phase; neither `summary` nor
    `metadata` is ever selected by this function, for any row, at any
    point. Verified by SQL-capture tests
    (`TestRetrievalBeforeAuthorization`) asserting no query issued by this
    path contains either column name.

    **CTCF-AUDIT-WINDOW-023.** The prior scan capped candidate inspection
    at the 1,000 most-recent events; an authorized event older than that
    many unrelated, unauthorized events could be silently omitted — a real
    completeness gap the revalidation reproduced directly (1,005
    unauthorized events plus one older authorized event; the old design
    would have missed the older event). The scan is now a
    deterministic-order (`-occurred_at, -id`), cursor-paginated loop over
    batches of 200 safe-column-only candidates, continuing across as many
    batches as needed until the requested result limit is satisfied or
    candidates are genuinely exhausted (A76). No count or volume signal
    about excluded events is exposed at any point.

    Added 15 new tests to `tests/test_privileged_audit_scope.py`:
    `TestCapabilityGrantScopeInheritance` (9 tests — direct package/
    organization scope still resolves; role_assignment-only package and
    organization scope now resolves; a role_assignment-derived grant's own
    `CAPABILITY_GRANT` event becomes visible; package scope does not widen
    to organization scope; no-scope-at-all fails closed; expiry is
    respected; package A does not expose package B), `TestRetrievalBeforeAuthorization`
    (3 tests — direct SQL-capture assertions that no query selects
    `summary`/`metadata`, for both authorized and unauthorized candidate
    rows, and that the HTTP response/context never carries either field),
    and `TestScanWindowCompleteness` (3 tests — an authorized event older
    than 1,005 newer unauthorized events remains visible; the newest 200
    authorized events are selected, not merely the newest events globally,
    verified against an independently-computed ground-truth query rather
    than a manually reconstructed expectation; no unauthorized count or
    volume signal is exposed). `tests/test_change_request_projection.py`
    was not modified — this cycle's scope is strictly the privileged-audit
    mechanism.

    Final local evidence: `manage.py check` passed; migration drift and
    unapplied-migration checks passed with no new migration (72/72
    applied); the focused foundation/remediation suite passed **53/53**
    (49.76s); `tests/test_privileged_audit_scope.py` plus
    `tests/test_change_request_projection.py` together passed **39/39**
    (46.15s — 31 privileged-audit, 8 Change Request); the complete suite
    passed **511/511** (113.25s) — 496 prior plus 15 new. All figures were
    produced by the exact commands recorded in `DT_BEACH_CURRENT_STATE.md`.
    This is local SQLite executable evidence only — PostgreSQL was not
    available in this session (no Docker daemon), and this correction has
    **not yet been independently revalidated**. Exact next action: run a
    new, independent Fable 5 revalidation session against the resulting
    commit. Milestone 1 remains prohibited until that revalidation accepts
    the foundation.

55. **Harrison's explicit owner and business acceptance recorded; the
    Controlled Transparency, Commercial Confidentiality & Authorization
    Foundation is closed.** Dated 2026-07-20, America/Santo_Domingo.
    Documentation-only entry — no application code, template, test, or
    migration was touched.

    An independent Fable revalidation of foundation correction cycle 3
    (commit `2c52b0b83340fda2eaa84700eaeddfbe0839d6d8`) confirmed
    CTCF-AUDIT-SCOPE-021, CTCF-AUDIT-RETRIEVAL-022, and
    CTCF-AUDIT-WINDOW-023 were all independently closed, with no new
    Critical or High blocker, no regression of Cycle 2 or any prior
    foundation finding, and no A1–A6 or Milestone 2+ work present. That
    revalidation reproduced fresh: `manage.py check` clean; migration
    drift and unapplied-migration checks clean, **72/72 migrations
    applied**; focused foundation/remediation suite **53/53**; Cycle 2+3
    suite (`tests/test_privileged_audit_scope.py` +
    `tests/test_change_request_projection.py`) **39/39**; full suite
    **511/511** — all SQLite, PostgreSQL not available and not claimed.
    It also independently measured, via disposable adversarial data, a
    privileged-audit scope-resolution query cost of approximately 65 SQL
    queries for 31 all-resolvable-but-unauthorized candidate events
    (~2.10 queries/event) — classified as a Low-severity, non-blocking,
    pilot-scale performance limitation, not a confidentiality defect.

    Based on that independent evidence, Harrison recorded explicit owner
    and business acceptance:

    > I explicitly accept Foundation Correction Cycles 2 and 3 at commit
    > 2c52b0b83340fda2eaa84700eaeddfbe0839d6d8. I accept the independently
    > validated Controlled Transparency, Commercial Confidentiality &
    > Authorization Foundation as technically complete for the current
    > roadmap gate. I accept the measured privileged-audit linear N+1
    > query characteristic as a non-blocking, pilot-scale performance
    > limitation. PostgreSQL runtime validation, database-level audit
    > append-only enforcement, backup restoration, deployed proxy/cache
    > validation, production log-sentinel analysis, and all other
    > documented owner or production validations remain open limitations
    > and are not represented as complete. CTCF-ASSERT-HTTP-019 remains
    > deferred. No Verification Assertion revocation HTTP route is
    > approved or implemented. The Controlled Transparency, Commercial
    > Confidentiality & Authorization Foundation is now closed. The next
    > authorized activity is the independent Milestone 1 Charter review
    > only. Procurement Gates A1–A6 implementation is not authorized yet.

    **Status distinctions, precise:** the foundation's code is
    *implemented* (all four commits: `5cd0df6` baseline through `2c52b0b`
    correction cycle 3); it is *independently revalidated* (three separate
    Fable passes, the last against this exact commit); it is now *owner
    accepted* (this entry). It is **not** PostgreSQL-validated (SQLite
    only, throughout every cycle) and **not** deployed (no production
    deployment of this foundation has occurred or is claimed). These
    remain open, explicitly, per Harrison's own acceptance text above —
    acceptance of the foundation does not convert any of these into
    claimed-complete evidence.

    Exact next action: run the independent Milestone 1 Charter review
    against the current documentation baseline. A1–A6 implementation is
    not authorized by this entry.

56. **Milestone 1 Charter Definition and Reconciliation — documentation-only.**
    Dated 2026-07-20. No application code, template, test, or migration was
    touched.

    The independent Milestone 1 Charter Review that followed entry 55 found
    that no standalone Milestone 1 charter existed — only a 26-line
    acceptance-criteria summary in
    `docs/MARKETMATCH_ARCHITECTURE_RECONCILIATION_AND_ROADMAP.md` §3 — and
    raised twelve findings (CHTR-001 through CHTR-012), including a direct
    conflict between that roadmap summary's "reuse `GateOverride`" clause
    and ADR-020's existing, documented reasoning against exactly that kind
    of reuse.

    This cycle authored a complete, standalone charter at
    `docs/MILESTONE_1_PROCUREMENT_GATES_CHARTER.md` (charter version 1),
    resolving all twelve findings: a new `apps.procurement_gates` domain
    kept fully separate from the existing eight-gate Handoff workflow and
    from `ProcurementPackage.Status`; a versioned, publish-immutable gate
    policy architecture with a canonical default, organization
    configuration, and transactionally-protected package pinning; a
    deterministic, non-fabricating existing-package migration; an
    immutable `PackageFreezeRevision` history distinct from the current
    `frozen_snapshot` cache; a dedicated `GateAttempt`/`GateEvaluation`/
    `GateDecision`/`GateInvalidation` record architecture with a new,
    enumerated `AuditEvent.Action` taxonomy; a new, domain-native
    `ProcurementGateOverride` model (ADR-044) that reuses
    `apps.workflow.GateOverride`'s lifecycle pattern without reusing its
    row or foreign keys; an absolute non-overridable-controls list; a
    deterministic evidence-classification rule; an explicit PostgreSQL
    closure gate for six named concurrency scenarios; a narrow API/UI
    boundary against Milestone 3; and a real HTTP/browser live-validation
    method distinct from automated tests.

    Documentation reconciled in the same commit: `DT_BEACH_CURRENT_STATE.md`,
    `DT_BEACH_SOURCE_OF_TRUTH_INDEX.md`, `README.md`, `ASSUMPTIONS.md`
    (A78, A79), `docs/architecture-decisions.md` (ADR-044),
    `docs/SECURITY.md`, `docs/KNOWN_LIMITATIONS.md`, and
    `docs/REQUIREMENTS_TRACEABILITY.md`.

    Fresh evidence for this cycle: HEAD confirmed at
    `4bc90224dae0e8adb87cdae72742a76a028de8bd` before editing; branch
    `integration/dt-beach-supply-control-1.0.0`; upstream
    `origin/integration/dt-beach-supply-control-1.0.0`; ahead/behind `0/0`;
    working tree clean before editing. `manage.py check` passed;
    `makemigrations --check --dry-run` reported no changes detected;
    `migrate --check` passed with 72/72 migrations applied, 0 pending —
    unchanged from entry 55, as expected for a documentation-only cycle.
    The full regression suite was **not** rerun for this cycle, by design —
    it was already independently reproduced at **511/511** during the
    revalidation that led to entry 55's acceptance, and documentation
    changes cannot alter Python test outcomes; that figure is carried
    forward here, not re-claimed as freshly rerun.

    **Status distinctions, precise:** this Charter is *authored* (this
    entry); it is **not** *independently revalidated* and **not** *owner
    approved*. A1–A6 remain entirely *unimplemented* — nothing in this
    entry changes that. Milestone 1 implementation is **not authorized**
    by this entry.

    Exact next action: run an independent Fable 5 Charter revalidation
    session against the commit introducing
    `docs/MILESTONE_1_PROCUREMENT_GATES_CHARTER.md`. Do not begin A1–A6
    implementation.

57. **Milestone 1 Charter Correction Cycle — documentation-only.** Dated
    2026-07-20. No application code, template, test, or migration was
    touched.

    The independent Milestone 1 Charter Revalidation that followed entry
    56 reread Charter version 1 (commit
    `b0cdf1cc4f6fb17dea206430ee0f1643710d2090`) fresh against itself and
    against the actual reused model code, and returned **MILESTONE 1
    CHARTER REQUIRES CORRECTION** with twelve new findings, REVAL-001
    through REVAL-012 — internal contradictions and omissions in version
    1's own text, distinct from the original CHTR-001–CHTR-012 review,
    which remained resolved. The most severe: a `GateAttempt`↔`EvidenceBundle`
    cardinality contradiction between §5.1 and §9.2 (REVAL-001); an
    assumption that `EvidenceBundle` stores per-requirement
    `minimum_count`/`required_verifier_capability`/`minimum_review_state`
    when the actual, reused model stores these only bundle-wide
    (REVAL-002); two incompatible definitions of "the canonical default
    policy" within §3 itself (REVAL-003); and a post-A2 invalidation
    cascade that invalidated `GateDecision` rows but never revoked active
    `ProcurementGateOverride` rows on A3–A6, letting a stale exception
    silently survive a critical change (REVAL-004).

    This cycle corrected all twelve findings in Charter version 2:
    `GateAttempt` no longer carries an `evidence_bundle` FK (evidence
    attaches only via generic target, one or more bundles per attempt,
    grouped deterministically by a `(required_verifier_capability,
    minimum_review_state, minimum_count)` tuple, with `EvidenceBundle`/
    `EvidenceItem` left completely unmodified); `is_canonical_default` is
    now the sole canonical-default determinant, with `organization = NULL`
    redefined as mere eligibility; the post-A2 cascade now also revokes
    every active A3–A6 override, system-attributed and audited; a package's
    first `A1` attempt is now authorized through an explicit
    organization-scoped exception (REVAL-005); the two overlapping
    override-eligibility flags are now explicitly composed
    (`overridable` + `non_overridable_requirements`, REVAL-006); an exact
    locked `attempt_number` allocation algorithm is specified (REVAL-007);
    approving an override now closes its `GateAttempt`, and expiry/
    revocation never reopens it (REVAL-008); the `PackageFreezeRevision.policy_version`
    field's rationale was rewritten truthfully (REVAL-009); policy
    publication now rejects any `gate_schema` missing or adding to the
    exact six `A1`–`A6` keys (REVAL-010); one shared, enumerated
    frozen-field code registry now governs both `ChangeRequest.field_name`
    validation and `frozen_fields` keys (REVAL-011); and the completion
    cross-reference was corrected from "§1–§17" to "§1–§19" (REVAL-012).
    §21.2 of the Charter records the complete disposition table.

    Documentation reconciled in the same commit: `DT_BEACH_CURRENT_STATE.md`,
    `DT_BEACH_SOURCE_OF_TRUTH_INDEX.md`, `README.md`, `ASSUMPTIONS.md`
    (A80, A81), `docs/architecture-decisions.md` (ADR-044 correction
    addendum, not reopened), `docs/SECURITY.md`, and
    `docs/KNOWN_LIMITATIONS.md`.

    Fresh evidence for this cycle: HEAD confirmed at
    `b0cdf1cc4f6fb17dea206430ee0f1643710d2090` before editing; branch
    `integration/dt-beach-supply-control-1.0.0`; upstream
    `origin/integration/dt-beach-supply-control-1.0.0`; ahead/behind `0/0`;
    working tree clean before editing. `manage.py check` passed;
    `makemigrations --check --dry-run` reported no changes detected;
    `migrate --check` passed, 72/72 migrations applied, 0 pending —
    unchanged, as expected for a documentation-only cycle. The full
    regression suite was **not** rerun for this cycle, by design — it was
    already independently reproduced at **511/511** during the
    revalidation that led to entry 55's foundation acceptance, and
    documentation changes cannot alter Python test outcomes; that figure
    is carried forward here, not re-claimed as freshly rerun.

    **Status distinctions, precise:** Charter version 2 is *authored* and
    *corrected against every accepted revalidation finding* (this entry);
    it is **not** *independently revalidated* and **not** *owner approved*.
    A1–A6 remain entirely *unimplemented* — nothing in this entry changes
    that. Milestone 1 implementation is **not authorized** by this entry.

    Exact next action: run a new, independent Fable 5 Charter revalidation
    session against the commit introducing Charter version 2. Do not begin
    A1–A6 implementation.

58. **Milestone 1 Charter Correction Cycle 2 — documentation-only.** Dated
    2026-07-20. No application code, template, test, or migration was
    touched.

    The independent Milestone 1 Charter Version 2 Revalidation that
    followed entry 57 reread Charter version 2 (commit
    `3b62228b4a6efb4079e7f8c010e107fcf9de639a`) fresh against itself, the
    actual repository models (`apps.audit.EvidenceBundle`,
    `apps.governance.ChangeRequest`/`CapabilityGrant`, `apps.procurement.ProcurementPackage`),
    and — critically — the actual, unmodified
    `apps.governance.services.request_change` function, and returned
    **MILESTONE 1 CHARTER VERSION 2 REQUIRES CORRECTION** with ten
    findings. Every REVAL-001–REVAL-012 correction from version 2 was
    independently confirmed textually resolved, but two of them left
    residual gaps under adversarial follow-through, and five further
    findings were newly discovered:

    - **NF-1 (Critical):** §8.2's claim that non-critical Change Requests
      "never touch hold state" was contradicted by
      `apps.governance.services.request_change` (`apps/governance/services.py:677-688`),
      which unconditionally sets `is_on_hold = True` on every
      `ChangeRequest` it creates, critical or not.
    - **REVAL-004-RESIDUAL (High):** the post-A2 cascade revoked overrides
      only on A3–A6, never addressing an `A2` gate left `OVERRIDDEN` by a
      policy-opt-in override version 2 itself permitted.
    - **REVAL-005-RESIDUAL (High):** the A1-bootstrap grant's scope shape
      was specified, but its exact capability code was never named.
    - **REVAL-008-RESIDUAL (Critical):** no behavior was defined for a
      downstream `PASSED`/`OVERRIDDEN` gate when the predecessor override
      it relied on later expired or was revoked.
    - **REVAL-009-TRACE (Medium):** §7.1 promised a required §18 test for
      the `PackageFreezeRevision.policy_version` equality invariant that
      did not exist in §18.
    - **REVAL-011-ENFORCEMENT (Medium):** the frozen-field registry's
      enforcing "service layer" and its dependency direction against
      `apps.governance` were never named.
    - **NF-2 (Medium):** `PackagePolicyAssignment.superseded_by` was
      defined but structurally unusable under §3.4's own permanent-pin
      rule, with no rationale given.
    - **NF-3 (High):** attempt-creation capability codes for A2–A6 were
      never named, and §13 referenced a `gate_schema` field that did not
      exist in §3.1/§3.2's validated field list.
    - **NF-4 (Medium):** no `on_delete` behavior was specified for any FK
      on any of the ~8 new Charter models.
    - **NF-7 (Low):** the illustrative canonical-default
      `UniqueConstraint(fields=[], ...)` example was invalid Django syntax.

    This cycle corrected all ten findings in Charter version 3:
    `apps.procurement_gates.services.request_gate_aware_change` is now the
    sole Milestone 1 Change Request entry point, wrapping the unmodified
    `request_change` with its own hold-state recomputation from all open
    `PackageHoldCause` rows (§8.2.1, NF-1); `A2` is now unconditionally,
    permanently non-overridable, enforced by publication-time rejection,
    closing the REVAL-004-RESIDUAL gap by construction rather than by
    special-casing the cascade further (§3.2, §12.1); a new capability
    code, `CREATE_PROCUREMENT_GATE_ATTEMPT`, is added and named explicitly
    for both the A1-bootstrap path and every A2–A6 attempt-creation path
    (§13, NF-3/REVAL-005-RESIDUAL); an explicit, locked
    downstream-invalidation cascade is defined for lapsed predecessor
    overrides, mirroring §8.1's shape, with a new `GATE_DOWNSTREAM_INVALIDATED`
    audit action (§9.5, §10, REVAL-008-RESIDUAL); the missing
    `policy_version` equality-invariant test was added (§18 test 18a,
    REVAL-009-TRACE); the registry's enforcement point and one-way
    dependency direction against `apps.governance` were named explicitly
    (§8.2.2, REVAL-011-ENFORCEMENT); `PackagePolicyAssignment.superseded_by`
    was removed entirely (§3.4, NF-2); a complete, field-by-field
    `on_delete` table was added for every new model, `PROTECT`-by-default
    with exactly one documented `CASCADE` exception (§3.5, NF-4); and the
    invalid `UniqueConstraint` example was replaced with valid Django
    syntax (§3.3, NF-7). §21.3 of the Charter records the complete
    disposition table.

    Documentation reconciled in the same commit: `DT_BEACH_CURRENT_STATE.md`,
    `DT_BEACH_SOURCE_OF_TRUTH_INDEX.md`, `README.md`,
    `docs/architecture-decisions.md` (ADR-045, new entry, does not reopen
    ADR-044), `docs/SECURITY.md`, `docs/KNOWN_LIMITATIONS.md`,
    `docs/REQUIREMENTS_TRACEABILITY.md`, and
    `docs/MARKETMATCH_ARCHITECTURE_RECONCILIATION_AND_ROADMAP.md`.

    Fresh evidence for this cycle: HEAD confirmed at
    `3b62228b4a6efb4079e7f8c010e107fcf9de639a` before editing; branch
    `integration/dt-beach-supply-control-1.0.0`; upstream
    `origin/integration/dt-beach-supply-control-1.0.0`; ahead/behind `0/0`;
    working tree clean before editing. `manage.py check` passed;
    `makemigrations --check --dry-run` reported no changes detected;
    `migrate --check` passed, 72/72 migrations applied, 0 pending —
    unchanged, as expected for a documentation-only cycle. The full
    regression suite was **not** rerun for this cycle, by design — it was
    already independently reproduced at **511/511** during the foundation
    acceptance revalidation, and independently reproduced again (39/39 and
    53/53 on the targeted privileged-audit/foundation suites) during the
    Charter Version 2 Revalidation that preceded this cycle; documentation
    changes cannot alter Python test outcomes, so those figures are
    carried forward here, not re-claimed as freshly rerun.

    **Status distinctions, precise:** Charter version 3 is *authored* and
    *corrected against every accepted Version 2 Revalidation finding*
    (this entry); it is **not** *independently revalidated* and **not**
    *owner approved*. A1–A6 remain entirely *unimplemented* — nothing in
    this entry changes that. Milestone 1 implementation is **not
    authorized** by this entry.

    Exact next action: run a new, independent Fable 5 Charter revalidation
    session against the commit introducing Charter version 3. Do not begin
    A1–A6 implementation.

59. **Milestone 1 Charter Correction Cycle 3 — documentation-only.** Dated
    2026-07-20. No application code, template, test, or migration was
    touched.

    The independent Milestone 1 Charter Version 3 Revalidation that
    followed entry 58 reread Charter version 3 (commit
    `f59237b6ba0c18e210c54f01cd79e98ea40e1709`) fresh against the actual
    repository — `apps.governance.services.request_change`/
    `approve_change_request`/`reject_change_request`/`_package_has_unresolved_holds`/
    `has_capability`, `apps.governance.models.RiskFlag.Level`,
    `apps.procurement.package_views.change_request_decide`, and the
    absence of any `apps.procurement_gates` app or `GenericForeignKey`
    usage anywhere in the codebase — and returned **MILESTONE 1 CHARTER
    VERSION 3 REQUIRES CORRECTION** with three blocking findings, two
    additional accepted findings, and one editorial defect:

    - **NF-NEW-1 (Critical, blocking):** no mandatory gate-aware
      `ChangeRequest` *decision* orchestration existed — the one real HTTP
      decision path, `change_request_decide`, calls
      `apps.governance.services.approve_change_request`/
      `reject_change_request` directly, bypassing §8.1's invalidation
      cascade entirely.
    - **NF-NEW-2 (Critical, blocking):** competing and incomplete
      package-hold mechanisms — §8.4 implied `RiskFlag`/`ChangeRequest`
      rows must be mirrored into `PackageHoldCause` to count, and §8.3
      inaccurately limited risk-driven holds to `HIGH_RISK` alone, when
      `_package_has_unresolved_holds` actually excludes only `STANDARD`
      (also holding on the existing `CONTROLLED_OPAQUE` level).
    - **NF-NEW-3 (Critical, blocking):** `apps.governance.services.has_capability`
      has no organization-scoped evaluation path at all — passing no
      `package` matches *any* active grant for that capability code with
      no organization check — so §13's named `A1`-bootstrap mechanism
      ("organization-scoped `CapabilityGrant`... via
      `CapabilityGrant.organization`") was unsupported by the actual
      accepted function.
    - **NF-NEW-4 (High, accepted):** lazy override-expiry detection was
      described as happening inside `compute_gate_state`/`GateEvaluation`,
      a function this Charter otherwise treats as a pure, side-effect-free
      read, without resolving whether that read path also acquires locks
      and performs mutation.
    - **NF-NEW-5 (High, accepted):** `GateAttempt`'s only stated deletion
      protection was an `on_delete=PROTECT` table entry, which cannot
      protect the generic-target `EvidenceBundle`/`EvidenceItem` rows that
      reference a `GateAttempt` via `content_type`/`object_id` rather than
      a real foreign key.
    - **Editorial (Low):** the `apps/governance/services.py:677-688`
      citation (and the `apps.governance.services`, "lines 706-880"
      citation in §14.1) drift under any unrelated edit above them in the
      file and were, in fact, already off by a line or two at each
      boundary versus the actual function body.

    This cycle corrected all six in Charter version 4:
    `apps.procurement_gates.services.decide_gate_aware_change` is now the
    sole Milestone 1 Change Request decision entry point, mirroring
    `request_gate_aware_change`'s shape — a locked approval transaction
    that authorizes before retrieval, invokes the unmodified
    `approve_change_request`, and conditionally runs §8.1's cascade only
    for critical changes reaching `A2`; a locked rejection transaction
    that never runs the cascade; and a binding requirement that
    `change_request_decide` be rewired to call the wrapper (§8.1, §8.2.3,
    NF-NEW-1). Package hold state is now split into existing governance
    sources (queried via a new, minimal, additive
    `apps.governance.services.has_unresolved_governance_holds`, preserving
    `_package_has_unresolved_holds`'s exact semantics and never importing
    `apps.procurement_gates`) and gate-native `PackageHoldCause` rows,
    combined by a renamed, unified `recompute_package_hold_state`
    projection; §8.3's `HIGH_RISK`-only inaccuracy is corrected, and
    §8.2.1/§8.2.2's "non-critical changes never touch hold state" claim is
    corrected to state precisely what is and is not true (§8.2.1 step 7,
    §8.2.2, §8.3, §8.4, §9.5 step 5, NF-NEW-2). `has_capability` gains an
    additive `has_capability(user, capability_code, *, package=None,
    organization=None)` extension — mutually exclusive, exact-organization-match,
    all existing callers unchanged — and the `A1`-bootstrap table row now
    names the exact required call (§13, NF-NEW-3).
    `apps.procurement_gates.services.compute_gate_state` is now stated
    explicitly to be completely side-effect-free; a new two-phase
    `apps.procurement_gates.services.reconcile_expired_overrides`
    (unlocked detection, then locked reconciliation, cascade, hold,
    audit, recompute, commit) performs the actual expiry mutation, with
    named mandatory invocation points and an eighth PostgreSQL-required
    concurrency scenario (§9.5 step 1, §11.5, §15.1, §15.2, NF-NEW-4).
    `GateAttempt` is now declared an immutable, non-deletable historical
    aggregate row — model-level `delete()` override, `pre_delete` guard,
    no admin/service deletion path, migration-only cleanup as the sole
    exception — with an explicit statement that ordinary
    `on_delete=PROTECT` does not cover this case (§3.5, NF-NEW-5). The
    brittle line-number citations were replaced with stable
    module/function-name references only (§8.2.1, §14.1, editorial).
    §21.4 of the Charter records the complete disposition table.

    Documentation reconciled in the same commit: `DT_BEACH_CURRENT_STATE.md`,
    `DT_BEACH_SOURCE_OF_TRUTH_INDEX.md`,
    `docs/architecture-decisions.md` (ADR-046, new entry, does not reopen
    ADR-044 or ADR-045), `docs/SECURITY.md`, `docs/KNOWN_LIMITATIONS.md`,
    `docs/REQUIREMENTS_TRACEABILITY.md`, and
    `docs/MARKETMATCH_ARCHITECTURE_RECONCILIATION_AND_ROADMAP.md`.

    Fresh evidence for this cycle: HEAD confirmed at
    `f59237b6ba0c18e210c54f01cd79e98ea40e1709` before editing; branch
    `integration/dt-beach-supply-control-1.0.0`; upstream
    `origin/integration/dt-beach-supply-control-1.0.0`; ahead/behind `0/0`;
    working tree clean before editing; no `apps.procurement_gates`
    directory or app present; no `GenericForeignKey` usage anywhere in the
    repository (confirmed by repository-wide grep). `manage.py check`
    passed (0 issues); `makemigrations --check --dry-run` reported no
    changes detected; `migrate --check` passed, 72/72 migrations applied,
    0 pending — unchanged, as expected for a documentation-only cycle; the
    final diff touches nine Markdown files only (`git diff --name-only`
    contains no `.py`, `.html`, or `migrations/` path) — **corrected from
    this entry's original "eight," an undercount independently found and
    verified by fresh `git diff --stat` during the Milestone 1 Charter
    Version 4 Revalidation and fixed in the version 4→5 correction cycle
    (IMPL-LOG-COUNT); the substantive claim (documentation-only, no
    `.py`/`.html`/`migrations/` path touched) was and remains accurate,
    only the file count was wrong.** The full regression
    suite was **not** rerun for this cycle, by the same design entry 58
    already established — documentation changes cannot alter Python test
    outcomes, and this cycle's own fresh evidence above independently
    confirms zero application code, template, or migration files changed.

    **Status distinctions, precise:** Charter version 4 is *authored* and
    *corrected against every accepted Version 3 Revalidation finding*
    (this entry); it is **not** *independently revalidated* and **not**
    *owner approved*. A1–A6 remain entirely *unimplemented* — nothing in
    this entry changes that. Milestone 1 implementation is **not
    authorized** by this entry.

    Exact next action: run a new, independent Fable 5 Charter revalidation
    session against the commit introducing Charter version 4. Do not begin
    A1–A6 implementation.

60. **Milestone 1 Charter Correction Cycle 4 — documentation-only.** Dated
    2026-07-20. No application code, template, test, or migration was
    touched.

    The independent Milestone 1 Charter Version 4 Revalidation that
    followed entry 59 reread Charter version 4 (commit
    `cf01d400e1dffd6c5981ee2ae8a01ad71f3c9006`) fresh against the actual
    repository — `apps.governance.services.raise_risk_flag`/
    `resolve_risk_flag`/`has_capability`/`CapabilityGrant`,
    `apps.governance.admin`'s blanket `ModelAdmin` auto-registration loop,
    `apps.procurement.package_views.change_request_create`/
    `change_request_decide`'s actual lock order versus
    `approve_change_request`/`reject_change_request`'s own internal order,
    and the full `git log --all` history for `NF-5`/`NF-6` — and returned
    **MILESTONE 1 CHARTER VERSION 4 REQUIRES CORRECTION** with two
    blocking findings and eight additional accepted findings:

    - **RISKFLAG-HOLD-1 (Critical, blocking):** §8.4 claimed the
      unmodified `raise_risk_flag`/`resolve_risk_flag` were "reachable
      identically whether or not a package participates in A1–A6" —
      verified false: `resolve_risk_flag` writes `is_on_hold` from the
      governance-side rule alone, silently clearing a gate-native
      `PackageHoldCause`-backed hold (e.g. an open critical-change hold
      awaiting refreeze) when an unrelated `RiskFlag` is resolved, because
      no gate-aware wrapper existed for `RiskFlag` the way one already did
      for `ChangeRequest`.
    - **NF4-A (High, blocking):** no binding admin-edit-immutability
      policy existed for procurement-gates historical models
      (`GateDecision`, `GateEvaluation`, `PackageFreezeRevision`,
      `ProcurementGateOverride`'s decided fields, etc.) beyond the two
      narrow cases (`GatePolicyVersion` post-publication, `GateAttempt`
      deletion) version 4 already covered — `apps/governance/admin.py`'s
      blanket, writable, default-`ModelAdmin` auto-registration loop was
      never addressed for the rest.
    - **DOC-COUNT-1 (Medium, accepted):** §20 completion criteria item 5
      said "§15.1's seven named scenarios" while §15.1/§15.2/§21.1 already
      said "eight," an uncorrected leftover from before version 4's own
      NF-NEW-4 correction brought the list to eight.
    - **LOCK-ORDER-1 (Medium, accepted):** `decide_gate_aware_change`
      locked `ProcurementPackage` before `ChangeRequest`, the reverse of
      the unmodified foundation functions' own internal order, creating a
      latent, unacknowledged opposite-order deadlock risk.
    - **NF4-C (Medium, accepted):** the organization-scoped `has_capability`
      binding rule was stated in prose without the exact query shape,
      leaving unstated whether a hybrid `package`-plus-`organization`
      grant would incorrectly qualify.
    - **NF-V4-2 (Medium, accepted):** `decide_gate_aware_change`'s
      decision-time authorization was not explicitly bound to the exact
      existing `CHANGE_REQUEST_APPROVAL_CAPABILITY[field_name]` mapping,
      risking a second, independently-drifting capability table.
    - **README-STALE (Medium, accepted):** `README.md` still narrated
      "Charter version 3" as current, one full correction cycle behind the
      other nine canonical governing documents.
    - **IMPL-LOG-COUNT (Low, accepted):** this log's own entry 59 stated
      "eight Markdown files" for the version 4 diff; the actual diff
      touches nine.
    - **NF-V4-5 (Low, accepted):** §8.2.1 named `change_request_decide` for
      required rewiring but never named `change_request_create`, its own
      current direct caller of `request_change`.
    - **TRACE-1 (Low, accepted):** no governing document disclaimed that
      `NF` identifiers are historical, non-contiguous labels.

    This cycle corrected all ten in Charter version 5:
    `apps.procurement_gates.services.raise_gate_aware_risk_flag`/
    `resolve_gate_aware_risk_flag` are now the sole Milestone 1 RiskFlag
    entry points, mirroring `request_gate_aware_change`/
    `decide_gate_aware_change`'s shape exactly, with a corrective
    `recompute_package_hold_state` write before commit and a mandatory
    bypass-prevention architectural test (§8.3, §8.3.1, §8.4, RISKFLAG-HOLD-1).
    A new §16.3 binding admin-immutability policy requires every
    procurement-gates historical model to be either excluded from Django
    admin entirely or exposed only through a dedicated read-only
    `ModelAdmin`, prohibits blanket writable auto-registration for those
    models by name, and distinguishes `ProcurementGateOverride`'s
    immutable fields from its service-routed lifecycle transitions (§16.3,
    NF4-A). `decide_gate_aware_change` now locks `ChangeRequest` before
    `ProcurementPackage`, matching the foundation's own order; a new
    §14.1a global lock-order table classifies every Charter-defined
    mutation into one of two consistent patterns, and the RiskFlag
    wrapper adopts the identical `RiskFlag`-then-`ProcurementPackage`
    order for the same reason (§8.2.3, §14.1a, LOCK-ORDER-1). §13 now
    states the exact organization-scoped `CapabilityGrant` query,
    excluding hybrid `package`- or `role_assignment`-plus-`organization`
    grants (§13, NF4-C), and explicitly binds `decide_gate_aware_change`'s
    authorization to the existing `CHANGE_REQUEST_APPROVAL_CAPABILITY`
    mapping with no second table (§13, NF-V4-2). §15's PostgreSQL-required
    scenario count is corrected to **nine** (eight pre-existing plus the
    new lock-order/deadlock-regression scenario), reconciled across §15,
    §20, and `docs/SECURITY.md` (§15.1, §15.2, §20 item 5, NF-V4-2/DOC-COUNT-1
    — the count was set to its true current value, not merely the "eight"
    the finding literally named, since LOCK-ORDER-1's own correction adds
    a ninth scenario in this same cycle). §8.2.1 now names
    `change_request_create` for required rewiring, symmetric with
    `change_request_decide` (§8.2.1, NF-V4-5). §21 now opens with the
    exact historical NF-numbering disclaimer (§21, TRACE-1). §21.5 records
    the complete disposition table for all ten findings. Two additional
    internal contradictions were independently found and corrected during
    this cycle while working the assigned findings, though neither was
    itself one of the ten accepted findings: §18 test 25d contradicted
    §8.2.1's own binding rule (it claimed *creating* a non-critical
    `ChangeRequest` left a package off hold, when §8.2.1 requires it stay
    on hold while `PENDING`; corrected, with the actual clearing behavior
    moved to new test 25t), and §9.6 claimed the cache-write for
    `PackageGateState` was performed by `compute_gate_state` itself,
    contradicting §9.1/§11.5's purity rule for that function (corrected —
    the write is now explicitly attributed to each mutating transaction
    that calls `compute_gate_state`, never to `compute_gate_state` itself).

    Documentation reconciled in the same commit: `README.md` (version 3→5
    narrative brought current, README-STALE), this entry's own
    correction (IMPL-LOG-COUNT), `DT_BEACH_CURRENT_STATE.md`,
    `DT_BEACH_SOURCE_OF_TRUTH_INDEX.md`, `docs/architecture-decisions.md`
    (new ADR entry), `docs/SECURITY.md` (DOC-COUNT-1's "seven"→"nine"
    correction plus the admin-immutability/RiskFlag-orchestration/lock-order
    additions), `docs/KNOWN_LIMITATIONS.md`,
    `docs/REQUIREMENTS_TRACEABILITY.md`, and
    `docs/MARKETMATCH_ARCHITECTURE_RECONCILIATION_AND_ROADMAP.md`.

    Fresh evidence for this cycle: HEAD confirmed at
    `cf01d400e1dffd6c5981ee2ae8a01ad71f3c9006` before editing; branch
    `integration/dt-beach-supply-control-1.0.0`; upstream
    `origin/integration/dt-beach-supply-control-1.0.0`; ahead/behind `0/0`;
    working tree clean before editing; no `apps.procurement_gates`
    directory or app present; `apps/governance/admin.py` confirmed to use
    a blanket, writable, default-`ModelAdmin` auto-registration loop for
    every model in the `governance` app (the exact pattern §16.3 now
    prohibits for procurement-gates historical models). `manage.py check`
    passed (0 issues); `makemigrations --check --dry-run` reported no
    changes detected; `migrate --check` passed, 72/72 migrations applied,
    0 pending — unchanged, as expected for a documentation-only cycle. The
    full regression suite was **not** rerun for this cycle, by the same
    design entry 58 already established — documentation changes cannot
    alter Python test outcomes, and this cycle's own fresh evidence above
    independently confirms zero application code, template, or migration
    files changed.

    **Status distinctions, precise:** Charter version 5 is *authored* and
    *corrected against every accepted Version 4 Revalidation finding*
    (this entry); it is **not** *independently revalidated* and **not**
    *owner approved*. A1–A6 remain entirely *unimplemented* — nothing in
    this entry changes that. Milestone 1 implementation is **not
    authorized** by this entry.

    Exact next action: run a new, independent Fable 5 Charter revalidation
    session against the commit introducing Charter version 5. Do not begin
    A1–A6 implementation.
