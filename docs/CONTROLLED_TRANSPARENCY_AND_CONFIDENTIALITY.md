# Controlled Transparency, Commercial Confidentiality & Authorization Foundation

This document is the consolidated policy/glossary reference for the
governance foundation added in this release (`apps.governance` +
`apps.procurement` commercial layers + `apps.audit` evidence bundles).
It exists alongside — and never replaces — `docs/architecture-decisions.md`
(ADR-041), `docs/SECURITY.md`, `docs/KNOWN_LIMITATIONS.md`, and
`ASSUMPTIONS.md` (A64–A80+), which record the same material in this
project's usual living-documentation locations.

## 1. Controlled Transparency vs. Controlled Confidentiality

These are not opposites — both require real verification, provenance,
permissions, and auditability (`apps.governance.models.VisibilityMode`):

- **CONTROLLED_TRANSPARENCY** — the buyer and commercial parties have
  agreed to reveal more of the supply chain. Still governed by the same
  classification/capability system; "transparent" does not mean
  "unauthenticated" or "unaudited."
- **CONTROLLED_CONFIDENTIALITY** — the configured default for
  China-managed DT Beach procurement (`ProcurementPackage.visibility_mode`
  defaults to this). Protects upstream factories, production-site
  addresses, supplier networks, source contacts, original costs, internal
  quotations, markup, margin, rebates, fee arrangements, and internal
  financial structures, while still giving the buyer real, verifiable
  operational information (a `VerificationAssertion` such as "Production
  verified at an authorized site").

Visibility mode is versioned (`visibility_mode_version`) and, once a
package is frozen, can only change through an approved
`governance.ChangeRequest` — never a direct field edit.

## 2. Party vs. Role vs. Capability glossary

| Concept | Model | What it answers |
|---|---|---|
| **Party** | `governance.Party` | *Who* is involved — an organization, an individual, or an operational site. May wrap an existing `accounts.Organization` (e.g. the buyer) or `procurement.Supplier` (e.g. a factory). Never permanently labeled "Factory"/"Trader"/"Seller" in code. |
| **Role Assignment** | `governance.RoleAssignment` | *What capacity* a Party holds, scoped to a package/project/organization context/shipment, with `effective_from`/`effective_until`, `status`, and versioned supersession. The same Party can hold different roles in different packages (proven by `tests/test_governance.py`). |
| **Capability** | `governance.CapabilityGrant` + `ROLE_DEFAULT_CAPABILITIES` | *What action* is actually allowed. Low-risk VIEW-type capabilities are implied by certain roles by default; every APPROVE_*/AUTHORIZE_*/EXPORT_*/VIEW_PRIVILEGED_AUDIT-type capability is **never** role-implied — it requires an explicit, auditable `CapabilityGrant`. |

Known package-scoped role codes (`governance.models.KNOWN_ROLE_CODES`,
free-text `SlugField` — new roles never require a schema migration):
`buyer`, `buyer_approver`, `seller_of_record`, `exporter_of_record`,
`china_procurement_operator`, `production_factory`, `production_site`,
`logistics_operator`, `consolidation_warehouse`, `quality_operator`,
`inspector`, `laboratory`, `technical_authority`, `non_financial_approver`,
`data_entry_clerk`, `commission_agent`, `installer`, `importer_of_record`,
`consignee`, `notify_party`, `customs_broker`, `external_service_provider`,
`finance_partner`.

Capability codes (`governance.models.ALL_CAPABILITY_CODES`):
`VIEW_FACTORY_IDENTITY`, `VIEW_FACTORY_ADDRESS`, `VIEW_FACTORY_CONTACT`,
`VIEW_FACTORY_QUOTE`, `VIEW_ORIGIN_COST`, `VIEW_INTERNAL_COST_COMPONENTS`,
`VIEW_MARKUP`, `VIEW_MARGIN`, `VIEW_CLIENT_QUOTE`, `VIEW_MONETIZATION`,
`VIEW_RESTRICTED_FINANCE`, `CREATE_EVIDENCE`, `VERIFY_EVIDENCE`,
`CREATE_COMMERCIAL_DOCUMENT`, `APPROVE_CLIENT_QUOTE`,
`APPROVE_TECHNICAL_SPEC`, `APPROVE_PAYMENT_ACTION`, `APPROVE_GATE`,
`AUTHORIZE_EXCEPTION`, `APPROVE_ROLE_CHANGE`, `APPROVE_VISIBILITY_CHANGE`,
`AUTHORIZE_DISCLOSURE`, `EXPORT_COMMERCIAL_DATA`, `VIEW_PRIVILEGED_AUDIT`,
`RESPOND_TO_CLAIM`, `VERIFY_CORRECTIVE_WORK`.

### Capability matrix (role-implied defaults only — see `ROLE_DEFAULT_CAPABILITIES`)

| Role | Default capabilities |
|---|---|
| `china_procurement_operator` | VIEW_FACTORY_IDENTITY, VIEW_FACTORY_ADDRESS, VIEW_FACTORY_CONTACT, VIEW_FACTORY_QUOTE, VIEW_ORIGIN_COST, VIEW_INTERNAL_COST_COMPONENTS, VIEW_MARKUP, VIEW_MARGIN, VIEW_MONETIZATION, CREATE_COMMERCIAL_DOCUMENT, CREATE_EVIDENCE |
| `production_factory` / `production_site` / `installer` | CREATE_EVIDENCE |
| `quality_operator` / `inspector` / `laboratory` | CREATE_EVIDENCE, VERIFY_EVIDENCE |
| `buyer` / `buyer_approver` / `seller_of_record` / `technical_authority` | VIEW_CLIENT_QUOTE |

Everything else (APPROVE_CLIENT_QUOTE, APPROVE_GATE, AUTHORIZE_DISCLOSURE,
APPROVE_ROLE_CHANGE, APPROVE_VISIBILITY_CHANGE, EXPORT_COMMERCIAL_DATA,
VIEW_PRIVILEGED_AUDIT, ...) is granted only via an explicit `CapabilityGrant`.

## 3. Separation-of-duties policy

Enforced structurally, not by convention:

- **Evidence uploader vs. verifier** — `apps.audit.services.verify_evidence_item`
  raises if `item.uploaded_by_id == verifying_user.id`, regardless of
  capability.
- **Commercial document preparer vs. client-quote approver** —
  `CREATE_COMMERCIAL_DOCUMENT` and `APPROVE_CLIENT_QUOTE` are granted
  independently; a quote's preparer has no path to approving their own
  work unless a second, explicit `CapabilityGrant` is made.
- **Change requester vs. change approver** — `request_change` only
  records the request and places the package on hold; `approve_change_request`
  requires a field-specific capability (`APPROVE_VISIBILITY_CHANGE` for
  visibility mode, `APPROVE_ROLE_CHANGE` for critical roles).
- **Exception requester vs. approver** — reuses the existing
  `apps.workflow.GateOverride`/`can_override_gates` mechanism unchanged.

## 4. Classification glossary

`governance.models.Classification` (central, reusable across records,
fields, documents, evidence, and derived artifacts):

`CHINA_INTERNAL`, `SOURCE_PRIVATE`, `TRADING_COMPANY_CONFIDENTIAL`,
`SUPPLIER_SHARED`, `CLIENT_PROJECT`, `CLIENT_SHARED`, `OPERATIONAL_SHARED`
(the default for every pre-existing, non-package `Document`/`Quotation`/
`PurchaseOrder` row — preserves prior behavior exactly), `RESTRICTED_FINANCE`,
`LEGAL_REQUIRED`.

A derived resource (translation, summary, redaction, export) may inherit
the same classification or become **more** restrictive automatically; it
can never become **less** restrictive without the caller separately
holding `AUTHORIZE_DISCLOSURE` — enforced in
`governance.services.create_derived_artifact` via `is_less_restrictive()`.

## 5. Commercial-layer policy

Six genuinely distinct objects, never one record with hidden columns:

1. **Factory RFQ** (`procurement.FactoryRFQ`) — a request, before any price exists.
2. **Factory Quote** — reuses `procurement.Quotation`/`QuotationLine`
   directly (it already modeled exactly this), scoped to a package with
   `classification=SOURCE_PRIVATE`.
3. **Internal Commercial Sheet** (`procurement.InternalCommercialSheet`) —
   factory price, full landed-cost breakdown, markup method/value,
   `TRADING_COMPANY_CONFIDENTIAL`. Never visible to a client-scoped view.
4. **Client Quote** (`procurement.ClientQuote`) — only approved
   client-facing fields (visible seller, product, quantity, sell price,
   terms). Structurally cannot expose factory identity/cost/markup/margin
   because those fields simply do not exist on this model.
5. **Client Purchase Order** — reuses `procurement.PurchaseOrder`
   (`po_kind=CLIENT`).
6. **Upstream Factory Purchase Order** — reuses `procurement.PurchaseOrder`
   (`po_kind=UPSTREAM_FACTORY`), `classification=SOURCE_PRIVATE`, `supplier`
   pointing at the hidden factory's own `Supplier` row.

## 6. Client-safe Party references & verification assertions

`procurement.services.client_safe_site_alias(party, package)` returns a
**package-scoped** alias (e.g. "Verified Production Site 1") — never the
source Party's real name, never a globally stable id (the same real
factory may get a different alias number in a different package).

