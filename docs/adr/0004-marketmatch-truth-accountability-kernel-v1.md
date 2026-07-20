# ADR 0004: MarketMatch Truth & Accountability Kernel V1

- Status: Accepted
- Date: 2026-07-20
- Contract version: `marketmatch-truth-accountability-v1`
- Default policy version: `marketmatch-truth-accountability-policy-v1`

## Context

MarketMatch needs a reusable vocabulary for recording what happened, what was
decided, what was approved, and what may safely be audited. The committed
Authority & Commercial Visibility Kernel, Evidence Core, and Operational
Context Kernel already define authorization, artifact integrity, and canonical
operational subjects. None of them is an event, decision, approval, workflow,
or audit ledger.

The current application has calendar events, activity dispatch, task and
notification status, application logs, document versions, uploads, Capture,
media attestation, speech-to-text, and analysis results. Those mechanisms do
not consistently carry an authoritative MarketMatch scope, actor, Evidence
reference, Operational Context reference, policy, and immutable lineage. They
therefore remain current application behavior and are not silently promoted to
official Truth & Accountability records.

## Decision

Introduce a pure Python contract module with no route, database, filesystem,
network, model, browser-storage, notification, or workflow integration. The
kernel imports the three committed kernels and supplies factory-only immutable
records, collection validation, restrictive projection, safe audit builders,
approval-requirement evaluation, and derived accountability views.

### Separate identities and meanings

An `OperationalEvent` records that something was observed, reported, created,
updated, received, corrected, invalidated, or system-generated. Its existence
does not prove the statement true and does not authorize a transition.
Facts and conclusions therefore remain distinguishable: an Event is a report
of occurrence, while a Decision is an explicitly attributed conclusion.

An Evidence item is an artifact or observation. An Event may cite its Evidence
ID, but does not contain raw Evidence, bytes, transcript text, document text, or
Evidence provenance. Evidence verification and attestation are not Approval.

An Operational Context is the canonical subject, such as a project or asset.
An Event targets or references that subject without replacing its ID. Context
hierarchy and visibility do not grant decision or approval authority.

A `DecisionRecord` is an explicit conclusion or selected course of action. It
is neither its source Event nor its Evidence. It records an authorized maker,
literal outcome and status, target, governing policy, Authority provenance,
and explicit basis references. It does not mutate the target.

An `ApprovalRecord` is a separate authorized response. It never rewrites its
target and does not mean legal acceptance. Missing, silent, truthy, numeric,
translated, expired, withdrawn, rejected, invalidated, malformed, wrong-scope,
or wrong-policy input is not approval.

A `SafeAuditRecord` is a bounded coded account of an attempt or result. It is
not application log prose, raw business data, an authorization grant, or a
cryptographic ledger.

### Identifier contract

The four identifiers have disjoint language-neutral prefixes:

- Event: `evt1:<kind>:<opaque>`
- Decision: `dec1:<kind>:<opaque>`
- Approval: `apr1:<kind>:<opaque>`
- Audit: `aud1:<kind>:<opaque>`

They are not Evidence IDs, Context IDs, content digests, database row numbers,
display strings, names, email addresses, addresses, PO/BL/container numbers,
or commercial values. ASCII bounds, prefix and opaque-content checks, path and
control rejection, and sensitive-token rejection make them safe references.
They do not carry authorization.

### Actors and Authority

`ActorReference` uses literal `HUMAN`, `SYSTEM`, `EXTERNAL_SYSTEM`, or `AGENT`
codes. Identity is not authority. A real generator identifier and version are
mandatory for `AGENT` and forbidden for other actor kinds, so human or system
records cannot acquire fabricated model provenance. Credentials, cookies,
sessions, tokens, headers, and request bodies are outside the contract.

Record factories accept authentic decisions issued by the Authority Kernel.
The decisions are bound to the exact actor, record ID, resource type, scope,
policy, version, and capability. V1 distinguishes `event.create`,
`decision.issue`, and bounded `approval.*` capabilities. Visibility does not
imply decision authority; decision authority does not imply approval authority.
The kernel contains no duplicate scope or capability evaluator.

### Events and chronology

The generic V1 Event taxonomy is deliberately small:

`OBSERVED`, `REPORTED`, `CREATED`, `UPDATED`, `RECEIVED`, `CORRECTED`,
`INVALIDATED`, and `SYSTEM_GENERATED`.

Procurement, shipment, installation, inspection, acceptance, and BTC states
are not generic Event types. Workflow states and translated labels reject.

`occurred_at`, optional `received_at`, and `recorded_at` are distinct,
timezone-aware timestamps normalized to UTC. V1 requires:

`occurred_at <= received_at <= recorded_at`

