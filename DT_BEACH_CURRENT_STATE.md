# DT Beach Supply Control — Current State

Last updated: 2026-07-21

## Foundation status: CLOSED AND OWNER-ACCEPTED

**The Controlled Transparency, Commercial Confidentiality & Authorization
Foundation is closed and owner-accepted. Foundation correction cycles 2
and 3 have both been independently revalidated. Milestone 1 Increment 1 is
closed and owner-accepted. Milestone 1 Increment 2 is also closed and
owner-accepted at `acd2becce64e99c2dac559ccf805b1c64344debd` after Codex
verification and Harrison's completed real-browser walkthrough. Increment 3
is not authorized.**

Accepted foundation implementation commit:

`2c52b0b83340fda2eaa84700eaeddfbe0839d6d8`

### Harrison's explicit owner and business acceptance

Recorded 2026-07-20, America/Santo_Domingo:

> I explicitly accept Foundation Correction Cycles 2 and 3 at commit
> 2c52b0b83340fda2eaa84700eaeddfbe0839d6d8.
>
> I accept the independently validated Controlled Transparency, Commercial
> Confidentiality & Authorization Foundation as technically complete for the
> current roadmap gate.
>
> I accept the measured privileged-audit linear N+1 query characteristic as a
> non-blocking, pilot-scale performance limitation.
>
> PostgreSQL runtime validation, database-level audit append-only enforcement,
> backup restoration, deployed proxy/cache validation, production log-sentinel
> analysis, and all other documented owner or production validations remain open
> limitations and are not represented as complete.
>
> CTCF-ASSERT-HTTP-019 remains deferred. No Verification Assertion revocation HTTP
> route is approved or implemented.
>
> The Controlled Transparency, Commercial Confidentiality & Authorization
> Foundation is now closed.
>
> The next authorized activity is the independent Milestone 1 Charter review only.
>
> Procurement Gates A1–A6 implementation is not authorized yet.

### Independent validation evidence backing this acceptance

An independent Fable revalidation of commit `2c52b0b` reproduced, fresh:

| Check | Result |
|---|---|
| `manage.py check` | Passed; no issues |
| `makemigrations --check --dry-run` | Passed; no model changes detected |
| `migrate --check` | Passed; no unapplied migrations |
| `showmigrations --plan` | **72/72 applied**, 0 pending |
| Focused foundation/remediation suite | **53 passed, 0 failed** (SQLite) |
| Cycle 2+3 suite (`test_privileged_audit_scope.py` + `test_change_request_projection.py`) | **39 passed, 0 failed** (SQLite) |
| Full regression suite | **511 passed, 0 failed** (SQLite) |

No new Critical or High foundation blocker was found. One newly-measured,
non-blocking performance characteristic was identified and is now
owner-accepted: resolving scope for a batch of all-resolvable-but-unauthorized
privileged-audit candidate events costs approximately 65 SQL queries for 31
such events (~2.10 queries/event) — a **Low** severity, pilot-scale
performance limitation, not a confidentiality defect (see
`docs/KNOWN_LIMITATIONS.md` and `ASSUMPTIONS.md` A76).

PostgreSQL was **not** validated (no Docker daemon in the validation
environment) — this remains an open limitation, not represented as
complete. `CTCF-ASSERT-HTTP-019` remains deferred; no Verification
Assertion revocation HTTP route exists or is approved.

### Active activity

**Milestone 1 Charter Definition and Reconciliation — complete, locally,
documentation-only.**

The independent Milestone 1 Charter Review (`CHTR-001` through `CHTR-012`)
found that no standalone Milestone 1 charter existed — only a 26-line
acceptance-criteria summary in
`docs/MARKETMATCH_ARCHITECTURE_RECONCILIATION_AND_ROADMAP.md` — and that
several of its mandatory clauses (notably reusing `apps.workflow.GateOverride`
directly) conflicted with existing, documented decisions (ADR-020). A
complete standalone charter has now been authored at
`docs/MILESTONE_1_PROCUREMENT_GATES_CHARTER.md`, resolving every finding:

| Finding | Disposition |
|---|---|
| CHTR-001 | Accept — standalone Charter created |
| CHTR-002 | Accept — new procurement-scoped override reuses the pattern, not the Handoff-specific `GateOverride` row/FKs; ADR-020 not reopened |
| CHTR-003 | Accept — policy versioning, publication immutability, canonical default, org configuration, and package pinning specified |
| CHTR-004 | Accept — deterministic, non-fabricating existing-package initialization specified |
| CHTR-005 | Accept — immutable freeze-revision history and downstream invalidation specified |
| CHTR-006 | Accept — override minimum-evidence/separation-of-duties/scope/non-overridable rules specified |
| CHTR-007 | Accept — attempt/evaluation/decision/invalidation model and audit taxonomy specified |
| CHTR-008 | Accept — explicit live HTTP/browser validation method specified |
| CHTR-009 | Accept as clarified boundary — narrow Milestone 1 surfaces only; convergence work stays Milestone 3 |
| CHTR-010 | Accept with explicit rule — SQLite for development, PostgreSQL required for lock-sensitive closure or a separate owner disposition |
| CHTR-011 | Accept — positive finding confirmed; `EvidenceBundle`/`EvidenceItem` reuse specified |
| CHTR-012 | Accept — deterministic evidence-classification and safe-projection rule specified |

