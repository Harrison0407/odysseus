# Implementation Roadmap

## Delivered in this pass (Priority 0 vertical slice + live fixture)

1. Phase 0 — read every source document, produced `SOURCE_DOCUMENT_ANALYSIS.md`, `SOURCE_PACKAGE_INDEX.md`, `DATA_QUALITY_AND_UNCERTAINTY.md`.
2. Canonical data model — ~150 models across 18 apps, migrated cleanly from empty.
3. Priority 0 vertical-slice UI — documents, procurement, dual-manifest shipment screen, receiving, inventory, requests, cost, HTML snapshot + share links.
4. Live-container acceptance fixture imported and verified against the actual UI (curl-driven smoke tests) and an automated test suite (21 tests).
5. Production Docker Compose stack (Postgres + Gunicorn + Caddy), validated live: build, migrate, seed, restart/persistence, backup, restore, authorized/unauthorized access, HTML snapshot generation — all exercised against the real stack, with two real bugs found and fixed during that validation (see `architecture-decisions.md` ADR-011, ADR-012).

## Next increment (Priority 0 completion)

- Gate-enforcement UI: a literal blocking message + override flow at the `Shipment.status` transition points (today enforced structurally in the data model and covered by tests, not yet a UI button).
- Handoff inbox: accept/reject screen wired to `apps.workflow.Handoff` (model + tests exist; no dedicated screen yet beyond the dashboard's read-only list).
- Detailed internal receiving manifest print view matching the exact 10-section layout in spec 13A.8 (today the data is all present on the shipment detail page, but not in that specific document layout).
- Dispatch/Delivery UI (`apps.requests`) — models complete, only the material-request creation screen is built so far.
- Landed-cost allocation-run trigger UI (`apps.cost`) — models complete, calculation must currently be run via the ORM/a management command.

## Priority 1 (deferred, tracked in `KNOWN_LIMITATIONS.md`)

CONFOTUR reconciliation UI, tool custody UI, cycle-count UI, storage
capacity/suitability UI, external storage comparison calculator, supplier
claim package generation, QR label printing.

## Explicitly out of scope for any near-term increment

Local OCR execution, automated machine translation, QuickBooks live
integration, WhatsApp integration, government CONFOTUR e-submission (the
spec itself excludes electronic submission — evidence/reconciliation
only).
