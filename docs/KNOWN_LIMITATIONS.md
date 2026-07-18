# Known Limitations

Honest, current list. This is a staged delivery of an intentionally very
large specification (`ASSUMPTIONS.md` A1) — this file exists so nothing
is silently claimed to be done when it isn't.

## Not yet built (UI)

1. **Gate-blocking UI.** The storage-readiness gate (spec 9.4) and
   operational-verification gate (spec 9.5) are structurally enforced by
   the data model (e.g. a `Receipt` cannot exist without a frozen
   `ReleasePacketVersion`), but there is no dedicated screen that shows a
   literal "blocked, here's why, override with reason" message at the
   `Shipment.status` transition itself.
2. **Handoff inbox.** `apps.workflow.Handoff`/`HandoffDecision` are fully
   modeled and covered conceptually, but there is no accept/reject screen
   yet — only a read-only list on the dashboard.
3. **Detailed internal receiving manifest, exact spec 13A.8 layout.** All
   the underlying data is present and shown on the shipment detail page,
   but not yet in the specific 10-section printable layout the spec
   describes for Manuel's team.
4. **Dispatch/Delivery UI.** `apps.requests` models the full
   request→approval→reservation→pick→dispatch→delivery→installation
   chain; only the request-creation screen is built.
5. **Landed-cost allocation-run trigger UI.** The calculation models
   (`CostAllocationRun`, `LandedCostVersion`) exist; running an allocation
   currently requires the ORM/a script, not a button.
6. **Priority 1 UI entirely:** CONFOTUR reconciliation screens, tool
   custody screens, cycle-count screens, storage capacity/suitability
   warnings, external-storage comparison calculator, supplier claim
   package generation, QR label printing. Data models exist for all of
   these (`apps.customs`, `apps.tools`, `apps.inventory.CycleCount`,
   `apps.receiving.AlternativeStorageOption`); none has a UI yet.

## Not yet built (integrations/infrastructure)

7. **OCR.** `DocumentClassificationResult`/`DocumentFieldSource` model
   what an OCR pipeline would populate; no OCR engine (local Tesseract or
   otherwise) is wired in. Scanned-PDF uploads are stored and downloadable
   but not automatically transcribed.
8. **Translation.** Same as above — the provenance fields exist
   (`proposed_translation`/`confirmed_translation`), no translation
   adapter is implemented.
9. **QuickBooks / MarketMatch integration.** Deliberately not built —
   see `QUICKBOOKS_INTEGRATION_DISCOVERY.md` and
   `MARKETMATCH_INTEGRATION_PATH.md` for why and what would be needed.
10. **Rate limiting** on login/share-link endpoints is not implemented.
11. **CI pipeline** (automated test run on every push) was not requested
    and was not built in this pass; tests are run manually via `pytest`.

## Fixed during this delivery (recorded so they aren't rediscovered)

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