**No application code, template, test, or migration was written or modified
during this cycle.** A1–A6 remain entirely unimplemented.

**Update — independent Milestone 1 Charter Revalidation completed, result:
MILESTONE 1 CHARTER REQUIRES CORRECTION.** An independent revalidation of
Charter version 1 (commit `b0cdf1cc4f6fb17dea206430ee0f1643710d2090`) found
twelve new findings, REVAL-001 through REVAL-012 — internal contradictions
and omissions in the Charter's own text (e.g. a `GateAttempt`↔`EvidenceBundle`
cardinality contradiction, two incompatible canonical-default-policy
definitions, a post-A2 invalidation cascade that left active procurement-gate
overrides unrevoked) — distinct from the original CHTR-001–CHTR-012 review.
A documentation-only **Milestone 1 Charter Correction Cycle** then produced
Charter version 2, resolving all twelve:

| Finding | Disposition |
|---|---|
| REVAL-001 | Accept — `GateAttempt` no longer carries an `evidence_bundle` FK; evidence attaches only via generic target, one or more bundles per attempt |
| REVAL-002 | Accept — `EvidenceBundle`/`EvidenceItem` left unmodified; deterministic tuple-based bundle grouping added in the policy schema instead |
| REVAL-003 | Accept — `is_canonical_default` is now the sole canonical-default determinant; `organization = NULL` redefined as mere eligibility, never the marker itself |
| REVAL-004 | Accept — post-A2 critical-change cascade now also revokes every active A3–A6 override, system-attributed, audited |
| REVAL-005 | Accept — explicit organization-scoped-capability exception added for creating a package's first `A1` attempt |
| REVAL-006 | Accept — gate-level `overridable` and requirement-level `non_overridable_requirements` now explicitly composed, fail-closed on malformed config |
| REVAL-007 | Accept — exact locked `attempt_number` allocation algorithm specified |
| REVAL-008 | Accept — approving an override now closes the underlying attempt; expiry/revocation never reopens it |
| REVAL-009 | Accept — `PackageFreezeRevision.policy_version` rationale rewritten truthfully; field retained |
| REVAL-010 | Accept — publication now rejects any `gate_schema` missing or adding to the exact six `A1`–`A6` keys |
| REVAL-011 | Accept — one shared, enumerated frozen-field code registry now governs both `ChangeRequest.field_name` and `frozen_fields` keys |
| REVAL-012 | Accept — completion criterion corrected from "§1–§17" to "§1–§19" |

**No application code, template, test, or migration was written or modified
during this correction cycle either.** A1–A6 remain entirely unimplemented.

**Update — independent Milestone 1 Charter Version 2 Revalidation
completed, result: MILESTONE 1 CHARTER VERSION 2 REQUIRES CORRECTION.** An
independent revalidation of Charter version 2 (commit
`3b62228b4a6efb4079e7f8c010e107fcf9de639a`) confirmed REVAL-001, 002, 003,
006, 007, 010, and 012 as fully resolved, confirmed REVAL-009's text
correction but found its promised §18 test absent, confirmed REVAL-011's
registry design but found its enforcement location unnamed, found that two
of version 2's own corrections (REVAL-004, REVAL-008) left residual gaps
under adversarial follow-through, and identified additional findings not
previously flagged — including a direct contradiction against the current,
unmodified `apps.governance.services.request_change` (NF-1). Ten findings
in total, distinct from the original REVAL-001–REVAL-012 review. A
documentation-only **Milestone 1 Charter Correction Cycle 2** then
produced Charter version 3, resolving all ten:

| Finding | Disposition |
|---|---|
| NF-1 | Accept — added `apps.procurement_gates.services.request_gate_aware_change` as the sole Milestone 1 Change Request entry point, wrapping the unmodified `request_change` with its own hold-state recomputation |
| REVAL-004-RESIDUAL | Accept — `A2` is now unconditionally, permanently non-overridable (publication-time rejection); it can never enter `OVERRIDDEN`, closing the gap by construction |
| REVAL-005-RESIDUAL | Accept — added the `CREATE_PROCUREMENT_GATE_ATTEMPT` capability code; the A1-bootstrap grant now names it explicitly |
| REVAL-008-RESIDUAL | Accept — added an explicit, locked downstream-invalidation cascade for gates that relied on a predecessor's now-lapsed override |
| REVAL-009-TRACE | Accept — added the missing §18 test for the `PackageFreezeRevision.policy_version` equality invariant |
| REVAL-011-ENFORCEMENT | Accept — named `request_gate_aware_change` as the sole registry-enforcement point; stated the binding one-way dependency direction against `apps.governance` |
| NF-2 | Accept — removed the structurally-unusable `PackagePolicyAssignment.superseded_by` field entirely |
| NF-3 | Accept — added the required, publish-validated `attempt_creation_capability` field to every gate entry |
| NF-4 | Accept — added an exhaustive, field-by-field `on_delete` table for every new model, `PROTECT`-by-default with one documented `CASCADE` exception |
| NF-7 | Accept — replaced the invalid `UniqueConstraint(fields=[], ...)` example with valid Django syntax |

**No application code, template, test, or migration was written or
modified during this correction cycle either.** A1–A6 remain entirely
unimplemented. Charter version 3 has **not** been independently
revalidated and has **not** been owner-approved. Milestone 1
implementation remains **not authorized**.

