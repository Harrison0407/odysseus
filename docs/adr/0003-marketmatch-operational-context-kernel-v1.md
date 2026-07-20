# ADR 0003: MarketMatch Operational Context Kernel V1

- Status: Proposed contract foundation
- Date: 2026-07-20
- Scope: Pure operational identity, graph, Authority, and Evidence contracts

## Context and repository evidence

The repository contains accepted ADR 0001 for the Authority and Commercial
Visibility Kernel and accepted ADR 0002 for Evidence Core. Those records are
the governing architectural intent available here. The separately named
MarketMatch Master Context, Foundation and Core Roadmap, Platform Vision and
Architecture Blueprint, capability/current-state documents, and DT Beach
roadmaps are absent from this repository snapshot. Their contents are not
inferred.

Current persisted application concepts are not an Operational Context graph.
The database contains owner-stamped Documents, immutable DocumentVersion text
snapshots, chat Sessions with optional filesystem folder names, uploads,
gallery media, and other application records. The word `workspace` currently
also identifies a filesystem tool boundary. None of those records proves a
durable MarketMatch organization/product/workspace/project registry, DT Beach
building/unit hierarchy, asset registry, procurement model, shipment/container
model, warehouse model, installation/inspection workflow, or supersession
ledger. Existing row IDs and names therefore cannot safely be promoted to
canonical operational identity or assigned future tenancy.

Authority V1 already defines `AuthorityScope` with organization, product,
workspace, project, resource, and owner dimensions. It intentionally uses
exact non-global scope evaluation because no hierarchy registry exists.
Evidence V1 already defines independent Evidence IDs, immutable records,
restrictive visibility inheritance, and safe flat projection. Operational
Context must reuse both foundations and must not reinterpret either one.

## Decision

Add `src/marketmatch_operational_context.py`, an importable pure-contract
kernel. It introduces no compatibility adapter because discovery found no
current record that can be mapped without fabricating canonical identity,
organization/product/project scope, or operational meaning. It changes no
route, API, UI, database model, migration, authentication rule, Evidence
record, file, or runtime datum.

### Operational Context purpose

Operational Context identifies a bounded operational subject and its explicit
place in a validated graph. It can answer what a context is, its exact Authority
scope, parent, non-hierarchical associations, responsible Party reference,
aliases, revision, and Evidence associations. It does not decide workflow
completion, quality acceptance, contractual fault, liability, or truth.

### Canonical identity versus name and alias

A canonical ID has the bounded ASCII form
`ctx1:<namespace>:<opaque-reference>`. It is language-neutral, path-safe,
control-free, excludes sensitive commercial/credential terms, and is distinct
from Evidence IDs, display names, aliases, database row numbers, and revisions.
A display-name or alias change never changes identity. A name or alias equal to
the canonical ID is rejected rather than silently becoming identity.

Aliases are separate immutable values. V1 supports only the repository-grounded
namespaces `LEGACY_RESOURCE_ID`, `EXTERNAL_SYSTEM_ID`, and `PROJECT_CODE`.
Their exact spelling is preserved and never translated. Comparison is
deterministic and case-insensitive within the boundary of exact organization,
product, workspace, project, and namespace. A collision inside that boundary
fails closed. An alias grants no authority and is not audit-safe by default.

### Context is not Party

A person, company, supplier, factory, installer, architect, inspector, or
customer remains a `PartyReference` or authenticated principal. A Context may
carry an optional owner/responsible Party reference, but the Party is not
converted into an operational subject. V1 consequently does not introduce an
`ORGANIZATION` context type that could be confused with a company Party.

### Context is not Evidence

A Context is the subject. A Document, photograph, recording, transcript,
measurement, observation, or system result is Evidence. Evidence associations
contain only canonical Context and Evidence IDs plus a stable purpose. They do
not embed content, integrity, provenance, or authorization and never mutate or
delete Evidence.

### Context type is not workflow state

The closed V1 types are `PRODUCT`, `WORKSPACE`, and `PROJECT`. These are the
smallest operational subjects proven by the committed Authority scope contract.
No DT Beach site, building, level, unit, room, area, asset, package, purchase
order, shipment, container, warehouse, delivery, installation, or inspection
type is claimed because no authoritative repository model or documentation was
found. Ordered, received, installed, inspected, accepted, and similar workflow
states are not context types.

The contract version is `marketmatch-operational-context-v1`. Unknown versions,
including unknown major versions, fail closed. Adding future types requires a
reviewed contract version; it is not an arbitrary string extension point.

### Scope compatibility

