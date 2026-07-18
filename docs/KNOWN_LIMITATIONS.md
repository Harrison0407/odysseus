# Known Limitations

Honest, current list. This is a staged delivery of an intentionally very
large specification (`ASSUMPTIONS.md` A1) — this file exists so nothing
is silently claimed to be done when it isn't.

## Not yet built (UI)

1. ~~Gate-blocking UI~~ — **Done in the Gate Controls milestone.** Every
   one of the 8 required transitions now has a real evaluator
   (`apps.workflow.gates`) and a handoff detail screen (`/flujo/<id>/`)
   that shows exactly why a gate is blocked, with an authorized-override
   path requiring a written reason. One residual gap: the "blocked"
   message currently lives on the handoff detail page itself, not
   additionally repeated inline on the Shipment/Material Request detail
   page at the point a transition is attempted outside the handoff flow.
2. ~~Handoff inbox~~ — **Done.** `/flujo/` is a full role-aware,
   filterable inbox (para mí / enviadas por mí / devueltas / bloqueadas /
   completadas / todas), with accept/reject/return/resubmit/comment all
   wired to real permission-checked actions.
3. ~~Detailed internal receiving manifest, exact spec 13A.8 layout~~ —
   **Done, with an honest caveat.** `apps.reports.views.receiving_manifest_snapshot`
   (`/reportes/recepcion/<receipt_id>/manifiesto/`, linked from the
   receiving detail page) generates a self-contained, downloadable HTML
   document with 10 numbered sections: container/shipment header,
   official BL summary, full internal operational manifest, physical
   receiving summary, per-line receiving detail (expected/received/
   damaged/missing/exception), inspections, quarantine/damage, open
   discrepancies, the receiving plan, and the official-vs-operational
   variance matrix requiring attention. **The exact section
   numbering/wording of the original spec 13A.8 text could not be
   re-read verbatim in this session** (the governing specification
   document itself is not stored in `Application/`, only derived docs
   are) — section 10 (the variance matrix) is independently confirmed
   correct against `OFFICIAL_VS_OPERATIONAL_MANIFEST_ANALYSIS.md`, which
   cites it explicitly ("spec 13A.8, item 10"); the other 9 sections
   were reconstructed from `BUSINESS_REQUIREMENTS.md` (Manuel's
   interview themes) and the existing receiving data model, covering the
   same operational content, not necessarily in the exact original
   order or wording. See `ASSUMPTIONS.md` A16. A human reviewer with the
   original spec text should confirm and reorder if needed. Verified
   live against the actual imported MEDUWY575021 fixture (11 internal
   manifest lines, 11 variances, all rendered correctly) and by 4
   automated tests.
4. ~~Dispatch/Delivery UI~~ — **Done in the Delivery, Installation,
   Inspection, and Final Acceptance milestone.** `/solicitudes/entregas/`,
   `/solicitudes/instalaciones/`, `/solicitudes/inspecciones/`, and
   `/solicitudes/aceptaciones/` cover approve → reserve → dispatch →
   deliver (partial/multi-trip/damage/refusal) → project receipt →
   install (with quantity-guard and authorized override) → inspect
   (pass/conditional/fail, punch-list) → correct → reinspect → final
   accept, all through the same reused `apps.workflow.gates`/
   `apps.workflow.services` engine — see the dedicated section in
   `REQUIREMENTS_TRACEABILITY.md`.