Exact next action: **run a new, independent Fable 5 Charter revalidation
session against the commit introducing Charter version 3. Do not begin
A1–A6 implementation.**

**Update — independent Milestone 1 Charter Version 3 Revalidation
completed, result: MILESTONE 1 CHARTER VERSION 3 REQUIRES CORRECTION.** An
independent revalidation of Charter version 3 (commit
`f59237b6ba0c18e210c54f01cd79e98ea40e1709`, the commit introducing
version 3) found three blocking findings and two additional accepted
findings — distinct from the ten findings resolved in version 3 itself —
plus one editorial line-citation defect. A documentation-only **Milestone
1 Charter Correction Cycle 3** then produced Charter version 4, resolving
all six:

| Finding | Disposition |
|---|---|
| NF-NEW-1 | Accept — added `apps.procurement_gates.services.decide_gate_aware_change` as the sole Milestone 1 Change Request decision entry point, mirroring `request_gate_aware_change`; the existing `change_request_decide` view must be rewired to call it |
| NF-NEW-2 | Accept — defined two distinct hold-source categories (existing governance sources via a new `has_unresolved_governance_holds`, and gate-native `PackageHoldCause` rows) combined by a unified `recompute_package_hold_state` projection; corrected the `HIGH_RISK`-only inaccuracy and the "non-critical changes never touch hold state" overclaim |
| NF-NEW-3 | Accept — added a minimal `has_capability(package=None, organization=None)` extension with mutual exclusivity and exact-organization-match rules |
| NF-NEW-4 | Accept — split `compute_gate_state` (now explicitly pure) from a new two-phase `reconcile_expired_overrides` (unlocked detection, then locked reconciliation) |
| NF-NEW-5 | Accept — declared `GateAttempt` immutable and non-deletable (model-level guard, `pre_delete` guard, no admin/service path, migration-only exception), since `on_delete=PROTECT` cannot protect its generic-target `EvidenceBundle`/`EvidenceItem` references |
| Editorial | Accept — replaced brittle, commit-pinned line-number citations for `apps.governance.services` functions with stable module/function-name references |

**No application code, template, test, or migration was written or
modified during this correction cycle either.** A1–A6 remain entirely
unimplemented. Charter version 4 has **not** been independently
revalidated and has **not** been owner-approved. Milestone 1
implementation remains **not authorized**.

Exact next action: **run a new, independent Fable 5 Charter revalidation
session against the commit introducing Charter version 4. Do not begin
A1–A6 implementation.**

**Update — independent Milestone 1 Charter Version 4 Revalidation
completed, result: MILESTONE 1 CHARTER VERSION 4 REQUIRES CORRECTION.** An
independent revalidation of Charter version 4 (commit
`cf01d400e1dffd6c5981ee2ae8a01ad71f3c9006`, the commit introducing
version 4) confirmed NF-NEW-1 through NF-NEW-5 and the editorial defect
were genuinely, independently resolved against actual repository code
(`apps.governance.services`, `apps.governance.models.CapabilityGrant`,
`apps.audit.models`'s generic-target pattern) — not merely against the
Charter's own self-report — but found two new blocking findings and eight
additional accepted findings, distinct from the six findings resolved in
version 4 itself. A documentation-only **Milestone 1 Charter Correction
Cycle 4** then produced Charter version 5, resolving all ten:

| Finding | Disposition |
|---|---|
| RISKFLAG-HOLD-1 (Critical) | Accept — added `apps.procurement_gates.services.raise_gate_aware_risk_flag`/`resolve_gate_aware_risk_flag` as the sole Milestone 1 RiskFlag entry points, mirroring the `ChangeRequest` wrappers exactly, with a corrective `recompute_package_hold_state` write after the unmodified `raise_risk_flag`/`resolve_risk_flag`; removed the false claim that the unmodified foundation functions were "reachable identically" for a gate-governed package |
| NF4-A (High) | Accept — added a binding admin-immutability policy (§16.3): every procurement-gates historical model must be excluded from Django admin or exposed only through a dedicated read-only `ModelAdmin`; a blanket writable auto-registration loop (the pattern `apps/governance/admin.py` already uses) is prohibited for these models by name |
| DOC-COUNT-1 | Accept — corrected §20's stale "seven" scenario count; the true current count is nine (eight pre-existing plus LOCK-ORDER-1's new ninth scenario), reconciled across the Charter and `docs/SECURITY.md` |
| LOCK-ORDER-1 | Accept — `decide_gate_aware_change` now locks `ChangeRequest` before `ProcurementPackage`, matching the foundation's own internal order; added a global lock-order table (§14.1a) covering every Charter-defined mutation |
| NF4-C | Accept — stated the exact organization-scoped `CapabilityGrant` query, excluding hybrid grants that also carry `package` or `role_assignment` |
| NF-V4-2 | Accept — bound `decide_gate_aware_change`'s decision-time authorization to the exact existing `CHANGE_REQUEST_APPROVAL_CAPABILITY[field_name]` mapping, no second table |
| README-STALE | Accept — updated `README.md`'s Milestone 1 narrative through version 5 |
| IMPL-LOG-COUNT | Accept — corrected `docs/implementation-log.md`'s "eight Markdown files" to "nine" |
| NF-V4-5 | Accept — named `change_request_create` for required rewiring, symmetric with `change_request_decide` |
| TRACE-1 | Accept — added the historical NF-numbering disclaimer to §21; no `NF-5`/`NF-6` finding is asserted, none found in a full repository/Git-history search |

