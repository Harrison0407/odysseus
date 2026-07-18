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
| Users, roles, assignments, handoffs | `apps.accounts`, `apps.workflow` | `UserRole`, `ResponsibilityAssignment`, `Handoff` | Login, persona dashboards | `test_permissions.py` | Done |
| Multilingual document upload + provenance | `apps.documents` | `Document`, `DocumentVersion`, `DocumentFieldSource` | `/documentos/` upload/list/detail | `test_documents.py` (3 tests) | Done (upload/hash/duplicate/download-auth); OCR/translation adapters modeled but not wired to a real engine — **Modeled** |
| PO / invoice / packing-list / BL / container matching | `apps.matching`, `apps.shipments` | `MatchRun`, `MatchCandidate`, `ManifestLineSource` | Shipment detail (variance matrix) | `test_live_container_fixture.py` | Done for the live fixture case; automatic fuzzy-matching engine (scoring heuristics beyond the fixture) is **Modeled**, not yet generalized |
| Payment and origin readiness | `apps.procurement` | `PaymentMilestone`, `PaymentRecord` | PO detail page | manual verification | Modeled |
| Pre-arrival storage and receiving plan | `apps.receiving` | `ReceivingPlan`, `StorageCapacityReservation`, `InspectionPlan` | none yet (data model only) | none yet | Modeled |
| Physical receiving and risk-based inspection | `apps.receiving` | `Receipt`, `ReceiptLine`, `Inspection`, `SamplingRule` | `/recepcion/` list/detail/post-line | `test_receiving_and_inventory.py` (4 tests) | Done |
| Quarantine, damage, shortage, overage, claims | `apps.receiving`, `apps.matching` | `QuarantineRecord`, `DamageRecord`, `Discrepancy` | receiving detail page | `test_damaged_quantity_is_quarantined...` | Done for damage/shortage/overage; `Claim`/`ClaimEvidence`/`ClaimDeadline` are **Planned** (Priority 1) |
| Location-based ledger inventory | `apps.inventory` | `InventoryLot`, `InventoryMovement` | `/almacen/ubicaciones/` | `test_onhand_quantity_is_derived_from_ledger...` | Done |
| Material requests, dispatch, project acceptance, returns | `apps.requests` | `MaterialRequest`, `Dispatch`, `Delivery`, `ProjectReceipt` | `/solicitudes/` list/create/detail | manual verification | Modeled; dispatch/delivery UI is **Planned** |
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

## Explicitly out of scope for this delivery (see `KNOWN_LIMITATIONS.md`)

Local OCR execution, automated translation, CONFOTUR government submission
(never in scope per spec), WhatsApp/QuickBooks live integration, and the
full Priority 1 breadth (tool custody UI, cycle-count UI, external storage
comparison UI) are modeled at the schema level but do not yet have a
finished UI or dedicated test. This is recorded here rather than silently
omitted.
