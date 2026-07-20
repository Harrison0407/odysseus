# Milestone 1 — Configurable Procurement Gates A1–A6 Charter

Status: **DRAFT — DOCUMENTATION ONLY. NOT INDEPENDENTLY REVALIDATED. NOT OWNER-APPROVED.**

Charter version: 3 (Milestone 1 Charter Correction Cycle 2, 2026-07-20)

Produced by: Milestone 1 Charter Definition and Reconciliation cycle,
2026-07-20 (version 1), corrected by the Milestone 1 Charter Correction
Cycle, 2026-07-20 (version 2), resolving every finding of the independent
Milestone 1 Charter Revalidation (REVAL-001 through REVAL-012, §21.2), and
corrected again by the Milestone 1 Charter Correction Cycle 2, 2026-07-20
(version 3), resolving every finding of the independent Milestone 1 Charter
Version 2 Revalidation performed against commit
`3b62228b4a6efb4079e7f8c010e107fcf9de639a` — NF-1, REVAL-004-RESIDUAL,
REVAL-005-RESIDUAL, REVAL-008-RESIDUAL, REVAL-009-TRACE,
REVAL-011-ENFORCEMENT, NF-2, NF-3, NF-4, and NF-7 (§21.3). Version 3 has
**not** itself been independently revalidated.

Supersedes: the 26-line acceptance-criteria summary in
`docs/MARKETMATCH_ARCHITECTURE_RECONCILIATION_AND_ROADMAP.md` §3 "Milestone 1
— Configurable Procurement Gates A1–A6" as the *operational* design
reference. That roadmap section remains the approved statement of *intent*
and *priority*; this document is the binding statement of *how*. Where the
two conflict on mechanism (not on intent), this Charter governs.

This document resolves every finding of the independent Milestone 1 Charter
Review (CHTR-001 through CHTR-012), every finding of the independent
Milestone 1 Charter Revalidation of version 1 (REVAL-001 through
REVAL-012), and, as of version 3, every finding of the independent
Milestone 1 Charter Version 2 Revalidation (NF-1, REVAL-004-RESIDUAL,
REVAL-005-RESIDUAL, REVAL-008-RESIDUAL, REVAL-009-TRACE,
REVAL-011-ENFORCEMENT, NF-2, NF-3, NF-4, NF-7 — see §21 for the complete
disposition tables). It does not implement anything. No application code,
template, test, or migration was written or modified to produce it. A1–A6
remain unimplemented after this document is committed. Implementation of
this Charter requires a separate, subsequent, explicit owner authorization
following a fresh, successful independent revalidation of this version —
see §22.

---

## 0. Scope and non-goals

**In scope for Milestone 1:** everything in §1–§20 of this Charter — the
gate policy architecture, the A1–A6 gate lifecycle for `ProcurementPackage`,
evidence reuse and classification, the A2 freeze/change-control mechanism,
the procurement-domain override mechanism, and the narrow API/UI/admin
surfaces listed in §16.

**Explicitly not in scope for Milestone 1** (unchanged from the roadmap, and
restated here because CHTR-009 found the boundary was previously implicit):

- Any change to `apps.workflow` — `WorkflowStage`, `GateDefinition`,
  `Handoff`, `HandoffChecklist`, `HandoffEvidence`, `HandoffDecision`, or the
  existing `apps.workflow.GateOverride` model. That system remains a
  separate, completed, eight-gate departmental Handoff workflow. Nothing in
  this Charter renames, extends, reinterprets, or reuses its rows.
- Any change to `ProcurementPackage.Status` (`draft`/`active`/`frozen`/
  `closed`) as a state machine. A1–A6 state is additive and orthogonal.
- Milestone 2 (Internationalization Foundation) as a complete localization
  layer — Milestone 1 only requires translation-*readiness* (§17).
- Milestone 3 (Authorization-Converged API and Output Integrations) —
  generalized search/export/report/QR/notification convergence.
- Milestone 4 and later (production hardening, payment legs, customs,
  asset passport, optional automation).
- Any redesign of the owner-accepted Controlled Transparency, Commercial
  Confidentiality & Authorization Foundation (`apps.governance`,
  `apps.audit`, `apps.procurement` commercial layers) as accepted at commit
  `2c52b0b83340fda2eaa84700eaeddfbe0839d6d8`.

**Naming and placement.** All new models defined by this Charter live in a
new Django app, `apps.procurement_gates`, distinct from `apps.procurement`
(commercial layers), `apps.workflow` (eight-gate Handoff), and `apps.audit`
(evidence/audit primitives). This keeps the three existing, completed
domains untouched, satisfies AR-10's "no parallel workflow engine" rule
(this is the one authorized *new* domain, not a duplicate of an existing
one), and gives Milestone 1 its own migration history isolated from the
owner-accepted foundation's migrations.

---

## 1. Separate procurement gate domain

**Decision (binding):**

- A1–A6 is implemented as `apps.procurement_gates`, a new app.
- A1–A6 state is never stored on, merged into, or derived from
  `ProcurementPackage.Status`, `Handoff.status`, `WorkflowStage`, or
  `apps.workflow.GateDefinition`.
- `ProcurementPackage.Status` continues to mean the same thing it means
  today (`draft`/`active`/`frozen`/`closed`) after Milestone 1 ships. No
  migration in this Charter alters its choices, default, or meaning.
- The existing eight-gate Handoff workflow (`apps.workflow`) is not
  imported into, subclassed by, or referenced as a foreign key target from
  any `apps.procurement_gates` model. The two systems may coexist on the
  same `ProcurementPackage`-adjacent objects (e.g. a shipment tied to a
  package may separately have Handoffs) without one knowing about the
  other's internal state.
- Every model, service function, and test added for Milestone 1 lives under
  `apps.procurement_gates`, `apps.procurement_gates.services`, and
  `apps.procurement_gates.tests` (or an equivalent `tests/test_procurement_gates_*.py`
  file under the repository's existing top-level `tests/` convention — the
  implementer picks one placement convention and applies it uniformly, but
  does not split gate tests across both without reason).

This resolves CHTR-001's domain-model half directly and is the load-bearing
decision every other section in this Charter assumes.

---

## 2. Canonical gate sequence

**Stable gate codes** (never renumbered, never reused for a different
meaning once referenced by history):

| Code | Name | Meaning |
|---|---|---|
| `A1` | Deal Established | Parties, package roles, responsibilities, seller/exporter/logistics/quality authorities, visibility mode, and required approvals are explicit for this package. |
| `A2` | Technical Freeze | The approved technical/commercial revision is frozen (§7). Critical changes require an approved Change Request and place the package on hold (§8). |
| `A3` | Production Evidence | Production start/progress evidence satisfies the pinned policy version's A3 evidence schema (§5). |
| `A4` | Quality Control | QC/inspection evidence is independently reviewed and approved (uploader ≠ verifier, enforced per §5). |
| `A5` | Packing Verification | Counts, packing, labels, and package/product traceability evidence satisfy the pinned policy version's A5 evidence schema. |
| `A6` | Forwarder Handoff Readiness | Required documents, verified package state, approvals, and transport-handoff evidence are complete. |

**Ordering rule (binding):** gate `A(n)` may reach `PASSED` only if gate
`A(n-1)` is currently `PASSED` (not `INVALIDATED`, not `EXPIRED`,
not `SUPERSEDED`) and the package is not currently `is_on_hold`. `A1` has
no predecessor and is gated only by its own evidence/approval requirements.
There is no configuration flag to disable this ordering, skip a gate, or
run gates out of order or in parallel — strict `A1→A2→A3→A4→A5→A6` is not
itself a policy-configurable property; only the *content* of each gate's
evidence/approval requirements is configurable (§3).

**A6 scope statement (binding, resolves ambiguity flagged during charter
authorship):** `A6 = PASSED` means the package is ready to hand off to the
forwarder. It explicitly does **not** mean, and no service function or UI
label may imply: shipment completed, customs clearance completed, title
transferred, payment settled, delivery completed, or accounting finalized.
Those remain governed by `apps.shipments`, `apps.receiving`, Milestone 5
(payment legs), and Milestone 6 (customs), none of which this Charter
touches.

---

## 3. Policy architecture

Resolves CHTR-003.

### 3.1 Models

**`GatePolicy`** — stable identity for a named policy family.

- `id` (UUID), `created_at`, `updated_at`, `created_by` (standard `BaseModel`).
- `organization` — FK to `accounts.Organization`, nullable. **Null means
  only that this `GatePolicy` is platform-scoped (not owned by any one
  organization) — it is a necessary precondition for, but never itself
  the marker of, canonical-default status.** Non-null scopes the policy to
  one organization's own configuration and makes it permanently ineligible
  to ever be the canonical default (§3.3). Whether a platform-scoped
  (`organization = NULL`) policy actually *is* the canonical default is
  determined exclusively by `is_canonical_default` (§3.3) — never by
  `organization` alone.
- `code` — slug, unique within `(organization, code)`.
- `name` — display name.
- `is_active` — soft-disables offering this policy for *new* pinnings; never
  affects packages already pinned to one of its versions.
- `is_canonical_default` — boolean, default `False`. See §3.3 for the full
  singleton rule. Settable only on a row where `organization IS NULL`; the
  service layer rejects setting it on any organization-owned policy.

**`GatePolicyVersion`** — the actual, immutable, versioned schema.

- Standard `BaseModel` fields.
- `policy` — FK to `GatePolicy`.
- `version_number` — `PositiveIntegerField`, unique per `policy`,
  monotonically increasing, assigned at creation (never reused, never
  decremented, never renumbered after assignment — including for withdrawn
  or superseded versions).
