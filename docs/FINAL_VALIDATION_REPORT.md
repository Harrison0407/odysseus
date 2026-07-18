# Final Validation Report

All items below were actually executed in this session; none are
descriptions of intended behavior. Commands and outputs are summarized;
full detail is in `docs/implementation-log.md`.

## 1. Migrations from an empty database

```
python manage.py makemigrations   # 25 migration files across 18 apps, 176 models total
python manage.py migrate          # Applied all migrations: OK, from an empty database
```
Result: **pass**, first attempt after one fix (unserializable `lambda`
default, corrected before migrations were generated).

## 2. Automated tests

```
python -m pytest -q
21 passed in ~25s
```
Coverage: live-container fixture (9 tests), document upload/duplicate/
authorization (4 tests), receiving/inventory ledger (4 tests),
permissions/object-level authorization (3 tests), plus supporting
fixtures. See `docs/REQUIREMENTS_TRACEABILITY.md` for what each test
actually proves.

## 3. Static checks

`python manage.py check` → "System check identified no issues (0 silenced)"
— run repeatedly throughout development, always clean before proceeding.

## 4. Document inspection

All 21 files in `Seed_docs/Live_container/` and both files in
`Business_context/` were opened and read (not inferred from filenames).
See `docs/SOURCE_PACKAGE_INDEX.md` for the full inventory and
`docs/SOURCE_DOCUMENT_ANALYSIS.md` for the extracted facts.

## 5. Matching fixtures

`import_live_container_fixture` produces 11 internal manifest lines and
11 `ManifestVariance` rows against the single official BL line, with 3
correctly flagged for Customs & Logistics review. Verified against both
the automated test suite and a live `curl`-driven walkthrough of the
actual shipment-detail page (see step 7).

## 6. Core workflows exercised through the actual UI

Using a running dev server and authenticated `curl` sessions (cookies
preserved across requests, real login POST with CSRF token):
- Login as a seeded pilot user (Markeris) → 200, correctly routed to the
  "Compras" persona dashboard.
- Every main nav route (`/`, `/documentos/`, `/compras/`, `/embarques/`,
  `/recepcion/`, `/almacen/ubicaciones/`, `/solicitudes/`, `/costos/`,
  plus the upload/create forms) → 200 for an authenticated user.
- Shipment detail page for the imported fixture → 200, verified to
  contain the exact official cargo description text, 3 "Revisión
  aduanal" badges, the critical-discrepancy banner, Sole-26 building
  references, and the 520-package official total.
- Purchase-order list/detail → all 5 fixture POs render, all 5 show the
  "sello no verificado" (unverified stamp) warning, and DT-BEACH804
  correctly shows an open balance of 30 (40 original − 10 allocated).

## 7. Desktop and mobile widths

Bootstrap-based responsive layout (`container-fluid`, responsive
columns, `min-height: 44px` touch targets on buttons under 576px).
**Update (later session):** every `<table>` across the entire
application (19 instances across 9 templates, all outside the
Delivery/Installation/Inspection/Acceptance screens, plus 1 inside
`installation_detail.html` itself) was audited and, where missing,
wrapped in a horizontally-scrolling container (`table-responsive` for
Bootstrap-based pages, a `.table-scroll` CSS rule for the one
self-contained non-Bootstrap page, `reports/snapshot_shipment.html`,
which also gained a viewport meta tag it previously lacked) so no table
forces the page itself to scroll horizontally on a narrow screen. Every
edited file was verified to have balanced `<div>`/`</div>` and
`<table>`/`</table>` tag counts, and the full test suite (103 tests,
several of which exercise the touched templates via real HTTP requests
through the Django test client) passed after the change. **No headless
browser or screenshot tool was available in this environment**, so this
remains a **structural, not a pixel-level visual**, verification — the
same honest distinction drawn here in the original Priority 0 pass.

## 8. Every role

Verified structurally (persona-branching logic in
`apps.core.views.dashboard_home`, one dashboard template per role) and
functionally for the `compras` role via the live curl walkthrough above.
The other 5 persona dashboards were verified by direct template/view
code review and the Django `check`/test suite, not by an individual
per-role live login walkthrough in this session — recorded as a **partial**
validation for the same honesty reason as item 7.

