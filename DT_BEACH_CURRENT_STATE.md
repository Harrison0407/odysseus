# DT Beach Supply Control — Current State

Last updated: 2026-07-19

## Foundation remediation status

**Foundation correction cycle 2 is complete. Two independently-discovered
gaps (CTCF-AUDIT-017, CTCF-CR-PROJ-018) have been corrected and a
documentation count discrepancy (CTCF-DOC-020) has been fixed. This
correction has not yet been independently revalidated. Milestone 1 has not
begun.**

Fresh remediation evidence began from clean, synchronized HEAD
`a89a9f714684515be1b2de704bf816611e094540` on
`integration/dt-beach-supply-control-1.0.0` — the commit an independent
Fable revalidation found closed all 14 non-deferred originally-accepted
findings, but also found two new gaps outside that original set:
privileged audit access was gated only by `can_override_gates` with no
organization/package scope applied to the underlying `AuditEvent`
queryset (CTCF-AUDIT-017), and basic package participation exposed a
Change Request's raw values regardless of decision authority
(CTCF-CR-PROJ-018). Both are now corrected — see ADR-042 and
`docs/SECURITY.md` for the mechanism. Neither required a migration.
`procurement.services.revoke_verification_assertion` remains intentionally
service-only, with no HTTP route added (CTCF-ASSERT-HTTP-019, deferred by
explicit decision, not by oversight).

Validation after this correction cycle (local, not yet independently
revalidated):

| Check | Command | Result |
|---|---|---|
| `manage.py check` | `python manage.py check` | Passed; no issues |
| `makemigrations --check --dry-run` | `python manage.py makemigrations --check --dry-run` | Passed; no model changes detected |
| `migrate --check` | `python manage.py migrate --check` | Passed; no unapplied migrations |
| Focused foundation/remediation suite | `pytest tests/test_foundation_remediation.py tests/test_procurement_confidentiality.py tests/test_confidentiality_http.py tests/test_evidence_and_disclosure.py -v` | **53 passed, 0 failed** (48.93s, SQLite) |
| Full regression suite | `pytest tests/` | **496 passed, 0 failed** (111.11s, SQLite — 472 prior + 24 new: `tests/test_privileged_audit_scope.py`, `tests/test_change_request_projection.py`) |

The **53** focused-suite figure is the exact, reproducible result of the
command listed above against the four files that constitute that suite —
a prior, unreproducible "78" figure has been corrected (CTCF-DOC-020).
PostgreSQL was not available in the environment this cycle ran in (no
Docker daemon); this is recorded as a limitation, not claimed as
PostgreSQL-validated.

The new integration tests use same-organization, different-organization,
cross-package, and cross-organization-audit-scope adversaries; unique
sentinels; temporary document storage; and server-side HTML/API/context/
count/direct-object assertions. No raw EvidenceBundle/EvidenceItem
projection was added. `VisibilityMode` remains versioned policy metadata:
neither mode bypasses capability/classification rules; a
controlled-transparency agreement becomes executable through an active,
field-scoped Disclosure Grant.

Exact next action:

**Run a new, independent Fable 5 revalidation session against the
resulting commit. Do not begin Milestone 1 during that revalidation.**

Only a successful independent revalidation may establish readiness to begin
**Milestone 1 — Configurable Procurement Gates A1–A6**.

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
