# DT Beach Supply Control — Source of Truth Index

Last updated: 2026-07-21

## Purpose

This document defines the source-of-truth hierarchy for DT Beach Supply Control.

When sources conflict, the priority and resolution rules in this document must be followed.

## Repository And Documentation Reconciliation Priority

For implementation status and repository-state claims, use this order:

1. Current repository code, migrations, tests, and fresh terminal evidence.
2. Approved architectural and operational documents, led by
   `docs/MARKETMATCH_ARCHITECTURE_RECONCILIATION_AND_ROADMAP.md`.
3. Current repository documentation.
4. Harrison's explicit decisions.
5. Historical planning and milestone documents.
6. Clearly identified assumptions.

Plans and documentation are never evidence of implementation by themselves.
Always distinguish requested, planned, reported implemented, verified
implemented, live validated, and deployed.

### MarketMatch procurement prototype status (2026-07-21)

Requested/authorized: a non-production team-review prototype only. Verified
implemented: the DEBUG-gated synthetic route and documented prototype features
in `docs/MARKETMATCH_PROCUREMENT_PROTOTYPE.md`. Automated/HTTP validated on
SQLite: 79 migrations applied, 0 pending; 669 pytest collected, 663 passed, 6
PostgreSQL-only skipped, exit 0. Browser visual validation is pending owner
walkthrough; it is not deployed. Supplier CSV import validation is absent.
Increment 3 production behavior remains unauthorized.

## Operational Data Source Priority

### 1. Approved and Signed Source Documents

Highest-authority sources include:

- Architect-signed drawings
- Approved construction drawings
- Approved shop drawings
- Approved purchase orders
- Approved proforma invoices
- Approved packing lists
- Approved technical specifications
- Approved factory installation procedures
- Signed inspection and acceptance records
- Approved change requests
- Approved commercial documents

No application record may silently override an approved and signed source.

### 2. Approved Operational Scope

The canonical operational lifecycle is:

`Required → Ordered → Manufactured → Shipped → Received → Delivered → Installed → Inspected → Accepted`

MarketMatch is the operational source of truth for this lifecycle.

QuickBooks remains the financial source of truth.

### 3. Verified Repository State

Remote repository:

`https://github.com/Harrison0407/odysseus.git`

Active branch:

`integration/dt-beach-supply-control-1.0.0`

Gate 0 verified pre-reconciliation HEAD:

`5cd0df64edc5baf89a0e3e4e3efe8e1fc78d0b2c`

Foundation remediation commit (independently revalidated for its 14
non-deferred original findings; see `docs/KNOWN_LIMITATIONS.md`):

`a89a9f714684515be1b2de704bf816611e094540`

Foundation correction cycle 2 commit (closed CTCF-AUDIT-017 and
CTCF-CR-PROJ-018; independently revalidated, which itself found and
reproduced three further gaps in the privileged-audit mechanism cycle 2
introduced):

`84b12a2187d93f2ccd9992780a5a4b73e54e7cc6`

**Accepted foundation implementation baseline — foundation correction
cycle 3** (closes CTCF-AUDIT-SCOPE-021, CTCF-AUDIT-RETRIEVAL-022, and
CTCF-AUDIT-WINDOW-023 found during revalidation of cycle 2; itself
independently revalidated on 2026-07-20 with no new Critical/High
blocker, then **explicitly owner-accepted by Harrison** — see
`DT_BEACH_CURRENT_STATE.md` and `docs/implementation-log.md` entry 55):

`2c52b0b83340fda2eaa84700eaeddfbe0839d6d8`

**Foundation status: owner accepted and closed** as of 2026-07-20.

**Documentation-only acceptance commit** (records Harrison's acceptance
text into the source-of-truth documents; no application code, template,
test, or migration changed):

The commit containing this revision; obtain its immutable hash with
`git rev-parse HEAD`. Its parent is `2c52b0b83340fda2eaa84700eaeddfbe0839d6d8`.