**No application code, template, test, or migration was written or
modified during this correction cycle either.** A1–A6 remain entirely
unimplemented. Charter version 5 has **not** been independently
revalidated and has **not** been owner-approved. Milestone 1
implementation remains **not authorized**.

Exact next action: **run a new, independent Fable 5 Charter revalidation
session against the commit introducing Charter version 5. Do not begin
A1–A6 implementation.**

**Update — independent Milestone 1 Charter Version 5 Revalidation
completed, result: MILESTONE 1 CHARTER VERSION 5 REQUIRES CORRECTION.** An
independent revalidation of Charter version 5 (commit
`ebcabdc582dd8ffea3ebdfce68dc55c4ee59c526`, the commit introducing
version 5) found four new blocking findings and five additional,
closely-related non-blocking cleanup items, distinct from the ten findings
resolved in version 5 itself. A documentation-only **Milestone 1 Charter
Correction Cycle 5** then produced Charter version 6, resolving all nine:

| Finding | Disposition |
|---|---|
| CR-CREATE-AUTH-GAP (Critical) | Accept — defined a new capability code, `REQUEST_PACKAGE_CHANGE`, as the sole, package-scoped authorization check for `ChangeRequest` creation, evaluated before any protected value is retrieved against a package loaded from its own persisted identity; corrected the false claim that the unmodified `apps.governance.services.request_change` performs a capability check — verified, it performs none |
| HOLD-CAUSE-CLOSURE-1 (Critical) | Accept — added `apps.procurement_gates.services.complete_package_refreeze` (§7.3) as the sole refreeze entry point, closing only the exact open `PackageHoldCause` rows a refreeze's own `source_change_requests` satisfies; defined deterministic `PackageHoldCause` identity (`package`, `cause_type`, `reference`), a partial uniqueness constraint on open rows, and idempotent closure |
| NF-V4-2-INCOMPLETE-MAPPING (Critical) | Accept — corrected the decision-capability lookup from bracket notation to the actual `CHANGE_REQUEST_APPROVAL_CAPABILITY.get(field_name, "APPROVE_ROLE_CHANGE")` semantics; documented which frozen-field codes are explicitly mapped and which resolve only through the shared fallback |
| LOCK-ORDER-1-1 (Critical) | Accept — `reconcile_expired_overrides` Phase 2 now locks the candidate `ProcurementGateOverride` before `ProcurementPackage`, matching human override approval/rejection/revocation's own binding order; removed the unsupported "never deadlocks" claim for this race |
| NF4-A-1 | Accept — added the missing `has_add_permission()==False` test and admin add-view `POST` rejection test to §16.3's required-tests list |
| PGSTATE-ADMIN-1 | Accept — explicitly exempted `PackageGateState` (a rebuildable cache) from §16.3's historical-model admin-declaration requirement, while keeping its mutations exclusively service-controlled |
| CHTR-010-COUNT-2 | Accept — corrected §21.1's own CHTR-010 disposition row from "eight" to "nine" named PostgreSQL scenarios, a count version 5's DOC-COUNT-1 fix had updated everywhere else but missed in this one historical row |
| HOLD-WORDING-1 | Accept — removed §8.1 step 1's stale "written exclusively" claim about `is_on_hold`, which contradicted §8.4's unified, always-recomputed projection rule |
| NF-1-SUMMARY-1 | Accept — corrected §21.3's historical NF-1 summary to state the unified hold projection covers both governance-side and gate-native sources, not `PackageHoldCause` rows alone |

**No application code, template, test, or migration was written or
modified during this correction cycle either.** A1–A6 remain entirely
unimplemented.

**Update — independent Milestone 1 Charter Version 6 Revalidation
completed, result: MILESTONE 1 CHARTER VERSION 6 APPROVED FOR OWNER
ACCEPTANCE.** An independent, delta-only revalidation of Charter version 6
(commit `e9b5cb31eedcea219ac35c906dfbf65e5ff4d2de`, the same commit
introducing version 6) confirmed genuine resolution of all nine
Version 5→6 items (CR-CREATE-AUTH-GAP, HOLD-CAUSE-CLOSURE-1,
NF-V4-2-INCOMPLETE-MAPPING, LOCK-ORDER-1-1, NF4-A-1, PGSTATE-ADMIN-1,
CHTR-010-COUNT-2, HOLD-WORDING-1, NF-1-SUMMARY-1) against actual
repository state — HEAD matched the commit exactly, upstream identical,
ahead/behind 0/0, working tree clean, nine Markdown files changed with no
Python/template/test/migration touched, 72/72 migrations applied, no
`apps.procurement_gates` implementation present — and found no Critical
or High contradiction. This is the first clean pass in this Charter's
revalidation history.

Following this result, Harrison (owner) explicitly accepted Milestone 1
Procurement Gates Charter Version 6, at commit
`e9b5cb31eedcea219ac35c906dfbf65e5ff4d2de`, as the approved architectural
and functional contract for Milestone 1, on 2026-07-21. **This acceptance
does not authorize implementation of A1–A6**; implementation authorization
remains a separate, explicit owner decision, not yet given. PostgreSQL
and live validation remain outstanding future implementation-closure
requirements.

Exact next action: **await Harrison's separate, explicit authorization to
begin Milestone 1 (A1–A6) implementation. Do not begin A1–A6
implementation absent that separate authorization.**