- `status` — `DRAFT` / `PUBLISHED` / `WITHDRAWN`.
- `gate_schema` — `JSONField`. **Must contain exactly six top-level keys,
  one per gate code `A1`–`A6`, no more and no fewer** (binding completeness
  rule, §3.2/§10 test list) — a gate with genuinely no evidence
  requirements under this policy still has an explicit entry with an empty
  requirement-code list, never a missing key. Each of the six entries
  contains: the ordered list of required evidence-requirement codes (§5.2)
  valid for that gate under this policy version, the capability code(s)
  required to record a `GateDecision` for that gate,
  `attempt_creation_capability` (**required, binding, resolves NF-3 and
  REVAL-005-RESIDUAL** — a single registered capability code, drawn from
  the existing `CapabilityGrant` capability-code registry, that authorizes
  *opening* a `GateAttempt` for this gate; deliberately separate from the
  gate's `GateDecision` capability above and from `CREATE_EVIDENCE` — see
  §13 for the full authorization table and the canonical policy's values),
  `overridable` (boolean — whether any override may be requested for this
  gate at all, §12.1; **binding for `A2`: this value must always be
  `False`, §12.1**), `non_overridable_requirements` (list of requirement
  codes from this gate's own requirement list that remain mandatory even
  when `overridable=True`, §12), and `override_satisfies_successor_predecessor`
  (boolean, default `False`, §12 item 5).
- `published_at` — nullable `DateTimeField`; set exactly once, only by the
  publish operation (§3.2).
- `published_by` — FK to user, nullable, set with `published_at`.
- `supersedes` — self FK, nullable, set when this version is created as a
  direct successor to a previous version of the same `policy`.
- `withdrawal_reason` — text, set only when `status` transitions to
  `WITHDRAWN`.

### 3.2 Publication rule (binding)

- A `GatePolicyVersion` is **editable only while `status == DRAFT`**. Any
  field on `gate_schema` may be freely rewritten pre-publication.
- **Gate-schema completeness validation (binding, resolves REVAL-010):**
  `publish_policy_version` must reject publication unless `gate_schema`
  contains exactly the six keys `A1`, `A2`, `A3`, `A4`, `A5`, `A6` — no
  missing key, no unrecognized extra key — and every entry independently
  passes schema validation (required fields present and correctly typed:
  requirement-code list, decision capability, `attempt_creation_capability`,
  `overridable`, `non_overridable_requirements`,
  `override_satisfies_successor_predecessor`). An entry with an empty
  requirement-code list is valid (a gate may legitimately require no
  evidence, only a decision); a *missing* entry is never valid. Runtime
  gate evaluation (§9) never interprets a missing gate entry as any
  particular default — it cannot occur, because publication rejected it.
- **`attempt_creation_capability` validation (binding, resolves NF-3 and
  REVAL-005-RESIDUAL):** every one of the six entries must declare a
  non-null, non-empty `attempt_creation_capability` naming a capability
  code registered in the existing capability-code registry (§13); a
  missing, null, or unregistered value is rejected at publication, never
  defaulted or inferred at runtime. Once published, `attempt_creation_capability`
  is immutable exactly like every other field on a `PUBLISHED`/`WITHDRAWN`
  row (§3.2 above). For the canonical A1–A6 policy shipped by the
  Milestone 1 data migration (§4.2), every gate's
  `attempt_creation_capability` is `CREATE_PROCUREMENT_GATE_ATTEMPT`
  (§13).
- **A2 non-overridability validation (binding, resolves REVAL-004-RESIDUAL):**
  `publish_policy_version` must reject publication of any `gate_schema`
  whose `A2` entry has `overridable = True`. `A2` — Technical Freeze — is
  never overridable in Milestone 1, absolutely, with no policy opt-in of
  any kind; this is a hardcoded publication-time rejection, not a default
  that a sufficiently-motivated policy author could override. See §12.1
  for the full binding rule and its rationale.
- Publishing is a single, atomic, one-way transition `DRAFT → PUBLISHED`
  performed by a dedicated service function
  (`apps.procurement_gates.services.publish_policy_version`), never a
  generic model save. It requires the `PUBLISH_GATE_POLICY` capability
  (§13, new capability code), sets `published_at`/`published_by`, and from
  that instant the row is immutable: no service function may write to
  `gate_schema`, `version_number`, or `policy` on a `PUBLISHED` or
  `WITHDRAWN` row. This is enforced in the service layer (reject the
  write) and additionally documented as a database-level invariant to be
  verified by a migration-time constraint or model `save()` guard — the
  implementer chooses the exact enforcement mechanism but must enforce it
  in more than a UI convention.
- A change to a published version's requirements is **never** an edit; it
  is authored as a new `DRAFT` version (`supersedes` = the prior version),
  then published, receiving the next `version_number`.
- **Withdrawal** (`PUBLISHED → WITHDRAWN`) marks a version unavailable for
  *new* pinnings. It never mutates `gate_schema` and never affects any
  `PackagePolicyAssignment` already pinned to that version — a withdrawn
  version remains fully valid and enforceable for every package already
  pinned to it. Withdrawal requires a written reason.
- **No scheduled/future activation** is implemented in Milestone 1
  (explicit non-goal, per the governing decision). Publish takes effect
  immediately upon the publish call; there is no "effective_from" for
  policy versions.
- **Historical reproducibility:** given any `PackagePolicyAssignment`, the
  exact `gate_schema` that governed that package's A1–A6 progression at
  any past moment is reconstructable by reading the pinned
  `GatePolicyVersion` row — never by re-reading a `GatePolicy`'s "current"
  state, which does not exist as a queryable concept (only versions have
  schema).

### 3.3 Canonical default and organization configuration

**Single, unambiguous singleton mechanism (binding, resolves REVAL-003):**
`is_canonical_default = True` is the **sole** determinant of the canonical
default policy family. There is no second, independent mechanism —
`organization = NULL` is a *necessary precondition* (only a platform-scoped
policy may ever be flagged canonical; an organization-owned policy can
never be canonical, full stop, enforced by the service layer refusing to
set the flag on any row with a non-null `organization`), but it is **not**
by itself sufficient and does **not** by itself mean "this is the canonical
default." Multiple platform-scoped (`organization = NULL`) `GatePolicy`
rows may coexist (e.g. an experimental platform-wide policy family not yet
promoted); at most one of them may ever have `is_canonical_default = True`
at a time, enforced by a database-level conditional unique constraint
(**resolves NF-7 — corrected from an invalid `fields=[]` example in
version 2**):

```python
models.UniqueConstraint(
    fields=["is_canonical_default"],
    condition=models.Q(is_canonical_default=True),
    name="unique_canonical_gate_policy",
)
```

or the equivalent single-row-true invariant for the Django version in use.
This constraint alone is necessary but not sufficient; it must be paired
with the service-layer/model-validation rules below, all of which are
required, not aspirational: (1) only a platform-scoped (`organization IS
NULL`) `GatePolicy` may ever be flagged canonical — the service layer
rejects setting the flag on any organization-owned row; (2) an
organization-owned policy can never become canonical by any path,
including direct model manipulation guarded by a `save()`-level check;
(3) exactly one usable published canonical version (`is_canonical_default
= True` and at least one `PUBLISHED`, non-`WITHDRAWN`-only
`GatePolicyVersion`) must exist before any package-policy assignment or
populated-database migration (§4.5) is permitted to run; (4) withdrawal of
the canonical default's only published version is rejected whenever doing
so would leave zero usable canonical defaults, per the availability
invariant below.

- A fresh installation seeds exactly one platform-scoped `GatePolicy` with
  `is_canonical_default = True` plus one initial `PUBLISHED`
  `GatePolicyVersion` under it, via a data migration (§4), not via test
  fixtures or admin-only manual setup.
- **Availability invariant (binding, resolves REVAL-003):** the system must
  never reach a state with zero usable (`is_canonical_default = True` AND
  possessing at least one `PUBLISHED`, non-`WITHDRAWN`-only
  `GatePolicyVersion`) canonical default. Withdrawing the canonical
  default's only published version is rejected by the service layer unless
  a replacement published version under the same policy (or a newly
  designated canonical policy with its own published version) already
  exists — the same rule applies before any populated-database migration
  (§4.5) may run: it must first verify a usable canonical default exists,
  and fail loudly rather than silently proceeding without one.
- An organization *may* create its own `GatePolicy` (`organization` set to
  itself). Doing so does not disable the canonical default for other
  organizations; it only changes what that one organization's admins may
  pin new packages to. Such a policy can never have `is_canonical_default`
  set, per the precondition above.
- Resolution order when a package enters `A1` and a policy must be chosen
  (§3.4): an explicit policy version chosen by an authorized admin at
  package-creation/A1 time, else the requesting organization's own active
  `GatePolicy`'s latest `PUBLISHED` version if one exists, else the one
  policy with `is_canonical_default = True`'s latest `PUBLISHED` version.
  This order is fixed by this Charter, not itself configurable.

### 3.4 Package pinning (binding)

- **`PackagePolicyAssignment`** — one row per `(ProcurementPackage,
  is_active=True)` pair, enforced by a partial unique constraint (unique
  on `package` where `is_active=True`), analogous to the existing
  `unique_active_handoff_per_target_gate` pattern in
  `apps.workflow.Handoff.Meta.constraints`.
- Fields: `package` (FK), `policy_version` (FK to `GatePolicyVersion`,
  must be `PUBLISHED` at assignment time — assigning a `DRAFT` or
  `WITHDRAWN` version is rejected by the service layer), `pinned_at`,
  `pinned_by`, `is_active`. **No `superseded_by` field (binding, resolves
  NF-2 — removed from version 2):** Milestone 1 supports exactly one
  permanent pinned assignment per package for the life of its gate
  progression; there is no re-pinning, supersession, or reassignment
  concept to link, so no self-FK for it exists on this model. A future
  milestone may introduce versioned reassignment only through a
  separately-approved architecture decision and its own migration; this
  Charter reserves no field, placeholder, or column for that possibility.
- **Pinning happens exactly once per package's A1 entry**, inside the same
  `transaction.atomic()` block that creates the package's first `A1`
  `GateAttempt` (§9), using `select_for_update()` on the `ProcurementPackage`
  row to prevent two concurrent A1-entry calls from both succeeding
  (§14.4). A package is never re-pinned to a different policy version
  while active — the pin is for the *life of the package's gate
  progression*, not per-gate. (There is deliberately no "policy migration
  mid-package" concept in Milestone 1; a package that needs a different
  policy is an explicit, documented business exception outside this
  Charter's scope, not a silent re-pin.)
- Once pinned, all six gates for that package are evaluated exclusively
  against that one `GatePolicyVersion`'s `gate_schema`, for the life of the
  package, regardless of later publications, withdrawals, or new default
  changes.

### 3.5 Foreign-key deletion policy (binding, resolves NF-4)

Version 2 specified every foreign key on every new model without stating
`on_delete` behavior anywhere in the document — a systemic omission across
all ~8 new models. This section is binding and exhaustive: every FK listed
below must use exactly the stated `on_delete` value; no field-specific
deviation is permitted without a documented, Charter-amendment-level
reason.

**General rule:** no history-critical foreign key may use `CASCADE`. A
current-state projection row (`PackageGateState`, §9.6) may be rebuilt,
but no operation may silently cascade-delete an immutable historical row
(`GateAttempt`, `GateEvaluation`, `GateDecision`, `GateInvalidation`,
`PackageFreezeRevision`, `ProcurementGateOverride`) as a side effect of
deleting something it references. `ProcurementPackage` deletion must be
blocked (via `PROTECT`) while any gate history exists for it, exactly as
the accepted foundation already blocks deletion of referenced `Document`
rows (`Attachment.document`, `EvidenceItem.document`, both `PROTECT` in
`apps.audit.models`).

| Model | FK field | `on_delete` | Rationale |
|---|---|---|---|
| `GatePolicy` | `organization` | `SET_NULL` (nullable, unchanged meaning — see §3.1) | Not a deletion-safety field; `NULL` is a valid, meaningful state already. |
| `GatePolicyVersion` | `policy` | `PROTECT` | A version can never outlive the policy family identity it belongs to. |
| `GatePolicyVersion` | `published_by` | `SET_NULL` | Actor account retention is not guaranteed; the published fact and timestamp remain regardless. |
| `GatePolicyVersion` | `supersedes` | `PROTECT` | The version-supersession chain is historical and must never silently break. |
| `PackagePolicyAssignment` | `package` | `PROTECT` | Deleting a package must not silently delete its policy pin. |
| `PackagePolicyAssignment` | `policy_version` | `PROTECT` | A pin must never be able to point at nothing; the pinned version is permanent history (§3.4). |
| `PackagePolicyAssignment` | `pinned_by` | `SET_NULL` (nullable already, §4.2) | Matches the existing system-assignment convention. |
| `PackageFreezeRevision` | `package` | `PROTECT` | Freeze history must survive as long as any reference to it could exist; package deletion is blocked while history exists. |
| `PackageFreezeRevision` | `policy_version` | `PROTECT` | The historical self-containment rationale (§7.1) requires this reference never silently vanish. |
| `PackageFreezeRevision` | `actor` | `SET_NULL` | Actor account retention is not guaranteed. |
| `PackageFreezeRevision` | `predecessor` | `PROTECT` | The revision chain (§7.1) must never have a broken link. |
| `PackageFreezeRevision` | `source_change_requests` (M2M) | N/A (M2M; through-row deletion only removes the association, never the `ChangeRequest` or the revision) | M2M rows carry no independent historical meaning beyond the association itself. |
| `GateAttempt` | `package` | `PROTECT` | Same package-deletion-blocked rationale as above. |
| `GateAttempt` | `opened_by` | `SET_NULL` | Actor account retention is not guaranteed. |
| `GateEvaluation` | `attempt` | `PROTECT` | An evaluation must never be able to outlive the attempt it evaluated. |
| `GateEvaluation` | `evaluated_by` | `SET_NULL` (nullable already, §9.3) | Matches the existing system-evaluation convention. |
| `GateDecision` | `attempt` | `PROTECT` | A decision must never be able to outlive the attempt it closed. |
| `GateDecision` | `evaluation` | `PROTECT` | The decision's evidentiary basis must remain traceable forever. |
| `GateDecision` | `decided_by` | `SET_NULL` | Actor account retention is not guaranteed. |
| `GateInvalidation` | `decision` | `PROTECT` | An invalidation record is meaningless without the decision it invalidates. |
| `GateInvalidation` | `invalidated_by` | `SET_NULL` (nullable already, §9.4) | Matches the existing system-invalidation convention. |
| `GateInvalidation` | `trigger_content_type`/`trigger_object_id` | `SET_NULL` on `trigger_content_type` (generic FK; the historical `reason` text and safe trigger-id reference remain even if the triggering row is later removed) | Mirrors the existing generic-FK convention used by `audit.EvidenceBundle`/`Comment`/`Attachment`. |
| `ProcurementGateOverride` | `package` | `PROTECT` | Same package-deletion-blocked rationale as above. |
| `ProcurementGateOverride` | `policy_version` | `PROTECT` | The pinned-version-scope invariant (§11.2) must remain traceable. |
| `ProcurementGateOverride` | `attempt` | `PROTECT` | An override record is meaningless without the attempt it applied to. |
| `ProcurementGateOverride` | `organization` | `SET_NULL` | Matches the existing `Handoff.organization` denormalization convention this field mirrors (§11.2). |
| `ProcurementGateOverride` | `requested_by`, `decided_by`, `revoked_by` | `SET_NULL` | Actor account retention is not guaranteed for any of the three. |
| `PackageHoldCause` | `package` | `PROTECT` | Same package-deletion-blocked rationale as above. |
| `PackageHoldCause` | `reference` (generic FK to `ChangeRequest`/`RiskFlag`) | `SET_NULL` on the content-type FK | Mirrors the existing generic-FK convention; the hold-cause record's own `cause_type`/timestamps remain meaningful independent of the referenced row's survival. |
| `PackageGateState` (cache, §9.6) | `package` | `CASCADE` | The **only** permitted `CASCADE` in this Charter — this row is an explicitly non-authoritative, rebuildable cache (§9.6); deleting it loses nothing that `rebuild_gate_state` cannot regenerate, and it must never block package deletion the way a historical row would. |

Required tests (§18): for every `PROTECT` relationship above, a test
attempting the blocked deletion and asserting it is rejected; for
`PackageGateState`, a test asserting its deletion is harmless and
`rebuild_gate_state` regenerates it identically.

---

## 4. Existing-package initialization

Resolves CHTR-004.

### 4.1 Binding rule

No existing `ProcurementPackage` row may be assigned a fabricated historical
`PASSED` gate result, freeze revision, evaluation, decision, or override as
part of this migration, regardless of its current `status`. "Existing" and
"historical" are not evidence of gate compliance under a policy that did
not exist when the package was created.

### 4.2 Migration steps (data migration, not schema-only)

1. Seed the canonical default `GatePolicy` + one initial `PUBLISHED`
   `GatePolicyVersion` (§3.3), if not already present (idempotent — safe to
   run on an empty database).
2. For every existing `ProcurementPackage` row, create exactly one
   `PackagePolicyAssignment` pinned to the canonical default policy's
   initial published version, `pinned_by = NULL` (system-assigned, not
   attributable to a human decision — this is explicit, never disguised as
   a real approver), with a `pinned_at` equal to the migration's run
   timestamp, not backdated to the package's original creation date.
3. For every existing `ProcurementPackage` row, initialize each of
   `A1`...`A6` in the current-state projection (§9) as `NOT_STARTED`. No
   `GateAttempt`, `GateEvaluation`, or `GateDecision` row is created by this
   migration — `NOT_STARTED` is the *absence* of any attempt, not a stored
   "passed" record dressed up as unstarted.
4. `ProcurementPackage.Status`, `is_frozen`, `frozen_at`, `frozen_by`,
   `frozen_snapshot`, and `is_on_hold` are **read-only inputs** to this
   migration and are not modified by it in any way.
5. The existing `Handoff`/`HandoffDecision` history for any target related
   to a package (e.g. a shipment) is untouched — this migration never
   creates, edits, or reads `apps.workflow` rows.
6. Existing `EvidenceBundle`/`EvidenceItem`/`AuditEvent` rows are preserved
   exactly as they are; none are re-typed, re-classified, or re-scoped to a
   gate retroactively.

### 4.3 Per-status treatment

| Existing `ProcurementPackage.Status` | Gate-migration treatment |
|---|---|
| `draft` | Pinned per §4.2; all gates `NOT_STARTED`; package proceeds through `A1` normally going forward. |
| `active` | Pinned per §4.2; all gates `NOT_STARTED`. **No inference** that an "active" package has already passed any gate — active is a commercial-lifecycle state, not a gate-completion signal. |
| `frozen` | Pinned per §4.2; all gates `NOT_STARTED`. The package's existing `is_frozen=True`/`frozen_snapshot` is **not** treated as an `A2` pass — see §4.4 for the explicit exemption path if the business decides such a package should not re-enter the new gate flow. |
| `closed` | Pinned per §4.2; all gates `NOT_STARTED`, then immediately eligible for the administrative exemption in §4.4. |
| Archived (if a future archival flag is added to `ProcurementPackage` before Milestone 1 ships) | Same treatment as `closed`; exemption eligibility carries over. |
| Test/demo packages (any package the organization has tagged as non-production, if such tagging exists) | Same treatment as `draft`/`active` per their actual status — Milestone 1 introduces no separate "demo" gate behavior; test data is exercised through the same, real gate flow so that tests are meaningful. |

### 4.4 Administrative exemption (not a historical pass)

A `closed` or otherwise clearly-historical package **may** be marked
`gate_progression_exempt = True` (a field on `PackagePolicyAssignment`, not
on any gate-decision model) by an authorized admin, with a required written
reason. This means: "this package is not expected to progress through
A1–A6 and its `NOT_STARTED` gates are not a defect to be chased." It is
displayed and audited distinctly from `PASSED` and must never be rendered,
exported, or reported as "gates passed," "gate history," or any language
implying historical compliance. Setting it is itself an audited action
(`GATE_PROGRESSION_EXEMPTION_GRANTED`, §10).

### 4.5 Empty-database and populated-database expectations

- **Empty database:** the migration creates the canonical `GatePolicy` +
  initial `GatePolicyVersion` and nothing else (no packages exist to pin).
  Running `migrate` on a fresh install must succeed and leave the system
  ready for a first real package to enter `A1` through the normal service
  path, pinning itself at that time (§3.4) — the data migration does not
  pre-pin a package that doesn't exist yet.
- **Populated database:** every existing package receives exactly one
  `PackagePolicyAssignment` per §4.2, and the migration is idempotent — running
  it twice (e.g. after a rollback/retry) must not create duplicate
  assignments, enforced by the partial unique constraint in §3.4 combined
  with a `get_or_create`-style guard in the migration's data function.
  Required tests: one against an empty test database, one against a
  populated fixture covering all four statuses in §4.3 (§18).

---

## 5. Evidence reuse

Resolves the evidence-integration portion of the review; reuses
`audit.EvidenceBundle`/`audit.EvidenceItem`/`procurement.VerificationAssertion`
exactly as they exist today — no new evidence storage model is introduced.

### 5.1 Attachment pattern and cardinality (binding, resolves REVAL-001)

**`GateAttempt` carries no `evidence_bundle` foreign key field.** The
authoritative relationship is exclusively generic-target, exactly the same
pattern already used by `apps.workflow.HandoffEvidence`→`Handoff` and the
foundation's other `EvidenceBundle` consumers: a `GateAttempt` (§9) is the
`content_type`/`object_id` target of **one or more** `EvidenceBundle` rows.
No parallel evidence-linking table is created, and no direct FK from
`GateAttempt` to any specific `EvidenceBundle` exists anywhere in this
domain. Every `EvidenceBundle` remains authorized/scoped to a package
purely by resolving its generic target through to the owning
`GateAttempt.package` — never through a denormalized shortcut.

**Exactly when one bundle versus multiple bundles exist for a gate
(binding, resolves REVAL-002):** the number of `EvidenceBundle` rows a
`GateAttempt` receives is deterministic and derived entirely from the
pinned policy version's `gate_schema[gate_code]` — never a runtime or UI
choice. See §5.2 for the exact grouping rule.

### 5.2 Evidence requirement codes and deterministic bundle grouping

**The existing `audit.EvidenceBundle`/`EvidenceItem` foundation is not
modified in any way by this Charter.** `EvidenceBundle.minimum_count`,
`.required_verifier_capability`, and `.minimum_review_state` remain exactly
what they are today: single, bundle-wide values applying to every code in
that one bundle's `required_evidence_types` list. This Charter achieves
per-requirement granularity purely by choosing bundle *boundaries*, never
by adding fields to the reused model.

Each `GatePolicyVersion.gate_schema[gate_code]` lists evidence requirements
as **stable, language-neutral codes** (e.g. `production_start_photo`,
`qc_inspection_report`, `packing_list_verified`, `bill_of_lading_draft`),
never free-text or locale-specific labels. Each requirement code declares,
in the policy schema: `evidence_type` (matches `EvidenceItem.evidence_type`
free-text convention), `minimum_count`, `required_verifier_capability`
(defaults to `VERIFY_EVIDENCE`, matching the existing `EvidenceBundle`
default), `minimum_review_state` (matches `EvidenceItem.ReviewStatus`), a
`classification_default` (§6), and a `cross_package_reuse_allowed` boolean
(default `False`). These per-code values are **policy-schema
configuration**, not `EvidenceBundle` fields — they exist only in
`GatePolicyVersion.gate_schema`'s JSON, never as columns on any evidence
model.

**Deterministic bundle-creation rule (binding):** when a `GateAttempt` is
opened, the gate's requirement codes are partitioned into groups by the
exact tuple `(required_verifier_capability, minimum_review_state,
minimum_count)`. Requirement codes sharing an identical tuple are placed
into the same `EvidenceBundle`, whose own `required_evidence_types` becomes
the list of `evidence_type` values for every code in that group, and whose
own `minimum_count`/`required_verifier_capability`/`minimum_review_state`
are set to that shared tuple's values (this is exactly what those three
bundle-wide fields already mean — one shared value governing every type
listed in `required_evidence_types`). Requirement codes with a *different*
tuple receive a *separate* `EvidenceBundle`. A gate whose requirement codes
all share one tuple therefore gets exactly one bundle; a gate needing `N`
distinct tuples gets `N` bundles — all sharing the same `GateAttempt` as
their generic target. This grouping is fully deterministic and reproducible
from the pinned policy version alone (same input, same groups, every time —
required for §18's evaluation-reproducibility test).

Within a group's one resulting bundle, `required_evidence_types` lists
every `evidence_type` in that group, and the single, shared
`minimum_count` is satisfied per §5.3's existing minimum-count rule
(counted per `evidence_type` code, not merely per bundle row-count, exactly
as `EvidenceBundle.required_evidence_types` already implies for any of its
existing non-gate consumers).

### 5.3 Lifecycle rules (binding)

- **Existence/upload:** an `EvidenceItem` is created against a gate's
  `EvidenceBundle` the same way any other bundle receives items today —
  through `apps.audit.services`, unmodified.
- **Verification/rejection:** unchanged `EvidenceItem.ReviewStatus`
  transitions (`pending → reviewed/verified/rejected`), performed by a user
  holding the requirement's `required_verifier_capability` *for that
  package's scope*.
- **Uploader/verifier separation (binding, no exception):** the user who
  verifies an `EvidenceItem` must not be the same user recorded as
  `uploaded_by` on that item. This mirrors the existing rule already
  enforced for `InternalCommercialSheet`/`ClientQuote` preparer/approver
  separation and must be enforced identically (service-layer check, not
  only a UI convention).
- **Expiry:** a policy requirement may declare a `max_evidence_age_days`;
  evidence older than that at evaluation time (§9) does not count toward
  satisfying the requirement, even if never explicitly revoked. This is
  computed at evaluation time, not by a background job that mutates the
  `EvidenceItem`.
- **Revocation/supersession:** unchanged from the existing `EvidenceBundle`/
  `EvidenceItem` model — no new revocation field is added; a superseding
  item is a new `EvidenceItem` row, the prior one is left as historical
  record (already the existing convention, e.g. `DerivedArtifact.supersedes`).
- **Minimum-count rules:** a gate requirement is satisfied only when at
  least `minimum_count` `EvidenceItem`s meeting `minimum_review_state` and
  the age rule above exist for the requirement code, within the correct
  package's `EvidenceBundle`.
- **Cross-package substitution denial (binding, no exception unless
  `cross_package_reuse_allowed=True` on that specific requirement code):**
  the evaluation function (§9.3) must verify every candidate
  `EvidenceItem`'s `EvidenceBundle.object_id`/`content_type` resolves to
  *this* `GateAttempt`, which resolves to *this* package. A caller-supplied
  evidence ID belonging to a different package's bundle must be rejected
  and logged as a denied attempt (§10, §13) even if the requirement code
  matches — package scope is derived from the persisted bundle/attempt
  chain, never trusted from request input.
- **Evidence reuse between attempts:** when a gate is re-attempted after an
  invalidation (§8) or a rejection, prior evidence *may* count toward the
  new attempt only if the specific requirement code has
  `cross_package_reuse_allowed` — no, that flag is for cross-*package*
  reuse; a separate, explicit `reuse_across_attempts_allowed` boolean per
  requirement code governs same-package, cross-attempt reuse, defaulting to
  `False` (each new attempt requires fresh evidence unless the policy
  explicitly says otherwise). This prevents silently recycling stale
  evidence into a new attempt by default.

### 5.4 Verification Assertions

Where a client-safe assertion is required (e.g. "production verified at an
authorized site" shown to a buyer without exposing the factory), a gate's
`GateDecision` (§9) may reference an existing or newly-created
`procurement.VerificationAssertion` exactly as that model already works —
`source_evidence_bundle` points at the gate's `EvidenceBundle`,
`client_visible_wording` is the only field ever projected to a client-scoped
viewer. No new assertion model is introduced.

---

## 6. Evidence classification

Resolves CHTR-012.

### 6.1 Deterministic classification rule (binding)

When a gate's `EvidenceBundle` is created (at `GateAttempt` creation, §9),
its `classification` is set by this fixed precedence, evaluated once at
creation time (not re-derived on every read):

1. If the specific evidence requirement code (§5.2) declares an explicit
   `classification_default`, use it.
2. Else, derive from the package's `visibility_mode`
   (`governance.VisibilityMode`):
   - `CONTROLLED_CONFIDENTIALITY` → `Classification.SOURCE_PRIVATE` for
     production/QC evidence (A3/A4), `Classification.TRADING_COMPANY_CONFIDENTIAL`
     for packing/handoff evidence (A5/A6), matching this codebase's existing
     defaults for analogous commercial records (`FactoryRFQ` vs.
     `InternalCommercialSheet`).
   - `CONTROLLED_TRANSPARENCY` → `Classification.OPERATIONAL_SHARED` for
     A3–A6 evidence, consistent with the existing `EvidenceBundle` model
     default.
3. Never `Classification.CLIENT_SHARED` by this rule alone — client-facing
   visibility of *any* gate evidence only ever happens through an explicit
   `VerificationAssertion` (§5.4) or an active `DisclosureGrant`, never by
   directly lowering an `EvidenceBundle`'s own classification.

### 6.2 Participant projection and information-absence

- A gate detail view/serializer returns, per requirement code: whether it
  is satisfied (`True`/`False`), the count of qualifying items, and — only
  to a viewer authorized at `classification` for this package/scope —
  which specific `EvidenceItem`s satisfy it. An unauthorized viewer sees
  the requirement code and satisfaction boolean only, never item
  identities, filenames, uploader identity, or `device_metadata`. This
  mirrors the existing "safe projection never uses `summary`/`metadata`"
  discipline already enforced for privileged audit retrieval
  (CTCF-AUDIT-RETRIEVAL-022).
- A viewer with no authorization at all for the package sees neither the
  requirement's satisfaction state nor its existence — the entire gate
  detail endpoint 404s/denies before any partial projection is computed
  (authorization before retrieval, §13).
- **No gate may expose raw evidence merely because a `GateDecision` or
  `VerificationAssertion` references it.** Referencing is an internal
  foreign key, not a disclosure; disclosure requires the same explicit
  `DisclosureGrant`/`VerificationAssertion` mechanism the foundation already
  requires for every other confidential source record.
- Original evidence is never overwritten by a translated, summarized, or
  reduced projection — any such derived form is a `governance.DerivedArtifact`
  row (already the existing mechanism), never a mutation of the
  `EvidenceItem`/`Document` original.

---

## 7. A2 technical freeze

Resolves CHTR-005.

### 7.1 `PackageFreezeRevision` (new model)

- Standard `BaseModel` fields.
- `package` — FK to `ProcurementPackage`.
- `revision_number` — `PositiveIntegerField`, unique per `package`,
  monotonically increasing starting at 1, never reused.
- `policy_version` — FK to the `GatePolicyVersion` pinned to this package
  at the time of this freeze (resolves REVAL-009: this field is kept, and
  its rationale is stated truthfully rather than as a hedge against a
  scenario the Charter forbids). It records exactly which policy version
  was applicable when this specific revision was created, so a historical
  read of one `PackageFreezeRevision` never has to interpret its meaning
  through a separate, mutable relationship graph (`PackagePolicyAssignment`)
  — the freeze row is self-contained on its own terms. **In Milestone 1,
  this value must always equal `PackagePolicyAssignment.policy_version`
  for the same package** (§3.4 makes package policy pinning permanent, so
  the two can never diverge under this Charter); the field exists primarily
  for historical self-containment and forward compatibility, so that a
  future, separately-approved milestone that ever permits policy
  reassignment mid-package does not have to retrofit this model. A required
  test (§18 test 18a, **added — resolves REVAL-009-TRACE**) asserts this
  invariant holds for every `PackageFreezeRevision` created under this
  Charter's rules.
- `frozen_fields` — `JSONField`: the exact set of critical
  technical/commercial fields and their values at freeze time (role party
  ids, incoterm, currency, payment terms, specification/drawing revision,
  evidence policy reference) — same conceptual content as today's
  `ProcurementPackage.frozen_snapshot`, but persisted as an immutable,
  numbered historical row instead of a single mutable field.
- `source_change_requests` — `ManyToManyField` to `governance.ChangeRequest`,
  populated only for revision 2+ (a refreeze), recording exactly which
  approved Change Request(s) triggered this revision. Empty for revision 1
  (the initial freeze at A2 entry).
- `evidence_references` — `JSONField` list of safe `EvidenceItem`/document
  identifiers relevant to this freeze (e.g. the frozen drawing revision's
  document id) — references only, never embedded confidential content.
- `actor` — FK to user who performed the freeze/refreeze.
- `status` — `CURRENT` / `SUPERSEDED`. Exactly one `CURRENT` row per
  package at any time; a new revision's creation atomically flips the
  prior `CURRENT` row to `SUPERSEDED` in the same transaction (`select_for_update`
  on the package row, §14).
- `predecessor` — self FK, nullable, set to the prior `CURRENT` revision
  being superseded (null only for revision 1).

### 7.2 Binding rules

- `PackageFreezeRevision` rows are **never** edited or deleted after
  creation. "Refreeze" always means: create a new row with
  `revision_number = previous + 1`, `predecessor` = the previous `CURRENT`
  row, and flip the previous row's `status` to `SUPERSEDED` — never an
  in-place update of `frozen_fields`.
- `ProcurementPackage.frozen_snapshot` **may remain** as a denormalized,
  current-state convenience field (e.g. for a dashboard that wants the
  latest frozen values without a join) — it is written at the same instant
  as the `CURRENT` `PackageFreezeRevision`, from the same data, in the same
  transaction. After Milestone 1, `frozen_snapshot` is documented as a
  cache of the latest `PackageFreezeRevision.frozen_fields` and is
  **not** the historical record; `PackageFreezeRevision` rows are.
  `frozen_snapshot` may be safely rebuilt at any time from the `CURRENT`
  revision and must never diverge from it outside of that rebuild.
- `ProcurementPackage.is_frozen`/`frozen_at`/`frozen_by` continue to mean
  "is a `CURRENT` freeze revision in effect" and are set/cleared in the same
  transaction as revision creation/hold-clearing; they are not independently
  writable by any other path.

---

## 8. Post-A2 critical changes

Resolves the change-control portion of CHTR-005/CHTR-006.

### 8.1 Binding sequence for an approved critical change

An approved critical post-A2 `governance.ChangeRequest` (status transition
`PENDING → APPROVED`, unchanged mechanism) triggers, atomically, inside one
`transaction.atomic()` block with `select_for_update()` on the
`ProcurementPackage` row:

1. `ProcurementPackage.is_on_hold = True` (already-existing field, now
   written exclusively by this path for critical-change holds — it may
   still be independently set by a `RiskFlag`-driven hold, see §8.3).
2. The package's current `A2` gate result (its latest non-invalidated
   `GateDecision` for `A2`, §9) is marked `INVALIDATED` via a new
   `GateInvalidation` row (§9.4) referencing this `ChangeRequest` as the
   trigger.
3. Every currently-`PASSED` `A3`–`A6` result for this package is likewise
   marked `INVALIDATED` via its own `GateInvalidation` row, same trigger
   reference, same transaction. Rows are never deleted — each prior
   `GateAttempt`/`GateEvaluation`/`GateDecision`/`PackageFreezeRevision` is
   fully preserved; only the *current-state projection* (§9.6) reflects
   `INVALIDATED` going forward.
4. **Active-override revocation (binding, resolves REVAL-004):** every
   currently-active `ProcurementGateOverride` (§11) on this package's `A3`–
   `A6` attempts — active meaning `decision = APPROVED`, `revoked_at IS
   NULL`, and not yet past `expires_at` — is revoked in this same
   transaction, regardless of whether the gate it covers is currently
   `PASSED` (an `OVERRIDDEN` gate is never `PASSED` via a `GateDecision`
   per §9.5, so it is not already covered by step 3, and would otherwise
   silently survive this cascade). Revocation here is **system-attributed**:
   `revoked_by = NULL` is the explicitly documented convention for a
   revocation triggered by this automatic cascade rather than by a human
   actor clicking "revoke" (the same convention already used for
   system-assigned `PackagePolicyAssignment.pinned_by = NULL` in §4.2's
   existing-package migration). The override row is never deleted;
   `revoked_at` is set to the cascade's transaction time, and `reason` is
   set to a fixed, safe, non-confidential string referencing only the
   triggering `ChangeRequest`'s id (e.g. `"Revoked by critical post-A2
   change cascade, ChangeRequest <id>"`) — never the `ChangeRequest`'s own
   `reason`/`proposed_new_value` free text, which may itself be
   confidential and must never enter this or any other audit projection
   unvetted. A `GATE_OVERRIDE_REVOKED` event is recorded per override
   revoked (§10). Because the underlying `GateAttempt` an override applied
   to is already closed (§9.2, resolves REVAL-008), revoking the override
   does not reopen that attempt — it only removes the override's masking
   effect on that attempt's derived current state, which then correctly
   falls back to whatever `compute_gate_state` derives from the attempt's
   last real `GateEvaluation` (typically `BLOCKED` once combined with this
   same transaction's hold — see step 1).
5. No new `PackageFreezeRevision` is created by steps 1–4 alone — that
   happens only when the refreeze is actually performed (step 6), which is
   a separate, later, explicit action, not automatic.
6. A new `PackageFreezeRevision` (refreeze) is created when an authorized
   actor performs it, per §7, referencing this `ChangeRequest` in
   `source_change_requests`.
7. The package remains `is_on_hold = True` until: the refreeze in step 6
   has occurred, **and** every other currently-open hold cause for this
   package (additional pending critical Change Requests, unresolved
   `RiskFlag`s configured to hold, §8.3) is also resolved. Hold clearing is
   therefore a recomputation over *all* open causes, not a single
   Change-Request-scoped flag flip (§8.4).
8. `A3`–`A6` must be re-attempted from scratch after refreeze — their
   invalidated results do not automatically become valid again merely
   because the hold cleared, and neither does a revoked override (step 4)
   revive itself; a fresh `GateAttempt` is required for each gate (§9.2),
   which may reuse still-valid, still-fresh evidence only where §5.3's
   `reuse_across_attempts_allowed` permits it, and may request a fresh
   override under the current (post-refreeze) terms if still needed —
   never by reviving the revoked one.

**Why step 4 needs no separate `A2` case (resolves REVAL-004-RESIDUAL):**
version 2 of this cascade scoped active-override revocation to "A3–A6"
only, leaving an open question — since §12.1 (version 2) permitted `A2`
to be made `overridable` via explicit policy opt-in — of what happens to
an `A2` gate that is currently `OVERRIDDEN` rather than decided, since
step 2 only invalidates an existing `GateDecision`. §12.1 now makes `A2`
**unconditionally, permanently non-overridable**, enforced by a
publication-time rejection (§3.2) rather than a policy default that could
be opted out of. `A2` can therefore never enter the `OVERRIDDEN` state at
all under this Charter; step 2's `GateDecision`-based invalidation is
always sufficient on its own, and step 4's scope correctly remains "A3–A6"
because no `A2` override can ever exist to revoke. This closes the gap by
construction rather than by adding a special case to the cascade.

### 8.2 Change Request entry point, frozen-field vocabulary, and
non-critical changes

Resolves NF-1, REVAL-011-ENFORCEMENT, and the non-critical-change portion
of the original charter review.

#### 8.2.1 Gate-aware Change Request entry point (binding, resolves NF-1)

**Version 2 problem, stated plainly:** version 2 claimed non-critical
changes "never touch hold state" while relying on the existing, unchanged
`apps.governance.services.request_change` — but that function
unconditionally sets `ProcurementPackage.is_on_hold = True` on **every**
`ChangeRequest` it creates, critical or not (verified at
`apps/governance/services.py:677-688` as of commit
`3b62228b4a6efb4079e7f8c010e107fcf9de639a`). Milestone 1 does not modify
that function (§0 forbids redesigning the accepted `apps.governance`
foundation), so the fix is an additive orchestration layer in front of it,
never a change to it.

**`apps.procurement_gates.services.request_gate_aware_change`** is the
**sole** Change Request creation entry point for Milestone 1. Every
Milestone 1 HTTP view, form, API endpoint, admin action, management
command, and internal procurement-gate service that creates a
`ChangeRequest` against a gate-governed package must call this function;
none may call `apps.governance.services.request_change` directly. The
existing `request_change` function remains fully available, unmodified,
to legacy (pre-Milestone-1, non-gate-aware) foundation callers — this is
additive orchestration, not a redesign of the accepted foundation.

The function performs, in order:

1. Enter `transaction.atomic()`.
2. Lock the `ProcurementPackage` row with `select_for_update()` — this is
   the same serialization boundary already used by every other
   Charter-defined mutation (§14.1).
3. **Authorize the actor before retrieving any protected value** —
   resolved through `RoleAssignment`/`CapabilityGrant` exactly as
   `apps.governance.services.has_capability` already requires (§13); a
   denial is recorded via `log_denied_attempt`/`PRIVILEGED_ACCESS_DENIED`
   (§10) and the function returns before touching `field_name`,
   `frozen_current_value`, or `proposed_new_value`.
4. Validate `field_name` against `apps.procurement_gates.constants.FROZEN_FIELD_CODES`
   (§8.2.2); an unrecognized code is rejected outright, before a
   `ChangeRequest` row is created.
5. Determine critical/non-critical classification for the validated
   `field_name` from the package's pinned `GatePolicyVersion.gate_schema`
   (§8.2.2) — **before** creating the request, never inferred afterward.
6. Invoke the existing, unmodified
   `apps.governance.services.request_change` to create the `ChangeRequest`
   row itself (reusing its `frozen_current_value` derivation and its
   `AuditEvent.Action.CHANGE_REQUEST` logging exactly as today).
7. **Recompute the package's `is_on_hold` inside the same transaction**,
   from the full set of currently-open `PackageHoldCause` rows (§8.4),
   never from a direct, unconditional flag write:
   - For a **non-critical** change: `request_change`'s own unconditional
     `is_on_hold = True` write (step 6) is **not** the transaction's final
     externally-visible hold state. No open `PackageHoldCause` is opened
     for a non-critical `ChangeRequest`. The recomputation in this step
     immediately re-derives `is_on_hold` from whatever other hold causes
     already exist for the package; if none exist, the package is **not**
     held by this non-critical request, correctly realizing §8.2.2's
     "never touches hold state" rule. There is no direct
     `package.is_on_hold = False` shortcut anywhere in this step — the
     result always comes from the same central `recompute_hold_state`
     function §8.4 already defines, applied to the same
     `PackageHoldCause` set every other hold-affecting operation uses.
   - For a **critical** change: a new `PackageHoldCause`
     (`cause_type = CRITICAL_CHANGE_REQUEST`, `reference` = this
     `ChangeRequest`) is opened in this same step, so the recomputation
     correctly leaves (or places) the package on hold; §8.1's cascade
     fires on the request's later approval, unchanged.

Required tests (§18): a static/architectural test proving no Milestone 1
view, form, API, admin action, or service imports or calls
`apps.governance.services.request_change` directly; service-level tests
proving the critical and non-critical outcomes above; a test proving an
unrelated, already-open hold cause (e.g. an unresolved `HIGH_RISK`
`RiskFlag`) survives a non-critical change's hold recomputation unchanged;
and authorization/denial-audit tests for step 3.

#### 8.2.2 Frozen-field vocabulary (binding, resolves REVAL-011 and
REVAL-011-ENFORCEMENT)

**Shared frozen-field vocabulary (binding, resolves REVAL-011).** Both
`governance.ChangeRequest.field_name` and `PackageFreezeRevision.frozen_fields`
keys (§7.1) must be drawn from one single, stable, language-neutral,
canonically enumerated registry defined by this Charter — never
independently free-text on either side. The registry (maintained as a
Python-level constant, e.g. `apps.procurement_gates.constants.FROZEN_FIELD_CODES`,
not a database table, since it changes only with a Charter amendment, not
per-tenant) lists, at minimum: `seller_of_record`, `exporter_of_record`,
`china_procurement_operator`, `production_factory`, `production_site`,
`visibility_mode`, `incoterm`, `currency`, `payment_terms`,
`approved_specification_revision`, `evidence_policy_reference` — this is
the exhaustive initial list; Milestone 1 does not permit ad hoc extension
without a Charter amendment. Aliases (e.g. `trade_terms` for `incoterm`)
are **not** independently accepted — there is exactly one canonical code
per concept, and any UI label or translation maps onto that one stored
code, never the reverse. `ChangeRequest.field_name` itself remains the
existing free-text `CharField` on the model (unchanged per this cycle's
authorization boundary — no application model is modified).

**Enforcement location and ownership (binding, resolves
REVAL-011-ENFORCEMENT):** the registry lives in, and is owned by,
`apps.procurement_gates.constants.FROZEN_FIELD_CODES`. The **only**
service-layer code that validates `field_name` against it is
`apps.procurement_gates.services.request_gate_aware_change` (§8.2.1),
step 4, executed before any `ChangeRequest` row is created — this is the
one, named, non-ambiguous enforcement point this Charter previously left
unnamed. **Dependency direction (binding, no exception):**
`apps.procurement_gates` may import from `apps.governance`;
`apps.governance` must never import from, or otherwise depend on,
`apps.procurement_gates` — the registry and its validation live entirely
in the new, downstream app, so no circular dependency between
`governance`, `procurement`, and `procurement_gates` is created or
possible. Because §8.2.1 establishes `request_gate_aware_change` as the
sole Milestone 1 creation entry point, and the unmodified
`apps.governance.services.request_change` remains reachable only by
legacy, non-gate-aware callers that predate and are outside this
Charter's scope, the registry check is not bypassable from any Milestone
1 code path. Any value not in the registry's codes is rejected with a
clear validation error at that single point — this is where the
vocabulary constraint is actually enforced, not on the model field
itself.

A `ChangeRequest` whose validated `field_name` is not on the package's
frozen-field list (§7.1 `frozen_fields`, whose keys are themselves drawn
from the same registry) follows an explicit, policy-declared rule: the
`GatePolicyVersion.gate_schema` (or a package-level policy extension)
declares, per registry code, whether a change is `critical` (triggers
§8.1) or `non_critical` (created via `request_gate_aware_change`, §8.2.1,
which uses the unmodified `ChangeRequest` mechanism for the row itself but
never leaves the package held on this cause alone, and never touches
freeze revisions or gate invalidation). There is no third, undeclared
category — a registry code with no declared critical/non-critical
classification in the pinned policy version, and any `field_name` value
outside the registry entirely, is treated as `critical` by default
(fail-closed), never silently allowed to bypass §8.1. Original submitted
values (the `ChangeRequest`'s own `frozen_current_value`/`proposed_new_value`
free text) are never altered by this vocabulary rule — only the stable
`field_name` code is constrained; display labels and translations of that
code do not alter the stored code itself, consistent with §17's
localization boundary.

Required tests (§18): canonical codes succeed; aliases (e.g. `trade_terms`
for `incoterm`) are rejected; unknown codes encountered in historical or
pre-existing data are treated as critical, never silently passed through;
direct Milestone 1 calls to `apps.governance.services.request_change` do
not exist anywhere in the codebase (the same static/architectural test
required by §8.2.1); and translations of a registry code never alter the
stored code itself.

### 8.3 Risk Flags plus Change Requests

An unresolved `RiskFlag` with `level = HIGH_RISK` on a package independently
sets/maintains `is_on_hold = True` through the same hold-cause model as
§8.1 — implemented as a `PackageHoldCause` join concept (a package is on
hold if it has *any* open hold cause row; §8.4 defines the exact shape),
not as a single boolean flipped by whichever mechanism ran most recently.
Resolving the `RiskFlag` (existing `resolved_at`/`resolved_by` fields)
removes that specific cause; the package clears `is_on_hold` only when the
cause set is empty.

### 8.4 Hold-cause recomputation (binding)

- `PackageHoldCause` — a lightweight append/close-only record: `package`,
  `cause_type` (`CRITICAL_CHANGE_REQUEST` / `RISK_FLAG` / other future
  types), `reference` (generic FK to the `ChangeRequest`/`RiskFlag`),
  `opened_at`, `closed_at` (null while open).
- `ProcurementPackage.is_on_hold` is **derived**: `True` iff at least one
  `PackageHoldCause` for that package has `closed_at IS NULL`. It is
  written by a single service function
  (`apps.procurement_gates.services.recompute_hold_state`) called at the
  end of every operation that opens or closes a cause, inside the same
  `select_for_update()`-protected transaction as the cause change — never
  left to eventual consistency or a scheduled job.
- **Multiple concurrent Change Requests:** each approved critical
  `ChangeRequest` opens its own `PackageHoldCause`; the hold persists until
  *all* such causes for that package are closed (by their respective
  refreezes/resolutions), not just the most recent one.
- **Duplicate approvals:** approving an already-`APPROVED` `ChangeRequest`
  is rejected by the existing `ChangeRequest` service layer (unchanged);
  this Charter adds no new duplicate-approval path.
- **Stale decisions:** a `GateDecision` (§9) computed against a
  `PackagePolicyAssignment`/freeze revision that has since been superseded
  or invalidated is itself invalidated by the same §8.1 steps 2–4 logic —
  there is no separate "stale decision" state; staleness *is*
  invalidation.
- **Retry after network failure / duplicate submission:** every mutating
  gate/override/policy-publish/freeze operation in this Charter is
  idempotent per §14's idempotency-key rule — a retried request with the
  same key returns the original result rather than creating a second
  attempt/decision/cause row.

---

## 9. Gate record architecture

Resolves CHTR-007.

### 9.1 Concept definitions (binding, exact meanings)

- **Attempt** — one bounded lifecycle for one package attempting one gate
  code once. Opened explicitly; closed by exactly one terminal
  `GateDecision` (`PASSED`/`FAILED`) or by invalidation/expiry/override
  without ever reaching a decision.
- **Evaluation** — a computed, deterministic, evidence/policy-schema output
  for a given attempt at a point in time: which requirement codes are
  satisfied, which are missing, whether the predecessor gate is currently
  valid, whether the package is on hold. Purely computed from persisted
  data (evidence, policy schema, predecessor state) — never itself an
  authorization decision. An attempt may accumulate several evaluations
  over time as evidence changes (e.g. re-evaluate after a new upload); each
  evaluation is a new, immutable row, never an edit of a prior one.
- **Decision** — an authorized human action (or an explicitly
  policy-approved automatic rule, if a future policy version ever declares
  one — Milestone 1 defines only human decisions) that closes an attempt as
  `PASSED` or `FAILED`, referencing the specific `GateEvaluation` it acted
  on. A decision is never computed; it is recorded.
- **Invalidation** — an append-only marker that a specific prior
  `GateDecision` (and, transitively, the attempt/evaluation it closed) is
  no longer current, with a reason and a trigger reference (§8). The
  original decision row is untouched; only a new `GateInvalidation` row is
  added and the current-state projection (§9.6) is recomputed.
- **State (current-state projection)** — "what is gate `A(n)` right now for
  this package," derived from the latest non-invalidated `GateDecision` (if
  any) plus expiry/override overlays. This is the only concept in this
  section that is a *cache*: it must always be reproducible by replaying
  attempt/evaluation/decision/invalidation/override history for that
  package and gate, and a rebuild function
  (`apps.procurement_gates.services.rebuild_gate_state`) must exist and be
  tested (§18) to prove that reproducibility.

### 9.2 `GateAttempt`

- Standard `BaseModel` fields.
- `package`, `gate_code` (choices `A1`–`A6`), `attempt_number`
  (`PositiveIntegerField`, unique per `(package, gate_code)`, monotonically
  increasing starting at 1 — see the exact allocation algorithm below,
  resolves REVAL-007).
- `policy_version` — denormalized copy of the package's pinned version at
  the moment this attempt was opened (self-describing, same rationale as
  §7.1).
- `opened_at`, `opened_by`.
- **No `evidence_bundle` field of any kind (resolves REVAL-001).** Evidence
  attaches exclusively via the generic `content_type`/`object_id` pattern
  described in §5.1 — one or more `EvidenceBundle` rows target this
  `GateAttempt` directly; there is no FK on this model pointing back at
  any of them.
- `closed_at` — null while open; set when a terminal `GateDecision` is
  recorded, when an override is approved for this attempt (§11.2,
  resolves REVAL-008 — approving an override closes the attempt exactly as
  a `GateDecision` would), or when the attempt is superseded by a new
  attempt for the same gate without ever reaching a decision (e.g.
  abandoned after invalidation of a predecessor).
- Uniqueness: at most one *open* (`closed_at IS NULL`) attempt per
  `(package, gate_code)` at a time, enforced by a partial unique
  constraint — mirrors `Handoff`'s `unique_active_handoff_per_target_gate`.
  A database-level uniqueness constraint additionally covers
  `(package, gate_code, attempt_number)` unconditionally (not just among
  open rows), so no two attempts for the same gate can ever share a number
  regardless of open/closed state.

**Exact attempt-creation transaction (binding, resolves REVAL-007):**

1. Enter `transaction.atomic()`.
2. Retrieve the `ProcurementPackage` row with `select_for_update()` — this
   row's lock is the serialization boundary for the entire operation; no
   separate lock on any `GateAttempt` row is needed or taken, since none
   exists yet for a brand-new attempt.
3. Inside that same lock, validate: the caller's authority for this
   package (§13, including the `A1`-bootstrap exception, resolves
   REVAL-005), the package's policy pin (§3.4) and this gate's presence in
   its `gate_schema` (guaranteed by §3.2's completeness rule), the
   predecessor gate's current validity, the package's current hold state
   (§8.4), and the absence of another currently-open attempt for this
   `(package, gate_code)`.
4. Compute `attempt_number = 1 + (Max(attempt_number) for existing
   GateAttempt rows filtered on this exact (package, gate_code), or 0 if
   none exist)` — this computation happens *after* acquiring the package
   lock in step 2 and *inside* the same transaction, never as a separate,
   unlocked `count()` query.
5. Create the `GateAttempt` row inside this same locked transaction and
   commit.

An idempotency key (§14.1) supplied with the creation request is checked
before step 4: a retried call with a key matching an already-created
attempt returns that existing attempt rather than repeating steps 4–5,
making retries deterministic per §14.3.

### 9.3 `GateEvaluation`

- Standard `BaseModel` fields.
- `attempt` — FK to `GateAttempt`.
- `evaluated_at`, `evaluated_by` (nullable — a system-triggered
  re-evaluation has no human actor; a manually-requested one does).
- `requirement_results` — `JSONField`: per requirement code, satisfied
  boolean + qualifying item count + (if applicable) age/expiry check
  result.
- `predecessor_valid` — boolean, computed against the current-state
  projection (§9.6) of `gate_code - 1` at evaluation time.
- `package_on_hold_at_evaluation` — boolean, computed against §8.4 at
  evaluation time.
- `overall_ready` — boolean: all requirements satisfied AND
  `predecessor_valid` AND NOT `package_on_hold_at_evaluation`.
- Never edited after creation. A changed evidence state produces a new
  `GateEvaluation` row, never a mutation of an old one.

### 9.4 `GateDecision` and `GateInvalidation`

- **`GateDecision`**: `attempt` (FK, one decision closes its attempt),
  `evaluation` (FK — the specific evaluation this decision acted on),
  `decided_by`, `decided_at`, `outcome` (`PASSED`/`FAILED`), `comment`
  (required when `FAILED`). Requires the capability declared for that gate
  in `gate_schema` (§3.1). Immutable after creation.
- **`GateInvalidation`**: `decision` (FK — the `GateDecision` being
  invalidated), `invalidated_at`, `invalidated_by` (nullable for
  system-triggered invalidation, e.g. §8.1's cascade), `reason`,
  `trigger_content_type`/`trigger_object_id` (generic FK to the
  `ChangeRequest`, expired `ProcurementGateOverride`, or refreeze that
  caused it). Immutable after creation. A `GateDecision` may have at most
  one `GateInvalidation` (invalidating an already-invalidated decision is
  a no-op, not a second row).

### 9.5 Stable states

`NOT_STARTED`, `BLOCKED`, `READY`, `IN_REVIEW`, `PASSED`, `FAILED`,
`INVALIDATED`, `EXPIRED`, `OVERRIDDEN`, `SUPERSEDED`.

| State | Stored or derived | Meaning |
|---|---|---|
| `NOT_STARTED` | Derived (absence of any `GateAttempt`) | No attempt has ever been opened for this gate. |
| `BLOCKED` | Derived | An attempt is open but `predecessor_valid=False` or `package_on_hold_at_evaluation=True` per the latest evaluation. |
| `READY` | Derived | An attempt is open, latest evaluation has `overall_ready=True`, awaiting a `GateDecision`. |
| `IN_REVIEW` | Derived | An attempt is open, latest evaluation has `overall_ready=False` due to missing/unverified evidence only (not hold/predecessor) — i.e. actively being worked. |
| `PASSED` | Derived from latest non-invalidated `GateDecision.outcome == PASSED` | Gate is satisfied and current. |
| `FAILED` | Derived from latest `GateDecision.outcome == FAILED` with no subsequent attempt opened yet | Gate was decided negatively; a new attempt may be opened. |
| `INVALIDATED` | Derived (a `GateInvalidation` exists for the latest decision and no subsequent attempt has passed) | A previously current `PASSED`/`FAILED` result was invalidated per §8. |
| `EXPIRED` | Derived | A `PASSED` decision's underlying evidence/override has aged past a policy-declared validity window without re-evaluation (Milestone 1 defines this hook; whether any gate actually uses a validity window is a policy-schema choice, not a code default). |
| `OVERRIDDEN` | Derived (an active `ProcurementGateOverride`, §11, exists for this gate's most recently closed attempt) | Gate is being treated as passed via an authorized override, not a `GateDecision`. The attempt the override applies to is `closed_at`-closed (§9.2, resolves REVAL-008), exactly as if a `GateDecision` had closed it. |
| `SUPERSEDED` | Derived | An older attempt/decision exists but a later attempt for the same gate has since opened or decided. |

**Override expiry/revocation resolution (binding, resolves REVAL-008):**
once a `ProcurementGateOverride` backing an `OVERRIDDEN` state expires or is
revoked, `compute_gate_state` stops returning `OVERRIDDEN` and instead
derives the state from that same (still-closed) attempt's last real
`GateEvaluation` — typically `BLOCKED` or, if the underlying `GateDecision`
path was never exercised, whatever that last evaluation actually showed.
The closed attempt is **never** reopened by expiry or revocation — progress
past that point requires opening a brand-new `GateAttempt` for the same
gate code (§9.2's attempt-number allocation naturally assigns it the next
number), exactly mirroring §8.1 step 8's "re-attempt from scratch"
convention. This keeps exactly one deterministic current state at every
instant: an attempt is either open (state derived from its live evaluation
history) or closed (state fixed as `PASSED`/`FAILED`/`OVERRIDDEN`, until an
invalidation/expiry/revocation moves it to `INVALIDATED`/`EXPIRED`/its
pre-override derived state, at which point only a new attempt — never the
old one — can move the gate forward again.

**Downstream invalidation on predecessor-override lapse (binding, resolves
REVAL-008-RESIDUAL):** version 2 left undefined what happens to a
downstream gate `A(n+1)` (or further) that reached `PASSED`, `OVERRIDDEN`,
`READY`, or `IN_REVIEW` in reliance on `A(n)`'s `OVERRIDDEN` state
satisfying its predecessor check (only possible when
`gate_schema[A(n)].override_satisfies_successor_predecessor = True`, §12
item 5) — once `A(n)`'s backing `ProcurementGateOverride` subsequently
expires or is revoked, nothing previously recomputed `A(n+1)`'s already-
decided state. This Charter now defines a deterministic cascade, modeled
directly on §8.1's critical-change cascade:

1. The expiry-detection or revocation operation that causes `A(n)` to stop
   being `OVERRIDDEN` (§11.5's lazy expiry check, or an explicit
   revocation) executes inside `transaction.atomic()` with
   `select_for_update()` on the `ProcurementPackage` row — the same
   serialization boundary as every other Charter-defined mutation.
2. Inside that same lock, the function determines whether `A(n)`'s prior
   `OVERRIDDEN` state was ever relied on as satisfied predecessor validity
   for gate `A(n+1)` — true whenever
   `gate_schema[A(n)].override_satisfies_successor_predecessor` was `True`
   at the time `A(n+1)`'s current attempt was opened or last evaluated
   (recorded on that `GateEvaluation`'s `predecessor_valid` field, §9.3).
   If so, the cascade below applies to `A(n+1)`; if `A(n+1)` has itself
   been passed/overridden and relied upon by `A(n+2)`, the same check
   propagates forward, gate by gate, through `A6`.
3. For every downstream gate from `A(n+1)` through `A6` whose current
   result is affected by this chain, append a `GateInvalidation` row
   (§9.4) against its current `GateDecision` (if any), with
   `trigger_content_type`/`trigger_object_id` referencing the lapsed
   `ProcurementGateOverride` — never its own free-text `reason` field,
   consistent with §8.1 step 4's confidentiality rule for cascade
   triggers. A downstream gate currently `OVERRIDDEN` on its own,
   independent override is likewise invalidated by revoking that override
   through the same system-attributed convention as §8.1 step 4. A
   downstream gate currently `READY` or `IN_REVIEW` (open attempt, no
   terminal decision yet) does not need a `GateInvalidation` row — its
   next `GateEvaluation` will naturally recompute `predecessor_valid` as
   `False` and report `BLOCKED` (§9.5's `BLOCKED` row), because the
   predecessor state feeding that evaluation has already changed.
4. Every `GateAttempt`, `GateEvaluation`, `GateDecision`, `GateInvalidation`,
   and `ProcurementGateOverride` row touched by this cascade is preserved
   in full — none is edited or deleted; only the current-state projection
   (§9.6) for the affected gates changes.
5. The package is placed or kept `is_on_hold = True` via the same
   `PackageHoldCause`/`recompute_hold_state` mechanism as §8.4, with
   `cause_type` distinguishing this trigger from a critical-change hold,
   so the two hold sources compose correctly rather than colliding.
6. No downstream attempt is reopened by this cascade — exactly as expiry
   and revocation never reopen `A(n)`'s own attempt. Progress past any
   invalidated downstream gate requires opening a brand-new `GateAttempt`
   for that gate once `A(n)`'s predecessor state is valid again (a fresh
   decision, or a fresh override under current terms).
7. This cascade is recorded with a stable audit action,
   `GATE_DOWNSTREAM_INVALIDATED` (§10), one event per downstream gate
   invalidated, in addition to the existing `GATE_OVERRIDE_EXPIRED`/
   `GATE_OVERRIDE_REVOKED` event for `A(n)` itself.

This closes the gap by defining the cascade explicitly rather than leaving
downstream gates silently stale; it deliberately mirrors §8.1's
already-accepted shape (lock → invalidate forward → hold → preserve
history → require new attempts) rather than inventing a second
invalidation philosophy.

No state is stored as a single mutable enum column anywhere. `NOT_STARTED`
through `OVERRIDDEN` are all computed by
`apps.procurement_gates.services.compute_gate_state(package, gate_code)`
from the tables above; this function is the single place this logic lives
(mirroring the existing `apps.workflow.gates.evaluate_gate` convention of
one canonical evaluator, never inlined per-view).

### 9.6 Current-state projection cache

For read-performance, a `PackageGateState` row (`package`, `gate_code`,
`state`, `last_recomputed_at`) **may** be maintained as a cache, written by
`compute_gate_state` immediately after every mutation that could change it
(decision, invalidation, override grant/revoke/expiry, evaluation). It is
explicitly documented as non-authoritative: `rebuild_gate_state` (§9.1)
must be able to regenerate every `PackageGateState` row from history alone
and a test must prove the cache and the rebuilt value always match (§18).

### 9.7 AuditEvent boundary (binding)

`audit.AuditEvent` continues to record privileged actions and denials
(unchanged role). It is explicitly **not** the store of record for gate
business state — `GateAttempt`/`GateEvaluation`/`GateDecision`/
`GateInvalidation` are. Every mutation in this section additionally writes
one `AuditEvent` per §10, but deleting all `AuditEvent` rows must never
lose the ability to reconstruct gate state from the tables above.

---

## 10. Audit taxonomy

Resolves the auditability portion of CHTR-007. New `audit.AuditEvent.Action`
members (added to the existing enum, no renumbering/removal of current
members):

| New action code | Recorded when |
|---|---|
| `GATE_POLICY_CREATED` | A `GatePolicy` row is created. |
| `GATE_POLICY_VERSION_PUBLISHED` | A `GatePolicyVersion` transitions `DRAFT → PUBLISHED`. |
| `GATE_POLICY_PINNED` | A `PackagePolicyAssignment` is created (§3.4). |
| `GATE_ATTEMPT_OPENED` | A `GateAttempt` is created. |
| `GATE_EVALUATED` | A `GateEvaluation` is created. |
| `GATE_PASSED` | A `GateDecision` with `outcome=PASSED` is created. |
| `GATE_FAILED` | A `GateDecision` with `outcome=FAILED` is created. |
| `GATE_INVALIDATED` | A `GateInvalidation` is created. |
| `GATE_OVERRIDE_REQUESTED` | A `ProcurementGateOverride` is created in requested state (§11). |
| `GATE_OVERRIDE_APPROVED` | A `ProcurementGateOverride` is approved. |
| `GATE_OVERRIDE_REJECTED` | An override request is rejected. |
| `GATE_OVERRIDE_REVOKED` | An active override is revoked. |
| `GATE_OVERRIDE_EXPIRED` | An override's `expires_at` passes (recorded at the next read/evaluation that observes the expiry, not by a required background job — see §11.5). |
| `GATE_DOWNSTREAM_INVALIDATED` | **(added, resolves REVAL-008-RESIDUAL)** A downstream gate's current result is invalidated because a predecessor's `override_satisfies_successor_predecessor`-backed `OVERRIDDEN` state expired or was revoked (§9.5). One event per downstream gate invalidated. |
| `PACKAGE_FREEZE_CREATED` | The first `PackageFreezeRevision` (revision 1) for a package. |
| `PACKAGE_REFREEZE_CREATED` | Any subsequent `PackageFreezeRevision`. |
| `GATE_PROGRESSION_EXEMPTION_GRANTED` | §4.4's administrative exemption is set. |
| `PRIVILEGED_ACCESS_DENIED` | Reused (already exists) for every authorization failure in this domain — no new "denial" code is added; the existing member is reused with `metadata` identifying the gate-domain capability that was missing, consistent with `apps.governance.services.log_denied_attempt`. |

### 10.1 Required fields on every gate-domain `AuditEvent`

`metadata` for every action above must include, at minimum: `actor_id`
(via the existing `actor` FK), `organization_id`, `package_id`,
`policy_version_id` (where applicable), `gate_code` (where applicable),
`attempt_id` (where applicable), `prior_state`, `resulting_state`,
`reason` (required for override/invalidation/exemption/rejection/revocation
actions, optional otherwise), and `occurred_at` (existing `auto_now_add`
field). **Never** include raw evidence content, document bytes,
`device_metadata`, or any confidential field value in `metadata` — only
identifiers and state labels, matching the existing rule that safe
projections never carry `summary`/full free-text business content for
privileged-audit paths.

---

## 11. Override architecture

Resolves CHTR-002 and CHTR-006.

### 11.1 Binding decision

The existing `apps.workflow.GateOverride` model is **not** reused as a
database row, and its foreign keys (`gate_definition` required `PROTECT`,
`handoff` optional) are **not** relaxed, made nullable, or otherwise
modified by Milestone 1. This Charter explicitly does not reopen or
contradict ADR-020's reasoning; it applies the same reasoning ADR-020 used
one level further: exactly as `record_installation_progress`'s
over-installation waiver was judged too narrow a fit for `GateOverride`'s
Handoff-shaped FKs, so is procurement-package gate override. Rather than
ADR-020's chosen alternative (`AuditEvent.Action.WAIVER` alone, with no
dedicated exception row), procurement gates need a dedicated row because
they carry expiry, revocation, and separation-of-duties fields that a bare
`AuditEvent` cannot enforce or query — so a **new**, domain-native model is
defined, reusing the *lifecycle pattern* `GateOverride` established
(reason, before/after state, expiry, revocation), not its table.

### 11.2 `ProcurementGateOverride` (new model)

- Standard `BaseModel` fields.
- `package` — FK to `ProcurementPackage` (package scope).
- `policy_version` — FK to `GatePolicyVersion` (policy-version scope —
  must match the package's currently-pinned version).
- `gate_code` — the specific gate being overridden (gate-code scope).
- `attempt` — FK to the specific `GateAttempt` being overridden (attempt
  scope) — an override is never generic to "this package's A4 forever," it
  is scoped to one bounded attempt.
- `organization` — denormalized from the package's organization
  (organization scope, for query isolation consistent with `Handoff.organization`'s
  existing denormalization rationale).
- `requested_by`, `requested_at`, `reason` (required, written).
- `before_state` — JSON snapshot of the blocking `GateEvaluation` result at
  request time (mirrors `GateOverride.before_state`).
- `evidence_references` — JSON list of safe evidence identifiers cited in
  support of the request (minimum evidence, §11.3).
- `decided_by`, `decided_at`, `decision` (`APPROVED`/`REJECTED`), null
  while pending.
- `after_state` — JSON snapshot of the resulting gate state immediately
  after approval (mirrors `GateOverride.after_state`); null until decided.
- `expires_at` — required, non-null on approval (finite expiry is
  mandatory, not optional, unlike the workflow `GateOverride` where it is
  optional — Milestone 1 tightens this for the higher-stakes procurement
  domain).
- `revoked_at`, `revoked_by` — nullable.
- `is_currently_active(at=None)` — same method shape as
  `governance.DisclosureGrant`/`workflow.GateOverride`: `False` if revoked,
  `False` if `now > expires_at`, `True` otherwise (only meaningful when
  `decision == APPROVED`).

### 11.3 Binding controls

- **Separation of duties:** `requested_by != decided_by`, enforced in the
  service layer, no exception.
- **Minimum evidence:** the policy schema (§3.1) may declare a minimum
  evidence-reference count required on the override request itself (e.g.
  "an A4 override requires at least one cited inspection note even though
  the formal QC evidence is missing") — enforced before the request may
  even be submitted for decision.
- **Capability checks:** requesting requires a package-scoped
  `AUTHORIZE_EXCEPTION`-family capability (existing code, reused);
  approving requires a distinct capability check evaluated against the
  approver's own grants, never inherited from the requester's.
- **Finite expiry:** `expires_at` is mandatory on approval (§11.2).
- **Revocation:** any user holding the approval capability for that
  package may revoke an active override before its natural expiry, with a
  required reason, recorded via `GATE_OVERRIDE_REVOKED`. Revocation is
  system-attributed (`revoked_by = NULL`) only for the one explicitly
  documented automatic path — the post-A2 critical-change cascade (§8.1
  step 4, resolves REVAL-004) — and is otherwise always attributed to the
  human actor who performed it; no other code path may set
  `revoked_by = NULL`.
- **Attempt closure on approval (resolves REVAL-008):** approving an
  override sets `closed_at` on the underlying `GateAttempt` (§9.2), exactly
  as a `GateDecision` would. Neither expiry nor revocation reopens that
  attempt; §9.5's "Override expiry/revocation resolution" paragraph is the
  single authoritative statement of what happens next.
- **Audit history:** every state transition (`GATE_OVERRIDE_REQUESTED`
  through `_EXPIRED`) is recorded per §10. The override row itself, like
  `GateDecision`, is immutable after each transition — a decision or
  revocation never edits `requested_by`/`before_state`; it only adds the
  decision/revocation fields.
- **This is not a second workflow engine:** `ProcurementGateOverride` has
  no notion of stages, departments, or generic targets — it is scoped
  exclusively to `(package, policy_version, gate_code, attempt)` and exists
  solely to let `compute_gate_state` (§9.5) report `OVERRIDDEN` for one
  specific blocked attempt. It replaces zero existing functionality in
  `apps.workflow`.

### 11.4 Explicit non-effect on the existing Handoff `GateOverride`

Creating, approving, revoking, or expiring a `ProcurementGateOverride` has
no effect on, and shares no foreign key, table, or manager with,
`apps.workflow.GateOverride`, `GateDefinition`, or `Handoff`. A future
reader of `apps.workflow.models` after Milestone 1 ships will find that
file byte-for-byte unchanged by this Charter's implementation.

### 11.5 Expiry detection

Expiry is evaluated lazily, at every `compute_gate_state`/`GateEvaluation`
call (`is_currently_active` checks `now > expires_at`), exactly like
`DisclosureGrant`/`GateOverride` already do. Milestone 1 does not require a
scheduled background job to proactively flip a status column; the
`GATE_OVERRIDE_EXPIRED` audit event is written the first time any read
path observes the expiry, with a guard to write it at most once per
override (checked via existence of a prior `GATE_OVERRIDE_EXPIRED` event
for that override id, or an `expired_audit_recorded` boolean on the row —
implementer's choice, but exactly-once must be tested, §18).

---

## 12. Non-overridable controls

Resolves the remainder of CHTR-006.

**Binding, absolute (no policy flag may weaken these, ever):**

1. Authorization (capability + role assignment) cannot be overridden —
   `ProcurementGateOverride` changes *what a gate's state is*, never *who
   may act*. A user without the approval capability cannot approve an
   override regardless of any override's own state.
2. Package scope and organization scope cannot be overridden — an override
   scoped to package A never affects package B, full stop, enforced by the
   FK shape itself (§11.2), not by a runtime check that could have a bug.
3. Classification cannot be overridden — an override never changes an
   `EvidenceItem`/`EvidenceBundle`'s `classification` or grants disclosure;
   it only changes gate state.
4. Separation of duties (§11.3) cannot be overridden by any capability,
   including platform-administration capabilities, in Milestone 1 — there
   is no "super-approver" exception.
5. Predecessor-gate validity cannot be silently bypassed: overriding gate
   `A(n)` never marks `A(n-1)` as passed and never allows `A(n+1)` to be
   attempted while `A(n)` is merely `OVERRIDDEN` rather than `PASSED`,
   unless the policy schema explicitly lists `A(n)` as
   `override_satisfies_successor_predecessor = True` for that gate — the
   **default is `False`**: an overridden gate still counts as "not truly
   passed" for the purpose of unblocking the next gate, unless a policy
   explicitly says the business accepts that risk for that specific gate.
6. Confidentiality rules (§6) cannot be overridden.
7. Minimum audit requirements (§10) cannot be disabled — an override
   request that would skip writing its own audit trail is rejected outright
   by the service layer, not merely discouraged.
8. A required written reason cannot be blank — enforced as a non-blank
   `TextField` validated in the service layer, not just a model
   `blank=False` UI hint.
9. Finite expiry cannot be waived — `expires_at` is mandatory (§11.2), no
   "permanent override" exists.
10. **Reconciled per-gate/per-requirement eligibility (binding, resolves
    REVAL-006 — supersedes any earlier, separately-worded `non_overridable`
    flag description elsewhere in this Charter; §3.1's `gate_schema`
    definition is the single authoritative field list):** two distinct,
    explicitly composed controls, both declared per gate in `gate_schema`
    (§3.1), govern override eligibility:
    - `gate_schema[gate_code].overridable` (boolean) — if `False`, **no**
      `ProcurementGateOverride` may be requested for that gate at all; the
      request-creation service function rejects it before any row is
      written. This is the coarse, gate-level switch.
    - `gate_schema[gate_code].non_overridable_requirements` (list of
      requirement codes drawn from that same gate's own requirement list)
      — even when `overridable = True`, every requirement code named in
      this list must still be independently satisfied by real evidence
      before an override may be approved; an approved override never
      excuses a listed requirement. This is the fine-grained, per-
      requirement control.
    - **Composition rule:** both controls are always checked, never one in
      place of the other. `overridable = False` makes
      `non_overridable_requirements` moot (nothing may be overridden at
      all); `overridable = True` with a non-empty
      `non_overridable_requirements` list means *some* missing
      requirements may be excused by an approved override and *others*
      (the listed ones) may never be, within the same gate, on the same
      attempt.
    - **Fail-closed on missing/malformed configuration:** if either field
      is absent, null, or fails schema validation for a given gate entry,
      that gate is treated as `overridable = False` — never as
      permissively overridable by default. This is enforced by §3.2's
      publish-time schema validation rejecting such a policy version
      outright, so a malformed configuration can never reach a package in
      the first place.
    - **Capability never substitutes for eligibility:** holding the
      approval capability (§13) never changes what `overridable`/
      `non_overridable_requirements` allow — a capability grant authorizes
      *who* may approve an eligible override; it never makes an
      ineligible one eligible.
    - **Predecessor validity remains non-overridable by default**
      regardless of either flag above — see item 5 of this list, which
      governs independently via `override_satisfies_successor_predecessor`
      and is not superseded by this item.

### 12.1 Per-gate override eligibility default

`gate_schema[gate_code].overridable` defaults to `True` for `A3`–`A6`, and
to `False` for `A1` unless a policy version explicitly opts in — `A1`
(deal terms) carries irreversible downstream consequences if overridden
casually, so the safe default requires explicit policy opt-in before it
becomes overridable at all. This default applies only when a policy
version is first authored as a `DRAFT`; per item 10 above, a *published*
version missing this field entirely is rejected outright, never silently
defaulted at publish time.

**`A2` is unconditionally, permanently non-overridable (binding, resolves
REVAL-004-RESIDUAL — supersedes version 2's "`False` unless a policy
version explicitly opts in" treatment of `A2`):** `gate_schema["A2"].overridable`
must always be `False`. There is no policy opt-in, no organization-level
exception, and no platform-administration carve-out that can ever set it
to `True` — §3.2's publish-time validation rejects any `gate_schema` whose
`A2` entry has `overridable = True` outright, before the version can ever
reach `PUBLISHED`. Consequently: no `ProcurementGateOverride` may ever be
requested for `A2` (the request-creation service function rejects it
before any row is written, same fail-closed mechanism as any other
`overridable = False` gate, §12 item 10); `A2` can never enter the
`OVERRIDDEN` state (§9.5); and the post-A2 critical-change cascade (§8.1)
never needs to reconcile an overridden `A2` against its `GateDecision`-based
invalidation step, because that case cannot occur (§8.1's closing note).
**Why `A2` specifically, and not `A3`–`A6`:** `A2` is the technical-freeze
gate whose entire purpose is to make the package's frozen terms
authoritative for every downstream gate and for the critical-change
cascade itself (§2, §7, §8); allowing it to be bypassed by override would
let a package proceed through `A3`–`A6` against terms that were never
actually frozen, undermining the freeze/change-control mechanism this
Charter exists to define. `A1` and `A3`–`A6` carry no equivalent
structural dependency, so their override eligibility remains
policy-configurable.

---

## 13. Authorization

For every read/mutation path introduced by this Charter, the following is
mandatory and mirrors the existing foundation's authorization discipline
exactly (no new authorization philosophy is invented):

**New capability code (binding, resolves NF-3 and REVAL-005-RESIDUAL):**
`CREATE_PROCUREMENT_GATE_ATTEMPT` is added to the existing `CapabilityGrant`
capability-code registry (alongside `PUBLISH_GATE_POLICY`, following the
same precedent), scoped exactly like any other capability code — via
`CapabilityGrant.role_assignment` (package-scoped, once a `RoleAssignment`
exists) or `CapabilityGrant.organization` (organization-scoped, for the
`A1` bootstrap path only, §9.2 step 3). It is never implied by any role
default (`ROLE_DEFAULT_CAPABILITIES`) and is a distinct capability from
`CREATE_EVIDENCE`, `APPROVE_GATE`, and every `GateDecision` capability —
attempt *creation* and gate *decision*/*evidence-creation* are three
separate authorization questions, never conflated. For the canonical
policy shipped by the Milestone 1 data migration (§4.2),
`gate_schema[gate_code].attempt_creation_capability` (§3.1, §3.2) is
`CREATE_PROCUREMENT_GATE_ATTEMPT` for all six gates, `A1` through `A6`.

| Path | Capability required | Notes |
|---|---|---|
| View gate summary/detail (authorized projection) | package-scoped active role assignment, no elevated capability beyond it | Unauthorized viewer gets denial before any projection is computed. |
| Create `GateAttempt` for `A2`–`A6` | Package-scoped `CREATE_PROCUREMENT_GATE_ATTEMPT`, per `gate_schema[gate_code].attempt_creation_capability` (§3.1, resolves NF-3) | Distinct from the gate's `GateDecision` capability and from `CREATE_EVIDENCE` — an actor may be authorized to open an A3 attempt without yet holding QC-decision or evidence-verification authority, and vice versa. |
| Create a package's **first** `GateAttempt`, for `A1` only (binding exception, resolves REVAL-005 and REVAL-005-RESIDUAL) | **Organization-scoped** `CapabilityGrant` for `CREATE_PROCUREMENT_GATE_ATTEMPT` (via `CapabilityGrant.organization`, not `CapabilityGrant.role_assignment`) on the package's hosting organization | By construction, no package-scoped `RoleAssignment`/`CapabilityGrant` can exist before `A1` establishes the package's first roles — `A1`'s own content is establishing them. This is the **only** gate-attempt-creation path in this Charter authorized at organization scope rather than package scope; every other attempt-creation path (including every subsequent `A1` re-attempt after a prior one closed, and every `A2`–`A6` attempt) requires the same capability at package scope once at least one `RoleAssignment` exists. |
| Record `GateEvaluation` | System-triggered or same capability as viewing detail (evaluation itself grants no authority, only informs) | |
| Record `GateDecision` | The specific capability declared in `gate_schema[gate_code]` (§3.1), e.g. `APPROVE_GATE` (existing code, reused) | Never satisfied by same-organization membership alone; distinct from `CREATE_PROCUREMENT_GATE_ATTEMPT` above. |
| `PUBLISH_GATE_POLICY` (new capability code) | Platform- or organization-scoped policy administration capability | Never implied by any role default (§ governance `ROLE_DEFAULT_CAPABILITIES` — no role gets this by default). |
| Request `ProcurementGateOverride` | `AUTHORIZE_EXCEPTION` (existing code) scoped to the package | Rejected outright for `A2` regardless of capability held — `A2` is unconditionally non-overridable (§12.1). |
| Approve `ProcurementGateOverride` | A distinct capability from the requester's own grant (§11.3) | |
| Revoke `ProcurementGateOverride` | Same capability as approval | |
| Create/approve `PackageFreezeRevision` | `APPROVE_TECHNICAL_SPEC` (existing code) for the initial freeze; refreeze additionally requires the triggering `ChangeRequest`'s own approval capability | |
| Create `governance.ChangeRequest` against a gate-governed, frozen package | Resolved entirely inside `apps.procurement_gates.services.request_gate_aware_change` (§8.2.1) — the same capability check `apps.governance.services.request_change` already performs, evaluated before any protected value is retrieved | This is the sole Milestone 1 creation entry point (§8.2.1); it is not a new authorization rule, only a named, single enforcement location. |

**Binding rules restated for this domain specifically (already true
platform-wide, restated because the review flagged them as easy to get
wrong under time pressure):**

- Same-organization membership alone is insufficient for any capability
  above — every check resolves through `RoleAssignment`/`CapabilityGrant`
  exactly as `apps.governance.services.has_capability` already requires.
- Superuser status alone is insufficient unless an explicitly approved
  platform-administration rule says otherwise (existing
  `can_override_gates`-style carve-out pattern may be extended only by an
  equally explicit, separately documented decision — Milestone 1 does not
  itself grant superuser any gate-domain capability by default).
- Authority scoped to package A never authorizes any action on package B —
  every service function must resolve `package_id` from the persisted
  target (`GateAttempt.package`, `ProcurementGateOverride.package`, etc.),
  never from a caller-supplied, unchecked parameter (this is the same rule
  CHTR-006/§5.3 already requires for evidence; it is restated here as a
  general authorization rule because it applies to every path in this
  section too).
- Authorization is evaluated before retrieval/projection on every path —
  no gate-detail, evidence-status, or override-history endpoint queries
  and *then* filters; it denies before querying anything beyond what is
  needed to make the authorization decision itself.
- Every denial is durably audited via the existing `PRIVILEGED_ACCESS_DENIED`
  mechanism (§10), including the specific capability that was missing and
  the package/gate context — never silently a bare 403/404 with no audit
  trail.

---

## 14. Concurrency and idempotency

### 14.1 Mandatory patterns

Every mutating service function introduced by this Charter uses:

- `transaction.atomic()` wrapping the full read-modify-write sequence.
- `select_for_update()` re-fetch by primary key of the row(s) being
  mutated (`ProcurementPackage`, `GateAttempt`, `PackagePolicyAssignment`,
  `ProcurementGateOverride`, `PackageFreezeRevision`) — the same
  fetch-then-lock-by-pk pattern already used in
  `apps.governance.services` (`ChangeRequest`/`ProcurementPackage`/`RiskFlag`,
  lines 706–880 of that file) and `apps.workflow.services` (`Handoff`).
- Optimistic revision checks where a client submits a version/attempt
  number it last observed (e.g. "decide on evaluation X") — the service
  function re-verifies that evaluation is still the latest for its
  attempt inside the lock before acting, rejecting with a specific
  "stale evaluation, re-fetch and retry" error otherwise, rather than
  silently deciding against outdated evidence.
- Idempotency keys for externally-retried mutations — every
  Charter-defined mutation that a browser/API client might resubmit after a
  timeout (open attempt, record decision, request/decide override,
  publish policy, pin policy, create freeze revision) accepts an
  idempotency key (client-generated UUID or a natural key like
  `(attempt_id, decided_by, outcome)` where that's already unique enough);
  a retried call with the same key returns the original result rather than
  creating a duplicate row, verified inside the same locked transaction.

### 14.2 Duplicate-submission detection

For calls without an explicit idempotency key, the partial-unique
constraints already specified (one open `GateAttempt` per gate, one active
`PackagePolicyAssignment` per package, at most one pending
`ProcurementGateOverride` per attempt) serve as the backstop:
a duplicate submission is caught as an integrity error inside the locked
transaction and translated into "already exists, here is the existing
row," never a second, silently-divergent row.

### 14.3 Deterministic retry results

A retried mutation must always return the same logical outcome as its
first (successful) attempt when replayed with the same idempotency
key/natural key — this is a required test category (§18), not just a
design aspiration.

### 14.4 Named race scenarios and required behavior

| Scenario | Required behavior |
|---|---|
| Simultaneous `GateDecision`s on the same attempt | `select_for_update()` on the attempt/package serializes them; the second to acquire the lock sees the attempt already closed and is rejected with a clear "already decided" error, not a second `GateDecision` row. |
| Simultaneous evidence verification on the same requirement | Each `EvidenceItem` verification is independently locked by its own row; no cross-item lock is needed, but the subsequent `GateEvaluation` recompute is itself serialized per attempt. |
| Policy publication race (two publish calls on the same `DRAFT` version) | `select_for_update()` on the `GatePolicyVersion` row; second caller sees `status already PUBLISHED` and is rejected, not double-published. |
| Package pinning race (two A1-entry calls for the same package) | `select_for_update()` on `ProcurementPackage`; the partial unique constraint on `PackagePolicyAssignment` backstops it — second caller gets the first caller's assignment, not a duplicate. |
| Change Request approval during an in-flight evaluation | The evaluation in flight completes against the pre-approval state (evaluations are immutable, §9.3); the approval's own transaction (§8.1) invalidates whatever was current *after* the evaluation's transaction commits — ordering is whichever transaction commits first, but neither corrupts the other; a fresh evaluation afterward reflects the new hold state. |
| Override expiry during decision | `is_currently_active()` is re-checked inside the locked transaction at the moment `compute_gate_state`/decision-eligibility is evaluated — an override that expired one second before a decision attempt is treated as expired, not honored. |
| Refreeze during downstream evaluation | The downstream `A3`+ evaluation reads the package's `is_on_hold` state inside its own transaction; if a refreeze's hold-clearing transaction hasn't committed yet, the evaluation correctly reports `BLOCKED`, not a race-dependent flicker. |
| Duplicate override decisions | Same pattern as duplicate `GateDecision`s — locked, second caller rejected with "already decided." |
| Stale browser submissions | Covered by §14.1's optimistic revision check — a decision submitted against an evaluation that is no longer the latest is rejected, prompting the client to re-fetch. |
| Concurrent attempt-opening for the same `(package, gate_code)` (resolves REVAL-007) | The package-row `select_for_update()` in §9.2's exact creation transaction serializes both callers; the second acquires the lock only after the first commits, sees the now-existing open attempt, and is rejected by §9.2's "at most one open attempt" constraint rather than being assigned a colliding or skipped `attempt_number`. |
| Override approval racing an in-flight `GateEvaluation` on the same attempt (resolves REVAL-008) | The evaluation in flight is unaffected (evaluations are immutable, §9.3); if the override approval's transaction commits first and closes the attempt, a subsequently-committing evaluation for that same now-closed attempt is rejected by the service layer — an evaluation is never recorded against an already-closed attempt. |

---

## 15. PostgreSQL validation

Resolves CHTR-010.

### 15.1 Binding rule

Development and broad behavioral testing (evidence lifecycle, policy CRUD,
gate-sequence logic, authorization denial, classification, override
approval flow, most of §18's matrix) **may** proceed against SQLite, per
this repository's existing convention.

**SQLite results must never be represented as PostgreSQL lock validation.**
Specifically, the following require an actual PostgreSQL connection and may
not be claimed as validated on SQLite evidence alone:

- `select_for_update()` genuinely blocking a concurrent transaction (SQLite
  does not have PostgreSQL's row-level MVCC locking semantics; a test that
  merely calls `select_for_update()` without a genuinely concurrent second
  connection proves nothing about lock contention on either engine, but a
  *concurrency* test using threads/subprocesses against SQLite specifically
  proves nothing about PostgreSQL's actual behavior under contention).
- Simultaneous gate decisions (§14.4 row 1).
- Policy-pinning race behavior (§14.4 row 4).
- Freeze/refreeze races (§14.4 row 7).
- Override decision and expiry races (§14.4 rows 6 and 8).
- Transactional hold recomputation under concurrent cause open/close
  (§8.4).
- **Downstream invalidation cascade locking (added, resolves
  REVAL-008-RESIDUAL)** — the package/gate-state row locking used by
  §9.5's downstream-invalidation cascade when a predecessor-satisfying
  override expires or is revoked concurrently with a downstream decision
  or a new downstream attempt being opened.

### 15.2 Closure gate

Milestone 1 **cannot** be declared technically closed (§20) until one of:

(a) the seven PostgreSQL-dependent scenarios above are executed against a
real PostgreSQL instance with recorded, reproducible evidence (command,
output, pass/fail, database engine explicitly logged), or

(b) PostgreSQL remains unavailable and the owner records an explicit,
separate, written disposition accepting this as an open, non-blocking
limitation for this specific milestone closure — using the same acceptance
mechanism already used for the foundation's own PostgreSQL deferral (see
`DT_BEACH_CURRENT_STATE.md`'s acceptance record) — never silently carried
forward without a fresh, milestone-specific acceptance statement.

Absent either (a) or (b), Milestone 1 remains open regardless of how many
SQLite tests pass.

---

## 16. API, UI, and admin boundary

Resolves CHTR-009.

### 16.1 Authorized Milestone 1 surfaces (narrow, exhaustive list)

- Policy administration (create `GatePolicy`, create/edit `DRAFT`
  `GatePolicyVersion`).
- Policy publication (the one-way `DRAFT → PUBLISHED` action).
- Package gate summary (A1–A6 state row per package).
- Gate detail (per-gate requirement satisfaction, authorized-projection
  evidence references, per §6.2).
- Evidence requirement status (upload/verify/reject actions on a gate's
  `EvidenceBundle`/`EvidenceItem`s — reusing existing `apps.audit` views
  where they already generically support arbitrary targets, extending
  only where a gate-specific URL/view wrapper is genuinely required).
- Freeze / refreeze action screens.
- Gate decision (pass/fail) action screens.
- Override request / decision / revocation screens.
- Participant-safe projections of the above (client-scoped, package-scoped,
  per §6.2 and §13).

### 16.2 Explicit exclusions (binding)

Milestone 1 must **not** implement:

- Generalized API convergence (a repository-wide, package-aware generic
  API layer for arbitrary cross-organization participants) — Milestone 3.
- Centralized search/autocomplete convergence — Milestone 3.
- Generalized export/report convergence — Milestone 3.
- QR projection integration — Milestone 3.
- Notification-channel convergence beyond whatever minimal, already-existing
  `audit.Notification` usage is reused as-is (no new channel/integration
  work) — Milestone 3.
- The full Milestone 2 localization layer (locale switching UI, translation
  adapter wiring) — Milestone 2. Milestone 1 only satisfies §17's
  translation-readiness rule.

Any HTTP endpoint or view added for Milestone 1 must be justified against
the list in §16.1; anything not on that list is out of scope regardless of
how small it seems, per the CHTR-009 boundary this section exists to draw.

---

## 17. Localization boundary

- Every user-facing string this Charter's models/services introduce
  (requirement codes, gate codes, action codes, state names) is a stable,
  language-neutral code — never itself a display string. Display labels
  belong in a template/UI-layer mapping, exactly like
  `GateDefinition.code`/`RoleAssignment.role_code`/`Classification` choices
  already separate stable codes from their `TextChoices` display labels
  today.
- Canonical locales remain `es`, `en`, `zh-Hans`; timezone remains
  `America/Santo_Domingo` — Milestone 1 introduces no new locale and no
  timezone handling of its own; all `DateTimeField`s follow the existing
  project-wide timezone convention unchanged.
- "Translation-ready" means: every display string surfaced by Milestone 1
  screens is capable of being run through the existing
  `governance.DerivedArtifact` (`ArtifactType.TRANSLATION`) mechanism
  without any Milestone-1-specific code change — it does not mean
  Milestone 1 ships actual translated strings or wires a translation
  adapter. That wiring remains Milestone 2 exactly per the roadmap.
- Original values (evidence, reasons, comments) are never overwritten by a
  translation — any translation of gate-domain text is a new
  `DerivedArtifact` row, never a mutation of the source field, mirroring
  §6.2's rule for evidence.

---

## 18. Required tests

The following test categories are mandatory before Milestone 1 may be
considered implementation-complete against this Charter (§20). Each row
below names the scenario; the implementer chooses exact test file
organization, but every row must be traceable to at least one test.

**Ordering and validity**
1. Strict `A1→A6` order enforced (cannot pass `A3` before `A2` passes).
2. Passing a gate whose predecessor is currently invalid is rejected.
3. Passing a gate whose predecessor is currently invalidated is rejected.
4. Passing a gate whose predecessor is currently expired is rejected.

**Policy**
5. Draft policy version is editable; published version is not.
6. Attempting to edit a published version is rejected at the service layer.
7. Exactly one canonical default policy exists and resolves per §3.3.
7a. Attempting to flag a second `GatePolicy` row `is_canonical_default =
    True` while one already holds it is rejected by the conditional
    unique constraint (resolves REVAL-003).
7b. Attempting to flag an organization-owned (`organization` non-null)
    `GatePolicy` as `is_canonical_default = True` is rejected by the
    service layer (resolves REVAL-003).
8. An organization's own policy is selected over the canonical default
   when both exist, per the resolution order in §3.3.
9. Package pinning is permanent for the life of the package (re-pin
   attempt rejected).
10. Publishing a new version supersedes correctly (`supersedes` chain
    intact; old version remains valid for already-pinned packages).
10a. Publishing a `GatePolicyVersion` whose `gate_schema` is missing a
     gate key, contains an unrecognized extra key, or has a malformed
     `overridable`/`non_overridable_requirements` entry is rejected
     (resolves REVAL-010).
10b. Withdrawing the canonical default's only published version with no
     replacement available is rejected; a populated-database migration
     attempted with zero usable canonical default refuses to proceed
     (resolves REVAL-003).
10c. Publishing a `GatePolicyVersion` whose `A2` entry has
     `overridable = True` is rejected outright, regardless of any other
     field's validity (resolves REVAL-004-RESIDUAL).
10d. Every gate entry's `attempt_creation_capability` is present, non-null,
     and names a registered capability code before a `gate_schema` may
     publish; a missing, null, or unregistered value on any of the six
     entries is rejected (resolves NF-3).

**Existing-package migration**
11. Empty-database migration succeeds and seeds exactly one canonical
    policy + version.
12. Populated-database migration pins every existing package exactly once,
    across all four `ProcurementPackage.Status` values, with zero
    fabricated `PASSED` results.
13. Re-running the migration (idempotency) creates no duplicate
    `PackagePolicyAssignment` rows.

**Evidence**
14. Cross-package evidence denial: an `EvidenceItem` from package B cannot
    satisfy a requirement for package A's attempt.
15. Caller-supplied package mismatch (a request naming package A's attempt
    but package B's evidence id) is rejected and denial is audited.
16. Uploader/verifier separation is enforced (same-user verification
    rejected).
17. Evidence rejection, expiry (age-out), revocation/supersession are each
    independently tested and each correctly changes (or fails to change)
    requirement satisfaction as specified in §5.3.
17a. Bundle-grouping determinism: two requirement codes sharing an
     identical `(required_verifier_capability, minimum_review_state,
     minimum_count)` tuple produce exactly one `EvidenceBundle`; two codes
     with a differing tuple produce two separate bundles, both targeting
     the same `GateAttempt` (resolves REVAL-001/REVAL-002).
17b. `GateAttempt` has no `evidence_bundle` field; every `EvidenceBundle`
     for an attempt is discoverable only by querying on
     `content_type`/`object_id` against that attempt (resolves
     REVAL-001).

**Freeze and change control**
18. A2 freeze creates revision 1 correctly.
18a. For every `PackageFreezeRevision` created (initial freeze, a plain
     refreeze, and a critical-change-triggered refreeze), `policy_version`
     equals the package's `PackagePolicyAssignment.policy_version` at that
     moment; a deliberately mismatched value supplied to the freeze/refreeze
     service function is rejected before the row is created; the invariant
     is additionally asserted by re-reading historical revisions after
     other, unrelated mutations occur (resolves REVAL-009-TRACE).
19. Freeze revisions are immutable (attempt to edit a `SUPERSEDED` or
    `CURRENT` revision's `frozen_fields` directly is not exposed by any
    service function — enforced by absence of such a function, and by a
    guard test attempting a direct model save and expecting the
    application layer never to call it that way).
20. A critical post-A2 `ChangeRequest` approval triggers hold + A2/A3–A6
    invalidation atomically (§8.1).
21. A3–A6 invalidated results remain queryable as history after
    invalidation (not deleted).
22. Refreeze creates revision 2+, correctly referencing the triggering
    `ChangeRequest` and predecessor revision.
23. Multiple concurrent Change Requests each open independent hold causes;
    hold clears only when all are resolved (§8.4).
24. A Risk Flag plus a Change Request both holding a package requires both
    to resolve before hold clears (§8.3/§8.4).
25. Gate evaluation is reproducible: replaying the same evidence/policy
    state produces the same `GateEvaluation.requirement_results`.
25a. A critical post-A2 `ChangeRequest` approval also revokes every
     currently-active `ProcurementGateOverride` on `A3`–`A6` for the
     package, system-attributed (`revoked_by = NULL`), with
     `GATE_OVERRIDE_REVOKED` recorded per override and no confidential
     `ChangeRequest` free text entering the audit `reason` (resolves
     REVAL-004).
25b. `ChangeRequest.field_name` values outside the shared frozen-field
     vocabulary registry (§8.2) are rejected by the service layer; values
     inside the registry but unclassified (critical/non-critical) in the
     pinned policy version default to critical treatment (resolves
     REVAL-011).

**Change Request entry point (resolves NF-1 and REVAL-011-ENFORCEMENT)**
25c. Static/architectural test: no Milestone 1 view, form, API endpoint,
     admin action, management command, or service imports or calls
     `apps.governance.services.request_change` directly; every such path
     resolves exclusively through
     `apps.procurement_gates.services.request_gate_aware_change`.
25d. A non-critical `ChangeRequest` created via `request_gate_aware_change`
     against a package with no other open hold cause leaves the package
     **not** on hold after the transaction commits, despite
     `request_change`'s own unconditional `is_on_hold = True` write inside
     the same call.
25e. A non-critical `ChangeRequest` created via `request_gate_aware_change`
     against a package that already has an unrelated open hold cause (e.g.
     an unresolved `HIGH_RISK` `RiskFlag`) leaves the package correctly
     still on hold, and that unrelated cause is neither cleared nor
     duplicated by this request.
25f. A critical `ChangeRequest` created via `request_gate_aware_change`
     opens a `PackageHoldCause` and leaves the package on hold, unchanged
     from §8.1's existing behavior.
25g. Authorization is evaluated, and a denial is durably audited via
     `PRIVILEGED_ACCESS_DENIED`, before any protected `ChangeRequest`
     field is retrieved by `request_gate_aware_change` for an unauthorized
     actor.
25h. Registry enforcement: canonical `FROZEN_FIELD_CODES` values succeed;
     a known alias (e.g. `trade_terms` for `incoterm`) is rejected, not
     silently mapped; a `field_name` value found only in historical/legacy
     data and absent from the registry is treated as critical when
     encountered by gate-domain evaluation, never silently passed through
     as non-critical; a translation/display label of a registry code never
     alters the stored code itself.

**Decisions and attempts**
26. A `GateDecision` requires the exact capability declared in
    `gate_schema` — a user with a different, plausible-sounding capability
    is denied.
26a. Creating a `GateAttempt` for `A2`–`A6` requires the exact
     `attempt_creation_capability` declared in `gate_schema[gate_code]`
     (`CREATE_PROCUREMENT_GATE_ATTEMPT` for the canonical policy); a user
     holding only the gate's `GateDecision` capability (and not
     `attempt_creation_capability`) is denied attempt creation, and a user
     holding only `attempt_creation_capability` (and not the decision
     capability) is denied recording the eventual `GateDecision` — proving
     the two are enforced as genuinely separate capabilities (resolves
     NF-3).
27. `GateAttempt` immutability: no service function edits a closed
    attempt's `gate_code`/`attempt_number`/`policy_version`.
27a. `attempt_number` allocation: a concurrency test opening two attempts
     for the same `(package, gate_code)` from two threads/processes
     simultaneously produces exactly one attempt numbered 1 and rejects
     the second caller under the open-attempt constraint, never assigning
     a duplicate or skipped number (resolves REVAL-007).
27b. Creating a package's first `A1` `GateAttempt` succeeds for a user
     holding only an organization-scoped `CapabilityGrant` for
     `CREATE_PROCUREMENT_GATE_ATTEMPT` (no package-scoped role assignment
     yet exists) and fails for a user holding neither organization- nor
     package-scoped grant of that exact capability (resolves REVAL-005 and
     REVAL-005-RESIDUAL).

**Overrides**
28. Override request requires minimum evidence per policy where declared.
29. Override approval requires a different user than the requester
    (separation of duties rejected otherwise).
30. Override rejection is recorded and does not change gate state.
31. Override expiry is detected lazily and recorded exactly once
    (`GATE_OVERRIDE_EXPIRED` not duplicated on repeated reads).
32. Override revocation immediately changes `compute_gate_state`'s output
    for that attempt.
33. Non-overridable controls (§12) cannot be bypassed by any override —
    tested per control (authorization, package scope, classification,
    separation of duties, predecessor validity default, confidentiality,
    audit requirement, written reason, finite expiry, explicitly
    non-overridable requirement).
33a. `overridable = False` rejects every override request for that gate
     outright, regardless of capability held; `overridable = True` with a
     non-empty `non_overridable_requirements` list still requires those
     specific requirements to be genuinely satisfied by real evidence
     before approval, even though the gate is otherwise overridable
     (resolves REVAL-006).
33b. Approving an override sets the underlying `GateAttempt.closed_at`;
     after the override later expires or is revoked, `compute_gate_state`
     derives the attempt's pre-override state from its last real
     `GateEvaluation` without reopening it, and no further progress on
     that gate is possible without opening a new `GateAttempt` (resolves
     REVAL-008).
33c. `gate_schema["A2"].overridable` cannot be published as `True` (see
     10c); requesting a `ProcurementGateOverride` for `A2` is rejected
     outright by the request-creation service function, regardless of
     capability held, and the denial is audited (resolves
     REVAL-004-RESIDUAL).

**Downstream invalidation cascade (resolves REVAL-008-RESIDUAL)**
33d. A downstream gate `A(n+1)` that reached `PASSED` while relying on
     `A(n)`'s `OVERRIDDEN` state (`override_satisfies_successor_predecessor
     = True`) is invalidated via a `GateInvalidation` row when `A(n)`'s
     backing override subsequently **expires**.
33e. The same downstream invalidation occurs when `A(n)`'s override is
     **revoked by a human actor**, system-attributed fields set correctly
     for the downstream `GateInvalidation` rows.
33f. The same downstream invalidation occurs when `A(n)`'s override is
     revoked by the **system-attributed** post-A2 critical-change cascade
     (§8.1 step 4).
33g. Multiple downstream gates (`A(n+1)` through `A6`, chained) are each
     invalidated when the originating predecessor override lapses,
     verified for a package with more than one downstream `PASSED` gate in
     the chain.
33h. Multiple independent descendant chains (e.g. two different packages,
     or two different gates within the allowed policy configuration) are
     each invalidated independently without cross-package or cross-chain
     leakage.
33i. The cascade transaction rolls back cleanly and leaves no partial
     invalidation state if an unexpected error occurs mid-cascade (e.g.
     after invalidating `A(n+1)` but before `A(n+2)`).
33j. The cascade is idempotent under retry: a retried expiry/revocation
     observation with the same idempotency key does not create duplicate
     `GateInvalidation` or `GATE_DOWNSTREAM_INVALIDATED` rows.
33k. The `GATE_DOWNSTREAM_INVALIDATED` audit event's safe projection
     references only the triggering override's identifier, never any
     confidential evidence or `ChangeRequest` free text.

**Authorization and confidentiality**
34. Same-organization, unauthorized-capability actor is denied on every
    path in §13's table.
35. Different-organization actor is denied.
36. Authority for package A does not authorize any action on package B.
37. Classification denial: an unauthorized viewer's gate-detail projection
    omits item identities per §6.2.
38. Confidential-sentinel absence: a unique, disposable sentinel value
    placed in a hidden `EvidenceItem`/`Document`/factory-identity field
    never appears in an unauthorized viewer's HTTP response body, JSON
    payload, or exported projection for any gate-domain endpoint.
39. Denial-audit durability: every rejection above produces a persisted
    `PRIVILEGED_ACCESS_DENIED` event with correct package/gate/capability
    metadata.

**Concurrency and idempotency**
40. Duplicate request idempotency: retried mutation with the same
    idempotency key returns the original result, not a duplicate row.
41. Simultaneous decision behavior matches §14.4's table for at least the
    "simultaneous `GateDecision`s" and "duplicate override decisions" rows
    on SQLite (thread/subprocess-based concurrency test, acceptable on
    SQLite for logic correctness, but see §15 for which specific races
    additionally require PostgreSQL evidence before closure).
42. Policy-publication race and package-pinning race each produce exactly
    one winning row, never two.
43. Freeze/refreeze race produces exactly one `CURRENT` revision at a time.
43a. Two simultaneous attempt-open calls for the same `(package,
     gate_code)` never produce duplicate or skipped `attempt_number`
     values, verified against the exact locked allocation algorithm in
     §9.2 (resolves REVAL-007).

**Regression guards**
44. The existing eight-gate Handoff workflow's full existing test suite
    continues to pass unmodified — proves Milestone 1 introduced no
    regression into `apps.workflow`.
45. `ProcurementPackage.Status` transitions and their existing tests
    continue to pass unmodified — proves no regression into the existing
    commercial-package state machine.

**Foreign-key deletion policy (resolves NF-4)**
46. For every `PROTECT` relationship listed in §3.5's table, attempting the
    blocked deletion raises the expected integrity error and the
    referencing historical row remains intact.
47. Deleting a `PackageGateState` cache row (§9.6, the sole `CASCADE`
    relationship in this Charter) is harmless: `rebuild_gate_state`
    regenerates an identical row from history alone, and no historical row
    is affected.

---

## 19. Live validation

Resolves CHTR-008.

### 19.1 Binding method

A **real, running-application HTTP/browser walkthrough** — not an
automated test suite run, not a management-command script exercising
services directly — using disposable, clearly-labeled test data, is
required before Milestone 1 may be declared technically complete. "Live
validation" is not satisfied by citing the automated test matrix in §18,
however thorough.

### 19.2 Required walkthrough paths

Each of the following is walked through the actual UI/HTTP layer, as a
logged-in user with realistic role/capability grants (not a superuser
bypass), with each step's request/response and resulting audit trail
recorded:

1. One **Controlled Transparency** package taken through a full,
   successful `A1→A6` path.
2. One **Controlled Confidentiality** package taken through a full,
   successful `A1→A6` path.
3. One blocked path (attempting `A3` before `A2` passes; observe the
   `BLOCKED` state and denial in the UI, not just a 4xx status code).
4. One failed-evidence path (a `GateDecision` with `outcome=FAILED`,
   re-attempt, and eventual pass).
5. One cross-package denial (attempt to reference package B's evidence
   from package A's session; observe rejection and audit entry).
6. One override path (request → approve → observe `OVERRIDDEN` state in
   the gate summary UI, and confirm the underlying attempt shows as closed
   per §9.2/§9.5).
7. One override expiry or revocation path (observe the state correctly
   revert to the attempt's pre-override derived state, per §9.5's
   resolution — not `PASSED`, not a reopened attempt — and confirm a new
   `GateAttempt` is required to actually progress the gate afterward).
8. One post-A2 critical-change path (approve a critical `ChangeRequest`,
   observe automatic hold + invalidation of A3–A6 in the UI, **and**
   observe every currently-active override on A3–A6 for that package
   revoked in the same action, system-attributed, per §8.1 step 4).
9. One refreeze path (perform the refreeze, observe hold clearing once all
   causes are resolved, observe the new `PackageFreezeRevision`).
10. Confidentiality sentinel-absence checks: place a unique sentinel value
    in a hidden field for each walkthrough package and confirm it never
    appears in any client-scoped or under-authorized page rendered during
    the walkthrough (view-source / response-body grep, not visual
    inspection alone).

### 19.3 Required record for each path

- Exact steps taken (URLs/actions), in enough detail to reproduce.
- Actors used and their exact capability grants (not "an admin" — the
  specific role assignments and capability grants that made each action
  possible or correctly impossible).
- Resulting state observed in the UI at each step.
- The specific `AuditEvent`/gate-domain audit rows produced, cross-checked
  against §10's expected action codes.
- Explicit confirmation of confidentiality sentinel absence (what sentinel,
  where placed, where checked, result).
- Cleanup behavior (disposable data removed or clearly marked non-production
  afterward).
- Database engine used for this specific walkthrough (SQLite or
  PostgreSQL — recorded honestly; see §15 for which claims require
  PostgreSQL specifically).
- Whether this walkthrough was local, live-staging, deployed, or
  production — recorded honestly; Milestone 1 does not require production
  validation, but must never claim it if it didn't happen.

---

## 20. Completion criteria

Milestone 1 **implementation** may be authorized only after this Charter
itself is independently reviewed and owner-approved — this Charter
document, once committed, is not itself that approval (§21).

Once implementation is authorized and undertaken, Milestone 1 **technical
completion** requires all of the following, together, before it may be
declared closed:

1. Implementation matches this Charter's binding decisions (§1–§19,
   corrected from an erroneous "§1–§17" citation in version 1 — resolves
   REVAL-012; §18's required tests and §19's live validation are just as
   binding as §1–§17's architectural decisions); any deliberate deviation
   is itself documented as a superseding Charter amendment before
   completion is declared, not silently coded around it.
2. No unresolved Critical or High finding from the implementation's own
   review remains open.
3. Migrations are clean (`makemigrations --check --dry-run`,
   `migrate --check` both pass).
4. All tests in §18's matrix pass, plus the full existing regression suite
   with no new failures.
5. PostgreSQL concurrency validation is completed per §15.1's seven named
   scenarios, or an explicit, separate, milestone-specific owner
   disposition accepts the gap per §15.2(b).
6. Live HTTP/browser validation per §19 is completed and recorded — not
   merely automated tests.
7. Documentation (this Charter plus the reconciliation list in the
   accompanying commit) is updated to reflect actual, verified
   implementation state — never planned or reported-but-unverified state.
8. A clean commit or an approved logical commit sequence exists.
9. Push to the current upstream branch succeeds.
10. Local HEAD and upstream HEAD are synchronized (`0`/`0` ahead/behind).
11. Working tree is clean.
12. The exact next action after closure is explicitly recorded (e.g.
    independent revalidation, then Milestone 2 planning) — Milestone 1
    closure never leaves "what happens next" implicit.

---

## 21. Charter finding reconciliation

### 21.1 CHTR-001–CHTR-012 (original independent Charter Review)

| Finding | Disposition | Resolved by |
|---|---|---|
| CHTR-001 — No standalone charter document existed | **Accept.** Standalone Charter created at `docs/MILESTONE_1_PROCUREMENT_GATES_CHARTER.md`. | This document. |
| CHTR-002 — `GateOverride` reuse mandate conflicted with ADR-020 | **Accept.** New `ProcurementGateOverride` model reuses the lifecycle/control *pattern*, not the Handoff-specific row or its foreign keys; ADR-020 is not reopened or contradicted. | §11. |
| CHTR-003 — No policy versioning/immutability/default/pinning mechanism | **Accept.** `GatePolicy`/`GatePolicyVersion`/`PackagePolicyAssignment` fully specified, including publish-immutability, canonical default resolution, org configuration, and transactionally-protected pinning. | §3. |
| CHTR-004 — No deterministic existing-package migration rule | **Accept.** Deterministic, non-fabricating migration defined per status, including empty- and populated-database expectations and an explicit non-historical-pass administrative exemption. | §4. |
| CHTR-005 — A2 freeze/reopen/invalidate semantics unreconciled with `frozen_snapshot` | **Accept.** Dedicated immutable `PackageFreezeRevision` history defined; `frozen_snapshot` redefined as a derived cache of the current revision, never the historical record. | §7, §8. |
| CHTR-006 — Override minimum-evidence/separation-of-duties/scope/non-overridable rules undefined | **Accept.** Fully enumerated, including a binding, absolute non-overridable-controls list and per-gate override eligibility defaults. | §11, §12. |
| CHTR-007 — No gate attempt/evaluation/decision/invalidation model or audit taxonomy | **Accept.** `GateAttempt`/`GateEvaluation`/`GateDecision`/`GateInvalidation` defined with exact meanings, plus a derived current-state cache and a rebuild-from-history requirement; new `AuditEvent.Action` codes enumerated. | §9, §10. |
| CHTR-008 — "Live validation" method undefined | **Accept.** Explicit, real HTTP/browser walkthrough method defined, distinct from and in addition to automated tests, with ten required paths and a required record format. | §19. |
| CHTR-009 — Milestone 1 vs. Milestone 3 API boundary ambiguous | **Accept as clarified boundary.** Narrow, exhaustive Milestone 1 surface list defined; generalized convergence work explicitly reserved for Milestone 3. | §16. |
| CHTR-010 — PostgreSQL availability/validation treatment unstated | **Accept with explicit rule.** SQLite permitted for general development; seven named lock-sensitive scenarios (six as of version 2, plus the version-3 downstream-invalidation-cascade scenario, resolves REVAL-008-RESIDUAL) require real PostgreSQL evidence or a separate, milestone-specific owner disposition before closure — never silently carried forward as validated. | §15. |
| CHTR-011 — `EvidenceBundle`/`EvidenceItem` reuse feasibility (positive finding) | **Accept.** Confirmed and specified in full; no new evidence-storage model introduced. | §5. |
| CHTR-012 — Evidence-classification inheritance from package visibility mode undefined | **Accept.** Deterministic classification precedence defined, plus participant-projection and information-absence rules and an explicit rule against exposing raw evidence merely because it is referenced. | §6. |

### 21.2 REVAL-001–REVAL-012 (independent Charter Revalidation of version 1, corrected in version 2)

The independent Milestone 1 Charter Revalidation performed against version 1
of this Charter (commit `b0cdf1cc4f6fb17dea206430ee0f1643710d2090`) found
twelve new findings — internal contradictions and omissions in version 1's
own text, distinct from the original CHTR-001–CHTR-012 review — and
returned **MILESTONE 1 CHARTER REQUIRES CORRECTION**. Version 2 (this
document) corrects all twelve:

| Finding | Original classification | Original severity | Blocked before correction? | Accepted disposition | Exact correction applied | Corrected Charter references |
|---|---|---|---|---|---|---|
| REVAL-001 — `GateAttempt`↔`EvidenceBundle` cardinality contradiction (§9.2's singular FK vs. §5.1's generic one-or-more target) | Data-model contradiction | Critical | Yes | Accept | Removed the `evidence_bundle` FK from `GateAttempt` entirely; evidence attaches exclusively via `content_type`/`object_id`, one or more bundles per attempt, per the deterministic grouping rule in §5.2 | §5.1, §5.2, §9.2, §18 tests 17a–17b |
| REVAL-002 — Charter assumed per-requirement `minimum_count`/`required_verifier_capability`/`minimum_review_state`, but `EvidenceBundle` stores these only bundle-wide | Contradicts reused foundation model | Critical | Yes | Accept | `EvidenceBundle`/`EvidenceItem` left unmodified; per-requirement values now live only in policy-schema JSON; a deterministic tuple-based grouping rule assigns requirement codes to bundles | §5.2, §18 tests 17a–17b |
| REVAL-003 — Contradictory canonical-default policy definitions (§3.1's "null means canonical" vs. §3.3's separate boolean) | Data-model contradiction / singleton ambiguity | Critical | Yes | Accept | `is_canonical_default` is now the sole determinant; `organization = NULL` redefined as mere platform-scope eligibility, never the marker itself; added an availability invariant preventing zero usable canonical defaults | §3.1, §3.3, §18 tests 7a–7b, 10b |
| REVAL-004 — Post-A2 invalidation cascade did not revoke active A3–A6 `ProcurementGateOverride` rows | Security/business-correctness gap | High | Yes | Accept | §8.1 gained step 4: every currently-active override on A3–A6 is revoked in the same transaction, system-attributed (`revoked_by = NULL`), reason references only the triggering `ChangeRequest`'s id (never its free text), `GATE_OVERRIDE_REVOKED` recorded, row never deleted | §8.1 step 4, §11.3, §18 test 25a |
| REVAL-005 — §13's authorization table required package-scoped capability to create `A1`'s `GateAttempt`, which is structurally impossible before `A1` exists | Authorization completeness gap | Medium-High | Yes | Accept | Added an explicit, narrowly-scoped exception: creating a package's *first* `A1` attempt requires an organization-scoped `CapabilityGrant` instead of a package-scoped one; every other attempt-creation path is unchanged | §13, §9.2 step 3, §18 test 27b |
| REVAL-006 — Overlapping, unreconciled override-eligibility flags (`overridable` vs. `non_overridable`) | Ambiguity / redundant control | Medium | Yes | Accept | Consolidated into two explicitly composed controls: gate-level `overridable` (all-or-nothing) plus requirement-level `non_overridable_requirements` (never excused even when overridable); fail-closed on malformed configuration | §12 item 10, §12.1, §3.1, §18 test 33a |
| REVAL-007 — Atomic `attempt_number` allocation underspecified | Underspecified atomic operation | Medium | Yes | Accept | Exact five-step locked transaction specified: `select_for_update()` on the package row, then `1 + Max(attempt_number)` inside that same lock, never an unlocked `count()+1` | §9.2, §14.4, §18 tests 27a, 43a |
| REVAL-008 — Override grant/expiry/revocation interaction with `GateAttempt.closed_at` undefined | State-machine gap | High | Yes | Accept | Approving an override now closes the attempt (`closed_at` set); expiry/revocation never reopens it — state falls back to the closed attempt's last real evaluation; progress requires a new attempt | §9.2, §9.5, §11.3, §8.1 step 4, §18 test 33b |
| REVAL-009 — `PackageFreezeRevision.policy_version` rationale was internally self-contradictory | Documentation smell / dead field | Low | No | Accept | Field retained; rationale rewritten truthfully (historical self-containment and forward compatibility) instead of citing a scenario §3.4 already forbids; added an explicit Milestone-1 equality invariant | §7.1 |
| REVAL-010 — `gate_schema` completeness for all six `A1`–`A6` gates undefined | Underspecification | Medium | Yes | Accept | Publication now rejects any `gate_schema` without exactly six keys (`A1`–`A6`), no missing, no extra; a gate with no requirements still needs an explicit empty entry | §3.1, §3.2, §18 test 10a |
| REVAL-011 — No shared vocabulary between `ChangeRequest.field_name` and `PackageFreezeRevision.frozen_fields` keys | Underspecification | Low-Medium | No | Accept | Defined one canonical, enumerated frozen-field code registry; both sides validated against it at the service layer; the `ChangeRequest` model itself is unmodified | §8.2, §18 test 25b |
| REVAL-012 — Completion cross-reference incorrectly excluded §18–§19 | Editorial cross-reference error | Low | No | Accept | Changed "§1–§17" to "§1–§19" in completion criterion 1 | §20 item 1 |

**No implementation has occurred.** All three tables in this section record
documentation dispositions only. A1–A6 remain unimplemented. Version 3 of
this Charter has not itself been independently revalidated — see §22.

### 21.3 NF-1, REVAL-004-RESIDUAL, REVAL-005-RESIDUAL, REVAL-008-RESIDUAL,
REVAL-009-TRACE, REVAL-011-ENFORCEMENT, NF-2, NF-3, NF-4, NF-7
(independent Milestone 1 Charter Version 2 Revalidation, corrected in
version 3)

The independent Milestone 1 Charter Version 2 Revalidation performed
against commit `3b62228b4a6efb4079e7f8c010e107fcf9de639a` confirmed
REVAL-001, 002, 003, 006, 007, 009 (text), 010, 011 (registry design), and
012 as resolved, found two of version 2's own REVAL corrections
(REVAL-004, REVAL-008) left residual gaps, and identified five newly
discovered findings — and returned **MILESTONE 1 CHARTER VERSION 2
REQUIRES CORRECTION**. Version 3 (this document) corrects all ten accepted
findings:

| Finding | Original classification | Original severity | Blocked before correction? | Accepted disposition | Exact correction applied | Corrected Charter references |
|---|---|---|---|---|---|---|
| NF-1 — §8.2's "non-critical changes never touch hold state" was contradicted by the current, unmodified `apps.governance.services.request_change`, which unconditionally sets `is_on_hold = True` on every `ChangeRequest` creation | Internal contradiction against current repository code | Critical | Yes | Accept | Added `apps.procurement_gates.services.request_gate_aware_change` as the sole Milestone 1 Change Request entry point, wrapping the unmodified `request_change`, with its own hold-state recomputation from all open `PackageHoldCause` rows | §8.2.1, §13, §18 tests 25c–25g |
| REVAL-004-RESIDUAL — the post-A2 cascade revoked overrides only on A3–A6, never addressing an `A2` gate left `OVERRIDDEN` by a policy-opt-in override | State-machine gap | High | Yes | Accept | `A2` is now unconditionally, permanently non-overridable (publication-time rejection); `A2` can never enter `OVERRIDDEN`, so the gap cannot occur | §3.2, §12.1, §8.1 closing note, §18 tests 10c, 33c |
| REVAL-005-RESIDUAL — the exact A1-bootstrap capability code was never named, only its organization-vs-package scope shape | Authorization completeness gap | High | Yes | Accept | Added `CREATE_PROCUREMENT_GATE_ATTEMPT` as a new capability code; the A1-bootstrap grant now names it explicitly | §13, §18 test 27b |
| REVAL-008-RESIDUAL — no defined behavior for a downstream `PASSED`/`OVERRIDDEN` gate when the predecessor override it relied on later expires or is revoked | Internal contradiction / stale-descendant gap | Critical | Yes | Accept | Added an explicit, locked downstream-invalidation cascade (§9.5), mirroring §8.1's shape, with a new `GATE_DOWNSTREAM_INVALIDATED` audit action | §9.5, §10, §15.1 (7th PostgreSQL scenario), §18 tests 33d–33k |
| REVAL-009-TRACE — §7.1 promised a required §18 test for the `policy_version` equality invariant that did not exist in §18 | Testability gap | Medium | No | Accept | Added §18 test 18a, covering initial freeze, refreeze, critical-change refreeze, historical revisions, and a deliberately mismatched rejected input | §7.1, §18 test 18a |
| REVAL-011-ENFORCEMENT — the frozen-field registry's enforcing "service layer" and its dependency direction against `apps.governance` were never named | Architecture conflict | Medium | Yes | Accept | Named `request_gate_aware_change` (§8.2.1) as the sole enforcement point; stated the binding one-way dependency direction (`procurement_gates` → `governance`, never the reverse) | §8.2.2, §18 test 25h |
| NF-2 — `PackagePolicyAssignment.superseded_by` was defined but structurally unusable under §3.4's own permanent-pin rule, with no rationale | Internal contradiction / dead field | Medium | No | Accept | Removed the field entirely; §3.4 states no re-pinning/supersession concept exists in Milestone 1 | §3.4 |
| NF-3 — attempt-creation capability codes for A2–A6 (and A1's exact code) were never named; §13 referenced a `gate_schema` field that did not exist in §3.1/§3.2's validated field list | Internal contradiction / testability gap | High | Yes | Accept | Added the required, publish-validated `attempt_creation_capability` field to every gate entry; added `CREATE_PROCUREMENT_GATE_ATTEMPT` as its canonical value for all six gates | §3.1, §3.2, §13, §18 tests 10d, 26a |
| NF-4 — no `on_delete` behavior was specified for any FK on any of the ~8 new Charter models | Migration gap | Medium | No | Accept | Added an exhaustive, field-by-field `on_delete` table (§3.5) with a `PROTECT`-by-default rule for history-critical rows and exactly one documented `CASCADE` (the rebuildable `PackageGateState` cache) | §3.5, §18 tests 46–47 |
| NF-7 — the illustrative canonical-default `UniqueConstraint(fields=[], ...)` example was invalid Django syntax | Editorial issue | Low | No | Accept | Replaced with a valid `UniqueConstraint(fields=["is_canonical_default"], condition=..., name=...)` example | §3.3 |

**No implementation has occurred.** All three tables in this section
record documentation dispositions only. A1–A6 remain unimplemented.
Version 3 of this Charter has not itself been independently revalidated —
see §22.

---

## 22. Status and next action

**MILESTONE 1 CHARTER CORRECTION CYCLE 2 COMPLETE (VERSION 3) — NOT YET
INDEPENDENTLY REVALIDATED — NOT OWNER-APPROVED.**

Version 1 of this Charter was independently revalidated and returned
MILESTONE 1 CHARTER REQUIRES CORRECTION, with twelve findings
(REVAL-001–REVAL-012, §21.2). Version 2 corrected all twelve, but was
itself independently revalidated (the Independent Milestone 1 Charter
Version 2 Revalidation, performed against commit
`3b62228b4a6efb4079e7f8c010e107fcf9de639a`) and returned **MILESTONE 1
CHARTER VERSION 2 REQUIRES CORRECTION**, finding two of version 2's own
REVAL corrections incomplete (REVAL-004-RESIDUAL, REVAL-008-RESIDUAL) and
five newly discovered findings (NF-1, NF-2, NF-3, NF-4, NF-7), plus two
further, more narrowly-scoped gaps (REVAL-005-RESIDUAL,
REVAL-009-TRACE/REVAL-011-ENFORCEMENT) — ten findings in total, all listed
in §21.3. Version 3 (this document) corrects all ten. Version 3 has
**not** itself been independently revalidated. Milestone 1 implementation
remains unauthorized. The exact next action is a new, independent Fable 5
Charter revalidation session against the commit that introduces this
version — confirming this Charter actually resolves CHTR-001 through
CHTR-012, REVAL-001 through REVAL-012, and NF-1/REVAL-004-RESIDUAL/
REVAL-005-RESIDUAL/REVAL-008-RESIDUAL/REVAL-009-TRACE/
REVAL-011-ENFORCEMENT/NF-2/NF-3/NF-4/NF-7 as claimed, contains no internal
contradictions of its own, and does not itself require further correction
— before any owner-approval decision or A1–A6 implementation
authorization is considered. Do not begin A1–A6 implementation.
