# ADR 0006: MarketMatch Tenancy & Membership Kernel V1

- Status: Accepted
- Date: 2026-07-20
- Contract version: `marketmatch-tenancy-membership-v1`
- Default policy version: `marketmatch-tenancy-membership-policy-v1`

## Context

MarketMatch needs an explicit isolation boundary before future products combine
organizations, projects, Evidence, accountability records, or human work. The
current application authenticates usernames from `auth.json`, stores password
hashes and cookie sessions through the existing authentication manager, and
persists username ownership on Documents, Sessions, uploads, gallery records,
Tasks, TaskRuns, and other application data. Privileges are principal-level
booleans. Administrator behavior is current application behavior, not tenant
membership.

The repository has no authoritative Tenant, Organization, Membership,
invitation, tenant-product, tenant-workspace, or tenant-project registry.
Current folders are presentation organization for Sessions. The tool
“workspace” is a filesystem path boundary. Neither is a MarketMatch Workspace
Context. Existing uses of “tenant” generally describe username ownership
isolation; they do not establish a canonical Tenant identity.

ADRs 0001–0005 already provide Principal and Party references, exact
`AuthorityScope`, PRODUCT/WORKSPACE/PROJECT Operational Context records,
Evidence, Events, Decisions, Approvals, audits, Work Items, and Assignments.
Those contracts remain authoritative.

## Decision

Add a pure, importable Tenancy & Membership V1 module. Tenant is the data
isolation root. Organization is a distinct operational identity explicitly
bound to one Tenant. V1 permits more than one Organization in a Tenant but
never permits one Organization identity to belong to multiple Tenants. This is
an explicit containment rule, not identity equivalence.

The kernel performs no authentication, authorization grant evaluation,
persistence, provisioning, email, billing, entitlement, route, API, UI,
database, filesystem, network, or runtime action.

## Contract boundaries

### Authentication versus membership

Authentication proves the current application recognized a principal. It does
not create membership. A principal may authenticate without any membership.
No login row, cookie, session, API token, username, or email domain is adapted
into a Tenant or Membership.

### Membership versus authorization

Membership is a bounded principal-to-Tenant-and-Organization relationship. It
may be a precondition for protected operations but grants no capability.
Authority evaluation remains a subsequent and independent step. The membership
evaluator returns `capability_granted=False` even for an effective membership.

### Membership versus role assignment

Membership has no role or capability collection. ADR 0001 role assignments and
capability grants remain separate. Membership administration does not imply
approval, execution, cancellation, entitlement, or billing authority.

### Party or principal versus membership

A principal or Party identity remains authoritative and separate from
Membership. V1 Membership targets an existing `principal:` reference and
supports only literal `USER` membership because current repository evidence
does not justify fabricating Party, service, agent, workflow, or external-system
membership.

### Organization versus Tenant

Tenant and Organization use different records and ID prefixes. An Organization
has exactly one explicit Tenant ID. Multiple Organizations may share one Tenant
in V1, but cross-Tenant movement requires a new history record and cannot mutate
the prior identity. Similar-looking IDs never imply equivalence.

### Tenant ownership versus record ownership

Tenant owns the isolation boundary. `owner_party_id` remains the responsible or
record-owning Party dimension. Tenant identity and record ownership are not
collapsed.

### Tenant identity versus display name

Canonical `tenant1:`, `org1:`, `mem1:`, `memtr1:`, and `tbind1:` identities are
language-neutral. Labels and names are optional presentation fields and never
identity, containment, membership, or audit authority.

## Tenant and Organization records

Tenant status is literal `ACTIVE`, `SUSPENDED`, `RESTRICTED`, or `DEACTIVATED`.
Organization status is `ACTIVE`, `SUSPENDED`, or `DEACTIVATED`. Status does not
grant membership or capability. Records contain canonical creation time,
classification, policy, creator, and Authority provenance but no address,
contacts, tax IDs, bank data, billing plan, entitlement, secrets, connection
strings, or arbitrary metadata.

## Membership and lifecycle

Membership contains exact Tenant, Organization, principal, `USER` kind,
initial status, canonical creation/effective/expiry times, sponsor, optional
superseded Membership, policy, classification, scope, and Authority provenance.
Invitation versus activation remains explicit: `INVITED` grants nothing.

Lifecycle states are `INVITED`, `ACTIVE`, `SUSPENDED`, `REVOKED`, `EXPIRED`,
`DECLINED`, `INVALIDATED`, and `SUPERSEDED`. Transitions are separate `memtr1:`
records with exact prior/next status, actor, chronology, scope, policy,
capability-specific Authority provenance, safe reason, and optional Event ID.

