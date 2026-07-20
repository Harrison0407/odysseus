MarketMatch Architecture Reconciliation and Official Roadmap

Decision date: 2026-07-19
Scope: Architecture and product reconciliation for DT Beach Supply Control on MarketMatch
Status: Final

1. Authority and baseline

This reconciliation uses the project source hierarchy and distinguishes documented state from live terminal evidence.

Documented repository state:

Branch: integration/dt-beach-supply-control-1.0.0
Upstream: origin/integration/dt-beach-supply-control-1.0.0
Documentation HEAD: 8b7e102
Verified functional baseline: 58889c8
Reported regression suite: 455/455 passing
Reported migrations: clean
Latest completed milestone: Controlled Transparency, Commercial Confidentiality & Authorization Foundation

No current terminal transcript was supplied in this reconciliation conversation. Therefore the baseline above is documented verified state, not a new live verification. The execution hub must re-check branch, HEAD, upstream, working tree, migrations, and tests before implementation.

2. Reconciliation decisions
ID	Audit finding	Decision	Official ruling
AR-01	Replace the modular monolith with microservices now	Reject	The existing modular monolith remains appropriate for pilot scale and current operational complexity. No distributed-system rewrite is justified. Extract services only after measured scaling or isolation pressure.
AR-02	Rebuild completed modules to obtain a cleaner architecture	Reject	Property Master, interactive plans, workflow, evidence, inventory, receiving, claims, and governance are completed foundations. Extend them through existing service layers and ADR patterns; do not rebuild them.
AR-03	Collapse Building Family and Physical Building into one entity	Reject	They remain distinct. The canonical hierarchy is Project → Building Family → Physical Building → Floor → Unit/Common Area.
AR-04	Create room layouts for buildings with missing individual plans	Reject	Missing Source is the required state for ARENA T1, MARE B, SOLE, SOLE PH, and SOLE 26 until authoritative plans are supplied. PALMERA is the only supplied family with reliable unit-type plans.
AR-05	Keep Party, Role Assignment, Capability, and Authorization Scope separate	Accept	This is the canonical authorization model. Sensitive approvals, authorizations, exports, and privileged audit access require explicit capability grants.
AR-06	Authorize before retrieval, projection, transformation, export, search, or AI use	Accept	This is non-negotiable. Restricted information must never enter browser payloads, APIs, logs, prompts, indexes, exports, notifications, or derived artifacts without authorization.
AR-07	Merge factory RFQ, factory quote, internal sheet, client quote, and POs into one commercial record	Reject	The commercial layers remain structurally separate. Client-safe objects must not contain factory identity, origin cost, markup, margin, rebate, or confidential supplier terms.
AR-08	Preserve the hosted-package plus package-scoped Party/Role/Capability model	Accept	The current model is valid. Do not rearchitect all legacy tenancy. Extend generic APIs and integrations through the governance authorization layer when package-aware access is required.
AR-09	Full configurable A1–A6 procurement gates are missing	Accept	This is the next principal product milestone. The current package status is not a substitute for A1–A6 and must remain a separate state machine.
AR-10	Reuse EvidenceBundle, CapabilityGrant, ChangeRequest, RiskFlag, and GateOverride for A1–A6	Accept	No parallel workflow, evidence, approval, or exception engine may be introduced.
AR-11	Add a universal numeric evidence-confidence score now	Reject	A generic score is not defensible without a calibrated model, ground truth, and review policy. Use explicit evidence requirements, review states, provenance, anomalies, risk indicators, and human decisions. A future score requires validated evidence.
AR-12	Add payment legs and monetization records	Accept with boundary	MarketMatch may record operational payment obligations, evidence, approvals, status, allocation, and reconciliation. QuickBooks and approved accounting records remain the financial source of truth. MarketMatch must not silently become the accounting ledger.
AR-13	Add escrow, custody of money, lending, factoring, and credit insurance execution	Defer	These require legal, regulatory, banking, accounting, and operational decisions. Data interfaces may be designed later, but no custody or regulated financial execution is authorized now.
AR-14	Implement Maidan, Shuangqing, double-clearance, and complete customs workflows	Defer	Risk flags may record and hold cases, but implementation requires approved legal/compliance rules, jurisdictions, evidence schemas, and named authorities.
AR-15	Complete the Internationalization Foundation	Accept	Canonical locales are es, en, and zh-Hans. Locale must never alter authorization or timezone. Originals and translations remain separate, provenance-linked artifacts.
AR-16	Add OCR and automated translation immediately	Defer to an adapter milestone	The models and security contract exist. Engines should be introduced only after the i18n foundation and authorized-projection boundary are complete. No engine may receive unauthorized originals.
AR-17	Integrate commercial records into search, autocomplete, exports, QR, notifications, and reports	Accept with prerequisite	These integrations are useful, but every path must reuse centralized authorized query filtering and export authorization. No bespoke visibility checks.
AR-18	Make the generic API package-aware for legitimate cross-organization participants	Accept	Current conservative denial is safe but incomplete. Extend APIs only through package-scoped role/capability authorization, with negative tests proving confidential fields are absent, not merely hidden.
AR-19	Build Asset Passport / Property Digital Passport next	Defer until upstream gates stabilize	The concept is accepted, but it depends on trustworthy approved/installed asset records, revisions, evidence, maintenance data, and lifecycle identifiers. It follows procurement-gate and integration hardening.
AR-20	Build a native mobile app and complex offline sync now	Defer	Responsive web remains the baseline. Native/offline work requires measured field connectivity, device, camera, sync-conflict, and support requirements.
AR-21	Add dependency scanning, backup encryption, shared rate limiting, and production observability	Accept	These are production-hardening requirements before broader external use. They do not justify microservices or Kubernetes.
AR-22	Audit records are provably immutable at the database/operations layer	Needs Evidence	Documentation establishes append-only application behavior, but database permissions, retention, tamper evidence, archival, and restore behavior need direct repository and operational verification.
AR-23	Authorization coverage is complete across every legacy and new retrieval path	Needs Evidence	Package views are tested, but a repository-wide authorization matrix and endpoint sweep are required before claiming universal coverage.
AR-24	Performance, scale, SLOs, monitoring, and multi-tenant capacity require architectural decomposition	Needs Evidence	Establish measurements first: workload, users, data volumes, latency, concurrency, storage growth, RPO/RTO, and operational support. Do not infer a scaling problem from system breadth alone.
AR-25	Documentation accurately represents current implementation	Accept as a correction finding	The current-state file is authoritative, but README and parts of requirements traceability contain historical counts/statuses. Documentation must be reconciled before the next milestone is declared complete.
AR-26	General historical importer, complex kits UI, and receiving-plan subrecord UI are immediate priorities	Needs Evidence	They are valid modeled gaps, but priority requires actual operational demand and acceptance scenarios. They do not outrank A1–A6, i18n, authorization integration, or production hardening.
AR-27	Add blockchain anchoring, GPS/data loggers, and 24/7 concierge to the official roadmap	Reject	No validated requirement or dependency justifies them. They may return only through a new business case and evidence.
AR-28	Add physical sample custody	Needs Evidence	The role concept is documented, but custody locations, chain-of-custody events, seals, acceptance authority, exceptions, and legal responsibility must be defined first.
AR-29	Mobile quality is fully validated	Needs Evidence	Responsive structure is tested, but pixel-level multi-viewport and device validation has not been demonstrated. Add visual validation before a broad field rollout.
AR-30	Current Git and test state is live-confirmed in this conversation	Needs Evidence	The documented baseline is accepted provisionally. The implementation hub must obtain fresh terminal evidence before work begins.
3. Official roadmap
Roadmap Gate 0 — Baseline and documentation reconciliation

