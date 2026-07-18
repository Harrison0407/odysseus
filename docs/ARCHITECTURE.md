# Architecture

See `architecture-decisions.md` in this directory for the running log of
individual decisions and their rationale. This document is the current
snapshot.

## Style: modular monolith

One Django project (`config`), 20 Django apps under `apps/`, one
PostgreSQL database, one deployable unit. No microservices, no message
queue, no Celery/Redis — per the spec's explicit preference and the
pilot's actual scale (one remote Linux server, a handful of named users).

```
apps/
  core          abstract base models, storage adapter, shared enums
  accounts      Organization, Department, Role, UserProfile, delegation
  projects      Project, Building, Floor, Unit, Area, Milestone
  documents     DocumentType, Document, DocumentVersion, provenance chain
  procurement   Supplier, Quotation, PurchaseOrder, payments
  items         Item, aliases, assemblies, kits, packages
  shipments     Shipment, Container, BillOfLading, the dual-manifest engine
  matching      MatchRun/Candidate, Discrepancy, Waiver
  receiving     ReceivingPlan, Receipt, Inspection, ContainerClosureAct
  inventory     StorageSite/Location, InventoryLot, InventoryMovement (ledger)
  requests      MaterialRequest, PickList, Dispatch, Delivery, Installation
  cost          Currency, CostDocument, LandedCostVersion
  workflow      WorkflowStage, Handoff (submit/accept transitions)
  audit         AuditEvent, Comment, Attachment, Notification
  reports       ReportVersion, SecureShareLink, HTML snapshot generation
  tools         Tool custody
  customs       CustomsDeclaration, ConfoturList/Line
  claims        SupplierClaim (claim lifecycle, package generation)
  labels        QRLabel, QRLabelPrintEvent, QRScanEvent (QR label/scan)
  api           DRF read endpoints under /api/v1/
```

Each app owns its own models/migrations/views/templates. Cross-app
references use Django's string-based FK (`"other_app.Model"`) so apps
stay independently migratable; there is no circular hard-import between
apps at the model layer.

## Request path

Server-rendered Django templates + HTMX for incremental interactivity +
vendored Bootstrap for layout — no SPA build step, no Node toolchain.
Bootstrap and htmx are vendored as static files (`static/vendor/`) rather
than loaded from a CDN, so the application has zero runtime dependency on
external networks (spec: "must function without ... cloud dependency").

## Identity and provenance

- Every domain model inherits `apps.core.models.BaseModel`: UUID primary
  key (never a sequential int exposed externally), `created_at`/`updated_at`,
  and `created_by`.
- `DocumentFieldSource` is a generic (content-type + object-id) provenance
  record attachable to any downstream field: source value, extracted
  value, normalized value, proposed/confirmed translation, canonical
  value, confidence, and confirming user/time (core principle 4.1).

## Storage

`apps.core.storage.DocumentStorage` is a small adapter (`save`/`open`/
`delete`) around local disk today, behind `settings.DOCUMENT_STORAGE_ROOT`
— a directory outside any publicly served static/media path. Swapping to
S3-compatible storage later only requires a new adapter implementation
behind the same three methods; no model or view changes.

## Dual-manifest engine

See `OFFICIAL_VS_OPERATIONAL_MANIFEST_ANALYSIS.md` for the full rationale.
In one sentence: `BillOfLading` (official, immutable, sourced from an
uploaded document) and `ShipmentManifest(purpose=INTERNAL_OPERATIONAL_MANIFEST)`
(fully decomposed, versioned, sourced line-by-line) are separate tables,
bridged by `ManifestVariance` rows that must be explicitly classified,
never silently reconciled.

## Ledger-based inventory

`InventoryMovement` is append-only. On-hand quantity is always computed
by summing movements `to_location` minus `from_location` for a lot — see
`apps/inventory/views.py::_on_hand_by_location_and_month` (sic:
`_on_hand_by_location_and_item`). There is no `Lot.quantity_on_hand`
field to accidentally edit directly (core principle 4.5).

## Security posture

See `SECURITY.md` for the full list. Summary: session auth, CSRF,
object/organization-scoped querysets in every view (`.filter(organization=
request.user.profile.organization)`), SHA-256 on every document version,
no public Postgres port (verified in the production Docker Compose
network topology — `db` and `web` are on an `internal: true` network;
only Caddy bridges to the external network and terminates 80/443).

## What is deliberately NOT built

- No React/SPA, no Kafka, no Kubernetes, no Elasticsearch — all explicitly
  excluded by the spec.
- No hard dependency on a paid OCR/translation/LLM provider — `DocumentFieldSource`
  and `DocumentClassificationResult` model the provenance an OCR/translation
  adapter would populate, but no such adapter is wired in this delivery
  (see `KNOWN_LIMITATIONS.md`).
