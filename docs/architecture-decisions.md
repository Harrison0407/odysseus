# Architecture Decision Log

Newest first.

## ADR-050 — Increment 1 Codex correction: permanent history guards, dedicated assignment/exemption authority, canonical serialization, and frozen migration replay

**Decision:** Codex implementation verification of Increment 1 commit
`9b803911` reported CX-I1-001 through CX-I1-010. Harrison authorized a
correction bounded to those findings. This ADR supersedes ADR-049 item 3's
live-service migration import and item 8's interim `APPROVE_GATE` reuse;
ADR-049 otherwise remains the historical implementation decision.

1. `PackagePolicyAssignment` is unconditionally unique by package and
   immutable/non-deletable through instance, queryset, bulk, admin, and
   cascade paths, including Django's base-manager route. The retained
   `is_active` field cannot authorize a second row. Migration 0003 fails
   loudly if pre-existing duplicates exist; migration 0004 binds each base
   manager to its protected queryset.
2. Published/withdrawn `GatePolicyVersion` records are transition-controlled,
   immutable, and non-deletable across the same bypass paths. Draft edits and
   lifecycle transitions use narrow internal write contexts reached only by
   authorized services.
3. Canonical ownership has a database check constraint. Canonical publication,
   withdrawal, and atomic version replacement lock the shared `GatePolicy`
   row before version locks and availability checks.
4. Both `decision_capability` and `attempt_creation_capability` must be stable
   registered codes.
5. `ASSIGN_GATE_POLICY` is a new package-scoped, no-default-role capability.
   The public assignment boundary accepts only actor/package/version IDs,
   locks and authorizes the persisted package before policy retrieval, reloads
   explicit versions, enforces tenant eligibility, and fails closed on
   ambiguous organization policy families. A separate private system path is
   limited to canonical bootstrap assignment.
6. `EXEMPT_PACKAGE_FROM_PROCUREMENT_GATES` replaces `APPROVE_GATE` for the
   administrative exemption. It is package-scoped, has no role default, and
   remains distinct from any gate decision or pass.
7. Denial and exemption audit metadata contains identifiers, capability/state
   codes, and no package display text, schema, or protected reason.
8. Migration 0002 now freezes canonical Version 1 data in the migration file,
   imports no live service/model/registry, uses historical models exclusively,
   and retains its documented irreversible no-op reverse. MigrationExecutor
   tests replay the corrected file from its prior state on empty and populated
   databases and invoke the frozen forward function twice.

Two PostgreSQL-only concurrency tests are included for package assignment and
dual canonical withdrawal. They are skipped on SQLite; PostgreSQL execution
remains pending and is not claimed. No GateAttempt, gate execution, workflow
change, or Increment 2 behavior is introduced.

**Status:** correction implemented; Increment 1 pending read-only Codex
re-verification and Harrison acceptance. Increment 2 remains unauthorized.

## ADR-049 — Milestone 1 Increment 1 implementation: platform-scope capability check, first `RunPython` data migration, and five narrow gap-fills the Charter left to the implementer

**Decision:** This entry records the first actual Milestone 1 application
code, migrations, and tests (Increment 1 — Procurement Gate Policy and
Package Assignment Foundation: `apps.procurement_gates.GatePolicy`,
`GatePolicyVersion`, `PackagePolicyAssignment`), authorized by Harrison
after the Charter Version 6 owner-acceptance commit (`1e1b247`). Everything
below is a direct implementation of `docs/MILESTONE_1_PROCUREMENT_GATES_CHARTER.md`
§§1–4, 10, 13, 14 except where explicitly marked as an implementer
gap-fill for a point the Charter left unspecified.

1. **`has_capability(..., organization=...)` implemented exactly per
   Charter §13** — additive keyword-only parameter, mutual exclusivity
   with `package` enforced by a new `AuthorizationConfigurationError`,
   identical query shape (`organization=organization, package__isnull=True,
   role_assignment__isnull=True`). Every pre-existing caller (none of which
   pass `organization=`) is behaviorally unchanged — verified by the full
   565-test regression run, not merely by inspection.
2. **Platform-scope capability check (implementer gap-fill).** The Charter's
   §13 authorization table names `PUBLISH_GATE_POLICY` as a "platform- or
   organization-scoped" capability but only ever writes the organization-scoped
   query shape (worked for `package.organization`, which is never null). A
   canonical/platform-scoped `GatePolicy` (`organization IS NULL`) has no
   organization to scope a `has_capability(..., organization=...)` call to,
   and Charter §13 separately requires every `apps.procurement_gates` call
   to `has_capability` carry an explicit `package=`/`organization=` scope —
   an unscoped call is a named authorization-architecture violation.
   Resolution: `apps.procurement_gates.services._actor_holds_platform_policy_capability`
   queries `CapabilityGrant` directly for a pure platform grant
   (`organization`, `package`, and `role_assignment` all `NULL`) instead of
   routing through `has_capability` at all, so the "no unscoped
   `has_capability` call" rule is never at risk of being read as satisfied
   by a technicality.
3. **First `RunPython` data migration in this repository
   (`procurement_gates/migrations/0002_seed_canonical_policy_and_assign_packages.py`).**
   Every prior migration in this codebase is schema-only; seed/demo data has
   always gone through a management command instead (e.g.
   `apps.procurement.management.commands.seed_confidentiality_demo`).
   Charter §4.2 is explicit and binding that the canonical policy seed and
   existing-package pinning must be "a data migration (§4), not via test
   fixtures or admin-only manual setup" — that requirement overrides the
   prior convention for this one feature. The migration uses
   `apps.get_model` for every schema-bound model (so a later schema change
   cannot retroactively break it on replay) and imports only
   `canonical_gate_schema`/`validate_gate_schema` directly from
   `apps.procurement_gates.services`, since those two functions are pure
   over plain constants (`GATE_CODES`, `ALL_CAPABILITY_CODES`) and perform
   no ORM access.