This is a mandatory pre-implementation gate, not a product rebuild.

Required outcomes:

confirm branch, HEAD, upstream, and working-tree status from terminal;
run migration checks and the full regression suite;
reconcile README, requirements traceability, known limitations, implementation log, and current-state document;
identify which untracked evidence documents belong in the repository and which must remain external;
record the verified commit, test count, migrations, and clean-tree state.
Milestone 1 — Configurable Procurement Gates A1–A6

This is the official next implementation milestone. This section states
intent and priority; the binding operational design — models, policy
versioning, migration rules, override architecture, audit taxonomy,
authorization, concurrency, tests, and live validation — is
`docs/MILESTONE_1_PROCUREMENT_GATES_CHARTER.md` (version 3 as of
2026-07-20, corrected via two documentation-only Charter Correction
Cycles: the first after an independent revalidation of version 1 found
twelve findings, REVAL-001–REVAL-012, all resolved in version 2; the
second after an independent revalidation of version 2 found ten further
findings — NF-1, REVAL-004-RESIDUAL, REVAL-005-RESIDUAL,
REVAL-008-RESIDUAL, REVAL-009-TRACE, REVAL-011-ENFORCEMENT, NF-2, NF-3,
NF-4, NF-7 — all resolved in version 3; version 3 itself not yet
independently revalidated). Where that Charter and this section differ on
mechanism, the Charter governs; this section's statement of intent is
unchanged by it.

Canonical gates:

A1 — Deal Established: parties, package roles, responsibilities, seller/exporter/logistics/quality authorities, visibility mode, and required approvals are explicit.
A2 — Technical Freeze: approved technical revision is frozen; critical role, term, specification, and visibility changes require Change Request approval and place the package on hold.
A3 — Production Evidence: production start/progress evidence satisfies the configured schema.
A4 — Quality Control: QC/inspection evidence is independently reviewed and approved.
A5 — Packing Verification: counts, packing, labels, and package/product traceability satisfy the configured schema.
A6 — Forwarder Handoff Readiness: required documents, verified package state, approvals, and transport-handoff evidence are complete.

Mandatory acceptance criteria:

Gate definitions and evidence schemas are configurable and versioned, not hard-coded to one supplier or project.
ProcurementPackage.Status remains separate from gate execution state.
Existing EvidenceBundle/EvidenceItem, Role Assignment, Capability Grant, Change Request, Risk Flag, and Gate Override mechanisms are reused.
Gate progression is blocked when evidence or approvals are incomplete; exceptions require explicit capability, written reason, required minimum evidence, expiry/revocation behavior, and audit history.
Uploader/verifier and preparer/approver separation remains enforced.
Critical changes after A2 create a hold and cannot silently mutate frozen terms.
Client projections expose only authorized verification assertions, never confidential source records.
No universal numeric confidence score is introduced.
Service operations are transactional, idempotent where needed, and concurrency-tested.
Automated tests cover positive, blocked, exception, revocation, expiry, change-request, cross-organization, information-absence, and audit-history scenarios.
Live validation exercises one controlled-transparency package and one controlled-confidentiality package through A1–A6.
Documentation, migrations, test count, commit, push, and clean working tree are recorded.
Milestone 2 — Internationalization Foundation
canonical es, en, zh-Hans locale switching;
locale stored separately from America/Santo_Domingo timezone;
static UI catalogs contain no private business data;
translations are provenance-linked derived artifacts and never replace originals;
authorization and tenancy remain identical across locale changes;
translation adapters remain optional and receive only authorized projections.
Milestone 3 — Authorization-Converged API and Output Integrations
package-aware API access for legitimate cross-organization participants;
centralized authorized search/autocomplete;
authorized exports and reports;
safe QR and notification projections;
negative tests for absence of factory identity, address, origin cost, markup, margin, rebates, and restricted finance data;
repository-wide endpoint authorization matrix.
Milestone 4 — Production Security, Audit, and Operations Hardening
dependency and secret scanning in CI;
backup encryption and restore verification;
shared-cache/global rate limiting where deployment topology requires it;
audit immutability/retention/tamper-evidence verification;
structured security logging and alerts;
health checks, metrics, error monitoring, RPO/RTO, and capacity baselines;
visual/device validation for field-critical screens.
Milestone 5 — Operational Payment Legs and Monetization Evidence
payment legs, obligations, approvals, evidence, status, split allocation, and restricted visibility;
monetization/cost streams with classification and authorization;
reconciliation to QuickBooks/approved accounting records;
no custody of money, escrow, lending, or accounting-source replacement;
legal-required information separated from commercially confidential information.
Milestone 6 — Standard Customs and Import Workflow

Proceed only after approved legal and operational requirements exist.

importer/exporter/broker responsibilities;
required documents and change controls;
holds, reviews, discrepancies, and approvals;
legal-required disclosure separated from client and supplier confidentiality;
special high-risk export structures remain blocked or manually reviewed until expressly approved.
Milestone 7 — Asset Passport Registry
create installed-asset identities only from accepted installation records;
preserve product, revision, package, evidence, plan/zone, inspection, acceptance, spare, warranty, and maintenance lineage;
support owner-safe projections without exposing confidential supply-chain data;
maintenance and replacement events append history rather than overwrite it.
Milestone 8 — Optional Automation and Channel Expansion

Only after measured demand:

OCR adapters;
automatic translation;
QuickBooks read/reconciliation integration;
generalized historical importer;
physical sample custody;
native mobile/offline sync;
sensors or GPS.

Blockchain anchoring and 24/7 concierge are excluded unless a new approved business case is produced.

4. Explicit non-roadmap items

The following are not authorized as near-term architecture work:

microservice rewrite;
rebuilding completed modules;
collapsing Building Family into Physical Building;
inventing apartment plans or room geometry;
replacing QuickBooks as financial source of truth;
custody of money or unapproved regulated finance;
generic evidence-confidence scoring without calibration;
blockchain, GPS, or concierge features without validated demand.
5. Exact next action

The execution hub must first obtain fresh terminal evidence and reconcile documentation. It must then define and run Milestone 1 — Configurable Procurement Gates A1–A6 against the acceptance criteria above.

This architecture reconciliation is complete.