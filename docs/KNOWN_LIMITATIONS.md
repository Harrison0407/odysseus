# Known Limitations

Honest, current list. This is a staged delivery of an intentionally very
large specification (`ASSUMPTIONS.md` A1) — this file exists so nothing
is silently claimed to be done when it isn't.

## Not yet built (UI)

1. ~~Gate-blocking UI~~ — **Done in the Gate Controls milestone.** Every
   one of the 8 required transitions now has a real evaluator
   (`apps.workflow.gates`) and a handoff detail screen (`/flujo/<id>/`)
   that shows exactly why a gate is blocked, with an authorized-override
   path requiring a written reason. One residual gap: the "blocked"
   message currently lives on the handoff detail page itself, not
   additionally repeated inline on the Shipment/Material Request detail
   page at the point a transition is attempted outside the handoff flow.
2. ~~Handoff inbox~~ — **Done.** `/flujo/` is a full role-aware,
   filterable inbox (para mí / enviadas por mí / devueltas / bloqueadas /
   completadas / todas), with accept/reject/return/resubmit/comment all
   wired to real permission-checked actions.
3. **Detailed internal receiving manifest, exact spec 13A.8 layout.** All
   the underlying data is present and shown on the shipment detail page,
   but not yet in the specific 10-section printable layout the spec
   describes for Manuel's team.
4. ~~Dispatch/Delivery UI~~ — **Done in the Delivery, Installation,
   Inspection, and Final Acceptance milestone.** `/solicitudes/entregas/`,
   `/solicitudes/instalaciones/`, `/solicitudes/inspecciones/`, and
   `/solicitudes/aceptaciones/` cover approve → reserve → dispatch →
   deliver (partial/multi-trip/damage/refusal) → project receipt →
   install (with quantity-guard and authorized override) → inspect
   (pass/conditional/fail, punch-list) → correct → reinspect → final
   accept, all through the same reused `apps.workflow.gates`/
   `apps.workflow.services` engine — see the dedicated section in
   `REQUIREMENTS_TRACEABILITY.md`.
5. **Landed-cost allocation-run trigger UI.** The calculation models
   (`CostAllocationRun`, `LandedCostVersion`) exist; running an allocation
   currently requires the ORM/a script, not a button.
6. **Priority 1 UI entirely:** CONFOTUR reconciliation screens, tool
   custody screens, cycle-count screens, storage capacity/suitability
   warnings, external-storage comparison calculator, supplier claim
   package generation, QR label printing. Data models exist for all of
   these (`apps.customs`, `apps.tools`, `apps.inventory.CycleCount`,
   `apps.receiving.AlternativeStorageOption`); none has a UI yet.
7. ~~`purchasing_to_finance`/`finance_to_logistics` have no "create
   handoff" button" on the Purchase Order detail page~~ — **Done.** All
   8 required gates now have a "create handoff" entry point on their
   target's detail page — the last two were wired into
   `procurement/po_detail.html`, reusing the exact same
   `{% url 'workflow:create' ... %}` button pattern already used on the
   Shipment/Material Request/Delivery/Installation detail pages, never a
   new create-handoff mechanism. Verified by 3 new tests
   (`tests/test_workflow_handoffs.py`) and live: a real HTTP click
   through the button created a genuinely `ready_for_submission` handoff
   for an approved PO.

## Delivery/Installation/Inspection/Final Acceptance milestone — honest gaps

- ~~Multi-lot split dispatch has no dedicated UI action~~ — **Done.** The
  material request detail page now shows every active reservation per
  line (lot, remaining, an editable quantity) and dispatches whatever
  quantities are submitted in one request, calling
  `apps.requests.services.create_dispatch` with explicit per-reservation
  tuples. `DispatchLine.reservation` (new FK, migration
  `requests.0003_dispatchline_reservation`) records which specific
  reservation each dispatched quantity was drawn from, so a line's
  dispatch can never exceed what a *specific* lot's reservation actually
  holds even when the line-wide aggregate would allow it (see
  ADR-023). Verified live: reserved 6 units from one lot and 4 from a
  second lot against the same line, dispatched both in a single
  submission, confirmed two distinct `DispatchLine` rows against the
  correct lots.