**Update — Milestone 1 Implementation Increment 1 complete (2026-07-21).**
Harrison separately, explicitly authorized a bounded first implementation
increment: **Procurement Gate Policy and Package Assignment Foundation**
— the new `apps.procurement_gates` app (`GatePolicy`, `GatePolicyVersion`,
`PackagePolicyAssignment`), covering Charter §§1–4 (gate codes, policy
architecture, publication rule, canonical default, package pinning,
existing-package migration) and the corresponding slices of §10
(audit taxonomy) and §13 (authorization). Explicitly excluded from this
increment, per its own boundary: `GateAttempt`, `GateEvaluation`,
`GateDecision`, `GateInvalidation`, `PackageGateState`,
`PackageFreezeRevision`, `PackageHoldCause`, `ProcurementGateOverride`,
evidence-bundle creation/mapping, gate evaluation, A1 completion, A2
freeze, A3–A6 behavior, `ChangeRequest`/`RiskFlag` wrappers, refreeze,
holds, override lifecycle, any procurement-gates UI/API, and any change to
`apps.workflow`.

Starting reconciliation for this cycle: HEAD confirmed at `1e1b247` (short
SHA) before editing; branch `integration/dt-beach-supply-control-1.0.0`;
upstream identical; ahead/behind `0/0`; working tree clean; 72/72
migrations applied, 0 pending; no `apps.procurement_gates` implementation
present. Result: 54 new tests added (`tests/test_procurement_gates_policy.py`),
full regression suite **565/565 passing** (511 prior + 54 new);
`python manage.py check`, `makemigrations --check --dry-run`, and
`migrate --check` all clean; `showmigrations --plan` shows 75/75 applied,
0 pending (72 prior + `procurement_gates` 0001/0002 + `audit`'s new
`Action`-choices migration). Verified against both a fresh empty SQLite
database (seeds exactly one canonical policy + published version, zero
package assignments) and the real populated development database (one
existing `frozen` package, pinned exactly once, `pinned_by = NULL`,
`is_frozen`/`frozen_snapshot`/`is_on_hold`/`Status` all unchanged), with
re-run idempotency confirmed. PostgreSQL concurrency validation was not
run this cycle — recorded as pending, not as validated. Full detail:
`docs/implementation-log.md` entry 63, `docs/architecture-decisions.md`
ADR-049.

**Historical Increment 1 boundary:** A1–A6 gate *execution* was entirely
unimplemented at Increment 1 completion. No
`GateAttempt` or any row downstream of it existed anywhere in the
codebase at that point. Increment 2 was **not authorized** by that entry.

Exact next action: **run an independent, increment-only Fable review
against the Increment 1 commit. Do not begin Increment 2 until that
review is reconciled and Harrison explicitly authorizes the next
increment.**

**Update — Increment 1 Codex correction implemented (2026-07-21).** Codex
implementation verification of commit `9b803911` found CX-I1-001 through
CX-I1-010. The authorized bounded correction closes CX-I1-001 through
CX-I1-009 and reconciles CX-I1-010 without adding gate execution: permanent
assignment cardinality/immutability; policy-version lifecycle immutability;
canonical database and shared-lock protection; registered decision
capabilities; tenant-safe authorized assignment via `ASSIGN_GATE_POLICY`;
fail-closed organization-policy ambiguity; frozen historical RunPython logic;
dedicated `EXEMPT_PACKAGE_FROM_PROCUREMENT_GATES`; and identifier-only audit
metadata. Additive migrations `procurement_gates.0003` and `.0004` enforce
database invariants and route the Django base managers through the protected
querysets. The
correction commit and final accepted Increment 1 baseline are
`220be7aa030b656fb960151c92166594ba539a26`.

**Owner acceptance — Increment 1 closed (2026-07-21).** Codex re-verification
returned **MILESTONE 1 IMPLEMENTATION INCREMENT 1 CODEX RE-VERIFIED — READY
FOR HARRISON RECONCILIATION**, finding no remaining Critical, High, or
blocking Medium Increment 1 defect. Harrison explicitly accepted MarketMatch
Milestone 1 Implementation Increment 1 — Procurement Gate Policy and Package
Assignment Foundation at `220be7aa030b656fb960151c92166594ba539a26`.
Acceptance evidence is 613 passed, 2 PostgreSQL-only concurrency tests skipped
on SQLite, 77/77 migrations applied, 0 pending migrations, and a clean working
tree. Real PostgreSQL concurrency execution remains pending and is not claimed
as validated. Increment 1 is closed and owner-accepted. Increment 2 remains
unauthorized and requires a separate explicit owner decision.

Exact next action: **await Harrison's separate explicit authorization for
Increment 2.**

**Update — Milestone 1 Implementation Increment 2 complete locally
(2026-07-21).** Harrison separately and explicitly authorized **Gate Execution
Core and A1 Deal Established** from synchronized baseline
`193fdfb720662a235b259b97694a0d5e3d8edcaa`. Increment 1 remains closed and
owner-accepted.