5. ~~Landed-cost allocation-run trigger UI~~ — **Done, and more than a
   trigger.** The calculation engine itself did not exist anywhere in the
   codebase before this session (only the `CostAllocationRun`/
   `CostAllocationLine`/`LandedCostVersion`/`LandedCostLine` data model
   did) — `apps.cost.services` now implements it:
   `run_allocation` splits a `CostCharge` across a shipment's internal
   manifest lines by quantity/product-value/gross-weight/net-weight/
   CBM/package/container, or manual percentage/amount (the last line
   always absorbs any rounding remainder, so allocated amounts sum
   exactly to the original charge); `calculate_landed_cost` aggregates
   every allocation into per-unit freight/local/other cost buckets plus
   each line's own unit price (traced through
   `ManifestLineSource → PurchaseOrderLine`, converted to the
   organization's base currency via `ExchangeRate` when one is on file
   — never silently assumed 1:1), creating a new, immutable
   `LandedCostVersion` every time; `finalize_landed_cost` marks a
   version final, once. `/costos/embarque/<id>/` (linked from the
   shipment detail page) exposes "ejecutar asignación" and "calcular
   nueva versión" buttons; `/costos/<id>/finalizar/` finalizes. 14 new
   tests, and verified live against the real imported MEDUWY575021
   fixture's actual USD 6,900 ocean-freight charge, allocated by CBM
   across its 11 real manifest lines, calculated, and finalized.
   **Known simplification** (see `ASSUMPTIONS.md` A18): the UI currently
   only exposes the automatic (basis-driven) allocation methods, not a
   per-line entry form for the manual-percentage/manual-amount methods
   — those are fully implemented and tested at the service layer.
   Uploading `CostDocument`/`CostCharge` records themselves still has no
   dedicated UI (out of scope for this gap, which was specifically
   about *running* an allocation, not creating the charges to allocate).
