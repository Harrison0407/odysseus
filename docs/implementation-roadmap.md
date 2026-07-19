# Implementation Roadmap

## Delivered in this pass (Priority 0 vertical slice + live fixture)

1. Phase 0 — read every source document, produced `SOURCE_DOCUMENT_ANALYSIS.md`, `SOURCE_PACKAGE_INDEX.md`, `DATA_QUALITY_AND_UNCERTAINTY.md`.
2. Canonical data model — ~150 models across 18 apps, migrated cleanly from empty.
3. Priority 0 vertical-slice UI — documents, procurement, dual-manifest shipment screen, receiving, inventory, requests, cost, HTML snapshot + share links.
4. Live-container acceptance fixture imported and verified against the actual UI (curl-driven smoke tests) and an automated test suite (21 tests).
5. Production Docker Compose stack (Postgres + Gunicorn + Caddy), validated live: build, migrate, seed, restart/persistence, backup, restore, authorized/unauthorized access, HTML snapshot generation — all exercised against the real stack, with two real bugs found and fixed during that validation (see `architecture-decisions.md` ADR-011, ADR-012).

## Delivered in this pass (Gate Controls and Formal Handoffs)

6. Reusable gate-evaluation engine (`apps.workflow.gates`) covering all
   8 required minimum transitions (Purchasing→Finance→Logistics→
   Receiving→Warehouse→Project→Installation→Inspection→Acceptance), each
   backed by a real evaluator reading existing Priority 0 models
   (`PaymentMilestone`, `ManifestVariance`/`CustomsReviewDecision`,
   `QuarantineRecord`, `Discrepancy`, `MaterialRequestLine`, `Delivery`,
   `InstallationRecord`/`InspectionRecord`) — no gate logic duplicated
   into a view or template.
7. Full handoff lifecycle service layer (`apps.workflow.services`):
   create (idempotent), submit (with missing-evidence and authorized-
   override handling), accept (with ownership transfer and, for
   `Shipment`-anchored gates, automatic status advancement), reject,
   return for correction, and corrected resubmission (creates a new
   superseding version, never edits the rejected one) — all transactional
   with row-level locking so duplicate/concurrent requests are safe by
   construction, not by convention.
8. Role-aware, filterable handoff inbox (`/flujo/`) plus a detail screen
   showing live gate readiness, evidence, comments, and full decision
   history; wired into the Shipment and Material Request detail pages;
   dashboard cards now link into filtered inbox views instead of showing
   decorative counts.
9. `UserProjectAccess` (modeled but unused since Priority 0) is now
   actually enforced for cross-project isolation; `ResponsibilityAssignment`
   (same situation) now actually records ownership transfer.
10. 32 new automated tests (16 gate-evaluator, 16 handoff-lifecycle/
    security), all passing alongside the pre-existing 21 (53 total). The
    full lifecycle — including a genuinely blocked gate, an authorized
    override, and an acceptance that flips `Shipment.status` — was also
    driven through real HTTP requests against a running server using the
    actual imported live-container fixture, not only unit tests.

## Delivered in this pass (Delivery, Installation, Inspection, and Final Acceptance)

11. Production UI for the 3 gates left engine-only after the Gate
    Controls milestone: delivery planning/dispatch, per-line delivery
    recording (partial/multi-trip/damage/refusal), project receipt,
    installation (assignment, progress, quantity guard with authorized
    override, installer acknowledgement, supervisor confirmation),
    inspection (pass/conditional/fail, punch-list defects, reinspection
    chaining), and final acceptance (accepted/conditional) —
    `/solicitudes/entregas/`, `/solicitudes/instalaciones/`,
    `/solicitudes/inspecciones/`, `/solicitudes/aceptaciones/`.
12. Every transition still goes exclusively through the reused
    `apps.workflow.gates`/`apps.workflow.services` engine — no new
    transition logic in any view, form, template, or JS.
13. Quantity-invariant enforcement in a new `apps.requests.services`
    domain layer: delivered/installed quantities can never exceed
    validly issued/delivered amounts except through an audited,
    permission-gated override; every inventory consequence (dispatch,
    quarantine on damage, installation consumption) is a real, posted
    `InventoryMovement` — never a silent balance edit.
