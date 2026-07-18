# Source Package Index

Index of every file physically inspected during Phase 0. Full analysis of
content, cross-references, and discrepancies is in `SOURCE_DOCUMENT_ANALYSIS.md`.

## Business context (`Business_context/`)

| File | Language | Nature |
|---|---|---|
| `CONSOLIDADO GENERAL DE LA CONVERSACIÓN CON MANUEL.docx` | Spanish | Interview transcript/analysis, 2026-07-10, warehouse/receiving pain points |
| `CONSOLIDADO GENERAL DE LAS CONVERSACIONES CON MARKERIS.docx` | Spanish | Interview transcript/analysis, three sessions (2026-07-01/03/13), purchasing/CONFOTUR/QuickBooks pain points |

Both explicitly warn that automatic transcription contains errors in names,
figures, and terms, and are histories as of their stated dates, not live
confirmations.

## Live container fixture (`Seed_docs/Live_container/`)

| File | Format | Role |
|---|---|---|
| `Bill of Landing - 2605529 TELEX BL 181AY26S4210706J1.pdf` | PDF | Official BL, MEDUWY575021 |
| `Shipping Cost - 2605529 深圳永亨usd.pdf` | PDF | Freight invoice, Guangzhou Eternalship, USD 6,900 |
| `SHipping Payment - Banco Popular Dominicano _YF --> MACTH Shipping Cost.pdf` | PDF | Payment evidence for the freight invoice |
| `(BOMA - Kitchens, Vanities, Closets PALMERA) PI-W5082 20250827_UPDATED_2026.04.20.pdf` | PDF | Supplier PI, Boman, Palmera |
| `[BOMA - WINDOWS SOL-26 & ARENA 9-12] PI-W5076 - update - 20251212.pdf` | PDF | Supplier PI, Boman, windows |
| `BOMAN Countertop Slabs - PIW5097 2026.03.18.pdf` | PDF | Supplier PI, Boman, quartz slabs |
| `DTBEACH SOLE-26 Kitchen Closets & Vanities LC Quotation 20260205 UPDATE - W5084.pdf` | PDF | Supplier PI, Boman, W5084 (header says project ARENA) |
| `YEKALON_Updated PI-60044678.pdf` | PDF, 9 pages | Supplier PI, Yekalon, interior doors + WPC frame, full legal terms |
| `DOC071726-...--> MATCH W5076.pdf` | PDF, scanned/stamped | Local PO DT-BEACH786 |
| `DOC071726-...--> MATCH W5084.pdf` | PDF, scanned/stamped | Local PO DT-BEACH804 |
| `DOC071726-...--> MATCH W5082.pdf` | PDF, scanned/stamped | Local PO DT-BEACH793 |
| `DOC071726-...--> MATCH YEKALON_Updated PI-60044678.pdf` | PDF, scanned/stamped | Local PO DT-BEACH1111 |
| `DOC071726-...--> MATCH PI5097.pdf` | PDF, scanned/stamped | Local PO 1260 |
| `CI + PL - YEKALON_Updated PI-60044678.xlsx` | XLSX (3 sheets) | Supplier CI/PL, Yekalon |
| `CI+PL_MEDUWY575021.xlsx` | XLSX (2 sheets) | **Internal consolidated CI/PL for the whole container**, named after the BL number |
| `PL - Perfileria Paños WA10 - 2026.6.12CI+PL-铝合金门窗.xlsx` | XLSX (2 sheets), Chinese | Supplier CI/PL, aluminum railing glass — declares "cntr qty: 3", not obviously part of this 1-container BL (see Assumption A10) |
| `PL - Perfileria Paños WA10 - WA10护栏玻璃尺寸.xlsx` | XLSX, Chinese | Technical glass-cutting list, Arena #9–12 balcony railings |
| `PL - W5084 - last 10 kitchen and others vinaties on this container.xlsx` | XLSX (3 sheets), mixed ZH/EN | Box-level packing list, Sole-26 kitchens/vanities |
| `Replacement & Slabs of Quartz.xlsx` | XLSX (23 MB, mostly embedded photos) | Box-level packing list, fabricated countertops + quartz slabs — physically lists 19 slab pieces vs. 20 ordered |

All files were opened and read (PDF via native text extraction, XLSX via
`openpyxl`) — none of the facts in `SOURCE_DOCUMENT_ANALYSIS.md` were
inferred from filenames alone, per the mandatory rule in
`00_READ_ME_FIRST_LIVE_CONTAINER_CASE.md` §1.
