# Security

## Foundation status (2026-07-20)

The Controlled Transparency, Commercial Confidentiality & Authorization
Foundation's security controls (including foundation correction cycles 2
and 3 below) were independently revalidated at commit
`2c52b0b83340fda2eaa84700eaeddfbe0839d6d8`, and Harrison recorded explicit
owner and business acceptance of the foundation on that basis — see
`DT_BEACH_CURRENT_STATE.md` for the full acceptance text. This acceptance
covers the application-level authorization/confidentiality controls
described below; it does **not** cover, and does not represent as
complete, PostgreSQL runtime validation (every cycle was validated against
SQLite only), database-level audit append-only enforcement, backup
restoration, deployed proxy/cache validation, or production log-sentinel
analysis — see `docs/KNOWN_LIMITATIONS.md` for the full, current list of
open operational and security validations.

## Milestone 1 procurement-gate security design (documentation-only, not yet implemented)

`docs/MILESTONE_1_PROCUREMENT_GATES_CHARTER.md` defines, but does not
implement, the authorization, confidentiality, audit, and non-overridable
control rules for Procurement Gates A1–A6:

- Authorization before retrieval, package/organization scoping, and
  same-organization/superuser insufficiency rules apply identically to the
  new gate domain (Charter §13) — no new authorization philosophy is
  introduced.
- Gate evidence classification is derived deterministically from package
  visibility mode and evidence-requirement type, never defaulting to a
  client-shared classification (Charter §6); raw evidence is never exposed
  merely because a `GateDecision`/`VerificationAssertion` references it.
- A new `ProcurementGateOverride` model (Charter §11, ADR-044) is bound by
  an explicit, absolute list of non-overridable controls (Charter §12):
  authorization, package/organization scope, classification, separation of
  duties, predecessor-gate validity by default, confidentiality, minimum
  audit requirements, written reason, and finite expiry can never be
  bypassed by any override, regardless of capability.
- New `AuditEvent.Action` codes are defined for every gate/policy/override/
  freeze lifecycle transition (Charter §10), with an explicit rule against
  ever placing raw evidence content or confidential field values in audit
  `metadata`.
- PostgreSQL-specific lock/race validation is required before Milestone 1
  may be declared closed for six named concurrency scenarios (Charter §15)
  — SQLite evidence alone is explicitly disallowed from being represented
  as PostgreSQL validation.

**Correction cycle update (2026-07-20):** an independent revalidation of
Charter version 1 found, and Charter version 2 corrected, two
security-relevant gaps: (1) the post-A2 critical-change cascade now also
revokes every currently-active `ProcurementGateOverride` on the affected
package's A3–A6 attempts, so an override granted under stale technical
terms can never silently survive a critical change (Charter §8.1 step 4);
(2) a package's very first `A1` gate attempt is authorized through an
explicit organization-scoped capability rather than an impossible
package-scoped one, since no package-scoped role can exist before `A1`
creates it (Charter §13). Both remain design-only — neither has been
implemented, and Charter version 2 has not itself been independently
revalidated.

None of this has been implemented. A1–A6 do not exist in the codebase as of
this entry; this section documents the approved design only.

## Implemented and verified in this delivery