14. Cross-project isolation extended to these new screens
    (`apps.workflow.services.can_view_target`, generalized from the
    Gate Controls milestone's `can_view_handoff`) — enforced at the
    view/service layer on every detail and action endpoint, and at the
    queryset level on every list screen.
15. 40 new automated tests (`tests/test_delivery_installation_acceptance.py`)
    covering all 27 required scenarios, passing alongside the
    pre-existing 53 (93 total). Demonstrated live through real HTTP
    requests against a running server (not only unit tests) — see
    `docs/REQUIREMENTS_TRACEABILITY.md` and `docs/implementation-log.md`
    for the full transcript.
16. Two real bugs found and fixed during this milestone's own live
    walkthrough (duplicate installation creation; final-acceptance
    detail becoming unreachable after a generic-route accept) — see
    `docs/architecture-decisions.md` ADR-018/ADR-021 and
    `docs/KNOWN_LIMITATIONS.md`.

## Delivered in this pass (Priority 0 completion — continuing autonomous session)

17. Evidence/photo upload (`apps.audit.services.attach_evidence`), wired
    into the Delivery/InstallationRecord/InspectionRecord detail pages.
18. Multi-lot/split dispatch UI, with a real concurrency/correctness
    bug found and fixed live (`DispatchLine.reservation`, ADR-023).
19. Department/project-scoped installer assignment
    (`apps.requests.views._assignable_users`, ADR-024).
20. Structural mobile-responsive audit — 19 pre-existing tables across
    9 templates wrapped in a horizontally-scrolling container.
21. `purchasing_to_finance`/`finance_to_logistics` create-handoff
    buttons on the Purchase Order detail page — all 8 required gates
    now have one.
22. Rate limiting on login (10 attempts/5 min per IP) and the public
    share-link view (30 requests/min per IP) — `apps.core.ratelimit`.
23. Detailed internal receiving manifest print view (spec 13A.8,
    10 sections) — `apps.reports.views.receiving_manifest_snapshot`.
24. Landed-cost allocation-run trigger UI — and the calculation engine
    itself, which did not exist anywhere before this pass
    (`apps.cost.services`, ADR-026).

Every item above was verified by automated tests and, where the
underlying data supports it, against the actual imported MEDUWY575021
fixture through real HTTP requests against a running dev server — see
`docs/implementation-log.md` for the full account of each.

## Next increment

- A literal "cannot advance" UI message tied directly to gate-blocked transitions outside the handoff detail page itself (today the handoff detail page IS that message; a shipment/request detail page doesn't yet independently repeat it).
- A per-line entry UI for the manual-percentage/manual-amount landed-cost allocation methods (fully implemented and tested at the service layer; only the automatic/basis-driven methods have a form today).
- A `CostDocument`/`CostCharge` upload UI (currently created via the ORM/fixture — this pass added the calculation engine and its trigger UI, not a document-upload flow, which is a distinct, larger feature).

## Priority 1 (tracked in `KNOWN_LIMITATIONS.md`)

25. ~~CONFOTUR reconciliation UI~~ — **Done** (continuing autonomous
    session). `apps.customs.services`/`/aduanas/confotur/` — see
    `docs/implementation-log.md` for the full account.
26. ~~Tool custody UI~~ — **Done.** `apps.tools.services`/
    `/herramientas/` (ADR-028); also fixed a real pre-existing
    `FieldError` crash in the Almacén dashboard found while building
    this.
27. ~~Cycle-count UI~~ — **Done.** `apps.inventory.services`/
    `/almacen/conteos/` — blind counting, recount tracking, approved
    variances posted as real `InventoryMovement`s (A19).
28. ~~Storage capacity/suitability warnings~~ — **Done.**
    `apps.inventory.services` (`check_location_suitability`/
    `enforce_location_suitability`, ADR-029), wired into put-away
    (`apps.receiving.services.post_receipt_line`) and the
    newly-activated `apps.requests.services.transfer_lot`;
    `/almacen/ubicaciones/<id>/` detail+transfer screens.
29. ~~External storage comparison calculator~~ — **Done.**
    `StorageComparisonScenario`/`AlternativeStorageOption`
    (re-parented + extended, ADR-030), `apps.receiving.services`
    (`create_comparison_scenario`/`add_storage_option`/
    `compare_scenario_options`/`finalize_comparison_scenario`);
    `/recepcion/planes/<id>/` + `/recepcion/comparaciones/<id>/`
    screens, printable HTML export.
30. ~~Supplier claim package generation~~ — **Done.** New
    `apps.claims` app: `SupplierClaim` (ADR-031), full lifecycle in
    `apps.claims.services`, evidence via the existing
    `attach_evidence` mechanism, printable claim package via the
    existing `_save_html_snapshot` mechanism; `/reclamos/` screens.
31. ~~QR labels and controlled scanning~~ — **Done.** New
    `apps.labels` app: `QRLabel`/`QRLabelPrintEvent`/`QRScanEvent`
    (ADR-032), one entity registry covering all 8 required entity
    types, opaque-token scan payloads, `login_required` scan landing
    forwarding into each entity's existing detail page, individual +
    batch printing, reprint history, invalidate-and-replace.

All four Priority 1 features are now complete. See
`docs/FINAL_VALIDATION_REPORT.md` for the consolidated system status.

## Physical property / field operations release (added in a later session)

32. ~~Physical building/floor/apartment hierarchy~~ — **Done.**
    `BuildingFamily` (new, ADR-033) + extended `Building`/`Floor`/`Unit`,
    idempotent `import_buildings_and_units` command (transcribed from
    the 3 supplied source PDFs), `/propiedades/` screens.
33. ~~Drawing and floor-plan register~~ — **Done.** New `apps.drawings`
    app: `Drawing` wraps the existing `Document` provenance system
    (ADR-034); a new revision is always a new row (`supersedes`),
    never an edit — approval requires the same senior-authorization
    permission used elsewhere. `/planos/` screens, linked from building/
    unit detail. All 3 supplied source PDFs registered
    (`register_source_drawings`).
34. ~~Order destination allocation and purchased spares~~ — **Done.**
    `OrderLineAllocation`/`PurchasedSpare` (new, ADR-035) extend
    `apps.procurement`; spares are an authorization record only —
    quantity received/available/reserved always read from the existing
    inventory ledger, never a second balance. `/compras/lineas/<id>/
    asignacion/` + allocation/spares list screens.
35. ~~Field issue tracking~~ — **Done.** New `apps.fieldissues` app
    (ADR-036): `FieldIssue` requires only `building`, everything else
    refinable later; full lifecycle including reject/resubmit/
    reinspection; closure requires `can_override_gates` regardless of
    who performed the correction. `/incidencias/` screens.
36. ~~Lawson training and reference installations~~ — **Done.** New
    `apps.training` app (ADR-037), mirroring the field-issue location/
    evidence pattern; reference-installation approval requires
    supervisor sign-off first. `/capacitaciones/` screens, live-
    validated in the real ARENA T1 Building 11.
37. ~~Apartment walkthroughs and corrective actions~~ — **Done.** New
    `apps.walkthroughs` app (ADR-038); building-agnostic (only
    `building` required), 6 configurable purposes, a defect becomes a
    real `FieldIssue`, delivery readiness always read from that
    issue's live status. `/recorridos/` screens, live-validated in
    ARENA T1 Building 9.
38. ~~Unclassified Evidence Inbox~~ — **Done.** New
    `apps.evidenceinbox` app (ADR-039): every upload wraps the existing
    `Document`/`DocumentVersion` provenance mechanism untouched;
    classification is a separate, reassignable, generic pointer
    (`EvidenceClassification`) covering building/floor/unit/
    walkthrough/walkthrough item/training session/field issue/product/
    supplier/purchase order line/shipment/container/installation/
    inspection record, org-isolation-checked via the shared
    `resolve_organization()` resolver. `/evidencias-sin-clasificar/`
    screens, live-validated with a real historical-style photograph
    uploaded and classified to a real ARENA T1 Building 11 unit.
39. ~~Interactive Apartment Plan / Room-Zone layer~~ — **Done.** New
    `apps.unitplans` app (ADR-040): reusable `UnitPlanTemplate` per
    family/unit-type-letter/floor-variant (never per apartment),
    `PlanZone` room/zone records, `UnitPlanAssignment` linking each
    physical unit to its current effective template. Only PALMERA
    (confirmed by direct inspection of its source PDF) has real
    per-unit-type plans (Tipos A-D) with AI-proposed Draft zones;
    every other family is an honest `Missing Source` slot. Interactive
    viewer with clickable zones, issue/photo/walkthrough-item creation
    prefilled with full building/floor/unit/room/plan/zone traceability,
    and a secure admin mapping/review/approve/supersede screen.
    `/propiedades/unidades/<id>/plano/` + `/propiedades/admin-planos/`
    screens, live-validated across MARE B Building 25, SOLE Building
    17, SOLE PH Building 18 (including its duplex penthouse), SOLE 26
    Building 26, PALMERA, and ARENA T1 Building 9.

## Explicitly out of scope for any near-term increment

Local OCR execution, automated machine translation, QuickBooks live
integration, WhatsApp integration, government CONFOTUR e-submission (the
spec itself excludes electronic submission — evidence/reconciliation
only).