The bounded implementation adds immutable/non-deletable `GateAttempt`,
`GateEvaluation`, and `GateDecision` history and rebuildable-only
`PackageGateState`; organization-authorized/idempotent package initialization;
package-authorized A1 evaluation and review; separated, capability-controlled
A1 pass/return; pure state computation; and A2 becoming current as
`NOT_STARTED` after A1 passes. It adds `audit.0005` and
`procurement_gates.0005`. Existing packages receive no fabricated attempt,
evaluation, decision, pass, or projection; initialization remains explicit.
`ProcurementPackage.Status`, freeze/hold fields, ChangeRequests, RiskFlags,
evidence, and workflow Handoffs remain independent and unchanged.

Explicitly absent: A2 evaluation/freeze, `PackageFreezeRevision`, A3–A6
execution, `GateInvalidation`, `PackageHoldCause`,
`ProcurementGateOverride`, evidence mapping, UI/API/forms/templates, and every
Increment 3 behavior. `apps.workflow` code and migrations are unchanged.

Fresh SQLite evidence before commit: **655 collected, 649 passed, 6 skipped**;
the six skips are PostgreSQL-only concurrency tests (assignment, canonical
withdrawal, simultaneous initialization, simultaneous re-attempt creation,
simultaneous A1 decision, and duplicate A2 projection activation). **79/79
migrations applied, 0 pending.** Real PostgreSQL concurrency execution remains
pending and is not claimed as validated.

Exact next action: **run a read-only, Increment-2-only Codex verification
against the resulting commit. Do not begin Increment 3 until that verification
is reconciled and Harrison separately authorizes it.**

**Update — Increment 2 testable A1 browser vertical slice complete
(2026-07-21).** Starting from the synchronized Increment 2 backend commit
`d2aacafe91edf5b680f4071e72a7291bdb95343f`, the existing package-detail
experience now displays safe policy/version identifiers, canonical A1–A6
state, current gate, exemption, current evaluation, stable requirement/blocker
codes, review state, and immutable attempt/decision history. Authorized POST
views expose Initialize, Evaluate A1, Request Review, Approve, Return, and
post-return New Attempt actions exclusively through the accepted Increment 2
services. `VIEW_PROCUREMENT_GATE_STATE` is an explicit package-scoped
capability with no role-default implication.

A DEBUG-only synthetic seed command supports a repeatable browser scenario by
explicit package code and refuses non-DEBUG execution. The completed Chrome
walkthrough ran at
`http://127.0.0.1:8012/compras/paquetes/ae92206f-d343-4964-869e-c2825d93b149/`:
Initialize → Evaluate → Request Review → self-approval denied → Return → New
Attempt → Evaluate → Request Review → approval by a separate actor. It ended
with A1 `PASSED`, A2 current but non-executable, package status/freeze/hold and
Handoff unchanged, and all confidential sentinels absent. A fresh Harrison
scenario is available after local server startup at
`http://127.0.0.1:8000/compras/paquetes/b8ee6165-bf55-415f-8333-7318eb54d8ee/`.

Fresh SQLite evidence: **663 collected, 657 passed, 6 PostgreSQL-only tests
skipped; 79/79 migrations applied, 0 pending**. Real PostgreSQL concurrency
execution remains pending and is not claimed. A2 evaluation/freeze and every
later-gate behavior remain unimplemented; Increment 3 remains unauthorized.
The UI completion baseline is this documentation/application commit,
`Complete testable A1 browser vertical slice`.

Exact next action: **Harrison browser-tests the fresh synthetic A1 scenario.
Do not begin Increment 3.**

**Owner acceptance — Increment 2 closed (2026-07-21,
America/Santo_Domingo).** Codex verification of implementation and browser
vertical-slice commit `acd2becce64e99c2dac559ccf805b1c64344debd`
returned **MILESTONE 1 IMPLEMENTATION INCREMENT 2 CODEX VERIFIED — READY FOR
HARRISON ACCEPTANCE**, with no remaining Critical or High Increment 2 defect.
Harrison then explicitly accepted **MarketMatch Milestone 1 Implementation
Increment 2 — Gate Execution Core and A1 Deal Established** at that commit.

Accepted evidence is **657 tests passed, 6 PostgreSQL-only tests skipped on
the SQLite validation engine; 79/79 migrations applied, 0 pending; and a clean
working tree**. Harrison completed the real browser walkthrough:
initialization, evaluation, review request, self-approval denial, return,
re-attempt, second evaluation, review request, separate approval, A1 `PASSED`,
and A2 current/non-executable. Real PostgreSQL concurrency execution remains
pending and this acceptance does not claim PostgreSQL validation. Increment 2
is closed and owner-accepted. Increment 3 remains absent and unauthorized.

**MILESTONE 1 IMPLEMENTATION INCREMENT 2 CLOSED AND OWNER-ACCEPTED —
INCREMENT 3 NOT AUTHORIZED.**

Exact next action: **await Harrison's separate explicit authorization to begin
Increment 3.**

---

## Historical record: Foundation correction cycle 3 mechanism

Foundation correction cycle 3 corrected three independently-discovered and
reproduced gaps in the cycle-2 privileged-audit mechanism
(CTCF-AUDIT-SCOPE-021, CTCF-AUDIT-RETRIEVAL-022, CTCF-AUDIT-WINDOW-023).
This correction has since been independently revalidated (see acceptance
above).