when receipt is present, otherwise `occurred_at <= recorded_at`. Historical
imports that violate this ordering require a future explicit adapter and are
not guessed. A correction referencing a source cannot predate the source's
recording.

### Immutable correction and lineage

Correction, supersession, withdrawal, and invalidation use explicit directional
`AccountabilityLineage` records. The new record points to the historical record.
Self-relations, missing references, chronology inversions, scope/policy
conflicts, and iterative lineage cycles fail closed. Prior Events, Decisions,
and Approvals remain in the validated collection. No lifecycle operation
deletes or mutates history.

Decision statuses are `PROPOSED`, `ISSUED`, `SUPERSEDED`, `WITHDRAWN`,
`INVALIDATED`, and `EXPIRED`. Approval statuses are `APPROVED`, `REJECTED`,
`CONDITIONAL`, `WITHDRAWN`, `EXPIRED`, and `INVALIDATED`. Invalidation is not
rejection; expiry is not withdrawal. Conflicting active Decisions and
contradictory Approvals are detected, not automatically resolved.

### Decision basis

Decision basis is a bounded tuple of Event, Evidence, Operational Context,
prior Decision, and Approval IDs plus the policy/version. Non-proposed Decisions
require at least one basis reference. Validation requires each referenced
record to exist and match exact tenant dimensions and policy. A basis remains
separately authorized and does not prove the Decision correct. No confidence
or model output is fabricated.

### Approval requirements

`ApprovalRequirement` is a pure predicate, not a workflow. An empty slot tuple
means explicitly that no Approval is required. Otherwise each `ApprovalSlot`
requires a literal capability and count. Multiple capabilities may be required.
Duplicate records do not multiply an approver; one approver cannot satisfy
distinct slots unless the requirement explicitly permits it. Evaluation is
deterministic and returns only stable reason codes. Protected notes and source
Evidence are never returned.

### Evidence and Operational Context integration

Records store only Evidence IDs and canonical Operational Context IDs. They do
not embed Evidence bytes or Context aliases. Collection validation calls the
committed contract validators, requires references to exist at the pure
boundary, and enforces matching organization, product, workspace, project,
owner, and visibility policy. Evidence and Context remain immutable and their
visibility remains authoritative. A visible Event, Decision, Approval, or
Context never authorizes linked Evidence.

### Projection

Event, Decision, and Approval projection is flat-only. It requires an authentic
Authority decision bound to the exact record ID, resource type, scope, policy,
and version. The result is a new scalar-only mapping containing only permitted
fields. Raw content, nested objects, Evidence IDs, Context aliases, protected
actors, and protected reason/condition codes are absent unless a bounded scalar
field is explicitly authorized. Hostile mappings produce fixed error codes;
their values or exceptions are not serialized. Mapping inputs cannot forge
computed reference counts.

Authorization precedes record projection, Evidence or Context access,
localization, search, retrieval, AI, export, notification, and delivery.

### Safe audit boundary

Safe audit records may contain only bounded record IDs, action/target/outcome
and reason codes, policy/version, a safe scope reference, canonical timestamp,
and an optional safe correlation ID. Actor kind/reference appears only when an
authentic allowed decision explicitly projects it and binds the principal.

Audit never contains raw Evidence, Event descriptions, Decision/Approval notes,
conditions, transcripts, document content, filenames, proper names, aliases,
addresses, supplier/factory identity, commercial terms, costs, margins,
credentials, cookies, sessions, tokens, headers, request bodies, arbitrary
metadata, or exception text. Newlines and control characters reject. An Audit
Record never authorizes an action.

### Collection consistency

The pure collection validator applies explicit bounds and checks unique IDs,
reference existence, exact scope/policy compatibility, chronology, lineage
cycles, status/lineage consistency, duplicate approvals, contradictory active
approvals, and conflicting active Decisions. Cycle traversal is iterative.
Business conflicts are reported with stable codes and are never resolved by
the kernel.

### Derived accountability views

A derived view requires a non-empty, duplicate-free set of valid and authorized
Events, Decisions, Approvals, Evidence, or Context records. Denied or malformed
sources block the result rather than being omitted. Incompatible tenancy,
scope, owner, or policy fails closed. Authority Kernel inheritance preserves
the most restrictive classification and intersection of visible fields. The
view is marked derived and is neither a Decision nor an Approval. `AGENT`
generation requires real generator provenance.

## Current state and target state

Current authoritative behavior remains in the existing application models and
routes. Document versions are useful immutable history; Capture, upload, media,
STT, and analysis create content or results; authentication and activity code
create operational logs. Those rows lack enough common authoritative scope,
actor, Context, Evidence, policy, or semantic provenance for an honest generic
adapter in this milestone.

