# Requirements Traceability Matrix

Status legend: **Done** = modeled, implemented, has a passing automated test.
**Modeled** = data model + core logic exist but UI/workflow is thin or
manual. **Planned** = intentionally deferred past this delivery (see
`KNOWN_LIMITATIONS.md` for the full, honest list — this is a staged
delivery per `ASSUMPTIONS.md` A1, not a claim that all 38 spec sections
are production-complete).

## Priority 0 capabilities (spec section 5)

| Capability | Module(s) | DB records | UI | Test(s) | Status |
|---|---|---|---|---|---|
| Users, roles, assignments, handoffs | `apps.accounts`, `apps.workflow` | `UserRole`, `ResponsibilityAssignment`, `Handoff`, `GateDefinition`, `GateOverride` | Login, persona dashboards, `/flujo/` inbox + detail | `test_permissions.py`, `test_workflow_gates.py` (16), `test_workflow_handoffs.py` (16) | Done — see "Gate Controls and Formal Handoffs" section below |
| Multilingual document upload + provenance | `apps.documents` | `Document`, `DocumentVersion`, `DocumentFieldSource` | `/documentos/` upload/list/detail | `test_documents.py` (3 tests) | Done (upload/hash/duplicate/download-auth); OCR/translation adapters modeled but not wired to a real engine — **Modeled** |
| PO / invoice / packing-list / BL / container matching | `apps.matching`, `apps.shipments` | `MatchRun`, `MatchCandidate`, `ManifestLineSource` | Shipment detail (variance matrix) | `test_live_container_fixture.py` | Done for the live fixture case; automatic fuzzy-matching engine (scoring heuristics beyond the fixture) is **Modeled**, not yet generalized |
| Payment and origin readiness | `apps.procurement` | `PaymentMilestone`, `PaymentRecord` | PO detail page | manual verification | Modeled |
| Pre-arrival storage and receiving plan | `apps.receiving` | `ReceivingPlan`, `StorageCapacityReservation`, `InspectionPlan` | `/recepcion/planes/<id>/` overview (added alongside the external storage comparison calculator — see below); `StorageCapacityReservation`/`InspectionPlan` themselves have no dedicated UI yet | `tests/test_storage_comparison.py` (plan-detail view coverage) | Modeled — the plan overview and its comparison scenarios are Done; the reservation/inspection-plan sub-records are still data-model only |
| Physical receiving and risk-based inspection | `apps.receiving` | `Receipt`, `ReceiptLine`, `Inspection`, `SamplingRule` | `/recepcion/` list/detail/post-line | `test_receiving_and_inventory.py` (4 tests) | Done |
| Quarantine, damage, shortage, overage, claims | `apps.receiving`, `apps.matching`, `apps.claims` | `QuarantineRecord`, `DamageRecord`, `Discrepancy`, `SupplierClaim` | receiving detail page; `/reclamos/` list/create/detail (added in a later session, see below) | `test_damaged_quantity_is_quarantined...`, `tests/test_supplier_claims.py` (21 tests) | Done |
| Location-based ledger inventory | `apps.inventory` | `InventoryLot`, `InventoryMovement` | `/almacen/ubicaciones/` | `test_onhand_quantity_is_derived_from_ledger...` | Done |
| Material requests, dispatch, project acceptance, returns | `apps.requests` | `MaterialRequest`, `Dispatch`, `Delivery`, `ProjectReceipt` | `/solicitudes/` list/create/detail, reserve/dispatch actions | 40 tests (`tests/test_delivery_installation_acceptance.py`) | Done — see "Delivery, Installation, Inspection, and Final Acceptance milestone" section below |
| Complex product kits and component completeness | `apps.items` | `AssemblyDefinition`, `KitDefinition`, `KitInstance` | none yet | `test_quartz_slab_and_fabricated_top_are_distinct_identities` | Modeled |
| Landed cost | `apps.cost` | `CostDocument`, `CostAllocationRun`, `LandedCostVersion` | `/costos/` list/detail | manual verification | Modeled; allocation-run UI (triggering a calculation) is **Planned** |
| Dashboards and responsibility queues | `apps.core` | — | persona dashboards (6 variants) | manual verification | Done |
| Historical import | `apps.shipments` mgmt command | — | `import_live_container_fixture` | `test_live_container_fixture.py` (9 tests) | Done for the live fixture; general-purpose "any historical shipment" importer is **Planned** |
| Self-contained HTML snapshot and secure share links | `apps.reports` | `ReportVersion`, `SecureShareLink`, `ShareSnapshot` | snapshot download, share view | manual verification (validated live in the production Docker stack) | Done |
| Production deployment package | `deploy/` | — | — | full manual validation (see `docs/FINAL_VALIDATION_REPORT.md`) | Done |
| Automated tests and audit trail | `tests/`, `apps.audit` | `AuditEvent` | — | 21 passing tests | Done |

