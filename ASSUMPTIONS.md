# Assumptions

Living document. Every entry is a conservative operational choice made where the
governing spec, the business-context interviews, or the source documents left a
minor point unresolved. None of these fabricate business data — where the
ambiguity is about a business fact (a quantity, a project name, a match), it is
recorded in `docs/DATA_QUALITY_AND_UNCERTAINTY.md` / `docs/source-package-analysis.md`
instead, exactly as required, and is **not** listed here as a resolved assumption.

## Scope and delivery approach

- **A1. Delivery is staged, not "all 38 sections at once."** The locked prompt
  describes an ERP-scale system (dozens of domains, OCR, multi-language
  translation, full production deployment, CONFOTUR reconciliation, tool
  custody, etc.). Building all of it to production quality in a single
  uninterrupted session is not achievable honestly. The conservative choice
  is to build the Priority 0 vertical slice completely and correctly
  (working code, migrations, tests, docs) before adding Priority 1 breadth,
  and to keep `docs/implementation-roadmap.md` and `docs/implementation-log.md`
  continuously honest about what is actually done versus scaffolded versus
  not yet started. This is safer than a shallow pass across every section
  that cannot be verified to actually work.
- **A2. Named pilot users are seed data, not code.** Harrison, Edison,
  Markeris, Lucía, Manuel, Óscar, Miguel, María Luisa are created by a
  management command with administrator-supplied emails/passwords; no
  business logic branches on a specific person's name.
- **A3. No paid/external services.** No external LLM, no paid OCR/translation
  API, no QuickBooks write integration, no Celery/Redis/Kafka/Kubernetes.
  Local OCR (tesseract) is treated as optional/pluggable via an adapter
  interface, not a hard dependency of core workflows.

## Technical defaults chosen conservatively