**Milestone 1 Charter Definition and Reconciliation is complete, locally,
documentation-only** — see `docs/MILESTONE_1_PROCUREMENT_GATES_CHARTER.md`
and `DT_BEACH_CURRENT_STATE.md` for the full disposition tables. Charter
version 1 was independently revalidated; the result was MILESTONE 1
CHARTER REQUIRES CORRECTION (CHTR-001–CHTR-012 remained resolved, but
twelve new findings, REVAL-001–REVAL-012, were found in version 1's own
text). A documentation-only Milestone 1 Charter Correction Cycle produced
Charter version 2, resolving all twelve REVAL findings. Charter version 2
was itself independently revalidated (commit
`3b62228b4a6efb4079e7f8c010e107fcf9de639a`); the result was MILESTONE 1
CHARTER VERSION 2 REQUIRES CORRECTION (ten findings: REVAL-004-RESIDUAL,
REVAL-005-RESIDUAL, REVAL-008-RESIDUAL, REVAL-009-TRACE,
REVAL-011-ENFORCEMENT, NF-1, NF-2, NF-3, NF-4, NF-7). A documentation-only
Milestone 1 Charter Correction Cycle 2 produced Charter version 3,
resolving all ten. Charter version 3 was itself independently revalidated
(commit `f59237b6ba0c18e210c54f01cd79e98ea40e1709`); the result was
MILESTONE 1 CHARTER VERSION 3 REQUIRES CORRECTION (three blocking
findings — NF-NEW-1, NF-NEW-2, NF-NEW-3 — two additional accepted
findings — NF-NEW-4, NF-NEW-5 — and one editorial line-citation defect).
A documentation-only Milestone 1 Charter Correction Cycle 3 produced
Charter version 4, resolving all six. Charter version 4 was itself
independently revalidated (commit
`cf01d400e1dffd6c5981ee2ae8a01ad71f3c9006`); the result was MILESTONE 1
CHARTER VERSION 4 REQUIRES CORRECTION (two blocking findings —
RISKFLAG-HOLD-1, NF4-A — and eight additional accepted findings —
DOC-COUNT-1, LOCK-ORDER-1, NF4-C, NF-V4-2, README-STALE, IMPL-LOG-COUNT,
NF-V4-5, TRACE-1). A documentation-only Milestone 1 Charter Correction
Cycle 4 produced Charter version 5, resolving all ten. Charter version 5
was itself independently revalidated (commit
`ebcabdc582dd8ffea3ebdfce68dc55c4ee59c526`); the result was MILESTONE 1
CHARTER VERSION 5 REQUIRES CORRECTION (four blocking findings —
CR-CREATE-AUTH-GAP, HOLD-CAUSE-CLOSURE-1, NF-V4-2-INCOMPLETE-MAPPING,
LOCK-ORDER-1-1 — and five additional accepted cleanup items — NF4-A-1,
PGSTATE-ADMIN-1, CHTR-010-COUNT-2, HOLD-WORDING-1, NF-1-SUMMARY-1). A
documentation-only Milestone 1 Charter Correction Cycle 5 produced Charter
version 6, resolving all nine. Charter version 6 was itself independently
revalidated, delta-only (commit
`e9b5cb31eedcea219ac35c906dfbf65e5ff4d2de`, the same commit introducing
version 6); the result was MILESTONE 1 CHARTER VERSION 6 APPROVED FOR
OWNER ACCEPTANCE — the first clean pass in this Charter's revalidation
history, with no findings requiring correction. Harrison (owner)
subsequently explicitly accepted Charter version 6, at the same commit,
as the approved architectural and functional contract for Milestone 1, on
2026-07-21.

**Update — Milestone 1 Implementation Increment 1 complete (2026-07-21).**
Following Charter acceptance, Harrison separately, explicitly authorized
a bounded first implementation increment — Procurement Gate Policy and
Package Assignment Foundation (`apps.procurement_gates`: `GatePolicy`,
`GatePolicyVersion`, `PackagePolicyAssignment`; Charter §§1–4 plus the
corresponding slices of §10/§13). A1–A6 gate *execution* (`GateAttempt`
and everything downstream) remains entirely unimplemented and
unauthorized. See `docs/implementation-log.md` entry 63 and ADR-049 for
full detail.

**Next governing activity: run an independent, increment-only Fable
review against the Increment 1 commit. Do not begin Increment 2 until
that review is reconciled and Harrison explicitly authorizes the next
increment.**

**Correction update:** Codex implementation verification of Increment 1
commit `9b803911` reported CX-I1-001 through CX-I1-010. The bounded correction
implements CX-I1-001 through CX-I1-009 and the related CX-I1-010 editorial
cleanup, adds `procurement_gates.0003`/`.0004`, and adds adversarial/MigrationExecutor
coverage. The correction commit and final accepted baseline are
`220be7aa030b656fb960151c92166594ba539a26`. Codex re-verification returned
**MILESTONE 1 IMPLEMENTATION INCREMENT 1 CODEX RE-VERIFIED — READY FOR
HARRISON RECONCILIATION** with no remaining Critical, High, or blocking Medium
Increment 1 defect. On 2026-07-21 Harrison explicitly accepted Increment 1 at
that commit. Acceptance evidence: 615 collected, 613 passed, 2 PostgreSQL-only
tests skipped on SQLite; 77/77 migrations applied, 0 pending, clean tree.
PostgreSQL concurrency execution remains pending and is not claimed as
validated. Increment 1 is closed and owner-accepted. Increment 2 remains
unauthorized and requires a separate explicit owner decision.