## Live-container acceptance fixture (spec section 13A.10 / 34)

| Acceptance requirement | Verified by |
|---|---|
| Match each local PO to its China-side commercial source | `import_live_container_fixture` links all 5 POs to their PIs; `SOURCE_DOCUMENT_ANALYSIS.md` §3 |
| Build the official summary without changing it | `test_official_bl_preserved_verbatim`, `test_official_manifest_is_not_edited_to_match_internal` |
| Build the complete internal operational manifest | `test_internal_manifest_decomposes_broad_categories` (11 lines) |
| Show how broad BL categories decompose into actual products | Shipment detail page, verified live (curl smoke test) rendering all 11 lines under 3 BL categories |
| Carry open PI quantities across shipments | `test_open_commitment_carryover_for_w5084_kitchens` (40 total, 10 in this container, 30 open) |
| Link prior-site evidence to earlier fulfillment | `PriorFulfillmentEvidence` modeled; no prior-fulfillment claim exists in this particular fixture (none of the 5 POs claimed prior receipt), so this path has model coverage but no fixture-driven test yet — **Modeled** |
| Treat replacements/corrective cargo separately from normal purchases | `test_replacement_cargo_not_counted_as_new_purchase` (2 `ReplacementCase` records) |
| Show remaining open balances | Same as carryover test above |
| Generate Manuel's receiving manifest | `ReceivingPlan`/`ReleasePacket` modeled; the dedicated "detailed internal receiving manifest" print view (spec 13A.8, 10-section layout) is **Planned** — today Manuel would use the shipment detail page's internal-manifest panel, which has the data but not that exact document layout |
| Reconcile package/weight/CBM/cost without double counting | Shipment detail "Reconciliación de totales" panel; verified live to show 520/22500kg/40cbm official vs. computed internal totals |
| Flag cargo not clearly represented for Customs & Logistics | `test_unattributed_cargo_flagged_for_customs_review` (3 flagged lines, each with a `CustomsReviewDecision`) |

## Gate Controls and Formal Handoffs milestone

A stage no longer closes merely by changing a status field — every one of
the 8 required minimum transitions is enforced through
`apps.workflow.gates.evaluate_gate()` (a single reusable evaluator per
gate, never duplicated into a view or template) and
`apps.workflow.services` (submit/accept/reject/return/resubmit, all
transactional with row-level locking).

| Required transition | Gate code | Target model | Evaluator test(s) |
|---|---|---|---|
| Purchasing → Finance | `purchasing_to_finance` | `PurchaseOrder` | `TestPurchasingToFinance` (2) |
| Finance → Logistics | `finance_to_logistics` | `PurchaseOrder` | `TestFinanceToLogistics` (2) |
| Logistics → Receiving | `logistics_to_receiving` | `Shipment` | `TestLogisticsToReceiving` (4) — reuses the dual-manifest engine (`ManifestVariance`/`CustomsReviewDecision`) built in the Priority 0 milestone |
| Receiving → Warehouse | `receiving_to_warehouse` | `Shipment` | `TestReceivingToWarehouse` (5) — quarantine and discrepancy blocking |
| Warehouse → Project | `warehouse_to_project` | `MaterialRequest` | `TestWarehouseToProject` (2) |
| Project Delivery → Installation | `project_delivery_to_installation` | `Delivery` | `TestProjectDeliveryToInstallationGate` (2) — **Done, full UI** (`/solicitudes/entregas/`) |
| Installation → Inspection | `installation_to_inspection` | `InstallationRecord` | `TestInstallationToInspectionGate` (2) — **Done, full UI** (`/solicitudes/instalaciones/`) |
| Inspection → Acceptance | `inspection_to_acceptance` | `InstallationRecord` | `test_11`–`test_17` in `tests/test_delivery_installation_acceptance.py` — **Done, full UI** (`/solicitudes/inspecciones/`, `/solicitudes/aceptaciones/`) |