`procurement.VerificationAssertion` preserves internally: assertion code,
source `EvidenceBundle` (which must already be `VERIFIED`, never merely
uploaded — enforced in `create_verification_assertion`), source Party,
verifier, timestamp, validity, classification, version, supersession, and
revocation. The buyer only ever sees `client_visible_wording` (e.g.
"Production verified at an authorized site.") — never the source
identity or documents.

## 7. Disclosure Grant procedure

`governance.DisclosureGrant` records a **partial**, explicit,
capability-gated (`AUTHORIZE_DISCLOSURE`), revocable release of specific
field codes (e.g. `["manufacturer_name"]`) from a hidden source Party to
a recipient organization/package. Revealing the manufacturer name never
automatically reveals address, cost, markup, margin, negotiation, or the
upstream PO — those require their own separate grants.
`governance.services.disclosed_fields(package, recipient_organization)`
computes the live union of currently-active (not expired, not revoked)
grants. Revocation (`revoke_disclosure_grant`) prevents future access but
never deletes the historical row — `DisclosureGrant.objects.filter(pk=...)`
still resolves after revocation.

## 8. Evidence Object / Evidence Bundle policy

`apps.audit.EvidenceBundle`/`EvidenceItem` extend the existing generic
evidence architecture (`Attachment`, reused as-is for simple cases).
Uploading an `EvidenceItem` never sets it beyond `review_status=pending`;
a bundle only reaches `Status.VERIFIED` once every requirement (minimum
count, required types, minimum review state) is met via
`verify_evidence_item`, which enforces uploader/verifier separation and
capability. No confidence score is ever assigned — this system has no
real, documented basis for computing one (per the release's own
instruction).

