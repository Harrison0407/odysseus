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
4. **Dispatch/Delivery UI.** `apps.requests` models the full
   request→approval→reservation→pick→dispatch→delivery→installation
   chain; only the request-creation screen is built. Consequently, the
   `project_delivery_to_installation`, `installation_to_inspection`, and
   `inspection_to_acceptance` gates are fully implemented and tested at
   the engine/service layer (see `REQUIREMENTS_TRACEABILITY.md`) but have
   no "create handoff" button anywhere yet — a handoff for those gates
   must currently be created via the ORM/a script; once created, the
   generic `/flujo/` accept/reject/return flow works for them exactly as
   it does for the two gates that do have buttons.
5. **Landed-cost allocation-run trigger UI.** The calculation models
   (`CostAllocationRun`, `LandedCostVersion`) exist; running an allocation
   currently requires the ORM/a script, not a button.
6. **Priority 1 UI entirely:** CONFOTUR reconciliation screens, tool
   custody screens, cycle-count screens, storage capacity/suitability
   warnings, external-storage comparison calculator, supplier claim
   package generation, QR label printing. Data models exist for all of
   these (`apps.customs`, `apps.tools`, `apps.inventory.CycleCount`,
   `apps.receiving.AlternativeStorageOption`); none has a UI yet.
7. **`purchasing_to_finance`/`finance_to_logistics` have no "create
   handoff" button** on the Purchase Order detail page yet (unlike the
   two Shipment-anchored gates, which do). The evaluators and full
   accept/reject/return/override flow are implemented and tested
   (`tests/test_workflow_gates.py::TestPurchasingToFinance`,
   `TestFinanceToLogistics`); only the "create" entry point on that
   specific page is missing.

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
11. **Rate limiting** on login/share-link endpoints is not implemented.
12. **CI pipeline** (automated test run on every push) was not requested
    and was not built in this pass; tests are run manually via `pytest`.

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
