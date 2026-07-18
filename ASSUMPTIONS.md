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