## 9. Derived Artifact & original-preservation policy

Original/source documents are immutable and are never falsified,
overwritten, or silently redacted (unchanged from the system's
pre-existing `Document`/`DocumentVersion` guarantee). `governance.DerivedArtifact`
preserves source object, version, hash (`_hash_projection`, sha256 of the
authorized projection actually used), source classification, the exact
authorized field projection, artifact type/language/author-or-model,
policy version, classification, review status, staleness
(`mark_stale_if_source_changed`), and supersession
(`supersede_derived_artifact` — never edits the original row).

## 10. AI/RAG authorization-before-retrieval policy

Required order, enforced structurally in
`governance.services.create_derived_artifact`:

```
authorize (capability + classification check)
  -> permitted resource retrieval
  -> permitted field projection (project_authorized_fields)
  -> optional transformation (transform_fn — translation/summary/AI)
```

The caller-supplied `transform_fn` (standing in for a real
translation/AI provider) is invoked **only** with the already-authorized
projection dict — it has no access to the full source object at all, so
"retrieve full confidential resource → send to AI → hide restricted
content afterward" is not merely discouraged, it is impossible given this
function signature. Proven in `tests/test_derived_artifacts.py` via a
spying test double that records exactly what it received.

No vector index/RAG/AI infrastructure exists yet in this system — this
release intentionally does not build speculative infrastructure for it.
The **contract** a future integration must honor is exactly the function
signature above: authorize first, retrieve only the authorized
projection, and pass only that projection onward. Any future search/RAG
index must be filtered by the same organization/project/package/
classification/capability boundaries before a document ever enters it.

