# Source Package Analysis — Live Container Fixture (MEDUWY575021 / TCNU8926924)

Status: living document. Last updated during Phase 0 (2026-07-18).

This document is the result of physically inspecting every file supplied in
`Seed_docs/Live_container/` and the two business-context transcripts in
`Business_context/`. Per the governing rule "do not trust filenames as final
truth," every figure below was read from document content, not inferred from
a filename. Where two source documents disagree, both values are recorded —
neither is silently corrected.

## 1. Document inventory

| # | File | Type | Key identifiers |
|---|------|------|------------------|
| 1 | `Bill of Landing - 2605529 TELEX BL 181AY26S4210706J1.pdf` | Official Bill of Lading (MSC) | BL MEDUWY575021, booking 181AY26S4210706J1, container TCNU8926924 |
| 2 | `Shipping Cost - 2605529 深圳永亨usd.pdf` | Freight invoice (Guangzhou Eternalship) | Job GZYF2605529, USD 6,900.00 ocean freight |
| 3 | `SHipping Payment - Banco Popular Dominicano...pdf` | Payment evidence | Banco Popular, US$6,900.00, ref 51668696, 2026-07-13/14 |
| 4 | `(BOMA - Kitchens, Vanities, Closets PALMERA) PI-W5082...pdf` | Supplier PI (Boman) | Order W5082, project PALMERA, total US$162,401.11 |
| 5 | `[BOMA - WINDOWS SOL-26 & ARENA 9-12] PI-W5076...pdf` | Supplier PI (Boman) | Order W5076, total US$293,561.67 |
| 6 | `BOMAN Countertop Slabs - PIW5097 2026.03.18.pdf` | Supplier PI (Boman) | PI W5097, 20 quartz slabs, US$6,830.08 |
| 7 | `DTBEACH SOLE-26 Kitchen Closets & Vanities LC Quotation...W5084.pdf` | Supplier PI (Boman) | Order W5084 — **PI header says "PROJECT NAME: ARENA"**, filename says "SOLE-26"; total US$51,559.74 |
| 8 | `YEKALON_Updated PI-60044678.pdf` | Supplier PI (Yekalon), 9-page legal PI | PI 60044678, interior doors + WPC frame, C&I total US$14,108.07 |
| 9–13 | `DOC071726-...--> MATCH *.pdf` | Local Markeris purchase orders (The Beach / DT Beach S.R.L.) | See §3 matching table |
| 14 | `CI + PL - YEKALON_Updated PI-60044678.xlsx` | Supplier CI + PL (Yekalon) | CI 60044678-1, 278 packages, 6,415.6 kg, 14.94 CBM |
| 15 | `CI+PL_MEDUWY575021.xlsx` | **Internal consolidated CI/PL for the whole container** | Named after the BL number itself; 520 pkgs / 22,500 kg / 40 CBM — reconciles exactly to the official BL |
| 16 | `PL - Perfileria Paños WA10 - 2026.6.12...xlsx` | Supplier CI/PL (aluminum railing glass, Arena #9–12) | Header says **cntr qty: 3** — does not obviously belong to this 1-container BL; flagged as unresolved in §5 |
| 17 | `PL - Perfileria Paños WA10 - WA10护栏玻璃尺寸.xlsx` | Technical glass cutting list, Arena #9–12 balcony railings | 144 glass panes + 8 spares, by building/position |
| 18 | `PL - W5084 - last 10 kitchen and others vinaties on this container.xlsx` | Detailed box-level packing list | "Sole-26 (G/H)" kitchens/vanities — labels apartments inconsistently (see §5) |
| 19 | `Replacement & Slabs of Quartz.xlsx` | Detailed box-level packing list (23 MB, mostly embedded photos) | Fabricated countertop pieces (APT-D/F/C/E, TYPE, Arena Countertop Replacement) + quartz slabs: **19 slabs physically listed vs. 20 ordered** |

## 2. Official cargo view (must be preserved unchanged)

Bill of Lading **MEDUWY575021** (MSC, Telex Release):
- Shipper: Guangzhou Boman Building Materials Co., Ltd. Consignee/Notify: DT BEACH, S.R.L.
- Vessel MSC REGULUS UX623A, Yantian, China → Caucedo, Dominican Republic.
- Container **TCNU8926924**, 40' HIGH CUBE, seal **FJ28053326**.
- Cargo description (verbatim, three broad categories only): **"QUARTZ STONE COUNTERTOP / WOODEN DOORS / KITCHEN CABINET"**.
- 520 packages, 22,500.000 kgs gross, 40.000 cu. m., shipped on board 09-Jun-2026, freight prepaid.
- Ocean freight invoiced separately at USD 6,900.00 (Guangzhou Eternalship, job GZYF2605529) and paid via Banco Popular Dominicano wire ref 51668696, described as "PAGO CONTENEDOR TCNU8926924."

This is the Official Customs and Transport view and must never be edited to reflect internal detail.

## 3. Local PO ↔ China-side PI matching (confirmed by physical inspection)

| Local PO (Markeris/DT Beach) | O.C. No. | Date | Matched China PI | PO total | PI total | Variance | Requested by / use noted on PO |
|---|---|---|---|---|---|---|---|
| DT-BEACH786 | 25/8/2025 | → | W5076 (windows) | USD 293,563.00 | USD 293,561.67 | **+$1.33** (rounding) | María Luisa; "USO: EDIFICIO ARENA 9,10,11,12 / EDIFICIO 26"; ODC 1702 |
| PO 1260 | 24/3/2026 | → | W5097 (quartz slabs) | USD 6,830.08 | USD 6,830.08 | $0.00 | María Luisa; "USO: GENERAL DEL PROYECTO" (project-wide, no building) |
| DT-BEACH804 | 2/9/2025 | → | W5084 (kitchens/vanities) | USD 51,559.76 | USD 51,559.74 | +$0.02 | María Luisa; **"USO: EDIFICIO SOLE 26"**, ODC 2176 — but the PI itself is headed "PROYECT NAME: ARENA" |
| DT-BEACH793 | 27/8/2025 | → | W5082 (kitchens/vanities) | USD 162,401.05 | USD 162,401.11 | −$0.06 | María Luisa; "USO: COCINAS/CLOSET/MUEBLES DE BAÑO / PALMERA", ODC 1703 |
| DT-BEACH1111 | 19/1/2026 | → | Yekalon PI-60044678 (doors) | USD 14,108.13 | USD 14,108.07 | +$0.06 | María Luisa; **"USO: PUERTAS ADICIONALES DEL EDIFICIO 17 AL 22 ÚNICAMENTE PARA REPOSICIÓN"** (replacement only) — CorrectiveActionCase/ReplacementCase, not a normal purchase |

Every one of the five local POs already bears a **"RECEIVED IN FULL"** rubber stamp in the source PDF — applied before the container had even arrived at destination. This is a real, physical instance of the exact failure mode the Markeris interview describes: a commercial/administrative document asserting receipt that has no relationship to physical verification. It is preserved as-is and must **never** be treated as receiving evidence by the system.

## 4. Internal operational manifest (CI+PL_MEDUWY575021.xlsx)

This spreadsheet is named after the BL number itself and is the "internal detailed CI/PL" the 00_READ_ME document describes. Its PL sheet totals **520 packages / 20,650 kg net / 22,500 kg gross / 40 CBM** — an exact reconciliation with the official BL. Its line items decompose the three broad BL categories as follows:

- **QUARTZ STONE COUNTERTOP** (BL category) decomposes into 7 internal lines:
  - `W5084` fabricated tops APT-D/F/C/E (qty 5) and `W5085` "TYPE" (qty 5) — **fabricated pieces, not slabs**, referencing PI numbers (W5084/W5085) not covered by any PI document in this package → unlinked source, requires Customs & Logistics review.
  - `W5086` and `W5087`, both explicitly labeled **"Arena Countertop Replacement C/F(K2-2) G(K1-2) B/E(K2-1)"** (qty 5 each) — replacement/corrective cargo, must stay linked to a defect case, not counted as new purchase.
  - `W5057` **QUARTZ SLABS** 3200×1600×20mm, qty 10 + qty 10 (two lines, 20 total) at unit price USD 66.7/m² — this is almost certainly the same commercial reference as **PI W5097** (unit price and 5.12 m² size match exactly); the reference in this sheet reads "W5057" not "W5097." This is a plausible transcription/typo, **not** confirmed — treated as `Possible Candidate`, not auto-merged.
- **WOODEN DOORS** (BL category) decomposes into: `DOC05226` MDF Door + WPC Frame (qty 70, unit US$79.86) + hinges (105 @ US$6) + accessories (100 sets @ US$29) — reconciles exactly to Yekalon PI-60044678 line items and quantities.
- **KITCHEN CABINET** (BL category) decomposes into: `W5084` "SOLE-26 APT-A" kitchens (qty 5), "SOLE-26 APT-B" kitchens (qty 5), "APT-GH-V1" vanities (qty 15), "APT-GH-V2" vanities (qty 15), plus an unattributed **"GENERAL" ACCESSORIES** line (qty 200, no PI/REF reference at all) — this last line has no documentary source and must be flagged for Customs & Logistics review per spec §13A.1.

**Open-commitment / carryover confirmation:** PI W5084 (Boman, kitchens/vanities/wardrobes/sinks for "ARENA") totals 40 kitchen sets and 70 vanity sets across its full scope, but this container only carries 10 kitchen sets (APT-A + APT-B) and 30 vanity sets (GH-V1 + GH-V2). The remaining balance of W5084 is open and must carry forward to a future shipment — this is the live acceptance fixture for §13A.4 (open PI carryover).

**Quantity discrepancy confirmed in packing detail:** The detailed box-level packing list (`Replacement & Slabs of Quartz.xlsx`) itemizes quartz slabs as **10 + 9 = 19 physical pieces**, while the commercial PI (W5097) and the local PO (1260) both say **20 slabs**. This is a genuine packed-vs-ordered quantity conflict inside the supplied fixture — a live instance of spec §13, "packed below or above ordered quantity" — and must not be silently reconciled.

## 5. Unresolved ambiguities requiring human confirmation (do not auto-resolve)

1. **W5057 vs W5097** — likely the same commercial reference (matching unit price/size) but written differently in two source documents. Flag as `Possible Candidate` match; require human confirmation.
2. **W5084 project/building naming conflict** — the PI header says "PROYECT NAME: ARENA"; the matched local PO says "USO: EDIFICIO SOLE 26"; the internal CI/PL calls the same lines "SOLE-26 APT-A/APT-B"; the detailed packing list (`PL - W5084 - last 10 kitchen...xlsx`) labels its own summary sheet "Sole-26 (G/H)" while its per-box detail sheets say "Sole-26 / APT-A" and "Sole-26 / APT-B" — three different labels for what appear to be the same two apartments, within source documents that do not agree even with each other. Preserve all forms as source values; require human-confirmed canonical apartment identity.
3. **`GENERAL` accessories line (qty 200, no PO/PI reference)** in the internal CI/PL — no documentary source found anywhere in the package. Must render as "not clearly represented — Customs & Logistics review required," never silently dropped or silently linked.
4. **`PL - Perfileria Paños WA10 - 2026.6.12...xlsx`** declares "cntr qty: 3," inconsistent with the single-container BL (TCNU8926924) that anchors this fixture. Its glass cutting list (Arena #9–12 balcony railing glass, 144 panes + 8 spares) may belong to a different, later shipment entirely. Treated as **not part of this container's manifest** unless a human confirms otherwise — excluded from the acceptance-fixture import by default and recorded as an open question, not silently merged into this container's cargo.
5. Freight invoice and BL both reference the same container/booking, so the US$6,900 ocean-freight charge allocates cleanly to this single container — no ambiguity there.

## 6. Business-context corroboration

The live fixture is not a hypothetical: it reproduces, in real documents, the exact failure patterns both Manuel and Markeris described in their July 2026 interviews (see `Business_context/`):
- Quartz "slabs" vs. fabricated "tops" being described ambiguously and at risk of being confirmed as equivalent (§3 of the Markeris consolidation; the W5057/W5097 slabs and the W5084/85/86/87 fabricated-tops lines in this same container are the concrete instance).
- Doors arriving as disassembled components (leaf/frame/casing/hinges/PVC strip) rather than as commercial "door" units (§5 of Markeris consolidation; Yekalon PI/CI in this container).
- Kitchens arriving as multi-box, apartment-specific kits that cannot be judged complete by box count alone (§6, §13 of Markeris/Manuel; W5084 kitchen box lists in this container).
- Replacement/corrective cargo riding in a later, unrelated container (§4 of the README; the Yekalon "REPOSICIÓN" doors for buildings 17–22 and the Boman "Arena Countertop Replacement" lines in this exact container).
- Commercial/administrative documents (the "RECEIVED IN FULL" stamped local POs) asserting a state that has not been physically verified.

This corroboration is why the dual-manifest, replacement-case, and open-commitment models in the canonical data model (§12–13A of the locked prompt) are treated as Priority 0, non-negotiable, rather than nice-to-have generalizations.