## 9. Object-level permissions

`test_purchase_order_list_is_scoped_to_users_organization`,
`test_purchase_order_detail_denies_cross_organization_access`,
`test_unauthorized_user_cannot_download_another_orgs_document` — all
pass. Also verified live: unauthenticated request to a protected page
returns a redirect to login, not the page content.

## 10. Document immutability and authorized downloads

`test_upload_creates_document_with_sha256`,
`test_duplicate_upload_is_detected_by_sha256`,
`test_rejects_disallowed_file_extension` — all pass. Authorized/
unauthorized download distinction verified both by test and live in the
production Docker stack (step 15 below).

## 11. Handoff blocking

**Superseded by the Gate Controls and Formal Handoffs milestone — now
fully implemented, tested, and live-verified.** See the dedicated
section at the end of this report for the complete account: gate
evaluation, blocking, authorized override, submission, acceptance,
rejection, return-for-correction, and corrected resubmission are all
implemented, covered by 32 automated tests, and driven end-to-end
through real HTTP requests against a running server using the actual
seeded users and the imported live-container fixture.

## 12. Release packet versioning

Modeled (`ReleasePacketVersion`, unique per `(release_packet,
version_number)`); exercised implicitly by the fixture's
`ShipmentManifestVersion`, not independently exercised with a second
version in this session. **Modeled, not fully exercised.**

## 13. Receiving and quarantine

`test_posting_receipt_line_creates_inventory_movement`,
`test_damaged_quantity_is_quarantined_not_added_to_available_stock`,
`test_damage_exception_creates_critical_discrepancy` — all pass.

## 14. Inventory transaction integrity

`test_onhand_quantity_is_derived_from_ledger_never_edited_directly` —
passes; also structurally guaranteed by the absence of any
directly-editable quantity field on `InventoryLot`.

## 15. Duplicate submission protection

`InventoryMovement.idempotency_key` (unique) is modeled; not
independently tested with a simulated double-submit in this session —
**modeled, not fully exercised**.

## 16. Landed-cost reconciliation and rounding

Modeled (`LandedCostVersion`/`LandedCostLine`); no allocation-run test
was written in this session since the allocation-run UI itself is not
yet built (`docs/KNOWN_LIMITATIONS.md` item 5) — **not yet exercised**.

## 17. CONFOTUR duplicate prevention

`ConfoturLine.is_duplicate_of` is modeled; not exercised by a test in
this session (`apps.customs` has no fixture data) — **modeled, not yet
exercised**.

## 18. HTML export

Exercised live in the production Docker stack: authenticated request to
`/reportes/embarque/<id>/instantanea/` returned 200 with a self-contained
HTML document (verified the "Esta es una instantánea" banner text is
present, confirming no external asset dependency and correct
snapshot-vs-live-source labeling).

## 19. Production Docker Compose

Built and ran the **actual** stack (Postgres 16 + Gunicorn 23 + Caddy 2)
via Docker Desktop, started for this purpose. Three real bugs were found
and fixed live during this process (see `docs/implementation-log.md`
step 7 for full detail):
- Whitenoise `collectstatic` failure on a dangling vendored-asset source map.
- `docker-compose.prod.yml` silently dropping unlisted environment variables.
- **A local dev `.env` baked into the image, silently running the
  "production" container against SQLite instead of Postgres** — caught by
  directly querying Postgres via `psql` and finding zero tables despite
  Django reporting real data through the (actually SQLite) connection.
After each fix, the full sequence was re-validated from a clean rebuild.

## 20. Persistence after restart

Verified genuinely against Postgres (after the SQLite bug above was
fixed): seeded 9 users + imported the 1-shipment fixture, then ran a full
`docker compose down` (containers + network removed, **not** just a
process restart) followed by `docker compose up -d` (fresh containers,
same named volumes) — user count and shipment count were identical
before and after, and the app remained reachable through Caddy (200 on
`/accounts/login/`).

## 21. Backup and restore

Verified genuinely against Postgres: `backup.sh` produced a 74 KB dump
containing 183 `CREATE TABLE` / 183 `COPY` statements (confirmed by
direct inspection of the decompressed file, not just a successful exit
code). Created a throwaway shipment after the backup (count 1→2), ran
`restore.sh` with typed confirmation, and confirmed the throwaway record
was gone and the original fixture data intact (count back to 1, correct
reference name), with the app healthy immediately after.