Suspension versus revocation remains distinct. Suspension can be followed by an
explicit reactivation transition. Revocation preserves history and terminates
the relationship. Revocation versus expiration also remains distinct: expiry
is caused by the canonical validity boundary, not a revocation assertion.
Immutable membership history means no transition rewrites the Membership.
An explicit replacement makes the earlier record `SUPERSEDED` only in the
validated as-of view; both source records remain unchanged. Future transitions
and replacements do not take effect early.

## Tenant Context

`TenantContext` is a pure exact isolation contract containing Tenant,
Organization, canonical PRODUCT/WORKSPACE/PROJECT Context IDs, exact committed
`AuthorityScope`, owner, classification, and policy/version. Product, workspace,
project, and owner dimensions are mandatory; missing dimensions are not
wildcards. Locale and timezone do not alter these facts.

## Product, Workspace, and Project binding

`ContextBinding` associates one canonical Operational Context with a Tenant and
Organization. PRODUCT has no parent, WORKSPACE parents to PRODUCT, and PROJECT
parents to WORKSPACE. Context type, parent, scope, owner, classification, and
policy must agree with ADR 0003. Aliases, folder names, filesystem paths,
display labels, hostnames, and database rows cannot become Context identities.

A Context cannot have two active incompatible Tenant bindings. Rebinding uses
explicit supersession and preserves prior records. Binding grants neither
membership nor Authority nor product entitlement.

## Membership evaluation

Evaluation requires exact principal, Tenant, Organization, policy, literal
effective `ACTIVE` state, and canonical time. Invitation, suspension,
revocation, expiration, future effectiveness, malformed history, wrong scope,
wrong policy, or duplicate active membership fails closed using stable reason
codes. The result is factory-sealed, always grants no capability, and cannot be
caller-supplied as effective. Conflicts are reported rather than resolved by
newest-record selection.

## Authority integration

The kernel imports ADR 0001 decisions and projector. Tenant creation,
Organization binding, Membership invitation/activation, lifecycle transitions,
Context binding, and projection use exact resource ID, actor, scope,
capability, policy, and version bindings. Viewing does not imply invitation;
invitation does not imply activation; activation does not create roles or
entitlements. Administrator status is not universal cross-Tenant authority.

## Operational Context integration

PRODUCT, WORKSPACE, and PROJECT identities and hierarchy remain owned by ADR
0003. Context visibility does not establish membership. Membership never
reveals hidden Context fields or aliases. Tenant binding does not change
Context identity.

## Evidence integration

Evidence remains owned by ADR 0002. Visible Membership does not authorize
Evidence, and visible Evidence does not establish Membership. Exact source
scope, owner, classification, and policy compatibility is required before any
tenant-scoped combination. No raw Evidence is embedded.

## Truth & Accountability integration

Membership transitions may reference committed Events while remaining separate
records. Truth Decisions and Approvals can be checked as tenant-compatible
sources but do not substitute for an exact Authority decision and are not
embedded in Membership V1. Approval-driven membership policy remains future
work. An audit does not grant Membership or Authority. Visibility of Membership
does not authorize linked accountability records.

## Work Orchestration integration

Work Items and Assignments remain ADR 0005 records. Membership-required human
work requires exact Tenant Context, project Context, scope, policy, member
principal, and Assignment executor compatibility. Membership alone does not
authorize assignment or execution. Suspended, revoked, expired, or cross-Tenant
members cannot satisfy the precondition. Work status never becomes membership
status.

## Collection and containment validation

Pure bounded validation checks unique typed IDs, Tenant/Organization
containment, Membership and transition chronology, exact lifecycle, referenced
Events, Context graph and types, binding parentage, scope, owner,
classification, policy, supersession, conflicting active Memberships, and
conflicting active Context bindings. Ordering is deterministic. Conflicts are
never silently merged or automatically resolved.

## Safe projection

Tenant, Organization, Membership, and Context Binding projectors are flat-only.
They require authentic Authority decisions bound to exact record, scope,
policy, and version, then return new scalar mappings. Hidden member identity,
Organization name, sponsor, Context linkage, owner, and unknown fields remain
omitted. Hostile mappings and caller-supplied effective state reject with fixed
value-free errors.

## Safe audit

Safe tenancy audit records use `taud1:` IDs and bounded record ID, Tenant ID,
action, outcome, reason, scope reference, policy/version, timestamp, and
optional correlation code. They contain no passwords, credentials, invitation
tokens, emails, cookies, sessions, bearer tokens, headers, request bodies,
human names, addresses, tax IDs, bank details, supplier/factory identity,
costs, margins, raw Evidence, arbitrary metadata, or exception text. Audit
never grants Membership or Authority.