6. **Priority 1 UI, in progress:**
   - ~~CONFOTUR reconciliation screens~~ — **Done.**
     `apps.customs.services.detect_duplicate_candidates`/
     `confirm_duplicate` (the duplicate-exemption detection/confirmation
     logic did not exist anywhere before this session, only
     `ConfoturLine.is_duplicate_of` did) + `/aduanas/confotur/` list/
     detail/reconciliation screens. Groups still-unresolved
     `ConfoturLine` rows by shared `quotation` or `manifest_line`;
     never auto-resolves a candidate, only a human confirmation does
     (matches the project-wide "a discrepancy stays visible until
     genuinely resolved" rule). 10 new tests.
   - ~~Tool custody screens~~ — **Done.** `apps.tools.services`
     (`checkout_tool`/`return_tool`/`record_repair`) enforces the one
     invariant the data model implied but no code ever checked: a tool
     cannot be checked out twice at once. `/herramientas/` list/detail
     screens, linked from the main nav and from the Almacén dashboard's
     "herramientas vencidas" card. 11 new tests. **A real,
     previously-undetected bug was found and fixed while building
     this:** the Almacén persona dashboard
     (`apps.core.views.dashboard_home`) crashed with a `FieldError` for
     *any* almacen/recepcion-role user, ever since it was written in
     the Priority 0 pass — it filtered `ToolCheckout` by
     `actual_return_date`, a field that only exists on the related
     `ToolReturn` model, not `ToolCheckout` itself. No test had ever
     exercised an authenticated almacen-role dashboard render before
     now. Fixed and covered by a regression test
     (`tests/test_tool_custody.py::TestAlmacenDashboardRegression`).
   - ~~Cycle-count screens~~ — **Done.** `apps.inventory.services`
     (`start_cycle_count`/`record_physical_count`/`approve_adjustment`)
     — a cycle count is seeded from the ledger-derived on-hand quantity
     per lot (never a stored figure), supports "blind" counting (the
     system quantity is hidden from the counter in the UI until a
     physical count is entered), tracks recounts without overwriting
     the first count's variance history, and posts any approved
     variance as a real `InventoryMovement`
     (`MovementType.ADJUSTMENT`) — never a silent stock edit.
     `/almacen/conteos/` list/create/detail screens. 12 new tests.
   - ~~Storage capacity/suitability warnings~~ — **Done.**
     `apps.inventory.services.check_location_suitability`/
     `enforce_location_suitability` reuse the previously-modeled but
     completely unused `LocationSuitability`/`LocationCapacity`
     (`WarehouseLocation` OneToOne) and `ProductRiskProfile`
     (`ProductCategory` OneToOne) — no new inventory-truth models were
     added. A locally-configured, explicit restriction
     (`WarehouseLocation.allowed_categories` violated, or a hard
     volume/weight ceiling exceeded) is **blocking**; a sensitive
     material (`ProductRiskProfile.risk_level == HIGH`) placed
     somewhere lacking covered/dry/secure conditions, or with
     flood/leak risk, is a **warning only** — matches the real-world
     failure mode in `BUSINESS_REQUIREMENTS.md` (exposure was
     historically a visibility problem, not something to make
     physically impossible). The *absence* of a
     `LocationSuitability`/`LocationCapacity` row is never itself
     treated as a blocking condition — only as "cannot confirm," at
     most a warning for high-risk items. Overrides reuse the exact
     same `apps.workflow.services.can_override_gates` +
     `AuditEvent.Action.WAIVER` pattern as the Milestone 3 installation
     quantity-guard override — no parallel authorization concept.
     Wired into the two workflows that actually create/move inventory:
     `apps.receiving.services.post_receipt_line` (put-away — this also
     fixed a real pre-existing bug: `receipt_line_update` was calling
     `WarehouseLocation.objects.first()` instead of ever letting the
     user choose a receiving location) and the newly-activated
     `apps.requests.services.transfer_lot`, which wires up the
     `Transfer` model that had no calling code anywhere before this.
     `Item` has no per-unit volume/weight field (only a free-text
     `dimensions` string) — current/projected utilization is derived
     from the most recently linked `ManifestLine.cbm`/
     `gross_weight_kg` ÷ quantity, and is reported as "Desconocida (sin
     datos de referencia)" rather than a fabricated zero when no
     manifest line exists to derive it from (see new ASSUMPTIONS.md
     entry). `/almacen/ubicaciones/<id>/` shows capacity, utilization,
     suitability conditions, a live suitability checker, assigned
     inventory (ledger-derived), pending inbound quantities (from
     `ReceivingPlanLine`), the site custodian, and a transfer form; a
     lot split across more than one origin location is refused with an
     explicit error rather than silently guessing a source. 20 new
     tests (`tests/test_storage_suitability.py`).
   - ~~External-storage comparison calculator~~ — **Done.**
     `AlternativeStorageOption` (modeled since Priority 0, never
     previously used by any UI) is now grouped under a new
     `StorageComparisonScenario` (version_number + is_current,
     mirroring `ReleasePacketVersion`/`LandedCostVersion`'s versioning
     pattern) instead of pointing directly at `ReceivingPlan` — this
     was a schema change to the previously-unused model, safe since it
     had zero rows/callers anywhere. `apps.receiving.services
     .compare_scenario_options` never fabricates a currency
     conversion: it reuses `apps.cost.services.convert_to_base_currency`
     (the exact function landed-cost calculation already uses,
     renamed from private to public for cross-app reuse) and returns
     `converted_total=None` with an explanatory note when no
     `ExchangeRate` is on file for that currency pair, rather than
     assuming 1:1 or guessing a rate. Demurrage/penalty exposure is
     deliberately excluded from the guaranteed comparable total (it's
     contingent risk, not a certain cost) and shown alongside it
     instead (A24). An `internal_baseline` option can link to a real
     `WarehouseLocation` and reuses its already-registered
     `LocationSuitability` rather than duplicating capacity/suitability
     data entry for our own warehouse. A scenario is immutable once
     `FINALIZED` — `add_storage_option`/`update_storage_option` both
     refuse further edits; a new comparison always creates a new
     version rather than editing a decided one.
     `/recepcion/planes/<id>/` (receiving-plan overview, linked from
     the Shipment detail page) and
     `/recepcion/comparaciones/<id>/` (comparison table, ranked
     results, add-option/finalize forms) are the new screens; a
     printable/downloadable HTML export reuses the exact same
     `_save_html_snapshot`/`ReportVersion` mechanism as the receiving
     manifest snapshot, not a new export path. 21 new tests
     (`tests/test_storage_comparison.py`).
   - ~~Supplier claim package generation~~ — **Done.** Unlike
     storage capacity/external-storage (which activated dormant
     models), `SupplierClaim` did not exist at all before this entry —
     confirmed via a repo-wide search for any `Claim` model. New
     `apps.claims` app, full lifecycle
     (`DRAFT -> APPROVED -> SUBMITTED -> SUPPLIER_RESPONDED -> RESOLVED -> CLOSED`,
     ADR-031), traceable via optional FKs to `Supplier`/
     `PurchaseOrder`/`PurchaseOrderLine`/`Item`/`Shipment`/`Container`/
     `ManifestVariance`/`Receipt`/`ReceiptLine`/`Discrepancy`/
     `QuarantineRecord`/`Inspection`/`ReplacementCase` — every field a
     genuine link to an existing record, never a re-entered copy. A
     claim referencing the Official-vs-Operational `ManifestVariance`
     only ever points at that already-immutable record; nothing here
     can overwrite either the Official Carrier Summary or the Internal
     Operational Manifest. Evidence reuses
     `apps.audit.services.attach_evidence`/`Attachment` (ADR-022, no
     new `ClaimEvidence` model); the printable/downloadable claim
     package reuses `apps.reports._save_html_snapshot`/`ReportVersion`
     (extended with an optional `content_object` link so a
     `ReportVersion` can point back at the specific claim it
     documents, rather than adding a dedicated per-claim
     package-version model) — the `CLAIM_PACKAGE` report type already
     existed in `ReportVersion.ReportType`, unused, anticipating
     exactly this feature. Approval requires at least one evidence
     attachment (a conservative operational rule, not a legal one —
     A27); each lifecycle transition is guarded so it can never run out
     of order or repeat (submitting twice is refused, not silently
     re-posted). `/reclamos/` list/create/detail screens, linked from
     the main nav. No email is ever sent automatically — "submitted"
     only records that a human sent the package by some other channel.
     21 new tests (`tests/test_supplier_claims.py`).
   - ~~QR label printing and controlled scanning~~ — **Done.** New
     `apps.labels` app: `QRLabel` (opaque, unguessable `token` — same
     `secrets.token_urlsafe` pattern as `SecureShareLink`, never the
     underlying object's real UUID), `QRLabelPrintEvent` (reprint
     history), `QRScanEvent` (scan audit history, including denied
     cross-organization attempts). Supports inventory lots, warehouse
     locations, receiving units (`Receipt`), dispatches, deliveries,
     installation material records, tools, and containers — all 8
     entity types named in the spec, registered in one small table
     (`apps.labels.services._ENTITY_REGISTRY`) rather than 8 separate
     bespoke implementations (ADR-032). The QR image itself is a
     self-contained base64 PNG data URI (via the `qrcode` package,
     added to `requirements.txt`) encoding only the opaque scan URL —
     never the object's ID, never any secret or PII. A scan
     (`/qr/<token>/`) is `login_required`, so an unauthenticated scan
     is sent to log in *before* anything about the label is resolved;
     once authenticated, the view re-checks organization membership
     and then simply redirects into the entity's own existing,
     already-permission-checked detail page — the scan itself performs
     no consequential action and introduces no parallel authorization
     path. Individual print (with a "cantidad" field, logged as a
     `QRLabelPrintEvent` on each explicit print action, never on a
     bare page view) and batch print (checkbox selection, demonstrated
     on the location detail screen's assigned-inventory table) are
     both supported; an invalidated label is never deleted, only
     flagged and linked to its replacement (`replaced_by`), and a scan
     of an invalidated label is denied. Print-link UI coverage: 7 of
     the 8 entity types have a visible "Imprimir etiqueta QR" link on
     their existing detail page (lot, location, tool, receipt,
     container, delivery, installation); `Dispatch` has no own detail
     page in this system (it's summarized inline on the parent
     Material Request's page, not shown as an individually browsable
     row) — its labels are fully supported and tested via the generic
     `labels:print` URL/service layer, just not yet linked from a
     template, since there's no natural per-dispatch row to attach the
     link to today. 22 new tests (`tests/test_qr_labels.py`).
7. ~~`purchasing_to_finance`/`finance_to_logistics` have no "create
   handoff" button" on the Purchase Order detail page~~ — **Done.** All
   8 required gates now have a "create handoff" entry point on their
   target's detail page — the last two were wired into
   `procurement/po_detail.html`, reusing the exact same
   `{% url 'workflow:create' ... %}` button pattern already used on the
   Shipment/Material Request/Delivery/Installation detail pages, never a
   new create-handoff mechanism. Verified by 3 new tests
   (`tests/test_workflow_handoffs.py`) and live: a real HTTP click
   through the button created a genuinely `ready_for_submission` handoff
   for an approved PO.

## Delivery/Installation/Inspection/Final Acceptance milestone — honest gaps

- ~~Multi-lot split dispatch has no dedicated UI action~~ — **Done.** The
  material request detail page now shows every active reservation per
  line (lot, remaining, an editable quantity) and dispatches whatever
  quantities are submitted in one request, calling
  `apps.requests.services.create_dispatch` with explicit per-reservation
  tuples. `DispatchLine.reservation` (new FK, migration
  `requests.0003_dispatchline_reservation`) records which specific
  reservation each dispatched quantity was drawn from, so a line's
  dispatch can never exceed what a *specific* lot's reservation actually
  holds even when the line-wide aggregate would allow it (see
  ADR-023). Verified live: reserved 6 units from one lot and 4 from a
  second lot against the same line, dispatched both in a single
  submission, confirmed two distinct `DispatchLine` rows against the
  correct lots.
- **Duplicate-click protection is deliberately asymmetric.**
  `create_installation_record` and `create_project_receipt` are
  idempotent (a repeated submit returns the existing row — see ADR-018);
  `create_inspection` is not, because a genuine reinspection must always
  create a new row. A rapid double-click on "Registrar inspección" could
  in principle create two near-identical inspection rows with duplicated
  punch-list items; this is mitigated only by human review (the
  duplicate would show as a second, identical-looking inspection in the
  installation's history), not a hard technical constraint.
- ~~Installer/inspector assignment dropdowns list every user in the
  organization~~ — **Done.** `assigned_installer` on the installation-
  creation form is now scoped to active users holding a role in the
  department actually configured to do installation work
  (`installation_to_inspection.from_department` — read from the same
  `GateDefinition` the gate engine uses, never a hard-coded department
  name), further narrowed to users with `UserProjectAccess` to the
  target project when the project has any such grants configured. This
  was always enforced server-side regardless of who was picked (a pure
  data-entry gap, not a security one) — the fix only tightens what the
  dropdown offers. Inspector assignment has no dropdown at all
  (`create_inspection` always records `request.user` as the inspector),
  so there was nothing to scope there.
- ~~Evidence/photo upload is modeled but not wired into these screens~~
  — **Done.** `apps.audit.services.attach_evidence`/`list_evidence` wire
  the generic `Attachment` model (content_type/object_id) into the
  Delivery/InstallationRecord/InspectionRecord detail pages, reusing
  `apps.documents.views.DocumentUploadForm` as-is (extension/size
  validation, SHA-256 dedup, never duplicated). Access to the upload
  action is gated by the same `_deny_cross_project` check as every other
  action on these screens; the download itself goes through the
  pre-existing, unchanged `documents:download` view (organization-scoped,
  not additionally project-scoped — same as every other document in the
  system, a deliberate non-change). None of this milestone's 3 gates
  currently declares an `evidence_requirements` list on its `GateResult`,
  so evidence is available and browsable but not yet a hard blocking
  requirement for any of the 3 gates.
- **Mobile rendering is structurally, not visually, verified** — same
  honesty convention as the Priority 0 milestone's item 7. Every
  list/detail template across the *entire* application now wraps its
  tables in a horizontally-scrolling container (a later-session audit
  found and fixed 19 tables across 9 templates that predated this
  milestone and lacked it — `cost`, `documents`, `inventory`,
  `procurement`, `receiving`, `shipments`, `workflow`, plus the
  self-contained HTML snapshot export, which also gained a viewport meta
  tag). **No headless browser or screenshot tool is available in this
  environment**, so no pixel-level visual regression pass at multiple
  viewport widths has ever been run — this is recorded honestly as a
  standing limitation of the environment, not a skipped task.

## Not yet built (integrations/infrastructure)

8. **OCR.** `DocumentClassificationResult`/`DocumentFieldSource` model
   what an OCR pipeline would populate; no OCR engine (local Tesseract or
   otherwise) is wired in. Scanned-PDF uploads are stored and downloadable
   but not automatically transcribed.
9. **Translation.** Same as above — the provenance fields exist
   (`proposed_translation`/`confirmed_translation`), no translation
   adapter is implemented.
10. **QuickBooks / MarketMatch integration.** Deliberately not built —
    see `QUICKBOOKS_INTEGRATION_DISCOVERY.md` and
    `MARKETMATCH_INTEGRATION_PATH.md` for why and what would be needed.
11. ~~Rate limiting on login/share-link endpoints~~ — **Done.**
    `apps.core.ratelimit` (a small fixed-window counter backed by
    Django's cache framework, no Redis/Celery dependency per
    `ASSUMPTIONS.md` A3) gates `RateLimitedLoginView` (10 failed
    attempts / 5 minutes per IP) and the public `shared_view` share-link
    endpoint (30 requests / minute per IP, since it's fully
    unauthenticated). **Known limitation of this implementation:** the
    default `LocMemCache` backend is per-process, so a multi-worker
    Gunicorn deployment enforces the limit per worker, not globally
    across the whole server — acceptable for this pilot's scale (a
    single small server, ~8 named users), recorded honestly rather than
    overstated as a hardened, distributed rate limiter.
12. **CI pipeline** (automated test run on every push) was not requested
    and was not built in this pass; tests are run manually via `pytest`.

## Fixed during the Delivery/Installation/Inspection/Final Acceptance milestone

- **Duplicate installation creation** — `create_installation_record` had no
  idempotency guard; an accidental repeated form submission during this
  milestone's own live HTTP walkthrough created two `InstallationRecord`
  rows for the same `(project_receipt, delivery_line)`. Fixed with a
  `select_for_update` + first-existing-wins guard (ADR-018), covered by
  `test_21c_duplicate_installation_creation_is_idempotent`. The same
  pattern was applied preemptively to `create_project_receipt`
  (`test_21d`).
- **Final-acceptance detail could become permanently unreachable** — the
  first version of `installation_final_accept` called
  `apps.workflow.services.accept_handoff` itself before recording the
  domain decision. A user who instead accepted the same handoff through
  the generic, already-exposed `/flujo/<id>/aceptar/` button (a fully
  valid entry point, reachable from the workflow inbox without ever
  visiting the installation page) left the record with no way to ever
  capture the accepted-vs-conditional decision. Caught during this
  milestone's live walkthrough (not by a unit test, since the tests only
  called the service functions directly). Fixed per ADR-021: the
  domain-specific screen now only activates once the handoff is already
  `ACCEPTED`, by whichever route.