| Capability | Verified by |
|---|---|
| Gate readiness evaluation (ready/blocked/warning, structured unmet requirements) | `GateResult` dataclass, all 16 `test_workflow_gates.py` tests |
| Blocking conditions never silently resolved | Every blocked-gate test asserts the target object (BL, manifest line) is untouched after evaluation |
| Formal handoff creation, submission, acceptance, rejection, return for correction | `test_successful_handoff_full_lifecycle`, `test_rejection_records_reason_and_decision`, `test_return_for_correction_and_resubmission_creates_new_superseding_version` |
| Corrected resubmission preserves old version untouched | Same test — asserts `SUPERSEDED` status and unmodified `rejection_or_correction_reason` on the old row |
| Missing-evidence blocking | `test_missing_evidence_blocks_submission_even_if_otherwise_ready`, `test_attaching_evidence_allows_submission` |
| Authorized override (permission + written reason + actor + timestamp + before/after state) | `test_authorized_override_records_reason_actor_and_before_after_state` |
| Unauthorized override denied | `test_unauthorized_override_is_denied` |
| Unauthorized acceptance / cross-role access denied | `test_unauthorized_user_cannot_accept_handoff`, `test_required_role_to_accept_enforced` |
| Duplicate submission is idempotent (double-click / refresh safe) | `test_creating_handoff_twice_is_idempotent`, `test_submitting_twice_second_call_raises_instead_of_double_processing` — backed by both an app-level pre-check and a DB-level partial unique constraint |
| Concurrent acceptance race-safety | `test_concurrent_acceptance_only_the_first_wins` (`select_for_update` row locking) |
| Role-aware inbox filtering | `test_inbox_para_mi_shows_only_handoffs_awaiting_this_user`, live-verified via `curl` (see `FINAL_VALIDATION_REPORT.md`) |
| Cross-project isolation | `test_cross_project_isolation_denies_view_and_accept` — uses the pre-existing but previously-unenforced `UserProjectAccess` model, now actually wired into `can_view_handoff`/`can_accept_handoff` |
| Ownership transfer on acceptance | `test_successful_handoff_full_lifecycle` — asserts a new open `ResponsibilityAssignment` row and, for `Shipment`-anchored gates, an automatic `Shipment.status` transition |
| Complete, immutable audit history | `test_complete_audit_history_recorded_for_full_lifecycle` |
| Overdue / blocked-work visibility | `apps.workflow.services.is_overdue` (reuses the pre-existing `ServiceLevelTarget` model, no new SLA model needed); inbox "bloqueadas" and "vencidas" views |
| Dashboard cards link to actionable filtered views, not decorative totals | `templates/core/_dashboard_common.html` links directly to `/flujo/?vista=para_mi` and `&overdue=1` |

Demonstrated live against the actual imported MEDUWY575021 fixture: a
real `logistics_to_receiving` handoff was created, found genuinely
blocked (3 unresolved critical `ManifestVariance` rows, 1 critical
`Discrepancy`, no `ReceivingPlan`), overridden by Harrison with a written
reason, submitted, and accepted by Manuel — which correctly transitioned
`Shipment.status` to `released_to_receiving` and transferred
`ResponsibilityAssignment` to Manuel/Almacén. Full transcript in
`docs/implementation-log.md`.

## Delivery, Installation, Inspection, and Final Acceptance milestone

Builds production UI/workflows for the 3 gates left engine-only after the
Gate Controls milestone, reusing that same gate-evaluation engine and
handoff service layer end to end — no transition logic is duplicated in
any view, form, template, or JS.

