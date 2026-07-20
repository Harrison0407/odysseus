# DT Beach Supply Control

A standalone operational control system for DT Beach connecting
purchasing → China origin → shipping/customs → landed cost → warehouse
receiving → inventory → project delivery → installation → acceptance,
built around one non-negotiable architectural rule: **official
carrier/customs documentation and the internal operational cargo truth
are modeled as two separate, explicitly reconciled views — never
merged, never silently edited into agreement.**

This is a staged delivery of a large governing specification. The implemented
baseline now includes the original Priority 0 vertical slice and subsequent
milestones through **Controlled Transparency, Commercial Confidentiality &
Authorization Foundation**. The accepted foundation-remediation findings have
been corrected locally with adversarial package, evidence, document, API, and
confidentiality coverage; 511 tests pass with clean migrations (472 from the
initial remediation, 24 from foundation correction cycle 2 — which closed two
independently-discovered gaps, CTCF-AUDIT-017 and CTCF-CR-PROJ-018 — and 15
from foundation correction cycle 3, which closed three further gaps found by
independently revalidating cycle 2's own privileged-audit mechanism:
CTCF-AUDIT-SCOPE-021, CTCF-AUDIT-RETRIEVAL-022, CTCF-AUDIT-WINDOW-023).
This correction cycle was independently revalidated and, on 2026-07-20,
explicitly owner-accepted by Harrison — the foundation is now closed. The
independent Milestone 1 Charter review is the next required control
point. What is not yet built is listed honestly in
`docs/KNOWN_LIMITATIONS.md`; the official milestone order is governed by
`docs/MARKETMATCH_ARCHITECTURE_RECONCILIATION_AND_ROADMAP.md`.

## What's implemented

- Full canonical data model across 27 Django app directories, clean from an empty database (PostgreSQL and SQLite both verified).
- Multilingual document upload with SHA-256 provenance and duplicate detection.
- The dual-manifest engine (Official Carrier Summary vs. Internal Operational Manifest) with an official-vs-operational variance matrix, demonstrated against the real supplied live-container fixture (BL MEDUWY575021 / container TCNU8926924).
- Ledger-based inventory (on-hand quantity always derived from posted movements, never edited directly).
- Physical receiving with risk exceptions, automatic quarantine of damaged stock, and discrepancy creation.
- Storage capacity/suitability warnings at put-away and transfer time, and an external-storage comparison calculator with immutable, versioned scenarios (never fabricates a currency conversion).
- Material requests, purchase orders with open-commitment carryover tracking, replacement/corrective-cargo case tracking.
- Supplier claim package generation: full claim lifecycle traceable to supplier/PO/shipment/discrepancy/inspection records, with a printable/downloadable claim package.
- QR label generation and controlled scanning for inventory lots, locations, receipts, dispatches, deliveries, installation records, tools, and containers — opaque payloads, a scan never itself authorizes anything.
- Landed-cost allocation/calculation engine and CONFOTUR reconciliation UI, both fully wired end-to-end.
- Persona-branched Spanish-language dashboards (Compras / China / Finanzas / Almacén / Obra / Dirección).
- Self-contained HTML shipment/receiving/claim/comparison snapshots and revocable, logged secure share links.
- Versioned REST API under `/api/v1/` (read-only; package-associated commercial
  and manifest records are package/classification scoped, while legacy records
  retain organization scope).
- A validated production deployment: Docker Compose (Postgres + Gunicorn + Caddy), with backup/restore/persistence genuinely exercised (see `docs/FINAL_VALIDATION_REPORT.md`).

## Documentation

Start with these, in order:

1. `DT_BEACH_CURRENT_STATE.md` — fresh repository, validation, milestone, and handoff state.
2. `docs/MARKETMATCH_ARCHITECTURE_RECONCILIATION_AND_ROADMAP.md` — approved architectural decisions and official milestone order.
3. `DT_BEACH_SOURCE_OF_TRUTH_INDEX.md` — conflict-resolution and evidence hierarchy.
4. `docs/SOURCE_DOCUMENT_ANALYSIS.md` — what was actually found in the supplied source documents.
5. `docs/BUSINESS_REQUIREMENTS.md` — interview pain points converted into enforceable behavior.
6. `docs/ARCHITECTURE.md` and `docs/DATA_MODEL.md` — how it is built.
7. `docs/REQUIREMENTS_TRACEABILITY.md` — Done vs. Modeled vs. Planned.
8. `docs/KNOWN_LIMITATIONS.md` — what is not yet built or not yet evidenced.
9. `docs/FINAL_VALIDATION_REPORT.md` — historical production validation evidence.
10. `ASSUMPTIONS.md` — conservative decisions made where source material was unresolved.

