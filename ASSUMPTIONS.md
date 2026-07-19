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
- **A29. A QR "print" is logged the moment the print page is loaded
  and an explicit "Registrar impresión" action is submitted — the
  system has no way to know whether a browser print dialog was
  actually confirmed or cancelled.** This is a standard, accepted
  simplification for internal operational tools: reprint *history* is
  a record of "this label was prepared/printed by this user at this
  time," not cryptographic proof a physical label left a printer. If
  Harrison needs stricter proof-of-print (e.g. for a compliance
  audit), that would require printer-integration hardware/APIs well
  beyond this session's scope.
- **A30. QR label print-link UI coverage is 7 of 8 entity types;
  `Dispatch` is supported at the service/URL layer but has no detail
  page to attach a visible link to.** `Dispatch` objects are
  summarized inline on their parent `MaterialRequest`'s detail page,
  never rendered as an individually browsable row — there was no
  existing per-dispatch template location to add a print link without
  first building a new "dispatch detail" screen, which is out of this
  feature's scope. `apps.labels.services.get_or_create_active_label`/
  `record_print`/the `labels:print` URL all work identically for a
  `Dispatch` as for any other registered entity (confirmed by the
  entity registry in `apps.labels.services` including it), and a
  physical dispatch label could be generated today via a direct link
  if one were added to a future dispatch-specific screen.

## Physical property / field operations release

- **A31. Building family → physical building assignment is read from
  the site-plan legend in `Buildings Plans Main.pdf`, not invented.**
  That document's numbered building index (Building/Bldg.Code/Type/
  Apts. columns) gives an explicit building-number list per family:
  ARENA T1 = {1,2,3,4,9,10,11,12} (exactly the 8 named in the release
  instruction — cross-confirms the source), MARE B =
  {12,14,16,19,21,23,24,25}, SOLE (no penthouse) = {13,17,22}, SOLE PH
  = {15,18,20}, SOLE 26 = {26}, PALMERA = 1 (a single hotel-style
  building, visually separate from the numbered residential grid).
  Apartment counts per building (34/20/20/16/40/104 respectively)
  independently match the per-floor unit template in `DT Beach
  Building Apartments.xlsx.pdf`, which cross-validates both documents
  against each other rather than trusting either alone.
- **A32. The apartment floor-template in the source spreadsheet is
  applied uniformly to every physical building sharing a type, since
  the source gives one template per type, not one per individual
  building number.** E.g. all 8 ARENA T1 buildings receive the same
  34-unit-per-building layout. This is the only way to populate 24
  physical buildings from a source that itself only tabulates 6
  representative floor plans (one per building type) — documented
  here rather than silently presented as if each building had its own
  independently-sourced plan.
- **A33. The four illustrative permanent-code examples in the release
  text were reconciled against the imported data — 3 of 4 matched
  exactly; the 4th did not, and is treated as illustrative shorthand,
  not a literal fact.** `ARENA-T1-B11-A3`, `ARENA-T1-B12-A3`, and
  `MARE-B-B12-C4` all resolve to real imported units (confirmed by
  test `test_known_examples_resolve_to_real_units`). `SOLE-PH-B03-A5`
  does not, because the site-plan legend gives SOLE PH's real building
  numbers as 15/18/20, not 3 — since the other three examples
  independently corroborate the site-plan-derived roster, "B03" is
  treated as a format illustration rather than evidence of a real
  building 3 under SOLE PH that the source documents don't otherwise
  support (core principle 4.3 — never invent a physical building
  assignment).
- **A34. `Unit.permanent_code` is unique across the whole system, not
  scoped per organization.** This pilot has exactly one real tenant
  organization; true multi-tenant per-organization scoping would need
  a direct `organization` FK on `Unit` (today it's reached via
  `building.project.organization`) purely to support two *different*
  organizations coincidentally importing identically-named building
  developments — an edge case with no real-world instance in this
  system. Global uniqueness is the more literal reading of the
  release's own instruction ("the complete physical identifier must
  always remain unique") and was verified as correct for the single
  real organization; noted here since it was discovered as a genuine
  edge case while cleaning up leftover multi-organization scratch data
  in the local dev database during this session's own live-HTTP
  validation (not a defect found in delivered code — no test exercises
  two organizations importing the same building family).