| Capability | Verified by |
|---|---|
| Delivery planning/dispatch (reserve → dispatch → deliver) | `apps.requests.services.reserve_line`/`create_dispatch`, `apps/requests/urls.py` reserve/dispatch actions |
| Complete delivery | `test_01_complete_delivery_accepts_full_dispatched_quantity`; live HTTP walkthrough |
| Partial delivery | `test_02_partial_delivery_sets_partially_delivered_status_not_delivered` |
| Multi-trip delivery (never double-counts) | `test_03_multi_trip_delivery_recomputes_never_double_counts` |
| Failed/refused delivery | `test_04_failed_delivery_recorded_as_rejected_not_silently_dropped` |
| Delivery damage (quarantine posting, delta-only) | `test_05_damaged_delivery_quarantines_only_the_new_delta` |
| Quantity exceeding available inventory/dispatched | `test_06_reservation_above_available_inventory_is_rejected`, `test_06b` |
| Valid installation | `test_07_valid_installation_within_delivered_quantity` |
| Installation above delivered quantity (blocked / authorized override) | `test_08_installation_above_delivered_quantity_blocked_without_override`, `test_08b_..._with_authorized_override_succeeds_and_is_audited` |
| Incomplete installation | `test_09_incomplete_installation_leaves_is_complete_false` |
| Installation damage / missing components | `test_10_installation_damage_and_missing_components_recorded` |
| Successful / failed / conditional inspection | `test_11`, `test_12`, `test_13` |
| Punch-list creation | `test_14_failed_inspection_creates_blocking_punch_list_items` |
| Correction and reinspection (never erases failed history) | `test_15_reinspection_chains_to_previous_and_never_erases_failed_history` |
| Final acceptance | `test_16_final_acceptance_recorded_once` |
| Acceptance blocked by open critical defect | `test_17_acceptance_gate_blocked_while_blocking_defect_open` |
| Authorized / unauthorized gate override | `test_18_authorized_gate_override_at_inspection_to_acceptance`, `test_19_unauthorized_gate_override_denied` |
| Rejection and return for correction (reuses the generic handoff flow) | `test_20_installation_to_inspection_handoff_can_be_returned_for_correction` |
| Duplicate form submission | `test_21`–`test_21d` (final acceptance, punch-list close, installation creation, project-receipt creation) |
| Concurrent update attempt | `test_22_concurrent_handoff_submission_only_first_wins` |
| Role-aware UI / inbox visibility | `test_23_installation_detail_shows_available_actions_only_to_authorized_roles` |
| Cross-project isolation (direct URL, list scoping) | `test_24_cross_project_isolation_denies_direct_url_access_to_installation`, `test_24b_..._denies_direct_url_progress_post` |
| Complete, immutable audit trail | `test_25_complete_audit_trail_for_full_chain` |
| Mobile-rendering smoke tests | `test_26`, `test_26b` |
| No regression in existing tests | Full suite: 93/93 passing (53 pre-existing + 40 new) |
| Evidence/photo upload with provenance and project-access enforcement (added in a later session) | `apps.audit.services.attach_evidence`/`list_evidence`, `TestEvidenceUpload` (4 tests) — reuses `apps.documents` upload/SHA-256/duplicate-detection, verified live via a real multipart HTTP upload + byte-identical download round-trip |
| Multi-lot and split dispatch (added in a later session) | `DispatchLine.reservation` FK, `apps.requests.services.create_dispatch`/`reservation_remaining_quantity`, `TestMultiLotSplitDispatch` (4 tests) — verified live: 6+4 units reserved from two lots against one line, dispatched together in one submission, confirmed as two distinct `DispatchLine` rows against the correct lots |
| Department/project-scoped assignment controls (added in a later session) | `apps.requests.views._department_for_gate`/`_assignable_users` (ADR-024), 2 new tests confirming the installer dropdown includes an Obra user with project access and excludes a Compras user and an Obra user scoped to a different project |
| Structural mobile-responsive audit (added in a later session) | 19 tables across 9 pre-existing templates (`cost`, `documents`, `inventory`, `procurement`, `receiving`, `shipments`, `workflow`) plus 1 in `installation_detail.html` wrapped in a horizontally-scrolling container; div/table tag balance verified for every edited file; full 103-test suite passing afterward — see `KNOWN_LIMITATIONS.md` for why this stays a structural, not visual, verification |
| `purchasing_to_finance`/`finance_to_logistics` create-handoff button (added in a later session) | `procurement/po_detail.html` + `apps.procurement.views.po_detail`, 3 new tests — all 8 required gates now have a create-handoff entry point; verified live via a real HTTP click that created a genuinely `ready_for_submission` handoff |
| Rate limiting on login and share-link endpoints (added in a later session) | `apps.core.ratelimit`, `apps.accounts.views.RateLimitedLoginView`, `apps.reports.views.shared_view`, `tests/test_rate_limiting.py` (4 tests) — login blocked after 10 failed attempts/5 min per IP even with a correct password; share-link view returns 429 after 30 requests/min per IP |
| Detailed internal receiving manifest, spec 13A.8 (added in a later session) | `apps.reports.views.receiving_manifest_snapshot`, `templates/reports/snapshot_receiving_manifest.html`, `tests/test_receiving_manifest_snapshot.py` (4 tests) — verified live against the actual imported MEDUWY575021 fixture (11 internal manifest lines, 11 variances rendered correctly); section-numbering fidelity caveat recorded in `ASSUMPTIONS.md` A16 |
| Landed-cost allocation-run trigger UI, and the calculation engine itself (added in a later session) | `apps.cost.services` (`run_allocation`/`calculate_landed_cost`/`finalize_landed_cost`, ADR-026), `apps.cost.views.shipment_cost_dashboard` + actions, `tests/test_cost_allocation.py` (14 tests) — verified live against the real imported fixture's actual USD 6,900 ocean-freight charge: allocated by CBM across its 11 real manifest lines, calculated, and finalized end-to-end |
| CONFOTUR reconciliation UI — Priority 1 (added in a later session) | `apps.customs.services.detect_duplicate_candidates`/`confirm_duplicate`, `/aduanas/confotur/` screens, `tests/test_confotur_reconciliation.py` (10 tests, including cross-organization isolation and HTTP-level confirm-then-resolved behavior) |
| Tool custody screens — Priority 1 (added in a later session) | `apps.tools.services` (ADR-028), `/herramientas/` screens, `tests/test_tool_custody.py` (11 tests) — also fixed and regression-tested a real pre-existing `FieldError` crash in the Almacén persona dashboard found while building this |
| Cycle-count screens — Priority 1 (added in a later session) | `apps.inventory.services` (`start_cycle_count`/`record_physical_count`/`approve_adjustment`, A19), `/almacen/conteos/` screens, `tests/test_cycle_count.py` (12 tests) — blind counting, recount tracking, and every approved variance posted as a real `InventoryMovement` |
| Storage capacity/suitability warnings — Priority 1 (added in a later session) | `apps.inventory.services` (`check_location_suitability`/`enforce_location_suitability`/`location_current_utilization`, ADR-029), `apps.requests.services.transfer_lot` (activates the previously-unused `Transfer` model), `apps.receiving.services.post_receipt_line` (put-away integration), `/almacen/ubicaciones/<id>/` detail+transfer screens, `tests/test_storage_suitability.py` (20 tests, including cross-organization isolation, authorized/unauthorized override, capacity-exceeded blocking, and warning-only sensitive-material placement) |
| External storage comparison calculator — Priority 1 (added in a later session) | `StorageComparisonScenario` (new, versioned) + `AlternativeStorageOption` (re-parented, extended — ADR-030), `apps.receiving.services` (`create_comparison_scenario`/`add_storage_option`/`compare_scenario_options`/`finalize_comparison_scenario`), `apps.cost.services.convert_to_base_currency` (made public for reuse — never fabricates a rate), `/recepcion/planes/<id>/` + `/recepcion/comparaciones/<id>/` screens, printable/downloadable HTML export via the existing `_save_html_snapshot` mechanism, `tests/test_storage_comparison.py` (21 tests, including multi-currency-without-a-rate, provenance-recorded conversion, immutable scenario versioning, cross-organization isolation, and a full create→add-option→finalize HTTP lifecycle) |
| Supplier claim package generation — Priority 1 (added in a later session) | New `apps.claims` app: `SupplierClaim` (ADR-031), full DRAFT→APPROVED→SUBMITTED→SUPPLIER_RESPONDED→RESOLVED→CLOSED lifecycle in `apps.claims.services`, evidence via `apps.audit.services.attach_evidence` (no new evidence model), printable/downloadable claim package via `apps.reports._save_html_snapshot` (extended with a `content_object` link, `ReportVersion.ReportType.CLAIM_PACKAGE`), `/reclamos/` list/create/detail screens, `tests/test_supplier_claims.py` (21 tests, including sequential claim numbering, official-vs-operational variance preserved untouched, approval blocked without evidence, full lifecycle to closure, duplicate-submission prevention, cross-organization isolation, and a full HTTP lifecycle) |
| QR labels and controlled scanning — Priority 1 (added in a later session) | New `apps.labels` app: `QRLabel`/`QRLabelPrintEvent`/`QRScanEvent` (ADR-032), one entity registry covering all 8 required entity types (inventory lot, warehouse location, receipt, dispatch, delivery, installation record, tool, container), opaque token payload (never the raw object ID), `login_required` scan-landing view (`/qr/<token>/`) that forwards into each entity's own existing permission-checked detail page, individual + batch printing, reprint history, invalidate-and-replace lifecycle, `tests/test_qr_labels.py` (22 tests, including opaque-payload verification, invalid/invalidated-token denial, cross-organization scan denial with audit logging, batch generation excluding foreign-organization entities, and a full HTTP print→invalidate→reprint lifecycle) |

