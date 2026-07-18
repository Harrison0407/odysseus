# Business Requirements — Derived From Source Interviews

This document converts every material challenge raised in the Manuel and
Markeris interviews (`Business_context/`) into enforceable product
behavior. It complements, not duplicates, `SOURCE_DOCUMENT_ANALYSIS.md`.
Full traceability to feature/module/test is in `REQUIREMENTS_TRACEABILITY.md`.

## 1. From Manuel's interview (warehouse/receiving)

| Pain point (verbatim theme) | Enforceable product behavior |
|---|---|
| No central, conditioned warehouse; storage location decided after cargo already arriving | `ReceivingPlan` is a required record before `Shipment.status` can reach `AUTHORIZED_TO_SHIP`; a missing/unsuitable plan is a blocking condition, overridable only with a recorded executive reason (`ReceivingPlan.override_reason`). |
| Buildings used as informal warehouses; material moved repeatedly, damaged | `StorageSite.site_type` includes `project_building`; `Transfer` and `InventoryMovement(TRANSFER)` record every internal movement so repeated moves are visible and countable, not silently absorbed. |
| Sensitive materials exposed to rain/humidity | `LocationSuitability` records `dry`, `covered`, `rain_exposure`, `leak_risk` per location; a receiving/put-away flow assigning sensitive cargo to an unsuitable location is a modeled critical condition (spec 17), enforced at the data layer today, UI warning is a near-term follow-up (see `KNOWN_LIMITATIONS.md`). |
| Materials distributed directly to buildings before verification | Core principle 4.7 (every direct delivery still passes through receiving) — `ReceiptPackage.is_direct_to_project` records this without bypassing `Receipt`/`Inspection`. |
| Manuel lacked full system access / role clarity | `UserRole` + `Department` + `UserProjectAccess` model configurable roles/departments per organization; the seed command creates Manuel with `almacen` + `recepcion` roles, not ad hoc admin access. |
| Emergency/weekly purchases without checking existing stock | `MaterialRequest.is_emergency_exception` flags the exception path explicitly rather than allowing a silent parallel process (spec 23, core principle 21). |
| Door/kitchen component identification depends on personal knowledge | `AssemblyDefinition` / `AssemblyComponent` / `KitDefinition` / `KitInstance` / `Package` model component-level completeness instead of relying on a person's memory. |
| No formal indicators (inventory accuracy, damage, storage duration) | KPI dashboard fields are modeled directly on the ledger (`InventoryMovement`, `QuarantineRecord`, `CycleCountLine`) so weekly indicators are computable, not manually tallied. |

## 2. From Markeris's interview (purchasing/receiving handoff/QuickBooks/CONFOTUR)

| Pain point | Enforceable product behavior |
|---|---|
| "Compras cree que la mercancía llegó correctamente porque factura, packing list y sistema así lo indican" | Core principle 4.3: `PurchaseOrder`, `SupplierInvoice`, `PackingList` never create inventory. Only `receiving.services.post_receipt_line` creates an `InventoryMovement`. Documented and tested in `tests/test_documents.py`/`tests/test_receiving_and_inventory.py`. |
| Quartz slabs vs. fabricated tops confirmed as equivalent by mistake | `Item.physical_form` distinguishes `raw_material` vs `fabricated_piece`; the live fixture imports both as genuinely separate `Item` rows (test: `test_quartz_slab_and_fabricated_top_are_distinct_identities`). |
| Sinks/fregaderos across multiple containers, two sizes, risk of correct total/wrong distribution | `QuantityReconciliation` rolls up required/ordered/shipped/received/installed per item+project without collapsing model/size distinctions (each size is a separate `Item`/`ItemVariant`). |
| Doors arrive as disassembled components, no way to ask "where are the 70 complete doors" | `AssemblyDefinition`/`AssemblyComponent` + `KitInstance.status` (`incomplete`/`complete`) directly answers that question instead of counting loose parts. |
| "Almacén no debe recibir definitivamente sin orden de compra, packing list y desglose técnico" | `Receipt.status` starts `PROVISIONAL`; `services.post_receipt_line` requires an existing `ManifestLine` (sourced from a `ReleasePacket`, itself frozen from a verified `ShipmentManifestVersion`) — there is no path to post inventory without that chain. |
| Reports/discrepancy process "no logró funcionar consistentemente" | `Discrepancy` + `DiscrepancyResolution` + `Waiver` give every discrepancy a permanent, visible record — even after resolution or waiver (core principle 4.4, spec 13). |
| CONFOTUR quotation split across containers/liquidations, risk of duplicate exemption | `ConfoturLine.is_duplicate_of` and `ExemptionAllocation` model duplicate-detection explicitly (Priority 1 — schema complete, UI is a near-term follow-up). |
| QuickBooks is not proof of physical existence | Explicit architectural boundary: this system is the physical/operational source of truth; `docs/QUICKBOOKS_INTEGRATION_DISCOVERY.md` documents the (currently unconfirmed) path to a read-only QuickBooks integration without ever treating QuickBooks as authoritative for physical quantity. |
| Nobody controls the handoff between departments | `apps.workflow.Handoff` requires outgoing submission + incoming explicit acceptance; a status field alone never transfers responsibility (spec 8). |
| Responsibility matrix ambiguity (who approves, who corrects, who reconciles) | `ResponsibilityAssignment` + `StageAssignment` (primary/backup/preparer/reviewer/approver/observer/escalation) implement the RACI-style matrix described in both interviews; concrete matrix in `RESPONSIBILITY_MATRIX.md`. |

## 3. Non-negotiable behaviors reaffirmed by both interviews together

1. A commercial or administrative document (a PO, an invoice, a "RECIBIDO EN FULL" stamp) is never receiving evidence. This is directly demonstrated by the live-container fixture, where all five local POs already carry that stamp pre-arrival (`SOURCE_DOCUMENT_ANALYSIS.md` §3) and the system still requires an actual `Receipt`.
2. Responsibility must follow control, not convenience — Purchasing is not accountable for warehouse custody; Warehouse is not accountable for site consumption; Project is not accountable for purchasing decisions (core principle 4.6).
3. Every quantity discrepancy must remain visible after resolution — nothing in this system "closes" a discrepancy by deleting it.
