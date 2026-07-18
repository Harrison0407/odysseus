# Implementation Roadmap

## Delivered in this pass (Priority 0 vertical slice + live fixture)

1. Phase 0 — read every source document, produced `SOURCE_DOCUMENT_ANALYSIS.md`, `SOURCE_PACKAGE_INDEX.md`, `DATA_QUALITY_AND_UNCERTAINTY.md`.
2. Canonical data model — ~150 models across 18 apps, migrated cleanly from empty.
3. Priority 0 vertical-slice UI — documents, procurement, dual-manifest shipment screen, receiving, inventory, requests, cost, HTML snapshot + share links.
4. Live-container acceptance fixture imported and verified against the actual UI (curl-driven smoke tests) and an automated test suite (21 tests).
5. Production Docker Compose stack (Postgres + Gunicorn + Caddy), validated live: build, migrate, seed, restart/persistence, backup, restore, authorized/unauthorized access, HTML snapshot generation — all exercised against the real stack, with two real bugs found and fixed during that validation (see `architecture-decisions.md` ADR-011, ADR-012).

## Delivered in this pass (Gate Controls and Formal Handoffs)

6. Reusable gate-evaluation engine (`apps.workflow.gates`) covering all
   8 required minimum transitions (Purchasing→Finance→Logistics→
   Receiving→Warehouse→Project→Installation→Inspection→Acceptance), each
   backed by a real evaluator reading existing Priority 0 models
   (`PaymentMilestone`, `ManifestVariance`/`CustomsReviewDecision`,
   `QuarantineRecord`, `Discrepancy`, `MaterialRequestLine`, `Delivery`,
   `InstallationRecord`/`InspectionRecord`) — no gate logic duplicated
   into a view or template.
7. Full handoff lifecycle service layer (`apps.workflow.services`):
   create (idempotent), submit (with missing-evidence and authorized-
   override handling), accept (with ownership transfer and, for
   `Shipment`-anchored gates, automatic status advancement), reject,
   return for correction, and corrected resubmission (creates a new
   superseding version, never edits the rejected one) — all transactional
   with row-level locking so duplicate/concurrent requests are safe by
   construction, not by convention.
8. Role-aware, filterable handoff inbox (`/flujo/`) plus a detail screen
   showing live gate readiness, evidence, comments, and full decision
   history; wired into the Shipment and Material Request detail pages;
   dashboard cards now link into filtered inbox views instead of showing
   decorative counts.
9. `UserProjectAccess` (modeled but unused since Priority 0) is now
   actually enforced for cross-project isolation; `ResponsibilityAssignment`
   (same situation) now actually records ownership transfer.
10. 32 new automated tests (16 gate-evaluator, 16 handoff-lifecycle/
    security), all passing alongside the pre-existing 21 (53 total). The
    full lifecycle — including a genuinely blocked gate, an authorized
    override, and an acceptance that flips `Shipment.status` — was also
    driven through real HTTP requests against a running server using the
    actual imported live-container fixture, not only unit tests.

## Next increment (Priority 0 completion)

- Detailed internal receiving manifest print view matching the exact 10-section layout in spec 13A.8 (today the data is all present on the shipment detail page, but not in that specific document layout).
- Dispatch/Delivery UI (`apps.requests`) — models and the `project_delivery_to_installation`/`installation_to_inspection`/`inspection_to_acceptance` gates are complete at the engine level; only the material-request creation screen exists as a dedicated UI, so those 3 gates have no "create handoff" button yet (the generic accept/reject/return inbox flow works for them regardless).
- Landed-cost allocation-run trigger UI (`apps.cost`) — models complete, calculation must currently be run via the ORM/a management command.
- A literal "cannot advance" UI message tied directly to gate-blocked transitions outside the handoff detail page itself (today the handoff detail page IS that message; a shipment/request detail page doesn't yet independently repeat it).

## Priority 1 (deferred, tracked in `KNOWN_LIMITATIONS.md`)

CONFOTUR reconciliation UI, tool custody UI, cycle-count UI, storage
capacity/suitability UI, external storage comparison calculator, supplier
claim package generation, QR label printing.

## Explicitly out of scope for any near-term increment

Local OCR execution, automated machine translation, QuickBooks live
integration, WhatsApp integration, government CONFOTUR e-submission (the
spec itself excludes electronic submission — evidence/reconciliation
only).