Cycle 3 began from clean, synchronized HEAD
`84b12a2187d93f2ccd9992780a5a4b73e54e7cc6` on
`integration/dt-beach-supply-control-1.0.0` — the foundation correction
cycle 2 commit. An independent Fable revalidation of that commit confirmed
CTCF-AUDIT-017 and CTCF-CR-PROJ-018 (cycle 2's own findings) were closed,
but reproduced, with direct evidence, three further gaps in the
privileged-audit mechanism cycle 2 introduced: a `CapabilityGrant` whose
scope is inherited only through `role_assignment` (no direct
package/organization on the grant row) resolved to no scope at all,
hiding its audit event from an otherwise-authorized viewer
(CTCF-AUDIT-SCOPE-021); the candidate scan selected
`AuditEvent.summary`/`.metadata` — columns the safe projection never uses
— before the per-row authorization decision (CTCF-AUDIT-RETRIEVAL-022);
and the scan's 1,000-event ceiling could silently omit an older
authorized event behind enough newer, unrelated ones
(CTCF-AUDIT-WINDOW-023). None was a confirmed browser-facing disclosure —
all three are read-path completeness/retrieval-hygiene corrections, now
closed. See ADR-043 and `docs/SECURITY.md` for the mechanism. No migration
was required. `procurement.services.revoke_verification_assertion` remains
intentionally service-only, with no HTTP route added
(CTCF-ASSERT-HTTP-019, deferred by explicit decision, not by oversight,
unchanged by this cycle).

Local validation at the time this cycle was implemented (since superseded
by the independent revalidation evidence recorded in the acceptance
section above):

| Check | Command | Result |
|---|---|---|
| `manage.py check` | `python manage.py check` | Passed; no issues |
| `makemigrations --check --dry-run` | `python manage.py makemigrations --check --dry-run` | Passed; no model changes detected |
| `migrate --check` | `python manage.py migrate --check` | Passed; no unapplied migrations |
| Focused foundation/remediation suite | `pytest tests/test_foundation_remediation.py tests/test_procurement_confidentiality.py tests/test_confidentiality_http.py tests/test_evidence_and_disclosure.py -v` | **53 passed, 0 failed** (49.76s, SQLite) |
| Privileged-audit + Change Request correction suites | `pytest tests/test_privileged_audit_scope.py tests/test_change_request_projection.py -v` | **39 passed, 0 failed** (46.15s, SQLite — 31 privileged-audit + 8 Change Request) |
| Full regression suite | `pytest tests/` | **511 passed, 0 failed** (113.25s, SQLite — 496 prior + 15 new in `tests/test_privileged_audit_scope.py`) |

The **53** focused-suite figure remains the exact, reproducible result of
the command listed above against the four files that constitute that
suite. PostgreSQL was not available in the environment this cycle ran in
(no Docker daemon); this is recorded as a limitation, not claimed as
PostgreSQL-validated.

The new integration tests use same-organization, different-organization,
cross-package, and cross-organization-audit-scope adversaries; unique
sentinels; temporary document storage; and server-side HTML/API/context/
count/direct-object assertions. No raw EvidenceBundle/EvidenceItem
projection was added. `VisibilityMode` remains versioned policy metadata:
neither mode bypasses capability/classification rules; a
controlled-transparency agreement becomes executable through an active,
field-scoped Disclosure Grant.

That independent revalidation was subsequently performed (against this same
commit, `2c52b0b`) and, together with Harrison's explicit acceptance
recorded above, closed this correction cycle and the foundation as a
whole. See "Foundation status: CLOSED AND OWNER-ACCEPTED" at the top of
this document for the current, authoritative next action:

**Run the independent Milestone 1 Charter review against the current
documentation baseline. Do not begin A1–A6 implementation.**

## Gate 0 status

**Gate 0 — Baseline & Documentation Reconciliation is complete.**

This document records fresh terminal evidence collected before the Gate 0
documentation-only commit. The governing roadmap is
`docs/MARKETMATCH_ARCHITECTURE_RECONCILIATION_AND_ROADMAP.md`.

No application behavior, migrations, models, templates, or tests were changed
during Gate 0.

## Repository reconciliation report

| Item | Fresh verified evidence |
|---|---|
| Local repository | `/Users/harrison/Downloads/DT_Beach_Supply_Control_Fable5:/Application` |
| Remote repository | `https://github.com/Harrison0407/odysseus.git` |
| Branch | `integration/dt-beach-supply-control-1.0.0` |
| Verified pre-reconciliation HEAD | `5cd0df64edc5baf89a0e3e4e3efe8e1fc78d0b2c` |
| Upstream | `origin/integration/dt-beach-supply-control-1.0.0` |
| Ahead / behind after fetch | `0 / 0` |
| Initial working tree | Clean; no tracked changes and no untracked files |
| Push status at initial HEAD | `Everything up-to-date` (push dry-run) |
| Gate 0 final HEAD | The single documentation-only Gate 0 commit containing this revision; obtain its immutable hash with `git rev-parse HEAD` |

The earlier application-code baseline remains:

- Commit: `58889c8`
- Title: `Document the Controlled Transparency / Confidentiality foundation (part 7/7)`
- Meaning: latest application-behavior commit before the later documentation-only
  commits `8b7e102`, `5cd0df6`, and this Gate 0 reconciliation.

## Validation report

Fresh validation was run in an isolated Python 3.13.5 environment using the
pinned development requirements.

| Check | Result |
|---|---|
| `manage.py check` | Passed; no issues |
| `makemigrations --check --dry-run` | Passed; no model changes detected |
| `migrate --check` | Passed; no unapplied migrations |
| `showmigrations --plan` | Every listed migration applied |
| Full regression suite | **455 passed, 0 failed** |
| Regression duration | 71.78 seconds |

Migration status: **clean**.

Regression status: **passing**.

## Milestone status

Latest completed product milestone:

**Controlled Transparency, Commercial Confidentiality & Authorization Foundation**

Current non-product milestone:

**Gate 0 — Baseline & Documentation Reconciliation — complete**

Gate 0's historical handoff named Milestone 1 as the next implementation
milestone. Fresh post-Gate-0 validation introduced a required foundation
remediation and independent-revalidation checkpoint before that milestone.
No A1–A6 functionality has been implemented.

## Documentation discrepancies reconciled

- The recorded repository HEAD was `8b7e102`; fresh evidence established
  `5cd0df6` as the pre-Gate-0 HEAD.
- Evidence-package documents previously described as untracked were already
  tracked in `5cd0df6`.
- The README's historical `20`-app and `246`-test counts were stale; the
  repository contains 27 Django app directories and the fresh suite collects
  and passes 455 tests.
- Requirements traceability still described the receiving manifest and
  landed-cost calculation UI as planned/modeled even though later committed
  milestones implemented and tested them.
- The next approved milestone was not stated consistently across the current
  documentation; it is now explicitly A1–A6, without claiming implementation.

## Evidence-package disposition report

No evidence was deleted, ignored, relocated, or archived during Gate 0.

| Filename | Disposition | Justification |
|---|---|---|
| `DT_BEACH_CURRENT_STATE.md` | Commit | Canonical current repository and milestone handoff record; reconciled by Gate 0 |
| `DT_BEACH_SOURCE_OF_TRUTH_INDEX.md` | Commit | Canonical hierarchy and evidence-resolution index; reconciled by Gate 0 |
| `Fable Interactive Plans Final Report 90874ed.md` | Commit | Historical milestone evidence explicitly labeled as reconstructed and tied to immutable commit `90874ed`; already committed in `5cd0df6` |
| `Fable Property Master Final Report 69f89a3.md` | Commit | Historical release evidence tied to immutable commit `69f89a3`; already committed in `5cd0df6` |
| `Git Evidence 90874ed.txt` | Commit | Intentionally historical Git snapshot that explicitly says it must not be updated to later counts; already committed in `5cd0df6` |
| `docs/MARKETMATCH_ARCHITECTURE_RECONCILIATION_AND_ROADMAP.md` | Commit | Approved governing architecture reconciliation and official milestone order supplied for Gate 0 |

## Current unresolved limitations

These do not block Gate 0, but remain product, evidence, integration, or
operations work governed by the roadmap:

- Configurable Procurement Gates A1–A6 are not implemented; they are Milestone 1.
- ARENA T1, MARE B, SOLE, SOLE PH, and SOLE 26 remain `Missing Source` for
  reliable individual apartment plans.
- OCR and automated translation engines are not wired; they are deferred to an
  authorized adapter milestone after the i18n foundation.
- QuickBooks live integration is not built; QuickBooks remains the financial
  source of truth.
- The exposed package-associated PurchaseOrder and ManifestLine endpoints are
  now package/classification scoped. Repository-wide API convergence and
  authorized commercial search/autocomplete, exports, reports, QR projections,
  and notifications remain future integration work.
- Audit immutability at the database/operations layer and repository-wide
  authorization coverage still need direct evidence before universal claims.
- Shared/global rate limiting, dependency/secret scanning, backup encryption,
  production observability, and CI hardening remain future work.
- Mobile responsiveness has structural test coverage but not pixel-level,
  multi-device visual validation.
- The exact original section numbering of the reconstructed receiving manifest
  has not been independently revalidated against the unavailable original
  specification text.
- Historical source-data ambiguities listed in `docs/DATA_QUALITY_AND_UNCERTAINTY.md`
  remain deliberately unresolved rather than guessed.

See `docs/KNOWN_LIMITATIONS.md` for the detailed historical and current record.

## Architectural rules carried forward

- MarketMatch is the platform name.
- PostgreSQL is the production source of truth.
- QuickBooks and approved accounting records remain the financial source of truth.
- Party, role, capability, and authorization scope remain separate.

## MarketMatch procurement prototype (authorized, non-production)

The DEBUG-only synthetic product prototype at `/prototype/procurement/` is a
team-review artifact, not Increment 3 production behavior. It has no database
models or production aggregate integrations. Details: `docs/MARKETMATCH_PROCUREMENT_PROTOTYPE.md`.
Closure verification: SQLite only; 79 migrations applied, 0 pending; 669
pytest tests collected, 663 passed, 6 PostgreSQL-only skipped, exit 0. HTTP
routes and synthetic exports passed. Browser visual validation is pending an
owner walkthrough; supplier CSV import validation is not implemented.
- Authorization occurs before retrieval, projection, transformation, export,
  search, AI processing, or derived-artifact creation.
- Commercial source layers remain structurally separate.
- Building Family and Physical Building remain distinct.
- Missing source information and room layouts must never be invented.
- Completed foundations are extended through existing service layers and ADR
  patterns, not rebuilt.

## Localization foundation

Canonical interface locales:

- `es` — Español
- `en` — English
- `zh-Hans` — 简体中文

Language and timezone remain separate settings.

DT Beach operational timezone: `America/Santo_Domingo`.
