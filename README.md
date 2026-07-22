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
explicitly owner-accepted by Harrison — the foundation is now closed.
Harrison then explicitly authorized **Milestone 1 Implementation
Increment 1 — Procurement Gate Policy and Package Assignment
Foundation**, now implemented: `apps.procurement_gates` (`GatePolicy`,
`GatePolicyVersion`, `PackagePolicyAssignment`) — configurable, versioned
A1–A6 gate policy schemas, canonical/organization-specific resolution,
and permanent package pinning, with 54 new tests (565/565 total passing).
A1–A6 gate *execution* was not part of Increment 1. Harrison separately
authorized **Increment 2 — Gate Execution Core and A1 Deal Established** on
2026-07-21 from baseline
`193fdfb720662a235b259b97694a0d5e3d8edcaa`; that bounded increment now
implements immutable `GateAttempt`, `GateEvaluation`, `GateDecision`, and
rebuildable `PackageGateState` history plus authorized A1 initialization,
evaluation, review, pass/return, and A2 readiness only. A2 evaluation/freeze,
A3–A6 behavior, overrides, new holds, invalidation, evidence mapping, and APIs
remain excluded. The package-detail A1 browser vertical slice was subsequently
completed from backend commit `d2aacafe91edf5b680f4071e72a7291bdb95343f` at
accepted implementation/browser commit
`acd2becce64e99c2dac559ccf805b1c64344debd` — see
`docs/MILESTONE_1_PROCUREMENT_GATES_CHARTER.md`,
`docs/implementation-log.md` entry 63 and ADR-049. The correction recorded in
entry 64 and ADR-050 subsequently passed read-only Increment 1 Codex
verification and received owner acceptance. What is not
yet built is listed honestly in `docs/KNOWN_LIMITATIONS.md`; the official
milestone order is governed by
`docs/MARKETMATCH_ARCHITECTURE_RECONCILIATION_AND_ROADMAP.md`.

Codex implementation verification of Increment 1 commit `9b803911` found
CX-I1-001 through CX-I1-010. The bounded correction closes CX-I1-001 through
CX-I1-009 and reconciles CX-I1-010: permanent assignment/version history is
guarded across instance/queryset/bulk/delete paths; canonical availability
uses a shared policy lock and atomic replacement; assignment and exemption
use dedicated package-scoped capabilities; organization-policy ambiguity
fails closed; audit metadata is identifier-safe; and the original RunPython
migration is frozen against live-code drift. The correction commit and final
accepted baseline are `220be7aa030b656fb960151c92166594ba539a26`.
Codex re-verification returned **MILESTONE 1 IMPLEMENTATION INCREMENT 1 CODEX
RE-VERIFIED — READY FOR HARRISON RECONCILIATION** with no remaining Critical,
High, or blocking Medium Increment 1 defect. Harrison explicitly accepted
Increment 1 on 2026-07-21. Acceptance evidence: 615 tests collected, 613
passed, 2 PostgreSQL-only concurrency tests skipped on SQLite, and 77/77
migrations applied with 0 pending. Increment 1 is closed and owner-accepted.
PostgreSQL concurrency execution remains pending and is not claimed as
validated. Increment 1 remains closed and owner-accepted. Codex verification
of Increment 2 returned **MILESTONE 1 IMPLEMENTATION INCREMENT 2 CODEX VERIFIED
— READY FOR HARRISON ACCEPTANCE**, with no remaining Critical or High defect,
and Harrison explicitly accepted Increment 2 on 2026-07-21 after completing
the real browser walkthrough. Increment 2 is closed and owner-accepted;
Increment 3 remains unauthorized.

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
- Milestone 1 Increment 1: configurable, versioned A1–A6 procurement gate policy schemas (`apps.procurement_gates`), canonical-default/organization-specific resolution, and permanent package-to-policy-version pinning.
- Milestone 1 Increment 2: immutable A1 execution history, package-gate initialization, confidentiality-safe A1 evaluation, authorized review/pass/return, pure current-state derivation, rebuildable projection, and a capability-scoped browser vertical slice in the existing package detail. Passing A1 makes A2 current but creates no A2 attempt, evaluation, decision, or freeze.

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
Current Increment 2 validation: **663 collected, 657 passed, 6 skipped** on
SQLite; all six skips are real PostgreSQL-only lock/race tests. **79/79
migrations are applied, 0 pending.** PostgreSQL concurrency execution remains
pending and is not claimed as validated. The accepted implementation and
browser vertical-slice commit is
`acd2becce64e99c2dac559ccf805b1c64344debd`.

For a clearly synthetic A1 browser scenario, run
`python manage.py seed_a1_browser_demo --package-code synthetic-a1-harrison-browser`
under `DEBUG=True`, start the local server, and open
`http://127.0.0.1:8000/compras/paquetes/b8ee6165-bf55-415f-8333-7318eb54d8ee/`.
The command prints the synthetic operator/approver credentials and refuses to
run outside DEBUG. Increment 3 remains unauthorized.

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