Demonstrated live against a running dev server with real seeded users
(Miguel/Obra, Harrison/Dirección, Markeris/Compras) and real HTTP
requests (cookies + CSRF tokens, no test client shortcuts): reserve →
dispatch → full delivery → project receipt → installation → installer
acknowledgement → supervisor confirmation → `installation_to_inspection`
handoff created/submitted/accepted → **failed** inspection with 2
blocking punch-list defects → `inspection_to_acceptance` handoff created
and confirmed genuinely blocked (both "not approved" and "open blocking
defects" reasons shown) → a plain submit rejected → an unauthorized
override attempt by Miguel denied server-side even via a direct POST
bypassing the UI → both defects closed → a passing reinspection
recorded → the handoff re-submitted (now ready) → accepted by Harrison →
final acceptance detail ("Aceptado") recorded → a duplicate final-accept
submission caught gracefully (no second row) → Markeris (no project
access) denied both direct-URL access and any trace in the list view.
Full transcript in `docs/implementation-log.md`.

## Physical property / field operations release (added in a later session)

| Capability | Module(s) | DB records | UI | Test(s) | Status |
|---|---|---|---|---|---|
| Physical building/floor/apartment hierarchy | `apps.projects` | `BuildingFamily` (new), `Building`/`Floor`/`Unit` (extended, ADR-033) | `/propiedades/` catalog, family/building/unit detail, search | `tests/test_property_master.py` (20 tests) | Done |
| Drawing and floor-plan register | `apps.drawings` (new app) | `Drawing` (ADR-034) | `/planos/` list/detail, linked from building/unit detail | `tests/test_drawing_register.py` (12 tests) | Done — graphical/spatial floor-plan schematic explicitly not built (A39) |
| Order destination allocation and purchased spares | `apps.procurement` (extended) | `OrderLineAllocation`, `PurchasedSpare` (new, ADR-035); `PurchaseOrderLine.required_quantity`, `InventoryLot.purchased_spare` (extended) | `/compras/lineas/<id>/asignacion/`, `/compras/asignaciones/`, `/compras/repuestos/` | `tests/test_order_allocation_spares.py` (20 tests) | Done — PO-approval block is a visible warning, not a hard gate (A40, no existing PO-approval transition to attach to) |
| Field issue reporting and corrective-action tracking | `apps.fieldissues` (new app) | `FieldIssue`, `FieldIssueEvidence`, `IssueCategory` (ADR-036) | `/incidencias/` report/list/detail, filtered dashboard views | `tests/test_field_issues.py` (20 tests) | Done — full lifecycle including reject/resubmit/reinspection live-validated end to end |

## Explicitly out of scope for this delivery (see `KNOWN_LIMITATIONS.md`)

Local OCR execution, automated translation, CONFOTUR government submission
(never in scope per spec), and WhatsApp/QuickBooks live integration
remain out of scope for this delivery (see below). All four Priority 1
features from `docs/implementation-roadmap.md` are now complete.
