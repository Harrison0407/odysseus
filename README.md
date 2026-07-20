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
confidentiality coverage; 472 tests pass with clean migrations. Independent
revalidation is the next required control point. What is not yet built is listed honestly in
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
472 tests, covering the live-container fixture import, document
upload/duplicate-detection/authorization, the receiving/inventory ledger,
object-level permission scoping, gate controls and formal handoffs,
delivery/installation/inspection/final acceptance, landed-cost
allocation, CONFOTUR reconciliation, tool custody, cycle counts, storage
capacity/suitability, external storage comparison, supplier claims, and
QR labels/controlled scanning. See `docs/REQUIREMENTS_TRACEABILITY.md`
for exactly what each area's tests prove.

Foundation-remediation result: **472 passed, 0 failed**. Migrations are clean.

## Current milestone handoff

Latest completed product milestone:
**Controlled Transparency, Commercial Confidentiality & Authorization Foundation**.

Exact next action: **independent revalidation of the remediated foundation**.

Milestone 1 remains planned and approved but must not begin until that
independent revalidation accepts the foundation.

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
