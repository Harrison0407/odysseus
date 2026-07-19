# DT Beach Supply Control — Source of Truth Index

Last updated: 2026-07-19

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

Latest application-behavior commit:

`58889c8`

Tests:

`455/455 passing`

Migrations:

`clean` — no model changes and no unapplied migrations.

Ahead / behind after fetch:

`0 / 0`

The commits after `58889c8` through Gate 0 are documentation-only and do not
change application behavior. The Gate 0 completion commit is the commit
containing this revision.

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
