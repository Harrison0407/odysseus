# DT Beach Supply Control

A standalone operational control system for DT Beach connecting
purchasing → China origin → shipping/customs → landed cost → warehouse
receiving → inventory → project delivery → installation → acceptance,
built around one non-negotiable architectural rule: **official
carrier/customs documentation and the internal operational cargo truth
are modeled as two separate, explicitly reconciled views — never
merged, never silently edited into agreement.**

This is a staged delivery of a very large governing specification
(`DT_Beach_Supply_Control_Fable5_FINAL_LOCKED_PROMPT.md`, one directory
above `Application/`). The Priority 0 vertical slice described below is
genuinely implemented, tested, and validated against a real production
Docker stack. What is not yet built is listed honestly in
`docs/KNOWN_LIMITATIONS.md` — please read that file before assuming any
capability beyond what's below.

## What's implemented

- Full canonical data model: 18 Django apps, 176 models, 25 migrations, clean from an empty database.
- Multilingual document upload with SHA-256 provenance and duplicate detection.
- The dual-manifest engine (Official Carrier Summary vs. Internal Operational Manifest) with an official-vs-operational variance matrix, demonstrated against the real supplied live-container fixture (BL MEDUWY575021 / container TCNU8926924).
- Ledger-based inventory (on-hand quantity always derived from posted movements, never edited directly).
- Physical receiving with risk exceptions, automatic quarantine of damaged stock, and discrepancy creation.
- Material requests, purchase orders with open-commitment carryover tracking, replacement/corrective-cargo case tracking.
- Landed-cost and CONFOTUR data models (schema-complete; allocation-run/reconciliation UI is a near-term follow-up).
- Persona-branched Spanish-language dashboards (Compras / China / Finanzas / Almacén / Obra / Dirección).
- Self-contained HTML shipment snapshots and revocable, logged secure share links.
- Versioned REST API under `/api/v1/` (read-only, organization-scoped).
- A validated production deployment: Docker Compose (Postgres + Gunicorn + Caddy), with backup/restore/persistence genuinely exercised (see `docs/FINAL_VALIDATION_REPORT.md`).

## Documentation

Start with these, in order:

1. `docs/SOURCE_DOCUMENT_ANALYSIS.md` — what was actually found in the supplied source documents.
2. `docs/BUSINESS_REQUIREMENTS.md` — interview pain points converted into enforceable behavior.
3. `docs/ARCHITECTURE.md` and `docs/DATA_MODEL.md` — how it's built.
4. `docs/OFFICIAL_VS_OPERATIONAL_MANIFEST_ANALYSIS.md` — the core architectural decision, explained against the real fixture.
5. `docs/REQUIREMENTS_TRACEABILITY.md` — what's Done vs. Modeled vs. Planned, honestly.
6. `docs/KNOWN_LIMITATIONS.md` — what is not yet built.
7. `docs/FINAL_VALIDATION_REPORT.md` — exactly what was tested and how.
8. `docs/USER_GUIDE_ES.md` — end-user quick-start guide (Spanish).
9. `docs/DEPLOYMENT.md` and `docs/BACKUP_RESTORE.md` — running it in production.
10. `ASSUMPTIONS.md` — every conservative choice made where the spec left a minor point unresolved.

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
21 tests, covering the live-container fixture import, document
upload/duplicate-detection/authorization, the receiving/inventory ledger,
and object-level permission scoping.

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