**Next governing activity:** await Harrison's separate explicit authorization
for Increment 2.

**Update — Milestone 1 Implementation Increment 2 complete locally
(2026-07-21).** Harrison separately authorized **Gate Execution Core and A1
Deal Established** from baseline
`193fdfb720662a235b259b97694a0d5e3d8edcaa`. The implementation is limited to
immutable attempt/evaluation/decision history, rebuildable package-gate state,
explicit initialization, and A1 evaluation/review/pass/return; A1 passage makes
A2 current only as `NOT_STARTED`. SQLite validation collected 655 tests: 649
passed and 6 PostgreSQL-only concurrency tests skipped; 79/79 migrations are
applied with 0 pending. PostgreSQL execution is pending, not claimed.

Increment 3 and A2 evaluation/freeze, A3–A6 execution, invalidation, new hold
causes, overrides, evidence mapping, and API work remain unauthorized and
absent.

**UI completion update (2026-07-21):** the original Increment 2 backend commit
is `d2aacafe91edf5b680f4071e72a7291bdb95343f`; the package-detail A1 browser
vertical slice is completed by
`acd2becce64e99c2dac559ccf805b1c64344debd` (`Complete testable A1 browser
vertical slice`). It adds no execution model or migration. Fresh evidence is
663 collected, 657 passed, 6 PostgreSQL-only skipped on SQLite, and 79/79
migrations applied with 0 pending. Chrome exercised the complete synthetic
return/re-attempt/pass flow; A2 was current but non-executable, protected
sentinels were absent, and package/Handoff state stayed independent. The fresh
Harrison URL is
`http://127.0.0.1:8000/compras/paquetes/b8ee6165-bf55-415f-8333-7318eb54d8ee/`.

**Accepted Increment 2 implementation baseline — closed and owner-accepted
(2026-07-21):**

`acd2becce64e99c2dac559ccf805b1c64344debd`

Codex verification returned **MILESTONE 1 IMPLEMENTATION INCREMENT 2 CODEX
VERIFIED — READY FOR HARRISON ACCEPTANCE**, with no remaining Critical or High
Increment 2 defect. Harrison explicitly accepted **MarketMatch Milestone 1
Implementation Increment 2 — Gate Execution Core and A1 Deal Established**
after completing the real browser flow through initialization, evaluation,
review request, self-approval denial, return, re-attempt, second evaluation,
review request, separate approval, A1 `PASSED`, and A2
current/non-executable. Accepted evidence is 657 passed, 6 PostgreSQL-only
skipped on SQLite, 79/79 migrations applied, 0 pending, and a clean tree.
PostgreSQL concurrency execution remains pending and is not claimed as
validated. Increment 3 remains absent and unauthorized.

**Next governing activity:** await Harrison's separate explicit authorization
to begin Increment 3.

Tests (as independently reproduced during the cycle-3 revalidation that
led to acceptance, then extended by Increment 1):

`511/511 passing` (472 from the foundation remediation, 24 from
foundation correction cycle 2, 15 from foundation correction cycle 3);
72/72 migrations applied; SQLite only — PostgreSQL not validated.

Increment 1 update: `565/565 passing` (511 above + 54 new
`tests/test_procurement_gates_policy.py` tests); 75/75 migrations applied
(72 above + 2 new `procurement_gates` migrations + 1 new `audit`
migration); SQLite only — PostgreSQL concurrency validation not run this
cycle, recorded as pending.

Migrations:

`clean` — no model changes and no unapplied migrations.

Ahead / behind after fetch:

`0 / 0` (once pushed)

The commits after `58889c8` through Gate 0 were documentation-only. The
foundation remediation (`a89a9f7`) and both correction cycles (`84b12a2`
and `2c52b0b`) are genuine application-behavior changes, each with their
own passing test evidence recorded above and in
`docs/implementation-log.md`. This acceptance commit is, like Gate 0,
documentation-only — it records Harrison's explicit acceptance of the
`2c52b0b` application-behavior baseline without changing it.

Application behavior is authoritative only when:

- tests pass;
- migrations are clean;
- the working tree is clean;
- the commit is pushed;
- milestone validation is complete.

### 4. Verified Imported Project Files

Imported project files are valid only when their identity, version, date, and source are known.

Expected import location:

`Application/imports/`