- **A38. The two multi-family source PDFs (the site plan and the
  apartment-typology table) are registered as one `Drawing` row per
  project they cover, all sharing the same underlying `Document`.**
  Neither file belongs to a single project — the site plan shows all 5
  developments, and the typology table is the actual import source for
  all of them. Rather than picking one arbitrary "owning" project (which
  would misrepresent the document's real scope) or leaving `Drawing
  .project` nullable (which would break the existing project-isolation
  convention used everywhere else), each covered project gets its own
  `Drawing` row pointing at the identical `Document`/`DocumentVersion` —
  the file is stored once; only the lightweight metadata row is
  duplicated per project, matching how the existing snapshot/report
  mechanism already treats one physical file as reusable across
  multiple logical references.
- **A39. No graphical/spatial floor-plan view was built.** The release
  says to provide one "where a visual topology can be derived
  reliably." The only per-unit data available is a letter-grid table
  (floor + apartment letter + measurements) with no real X/Y
  coordinates — rendering a schematic floor plan from that would mean
  inventing a spatial layout the source doesn't actually specify,
  which the release's own labeling requirement ("clearly labeled as an
  operational schematic, never as the original approved architectural
  drawing") exists precisely to guard against. Skipping it is the more
  conservative reading; the real architectural PDFs remain available
  and linked per building/project instead.
- **A35. `import_buildings_and_units --organization <name>` was added
  as an explicit, optional flag (defaulting to `Organization.objects
  .first()`, matching every other bootstrap command's existing
  convention) after this session's own live validation showed
  `.first()` is non-deterministic once more than one `Organization` row
  exists (Django orders by PK — a random UUID — when no explicit
  ordering is defined).** This mirrors the `--org-name` option already
  present on `seed_delivery_demo_data`, not a new pattern.
- **A40. "Before final order approval, any excess must either be
  assigned or confirmed as spare" is surfaced as a visible warning
  banner on the PO detail page, not a hard workflow block.** This
  codebase has no existing "approve this PurchaseOrder" action/status
  transition to attach a hard gate to — `PurchaseOrder.approval_status`
  is a plain field, and the actual cross-department handoff
  (`purchasing_to_finance`) is a generic, reusable mechanism
  (`apps.workflow.services.create_handoff`) shared by every gate in the
  system. Hard-coding a domain-specific allocation check into that
  generic function would couple it to procurement specifically; adding
  a bespoke "approve PO" workflow action that doesn't otherwise exist
  is out of this release's scope. The warning is real and visible
  (`po_detail` computes `has_unresolved_excess` from the same
  `line_allocation_summary` the allocation screen uses), just not a
  server-side block on a transition this system doesn't yet model.
- **A41. `apps.receiving.services.post_receipt_line`'s new
  `purchased_spare` parameter is always explicitly supplied by the
  caller — never auto-detected from the manifest line.** Tracing a
  `ReceiptLine` back to a `PurchasedSpare` would require walking
  `ManifestLine.sources -> PurchaseOrderLine -> purchased_spares`,
  which is possible but would guess *which* spare confirmation applies
  when a line has more than one. Requiring the receiving user to
  explicitly pick the spare confirmation they're receiving against
  keeps this an authorized, deliberate act — consistent with "never
  alter inventory... except via valid posted movements" and "a
  photo/scan/link must never authorize a consequential action by
  itself."
- **A42. Field-issue closure authority reuses `can_override_gates`
  rather than a new single-purpose permission flag.** The release asks
  for "configurable roles and permissions, never hard-coded names" —
  it does not require a dedicated "can verify field issues" flag
  distinct from the senior-authorization concept already threaded
  through every other approval-style action in this release (drawing
  approval, purchased-spare confirmation). Reusing it keeps one
  permission concept instead of proliferating near-identical boolean
  role flags; a future policy wanting a narrower, issue-specific
  permission can add one without changing the enforcement pattern.
- **A43. "The verifier must be independent from the worker when
  configured by policy" is satisfied at its stated minimum, not with a
  hard `verifier != worker` block.** The release's own wording is
  "at minimum, the worker must not automatically gain closure
  permission merely by completing the work" — `verify_and_close_issue`
  re-checks `can_override_gates` unconditionally regardless of who
  performed the correction, so a plain worker without that permission
  can never self-close. It does not additionally forbid a
  permission-holder from closing their own work, since the spec frames
  strict independence as policy-configurable ("when configured by
  policy"), not a universal hard rule, and no such policy toggle was
  otherwise specified. A future stricter policy could add a
  `verifier_id != correction_performed_by_id` check without changing
  this design.
- **A44. `FieldIssue.category`/`item` reuse the existing configurable-
  taxonomy pattern (`IssueCategory`, mirroring `DocumentType`) and the
  existing `items.Item` catalog, rather than a hard-coded choices
  list.** The release names example categories (windows, doors,
  kitchens, ...) but the system already has an established convention
  for "configurable taxonomy, never hard-coded into business logic" —
  reusing it here avoids a second taxonomy mechanism.
- **A45. "Approved reference installation" requires supervisor
  sign-off first, as a hard precondition, not merely a suggested
  order.** The release lists "supervisor sign-off" and "whether the
  result becomes an approved reference installation" as separate
  captured facts without stating their exact ordering — requiring
  sign-off before reference approval is the conservative reading (a
  reference example for future teams should carry a supervisor's
  endorsement, not just the trainer's own say-so), and both actions
  reuse the same `can_override_gates` senior-authorization permission
  already established for drawing approval and purchased-spare
  confirmation.
- **A46. `TrainingParticipantAcknowledgement` is recorded by the
  participant themselves calling `acknowledge_participation`, not by
  the trainer/supervisor on their behalf.** The release says
  "participant acknowledgement" without specifying who records it;
  requiring the participant's own action (enforced by checking
  `session.participants.filter(pk=participant.pk)`) is the more
  literal reading of "acknowledgement" and avoids a supervisor being
  able to silently mark someone as having acknowledged something they
  didn't.
- **A47. "Purpose" (6 fixed values: construction progress, quality
  control, training/reference, pre-delivery final, final handover/
  delivery, reinspection) is a fixed `TextChoices` enum, while
  "category" (windows, doors, kitchens, ...) is a fully configurable
  taxonomy (`WalkthroughCategory`).** These are different axes with
  different natures: purpose drives concrete, distinct business logic
  (only pre-delivery/final-handover purposes trigger the delivery-
  readiness control) and the release names exactly 6 of them with no
  "configurable future purposes" language — unlike category, which the
  release explicitly calls out as extensible ("configurable future
  categories"). Modeling purpose as a fixed enum and category as
  configurable data matches how each was actually described.
- **A48. A "selected group of apartments" walkthrough uses a separate
  `units` M2M field, distinct from the single `unit` FK.** A
  walkthrough scoped to exactly one apartment sets `unit`; one spanning
  a curated subset (e.g. "recheck these 3 units that reported issues")
  sets `units` instead. `WalkthroughItem.unit` is independently
  nullable so a multi-unit walkthrough's items can each record which
  specific unit they belong to — the release's own item field list
  doesn't include a `unit` field, but without one, a multi-unit
  walkthrough's checklist would have no way to attribute an item to a
  specific apartment, which would silently lose information the
  release explicitly asks this scope option to support.
- **A49. Efficient "sequential walkthroughs" are a service-layer
  convenience (`create_next_sequential_walkthrough`) that copies
  building/floor/category/purpose/inspector into a new `Walkthrough`
  for the next unit — not one `Walkthrough` row spanning an entire
  floor's apartments.** Keeping one walkthrough per apartment (with
  `units` reserved for the deliberate "selected group" case, A48)
  keeps `WalkthroughItem`'s cardinality simple and its delivery-
  readiness computation scoped to exactly the apartment it's actually
  about, while still satisfying "without repeatedly re-entering the
  same information" as a UX/data-copying convenience.
- **A50. Blocking-defect resolution is judged by the linked
  `FieldIssue.status`, not a separate boolean the walkthrough tracks
  itself.** `delivery_readiness_summary` treats a `WalkthroughItem
  .is_blocking_defect=True` item as still blocking unless its linked
  issue has reached `VERIFIED_CLOSED` — reusing the existing,
  already-tested closure lifecycle (which itself requires before/after
  evidence and an authorized independent verifier) rather than a
  second, weaker "resolved" flag on the walkthrough item that could
  drift out of sync with the real correction's actual state.
- **A51. The Unclassified Evidence Inbox wraps every upload in the
  same `Document`/`DocumentVersion` mechanism (SHA-256 hashing,
  duplicate detection, immutable version history) already used by
  every other upload path, rather than inventing a parallel file
  model.** `UnclassifiedEvidence` is a thin wrapper (organization,
  optional project/building for a coarse starting guess, optional
  `date_taken`, notes) around a `Document` FK — provenance (uploader,
  timestamp, original filename, checksum) is therefore established at
  upload time and is never touched again by any later classification
  action.
- **A52. Classification is a separate, generic, reassignable pointer
  (`EvidenceClassification`, content_type/object_id) layered on top of
  the untouched upload, not a field on `UnclassifiedEvidence` itself.**
  This lets one piece of evidence be classified, reclassified, or even
  linked to more than one record over time (e.g. a photo classified to
  a building and, separately, to the specific field issue it
  documents) without ever losing the history of where it was
  previously thought to belong — reclassification marks the old row
  `is_active=False` and links `superseded_by`, mirroring the
  `OrderLineAllocation.reassigned_from` / `Drawing.supersedes`
  versioned-immutable-row pattern already used elsewhere in this
  release.
- **A53. `classification_status` is derived from whether the
  classified target is a "leaf" record (unit, walkthrough item,
  walkthrough, field issue, training session, installation record,
  inspection record, purchase order line) versus a coarser one
  (building, floor, project, supplier, ...).** The release describes
  three states — unclassified, partially classified, classified —
  without defining the exact boundary; treating "classified" as
  "resolved to a specific, individually-actionable record" and
  "partially classified" as "narrowed to a general area but not yet
  pinned to one" is the most useful reading for a reviewer scanning
  the inbox to see what still needs finishing.
- **A54. The classification target list (`CLASSIFIABLE_TARGETS`) is a
  fixed, explicit list of (app_label, model) pairs covering every
  target type the release names, rather than every model in the
  system.** This avoids accidentally exposing classification against
  models that were never intended as evidence targets (e.g. internal
  workflow/audit rows) while still covering building/floor/unit,
  walkthrough/walkthrough item, training session, field issue,
  product, supplier, purchase order line, shipment/container, and
  installation/inspection records — extending it to a new target type
  later is a one-line addition, not a redesign.
- **A55. Classifying evidence to a target enforces the same
  organization-isolation guarantee as every other cross-app reference
  in this release, via the shared `apps.workflow.services
  .resolve_organization()` resolver rather than a bespoke,
  evidence-inbox-specific check.** `resolve_organization()` was
  extended with `shipment`/`purchase_order`/`walkthrough` fallback
  chains (needed for `Container`, `PurchaseOrderLine`, and
  `WalkthroughItem`, none of which carry a direct organization field)
  so this one shared function stays the single place cross-cutting
  isolation logic lives, instead of duplicating it per classifiable
  target type.
- **A56. Of the two source PDFs beyond the unit-typology spreadsheet
  and site-plan legend, only "PALMERA - PLANOS 13.11.2025.pdf" is an
  actual per-unit-type architectural floor plan.** Direct visual
  inspection of all 16 pages (rendered at 150dpi) confirms sheets
  H-05/H-06/H-07/H-08 are genuinely labeled "APARTAMENTO TIPO A/B/C/D"
  with furnished/dimensioned room layouts, and sheet H-09 is a real,
  complete APT-number-to-tipo occupancy table for all 104 units
  (cross-checked against — and found consistent with —
  `apps.projects.building_source_data.palmera_rows()`'s already-
  imported letter assignments). ARENA T1, MARE B, SOLE, SOLE PH, and
  SOLE 26 have no equivalent per-unit-type drawing anywhere in the
  supplied source material — only the site-plan legend and the
  typology spreadsheet, neither of which shows room layouts. Per the
  release's explicit instruction not to invent a template where the
  source is insufficient, every template slot for those 5 families is
  seeded as `Status.MISSING_SOURCE` with no zones, never a
  plausible-looking but fabricated room boundary.
- **A57. `UnitPlanTemplate` is resolved deterministically from
  family + unit-type-letter + floor-variant, computed from data already
  imported and verified in M1** (`Unit.apartment_letter`,
  `Floor.level`, `Unit.is_penthouse`) **— never re-derived from the
  family unit-spec tuples a second time.** `floor_variant` is `ALL_FLOORS`
  for PALMERA (its real Tipo A-D sheets are letter-only, not
  floor-specific — confirmed by H-04's typical-floor plan showing the
  same 4 letters repeating on every residential level),
  `PENTHOUSE_DUPLEX` for any `is_penthouse=True` unit (SOLE PH's
  floor "4-5" units), `FIRST_FLOOR` for floor level 1 otherwise, and
  `UPPER_FLOOR` for every other level — matching each family's real,
  already-transcribed terrace/footprint differences (A32-era source
  data) without adding a second parallel data source.
- **A58. The "AI-proposed" PALMERA room zones (rectangular, not
  pixel-perfect polygons) are real room identifications from the
  actual furnished sub-view of each Tipo sheet, cropped and rendered at
  150dpi, with rectangle boundaries visually estimated from that real
  image — not fabricated room existence or count.** Every such zone is
  seeded `validation_state=NEEDS_REVIEW`, `source_confidence=LOW`, and
  the template itself stays `Status.DRAFT` (never `APPROVED`) until an
  authorized user validates/approves it through the admin mapping
  tool — satisfying "automated extraction is Draft by default" without
  ever presenting a derived crop as an architect-approved drawing.
- **A59. `UnitPlanTemplate`/`PlanZone` reuse the exact
  versioned-immutable-row pattern already established for
  `Drawing`/`OrderLineAllocation`/`EvidenceClassification`
  (`supersedes` self-FK, `is_current`/`is_active` flag, never an
  in-place edit).** A `UniqueConstraint` on (organization, code)
  scoped to `is_current=True` (mirroring `UnitPlanAssignment`'s
  one-current-assignment-per-unit constraint) allows a superseded
  template to keep the same human-meaningful code as its replacement
  without a collision — the same technique used for reassigned
  allocations and reclassified evidence.
- **A60. Superseding a template automatically moves every unit
  currently assigned to it onto the new revision — but never touches
  any FieldIssue/WalkthroughItem/InstallationRecord/InspectionRecord
  already created against the old one, since those store their own
  direct FK.** The alternative (leaving units pointed at a template
  now marked `SUPERSEDED`) would mean the interactive viewer kept
  showing an explicitly-replaced plan by default, which defeats the
  purpose of uploading an improved/as-built drawing — while historical
  traceability is guaranteed structurally by the direct FK on each
  historical record, not by anything unit-assignment-related.
- **A61. Editing an existing zone's shape/name/type through the admin
  tool also never mutates the row in place — it always creates a new
  `PlanZone` (`supersedes`) and marks the old one `is_active=False`,
  the same as a template-level supersede.** Without this, correcting a
  room boundary after a `FieldIssue` had already been created against
  it would silently move that issue's "exact source-location
  reference" out from under it — verified by a dedicated test that an
  issue created against a zone keeps pointing at that zone's original
  shape after the zone is later edited.
- **A62. "Photograph" and "note" actions from a selected room reuse the
  Unclassified Evidence Inbox's existing generic classification
  mechanism (`unitplans.planzone` added to `CLASSIFIABLE_TARGETS`,
  A54) rather than a new evidence model, and "installation record" /
  "inspection item" get `plan_template`/`plan_zone` FKs for
  traceability/display but no dedicated "quick create from a room"
  flow.** Both `InstallationRecord` and `InspectionRecord` are already
  multi-step domain workflows with their own real preconditions
  (`project_receipt`/`delivery_line`, an existing `InstallationRecord`
  to inspect) that don't fit a one-click "create from a room click"
  the way `FieldIssue` and `WalkthroughItem` do — the release's own
  example list, section "ISSUE CREATION FROM PLAN," only fully details
  the field-issue case. A bare "note not tied to a photo or issue" is
  not modeled as a new object — free text already has a home on the
  issue description or the evidence's own `notes` field.
- **A63. The 4 real PALMERA derived-crop images live at
  `imports/buildings/derived_plans/*.jpg`, alongside the existing
  source PDFs — and, like those PDFs, that path is gitignored.** This
  matches the release's own established precedent exactly:
  `import_buildings_and_units` and `register_source_drawings` already
  depend on `imports/buildings/*.pdf` existing locally but untracked;
  `seed_unit_plan_templates` depends on these 4 derived crops the same
  way. The actual operational copy each `UnitPlanTemplate` serves from
  lives in `protected_documents/` (via the normal `Document`/
  `DocumentVersion` upload mechanism, itself already gitignored) — the
  `imports/` copy is only ever the one-time seed input, not a second
  source of truth.
