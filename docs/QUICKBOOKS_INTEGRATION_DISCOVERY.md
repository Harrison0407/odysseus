# QuickBooks Integration Discovery

Per spec section 31, this document lists what remains genuinely unknown
before any QuickBooks integration could be designed — nothing here should
be read as "planned and scheduled," only as "required before scoping is
possible." No QuickBooks write-back is implemented or planned; per spec,
QuickBooks integration, if ever built, is read-only.

## What the Markeris interview (2026-07-13 session) established

- The company uses **QuickBooks Enterprise**.
- It is hosted on a rented virtual server, described in the transcript as
  provider "DTBG or similar" — **name not confirmed**.
- Warehouse (Óscar, under Manuel) can enter and view inventory-linked data;
  Compras can create/review orders; Finance/Accounting have their own
  module access.
- Costs can be allocated by building; project-level (e.g. Arena, Palmera)
  analysis is possible; apartment-level granularity was *suggested* but
  must be confirmed directly with Contabilidad.
- The interview explicitly frames the target architecture as: this
  operational system → integration → QuickBooks as the financial/
  accounting system of record. QuickBooks is never intended to be the
  source of truth for physical inventory (core principle 4.1 / 4.5 of
  the governing spec, directly motivated by the "20 slabs vs. 20
  fabricated tops" incident described in the same interview).

## Still required before any integration can be scoped

1. Exact QuickBooks Enterprise version and hosting model (confirm the
   hosting provider name and whether it is a hosted-desktop or QBO-style
   deployment).
2. Who administers the QuickBooks instance today (name/role).
3. Available integration method: QuickBooks SDK (qbXML/QBFC for desktop
   editions), an ODBC driver, or a QuickBooks Online-style REST API —
   these are mutually exclusive and imply very different integration
   architectures.
4. Whether a test/sandbox QuickBooks environment exists for integration
   development, or whether all integration work must be validated
   against production data (materially raises risk).
5. Field-level mapping: which QuickBooks fields (item, class/building,
   customer/project) correspond to this system's `Item`, `Building`,
   `Project` records — not yet mapped in either direction.
6. Explicit, written agreement on source-of-truth ownership per field:
   this system should own physical quantity/location/condition
   (`InventoryLot`, `InventoryMovement`); QuickBooks should own
   costs/accounts-payable/financial statements. Where the two must agree
   (e.g. landed cost), which system is authoritative needs a named
   decision-maker, not an assumption.
7. Backup and security practices for the QuickBooks host, since any
   integration credential would need to respect those constraints.

## Recommended integration shape once the above is answered

A one-way, read-only export from this system → QuickBooks (e.g. posting
finalized `LandedCostVersion` totals and confirmed `InventoryMovement`
consumption as journal-adjacent data), triggered manually or on a
schedule, with every export logged as an `AuditEvent`. No component of
this delivery assumes or depends on that integration existing.
