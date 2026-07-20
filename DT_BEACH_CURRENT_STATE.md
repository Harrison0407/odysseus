# DT Beach Supply Control — Current State

Last updated: 2026-07-20

## Foundation status: CLOSED AND OWNER-ACCEPTED

**The Controlled Transparency, Commercial Confidentiality & Authorization
Foundation is closed and owner-accepted. Foundation correction cycles 2
and 3 have both been independently revalidated. Milestone 1 implementation
has not begun.**

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
during this cycle.** A1–A6 remain entirely unimplemented. This Charter has
**not** been independently revalidated and has **not** been owner-approved.
Milestone 1 implementation remains **not authorized**.

Exact next action: **run an independent Fable 5 Charter revalidation
session against the commit introducing
`docs/MILESTONE_1_PROCUREMENT_GATES_CHARTER.md`. Do not begin A1–A6
implementation.**

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