## Derived tenant views

Pure restrictive views cover active, suspended, and expiring Memberships;
Tenant products, workspaces, and projects; and unresolved Membership conflicts.
Every source requires authorization. Denied sources cannot be silently omitted.
Tenant, scope, policy, classification, and visible fields inherit restrictively.
Outputs contain canonical record IDs in deterministic order, not hidden member
identities. A validated collection is sealed to its exact as-of timestamp, so a
stale state snapshot cannot be reused at another time. A view is not Membership,
Authority, entitlement, or billing truth.

## Locale and timezone independence

Locale changes presentation labels only. Timezone changes display only. Neither
changes Tenant, Organization, Membership, Context binding, state, validity,
scope, Authority, containment, or audit meaning. Canonical timestamps are UTC.

## Current implementation versus target state

Current authentication, cookies, administrator behavior, privileges, username
ownership, folders, filesystem workspaces, Documents, Library, Capture,
uploads, media, STT, analysis, Tasks, TaskRuns, scheduler, event bus, and
background jobs remain authoritative and unchanged. The target state may add
durable tenancy only after identity, scope, rollback, non-fabrication, and data
migration are separately proven.

## Compatibility adapters

No adapter is approved. `adapt_authenticated_principal` returns
`UNSUPPORTED_ADAPTER`. Login does not imply Membership; administrator status
does not imply cross-Tenant Membership; owner does not imply Tenant owner;
email domain does not imply Organization; folder name does not imply Workspace.

## Persistence and production deferral

V1 adds no Tenant, Organization, Membership, invitation, or Context-binding
table; no migration; no registry; no browser or JSON storage; no route; no API;
no UI; no provisioning; no deletion; no runtime-data write. Existing routes do
not import the kernel.

Billing, subscription, entitlement enforcement, regional placement, dedicated
deployment, invitation email/token generation, SSO, SCIM, service-account
membership, and identity-provider integration are deferred.

## Stable code glossary

- IDs: `tenant1`, `org1`, `mem1`, `memtr1`, `tbind1`, `taud1`.
- Tenant status: `ACTIVE`, `SUSPENDED`, `RESTRICTED`, `DEACTIVATED`.
- Organization status: `ACTIVE`, `SUSPENDED`, `DEACTIVATED`.
- Membership kind: `USER`.
- Membership status: `INVITED`, `ACTIVE`, `SUSPENDED`, `REVOKED`, `EXPIRED`,
  `DECLINED`, `INVALIDATED`, `SUPERSEDED`.
- Binding status: `ACTIVE`, `SUPERSEDED`, `INVALIDATED`.
- Audit actions: `tenant.create`, `organization.bind`, `membership.invite`,
  `membership.activate`, `membership.suspend`, `membership.revoke`,
  `membership.expire`, `membership.evaluate`, `context.bind.tenant`,
  `record.reject`, and `tenant.cross_tenant.deny`.
- Audit outcomes: `allowed`, `denied`, `created`, `recorded`, `detected`, and
  `rejected`. Unknown audit actions, outcomes, and reason codes fail closed.

## Reason-code glossary

Stable reasons include `EFFECTIVE`, `NO_MEMBERSHIP`, `WRONG_TENANT`,
`WRONG_ORGANIZATION`, `WRONG_PRINCIPAL`, `INACTIVE`, `NOT_YET_EFFECTIVE`,
`EXPIRED`, `TENANT_INACTIVE`, `ORGANIZATION_INACTIVE`, `POLICY_MISMATCH`,
`CONFLICT`, `MALFORMED`, `TENANT_CONFLICT`, `POLICY_CONFLICT`,
`DUPLICATE_ACTIVE_MEMBERSHIP`, `CONFLICTING_CONTEXT_BINDING`,
`SOURCE_NOT_AUTHORIZED`, and `WORK_TENANT_CONFLICT`.

## Fictional examples

1. Fictional Tenant Alpha contains Fictional Organization Alpha while their
   canonical IDs remain distinct.
2. An authenticated fictional user with no Membership receives
   `NO_MEMBERSHIP`; authentication does not create a role.
3. An invited user cannot see a project. After explicit activation, Membership
   may satisfy a precondition, but a separate Authority decision is still
   required.
4. A project bound to another Tenant cannot be combined into a report or Work
   Assignment, even when its display name matches.
5. Suspending a Membership preserves activation history. Reactivation creates
   another transition rather than rewriting the suspension.

## Limitations

This milestone does not persist or provision Tenants, send invitations, migrate
users, alter ownership, implement billing or entitlement, integrate SSO/SCIM,
create service membership, expose routes or UI, deploy regions, or determine
legal or commercial responsibility.