4. **`gate_schema` per-gate field names (implementer gap-fill).** Charter
   §3.1 names four of the six per-gate fields exactly
   (`attempt_creation_capability`, `overridable`,
   `non_overridable_requirements`, `override_satisfies_successor_predecessor`)
   but describes the other two only in prose ("the ordered list of required
   evidence-requirement codes", "the capability code(s) required to record
   a `GateDecision`"). Implemented as `evidence_requirement_codes` (list)
   and `decision_capability` (a single string — the §13 authorization table
   uses it in the singular, e.g. "the specific capability declared in
   `gate_schema[gate_code]`... e.g. `APPROVE_GATE`"). No other schema key or
   alias was invented.
5. **`GATE_POLICY_VERSION_WITHDRAWN` audit action (implementer gap-fill).**
   Charter §10's action table enumerates every other policy-lifecycle
   action but is silent on withdrawal (§3.2). Added following the identical
   naming/meaning pattern as `GATE_POLICY_VERSION_PUBLISHED`.
6. **No Django admin registration for `apps.procurement_gates` models**
   (Charter §16.3 option 1 — excluded entirely), rather than option 2 (a
   dedicated read-only `ModelAdmin`). No prior app in this repository has
   ever implemented option 2's `has_add_permission`/`has_change_permission`/
   `has_delete_permission`-overridden pattern — every existing `admin.py` is
   the same blanket `admin.site.register` loop the Charter names as the
   anti-pattern this rule exists to prevent. Full exclusion is the smaller,
   equally-compliant change; a read-only surface remains open for a future
   increment if genuinely needed.
7. **Canonical policy seed sets `overridable = False` on all six gates**,
   not only the Charter-mandated `False` on `A2`. The override mechanism
   (`ProcurementGateOverride`, Charter §11) is out of scope for this
   increment; shipping `overridable = True` anywhere in the canonical
   schema today would advertise a capability nothing yet implements.
8. **`grant_gate_progression_exemption` (Charter §4.4) requires package-scoped
   `APPROVE_GATE`** (interim choice). The Charter names no dedicated
   capability for granting this administrative exemption. `APPROVE_GATE`
   is reused rather than a new code minted, since Milestone 1 does not
   otherwise use `APPROVE_GATE` yet (`GateDecision` recording is a later,
   not-yet-authorized increment) and a dedicated code can be introduced
   later without any migration if the business decides administrative
   exemption authority should differ from gate-decision authority.

**Why:** Increment 1's own scope boundary (see
`docs/implementation-log.md`) forbids redesigning or re-adjudicating the
accepted Charter and forbids inventing missing schema keys/aliases: every
point above is either a literal, verified-by-test implementation of an
already-binding Charter rule, or a narrow, explicitly-flagged gap-fill
where the Charter under-specified a mechanism this increment's own scope
requires to exist (a real `PUBLISH_GATE_POLICY` check for the canonical
policy; real field names for the two unnamed schema entries; a real audit
action for a state transition the Charter itself defines). None of the
eight points redefine any Charter-binding rule, model, constraint,
on_delete behavior, or resolution order.

## ADR-048 — `ChangeRequest` creation gets a new, named capability code
enforced before the unmodified foundation is invoked; refreeze gets a
single, named service with deterministic hold-cause identity; the
`ChangeRequest` decision-capability mapping is corrected to its actual
`.get(...)`-with-fallback shape; expired-override reconciliation adopts
the foundation's own lock order

**Decision:** `docs/MILESTONE_1_PROCUREMENT_GATES_CHARTER.md` version 6
adds four binding corrections, all documentation-only as of this entry (no
application code changed): (1) a new capability code,
`REQUEST_PACKAGE_CHANGE`, is the sole, package-scoped authorization check
`apps.procurement_gates.services.request_gate_aware_change` performs
before creating a `governance.ChangeRequest`, evaluated before any
protected value is retrieved and against a package loaded from its own
persisted primary key, never caller-supplied data (Charter §8.2.1, §13).
(2) `apps.procurement_gates.services.complete_package_refreeze` is named
as the sole Milestone 1 refreeze entry point: a ten-step locked
transaction that creates the new `PackageFreezeRevision` and closes only
the exact open `PackageHoldCause` rows matching its own
`source_change_requests`, leaving every unrelated hold cause untouched;
`PackageHoldCause` gains a deterministic identity rule —
`(package, cause_type, reference)`, one open row per triple enforced by a
partial unique constraint, idempotent creation and closure, and immutable
closed history (Charter §7.3, §8.4). (3) `decide_gate_aware_change`'s
decision-time authorization is now described using the actual dictionary
access pattern, `CHANGE_REQUEST_APPROVAL_CAPABILITY.get(field_name,
"APPROVE_ROLE_CHANGE")`, with every mapped and fallback-only
`FROZEN_FIELD_CODES` entry explicitly enumerated (Charter §13). (4)
`apps.procurement_gates.services.reconcile_expired_overrides`'s Phase 2
now locks the candidate `ProcurementGateOverride` before
`ProcurementPackage`, matching human override approval/rejection/revocation's
own binding order exactly; a new numbered override-mutation transaction
specification (Charter §11.3) states this shared order explicitly.

**Why:** the independent Milestone 1 Charter Version 5 Revalidation
(against commit `ebcabdc582dd8ffea3ebdfce68dc55c4ee59c526`, the commit
introducing version 5) found that (1) §8.2.1 step 3 named no actual
capability code for `ChangeRequest`-creation authorization, and §13's
authorization table falsely credited the unmodified
`apps.governance.services.request_change` with performing "the same
capability check" — verified false against that function in its
entirety, which checks only `package.is_frozen` (CR-CREATE-AUTH-GAP,
Critical); (2) no Charter version ever named the service function that
performs a refreeze, and §8.4 never defined which specific open
`PackageHoldCause` rows a given refreeze may close, risking either
clearing an unrelated hold or a duplicate refreeze reopening already-done
closure work (HOLD-CAUSE-CLOSURE-1, Critical); (3) §13's authorization
table and required test 25u described the decision-capability lookup
using bracket notation, `CHANGE_REQUEST_APPROVAL_CAPABILITY[field_name]`,
which does not match the actual repository code and would raise
`KeyError` for the five currently-unmapped `FROZEN_FIELD_CODES` entries
the real `.get(...)` fallback exists to handle
(NF-V4-2-INCOMPLETE-MAPPING, Critical); and (4)
`reconcile_expired_overrides` Phase 2 locked `ProcurementPackage` only,
the reverse of, and inconsistent with, the global lock-order table's own
binding order for every other operation whose primary existing row is a
`ProcurementGateOverride` — an un-reconciled, opposite-order deadlock risk
of the same class ADR-047's LOCK-ORDER-1 correction already fixed once
for `decide_gate_aware_change` (LOCK-ORDER-1-1, Critical). Five further,
narrower findings (NF4-A-1, PGSTATE-ADMIN-1, CHTR-010-COUNT-2,
HOLD-WORDING-1, NF-1-SUMMARY-1) were also resolved. None of these
corrections reopens or contradicts ADR-044 through ADR-047 above;
`apps.workflow.GateOverride`, `apps.governance.services.request_change`,
`approve_change_request`, `reject_change_request`, `raise_risk_flag`,
`resolve_risk_flag`, and the existing `has_capability` call shape all
remain unmodified by all of them.

## ADR-047 — Milestone 1 RiskFlag mutations route through a new
gate-aware orchestration boundary; procurement-gates historical models get
a binding admin-immutability policy; `ChangeRequest` decisions adopt the
foundation's own lock order via a new global lock-order table; the
organization-scoped `has_capability` query is stated exactly; the
existing `ChangeRequest` approval-capability mapping is explicitly reused

**Decision:** `docs/MILESTONE_1_PROCUREMENT_GATES_CHARTER.md` version 5
adds five binding rules, all documentation-only as of this entry (no
application code changed): (1)
`apps.procurement_gates.services.raise_gate_aware_risk_flag`/
`resolve_gate_aware_risk_flag` become the sole Milestone 1 entry points
for raising or resolving a package-scoped `governance.RiskFlag` against a
gate-governed package, mirroring the `ChangeRequest` wrappers' shape
exactly — lock, authorize before retrieval, invoke the unmodified
foundation function, then a corrective `recompute_package_hold_state`
write before commit (Charter §8.3.1). (2) every procurement-gates
historical or append/close-only model (`GatePolicyVersion` post-publication,
`PackagePolicyAssignment`, `GateAttempt`, `GateEvaluation`, `GateDecision`,
`GateInvalidation`, `PackageFreezeRevision`, `ProcurementGateOverride`
post-decision, `PackageHoldCause` historical fields) must be either
excluded from Django admin entirely or exposed only through a dedicated
read-only `ModelAdmin`; a blanket writable auto-registration loop — the
exact pattern `apps/governance/admin.py` already uses for the `governance`
app — is prohibited for these models by name (Charter §16.3). (3)
`apps.procurement_gates.services.decide_gate_aware_change` now locks
`governance.ChangeRequest` before `ProcurementPackage`, matching
`approve_change_request`/`reject_change_request`'s own internal order
exactly; a new global lock-order table (Charter §14.1a) classifies every
Charter-defined mutation into one of two named, consistently-applied
patterns. (4) The organization-scoped `has_capability(..., organization=...)`
binding query is now stated exactly — `organization` matches, `package IS
NULL`, `role_assignment IS NULL` — excluding hybrid grants that carry
both an organization and a package or role-assignment scope (Charter
§13). (5) `decide_gate_aware_change`'s decision-time authorization is now
explicitly bound to reuse the exact, existing
`apps.governance.services.CHANGE_REQUEST_APPROVAL_CAPABILITY[field_name]`
mapping, rather than an independently-defined second table (Charter §13).

**Why:** the independent Milestone 1 Charter Version 4 Revalidation
(against commit `cf01d400e1dffd6c5981ee2ae8a01ad71f3c9006`, the commit
introducing version 4) found that (1) §8.4 claimed the unmodified
`raise_risk_flag`/`resolve_risk_flag` were "reachable identically whether
or not a package participates in A1–A6" — verified false:
`resolve_risk_flag` writes `is_on_hold` from the governance-side rule
alone, silently clearing a gate-native `PackageHoldCause`-backed hold
(e.g. an open critical-change hold awaiting refreeze) when an unrelated
`RiskFlag` is resolved, because no gate-aware wrapper existed for
`RiskFlag` the way one already did for `ChangeRequest` (RISKFLAG-HOLD-1,
Critical); (2) `apps/governance/admin.py`'s blanket, writable, default-
`ModelAdmin` auto-registration loop was never addressed for procurement-gates
historical models beyond the two narrow cases (`GatePolicyVersion`
post-publication, `GateAttempt` deletion) version 4 already covered — a
staff user with ordinary admin change permission could otherwise directly
edit `GateDecision.outcome`/`PackageFreezeRevision.frozen_fields`/an
override's `expires_at`, bypassing every lock, cascade, and audit
guarantee this Charter defines (NF4-A, High); (3)
`decide_gate_aware_change` locked `ProcurementPackage` before
`ChangeRequest`, the reverse of the unmodified foundation functions' own
internal order, creating a latent, unacknowledged opposite-order deadlock
risk for any caller that ever reached the foundation functions directly
(LOCK-ORDER-1, Medium); (4) the organization-scope binding rule was stated
in prose without the exact query shape, leaving unstated whether a hybrid
`package`-plus-`organization` grant would incorrectly qualify (NF4-C,
Medium); and (5) `decide_gate_aware_change`'s authorization check was not
explicitly bound to the existing `CHANGE_REQUEST_APPROVAL_CAPABILITY`
mapping, risking a second, independently-drifting capability table
(NF-V4-2, Medium). Four further, editorial/documentation-only findings
(README-STALE, IMPL-LOG-COUNT, NF-V4-5, TRACE-1) were also resolved. None
of these corrections reopens or contradicts ADR-044, ADR-045, or ADR-046
above; `apps.workflow.GateOverride`, `apps.governance.services.raise_risk_flag`,
`resolve_risk_flag`, `approve_change_request`, `reject_change_request`,
and the existing `has_capability` call shape all remain unmodified by all
of them.

## ADR-046 — Milestone 1 Change Request decisions route through a new
orchestration service; package hold state is split into governance and
gate-native sources; `has_capability` gains an organization-scope
extension; override expiry detection is split from reconciliation;
`GateAttempt` is declared non-deletable

**Decision:** `docs/MILESTONE_1_PROCUREMENT_GATES_CHARTER.md` version 4
adds five binding rules, all documentation-only as of this entry (no
application code changed): (1)
`apps.procurement_gates.services.decide_gate_aware_change` becomes the
sole Milestone 1 entry point for approving or rejecting a
`governance.ChangeRequest` against a gate-governed package, mirroring
`request_gate_aware_change`'s shape; the existing
`apps.procurement.package_views.change_request_decide` view must be
rewired to call it rather than `apps.governance.services.approve_change_request`/
`reject_change_request` directly (Charter §8.2.3). (2)
`ProcurementPackage.is_on_hold` is now derived from two distinct sources —
existing governance holds (pending `ChangeRequest`, non-`STANDARD`
`RiskFlag`), queried via a new, minimal
`apps.governance.services.has_unresolved_governance_holds`, and
gate-native `PackageHoldCause` rows for A1–A6-specific lifecycle causes
only — combined by a single `recompute_package_hold_state` projection
(Charter §8.4). (3) `apps.governance.services.has_capability` gains an
additive, keyword-only `organization=` scope, mutually exclusive with
`package=`, so the `A1` bootstrap authorization path names an evaluation
mode the function actually supports (Charter §13). (4)
`apps.procurement_gates.services.compute_gate_state` is stated to be
completely side-effect-free; a new two-phase
`apps.procurement_gates.services.reconcile_expired_overrides` performs
override-expiry detection (unlocked) and reconciliation (locked,
cascading, audited) as a separate service (Charter §11.5). (5)
`GateAttempt` is declared an immutable, non-deletable historical
aggregate row, with an explicit model-level/`pre_delete` prohibition
distinct from, and stronger than, ordinary `on_delete=PROTECT` (Charter
§3.5).

**Why:** the independent Milestone 1 Charter Version 3 Revalidation
(against commit `f59237b6ba0c18e210c54f01cd79e98ea40e1709`, the commit
introducing version 3) found that (1) no orchestration existed for
*deciding* a Change Request — the one real HTTP decision path called the
foundation's approve/reject functions directly, leaving §8.1's
invalidation cascade unreachable from it (NF-NEW-1); (2) version 3's hold
design implied mirroring `RiskFlag`/`ChangeRequest` rows into
`PackageHoldCause`, and inaccurately limited risk-driven holds to
`HIGH_RISK` alone when the existing foundation also holds on
`CONTROLLED_OPAQUE` (NF-NEW-2); (3) `has_capability` has no
organization-scoped evaluation path at all, so §13's named `A1`-bootstrap
mechanism was unsupported by the actual accepted function (NF-NEW-3); (4)
lazy expiry detection was described as happening inside
`compute_gate_state`, a function this Charter otherwise treats as a pure
read, without resolving whether that read path also locks and mutates
(NF-NEW-4); and (5) `GateAttempt`'s only stated deletion protection was an
`on_delete=PROTECT` table entry, which cannot protect the generic-target
`EvidenceBundle`/`EvidenceItem` rows that reference a `GateAttempt` via
`content_type`/`object_id` rather than a real foreign key (NF-NEW-5).
None of these five corrections reopens or contradicts ADR-044 or ADR-045
above; `apps.workflow.GateOverride`, `apps.governance.services.request_change`,
`approve_change_request`, `reject_change_request`, and the existing
`has_capability` call shape all remain unmodified by all of them.

## ADR-045 — Milestone 1 Change Requests route through a new orchestration
service, never the foundation's `request_change` directly; `A2` becomes
unconditionally non-overridable; a downstream-invalidation cascade is
added for lapsed predecessor overrides

**Decision:** `docs/MILESTONE_1_PROCUREMENT_GATES_CHARTER.md` version 3
adds three binding rules, all documentation-only as of this entry (no
application code changed): (1)
`apps.procurement_gates.services.request_gate_aware_change` becomes the
sole Milestone 1 entry point for creating a `governance.ChangeRequest`
against a gate-governed package — every Milestone 1 view, form, API,
admin action, and service must call it, never
`apps.governance.services.request_change` directly; the latter remains
fully available, unmodified, to legacy foundation callers (Charter
§8.2.1). (2) `gate_schema["A2"].overridable` must always be `False`,
enforced by publication-time rejection, with no policy opt-in of any kind
(Charter §12.1, supersedes the "`False` unless a policy version explicitly
opts in" treatment `A2` previously shared with `A1`). (3) An explicit,
locked downstream-invalidation cascade is defined for when a gate that
relied on a predecessor's `override_satisfies_successor_predecessor`-backed
`OVERRIDDEN` state later has that predecessor override expire or be
revoked (Charter §9.5).

**Why:** the independent Milestone 1 Charter Version 2 Revalidation
(against commit `3b62228b4a6efb4079e7f8c010e107fcf9de639a`) found that (1)
version 2's claim that non-critical Change Requests "never touch hold
state" was contradicted by the current, unmodified
`apps.governance.services.request_change`, which unconditionally sets
`is_on_hold = True` on every `ChangeRequest` it creates — an orchestration
layer was required rather than a change to the accepted foundation, which
this Charter cycle's authorization boundary forbids modifying (NF-1); (2)
version 2 permitted `A2` to be made `overridable` via policy opt-in, but
the post-A2 critical-change cascade (ADR-044's correction addendum) only
ever revoked active overrides on `A3`–`A6`, leaving an `OVERRIDDEN` `A2`
gate untouched by a critical change — the safer, simpler fix is to remove
the possibility entirely rather than special-case the cascade further
(REVAL-004-RESIDUAL); (3) nothing defined what happens to a downstream
gate that already passed in reliance on a predecessor's override once
that override lapses, leaving a real "stale descendant" gap
(REVAL-008-RESIDUAL). None of these three corrections reopens or
contradicts ADR-044 above; `apps.workflow.GateOverride` and
`apps.governance.services.request_change` remain unmodified by all of
them.

## ADR-044 — Milestone 1 procurement gate overrides get a new, domain-native model; `apps.workflow.GateOverride` is not reused or relaxed
**Decision:** `docs/MILESTONE_1_PROCUREMENT_GATES_CHARTER.md` §11 defines a
new `ProcurementGateOverride` model, scoped to
`(package, policy_version, gate_code, attempt, organization)`, for
Procurement Gates A1–A6 exception handling. It does **not** reuse the
existing `apps.workflow.GateOverride` row, and does not relax, make
nullable, or otherwise modify that model's `gate_definition`
(`on_delete=PROTECT`, required) or `handoff` foreign keys.
**Why:** the independent Milestone 1 Charter Review (finding CHTR-002)
found that the roadmap's own acceptance criterion — "Gate Override
mechanisms are reused" — directly conflicted with ADR-020, which had
already rejected reusing `GateOverride` for a narrower case (an
over-installation waiver) on the grounds that doing so would require
either fabricating a `GateDefinition`/`Handoff` row that doesn't
correspond to anything real, or weakening `GateOverride`'s foreign-key
constraints to make them optional — both judged worse than a dedicated
alternative. Procurement gate overrides are a materially better fit for a
dedicated model than ADR-020's waiver case was for `AuditEvent.Action.WAIVER`
alone, because they need their own expiry, revocation, and
separation-of-duties fields enforced and queried directly — a bare
`AuditEvent` cannot do that. `ProcurementGateOverride` therefore reuses
`GateOverride`'s *lifecycle pattern* (written reason, before/after state,
finite expiry, revocation) as a new, procurement-domain-native model, not
its table. This ADR does not reopen, weaken, or contradict ADR-020;
`apps.workflow.GateOverride`'s foreign-key constraints are unchanged by
Milestone 1. See the Charter for the full model definition and the
non-overridable-controls list it is bound by.

**Correction addendum (2026-07-20, Milestone 1 Charter Correction Cycle,
resolves REVAL-004/REVAL-008):** the independent Milestone 1 Charter
Revalidation of Charter version 1 found this decision's original text
incomplete on two lifecycle points, since corrected in Charter version 2
without reopening any part of the decision above. First, approving a
`ProcurementGateOverride` closes its underlying `GateAttempt`
(`closed_at` set), exactly as a `GateDecision` would — an override was
not previously stated to close anything, leaving the "at most one open
attempt per gate" rule's interaction with overrides undefined. Second, the
post-A2 critical-change invalidation cascade (Charter §8.1) now also
revokes every currently-active `ProcurementGateOverride` on the affected
package's A3–A6 attempts, system-attributed (`revoked_by = NULL`), so an
override granted under stale technical terms cannot silently survive a
critical change. Neither correction touches `apps.workflow.GateOverride`
or its foreign keys.

## ADR-043 — Foundation correction cycle 3: role_assignment-derived CapabilityGrant scope, retrieval-safe privileged-audit columns, unbounded-completeness scan
**Decision:** Three narrow corrections to the privileged-audit mechanism
introduced by ADR-042, found by an independent Fable revalidation of
cycle 2's own commit. No second authorization system, audit store, or
scope model was introduced.

(1) **CTCF-AUDIT-SCOPE-021.** `governance.services._resolve_scope_for_target`
did not resolve a `CapabilityGrant` target's scope when the grant's own
`package`/`organization` columns were unset but its `role_assignment`
carried a real, resolvable scope (`role_assignment.package` or
`role_assignment.organization_context`) — a valid, pre-existing pattern
`grant_capability(..., role_assignment=assignment)` has always allowed.
Such a `CAPABILITY_GRANT` audit event was excluded even for an
otherwise-authorized viewer (fail-closed, never an over-exposure, but an
avoidable completeness gap). The resolver now explicitly checks
`CapabilityGrant.role_assignment` as a third resolution step, after direct
`package` and direct `organization`, before giving up. `RoleAssignment`
itself never fails closed (`organization_context` is a required field), so
this step always succeeds once reached. `RoleAssignment.project` is not
consulted — it carries no `ProcurementPackage` relationship and
`organization_context` is always already resolvable, so a project-based
fallback would be structurally unreachable dead code, not an intentional
omission.

(2) **CTCF-AUDIT-RETRIEVAL-022.** `privileged_audit_queryset`'s candidate
scan previously selected full `AuditEvent` rows — including `summary` and
`metadata`, which can carry confidential target text — for every candidate,
authorized or not, before the per-row scope decision. The function now
selects only `_AUDIT_EVENT_SAFE_FIELDS` (id, action, occurred_at, actor_id,
content_type_id, object_id) at every phase; `summary`/`metadata` are never
selected by this function at all, for any row, since the safe projection
(`privileged_audit_projection`) never uses either field. Independently
verified by direct SQL capture (no query issued by this path contains the
`summary` or `metadata` column names).

(3) **CTCF-AUDIT-WINDOW-023.** The prior implementation capped scanning at
the 1,000 most-recent candidate events; an authorized event older than
that many unrelated, unauthorized events could be silently omitted. The
scan is now a deterministic-order (`-occurred_at, -id`), safe-column,
cursor-paginated loop over batches of 200 candidates, continuing until the
requested result `limit` is satisfied or candidates are genuinely
exhausted — completeness of the requested result count no longer depends
on an arbitrary global ceiling. No count or volume signal about excluded
events is exposed at any point.

**Why:** All three were read-path completeness/retrieval-hygiene gaps in
the privileged-audit mechanism itself, not authority-to-decide gaps and not
confirmed browser-facing disclosures — found and reproduced with direct
evidence (SQL capture, object-level scope resolution, and a
>1,000-unauthorized-event/1-older-authorized-event reproduction) during
independent revalidation of ADR-042's own commit, consistent with this
project's practice of treating each Fable pass as adversarial verification,
not confirmation.

## ADR-042 — Foundation correction cycle 2: privileged-audit scope-before-retrieval and Change Request read-authorization as a distinct axis
**Decision:** Two narrow corrections to the Controlled Transparency /
Confidentiality foundation (ADR-041), reusing its existing
Party/Role/CapabilityGrant architecture — no second authorization system,
no second audit store, no second workflow engine.

(1) **Privileged audit (CTCF-AUDIT-017).** `governance.views.privileged_audit`
previously required only `can_override_gates` and applied no scope to the
underlying `AuditEvent` queryset, so any senior role-holder in any tenant
organization could read every organization's privileged events. Access now
requires an explicit, currently-active `VIEW_PRIVILEGED_AUDIT`
`CapabilityGrant`, scoped to an organization or a package exactly like
every other sensitive capability in this system —
`governance.services.authorized_privileged_audit_scopes` resolves the
caller's authorized organization/package ids, and
`governance.services.privileged_audit_queryset` resolves each candidate
`AuditEvent`'s target scope through its own existing persisted
relationships (package, hosting organization, evidence-bundle target,
etc. — reusing `apps.audit.services.evidence_bundle_package` rather than
adding a parallel resolver) before treating any event as visible. An event
whose target cannot be resolved is excluded, never included. The rendered
projection (`governance.services.privileged_audit_projection`) never
copies a target's raw `__str__` or `AuditEvent.metadata` into the browser
— only the action type, actor, and timestamp, plus a fixed, generic,
per-action-type description.

(2) **Change Request projection (CTCF-CR-PROJ-018).** `package_detail`
previously placed every `ChangeRequest` for a package into the template
context unconditionally — basic package participation, not any
field-specific decision authority, gated what a viewer saw.
`governance.services.change_request_projection` now distinguishes three
permissions: knowing a request exists, reading its raw
`field_name`/`frozen_current_value`/`proposed_new_value`/`reason`, and
deciding it. Detailed values are visible only to the requester, the
decider (once decided), or an actor holding the same field-specific
capability already used to decide that field
(`CHANGE_REQUEST_APPROVAL_CAPABILITY`, unchanged) — everyone else receives
a safe, generic projection. The queryset uses `.only()` on the
non-sensitive columns so the sensitive text fields are not fetched at all
for rows the viewer never ends up authorized to see in full.

**Why:** Both gaps were found during an independent revalidation pass of
the foundation (a89a9f7) that treated Codex's report, prior Fable reports,
and passing tests as claims to verify, not proof. Both are read-path
authorization gaps, not authority-to-decide gaps — the existing
approve/reject/revoke decision checks were already correct and are
unchanged by this cycle.

## ADR-041 — Controlled Transparency / Confidentiality foundation: Party/Role/Capability as a cross-cutting layer, commercial layers as genuinely distinct objects
**Decision:** New `apps.governance` app adds Party (wraps an existing
`Organization`/`Supplier` rather than duplicating identity), package/
project/organization-scoped `RoleAssignment` (versioned, effective-dated),
and `CapabilityGrant` (every APPROVE_*/AUTHORIZE_*/EXPORT_*/
VIEW_PRIVILEGED_AUDIT-type action requires an explicit grant, never a
role-implied default) as a layer *alongside* — never replacing — the
existing `Organization`/`UserProfile`/`Role`/`UserProjectAccess` tenancy
system. A `ProcurementPackage` (new, in `apps.procurement`) is "hosted"
by one administering organization while its other participants (buyer,
seller of record, China procurement operator, factory) are represented
purely through package-scoped `RoleAssignment` rows — every
package-related HTTP view and service function authorizes through
`governance.services.has_capability`/`active_role_assignments`, never a
bare `request.user.profile.organization` equality check, since a
package's real participants legitimately span more than one
organization. Six commercial-layer objects are kept genuinely distinct
(Factory RFQ, Factory Quote — reusing the existing `Quotation` model
directly, Internal Commercial Sheet, Client Quote, Client PO and
Upstream Factory PO — both reusing the existing `PurchaseOrder` model
via a new `po_kind` field) rather than one record with client-visible
columns hidden in the UI. A central `Classification` enum
(`OPERATIONAL_SHARED` by default, preserving every pre-existing
document/quotation/PO's current behavior exactly) plus
`CLASSIFICATION_REQUIRED_CAPABILITY` gate read access uniformly across
documents, evidence, and commercial records. `DisclosureGrant`,
`ChangeRequest`, `RiskFlag`, and `DerivedArtifact` complete the
foundation; `apps.audit.EvidenceBundle`/`EvidenceItem` extend the
existing generic `Attachment` evidence primitive rather than replacing
it, and `apps.workflow.GateOverride` (the existing exception mechanism)
is reused directly, only hardened with `expires_at`/`revoked_at`.
**Why:** The release explicitly forbids a second, parallel authorization
engine and forbids hard-coding any organization permanently as
"Factory"/"Trader"/"Seller." Layering Party/Role/Capability alongside
the existing tenancy system — rather than replacing it — lets a single
user (Edison) keep his ordinary base-tenant login for every
pre-existing module while separately holding a package-scoped role for
this new cross-organization commercial relationship, without
rearchitecting the single-tenant-scoped convention nearly every other
model in this codebase already relies on. Reusing `Quotation`/
`PurchaseOrder`/`GateOverride`/`Attachment` directly (extended with new
fields) rather than inventing parallel models keeps the "smallest
reusable policy layer" promise concrete rather than aspirational — see
`docs/CONTROLLED_TRANSPARENCY_AND_CONFIDENTIALITY.md` for the full
glossary, capability matrix, and per-policy detail.

## ADR-040 — Interactive Apartment Plan / Room-Zone layer: reusable per-family/letter/floor-variant templates, never one row per apartment; Missing Source is a valid, honest state
**Decision:** New `apps.unitplans` app, strictly separating the
existing immutable Source Drawing (`apps.drawings.Drawing`, untouched)
from a new Operational Interactive Plan layer. `UnitPlanTemplate` is
resolved deterministically from family + `Unit.apartment_letter` +
a computed floor-variant (first floor / upper floor / all floors /
penthouse duplex, derived from already-imported `Floor.level` and
`Unit.is_penthouse` — never a second data source) — one row per
distinct unit-type slot, never per physical apartment.
`UnitPlanAssignment` links each `Unit` to its *current* effective
template (`is_current`, one-per-unit unique constraint). `PlanZone`
holds each room/zone's type, relative (0-1) rect/polygon coordinates,
and its own independent validation lifecycle
(draft/needs-review/validated). Both `UnitPlanTemplate` and `PlanZone`
reuse the exact versioned-immutable-row `supersedes`/`is_current`
pattern already established for `Drawing`/`OrderLineAllocation`
/`EvidenceClassification` — a later plan revision or a corrected room
boundary is always a new row, and superseding a template automatically
moves every currently-assigned unit onto the new revision while never
touching any `FieldIssue`/`WalkthroughItem` that already stored its own
direct FK to the old template/zone. Of the two families' worth of
source material supplied, only PALMERA (sheets H-05 through H-08,
confirmed by direct visual inspection to be genuine "APARTAMENTO TIPO
A/B/C/D" furnished/dimensioned plans, cross-checked against the real
H-09 occupancy table) has an actual per-unit-type drawing; every other
family's template slots are seeded `Status.MISSING_SOURCE` with zero
invented rooms, and PALMERA's own AI-cropped/proposed zones stay
`Draft`/`Needs Review` until an authorized user validates and approves
them through a dedicated admin mapping screen (`can_override_gates`,
the same universal senior-authorization permission used everywhere
else in this release).
**Why:** The release explicitly forbids inventing a template where the
source is insufficient, and forbids treating a derived crop as an
architect-approved drawing — this design makes "honestly missing" a
first-class, fully-functional state (the interactive viewer, issue
creation, and every other action still work, they just show "Fuente
faltante" instead of a fabricated room) rather than something the code
has to special-case or crash on. Keeping templates reusable per
family/letter/variant instead of per-apartment avoids hundreds of
near-duplicate rows, and reusing the versioned-immutable-row pattern
guarantees a plan revision, a corrected room boundary, or an as-built
upload can never retroactively change what an already-created
FieldIssue or WalkthroughItem was actually created against.

## ADR-039 — Unclassified Evidence Inbox: upload provenance is permanent and untouched; classification is a separate, reassignable, generic pointer
**Decision:** `apps.evidenceinbox.UnclassifiedEvidence` wraps a
`documents.Document`/`DocumentVersion` (the same SHA-256-hashed,
duplicate-detected, immutable-version mechanism every other upload in
this system uses) with only an optional coarse project/building guess
and free-text notes — nothing about the original upload is ever
modified again. A separate `EvidenceClassification` row (generic
content_type/object_id pointer, same shape as `Attachment`/
`AuditEvent`, ADR-004) links that evidence to a real target
(building/floor/unit/walkthrough/walkthrough item/training session/
field issue/product/supplier/purchase order line/shipment/container/
installation/inspection record). Reassigning a classification never
edits or deletes the old row — it is marked `is_active=False` and
linked via `superseded_by`, the same versioned-immutable-row pattern
as `Drawing.supersedes` and `OrderLineAllocation.reassigned_from`.
Every classification target is resolved and organization-checked
through the shared `apps.workflow.services.resolve_organization()`
helper (extended with `shipment`/`purchase_order`/`walkthrough`
fallbacks for this release) rather than a bespoke inbox-specific
isolation check.
**Why:** The whole point of this inbox is historical/ambiguous
evidence (Lawson's own photographs, or any future field upload) whose
correct building/apartment/issue isn't known yet, and may turn out to
have been guessed wrong the first time. Keeping the original upload's
provenance permanent and layering a reassignable, fully-audited
classification on top means a reviewer can always answer "who
uploaded this, when, and what did it originally look like," while
still being free to correct where it belongs — including more than
once — without ever losing that history. Reusing the shared
organization resolver, rather than writing a new isolation check
specific to this app, keeps that guarantee in exactly one place for
every current and future cross-app reference.

## ADR-038 — Walkthrough corrective defects become real FieldIssues; delivery readiness is always read from that issue's actual status, never a parallel flag
**Decision:** `WalkthroughItem.field_issue` (FK) links a defect to a
genuinely created, fully-lifecycled `apps.fieldissues.FieldIssue` via
`create_issue_from_item` (which calls the existing `report_issue`).
`delivery_readiness_summary` computes blocking-defect/pending-
verification counts by querying that linked issue's real `status` —
an item flagged `is_blocking_defect=True` only stops blocking once its
issue reaches `VERIFIED_CLOSED` through the existing, already-
evidence-gated closure lifecycle. `mark_delivery_decision` refuses a
`READY` decision while blocked, unless an authorized override
(`can_override_gates` + written reason, logged as
`AuditEvent.Action.WAIVER`) is supplied — the same override shape used
throughout this release (storage suitability, drawings, spares, field
issues).
**Why:** Building a second "is this defect actually fixed" tracker on
`WalkthroughItem` would let a walkthrough's own record drift out of
sync with the real correction workflow's state (e.g. an item marked
"resolved" while its linked issue was actually rejected and never
re-verified). Reading the live `FieldIssue.status` instead makes that
drift structurally impossible — there is exactly one place a
correction's real state lives.

## ADR-037 — Training sessions mirror the field-issue location/evidence pattern exactly; reference-installation approval requires supervisor sign-off first
**Decision:** `TrainingSession` (new `apps.training` app) uses the same
building-required/floor-unit-room-optional location shape as
`FieldIssue`, the same stage-tagged evidence wrapper
(`TrainingEvidence`, reusing `attach_evidence`), and the same
`can_override_gates` permission for both `supervisor_sign_off` and
`approve_as_reference_installation` — the latter hard-requires the
former to have already happened. `FieldIssue.training_session` (new
FK) lets `create_issue_from_training` produce a real, linked
`FieldIssue` via the existing `report_issue` service, never a
duplicated/disconnected copy.
**Why:** Reusing the exact location/evidence/permission shapes already
established for field issues (rather than inventing new ones for
training) keeps the whole property-master release internally
consistent and avoids a fourth permission concept. Requiring
supervisor sign-off before reference-installation approval (A45)
ensures the "correct example for later teams" carries more than one
person's endorsement.

## ADR-036 — Field issues reuse Comment/Attachment-style provenance and the existing senior-authorization permission; before/after evidence gets one small stage-tagged wrapper
**Decision:** `FieldIssue` (new `apps.fieldissues` app) requires only
`building` at creation; every other location field (floor/unit/room)
is optional and refinable later without touching `created_at`/
`created_by`. Comments reuse the existing generic `apps.audit.Comment`
model directly (no new comment model). Evidence reuses
`apps.audit.services.attach_evidence` for the actual upload, wrapped by
one new `FieldIssueEvidence` (`stage`: before/during/after) — the one
piece of metadata the generic `Attachment` model doesn't carry that
this feature's closure rule needs. `verify_and_close_issue` requires
`can_override_gates`, the same permission every other approval-style
action in this release already uses (drawing approval, purchased-spare
confirmation).
**Why:** Building-required-rest-optional matches "must be able to
report quickly... refinable later without losing original provenance"
directly. Reusing `Comment`/`attach_evidence` instead of building
field-issue-specific equivalents keeps exactly one comment mechanism
and one evidence-upload mechanism for the whole system. Reusing
`can_override_gates` (rather than inventing `can_verify_field_issues`)
keeps "closure authority must use configurable permissions, never
hard-coded names" satisfied without a fourth near-identical permission
flag (see ASSUMPTIONS A42/A43).

## ADR-035 — Purchased spares are an authorization record, not a second inventory balance; consumption is always a real ledger movement
**Decision:** `PurchasedSpare` (new) records only the *authorization*
(actor, permission, quantity, compatibility, reason, timestamp,
related order line). It has no `quantity_available`/
`quantity_consumed` fields of its own — `InventoryLot` gained one
nullable `purchased_spare` FK (and reuses its pre-existing, previously
unused `bought_for_scope`/`bought_for_building` fields), and
`apps.procurement.services.spare_inventory_summary` computes every
quantity (received/available/reserved) by querying the existing
`InventoryMovement`/`InventoryReservation` ledger for lots linked to
that confirmation. `OrderLineAllocation` similarly never mutates a
prior allocation on reassignment — it flips `is_active` and creates a
new row linked via `reassigned_from`.
**Why:** The release is explicit: "do not alter inventory balances
through direct field updates" and "a spare later used in an apartment
must move through the existing auditable inventory movement,
reservation, delivery and installation architecture." Giving
`PurchasedSpare` its own quantity-tracking fields would create exactly
the second, competing source of truth core principle 4.5 already
forbids for ordinary inventory — spares are ordinary inventory with an
extra provenance link, not a separate ledger.

## ADR-034 — Drawing wraps an existing Document; a new revision is always a new row, never an edit
**Decision:** `apps.drawings.Drawing` has a `source_document` FK to the
pre-existing `apps.documents.Document` (SHA-256 hashing/duplicate
detection reused as-is) plus drawing-specific metadata (building/floor/
unit, discipline, type, status, revision). `supersede_drawing` always
creates a brand new `Drawing` row (`supersedes` FK to the prior one,
`revision+1`) and only ever flips the old row's `status`/`is_current` —
it never edits `source_document`, `building`, or any other field on the
historical row.
**Why:** The release is explicit: "a later drawing revision must never
silently replace the historical drawing linked to an earlier order or
installation." Any future model with an FK to a specific `Drawing`
(order allocation, installation, walkthrough, field issue) keeps
pointing at that exact row forever, by construction — there is no
"current revision" mutable pointer to accidentally follow into a newer
version. This is the same versioned-immutable-row pattern already used
for `ReleasePacketVersion`/`LandedCostVersion`/`StorageComparisonScenario`,
applied here rather than inventing a parallel "latest version" concept.

## ADR-033 — BuildingFamily is a new grouping layer above the pre-existing Building model, not a parallel hierarchy; permanent unit codes are stored, not derived
**Decision:** `BuildingFamily` (new) sits between `Project` and the
pre-existing `Building` model (`Building.family`, nullable for backward
compatibility with rows created before this release). `Unit` gained a
stored `permanent_code` field, generated once at creation
(`apps.projects.services.build_permanent_code`) and never regenerated —
it is a plain column, not a computed property, specifically so it
survives a later correction to the unit's floor/building assignment
without changing. The idempotent import
(`import_buildings_and_units`) matches existing rows by this code.
**Why:** `Project`/`Building`/`Floor`/`Unit`/`Area` already modeled
exactly "project → physical building → floor → apartment" — the
release asked for one more layer above Building (family/type grouping
like "ARENA T1" spanning 8 physical buildings), not a replacement
hierarchy. Extending the existing models in place, rather than
introducing a second building-hierarchy model set, keeps this the one
place `Delivery`/`InstallationRecord`/`MaterialRequest`/kits already
point to (`apps.workflow.services.resolve_project` was extended with a
`unit`/`building` fallback rather than duplicated). Storing the
permanent code as a plain field (not a `@property` computed from
current FKs) is what makes "must remain unchanged throughout
construction... occupancy... maintenance" actually true even if a data
entry mistake in the building/floor assignment is corrected later.

## ADR-032 — QR labels use one small entity registry instead of eight bespoke implementations; a scan only ever forwards into an existing, already-permission-checked page
**Decision:** `apps.labels.services._ENTITY_REGISTRY` maps each
supported entity type's model name to three pure functions: how to
find its organization (for isolation), what human-readable label/
context to print, and which existing URL a scan should redirect to.
`QRLabel.token` is an opaque, random value (same
`secrets.token_urlsafe` pattern as `apps.reports.models
.SecureShareLink.token`) — the QR image encodes only
`/qr/<token>/`, never the entity's real UUID. The scan-landing view
(`qr_scan_landing`) is `login_required` and, once authenticated,
re-checks organization membership before resolving the label, then
performs a plain HTTP redirect into the entity's own existing detail
view — that view's own `login_required`/organization-scoped
`get_object_or_404` runs again, independently. The scan endpoint
itself never mutates any state.
**Why:** Eight entity types (inventory lot, warehouse location,
receiving unit, dispatch, delivery, installation material record,
tool, container) named individually in the spec would otherwise
tempt eight near-identical view/service pairs. A small registry keeps
"how do I find this entity's organization" and "where does a scan of
this entity land" each defined exactly once, in one place, and makes
adding a ninth entity type later a one-entry addition, not a new
module. Reusing each entity's own existing detail view (rather than
building a parallel "QR-safe" view per entity) means a scan can never
accidentally expose more, or check less, than a normal logged-in visit
to that same page already does — there is exactly one permission
check per entity type, not two that could drift apart.

## ADR-031 — Supplier claims are a new `apps.claims` app; evidence and package generation reuse existing generic mechanisms rather than new per-claim models
**Decision:** `SupplierClaim` (new model, new `apps.claims` app) links
via optional FKs to every real record that can justify a claim
(`Supplier`, `PurchaseOrder`/`PurchaseOrderLine`, `Item`, `Shipment`,
`Container`, `ManifestVariance`, `Receipt`/`ReceiptLine`,
`Discrepancy`, `QuarantineRecord`, `Inspection`, `ReplacementCase`) —
never a re-entered copy of their data. Evidence reuses
`apps.audit.services.attach_evidence`/`Attachment` (ADR-022) instead of
a new `ClaimEvidence` model. Package generation reuses
`apps.reports._save_html_snapshot`/`ReportVersion` (which already had
an unused `CLAIM_PACKAGE` report-type choice, anticipating this exact
feature) instead of a new per-claim package-version model —
`_save_html_snapshot` gained one new optional parameter,
`content_object`, so the resulting `ReportVersion` can be traced back
to the specific claim it documents via the same generic
`content_type`/`object_id` pointer every other cross-cutting concern in
this system already uses (ADR-004).
**Why:** Unlike storage-suitability/external-storage (Priority 1 items
that activated already-modeled-but-dormant models), no `Claim` model
existed anywhere before this — confirmed by a repo-wide search. Given a
green field, the natural trap is inventing a self-contained
"claims module" that quietly re-implements evidence upload and report
snapshotting a third and fourth time. Reusing both existing generic
mechanisms keeps exactly one evidence-upload path and exactly one
HTML-snapshot/document-provenance path for the whole system, which is
also what the cross-cutting instruction for this delivery explicitly
asked for.

## ADR-030 — External-storage comparison scenarios are versioned like `LandedCostVersion`/`ReleasePacketVersion`, not edited in place; never fabricate a currency conversion
**Decision:** `StorageComparisonScenario` (new) wraps a set of
`AlternativeStorageOption` rows for one `ReceivingPlan`, using the same
`version_number` + `is_current` pattern as `ReleasePacketVersion`. Once
`status=FINALIZED`, `apps.receiving.services
.add_storage_option`/`update_storage_option` refuse further edits — a
new comparison creates a new version. Currency conversion for the
comparison table reuses `apps.cost.services.convert_to_base_currency`
(renamed from the private `_convert_to_base_currency`, now a public,
cross-app function) rather than a second implementation — an option in
a currency with no `ExchangeRate` on file shows `converted_total=None`
and an explanation, never a fabricated or assumed 1:1 rate.
**Why:** `AlternativeStorageOption` was modeled since Priority 0
(originally FK'd directly to `ReceivingPlan`) but had zero calling code
anywhere — confirmed before making any schema change. Re-parenting it
under a new versioned scenario, rather than adding an ad-hoc
"is_finalized" flag directly on the option or on `ReceivingPlan`, gives
"immutable historical scenario versions" (an explicit spec requirement)
the same treatment every other decided/frozen record in this system
already gets, instead of a bespoke one-off mechanism. Reusing
`apps.cost`'s exchange-rate lookup (rather than adding a second
`exchange_rate`/`exchange_rate_source` pair of fields directly on
`AlternativeStorageOption`) keeps "never fabricate a conversion rate"
enforced in exactly one place for the whole system.

## ADR-029 — Storage suitability is enforced at the put-away/transfer service layer, reusing previously-dormant models; blocking vs. warning is a fixed rule, not per-location config
**Decision:** `apps.inventory.services.check_location_suitability`/
`enforce_location_suitability` are the single point where a
`WarehouseLocation`'s configured `LocationSuitability`/`LocationCapacity`
row and a product's `ProductRiskProfile` are checked against a proposed
quantity. Both `apps.receiving.services.post_receipt_line` (put-away)
and the newly-activated `apps.requests.services.transfer_lot` call
through this one function — no separate suitability logic was written
for either workflow. Whether a given mismatch is *blocking* or
*warning-only* is a fixed rule in code (category/capacity violations
block; sensitive-material/environmental mismatches warn — see
ASSUMPTIONS.md A21), not a per-location configurable flag.
**Why:** `LocationSuitability`, `LocationCapacity`, and
`ProductRiskProfile` were all already modeled in a prior milestone but
had zero calling code anywhere in the codebase — building a second,
parallel capacity-tracking mechanism instead of wiring up the existing
one would have created two sources of truth for the same fact. Making
blocking-vs-warning a fixed rule rather than a per-location setting
keeps the authorization surface small and avoids a foot-gun where a
location could be misconfigured to hard-block put-aways with no
override path; the one override path that exists
(`can_override_gates` + written reason + `AuditEvent.Action.WAIVER`) is
reused verbatim from the Milestone 3 installation quantity-guard
override rather than inventing a second authorization concept.
**Also fixed while building this:** `apps.receiving.views
.receipt_line_update` previously called
`WarehouseLocation.objects.first()` as a placeholder — no receiving
location was ever genuinely selected by a user before this change; the
form now has a real `receiving_location` field scoped to the user's
organization.

## ADR-028 — Tool checkout uniqueness is enforced by query, not a database constraint
**Decision:** `apps.tools.services.is_checked_out`/`checkout_tool`
enforce "a tool cannot be checked out twice at once" by querying for an
existing `ToolCheckout` with an active assignment and no `ToolReturn`,
inside a `transaction.atomic` block — not a `UniqueConstraint` on the
model.
**Why:** The invariant is inherently about the *absence* of a related
row (no `ToolReturn` yet), which Django/PostgreSQL partial unique
constraints can't directly express against a reverse OneToOne without
a denormalized "is_active" flag duplicating state already derivable
from the data. `ToolAssignment.is_active` already exists for this
exact purpose (Priority 0) — reusing it as the query predicate, guarded
by the atomic block, is consistent with the `Handoff`
create-idempotency pattern (ADR from the Gate Controls milestone): an
app-level check inside a transaction, not a novel constraint shape.

## ADR-027 — CONFOTUR duplicate candidates are grouped live at read time, never a persisted "dismissed" state
**Decision:** `apps.customs.services.detect_duplicate_candidates`
recomputes the candidate list on every call by grouping still-live
(`is_duplicate_of__isnull=True`) `ConfoturLine` rows by shared
`quotation`/`manifest_line`; there is no "confirmed not a duplicate,
stop warning me" action or field.
**Why:** The project-wide convention (already established for
`Discrepancy`/`ManifestVariance`) is that a flagged risk stays visible
until genuinely resolved, never silently dismissed — "nothing in this
system closes a discrepancy by deleting it"
(`BUSINESS_REQUIREMENTS.md` §3). A permanent per-pair dismiss would
need a new model/field for something that isn't otherwise tracked, for
the sole purpose of making a real duplicate-exemption risk stop being
shown — the wrong trade-off for a control specifically about preventing
silent double-claimed exemptions (spec section 25).

## ADR-026 — Landed-cost allocation: last-line-absorbs-rounding, never-fabricate-a-conversion-rate
**Decision:** `apps.cost.services.run_allocation` computes every
allocated amount by proportional share except the *last* eligible line,
which instead gets `total_charge - sum_so_far` — guaranteeing the sum of
allocated amounts always exactly equals the original charge, with no
floating-point/rounding leftover silently dropped or invented.
`_convert_to_base_currency` returns `None` (not the original amount
treated as if already converted) when no `ExchangeRate` row exists for
a currency pair, and `calculate_landed_cost` propagates that `None`
through to `final_landed_cost_per_unit`/`total_landed_value` rather
than fabricating a number.
**Why:** Both are direct applications of core principle 4.3 (never
silently confirm/assume) to a domain (money) where a silent rounding
error or an invented exchange rate would be a real, hard-to-detect
financial-accuracy bug. Every other data-quality gap in this project is
handled the same way — recorded as `None`/unknown rather than guessed
(see `DATA_QUALITY_AND_UNCERTAINTY.md`) — and this extends that
convention to the landed-cost engine, the one place in the system that
touches real money math.

## ADR-025 — Detailed receiving manifest reuses the snapshot/document persistence path, not a new report mechanism
**Decision:** `apps.reports.views._save_html_snapshot` was factored out
of `shipment_snapshot` (previously inlined there) and is now called by
both `shipment_snapshot` and the new `receiving_manifest_snapshot` —
same `ReportVersion` + `Document`/`DocumentVersion` creation, same
SHA-256 hashing via the existing document storage layer, same
share-link eligibility (any `ReportVersion` with a
`rendered_html_document` can be attached to a `SecureShareLink`,
unchanged).
**Why:** The detailed receiving manifest is, mechanically, exactly the
same kind of artifact as the shipment snapshot (spec section 29,
self-contained HTML, no live dependency) — reusing the persistence path
means the new report automatically gets versioning, hashing, and
share-link support for free, and a second, subtly different
"generate-and-store-a-report" code path never gets a chance to drift
from the first one's behavior.

## ADR-024 — Assignment dropdowns scoped by the gate's own configured department, not a hard-coded name
**Decision:** `apps.requests.views._department_for_gate(organization,
gate_code, attr)` reads `GateDefinition.from_department`/`to_department`
— the same configuration row `apps.workflow.gates.evaluate_gate` and
`apps.workflow.services` already use — to decide which department's
members should populate an assignment dropdown
(`_assignable_users`), rather than filtering on a literal department
code string like `"obra"`.
**Why:** Every other place in the codebase that needs "who does this
kind of work" (gate `from_department`/`to_department`, `WorkflowStage`)
already reads it from configured `Department`/`GateDefinition` rows —
never a hard-coded name (ADR-002, and the explicit constraint repeated
in every milestone's kickoff instructions). Hard-coding `"obra"` in a
form's `__init__` would have been the one place in the whole delivery
that quietly broke that rule.

## ADR-023 — `DispatchLine.reservation` FK + always re-fetch the line with `select_for_update()` inside `create_dispatch`
**Decision:** Added a nullable `DispatchLine.reservation` FK
(`requests.0003_dispatchline_reservation`), and changed
`create_dispatch` to re-fetch each `MaterialRequestLine` by primary key
with `select_for_update()` at the top of every loop iteration, rather
than trusting whatever `MaterialRequestLine` instance the caller passed
in for that entry.
**Why:** Enabling a real multi-lot split-dispatch UI means a single call
to `create_dispatch` can legitimately carry several entries for the
*same* line (one per reservation/lot). A live HTTP test of exactly that
case (reserve 6 from lot A + 4 from lot B, dispatch both in one
submission) surfaced a real bug: the two entries' `MaterialRequestLine`
objects were distinct Python instances of the same DB row (produced by
`select_related` inside two separate `InventoryReservation` rows), so
saving `quantity_dispatched` from the first entry was silently
overwritten by the second entry's stale in-memory copy — only 4 of the
intended 10 units ended up marked dispatched. Re-fetching with
`select_for_update()` per iteration fixes the correctness bug and also
makes two concurrent dispatch calls for the same line serialize safely,
which the milestone's concurrency requirement calls for anyway. Caught
by `tests/test_delivery_installation_acceptance.py::TestMultiLotSplitDispatch`
before this reached a real user.
**Reservation-level guard:** the line-wide "reserved minus dispatched"
check alone cannot prevent dispatching more than one specific lot's
reservation holds when a line's reservations span multiple lots — added
`reservation_remaining_quantity()` as a second, independent guard.

## ADR-022 — Evidence upload reuses `apps.documents` + `apps.audit.Attachment`, no new upload path
**Decision:** `apps.audit.services.attach_evidence(target, user, *,
document_type, title, uploaded_file)` is the one function that turns an
uploaded file into evidence linked to *any* target
(Delivery/InstallationRecord/InspectionRecord today, trivially any future
model tomorrow). It calls `apps.documents.views.DocumentUploadForm`'s
validation and `Document`/`DocumentVersion` creation exactly as the
existing `/documentos/subir/` screen does, then creates one `Attachment`
row.
**Why:** A bespoke per-model "photo" field (the pattern
`InstallationRecord.photo_document` used, kept only for backward
compatibility) would have to reinvent extension/size validation and
SHA-256 duplicate detection for every new evidence-bearing screen.
`Attachment` was modeled from day one exactly for this
(`content_type`/`object_id`, ADR-004) but sat unused until this
milestone — the same "wire up an existing unused model instead of
inventing a parallel one" move already made for `UserProjectAccess`
(ADR-015) and `ResponsibilityAssignment` (ADR-016).

## ADR-021 — Final acceptance detail is captured *after* the generic `accept_handoff`, never instead of it
**Decision:** `installation_final_accept` (the domain-specific "aceptado /
aceptado condicionado + notas" screen) requires the `inspection_to_acceptance`
handoff to already be `ACCEPTED` before it does anything; it never calls
`accept_handoff` itself.
**Why:** The generic "Aceptar" button (`/flujo/<id>/aceptar/`) is a fully
valid, already-exposed entry point for accepting *any* handoff, including
this one — a real user can reach it directly from the workflow inbox
without ever visiting the installation detail page. An earlier version of
this view called `accept_handoff` itself before recording the domain
decision, which meant a user who instead used the generic inbox button
left the record permanently unable to capture the accepting authority's
accepted-vs-conditional decision (the dedicated screen only offered its
form for a still-`SUBMITTED` handoff, and once accepted generically there
was no path back to it). Caught during the live HTTP walkthrough for this
milestone, not by a unit test — unit tests called the service functions
directly and never exercised the two-URL interaction. Fixed by making the
domain screen strictly additive: it activates only once `accept_handoff`
(by whichever route) has already run, and it never re-implements or
races against that transition.

## ADR-020 — Model-level quantity guards are `AuditEvent.Action.WAIVER`, not `GateOverride`
**Decision:** `apps.requests.services.record_installation_progress` lets an
installation exceed its validly-delivered quantity only when the caller
passes `override_reason` and holds `can_override_gates`; this is logged as
an `AuditEvent.Action.WAIVER`, never a `GateOverride` row.
**Why:** `GateOverride` is deliberately shaped around the 8-gate
`Handoff` transition system (`gate_definition`, `handoff` FKs) — it
answers "why was this *stage transition* allowed to proceed while
blocked." An over-installation is a narrower, purely quantitative guard
inside a single model, with no corresponding gate transition or Handoff
row at the moment it happens. Reusing `GateOverride` here would force a
fake gate/handoff into existence just to hang a reason on, or would
weaken `GateOverride`'s FK constraints to make them optional — both worse
than reusing the same permission check (`can_override_gates`) with the
audit log the codebase already has for non-gate authorized exceptions.

## ADR-019 — Delivery/installation/inspection screens reuse `apps.workflow.services`, never a second permission engine
**Decision:** `apps.workflow.services` gained `user_can_access_project`
(factored out of `can_view_handoff`) and `can_view_target`, used by every
new `apps.requests.views` detail/action view via a small
`_deny_cross_project` guard.
**Why:** Cross-project isolation was already solved once, for `Handoff`,
in the Gate Controls milestone (ADR-015). The new Delivery/
InstallationRecord/InspectionRecord screens needed the identical rule
*before* a handoff necessarily exists for a given target yet (e.g. a
freshly-created `Delivery` with no handoff at all). Rather than
re-deriving project/organization scoping in `apps.requests.views`, the
existing check was generalized to operate directly on a target via the
already-existing `resolve_project`/`resolve_organization` helpers.

## ADR-018 — Installation and project-receipt creation are idempotent; inspection creation is deliberately not
**Decision:** `create_installation_record` and `create_project_receipt`
use a `select_for_update` + first-existing-wins pattern (mirroring
`get_or_create_delivery`/`create_handoff`) keyed on
`(project_receipt, delivery_line)` and `delivery` respectively.
`create_inspection` has no such guard.
**Why:** A double-click/retry on "Crear instalación" or "Registrar
recepción" must not create a second work order or a second receipt for
the same delivered material — confirmed as a real gap during this
milestone's own live HTTP walkthrough, where an accidental duplicate
`curl` POST created two `InstallationRecord` rows for the same
`delivery_line` before this fix (see `docs/implementation-log.md`, and
`tests/test_delivery_installation_acceptance.py::test_21c/21d`).
Inspections are the opposite case: a genuine reinspection is *supposed*
to create a new row every time (that is the entire point of
`previous_inspection` chaining, ADR-017 in the model docstrings) — adding
duplicate-prevention there would silently block a legitimate second
inspection. This is recorded as an honest, deliberate gap in
`docs/KNOWN_LIMITATIONS.md` rather than papered over.

## ADR-017 — Reinspection chains via a self-referential FK; failed history is never overwritten
**Decision:** `InspectionRecord.previous_inspection` points to the prior
cycle; `apps.workflow.gates.evaluate_inspection_to_acceptance` only reads
the single most-recent inspection's `passed` value (plus all-time open
*blocking* punch-list items across every inspection in the chain).
**Why:** Spec requirement: a failed inspection must never be overwritten
or deleted on reinspection. Modeling each cycle as its own permanent row
(rather than mutating one row's result field) makes this the structural
default rather than something application code has to remember to
preserve.

## ADR-016 — `ResponsibilityAssignment` reused (not replaced) for ownership transfer
**Decision:** `accept_handoff()` closes any open `ResponsibilityAssignment`
for the target and opens a new one, rather than introducing a new
"current owner" model.
**Why:** `ResponsibilityAssignment` already existed from the Priority 0
milestone specifically to answer "who owns this record now" (ADR-004
established the generic content-type pattern this relies on) but was
never actually written to by any code path. This milestone is what
finally makes it real, instead of adding a parallel concept.

## ADR-015 — `UserProjectAccess` enforced for the first time
**Decision:** `can_view_handoff`/`can_accept_handoff` check
`UserProjectAccess` for any handoff whose target resolves to a project
(currently `MaterialRequest`), with a bypass for management-role users.
**Why:** `UserProjectAccess` was modeled in the Priority 0 milestone but
`grep`-confirmed unused anywhere before this milestone. Cross-project
isolation was an explicit requirement here, and this was the obvious
existing model to wire up rather than inventing a second
project-authorization mechanism.

## ADR-014 — Gate readiness is evaluated by a pure function registry, not stored as a workflow engine's state machine
**Decision:** `apps.workflow.gates.GATE_EVALUATORS` maps a gate `code` to
a plain Python function returning a `GateResult`; there is no generic
rule-configuration UI or DSL.
**Why:** The 8 required gates each depend on genuinely different
business signals (payment milestones, manifest variances, quarantine
records, reservation quantities, installation/inspection records) drawn
from models that already exist across 6 different apps. A configurable
rule engine would need to reinvent expressive power Python already has,
for a fixed, spec-mandated set of 8 transitions — not a case where more
abstraction pays for itself. Each evaluator is independently unit-tested
(`tests/test_workflow_gates.py`).

## ADR-013 — `ServiceLevelTarget` reused for overdue tracking, no new SLA model
**Decision:** `apps.workflow.services.is_overdue()` compares
`Handoff.submitted_at` against `GateDefinition.to_stage.sla_targets`.
**Why:** `ServiceLevelTarget` (tied to `WorkflowStage`) already existed
from the Priority 0 milestone and was unused. Reused rather than adding a
duplicate `Handoff.due_at`/SLA concept.

## ADR-012 — `.dockerignore` must exclude `.env`; production DB choice guarded by `DEBUG`
**Decision:** Added `.dockerignore` excluding `.env`/`.env.*` (except
`.env.example`), and added `if DEBUG and env_bool("USE_SQLITE_FOR_TESTS")`
(previously just the env check alone) in `settings.py`.
**Why:** During production-stack validation, the local dev `.env`
(`USE_SQLITE_FOR_TESTS=1`) was copied into the Docker image by `COPY . /app/`
and silently made the containerized app run against an ephemeral
in-container SQLite file instead of the real Postgres volume — migrations
"succeeded" and queries "worked," but the actual Postgres database stayed
empty the whole time, and a restart appeared to preserve data only because
the container's writable layer (not a volume) briefly survived a `restart`.
This was caught by directly querying Postgres via `psql` and finding zero
tables. The `DEBUG` guard makes this whole class of bug structurally
impossible even if a future dev `.env` leaks into an image some other way.

## ADR-011 — `env_file:` in docker-compose.prod.yml instead of hand-enumerated `environment:` keys
**Decision:** The `web` service loads `env_file: .env.production` instead
of manually listing each variable under `environment:`.
**Why:** During validation, `DJANGO_SECURE_SSL_REDIRECT=0` was added to
`.env.production` to allow IP-only HTTP testing, but the container never
saw it because only a hand-picked subset of variables was forwarded.
Compose's `--env-file` flag only affects variable *substitution inside
the YAML itself*, not automatic container environment injection — a
subtlety easy to get wrong exactly as it was gotten wrong here.

## ADR-010 — Vendor Bootstrap/htmx locally, including source maps
**Decision:** `static/vendor/` contains `bootstrap.min.css(.map)`,
`bootstrap.bundle.min.js(.map)`, `htmx.min.js`, fetched once at build time
rather than loaded from a CDN at runtime.
**Why:** Spec requires the app to function without a cloud dependency.
Whitenoise's `CompressedManifestStaticFilesStorage` hard-fails
`collectstatic` if a vendored CSS/JS file references a `sourceMappingURL`
that doesn't exist on disk — caught during production validation and
fixed by downloading the real `.map` files (and setting
`WHITENOISE_MANIFEST_STRICT = False` as a defensive fallback for any
future vendored asset with the same issue).

## ADR-009 — Dev virtualenv lives outside the project directory
**Decision:** The local Python virtualenv used for `manage.py`/`pytest`
during development is created under the session scratchpad, not inside
`Application/.venv`.
**Why:** The repository's parent directory name contains a literal `:`
character, which both `python -m venv` and Docker's bind-mount volume
parser treat as a path separator, breaking venv creation and Caddyfile
mounting respectively. This is purely a local-filesystem quirk of this
delivery environment; a real deployment path (e.g.
`/opt/dtbeach-supply-control`) will never hit it. The Docker image itself
is unaffected since `COPY . /app/` happens inside the build context, not
via the affected bind-mount mechanism.

## ADR-008 — One Django project, 18 apps, no microservices
**Decision:** Modular monolith (see `ARCHITECTURE.md`).
**Why:** Explicit spec preference; also matches actual pilot scale (single
server, ~8 named users, one organization).

## ADR-007 — UUID primary keys everywhere
**Decision:** `apps.core.models.BaseModel` uses `UUIDField` as PK.
**Why:** Spec section 12 requires share links and QR labels to never
expose sequential IDs.

## ADR-006 — Ledger-only inventory, no direct quantity field
**Decision:** `InventoryLot` has no `quantity_on_hand` field; on-hand is
always computed from `InventoryMovement` at read time.
**Why:** Core principle 4.5. Directly demonstrated in
`tests/test_receiving_and_inventory.py::test_onhand_quantity_is_derived_from_ledger_never_edited_directly`.

## ADR-005 — Documents are immutable; replace = new version
**Decision:** `DocumentVersion` rows are never updated after creation;
`Document.current_version` always resolves the latest.
**Why:** Core principle 4.4.

## ADR-004 — Generic content-type pointers for cross-cutting concerns
**Decision:** `DocumentFieldSource`, `StageAssignment`, `Handoff`,
`ResponsibilityAssignment`, `Comment`, `Attachment`, and
`AuditEvent` all use `(content_type, object_id)` rather than a per-model
FK.
**Why:** These concerns apply identically to a `PurchaseOrder`, a
`Shipment`, a `MaterialRequest`, etc. — a per-model handoff/audit table
per domain object would multiply the model count without adding
behavior.

## ADR-003 — Official BL and internal manifest are separate tables, bridged by `ManifestVariance`
**Decision:** See `OFFICIAL_VS_OPERATIONAL_MANIFEST_ANALYSIS.md`.
**Why:** Non-negotiable per spec section 13A; verified against the real
live-container fixture, not just designed in the abstract.

## ADR-002 — Named pilot users are seed data, not code
**Decision:** `apps/accounts/management/commands/seed_pilot_data.py`
creates Harrison/Edison/Markeris/Lucía/Manuel/Óscar/Miguel/María Luisa
with configurable roles; no view or model branches on a username.
**Why:** Explicit spec requirement; also just good practice.

## ADR-001 — PostgreSQL only in Docker/production, SQLite only for a debug-gated local shortcut
**Decision:** See ADR-012 above for the guard that was added after this
was found to be under-enforced.
**Why:** Spec mandates PostgreSQL; a debug-only local shortcut is still
useful for fast iteration without Docker running.