- **Duplicate-click protection is deliberately asymmetric.**
  `create_installation_record` and `create_project_receipt` are
  idempotent (a repeated submit returns the existing row — see ADR-018);
  `create_inspection` is not, because a genuine reinspection must always
  create a new row. A rapid double-click on "Registrar inspección" could
  in principle create two near-identical inspection rows with duplicated
  punch-list items; this is mitigated only by human review (the
  duplicate would show as a second, identical-looking inspection in the
  installation's history), not a hard technical constraint.
- ~~Installer/inspector assignment dropdowns list every user in the
  organization~~ — **Done.** `assigned_installer` on the installation-
  creation form is now scoped to active users holding a role in the
  department actually configured to do installation work
  (`installation_to_inspection.from_department` — read from the same
  `GateDefinition` the gate engine uses, never a hard-coded department
  name), further narrowed to users with `UserProjectAccess` to the
  target project when the project has any such grants configured. This
  was always enforced server-side regardless of who was picked (a pure
  data-entry gap, not a security one) — the fix only tightens what the
  dropdown offers. Inspector assignment has no dropdown at all
  (`create_inspection` always records `request.user` as the inspector),
  so there was nothing to scope there.
- ~~Evidence/photo upload is modeled but not wired into these screens~~
  — **Done.** `apps.audit.services.attach_evidence`/`list_evidence` wire
  the generic `Attachment` model (content_type/object_id) into the
  Delivery/InstallationRecord/InspectionRecord detail pages, reusing
  `apps.documents.views.DocumentUploadForm` as-is (extension/size
  validation, SHA-256 dedup, never duplicated). Access to the upload
  action is gated by the same `_deny_cross_project` check as every other
  action on these screens; the download itself goes through the
  pre-existing, unchanged `documents:download` view (organization-scoped,
  not additionally project-scoped — same as every other document in the
  system, a deliberate non-change). None of this milestone's 3 gates
  currently declares an `evidence_requirements` list on its `GateResult`,
  so evidence is available and browsable but not yet a hard blocking
  requirement for any of the 3 gates.
- **Mobile rendering is structurally, not visually, verified** — same
  honesty convention as the Priority 0 milestone's item 7. Every
  list/detail template across the *entire* application now wraps its
  tables in a horizontally-scrolling container (a later-session audit
  found and fixed 19 tables across 9 templates that predated this
  milestone and lacked it — `cost`, `documents`, `inventory`,
  `procurement`, `receiving`, `shipments`, `workflow`, plus the
  self-contained HTML snapshot export, which also gained a viewport meta
  tag). **No headless browser or screenshot tool is available in this
  environment**, so no pixel-level visual regression pass at multiple
  viewport widths has ever been run — this is recorded honestly as a
  standing limitation of the environment, not a skipped task.

## Not yet built (integrations/infrastructure)

8. **OCR.** `DocumentClassificationResult`/`DocumentFieldSource` model
   what an OCR pipeline would populate; no OCR engine (local Tesseract or
   otherwise) is wired in. Scanned-PDF uploads are stored and downloadable
   but not automatically transcribed.
9. **Translation.** Same as above — the provenance fields exist
   (`proposed_translation`/`confirmed_translation`), no translation
   adapter is implemented.
10. **QuickBooks / MarketMatch integration.** Deliberately not built —
    see `QUICKBOOKS_INTEGRATION_DISCOVERY.md` and
    `MARKETMATCH_INTEGRATION_PATH.md` for why and what would be needed.
11. ~~Rate limiting on login/share-link endpoints~~ — **Done.**
    `apps.core.ratelimit` (a small fixed-window counter backed by
    Django's cache framework, no Redis/Celery dependency per
    `ASSUMPTIONS.md` A3) gates `RateLimitedLoginView` (10 failed
    attempts / 5 minutes per IP) and the public `shared_view` share-link
    endpoint (30 requests / minute per IP, since it's fully
    unauthenticated). **Known limitation of this implementation:** the
    default `LocMemCache` backend is per-process, so a multi-worker
    Gunicorn deployment enforces the limit per worker, not globally
    across the whole server — acceptable for this pilot's scale (a
    single small server, ~8 named users), recorded honestly rather than
    overstated as a hardened, distributed rate limiter.