Charter version 3 was then itself independently revalidated; the result
was **MILESTONE 1 CHARTER VERSION 3 REQUIRES CORRECTION** — six findings,
including three blocking (NF-NEW-1 — no mandatory gate-aware `ChangeRequest`
decision orchestration; NF-NEW-2 — competing/incomplete package-hold
mechanisms; NF-NEW-3 — organization-scoped bootstrap authorization
unsupported by `has_capability`), two accepted, non-blocking findings
(NF-NEW-4, NF-NEW-5), and one editorial line-citation defect. A
documentation-only **Milestone 1 Charter Correction Cycle 3** produced
Charter version 4, resolving all six findings — again with no application
code, template, test, or migration changed.

Charter version 4 was then itself independently revalidated; the result
was **MILESTONE 1 CHARTER VERSION 4 REQUIRES CORRECTION** — ten findings,
including two blocking (RISKFLAG-HOLD-1 — Critical: the unified
package-hold projection was silently defeated by the unmodified
`apps.governance.services.resolve_risk_flag`, which no gate-aware wrapper
protected against; NF4-A — High: no admin-edit-immutability policy
existed for procurement-gates historical models beyond the two narrow
cases version 4 already covered) and eight accepted, non-blocking findings
(DOC-COUNT-1, LOCK-ORDER-1, NF4-C, NF-V4-2, README-STALE, IMPL-LOG-COUNT,
NF-V4-5, TRACE-1). A documentation-only **Milestone 1 Charter Correction
Cycle 4** produced Charter version 5, resolving all ten findings — again
with no application code, template, test, or migration changed.

Charter version 5 was then itself independently revalidated; the result
was **MILESTONE 1 CHARTER VERSION 5 REQUIRES CORRECTION** — nine findings,
including four blocking (CR-CREATE-AUTH-GAP — Critical: no capability code
was ever named for `ChangeRequest`-creation authorization, and the Charter
falsely credited the unmodified `apps.governance.services.request_change`
with performing a capability check it does not perform; HOLD-CAUSE-CLOSURE-1
— Critical: no named refreeze service existed and no rule defined which
open `PackageHoldCause` rows a given refreeze may close; NF-V4-2-INCOMPLETE-MAPPING
— Critical: the Charter described the decision-time capability lookup with
bracket notation that does not match the actual repository code's
`.get(field_name, "APPROVE_ROLE_CHANGE")` fallback access; LOCK-ORDER-1-1
— Critical: `reconcile_expired_overrides`' expiry-reconciliation lock
order was inconsistent with human override approval/rejection/revocation's
own binding order) and five accepted, non-blocking cleanup items (NF4-A-1,
PGSTATE-ADMIN-1, CHTR-010-COUNT-2, HOLD-WORDING-1, NF-1-SUMMARY-1). A
documentation-only **Milestone 1 Charter Correction Cycle 5** produced
Charter version 6, resolving all nine findings — again with no application
code, template, test, or migration changed.

Charter version 6 was then itself independently revalidated, delta-only,
against commit `e9b5cb31eedcea219ac35c906dfbf65e5ff4d2de` (the same commit
introducing version 6); the result was **MILESTONE 1 CHARTER VERSION 6
APPROVED FOR OWNER ACCEPTANCE** — the first clean pass in this Charter's
revalidation history, with no findings requiring correction. Following
that result, Harrison (owner) explicitly accepted Charter version 6, at
the same commit, as the approved architectural and functional contract
for Milestone 1, on 2026-07-21.

Following Charter acceptance, Harrison separately, explicitly authorized
**Milestone 1 Implementation Increment 1 — Procurement Gate Policy and
Package Assignment Foundation**, bounded to the configuration/pinning
layer only (`GatePolicy`, `GatePolicyVersion`, `PackagePolicyAssignment` —
none of `GateAttempt`, `GateEvaluation`, `GateDecision`,
`ProcurementGateOverride`, freeze/change-control, or any gate-execution
behavior). That increment is now implemented, migrated, and tested
(565/565 tests passing) — see `docs/implementation-log.md` entry 63 and
ADR-049.

Increment 2 was subsequently authorized and implemented from baseline
`193fdfb720662a235b259b97694a0d5e3d8edcaa`; see implementation-log entry 66
and ADR-051. The A1 browser vertical slice completed at
`acd2becce64e99c2dac559ccf805b1c64344debd`; Codex verification returned
**MILESTONE 1 IMPLEMENTATION INCREMENT 2 CODEX VERIFIED — READY FOR HARRISON
ACCEPTANCE** with no remaining Critical or High Increment 2 defect. Harrison
completed initialization, evaluation, review request, self-approval denial,
return, re-attempt, second evaluation, review request, separate approval, A1
`PASSED`, and A2 current/non-executable, then explicitly accepted Increment 2
on 2026-07-21. Accepted validation was SQLite: 657 passed, 6 PostgreSQL-only
skipped, 79/79 migrations applied, and 0 pending. PostgreSQL concurrency
execution remains pending and is not claimed as completed.

Increment 2 is closed and owner-accepted. Increment 3 is not authorized.

Exact next action: **await Harrison's separate explicit authorization to begin
Increment 3.**

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