The V1 target is importable pure contracts and validation. A future target may
add durable registries and product/domain adapters only after identity, scope,
ownership, timestamps, rollback, and non-fabrication are proven.

## Compatibility adapters

No production compatibility adapter is introduced. Converting current logs,
calendar rows, task states, notifications, database row IDs, or analysis text
would fabricate at least one of actor, scope, Context, Evidence, event time, or
official semantics. Existing systems remain authoritative and unchanged.

## Persistence and integration boundary

V1 adds no database model or migration, durable Event/Decision/Approval/Audit
registry, event store, event sourcing, cryptographic audit chain, materialized
state, task, notification, workflow, route, public API, UI, browser storage, or
JSON registry. It performs no runtime-data write. No current endpoint imports
the kernel.

## Locale and timezone

Codes, IDs, references, policy, chronology, authority, and audit meaning are
language-neutral. Locale may translate future display labels but not codes.
Timezone may format a canonical UTC timestamp but does not alter chronology.

## Stable code glossary

- Actor kinds: `HUMAN`, `SYSTEM`, `EXTERNAL_SYSTEM`, `AGENT`.
- Record kinds: `EVENT`, `DECISION`, `APPROVAL`, `AUDIT`.
- Event types: the eight codes listed above.
- Decision types: `CONCLUSION`, `COURSE_OF_ACTION`, `CLASSIFICATION`,
  `EXCEPTION`, `LIFECYCLE`.
- Decision outcomes: `SELECTED`, `DECLINED`, `DEFERRED`, `INCONCLUSIVE`.
- Decision statuses: `PROPOSED`, `ISSUED`, `SUPERSEDED`, `WITHDRAWN`,
  `INVALIDATED`, `EXPIRED`.
- Approval types: `AUTHORIZATION`, `REVIEW`, `EXCEPTION`.
- Approval statuses: `APPROVED`, `REJECTED`, `CONDITIONAL`, `WITHDRAWN`,
  `EXPIRED`, `INVALIDATED`.
- Target types: `EVENT`, `DECISION`, `APPROVAL`, `EVIDENCE`,
  `OPERATIONAL_CONTEXT`.
- Lineage: `CORRECTS`, `SUPERSEDES`, `WITHDRAWS`, `INVALIDATES`.
- Requirement results: `SATISFIED`, `UNRESOLVED`, `CONFLICT`, `INVALID`.
- Audit outcomes: `ALLOWED`, `DENIED`, `COMPLETED`, `REJECTED`.

Stable failure reasons include `INVALID_IDENTIFIER`, `INVALID_ACTOR`,
`INVALID_SCOPE`, `INVALID_TIMESTAMP`, `INVALID_EVENT`, `INVALID_DECISION`,
`INVALID_APPROVAL`, `INVALID_LINEAGE`, `INVALID_AUTHORITY_DECISION`,
`INVALID_PROJECTION`, `INVALID_AUDIT`, `INVALID_VERSION`, `MISSING_REFERENCE`,
`DUPLICATE_IDENTIFIER`, `SCOPE_CONFLICT`, `POLICY_CONFLICT`,
`VISIBILITY_CONFLICT`, `CHRONOLOGY_CONFLICT`, `LINEAGE_CYCLE`, `CONFLICTING_DECISIONS`,
`CONFLICTING_APPROVALS`, `DUPLICATE_APPROVAL`, `INVALID_REQUIREMENT`,
`SOURCE_NOT_AUTHORIZED`, `SOURCE_SCOPE_CONFLICT`,
`SOURCE_POLICY_CONFLICT`, and `COLLECTION_LIMIT_EXCEEDED`.

## Fictional examples

An inspector for fictional Project Alpha reports an observation at
`ctx1:project:alpha`. Event `evt1:reported:alpha` cites
`ev1:document:alpha`. The Event says only that the report occurred; it does not
declare the observation correct.

An authorized principal issues `dec1:course:alpha` with the Event as explicit
basis. Another authorized principal records `apr1:review:alpha`. The three
records remain separate. A later withdrawal is a new Approval plus lineage; the
original Approval remains history.

A viewer permitted to see only the Event ID and type receives those scalar
fields. The linked document and reporter identity remain hidden. A safe audit
record says that projection was allowed without copying the report text.

## Consequences and limitations

The contracts provide a conservative foundation and reject incomplete legacy
records rather than fabricate truth. The kernel does not determine legal truth,
liability, blame, quality acceptance, or contractual acceptance. They do not
implement BTC closure, procurement, shipment, installation, inspection,
acceptance, Asset Passport, billing, QuickBooks, orchestration, autonomous
actions, LangGraph, DeerFlow, or authoritative AI decisions.
