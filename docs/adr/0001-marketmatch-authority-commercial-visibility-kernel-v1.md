# ADR 0001: MarketMatch Authority and Commercial Visibility Kernel V1

- Status: Accepted contract foundation
- Date: 2026-07-19
- Scope: Pure authority contracts and compatibility with current authentication

## Context

MarketMatch currently authenticates browser users with the Odysseus cookie and
session manager. Middleware resolves a canonical username into
`request.state.current_user`; `AuthManager` remains the source of administrator
status and effective privileges. MarketMatch STT and transcript analysis apply
an additional strict rule: `can_use_marketmatch` must be the literal boolean
`true` in both the stored and effective privilege maps. Documents and Capture
Library records use current username ownership checks.

The repository does not yet have durable Party, scoped Role, scoped Capability,
organization, product, project, or commercial-visibility registries. It also
does not have one general field-projection policy that can safely precede
localization, search, AI, sharing, and export.

## Decision

Introduce a pure, deterministic, deny-by-default kernel in
`src/marketmatch_authority.py` and a read-only compatibility adapter in
`src/marketmatch_authority_auth.py`. Existing routes do not use the new kernel
for enforcement in V1. Their current authentication, privileges, administrator
rules, and ownership checks remain authoritative.

### Party is not Role

A `PartyReference` identifies an actor through a stable, language-neutral code
and kind. It contains no role or capability. A `ScopedRoleAssignment` separately
binds a Party to a stable role code and explicit scope. One Party may hold many
roles in different scopes. Role codes never grant authority by themselves.

### Scope and Capability

`AuthorityScope` can represent organization, product, workspace, project,
resource, and owner dimensions. A global scope is valid only when declared
explicitly and without conflicting dimensions.

V1 has no durable hierarchy registry capable of proving that one scope owns a
different or more specific scope. Non-global grant, role, and resource scopes
therefore match by exact dimensional equality. An omitted dimension is not a
wildcard. Global grants require both an explicit global scope and an explicit
policy declaration that the requested capability may be global.

`ScopedCapabilityGrant` contains a stable capability code, explicit scope,
source reference, optional role-assignment reference, active state, and literal
allow state. Evaluation requires an explicitly known policy capability and a
matching active grant. A role-linked grant is valid only while the referenced
role assignment is valid, active, belongs to the same Party, and covers the
resource scope.

Missing or ambiguous scope, missing principal, missing or unknown capability,
inactive assignment, cross-scope access, invalid classification, invalid policy,
and evaluation errors all deny safely.

### Authorization and field projection

An `AuthorizationRequest` combines a previously resolved principal, capability,
resource context, requested fields, and projection purpose. Locale and display
timezone may accompany a request as non-authoritative presentation context;
they never affect a decision.

The integrating service must construct `ResourceContext` from authoritative
resource metadata after loading the protected resource. Organization, product,
workspace, project, resource, owner, classification, and available-field values
must never be accepted as authority merely because a client supplied them. The
pure V1 kernel validates consistency; it does not discover tenancy or ownership.

An `AuthorizationDecision` contains only safe identifiers and metadata:
allow/deny, stable reason, effective scope, visible and omitted field names,
policy identity/version, principal reference, action, and resource reference.
It never contains protected field values.

`project_authorized_fields` returns a new mapping containing only the visible
fields in an allowed decision. V1 supports explicit omission only. It neither
mutates the source nor replaces hidden values with misleading values. Unknown
record fields remain absent. Unknown projection behavior is rejected.

Projection V1 is deliberately flat-only. It copies only immutable scalar
values (`null`, strings, booleans, finite numbers). Nested mappings, sequences,
sets, custom objects, and other mutable values are omitted even when their
top-level field name is visible. This prevents hidden nested fields and mutable
aliasing from crossing the projection boundary. Recursive schema-aware
projection is deferred.

### Transparent and controlled-confidentiality policies

A transparent policy may enumerate every field it intentionally exposes. It is
not a wildcard: only requested, available, and policy-visible fields survive.

A controlled-confidentiality policy can expose facts such as
`verification_status` and `evidence_summary` while omitting fields such as
`supplier_identity`, `factory_identity`, `factory_address`, `origin_cost`, and
`markup`. These are generic policy examples, not a universal procurement data
schema. Internal Parties and records continue to exist even when a projection
hides their identity.

Policy visibility authorizes the complete scalar value of a named field. An
authoritative schema must therefore keep evidence-source identity and other
confidential facts in separate fields; it must not embed them inside a visible
`evidence_summary` string. V1 does not guess confidential substrings or rewrite
text, because either behavior could leak or falsify commercial information.

### Derived-artifact inheritance

`inherit_derived_visibility` applies equally to translation, summary, analysis,
export, preview, search projection, embedding, and report artifacts. It:

1. rejects any unauthorized or invalid source;
2. intersects the fields visible across all sources;
3. selects the most restrictive source classification;
4. preserves every source scope constraint;
5. denies conflicting organization, product, workspace, project, or owner
   scopes;
6. denies mixed policy identifiers or versions because V1 cannot safely order
   policies from different contracts.

A derived artifact therefore cannot broaden source visibility. Combining
sources adds restrictions; it never removes them.

### Required downstream order

All future consumers must follow this order:

1. principal resolution;
2. scope validation;
3. authorization;
4. field projection;
5. localization or translation;
6. search indexing/retrieval or AI/RAG processing;
7. export, sharing, notification, or delivery.

Authorization controls access only. It does not decide truth, liability,
commercial fault, or responsibility.

## Current-auth compatibility

The adapter consumes the exact canonical username already placed in request
state and calls the existing `AuthManager.get_privileges`. It does not inspect
or create cookies, validate sessions or passwords, copy auth records, or write
auth state.

The adapter maps a closed set of current literal-boolean privileges to stable
capability codes. Unknown privilege keys never become capabilities. Existing
non-MarketMatch privileges are represented with explicit global scopes because
that is their current behavior. `can_use_marketmatch` maps to the explicit
`marketmatch` product scope only when both stored and effective values are
literal `true`, including for administrators. Administrator status alone never
creates a grant.

Compatibility principal references use the bounded form
`legacy-user:<canonical-username>`. This is an adapter reference, not a claim
that durable Party identity issuance has been completed.

## Safe decision audit representation

`build_safe_audit_summary` contains only correlation identifier, safe principal
reference, action code, resource type and safe identifier, allow/deny, reason,
policy version, and an optional explicit timestamp. It excludes records,
visible or hidden values, credentials, cookies, session tokens, supplier or
factory identities, addresses, costs, margins, and commercial terms.
Identifiers must be bounded language-neutral codes, not field values. Unsafe,
overlong, control-bearing, newline-bearing, or obviously confidential-value
identifiers are omitted or normalized to `invalid`; malformed decisions are
rejected with a fixed error code.

## Reason-code glossary

| Code | Meaning |
|---|---|
| `ALLOWED` | A known capability has an active matching grant and policy. |
| `MISSING_PRINCIPAL` | No resolved principal was supplied. |
| `INVALID_PRINCIPAL` | Principal structure or authentication state is invalid. |
| `MISSING_RESOURCE_SCOPE` | A protected resource has no explicit scope. |
| `AMBIGUOUS_SCOPE` | Scope dimensions conflict or are not singular stable codes. |
| `INVALID_RESOURCE` | Resource or requested-field structure is invalid. |
| `INVALID_CLASSIFICATION` | Resource classification is not a supported stable code. |
| `UNKNOWN_CAPABILITY` | The requested capability is not declared by the policy. |
| `MISSING_CAPABILITY` | The principal has no grant for the requested capability. |
| `INACTIVE_ASSIGNMENT` | A grant or required role assignment is inactive or invalid. |
| `EXPLICIT_DENY` | The only matching grant has literal allow set to false. |
| `AMBIGUOUS_GRANT` | More than one grant covers the same request. |
| `SCOPE_MISMATCH` | A grant exists but does not cover the resource scope. |
| `CLASSIFICATION_DENIED` | The policy does not cover the resource classification. |
| `INVALID_POLICY` | Policy structure or projection behavior is unsafe. |
| `SOURCE_NOT_AUTHORIZED` | A derived artifact has an invalid or unauthorized source. |
| `SOURCE_SCOPE_CONFLICT` | Source tenancy or owner scopes are incompatible. |
| `SOURCE_POLICY_CONFLICT` | Source policy identifiers or versions differ. |
| `INVALID_DERIVATION` | Derived visibility inputs are incomplete or unsupported. |
| `EVALUATION_ERROR` | An unexpected evaluation failure was caught and denied. |

## Generic scenarios

1. A Party has a reviewer role in Project Alpha and an observer role in Project
   Beta. A capability grant linked to the Alpha assignment allows review only
   for Alpha resources.
2. A transparent internal policy exposes every field explicitly enumerated by
   that policy, but does not expose an unexpected field added to a record.
3. A controlled policy exposes verification status and evidence summary while
   omitting identities, address, origin cost, and markup.
4. A translated summary derived from two sources receives only the intersection
   of their visible fields, their most restrictive classification, and all
   source scope constraints.
5. The same request evaluated with Spanish, English, or Simplified Chinese
   presentation context returns the same authority decision and reason code.

## Current state versus target state

V1 implements importable contracts, pure evaluation, field projection,
derived-visibility inheritance, safe audit summaries, and a current-auth
adapter. Existing production routes continue to enforce their established
rules and public behavior.

The target architecture is a durable multi-product identity and authority
platform in which issued Party identities, scoped assignments, grants, policy
registries, and audits are managed through reviewed administrative workflows.
V1 is only the contract foundation for that target.

## Deferred items

- durable Party identity issuance and alias migration;
- Party, Role, Capability, scope, policy, and audit persistence;
- organization, product, workspace, and project registries;
- administrative policy-management UI and APIs;
- policy integration into existing production route enforcement;
- Evidence Core, search, RAG, agents, reports, exports, shared links, and API
  integration;
- localized role and capability labels;
- product-specific commercial policy catalogs;
- durable access-decision logging and retention policy.

No database migration, browser storage, external dependency, public debug
endpoint, remote call, AI invocation, or user-facing interface is introduced by
this decision.

## Consequences

Future code has a reusable contract that can be tested before any authorization
rewrite. Current login, cookie sessions, privileges, administrator behavior,
Documents ownership, Capture, Library, STT, analysis, locale preferences, UI,
and APIs remain unchanged. A future production integration must separately
prove equivalence at each route seam before replacing existing enforcement.