## 22. Gate Controls and Formal Handoffs milestone (validated in a later session)

Baseline verified before starting: branch `main`, HEAD =
`3aa6127b22efda46a4bb6532f319e6a83ae72043`, clean working tree.

- **Migrations from empty:** two new migrations (`accounts.0003`,
  `workflow.0002`) applied cleanly; `makemigrations --check` confirms no
  drift.
- **Automated tests:** 32 new tests (16 gate-evaluator, 16 handoff-
  lifecycle/security) plus the pre-existing 21 — **53/53 passing**.
- **Live HTTP walkthrough** (not just unit tests) against a running dev
  server, using the actual seeded users and the real imported
  MEDUWY575021 fixture:
  1. Ran `import_live_container_fixture`; it created a real
     `logistics_to_receiving` handoff and reported it **genuinely
     blocked** (4 discrepancies, 3 requiring customs review, 1 missing
     requirement) purely from the fixture's own data.
  2. Logged in as Harrison via real POST + CSRF token; loaded the
     handoff detail page and confirmed it rendered the exact same
     blockers.
  3. POSTed a plain submit; confirmed it was rejected with a clear
     message and the handoff stayed `not_ready` (never silently
     advanced).
  4. POSTed an authorized override with a written reason; confirmed the
     handoff moved to `submitted` and a `GateOverride` row was created
     recording the reason, actor, and timestamp.
  5. Logged in as Manuel; confirmed the handoff appeared in his "para
     mí" inbox (role-based visibility, not a hard-coded name check);
     accepted it via POST.
  6. Confirmed, directly against the database: exactly one
     `HandoffDecision(decision="accepted")`, `Shipment.status` advanced
     to `released_to_receiving`, and a new open `ResponsibilityAssignment`
     pointed at Manuel/Almacén.
- **Security scenarios verified by test:** unauthorized override denied
  (`test_unauthorized_override_is_denied`), unauthorized acceptance
  denied (`test_unauthorized_user_cannot_accept_handoff`), cross-role
  access denied when a gate specifies a required role
  (`test_required_role_to_accept_enforced`), cross-project isolation
  denied both at the service layer and via a direct URL hit returning a
  302 redirect rather than the record
  (`test_cross_project_isolation_denies_view_and_accept`), duplicate
  submission is idempotent at both the app and DB-constraint level
  (`test_creating_handoff_twice_is_idempotent`,
  `test_submitting_twice_second_call_raises_instead_of_double_processing`),
  and concurrent acceptance is race-safe via `select_for_update`
  (`test_concurrent_acceptance_only_the_first_wins`).
- **Immutable audit history:** every create/submit/accept call produces
  an `AuditEvent`; `HandoffDecision` and `GateOverride` rows are never
  updated or deleted by application code; a corrected resubmission
  creates a new `Handoff` row and marks the old one `SUPERSEDED` rather
  than editing it (verified by
  `test_return_for_correction_and_resubmission_creates_new_superseding_version`).