12. **CI pipeline** (automated test run on every push) was not requested
    and was not built in this pass; tests are run manually via `pytest`.

## Fixed during the Delivery/Installation/Inspection/Final Acceptance milestone

- **Duplicate installation creation** — `create_installation_record` had no
  idempotency guard; an accidental repeated form submission during this
  milestone's own live HTTP walkthrough created two `InstallationRecord`
  rows for the same `(project_receipt, delivery_line)`. Fixed with a
  `select_for_update` + first-existing-wins guard (ADR-018), covered by
  `test_21c_duplicate_installation_creation_is_idempotent`. The same
  pattern was applied preemptively to `create_project_receipt`
  (`test_21d`).
- **Final-acceptance detail could become permanently unreachable** — the
  first version of `installation_final_accept` called
  `apps.workflow.services.accept_handoff` itself before recording the
  domain decision. A user who instead accepted the same handoff through
  the generic, already-exposed `/flujo/<id>/aceptar/` button (a fully
  valid entry point, reachable from the workflow inbox without ever
  visiting the installation page) left the record with no way to ever
  capture the accepted-vs-conditional decision. Caught during this
  milestone's live walkthrough (not by a unit test, since the tests only
  called the service functions directly). Fixed per ADR-021: the
  domain-specific screen now only activates once the handoff is already
  `ACCEPTED`, by whichever route.

## Fixed during the Gate Controls milestone

- One test bug (not a product bug): `test_successful_handoff_full_lifecycle`
  initially forgot to attach the evidence `logistics_to_receiving`
  requires before submitting, so it failed with `GateBlockedError` on
  first run — exactly the correct behavior; the test was fixed to attach
  evidence first, not the product code.
- A migration had to be deleted and regenerated once after making
  `Handoff.organization` nullable, to avoid Django's interactive
  "provide a one-off default" prompt (no data existed yet to make this a
  real risk — see `docs/implementation-log.md` step 9).

## Fixed during the Priority 0 delivery (recorded so they aren't rediscovered)

- A `lambda` default on a model field broke `makemigrations` — fixed.
- Whitenoise's manifest storage failed on a vendored CSS file's dangling
  source-map reference — fixed.
- `docker-compose.prod.yml` silently dropped any env var not explicitly
  hand-listed — fixed by switching to `env_file:`.
- The production Docker image had briefly (during validation) been
  running against an in-container SQLite file instead of Postgres because
  a local dev `.env` was copied into the image — fixed with
  `.dockerignore` and a `DEBUG`-gated settings guard, and the entire
  validation sequence was re-run against genuine Postgres afterward. See
  `docs/architecture-decisions.md` ADR-011/ADR-012 and
  `docs/implementation-log.md` for the full account.
- `backup.sh`'s document-archiving step used a broken `docker run` volume
  invocation — fixed by archiving via `docker compose exec ... tar`.

## Data-quality ambiguities (not bugs — see `DATA_QUALITY_AND_UNCERTAINTY.md`)

The W5057/W5097 possible-match, the Sole-26 building/project labeling
conflict, the unattributed 200-unit accessories line, and the 19-vs-20
quartz slab packed-quantity conflict are all *intentionally* left
unresolved in the data, exactly as the governing spec requires — these
are not gaps to close but the system correctly doing its job.