- **A4. Default inspection deadline for provisional receipts:** 7 calendar
  days after unloading (the spec's stated maximum default).
- **A5. Default "stored too long" warning threshold:** 30 days (spec-stated
  default), configurable per organization.
- **A6. Currency handling:** all monetary fields store both the original
  currency/amount and a base-currency (USD) equivalent with the exchange
  rate, date, and source recorded — never only a converted number.
- **A7. UUID primary keys** for all domain models that may ever be referenced
  by a share link or exposed externally; sequential integer PKs are never
  exposed in URLs.
- **A8. Object storage abstraction:** documents are stored on local disk
  behind a storage interface from day one, so a later switch to S3-compatible
  storage does not require a data-model change.
- **A9. Spanish is the default UI language**, with an English toggle, per
  spec §30. Model field labels and admin are bilingual-friendly but the
  primary business UI strings are Spanish first.

## Fixture-handling assumptions (see also source-package-analysis.md §5)

- **A10. `PL - Perfileria Paños WA10 - 2026.6.12...xlsx`** (header states
  "cntr qty: 3") is treated as **out of scope** for the single-container
  MEDUWY575021 / TCNU8926924 acceptance fixture, since nothing else in the
  package ties it to this BL. It is retained as a document in the source
  index but excluded from this container's imported manifest by default.
  A human reviewer can re-link it explicitly.
- **A11. W5057 vs W5097** is imported as two distinct `ItemAlias`/reference
  strings pointing at one `Possible Candidate` match, never auto-merged into
  a single confirmed item, per core principle 4.3 (no silent confirmation).
- **A12. The apartment-label conflict** between "Sole-26 (G/H)" and
  "Sole-26 / APT-A, APT-B" within the same source workbook is preserved by
  keeping both source strings on the `ManifestLineSource` record; the
  canonical apartment/unit identity is left `Unknown — Pending Confirmation`
  until a human resolves it.

## Delivery/Installation/Inspection/Final Acceptance milestone

- **A13. "Approve a material request" defaults every line's
  `quantity_approved` to the requested amount.** The spec describes an
  approval step but not a UI for partially approving individual lines at
  a different quantity than requested. The conservative default is
  "approve as requested"; a line-by-line different-quantity approval
  screen is not built, but nothing prevents an approver from directly
  editing `quantity_approved` via the admin/ORM before reservation if a
  real partial approval is needed — the quantity-invariant guards
  downstream (reservation, dispatch, delivery, installation) all read
  from `quantity_approved`, not the requested amount, so a corrected
  value is respected everywhere it matters.
- **A14. One material-request line is assumed to draw from one lot per
  dispatch.** `request_dispatch` dispatches the full reserved-and-
  undispatched quantity per line using that line's first active
  reservation. The service layer supports an explicit multi-lot split
  per line (`apps.requests.services.create_dispatch` takes an explicit
  list of `(line, reservation, quantity)` tuples); only the one-lot-per-
  line UI shortcut is built, since it matches the pilot's real usage
  (see `docs/KNOWN_LIMITATIONS.md`).
- **A15. "Final acceptance" is a two-step action by design, not an
  oversight.** Accepting the `inspection_to_acceptance` handoff (the
  formal gate transition, via the reused, generic
  `apps.workflow.services.accept_handoff`) and recording the accepted-
  vs-conditional decision detail (`apps.requests.services.record_final_acceptance`)
  are deliberately separate calls — the latter only activates once the
  former has happened, by whichever route (see ADR-021). This keeps the
  generic handoff-acceptance action usable from the ordinary workflow
  inbox for every gate uniformly, while still capturing the domain-
  specific decision detail this particular gate needs.

## Priority 0 completion (continuing autonomous session)

- **A16. The detailed internal receiving manifest's 10-section layout
  (spec 13A.8) was reconstructed, not copied verbatim.** The governing
  specification document itself is not stored anywhere in
  `Application/` — only documents derived from it during earlier
  sessions are (`BUSINESS_REQUIREMENTS.md`,
  `OFFICIAL_VS_OPERATIONAL_MANIFEST_ANALYSIS.md`, etc.), and the exact
  original section numbering/wording could not be re-read in this
  session. The conservative choice was to build a genuinely useful
  10-section document covering the same operational content Manuel's
  interview describes (container/shipment header, official summary,
  full internal manifest, physical receiving detail, inspections,
  quarantine/damage, discrepancies, receiving plan, and the variance
  matrix) rather than either fabricating spec text from memory or
  refusing to build the feature. Section 10 (the variance matrix) is
  independently confirmed correct via a direct citation in
  `OFFICIAL_VS_OPERATIONAL_MANIFEST_ANALYSIS.md` ("spec 13A.8, item
  10"); the other 9 are a faithful reconstruction, not a verified
  match to the original numbering. See `KNOWN_LIMITATIONS.md` item 3.
- **A17. Rate limiting uses Django's default in-process cache, not a
  shared store.** No Redis/Celery dependency is added (A3), so the
  10-attempts/5-minutes login limit and the 30-requests/minute
  share-link limit are enforced per Gunicorn worker process, not
  globally across a multi-worker production deployment. Acceptable at
  this pilot's scale (a single small server, ~8 named users); a real
  multi-worker rollout wanting a global limit would need a shared cache
  backend (e.g. Redis) — deliberately not added here per A3.
- **A18. Landed-cost bucket mapping and net-weight basis are
  conservative simplifications.** `CostDocument.CostType` has 11 values
  (freight, insurance, customs duties, port/terminal, brokerage, local
  transport, handling, storage, demurrage, inspection, other) but
  `LandedCostLine` only has 3 per-unit buckets (freight/local/other).
  `apps.cost.services` maps FREIGHT to the freight bucket; customs
  duties/port/brokerage/local transport/handling/storage/demurrage to
  the local bucket; insurance/inspection/other to the other bucket —
  a defensible grouping, not a spec-mandated one, since no single
  correct mapping is stated anywhere available to this session. Net
  weight is not separately modeled on `ManifestLine` (only
  `gross_weight_kg` is), so the `NET_WEIGHT` allocation method currently
  uses gross weight as its basis — recorded here rather than silently
  treated as identical without comment.
- **A19. `lot_on_hand_quantity` is duplicated once, deliberately.**
  `apps.inventory.services.lot_on_hand_quantity` and
  `apps.requests.services.lot_on_hand_quantity` (added in an earlier
  milestone) compute the identical ledger-derived on-hand quantity for
  a lot. Rather than having `apps.inventory` (the lower-level, more
  fundamental app) import a utility from `apps.requests` (a
  higher-level app built on top of inventory), the small pure function
  was reimplemented in its more architecturally correct home. A future
  cleanup pass could extract both into a single shared helper; not done
  here to avoid touching tested Milestone 3 code for a purely cosmetic
  gain.
- **A20. Per-unit volume/weight for storage-capacity utilization is
  derived from the most recently linked `ManifestLine`, not a stored
  `Item` field.** `Item` has no per-unit CBM/kg field (only a free-text
  `dimensions` string), so `apps.inventory.services
  ._item_per_unit_footprint` divides the most recently created
  `ManifestLine.cbm`/`gross_weight_kg` linked to that item by that
  line's `quantity` to approximate a per-unit footprint for projected
  capacity checks. This is an approximation (a single historical
  shipment's packaging may not represent every unit of that item
  going forward) and is reported as unknown — never a fabricated
  zero — when no manifest line exists to derive it from, consistent
  with A6/A9's "never silently assume a missing physical fact is
  zero." A future improvement would add real per-unit
  volume/weight fields to `Item` itself; not done here since it would
  require a data-migration/backfill decision (which historical
  shipment, if any, should populate existing items) that is better
  made deliberately than inferred.
- **A21. A blocking storage restriction is *configured*
  (`WarehouseLocation.allowed_categories` or a hard capacity ceiling
  exceeded); a sensitive-material/environmental mismatch is a
  *warning only*, never a block.** `ProductRiskProfile.risk_level ==
  HIGH` placed in a location lacking covered/dry/secure conditions, or
  with flood/leak risk, always produces a warning, never a
  `StorageSuitabilityError`, even for an unauthorized user — the
  business requirements describe environmental exposure as a
  historical *visibility* failure (nobody noticed materials sitting in
  the rain), not a case needing to become physically impossible to
  cause. If Harrison wants specific risk/location combinations to be
  hard-blocking rather than warning-only, that should be configured via
  `WarehouseLocation.allowed_categories` (already blocking) rather than
  by changing this default, since a blanket "all high-risk warnings
  become blocks" rule could stop urgent legitimate put-aways with no
  override path considered case-by-case.
- **A22. The absence of a `LocationSuitability`/`LocationCapacity` row
  is never itself a blocking condition.** Most existing
  `WarehouseLocation` rows (seeded and test fixtures alike) have no
  suitability/capacity row configured at all.
  `check_location_suitability` treats a missing row as "cannot
  confirm," at most a warning for a high-risk item — never as an
  automatic block — so this feature does not retroactively make every
  unconfigured location in the system unusable. Configuring
  suitability/capacity per location remains an operational data-entry
  task outside this session's scope.
- **A23. A lot with positive on-hand balance at more than one location
  cannot be transferred through `/almacen/ubicaciones/<id>/transferir/`
  — the screen refuses with an explicit error instead of guessing
  which location the user meant.** `transfer_lot` needs a single
  unambiguous origin location; rather than picking "whichever location
  holds the most" (considered and rejected as presumptuous), the view
  requires the user to resolve the ambiguity through another means.
  This is a deliberate scope limit of the new UI, not a limitation of
  the `Transfer` model or `transfer_lot` service itself, both of which
  accept an explicit `from_location` from any caller.
- **A24. Demurrage/penalty exposure is excluded from a storage option's
  guaranteed comparable total, shown separately instead.**
  `apps.receiving.services._option_guaranteed_total` sums
  storage/handling/transport/insurance costs (applying the minimum
  commitment as a floor when it's higher), but never adds
  `demurrage_penalty_estimated_cost` into that guaranteed figure — a
  contingent risk exposure is not the same kind of number as a firm
  quoted cost, and blending them would make a single "total" number
  misleadingly certain. Both are shown side by side in the comparison
  table/export so nothing is hidden, just not summed together.
- **A25. `AlternativeStorageOption.storage_cost`/`handling_cost`/
  `inbound_transport_cost`/`outbound_transport_cost`/`insurance_cost`
  are each entered as a total for the option's entire evaluated
  period (`expected_duration_days`), not a per-day/per-unit rate.**
  No rate-basis field was added (e.g. "per day," "per CBM") since the
  spec doesn't mandate one and inventing a unit-conversion system here
  would be speculative; a user comparing quoted rates must do the
  arithmetic to the same evaluated period before entering a total.
  Documented here rather than silently assumed identical across every
  option.
- **A26. `AlternativeStorageOption.scenario` and `.currency` are
  schema-nullable (`null=True`) even though every option created
  through the service layer always has both set.** This is the same
  "nullable in schema, required in practice, enforced by the service/
  form layer" pattern already used throughout this codebase (e.g. most
  optional-in-schema FKs elsewhere) — chosen here specifically because
  `AlternativeStorageOption` had zero existing rows in every
  environment before this feature (confirmed: no calling code
  anywhere), so adding a true non-nullable FK would have required an
  arbitrary one-off migration default with no real row to apply it to.
  `add_storage_option`/`StorageOptionForm` both require a currency in
  practice.
- **A27. A `SupplierClaim` cannot be approved for submission without
  at least one evidence attachment.** `apps.claims.services
  .approve_claim` raises `ClaimError` if
  `apps.audit.services.list_evidence(claim)` is empty. This is a
  conservative *operational* rule chosen by this session, not a legal
  requirement handed down by the spec — a claim with zero evidence
  should not leave draft state. It's a "add the evidence, then
  approve" gate, not a business emergency requiring an override path,
  so no override mechanism was added (unlike the storage-suitability
  gates, which do have one). If Harrison wants an override path here
  too (e.g. for a claim where evidence genuinely doesn't exist), that
  is a five-minute addition reusing the same `can_override_gates`
  pattern — flagged here rather than silently assumed unnecessary.
- **A28. `SupplierClaim.claim_number` is generated
  organization-and-year-scoped (`CLM-{year}-{sequence:04d}`) using a
  `Max()` aggregate inside `transaction.atomic`, not a
  `select_for_update`-guarded counter.** This matches the exact
  pattern already used for `StorageComparisonScenario.version_number`
  in this same delivery (A-adjacent, ADR-030) — at this pilot's actual
  concurrency (a handful of named users, not simultaneous claim
  creation at the same instant), the small theoretical race window is
  an acceptable, consistent trade-off rather than a bespoke
  distributed-lock mechanism for one sequence generator when none of
  the system's other sequence generators have one either.