### Future Internationalization security contract

This release does not implement UI internationalization. It establishes
the contract the future Internationalization Foundation must honor:

- Translation inherits source confidentiality (a `DerivedArtifact` of
  `artifact_type=TRANSLATION` carries the same `source_classification`
  rule as any other derived artifact).
- Locale/language never changes permissions — proven by
  `test_language_change_does_not_alter_permissions`.
- A translation service receives only the authorized projection, never
  the full source (same mechanism as section 10 above).
- UI string catalogs must never contain private business data (factory
  names, costs, etc.) — they are static, translated interface labels
  only.
- Changing Spanish/English/Chinese must never change tenancy, roles,
  fields, authority, or timezone.

## 11. Exception policy

Reuses `apps.workflow.GateOverride` (the existing gate/override
mechanism) directly — no parallel exception system was built. Hardened
this release with `expires_at`/`revoked_at`/`revoked_by` and
`is_currently_active()`. An exception never flips the underlying rule to
"passed" — it remains an explicit, immutable, auditable record
(requester, authority used, reason, before/after state) layered on top of
a rule that is still failing.

## 12. Configurable workflow policy

No universal A1–A6 workflow was introduced. `ProcurementPackage.Status`
(draft/active/frozen/closed) is its own distinct, disjoint state machine
— proven disjoint from `FieldIssue.Status` by
`tests/test_workflow_template_preservation.py`. The DT Beach operational
supply chain (REQUIRED → ... → ACCEPTED) and the field-issue lifecycle
(REPORTED → ... → VERIFIED_CLOSED) are untouched.

## 13. Change Request procedure

`governance.ChangeRequest` — created only against an already-frozen
package (`ProcurementPackage.is_frozen`); creation immediately places the
package `is_on_hold=True`. Approval requires a field-specific capability
(`APPROVE_VISIBILITY_CHANGE` for `visibility_mode`, `APPROVE_ROLE_CHANGE`
for seller/exporter/China-operator/factory/site) and releases the hold;
rejection also releases the hold without applying the change. Every
decision is immutable history (`decided_by`, `decided_at`,
`decision_comment`).

## 14. Risk flags (foundation only)

`governance.RiskFlag` — `STANDARD`/`CONTROLLED_OPAQUE`/`HIGH_RISK`,
free-text `indicator_codes`. Records risk and may trigger review/hold;
never declares legality, approves an opaque transaction, or replaces
legal/compliance review. Maidan, Shuangqing, double-clearance, and full
customs workflows are explicitly out of scope (section 15 below).

## 15. Explicitly out of scope (documented, not built)

Full A1–A6 procurement gates; payment legs/settlement events/
monetization streams/export rebate accounting; bank/factoring
integration; credit insurance; escrow or custody of money; complete
claim-governance expansion beyond applying this release's classification/
capability system to the existing `apps.claims` module; blockchain
anchoring; GPS/data-loggers; physical sample custody; Maidan/Shuangqing/
double-clearance workflows; complete customs/import workflow; Asset
Passport / Property Digital Passport; native mobile application; complex
offline sync; 24/7 concierge; full automatic catalog translation; the
complete Internationalization Foundation (only its security contract,
section 10, is established here).

## 16. Administrator guidance

- **Party / Role Assignment / Capability administration**:
  `/gobernanza/partes/` (create/view Parties, view their role
  assignments and member users). Gated by `can_override_gates`.
- **Package administration**: `/compras/paquetes/` → package detail
  page hosts factory-quote/internal-sheet/client-quote creation, freeze,
  change requests, and disclosure grants, each conditionally rendered by
  the viewer's own capabilities (never hidden only by CSS).
- **Privileged audit / access explanation**: `/gobernanza/auditoria-privilegiada/`
  — read-only log of every role assignment, capability grant, disclosure
  grant/revocation, visibility-mode change, package freeze, change
  request, verification assertion, evidence verification, and privileged
  access grant/denial. Gated by `can_override_gates`; never exposed to
  ordinary users. No user impersonation exists or was added.
- **Seeding a live scenario**: `python manage.py seed_confidentiality_demo`
  (idempotent) creates the DT Beach / China Trading Co / Edison / hidden
  factory / client scenario used for this release's live validation.
