# Data Quality and Uncertainty

Concrete, unresolved ambiguities found in the supplied source package, and
how the system represents each one. Per core principle 4.8 ("unknown is
valid data"), none of these were resolved by guessing — each is stored as
an explicit, human-reviewable state.

## 1. W5057 vs. W5097 (quartz slabs)

- **Where it appears:** `CI+PL_MEDUWY575021.xlsx` (internal CI/PL) references the quartz slab line as "W5057"; the supplier PI document is titled "PIW5097" and the matched local PO (1260) also says "W5097" / PI-linked.
- **Evidence for same item:** identical unit price (USD 66.7), identical size (5.12 m², 3200×1600×20mm), identical quantity (20).
- **System representation:** two `ItemAlias` rows on one `Item`, connected by a `MatchCandidate` with `status=POSSIBLE_CANDIDATE` (never `MANUALLY_CONFIRMED`). A human must confirm the match before it can be treated as certain.

## 2. Sole-26 apartment-labeling conflict

- PI W5084 header: `PROYECT NAME: ARENA`.
- Matched local PO DT-BEACH804: `USO: EDIFICIO SOLE 26`.
- Internal CI/PL (`CI+PL_MEDUWY575021.xlsx`): line references read "SOLE-26 APT-A" / "SOLE-26 APT-B".
- Detail packing list (`PL - W5084 - last 10 kitchen...xlsx`): its own **summary** sheet says "Sole-26 (G/H)" while its own **detail** sheets say "Sole-26 / APT-A" and "Sole-26 / APT-B" — an internal inconsistency within a single source file.
- **System representation:** modeled as Building "Sole-26" under Project "Arena" (the choice with the most corroborating references), but every quotation record still preserves the original `project_label_on_document` string verbatim, and every PO preserves its own `usage_note_on_document` string verbatim. Nothing was silently normalized away.

## 3. Unattributed "GENERAL" accessories line

- 200 units, USD 4,000 commercial value, packed as 31 packages — appears only in the internal CI/PL with no PO/PI/quotation reference at all ("REF" column blank).
- **System representation:** `ManifestLine.declaration_status = NOT_REPRESENTED_REVIEW_REQUIRED`, with a `ManifestVariance(customs_review_required=True)` and an open `CustomsReviewDecision(decision=PENDING)`. This is exactly the case core principle 4.9 and spec 13A.1 require to be visibly flagged rather than silently accepted or silently dropped.

## 4. Packed-vs-ordered quartz quantity mismatch

- PO 1260 / PI W5097 order 20 slabs.
- The detailed box-level packing list (`Replacement & Slabs of Quartz.xlsx`) itemizes only **19** physical pieces (10 + 9, two separate box groups).
- **System representation:** both quantities are preserved (`ManifestLine.quantity = 20` commercial vs. the packing detail's 19 physical count noted in the line description); a `Discrepancy(discrepancy_type=PACKED_QUANTITY_MISMATCH, severity=CRITICAL)` is created and left unresolved pending human review — it is not averaged, rounded, or silently reconciled to either number.

## 5. Possibly out-of-scope shipment reference

- `PL - Perfileria Paños WA10 - 2026.6.12CI+PL-铝合金门窗.xlsx` declares "cntr qty: 3", inconsistent with the single-container BL (TCNU8926924, MEDUWY575021) that anchors this fixture.
- **System representation:** excluded from the imported manifest for this container by default (`ASSUMPTIONS.md` A10); the document itself remains indexed in `SOURCE_PACKAGE_INDEX.md` so it is not lost, only not asserted to belong here without confirmation.

## 6. Documents asserting facts not yet verified

- All five local purchase orders in the fixture carry a **"RECEIVED IN FULL"** rubber stamp applied to the PDF before the shipment had even arrived at destination.
- **System representation:** `PurchaseOrder.document_marked_received_in_full = True` records this as a fact about the *document*, and the UI displays an explicit warning badge. It is architecturally impossible for this flag to create a `Receipt` or an `InventoryMovement` — those only come from `apps.receiving.services.post_receipt_line`, which requires a real posting action by a receiving user. Verified by `test_local_pos_received_in_full_stamp_is_not_treated_as_receipt`.

## 7. Historical/incomplete data in general

Business-context interviews are explicitly dated snapshots (Manuel:
2026-07-10; Markeris: 2026-07-01/03/13) and state their own transcription
may contain errors. Numeric examples inside those interviews (e.g. sink
counts by project: Arena 272, Palmera 104, Sole 40) are **not** imported
as system data — they are narrative context requiring independent
verification against actual commercial documents before being trusted,
per the interview documents' own caveats. See `BUSINESS_REQUIREMENTS.md`
for how the *behavioral* lessons from these interviews were used instead.