Every Context imports an exact non-global `AuthorityScope`, requires explicit
organization and product dimensions, binds `scope.resource_id` to its canonical
Context ID, and validates type-specific workspace/project dimensions.
`PRODUCT` has neither workspace nor project, `WORKSPACE` has a workspace and no
project, and `PROJECT` has a project. Missing dimensions never become general
wildcards.

Authority remains the only capability/grant evaluator. The Context kernel uses
Authority's own visibility-inheritance contract to validate scopes and applies
additional graph-specific compatibility rules. It does not copy or replace
`evaluate_authorization`.

### Hierarchy and association

Hierarchy is the explicit `parent_context_id` on an immutable record.
Parent/child type combinations are supplied by a closed `HierarchyPolicy`; the
kernel has no universal product hierarchy. A reviewed policy may, for example,
allow fictional `PRODUCT -> WORKSPACE -> PROJECT` edges. Unknown combinations,
missing parents, self-parenting, cross-organization/product scope, incompatible
workspace/project refinement, and cycles reject. Graph and ancestry validation
are iterative and bounded.

Non-hierarchical relationships are separate. `ASSOCIATED_WITH` is symmetric
and endpoint-normalized. `REPLACES` and `SUPERSEDES` are directional and
cycle-checked. Exact duplicates collapse deterministically; conflicting
directional declarations fail. None means ordered, delivered, installed,
accepted, legally responsible, or factually correct.

### Graph validation and ancestry

`validate_context_graph` validates immutable record integrity, unique Context
IDs, explicit parent existence, hierarchy policy, scope compatibility, alias
collision boundaries, relationship endpoints, duplicate/conflicting edges,
and iterative parent/supersession cycles. V1 accepts at most 2,048 Contexts,
8,192 relationships, and 64 aliases per Context.

`context_ancestry` returns only a deterministic root-to-subject tuple of
canonical IDs. It never exposes names or aliases and is not an authorization
decision. Missing nodes, malformed graphs, cycles, and paths of 512 or more
nodes fail closed.

### Versioning and supersession

`revision` is a positive literal integer distinct from canonical identity.
Revision two and above declare exactly the preceding revision. A pure
transition validator requires the same identity, type, scope, monotonic time,
and consecutive revisions, leaving the prior immutable object intact.

Replacement or supersession between different identities is an explicit
directional relationship. It never mutates, merges, deletes, or silently splits
the earlier identity. V1 does not implement event sourcing.

### Authority before projection

The required order remains principal resolution, authoritative scope
validation, authorization, flat field projection, Context access, Evidence
access, then localization/search/RAG/AI/export/delivery. Context projection
accepts only an authentic Authority decision bound to resource type
`operational_context`, the exact Context ID and scope, and the exact policy ID
and version. Forged or mutated decisions and records fail closed.

Projection is flat-only and returns a new mapping of authorized immutable
scalars. Unknown fields, nested data, aliases, raw Evidence, and mutable values
are omitted. Display name, owner, scope dimensions, alias count, and Evidence
count remain separately authorizable fields. Locale and display timezone never
participate in identity, graph validation, or authorization.

Derived summaries use Authority's `inherit_derived_visibility`. Every source
must be authentic and allowed; visible fields are intersected, the most
restrictive classification is retained, and exact source scope/policy
conflicts reject. A denied or malformed source is never silently omitted.

### Evidence association and visibility

An `EvidenceAssociation` links one Context ID to one Evidence ID with the stable
purpose `ASSOCIATED_WITH`. Validation requires both endpoints, exact
organization/product/workspace/project/owner scope compatibility, and matching
policy provenance. Linking does not authorize Evidence; removing the link does
not delete Evidence.

A visible Context never automatically reveals Evidence. A bounded linked count
is returned only when an authentic Context decision explicitly projects
`linked_evidence_count` and every linked Evidence record and matching Evidence
decision is supplied and validated. A denied Evidence decision may still
support existence count under that explicit Context field policy, but no
Evidence ID, metadata, content, or protected value is returned.

### Safe projection and audit

The projector is flat-only and value-omitting, consistent with Authority V1.
It does not recursively project aliases or external references. Hostile mapping
failures become a fixed error without rejected values.

The safe audit summary contains only Context ID/type, action, allow/deny,
reason, policy/version, the canonical Context ID as safe scope reference, UTC
timestamp, and optional bounded correlation ID. It excludes hidden names,
aliases, addresses, supplier/factory identities, commercial terms, costs,
margins, credentials, cookies, sessions, arbitrary metadata, Evidence IDs,
Evidence content, and exception values. It is an access-event summary, not a
workflow or legal conclusion.

