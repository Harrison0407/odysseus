# Official vs. Operational Logistics — Architectural Analysis

This is the single mandatory architectural separation in the governing
spec (section 13A): **Official Logistics** (the carrier/customs
documentary record) and **Operational Logistics** (everything the
business knows is physically loaded or expected) are modeled as two
distinct, explicitly linked views. Neither is ever derived by mutating
the other. This document explains how and why, grounded in the live
fixture.

## 1. Why they cannot be merged

The official BL for the live fixture states, verbatim:

```
QUARTZ STONE COUNTERTOP
WOODEN DOORS
KITCHEN CABINET
```

3 lines, 520 packages, 22,500 kg, 40 CBM. That is legally correct and
exactly what customs, the carrier, and any legal audit must see —
unchanged, forever.

The physical reality inside that one container, per the internal CI/PL
and detailed packing lists, is at least 11 distinct commercial/physical
lines: two different fabricated countertop shapes, two explicitly-labeled
*replacement* countertop pieces, 20 quartz slabs (see the packed-quantity
discrepancy in `DATA_QUALITY_AND_UNCERTAINTY.md`), 70 sets of disassembled
replacement doors for buildings 17–22, two Sole-26 kitchen apartment
kits, two vanity kits, and one unattributed 200-unit accessories line.

If the system tried to force the BL's own row to "become" 11 rows, the
legal document would no longer match what was filed with customs — a
serious compliance failure per core principle 4.9 ("never facilitate
concealment, misdeclaration, ... or removal of cargo from the official
record"). If the system instead discarded the internal detail to keep
the BL "clean," Manuel's receiving team would have no way to know what
70 door-components or 2 kitchen kits to actually expect. Both failure
modes are real, and both are why the two views must coexist as separate,
versioned, cross-referenced records rather than one shared table.

## 2. How the schema enforces the separation

- `BillOfLading` stores the official fields directly (`official_cargo_description`, `declared_packages`, `declared_gross_weight_kg`, `declared_cbm`) and is only ever created from an uploaded official document (`source_document`, required, `on_delete=PROTECT`).
- `ShipmentManifest.purpose` is an enum (`ManifestPurpose`) with `OFFICIAL_CARRIER_SUMMARY` as one value among several — its lines are generated read-only from the BL and are never hand-edited to add detail.
- `INTERNAL_OPERATIONAL_MANIFEST` is a *separate* `ShipmentManifest` row with its own `ShipmentManifestVersion` history, `ManifestLine` rows, and `ManifestLineSource` provenance back to the actual PO/invoice/packing-list lines that justified each internal line.
- `ManifestVariance` is the explicit bridge: one row per (official category, internal line) pair, carrying `is_explained`, `severity`, and `customs_review_required`. A difference is not automatically an error — it must be classified. An *unexplained* critical variance is what actually blocks the "Operationally Verified" gate (spec 9.5).

## 3. What the live fixture proves this actually does

Running `import_live_container_fixture` and then viewing the shipment
detail page shows, side by side:

- **Left panel ("Resumen Oficial"):** the exact 3-category BL text, 520/22,500 kg/40 CBM, never edited.
- **Right panel ("Manifiesto Operativo Interno"):** all 11 decomposed lines, each tagged with its own `declaration_status` badge — "Representado bajo categoría más amplia" for lines that map cleanly under a BL category, "Reemplazo" for the two replacement countertop lines and the replacement doors, and "Revisión aduanal" (in red) for the 3 lines that could not be cleanly justified: the "TYPE" fabricated-top line (unknown PI reference "W5085"), the quartz slab line (packed-quantity mismatch), and the unattributed 200-unit accessories line.
- **Totals reconciliation panel:** official 520 pkg / 22,500 kg / 40 CBM vs. internally computed totals from the 11 lines — allowing a human to see at a glance whether the decomposition actually adds up to the legal total (it does, in this fixture, modulo the already-flagged slab-count discrepancy).
- **Variance matrix:** the full `ManifestVariance` table, with severity and customs-review-status columns, is the operational equivalent of Manuel's "official-versus-operational warnings requiring attention" (spec 13A.8, item 10).

## 4. What is intentionally NOT done

- The system does not attempt to auto-resolve the 3 flagged lines. A `CustomsReviewDecision` exists in `PENDING` state for each; only a human with the Customs & Logistics role changes that.
- The system does not let a user edit `BillOfLading.official_cargo_description` from the internal-manifest screen — there is no UI path to do so, and the field is only ever set from `source_document`.
- Freezing (`ShipmentManifestVersion.is_frozen`) is implemented at the model level; the UI action to freeze-and-version on loading confirmation (spec 13A.9) is modeled but not yet exposed as a button — tracked in `KNOWN_LIMITATIONS.md`.