- **Update (later session):** the 3 gates anchored on `Delivery`/
  `InstallationRecord` (`project_delivery_to_installation`,
  `installation_to_inspection`, `inspection_to_acceptance`), noted below
  as engine-only at the time this section was written, now have full
  production UI and were exercised live via real HTTP requests — see
  section 23 ("Delivery, Installation, Inspection, and Final Acceptance
  milestone") further down this document.

## 23. Delivery, Installation, Inspection, and Final Acceptance milestone

Baseline verified before starting: branch `main`, HEAD =
`86c31016d057e0338e542577aa08fd3d6a7c7cb1`, clean working tree.

- **Migrations from empty:** one new migration
  (`requests.0002_alter_delivery_options_and_more`) applied cleanly;
  `makemigrations --check` confirms no drift.
- **Automated tests:** 40 new tests
  (`tests/test_delivery_installation_acceptance.py`) covering all 27
  required scenarios, plus the pre-existing 53 — **93/93 passing**.
- **Live HTTP walkthrough** (not just unit tests) against a running dev
  server, real cookies + CSRF tokens, seeded pilot users and a dedicated
  demo dataset (`seed_delivery_demo_data`):
  1. Miguel (Obra): approved → reserved → dispatched → recorded the
     delivery line fully accepted → completed the delivery → created the
     project receipt → created the installation record → recorded full
     installation progress → acknowledged as installer → confirmed as
     supervisor.
  2. Created and submitted the `installation_to_inspection` handoff;
     accepted it (Miguel, Obra department on both sides of this gate).
  3. Recorded a **failed** inspection with 2 blocking punch-list
     defects. Created the `inspection_to_acceptance` handoff and
     confirmed it genuinely blocked — the detail page showed both "la
     inspección más reciente no fue aprobada" and "existen defectos
     críticos abiertos" purely from the data, not a canned message.
  4. Confirmed a plain submit was rejected and the handoff stayed
     `not_ready`. Confirmed a direct-POST **unauthorized override**
     attempt (Miguel, whose Obra role lacks `can_override_gates`) was
     denied server-side with the exact permission-denied message — the
     override form itself is also hidden from him in the UI, but this
     was proven by bypassing the UI entirely via a direct POST to
     `/flujo/<id>/anular-enviar/`.
  5. Closed both punch-list defects; recorded a passing reinspection;
     re-submitted the handoff — now genuinely ready and accepted.
  6. Harrison (Dirección) accepted the handoff via the generic, reused
     accept endpoint, then recorded the final-acceptance detail
     ("Aceptado") through the domain-specific screen. A duplicate
     final-accept submission was caught gracefully — confirmed via
     direct database query that exactly one `AcceptanceRecord` exists.
  7. Markeris (Compras, no project access to the demo project) was
     denied both a direct URL hit on the installation detail page (302
     redirect) and any trace of the record in his own installation list
     view (queryset-level scoping, not just click-through denial).
- **Inventory/quantity safeguards confirmed live and by test:** delivered
  quantities never exceed dispatched; installed quantities never exceed
  validly delivered quantities except through an audited, permission-
  gated override (`AuditEvent.Action.WAIVER`); every inventory
  consequence (dispatch, damage quarantine, installation consumption) is
  a real posted `InventoryMovement`; partial and multi-trip delivery
  correctly recompute rather than increment.
- **Two real bugs found and fixed live, during this milestone's own
  walkthrough** (not by unit tests, which called the service layer
  directly and didn't exercise the two-URL interaction that exposed
  either issue): duplicate installation creation from a repeated
  submission (fixed with an idempotent creation guard, ADR-018), and the
  final-acceptance detail becoming permanently unreachable if the
  generic accept button was used before the domain-specific screen
  (fixed per ADR-021). Both were re-verified with a fresh walkthrough
  afterward. See `docs/KNOWN_LIMITATIONS.md` and
  `docs/implementation-log.md` for the full account.
- **What's honestly not covered yet:** multi-lot split dispatch has no
  dedicated UI (service layer supports it); evidence/photo upload is not
  wired into these new screens; installer/inspector assignment dropdowns
  list every user, not just Obra department members (a data-entry
  convenience gap, not a security one — authorization is still fully
  enforced server-side regardless of who is picked); mobile rendering is
  structurally but not visually/screenshot verified. See
  `docs/KNOWN_LIMITATIONS.md` for the complete, itemized list.

## Overall recommendation

**CONDITIONAL GO** — see `docs/KNOWN_LIMITATIONS.md` for the exact list of
what is modeled-but-not-yet-exercised and what has no UI yet at all. The
Priority 0 vertical slice (documents, procurement, the dual-manifest
engine, receiving, inventory ledger, material requests, landed-cost data
model, dashboards, HTML snapshots, and the full production deployment/
backup/restore cycle), the Gate Controls and Formal Handoffs milestone
(gate evaluation, blocking, authorized override, and the full handoff
lifecycle for the 8 required transitions), and the Delivery, Installation,
Inspection, and Final Acceptance milestone (the last 3 of those 8
transitions now have production UI, a quantity-invariant-safe domain
service layer, and cross-project-isolated screens) are genuinely working
end-to-end against real data, not merely designed. This is not yet the
complete 38-section system the governing prompt describes, and should not
be represented as such.