- **Authentication:** Django session auth; no custom auth backend risk surface.
- **CSRF:** `CsrfViewMiddleware` enabled; all POST forms include `{% csrf_token %}`.
- **Session cookies:** `HttpOnly`, `SameSite=Lax`; `Secure` forced when `DEBUG=False`.
- **Object/organization-level authorization:** every list/detail view filters
  by `request.user.profile.organization` (or the equivalent FK chain);
  verified by `tests/test_permissions.py` (cross-organization access
  returns 404, not the other org's data) and live in the production
  stack (unauthenticated request to `/documentos/` returns 302 to login).
- **File upload validation:** extension allowlist and size limit enforced
  in `DocumentUploadForm.clean_file`; rejected uploads never reach storage.
- **SHA-256 on every document version**, computed while streaming to
  disk (`apps.core.storage.DocumentStorage.save`), with duplicate
  detection surfaced as a warning, never a silent auto-merge.
- **No public PostgreSQL port:** verified directly — in
  `deploy/docker-compose.prod.yml`, `db` and `web` are on an
  `internal: true` Docker network; only `caddy` bridges to the `external`
  network and only Caddy's 80/443 are published to the host.
- **Environment-based secrets:** `DJANGO_SECRET_KEY`, DB credentials, etc.
  are read from environment/`.env.production` only; `.dockerignore`
  prevents any local `.env` from being baked into the production image
  (see `architecture-decisions.md` ADR-012 for the real bug this
  prevents).
- **Security headers:** `SECURE_CONTENT_TYPE_NOSNIFF`, `X_FRAME_OPTIONS = DENY`,
  and (in production) `SECURE_HSTS_*`, `SECURE_SSL_REDIRECT` (configurable —
  must be disabled only during a temporary IP-only bring-up before a
  domain/TLS cert exists, see `DEPLOYMENT.md`).
- **Audit trail:** `apps.audit.AuditEvent` is written for document
  upload, receipt posting, and other material actions; append-only by
  convention (no update/delete path in the application code).
- **Revocable share links:** `SecureShareLink.token` is a
  `secrets.token_urlsafe(32)` random value (not sequential), with
  `expires_at`/`revoked_at` and an access log (`ShareSnapshot`, records
  IP + timestamp per view).

## Delivery/Installation/Inspection/Final Acceptance milestone

- **Cross-project isolation extended to the new screens.**
  `apps.workflow.services.can_view_target` (generalized from the
  existing `can_view_handoff`) is enforced at the top of every
  delivery/installation/inspection detail and action view
  (`apps.requests.views._deny_cross_project`), and every list view is
  additionally scoped at the queryset level
  (`_scope_to_accessible_projects`) so a user without `UserProjectAccess`
  to a project never sees the row at all, not just gets denied on
  click-through. Verified by
  `test_24_cross_project_isolation_denies_direct_url_access_to_installation`
  and `test_24b_..._denies_direct_url_progress_post`, and live: a direct
  URL hit by a user with access only to a different project returns 302,
  and the record is absent from that user's list view.
- **Quantity-guard overrides require the same permission as gate
  overrides, enforced server-side.** `record_installation_progress`
  checks `apps.workflow.services.can_override_gates(user)` before
  allowing an installed quantity above the validly delivered amount,
  regardless of whether an `override_reason` was supplied — an
  unauthorized user's override attempt raises `QuantityInvariantError`
  and nothing is written. Verified live during this milestone's
  walkthrough: a direct POST to `/flujo/<id>/anular-enviar/` by a user
  without `can_override_gates` was denied with the exact same message
  the service layer raises, not merely hidden in the template (the
  override form itself is also conditionally hidden client-side, but the
  enforcement is server-side and was proven by bypassing the UI).
- **Final-acceptance detail cannot be recorded before the underlying
  handoff is genuinely accepted.** `installation_final_accept` requires
  the `inspection_to_acceptance` `Handoff` to already be `ACCEPTED`
  (via the unchanged, existing `accept_handoff` authorization checks)
  before accepting any POST — see ADR-021.
- **Duplicate-submission protection added at the service layer**, not
  only via UI disable-on-submit: `create_installation_record` and
  `create_project_receipt` are idempotent; `record_final_acceptance` and
  `close_punch_list_item` raise a clear, caught error on a second
  identical call rather than creating a second row or silently
  reprocessing.
- **Evidence upload reuses the existing, tested upload validation** —
  `apps.audit.services.attach_evidence` calls the same
  `DocumentUploadForm.clean_file` (extension allowlist, size limit) and
  SHA-256/duplicate-detection path as `/documentos/subir/`; the upload
  action itself is gated by `_deny_cross_project` on the parent
  Delivery/InstallationRecord/InspectionRecord. Verified live: a real
  multipart upload followed by a byte-identical download, and by test
  (`TestEvidenceUpload::test_cross_project_isolation_denies_evidence_upload`)
  that a user without project access cannot create an attachment via a
  direct POST.

## Verified by direct testing in this session

- Unauthorized document download → 404 (not the file), confirmed by
  `test_unauthorized_user_cannot_download_another_orgs_document`.
- Cross-organization purchase-order detail access → 404, confirmed by
  `test_purchase_order_detail_denies_cross_organization_access`.
- Anonymous dashboard access → 302 redirect to login, confirmed by
  `test_anonymous_user_redirected_to_login` and live via `curl` against
  the running production stack.
- No public Postgres port, confirmed by inspecting the actual running
  `docker compose ps` output during production validation (`db` shows no
  host port mapping; only `caddy` does).

## Rate limiting (added in a later session)

- **Login:** `apps.accounts.views.RateLimitedLoginView` blocks further
  attempts after 10 failed logins from the same IP within 5 minutes —
  even a correct password is rejected while blocked (verified by
  `tests/test_rate_limiting.py::test_login_blocked_after_max_failed_attempts`,
  which explicitly checks this, not just that the generic error
  repeats). Scoped per IP, not globally — a different IP is unaffected
  (`test_login_not_blocked_for_a_different_ip`).
- **Public share links:** `apps.reports.views.shared_view` (fully
  unauthenticated, reachable by anyone with a token) returns HTTP 429
  after 30 requests/minute from the same IP, checked *before* the token
  is even looked up in the database
  (`test_share_view_rate_limited_after_max_requests`).
- Backed by `apps.core.ratelimit`, a small fixed-window counter on
  Django's cache framework — no Redis/Celery dependency added. Known
  limitation: the default `LocMemCache` backend is per-process, so this
  enforces the limit per Gunicorn worker, not globally across the whole
  server — acceptable at this pilot's scale, recorded honestly in
  `KNOWN_LIMITATIONS.md` rather than overstated.

## Storage suitability, external storage comparison, supplier claims, QR labels (added in a later session)

- **Storage suitability overrides are server-side authorized, not
  merely UI-hidden.** `apps.inventory.services.enforce_location_suitability`
  requires `apps.workflow.services.can_override_gates(user)` before
  accepting an `override_reason` for a blocked put-away/transfer;
  supplying a reason without that permission raises
  `StoragePermissionError` rather than silently succeeding. Every
  override and every non-blocking warning is logged to `AuditEvent`.
  Verified by `tests/test_storage_suitability.py`
  (`test_unauthorized_override_denied`).
- **External storage comparison never fabricates a currency
  conversion.** `apps.receiving.services.compare_scenario_options`
  reuses `apps.cost.services.convert_to_base_currency`; an option in a
  currency with no `ExchangeRate` on file is reported as
  `converted_total=None` with an explanation, never assumed 1:1.
  Scenarios are org/project-scoped via their `ReceivingPlan`; a
  `FINALIZED` scenario's options cannot be edited by any path.
- **Supplier claims require evidence before approval** (a conservative
  operational rule — see `ASSUMPTIONS.md` A27) and every lifecycle
  transition is guarded against running out of order or twice —
  `submit_claim` on an already-`SUBMITTED` claim raises rather than
  reposting. Verified by `tests/test_supplier_claims.py`
  (`test_cannot_submit_twice`, `test_duplicate_submission_prevented_via_http`).
- **QR label payloads are opaque and never expose the underlying
  record.** `QRLabel.token` is a `secrets.token_urlsafe(24)` random
  value (same family as `SecureShareLink.token`); the QR image encodes
  only `/qr/<token>/`. `qr_scan_landing` is `login_required` — an
  unauthenticated scan is redirected to log in before the label is
  ever resolved — and re-checks organization membership before
  forwarding to the entity's own existing, already-permission-checked
  detail page; a cross-organization scan attempt is logged
  (`QRScanEvent.was_cross_organization_denied`) rather than silently
  allowed or silently dropped. A scan never performs a consequential
  action itself. Verified by `tests/test_qr_labels.py`
  (`test_cross_organization_scan_denied_and_logged`,
  `test_invalidated_label_scan_is_denied`,
  `test_scan_alone_never_performs_a_consequential_action`).
- **A real pre-existing cross-organization access gap was found and
  fixed during this session's live validation pass:**
  `apps.inventory.views.lot_detail` had no organization scoping at
  all — confirmed live via `curl` with a genuine second-organization
  user before the fix (200 OK, should have been 404) and after (404).
  Fixed and covered by a regression test
  (`tests/test_storage_suitability.py::TestLotDetailIsolation`).

## Physical property / field operations release (added in a later session)

- **Building/floor/unit/drawing/allocation/field-issue access is
  organization- and project-scoped the same way as everywhere else in
  this system** — every detail view does a direct
  `get_object_or_404(..., <path>__organization=request.user.profile
  .organization)` lookup (never a bare `pk=pk` fetch followed by a
  permission check after the fact) plus
  `apps.workflow.services.user_can_access_project` for project-level
  isolation, with the same management-role bypass used throughout.
  Verified by `tests/test_property_master.py`,
  `tests/test_drawing_register.py`, `tests/test_order_allocation_spares.py`,
  and `tests/test_field_issues.py` (cross-organization → 404;
  no-project-access → 404; explicit `UserProjectAccess` grant → 200;
  management role → 200 without a grant).
- **`Unit.permanent_code` is generated once and never regenerated** —
  the identifier a QR label, order allocation, drawing link, or field
  issue points at cannot silently change out from under it later.
- **A drawing revision is always a new row** — `supersede_drawing`
  never edits `source_document` (or anything else) on the prior
  `Drawing`; any historical FK elsewhere keeps pointing at the exact
  revision it referenced, verified by a dedicated test
  (`test_historical_reference_to_old_drawing_is_never_silently_replaced`).
- **Purchased-spare confirmation and drawing approval both require
  the same senior-authorization permission** (`can_override_gates`) —
  neither can be triggered by an ordinary user, verified by dedicated
  unauthorized-attempt tests in both test files.
- **Field-issue closure cannot be granted by completing the work.**
  `verify_and_close_issue` re-checks `can_override_gates`
  unconditionally, including in the specific test scenario where the
  same user who performed the correction attempts to also close it
  without holding that permission (denied).
- **A rejected field-issue correction's evidence and comments are
  never deleted** — resubmission only ever adds new evidence and
  starts a new verification cycle, verified by a dedicated test
  asserting the original "after" evidence row still exists post-
  rejection.
- **Duplicate-submission protection**: `report_issue` returns the
  existing row (not a new one) for an identical building+reporter+
  title report within a 60-second window — verified by both a
  service-level and an HTTP-level double-submit test.

- **Training reference-installation approval cannot skip supervisor
  sign-off** — `approve_as_reference_installation` checks
  `session.supervisor_signed_off_at is None` before permission is even
  relevant, verified by a dedicated test. Both sign-off and approval
  also require `can_override_gates`, verified by dedicated unauthorized-
  attempt tests.
- **Participant acknowledgement is validated against the real
  participant list** — `acknowledge_participation` raises if the
  calling user isn't actually registered as a participant on that
  session, verified by a dedicated test.
- **An apartment cannot be marked ready for delivery while blocking
  defects or unverified corrections remain, without an authorized,
  written-reason override.** `mark_delivery_decision` requires
  `can_override_gates` for the override path (verified by a dedicated
  unauthorized-attempt test) and always logs the override as
  `AuditEvent.Action.WAIVER` with the full readiness snapshot as
  `before_state` — never a silent bypass.
- **A rejected walkthrough-linked correction's evidence is never
  deleted**, since the correction lifecycle itself is `FieldIssue`'s —
  the same guarantee already covers walkthrough-originated corrective
  issues with no additional code.
- **Unclassified Evidence Inbox: classification targets are
  organization-checked, not just the evidence record itself.** An
  initial implementation of `inbox_classify`/`inbox_reclassify`/
  `inbox_batch_classify` resolved the destination target by
  `ContentType` + primary key alone, with no verification that the
  resolved object belonged to the requesting user's organization —
  found and fixed during development, before any commit, via a shared
  `_resolve_target_or_none()` helper that checks the target through
  `apps.workflow.services.resolve_organization()` (extended with
  `shipment`/`purchase_order`/`walkthrough` fallbacks for `Container`,
  `PurchaseOrderLine`, and `WalkthroughItem`). Verified by a dedicated
  test asserting a cross-organization classification attempt is
  silently refused and leaves the evidence unclassified.
- **Reassigning a classification requires a written reason and never
  deletes the prior classification** — `reclassify_evidence` raises
  without one, and marks the old row inactive + `superseded_by` rather
  than mutating or removing it, verified by dedicated tests including
  an attempt to reclassify an already-superseded row.
- **Interactive Apartment Plan / Room-Zone layer: the interactive
  viewer and the admin mapping screen are both organization- and
  project-scoped**, verified live (404 for a cross-organization user
  on the unit-plan viewer, the admin template-detail screen, and a
  field issue created from a zone) and by dedicated tests. Every
  mutating admin action (add/edit zone, validate zone, approve/
  supersede template) requires `can_override_gates` — enforced in the
  service layer itself (not just the view), verified by dedicated
  unauthorized-attempt tests for all three actions.
- **Editing a zone's shape/name/type, or superseding a template, never
  mutates the row in place** — both create a new row and mark the old
  one inactive/superseded, verified by a dedicated test that a
  `FieldIssue` created against a zone keeps pointing at that zone's
  exact original shape after the zone is later edited, and another
  confirming an unrelated issue's `plan_template` reference survives a
  template supersede untouched even while 48 real units assigned to
  that template were moved onto the new revision.

- **Controlled Transparency / Confidentiality: deny-by-default,
  package- and classification-gated visibility, never a client-side hide.** A
  package's factory-quote and internal-cost-sheet sections do not
  merely render `display:none` for an unauthorized viewer — the backing
  querysets are empty (`Quotation.objects.none()`) unless the requesting
  user holds the matching capability, so the data never leaves the
  server for that request at all. Verified live: the rendered HTML for
  an unauthorized DT Beach client contained zero occurrences of the
  factory name, address, or quote reference, and the same package's
  package-associated upstream factory Purchase Orders and linked
  ManifestLines are now filtered by target package participation and
  classification before serialization. Adversarial integration tests
  prove zero-result lists/counts and 404 direct access for unrelated
  same-organization and different-organization users.
- **An unauthorized package is always a 404, never a 403.** Bare hosting-
  organization equality is not authorization. Discovery requires an active
  package role, an active package-scoped capability, or the existing
  same-tenant `can_override_gates` executive authority. Same-organization and
  different-organization adversaries are absent from both detail and list
  responses.
- **Sensitive capabilities are never role-implied** —
  `ROLE_DEFAULT_CAPABILITIES` deliberately excludes every APPROVE_*/
  AUTHORIZE_*/EXPORT_*/VIEW_PRIVILEGED_AUDIT-type action; each requires
  its own explicit, auditable `CapabilityGrant`, verified by dedicated
  tests for every gated action (submit factory quote, approve client
  quote, freeze package, authorize disclosure, approve change request).
- **Separation of duties enforced structurally, not by convention** — a
  `ClientQuote`'s preparer cannot approve it themselves even if the same actor
  also holds `APPROVE_CLIENT_QUOTE` (proven by an adversarial regression); an
  authorized China-ops user's own approval attempt was denied and the
  quote remained in Draft, while a separately-granted buyer-approver
  user's attempt succeeded; an `EvidenceItem`'s uploader can never
  be its own verifier (`verify_evidence_item` raises on
  `uploaded_by == verifying_user` regardless of capability).
- **Every denied privileged attempt is now recorded in the restricted
  audit trail** — fixed a real bug this release where six
  permission-gated functions' denial-logging call was silently rolled
  back by an over-broad `@transaction.atomic` boundary spanning the
  entire function; the fix moves that boundary to start only after the
  permission check, verified by dedicated tests asserting a
  `PRIVILEGED_ACCESS_DENIED` `AuditEvent` exists after a denied attempt.
- **A Disclosure Grant is field-scoped, projected server-side, and revocable,
  never a blanket reveal** — disclosing a manufacturer name never automatically reveals
  address, cost, markup, or margin (each would need its own grant);
  active frozen `permitted_projection` is the only released value set;
  expiration or revocation removes it from subsequent responses while the
  historical grant remains. Creation and revocation both require
  `AUTHORIZE_DISCLOSURE`, derived from the target grant's package.
- **Evidence mutation is target-derived.** Package authority for bundle
  creation, item addition, verification, and rejection is derived from the
  persisted EvidenceBundle target. A posted package UUID is treated only as a
  consistency assertion and cannot lend authority from another package.
- **Classified document bytes are mediated.** Package participation and
  classification are applied to document metadata and DocumentVersion queries
  before the storage adapter is opened. Temporary-storage tests prove that an
  unauthorized same- or different-organization request returns 404 without a
  storage read.
- **Privileged foundation mutations are capability-gated and denial-audited.**
  Change Request approval and rejection use the same field-specific authority;
  risk create/resolve uses explicit package-scoped `MANAGE_RISK_FLAGS`;
  VerificationAssertion revocation uses package-scoped commercial-document
  authority. Pending Change Requests and unresolved non-standard risk flags
  are recomputed transactionally before a package hold clears.
- **Authorization happens before any transformation, structurally** —
  `governance.services.create_derived_artifact`'s injected `transform_fn`
  (standing in for a real translation/AI provider) receives only the
  already-authorized field projection as its sole argument; it has no
  reference to the full source object at all, proven with a spying test
  double that records exactly what it was given. A derived artifact can
  never become less restrictive than its source classification without
  the caller separately holding `AUTHORIZE_DISCLOSURE`.
- **The Party admin screens (list/detail/create) are gated by
  `can_override_gates`** — the same senior-authorization permission used
  everywhere else in this system, never Django superuser status and never
  a bespoke new flag; verified live that a package-scoped China-ops user
  without that permission is denied (404).
- **Privileged audit is scoped before retrieval, never system-wide
  (foundation correction cycle 2, CTCF-AUDIT-017; ADR-042).**
  `can_override_gates` alone no longer grants access to
  `/gobernanza/auditoria-privilegiada/`, and Django superuser status alone
  never does either — access requires an explicit, currently-active
  `VIEW_PRIVILEGED_AUDIT` `CapabilityGrant`, scoped to an organization or a
  package exactly like every other sensitive capability. The underlying
  `AuditEvent` queryset is built from the caller's authorized
  organization/package ids *before* any event is treated as visible
  (`governance.services.authorized_privileged_audit_scopes` +
  `privileged_audit_queryset`); an event whose target cannot be resolved
  through an existing relationship is excluded, never included. A
  package-scoped grant never widens to the whole organization; an
  organization-scoped grant never widens to every organization. The
  rendered projection never copies a target's raw `__str__` or
  `AuditEvent.metadata` into the browser — verified by
  `tests/test_privileged_audit_scope.py` (31 tests: authority matrix,
  cross-organization and cross-package isolation, unresolvable-target
  fail-closed behavior, information-absence, and denial-audit durability).
  A denied privileged-audit access attempt is itself now durably audited.
- **Privileged-audit retrieval never selects `summary`/`metadata` at any
  phase, and completeness no longer depends on an arbitrary scan ceiling
  (foundation correction cycle 3, CTCF-AUDIT-SCOPE-021 /
  CTCF-AUDIT-RETRIEVAL-022 / CTCF-AUDIT-WINDOW-023; ADR-043).** An
  independent revalidation of cycle 2 found and reproduced three gaps in
  the mechanism above: (a) a `CapabilityGrant` whose scope is inherited
  entirely through `role_assignment` (no direct `package`/`organization`
  on the grant row) resolved to no scope at all, hiding its
  `CAPABILITY_GRANT` event even from an authorized viewer —
  `_resolve_scope_for_target` now explicitly follows `role_assignment`
  as a third resolution step; (b) the candidate scan selected full
  `AuditEvent` rows, including `summary`/`metadata`, before the per-row
  scope decision — the scan now selects only
  `id`/`action`/`occurred_at`/`actor_id`/`content_type_id`/`object_id` at
  every phase, verified by direct SQL-capture tests asserting no query
  issued by this path contains the `summary` or `metadata` column names;
  (c) the scan was capped at the 1,000 most-recent candidate events, so an
  authorized event older than that many unrelated events could be silently
  omitted — the scan is now a deterministic, cursor-paginated loop that
  continues across batches until the requested result count is satisfied
  or candidates are genuinely exhausted, with no cap on how far back it
  will look. None of the three is a confirmed browser-facing disclosure —
  all three are read-path completeness/retrieval-hygiene corrections.
- **Change Request raw values require field-specific detailed-read
  authority, never mere package participation (foundation correction
  cycle 2, CTCF-CR-PROJ-018; ADR-042).**
  `governance.services.change_request_projection` distinguishes knowing a
  request exists, reading its raw `field_name`/`frozen_current_value`/
  `proposed_new_value`/`reason`, and deciding it (approve/reject,
  unchanged). Only the requester, the decider, or an actor holding the
  same field-specific capability already required to decide that field
  receives the raw values; every other package-authorized viewer receives
  a safe, generic projection. Decision-button visibility is never treated
  as read authorization. Verified by
  `tests/test_change_request_projection.py` (9 tests, including
  authority-for-one-field-does-not-reveal-another and
  package-B-authority-does-not-reveal-package-A cases).

## Known gaps (see `KNOWN_LIMITATIONS.md` for the full list)

- No automated dependency vulnerability scan is wired into this delivery
  (no CI pipeline was requested/built in this pass).
- Backup encryption is documented as an operator responsibility
  (`backup.sh` prints a reminder) rather than automated in the script.
- Database-level append-only enforcement, deployed database privilege
  inspection, backup-restore testing, deployed proxy/cache behavior, and
  production log-sentinel analysis remain owner validation; application tests
  do not constitute evidence for those operational controls.