## Local development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env    # edit as needed; set USE_SQLITE_FOR_TESTS=1 for a quick start without Docker
python manage.py migrate
python manage.py seed_pilot_data              # creates org, roles, and the 8 named pilot users
python manage.py import_live_container_fixture  # loads the real acceptance fixture
python manage.py runserver
```

Or with Docker Compose (Postgres included):
```bash
docker compose up -d
docker compose exec web python manage.py seed_pilot_data
docker compose exec web python manage.py import_live_container_fixture
```
Visit `http://localhost:8000/`. Pilot user passwords are printed once by
`seed_pilot_data` — copy them immediately, they are never logged again.

## Tests

```bash
pytest
```
511 tests, covering the live-container fixture import, document
upload/duplicate-detection/authorization, the receiving/inventory ledger,
object-level permission scoping, gate controls and formal handoffs,
delivery/installation/inspection/final acceptance, landed-cost
allocation, CONFOTUR reconciliation, tool custody, cycle counts, storage
capacity/suitability, external storage comparison, supplier claims, and
QR labels/controlled scanning. See `docs/REQUIREMENTS_TRACEABILITY.md`
for exactly what each area's tests prove.

Foundation correction cycle 3 result: **511 passed, 0 failed** (113.25s,
SQLite). Focused foundation/remediation suite (`pytest
tests/test_foundation_remediation.py tests/test_procurement_confidentiality.py
tests/test_confidentiality_http.py tests/test_evidence_and_disclosure.py`):
**53 passed, 0 failed** (49.76s). Migrations are clean; no migration was
required in cycles 2 or 3.

## Current milestone handoff

Latest completed product milestone:
**Controlled Transparency, Commercial Confidentiality & Authorization
Foundation — CLOSED AND OWNER-ACCEPTED (2026-07-20)**.

Foundation correction cycle 3 closed three independently-discovered and
reproduced gaps in cycle 2's own privileged-audit mechanism
(CTCF-AUDIT-SCOPE-021, CTCF-AUDIT-RETRIEVAL-022, CTCF-AUDIT-WINDOW-023);
see `docs/KNOWN_LIMITATIONS.md` and ADR-043. An independent Fable
revalidation of that correction (commit `2c52b0b83340fda2eaa84700eaeddfbe0839d6d8`)
found no new Critical/High blocker, and Harrison recorded explicit owner
and business acceptance of the foundation on that basis — see
`DT_BEACH_CURRENT_STATE.md` for the full acceptance text. PostgreSQL was
not validated in that process (SQLite only, no Docker daemon available)
and remains an open limitation, not represented as complete; no
production deployment of this foundation has occurred or is claimed.

The independent Milestone 1 Charter Review that followed foundation
acceptance found that no standalone Milestone 1 charter existed (only a
short roadmap acceptance-criteria summary) and identified twelve findings,
CHTR-001 through CHTR-012 — including a direct conflict between the
roadmap's "reuse `GateOverride`" clause and ADR-020's existing reasoning
against exactly that kind of reuse. A **Milestone 1 Charter Definition and
Reconciliation** cycle has now produced a complete, standalone charter —
`docs/MILESTONE_1_PROCUREMENT_GATES_CHARTER.md` — resolving all twelve
findings. This was a documentation-only cycle: no application code,
template, test, or migration changed, and A1–A6 remain unimplemented.

That Charter (version 1) was then independently revalidated; the result
was **MILESTONE 1 CHARTER REQUIRES CORRECTION** — twelve new findings,
REVAL-001 through REVAL-012, were found in the Charter's own text (not in
the original CHTR review), including a `GateAttempt`↔`EvidenceBundle`
cardinality contradiction and two incompatible canonical-default-policy
definitions. A documentation-only **Milestone 1 Charter Correction Cycle**
produced Charter version 2, resolving all twelve REVAL findings — again
with no application code, template, test, or migration changed.

Charter version 2 was then itself independently revalidated; the result
was **MILESTONE 1 CHARTER VERSION 2 REQUIRES CORRECTION** — ten findings,
including two of version 2's own REVAL corrections left incomplete under
adversarial follow-through (REVAL-004-RESIDUAL, REVAL-008-RESIDUAL) and a
direct contradiction against the current, unmodified
`apps.governance.services.request_change` (NF-1). A documentation-only
**Milestone 1 Charter Correction Cycle 2** produced Charter version 3,
resolving all ten findings — again with no application code, template,
test, or migration changed.

Exact next action: **run a new, independent Fable 5 Charter revalidation
session against the commit introducing Charter version 3. Do not begin
A1–A6 implementation.**

Milestone 1 remains planned and approved; neither foundation acceptance nor
this Charter's own authorship authorizes A1–A6 implementation, which begins
only after independent Charter revalidation and explicit owner approval of
the Charter itself.

## Production deployment

See `docs/DEPLOYMENT.md`. Short version:
```bash
cd deploy
cp .env.production.example .env.production   # fill in real secrets
./deploy.sh
./create_admin.sh
```

## Project boundary

All development for this delivery was performed exclusively inside this
`Application/` directory. No file outside it (including the MarketMatch
repository, referenced only conceptually in `docs/MARKETMATCH_INTEGRATION_PATH.md`)
was read, modified, or depended upon.
