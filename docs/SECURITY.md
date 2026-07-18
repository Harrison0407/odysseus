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

## Known gaps (see `KNOWN_LIMITATIONS.md` for the full list)

- Rate limiting on login/share-link endpoints is not yet implemented
  (spec asks for "rate limiting or reasonable protection" — currently
  relying on Django's default session/CSRF protections only, no
  dedicated throttle).
- No automated dependency vulnerability scan is wired into this delivery
  (no CI pipeline was requested/built in this pass).
- Backup encryption is documented as an operator responsibility
  (`backup.sh` prints a reminder) rather than automated in the script.
