# MarketMatch Integration Path

This application is built as a fully standalone product for the current
delivery (per the governing prompt's explicit instruction: "This
application must be designed so it can later be migrated into or
integrated with MarketMatch without losing operational history"). No
MarketMatch code was read, imported, or modified in this delivery — this
document only describes how a future migration could proceed, based on
how this system's own data model is shaped.

## What makes migration tractable by design

- **UUID primary keys everywhere** (`apps.core.models.BaseModel`) — no
  sequential-ID collision risk when merging into a multi-tenant system.
- **`Organization` is already a first-class model**, not assumed to be
  singular — every domain model hangs off `Organization` (directly or
  via `Project`), so MarketMatch could add this system's data as
  "one more organization" without a schema redesign.
- **Full audit trail** (`apps.audit.AuditEvent`) and **document
  provenance** (`DocumentFieldSource`, SHA-256 per version) mean
  operational history migrates as data, not as tribal knowledge.

## Suggested migration order (by dependency)

1. `Organization`, `Department`, `Role`, `UserProfile`/`UserRole` — identity first.
2. `Project`, `Building`, `Floor`, `Unit`, `Area` — location hierarchy.
3. `Document`, `DocumentVersion` (with SHA-256 intact — do not
   recompute, carry the original hash) — evidence layer.
4. `Supplier`, `Quotation`, `PurchaseOrder` and their lines — commercial layer.
5. `Item` and its aliases/assemblies/kits — product identity.
6. `Shipment`, `Container`, `BillOfLading`, the manifest tables, and
   `ManifestVariance`/`Discrepancy`/`Waiver` — logistics and exceptions.
7. `InventoryLot`, `InventoryMovement` — the ledger, in strict
   chronological order, to preserve the derivable on-hand calculation.
8. `MaterialRequest` → `Delivery` → `InstallationRecord` →
   `AcceptanceRecord` chain — project-side history.
9. `Handoff`/`AuditEvent`/`ReportVersion`/`SecureShareLink` — governance
   and reporting history, last, since everything else must exist first
   for their content-type/object-id pointers to resolve.

## What must NOT happen during migration

- Recomputing or discarding SHA-256 hashes on migrated documents (breaks
  the immutability guarantee retroactively).
- Renumbering any UUID (breaks every generic content-type pointer and
  every `SecureShareLink`/QR label already issued).
- Collapsing this organization's `Discrepancy`/`Waiver` history — a
  waived discrepancy must remain visible after migration exactly as it
  did before (core principle 4.4).

## Explicitly not attempted in this delivery

No MarketMatch schema was inspected, and no adapter code targeting a
specific MarketMatch API/database shape was written, since neither was
available to read from within the `Application/` sandbox this delivery
was scoped to. This document is the honest limit of what could be
determined without that access.