Building-document location:

`Application/imports/buildings/`

Known source documents include:

- Buildings Plans Main.pdf
- DT Beach Building Apartments.xlsx.pdf
- PALMERA - PLANOS 13.11.2025.pdf

### 5. Controlled System Records

Controlled records may represent:

- requirements
- procurement packages
- factory RFQs and quotes
- client quotes
- shipments and containers
- warehouse movements
- deliveries
- installation evidence
- inspections
- acceptance
- approved changes
- disclosure grants
- evidence assertions

Every controlled record must preserve traceability to its source documents and evidence.

### 6. Field Evidence

Field evidence includes:

- original photographs
- original videos
- timestamps
- location context
- building, floor, unit, room, or zone assignment
- responsible person
- inspection observations
- installation checklists
- factory procedure references

Original evidence must never be overwritten.

### 7. Derived Artifacts

Derived artifacts include:

- translations
- summaries
- annotations
- extracts
- thumbnails
- OCR output
- AI analysis
- comparison reports
- client-safe projections
- verification assertions

Derived artifacts are never the original source.

Every derived artifact must retain:

- source reference
- source version or hash
- generator or model
- creation timestamp
- source and target language when applicable
- authorization context
- review status
- stale-state handling

### 8. Human Communications

WhatsApp messages, emails, meetings, voice notes, and informal instructions are supporting evidence, not automatically approved scope.

They become authoritative only when converted into an approved:

- change request
- decision record
- purchase order
- drawing revision
- technical instruction
- inspection instruction
- acceptance record

## Conflict Resolution Rules

When two sources conflict:

1. Do not guess.
2. Preserve both originals.
3. Identify dates and versions.
4. Identify the approving authority.
5. Mark the conflict explicitly.
6. Block downstream acceptance when the conflict affects scope, safety, quantity, installation, or commercial terms.
7. Resolve through an approved decision or change request.
8. Record who resolved it, when, and why.

## Missing Source Rule

Missing source information must never be invented.

Use explicit states such as:

- Missing Source
- Pending Verification
- Unapproved
- Superseded
- Conflicting Source
- Not Applicable

PALMERA contains genuine unit-type plans.

The supplied drawings do not contain reliable individual apartment plans for:

- ARENA T1
- MARE B
- SOLE
- SOLE PH
- SOLE 26

These must remain marked as `Missing Source` until authoritative drawings are provided.

## Confirmed Building Identity Mapping

- Building 17 — SOLE
- Building 18 — SOLE PH
- Building 19 — MARE B
- Building 20 — SOLE PH
- Building 21 — MARE B
- Building 22 — SOLE
- Building 23 — MARE B
- Building 24 — MARE B
- Building 25 — MARE B
- Building 26 — SOLE 26

Confirmed ARENA buildings:

- 1
- 2
- 3
- 4
- 9
- 10
- 11
- 12

## Active Building Scope

Active:

- MARE B
- SOLE
- SOLE PH
- SOLE 26
- PALMERA
- ARENA T1

Future or inactive:

- MARE A
- ARENA T2
- ARENA T3

## Authority Boundaries

Product owner:

`Harrison`

Operational approval authority:

`María Luisa`

Technical authority:

`Director of Construction and Architect`

Warehouse authority:

`Manuel Quezada`

Application implementation:

`Codex / MarketMatch development workflow`

Financial authority:

`QuickBooks and approved accounting records`

## Confidentiality Rules

Authorization must be evaluated before:

- retrieval
- search
- translation
- summarization
- AI processing
- export
- derived-artifact creation
- client projection

Unauthorized confidential data must not reach:

- browser payloads
- API responses
- logs
- prompts
- embeddings
- search indexes
- exports
- notifications

## Commercial Source Layers

Commercial information must remain separated into controlled layers:

- Factory RFQ
- Factory Quote
- Internal Commercial Sheet
- Client Quote
- Approved Purchase Order
- Approved Invoice
- Payment and settlement records when implemented

A client quote must not expose factory identity, internal cost, margin, rebate, or confidential supplier terms unless explicitly authorized.

## Localization Rules

Canonical interface locales:

- `es` — Español
- `en` — English
- `zh-Hans` — 简体中文

Translations never replace originals.

Interface language does not modify authorization.

DT Beach operational timezone:

`America/Santo_Domingo`

## Update Discipline

Update this index when:

- a new authoritative source is accepted
- a drawing revision is approved
- a building mapping is confirmed or corrected
- a source is superseded
- a governance rule changes
- a new source category is introduced
- the verified repository baseline changes materially

Every accepted update must be committed and pushed.
