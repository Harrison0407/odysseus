# Architecture Decision Log

Newest first.

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