## Fixed during the Gate Controls milestone

- One test bug (not a product bug): `test_successful_handoff_full_lifecycle`
  initially forgot to attach the evidence `logistics_to_receiving`
  requires before submitting, so it failed with `GateBlockedError` on
  first run — exactly the correct behavior; the test was fixed to attach
  evidence first, not the product code.
- A migration had to be deleted and regenerated once after making
  `Handoff.organization` nullable, to avoid Django's interactive
  "provide a one-off default" prompt (no data existed yet to make this a
  real risk — see `docs/implementation-log.md` step 9).

## Fixed during the Priority 0 delivery (recorded so they aren't rediscovered)

- A `lambda` default on a model field broke `makemigrations` — fixed.
- Whitenoise's manifest storage failed on a vendored CSS file's dangling
  source-map reference — fixed.
- `docker-compose.prod.yml` silently dropped any env var not explicitly
  hand-listed — fixed by switching to `env_file:`.
- The production Docker image had briefly (during validation) been
  running against an in-container SQLite file instead of Postgres because
  a local dev `.env` was copied into the image — fixed with
  `.dockerignore` and a `DEBUG`-gated settings guard, and the entire
  validation sequence was re-run against genuine Postgres afterward. See
  `docs/architecture-decisions.md` ADR-011/ADR-012 and
  `docs/implementation-log.md` for the full account.
- `backup.sh`'s document-archiving step used a broken `docker run` volume
  invocation — fixed by archiving via `docker compose exec ... tar`.

## Data-quality ambiguities (not bugs — see `DATA_QUALITY_AND_UNCERTAINTY.md`)

The W5057/W5097 possible-match, the Sole-26 building/project labeling
conflict, the unattributed 200-unit accessories line, and the 19-vs-20
quartz slab packed-quantity conflict are all *intentionally* left
unresolved in the data, exactly as the governing spec requires — these
are not gaps to close but the system correctly doing its job.
