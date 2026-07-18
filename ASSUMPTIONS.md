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
