# Security

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

## Known gaps (see `KNOWN_LIMITATIONS.md` for the full list)

- No automated dependency vulnerability scan is wired into this delivery
  (no CI pipeline was requested/built in this pass).
- Backup encryption is documented as an operator responsibility
  (`backup.sh` prints a reminder) rather than automated in the script.