## Stable-code glossary

| Family | Codes |
|---|---|
| Contract | `marketmatch-operational-context-v1` |
| Policy | `marketmatch-operational-context-policy-v1` |
| Context type | `PRODUCT`, `WORKSPACE`, `PROJECT` |
| Alias namespace | `LEGACY_RESOURCE_ID`, `EXTERNAL_SYSTEM_ID`, `PROJECT_CODE` |
| Relationship | `ASSOCIATED_WITH`, `REPLACES`, `SUPERSEDES` |
| Evidence-association purpose | `ASSOCIATED_WITH` |
| Core failures | `INVALID_IDENTIFIER`, `INVALID_TYPE`, `INVALID_SCOPE`, `INVALID_RECORD`, `INVALID_VERSION` |
| Graph failures | `INVALID_HIERARCHY`, `MISSING_PARENT`, `HIERARCHY_CYCLE`, `GRAPH_LIMIT_EXCEEDED`, `DUPLICATE_CONTEXT` |
| Relationship failures | `INVALID_RELATIONSHIP`, `RELATIONSHIP_CONFLICT` |
| Alias failures | `INVALID_ALIAS`, `ALIAS_COLLISION` |
| Evidence failures | `INVALID_EVIDENCE_ASSOCIATION`, `EVIDENCE_SCOPE_CONFLICT`, `EVIDENCE_POLICY_CONFLICT`, `EVIDENCE_NOT_AUTHORIZED` |
| Authority/projection failures | `INVALID_AUTHORITY_DECISION`, `INVALID_PROJECTION`, `SOURCE_NOT_AUTHORIZED`, `SOURCE_SCOPE_CONFLICT`, `SOURCE_POLICY_CONFLICT` |
| History/audit failures | `INVALID_REVISION`, `INVALID_ANCESTRY`, `INVALID_AUDIT` |

## Generic fictional examples

1. A fictional product Context parents a workspace Context, which parents a
   project Context under an explicit hierarchy policy. Their canonical path is
   IDs only; translated display names do not alter it.
2. Two fictional project Contexts have the same external code in different
   namespaces. They do not collide. The same namespace/value inside the same
   exact scope does collide and requires review.
3. A fictional project has one associated Evidence document. A viewer allowed
   to see only the project's existence sees no Evidence. A separate controlled
   policy may reveal count `1` without revealing the Evidence ID or content.
4. A fictional report combines two Contexts. It fails when their project scope
   or policy differs, and it cannot omit the denied Context to broaden access.
5. A corrected project name is revision two of the same identity. A replacement
   project instead receives a new ID and an explicit `REPLACES` relationship.

## Current state versus target state

V1 provides pure immutable/effectively immutable identities, scoped records,
explicit hierarchy policy, graph and ancestry validation, aliases,
relationships, revision checks, Authority projection/inheritance, Evidence
associations/count boundaries, and safe audit summaries. No production system
uses these contracts yet.

The target platform may later add reviewed durable Context/type registries,
product-specific hierarchies, DT Beach adapters, durable Evidence links,
search/export projections, Asset Passport behavior, and operational workflows.
Those are future capabilities, not current implementation.

## Compatibility adapters

No adapter is implemented in V1. Current Documents, Sessions/folders, uploads,
Gallery records, and database row IDs lack sufficient authoritative operational
identity and scope. Guessing would convert names or internal row identifiers
into canonical IDs and fabricate organization/project ownership. A future
adapter requires a separately reviewed, authoritative source mapping.

## Persistence and integration deferral

V1 explicitly defers durable Context, hierarchy, association, alias, Evidence
link, revision, and graph-audit registries. It adds no database table, migration,
JSON registry, browser storage, public API, route import, debug endpoint, UI, or
runtime-data write. Existing routes remain authoritative and unchanged.

## Limitations

- no DT Beach building, level, unit, room, area, asset, procurement, shipment,
  container, warehouse, delivery, installation, or inspection model;
- no procurement, shipment, installation, inspection, acceptance, issue, or
  financial workflow;
- no Asset Passport implementation or UI;
- no durable registry, graph persistence, event sourcing, or migration;
- no production route or public API integration;
- no recursive nested projection;
- no alias-based lookup or authority;
- no legal truth, fault, acceptance, or liability determination;
- no compatibility mapping from current row IDs or names.

## Consequences

Future MarketMatch services have a bounded context-graph contract that composes
with the committed Authority and Evidence kernels without changing current
authentication, Documents, Library, Capture, media, STT, local analysis,
locale preferences, APIs, UI, or runtime data.
