# ADR 0002: MarketMatch Evidence Core V1

- Status: Accepted contract foundation
- Date: 2026-07-20
- Scope: Pure evidence contracts, Authority integration, and read-only compatibility adapters

## Context and repository evidence

The repository's accepted MarketMatch architecture record is ADR 0001, which
defines a pure Authority and Commercial Visibility Kernel, exact scope matching,
flat-only field projection, safe decision summaries, and restrictive derived-
artifact visibility inheritance. The broader named MarketMatch Master Context,
Foundation and Core Roadmap, Platform Vision and Architecture Blueprint, and
separate capability/current-state documents are not present in this repository
snapshot. This decision therefore relies on ADR 0001 as approved intent and on
the following code and tests as evidence of current behavior; it does not invent
the absent documents' contents.

Current evidence-like behavior is distributed rather than represented by a
single evidence registry:

- `Document` rows have UUID identifiers, current textual content, immutable
  `DocumentVersion` snapshots, direct username ownership, UTC-naive database
  `created_at`/`updated_at` fields, and limited source-email provenance.
  Document content lives in SQLite text columns. Ownership is enforced by the
  current Document helpers and Library queries.
- MarketMatch Capture sends raw WAV to the cookie-only local STT route. The
  route holds request bytes only long enough for isolated local transcription;
  it does not persist audio. Capture photos and videos remain in browser memory
  and are explicitly not uploaded or saved. An explicit save creates a normal
  owner-stamped markdown Document containing textual notes, transcript, media
  names/captions, and the fact that visual media was not persisted. Capture
  history is the existing owner-filtered Documents Library query.
- Local analysis receives bounded transcript text, invokes only a configured
  local endpoint, validates a conservative structured draft, returns it to the
  browser, and does not make that draft an evidence conclusion or persist it as
  an Evidence record.
- General uploads live under the configured uploads directory and are indexed
  by `uploads.json`. Upload processing computes SHA-256 from the exact upload
  stream and performs owner-qualified content deduplication. The existing
  metadata row stores an upload ID, path, MIME type, size, names, hash,
  timestamps, client IP, and owner. This remains an upload subsystem, not an
  Evidence registry.
- Gallery metadata has an optional indexed SHA-256 `file_hash`; no general
  Document digest exists.
- The MarketMatch original-media observer counts and hashes one bounded exact
  byte stream. The original-media attestation validates a closed canonical
  declaration with SHA-256 and byte size, while explicitly not proving issuer
  identity, durable media, authorization, or byte immutability. STT input and
  artifact manifest/payload contracts also use SHA-256. SHA-256 is therefore
  the repository-approved V1 evidence digest algorithm.
- Existing application logs and event dispatch are operational mechanisms, not
  a durable evidence chain-of-custody ledger.

Database startup migrations are direct, historically accumulated schema
adjustments. Current Documents and Capture do not require a durable Evidence
registry to preserve their behavior, and existing records cannot all be mapped
to byte integrity or future organization/project scopes without guessing.
Persistence is therefore unsafe and unnecessary for this bounded V1.

## Decision

Add `src/marketmatch_evidence.py`, a pure importable Evidence Core, and
`src/marketmatch_evidence_compat.py`, a read-only adapter for current structures.
No route imports either module in V1. No endpoint, UI, database model, migration,
runtime data, binary storage behavior, authentication rule, or ownership check
changes.

Evidence records an artifact, observation, statement, measurement, media object,
document, external reference, or system result. Evidence is not a conclusion.
The contracts contain no field for truth, legal acceptance, liability, blame,
commercial fault, or moral responsibility. `SUPPORTS` and `CONTRADICTS` are
directional relationship assertions only and do not determine truth.

### Contract and policy version

The contract identifier is `marketmatch-evidence-v1`; the relation/attestation
policy identifier is `marketmatch-evidence-policy-v1`. They are stable,
language-neutral codes. V1 rejects every other contract version, including an
unknown major version. Audit and derivation structures retain the versions that
governed their inputs. This is explicit versioning, not a generic plugin system.

### Evidence identity versus digest

An Evidence ID uses the bounded `ev1:<namespace>:<opaque-reference>` form. It is
ASCII, path-safe, control-free, at most 160 bytes, and rejects sensitive token-
like components. Bundle and attestation IDs use distinct `bundle1:` and `att1:`
prefixes. A bare SHA-256 digest is not a valid Evidence ID.

Evidence identity and content digest have different meanings. Separate Evidence
records may share one digest while preserving distinct acquisition events,
provenance, owners, scopes, policies, or business contexts. Deduplication never
collapses Evidence identity.

### Evidence and media kinds

The closed Evidence kinds are `DOCUMENT`, `IMAGE`, `VIDEO`, `AUDIO`,
`TRANSCRIPT`, `OBSERVATION`, `MEASUREMENT`, `SYSTEM_RECORD`, `ANALYSIS`,
`REPORT`, and `EXTERNAL_REFERENCE`. The separate media kinds are `NONE`, `TEXT`,
`IMAGE`, `VIDEO`, `AUDIO`, `APPLICATION`, and `EXTERNAL`. Codes are not display
labels and never change with locale.

### Content integrity

The closed digest algorithm is `sha256`. Integrity bases are:

- `BYTES_VERIFIED`: constructed only by hashing exact built-in `bytes`; SHA-256,
  normalized lowercase digest, and non-negative byte length are mandatory.
- `METADATA_ONLY`: records an optional declared SHA-256 and byte length without
  claiming the adapter observed those bytes.
- `EXTERNAL_REFERENCE`: no byte digest or byte length is claimed.
- `NOT_AVAILABLE`: integrity data is honestly absent.

MIME type and original filename are descriptive metadata and do not affect the
digest. Weak/unknown algorithms, malformed hashes, boolean/string/negative byte
lengths, credential-bearing filenames, traversal, and control characters reject.
Original artifacts are never placed inside the contract as raw bytes.

### Provenance and timestamp semantics

`ProvenanceContext` has closed source-kind and acquisition-method codes plus
bounded optional source system, capturing Party/principal reference, capture,
received, and ingestion timestamps, external reference, original filename,
device reference, and safe note. It has no arbitrary metadata dictionary.
Credentials, bearer material, cookies, password/token assignments,
credential-bearing URLs, URL queries/fragments, traversal, and controls reject.

All supplied timestamps must be timezone-aware and normalize to UTC. Capture,
receipt, ingestion, record creation, attestation, and derived generation remain
distinct. Capture or receipt after ingestion is inconsistent and rejects.
Current `Document` UTC-naive database timestamps are normalized only in the
Document adapter because `core.database.utcnow_naive` explicitly defines their
meaning. Current upload timestamps are local-naive, so the upload adapter
requires an explicit aware ingestion time and does not guess. The pure core does
not consult the wall clock or fabricate missing times; an integrating workflow
may apply a future-time tolerance before construction. Timezone changes affect
presentation only, never identity, digest, provenance ordering, or authority.

### Evidence record

`EvidenceRecord` is frozen and contains a stable ID, closed evidence/media kind,
an imported `AuthorityScope`, optional consistent owner Party reference, content
integrity descriptor, provenance, canonical creation/ingestion time,
`ResourceClassification`, visibility policy identity/version, and optional
bounded title/description. User, Document Library, Capture, and upload sources
require an owner. The record carries no raw content or lifecycle assertion.
Deterministic metadata serialization is provided for contract exchange, but it
is not safe authorization or logging by itself.

### Relations

`EvidenceRelation` is immutable and directional: the source is the subject and
the target is the object. For example, `A DERIVED_FROM B` means A is derived from
B, and `A SUPERSEDES B` leaves B intact. Closed codes are `DERIVED_FROM`,
`SUPERSEDES`, `DUPLICATE_CONTENT_OF`, `SUPPORTS`, `CONTRADICTS`,
`REDACTED_FROM`, `TRANSLATED_FROM`, `TRANSCRIBED_FROM`, `EXTRACTED_FROM`,
`THUMBNAIL_OF`, `INCLUDED_IN`, and `ASSOCIATED_WITH`. Self-relations reject.
Canonicalization sorts relations, collapses exact duplicates, and rejects cycles
across transformation lineage relations. A relation never mutates an Evidence
record.

### Bundles

`EvidenceBundle` has a distinct ID, closed purpose, explicit nonempty member IDs,
scope, optional consistent responsible owner, creation time, and version.
Membership is sorted and duplicate IDs collapse deterministically. Unknown
members fail validation. Member validation uses Authority source-scope conflict
handling; cross-tenant/owner scope conflicts fail closed. Bundle authorization
and every member authorization are separately required before member access.
Bundle membership or a bundle attestation never verifies members and never
broadens their visibility.

Bundle purposes are `COLLECTION`, `REVIEW`, `VERIFICATION`, `EXPORT`, and
`REPORT`. Empty bundles are rejected in V1 because a purpose without any
explicit member is not evidence of completeness.

### Attestations

`EvidenceAttestation` is separate from Evidence and targets either one Evidence
ID or one bundle ID. It records a distinct attestation ID, closed type and status,
verifier Party reference, method, aware timestamp, policy version, optional
reason/note, and explicit supporting Evidence IDs. Multiple and contradictory
attestations coexist; no newest-wins mutation is performed. Verifier identity
remains a separately projectable protected field.

Types are `INTEGRITY`, `REVIEW`, `VERIFICATION`, and `COMPLETENESS`. Statuses are
`UNREVIEWED`, `VERIFIED`, `REJECTED`, `INCONCLUSIVE`, `SUPERSEDED`, and
`INVALIDATED`. Methods are `MANUAL_REVIEW`, `BYTE_DIGEST`, `SYSTEM_VALIDATION`,
and `SOURCE_CONFIRMATION`. These codes do not mean legal acceptance or blame.

### Derived artifacts

`EvidenceDerivation` is created from one or more explicitly authorized source
records. Transformations are `TRANSCRIPTION`, `TRANSLATION`, `SUMMARY`,
`ANALYSIS`, `EXTRACTION`, `REDACTION`, `THUMBNAIL`, `EXPORT`, and `REPORT`.
Generator kinds are `HUMAN`, `SYSTEM`, `LOCAL_MODEL`, and `EXTERNAL_SYSTEM`.
Generator/model identifier and version are a paired optional declaration for a
real system or external generator, are required for a declared local model, and
are omitted for a human generator; confidence is never fabricated.

The derivation retains ordered source IDs, source digest/integrity basis and
contract version, source policy/classification/visible fields, generation time,
review status, and the Authority Kernel's inherited visibility result. Empty,
missing, malformed, duplicate, or denied sources fail closed. Organization,
product, workspace, project, owner, or policy conflicts fail closed. Derived
scope cannot remove a source constraint, derived classification must equal the
most restrictive source classification, and derived policy must match source
policy provenance. A translation, transcript, summary, analysis, redaction,
thumbnail, export, or report remains a new Evidence record and never replaces an
original.

### Authority and visibility inheritance

Evidence Core imports `AuthorityScope`, `AuthorizationDecision`,
`ResourceClassification`, `SourceVisibility`, `project_authorized_fields`, and
`inherit_derived_visibility` from ADR 0001's Authority Kernel. It does not copy
the Authority scope evaluator or issue decisions. Authentic decisions are
validated by the Authority projector before use, and each decision must match
the exact Evidence resource and policy.

The required processing order remains:

1. principal resolution;
2. authoritative resource and scope validation;
3. authorization;
4. field projection;
5. Evidence access;
6. localization or translation;
7. search indexing/retrieval or RAG/AI;
8. export, sharing, notification, or delivery.

No denied or unprojected source may be silently omitted to make a derivation
succeed. Combining sources intersects visible fields, selects the most
restrictive classification, retains scope/policy provenance, and fails on
incompatibility. Locale (`es`, `en`, `zh-Hans`) and display timezone are
non-authoritative.

### Safe projection

`project_evidence_metadata` accepts an `EvidenceRecord` or mapping and an
authentic matching Authority decision. It constructs a new mapping, admits only
a closed set of known metadata fields, invokes ADR 0001's projector, and returns
only authorized scalar values. Raw bytes, transcript/document content, arbitrary
provenance dictionaries, unknown fields, and nested mutable values never cross
the boundary. V1 is explicitly flat-only; nested values are omitted, not
recursively projected. Hostile mapping failures become a fixed value-free error.

### Safe audit boundary

`build_safe_evidence_audit_summary` emits only Evidence ID/kind, action,
allow/deny, reason, policy identity/version, integrity algorithm/basis, canonical
timestamp, and optional safe correlation ID. It does not contain raw evidence,
text, transcript, filename, provenance, external reference, actor/source
identity, supplier/factory identity, address, cost, margin, credentials, cookie,
session token, or arbitrary exception data. Control/newline injection,
overlong IDs, and sensitive identifier components reject. An audit summary is an
access event summary, not a truth or chain-of-custody claim.

### Compatibility adapters

The adapters are deliberately lossy and honest:

- Current `Document` metadata becomes a `DOCUMENT`/`TEXT` Evidence reference
  with owner and timestamp, but `NOT_AVAILABLE` integrity. Content is not read.
- A current textual Capture Document is recognized only by its established
  marker and becomes a `CAPTURE` provenance record. Audio/photo/video remain
  outside because current Capture does not persist those binaries.
- Existing upload metadata can become an Evidence reference with its recorded
  SHA-256 represented as `METADATA_ONLY`. The caller supplies an aware ingestion
  time; path, client IP, and unsafe original name are not copied.
- Current validated original-media attestation and observation outputs become
  `METADATA_ONLY` descriptors. Their constructible result objects are not
  upgraded to `BYTES_VERIFIED`. A caller that has exact bytes must use
  `ContentIntegrityDescriptor.from_bytes`.

Adapters do not migrate, mutate, deduplicate, authorize, or persist current
records. Unsupported/missing owner, identifier, time, digest, MIME type, or safe
filename fails with a fixed compatibility error. Current systems remain the
source of truth.

## Generic scenarios

1. Two fictional inspectors upload the same public sample bytes in separate
   acquisition events. The Evidence IDs and provenance differ; SHA-256 matches.
2. A reviewer can see that a fictional inspection note exists and is verified,
   while filename, source identity, location, and commercial fields remain
   omitted by policy.
3. A Spanish transcript is derived from an authorized audio record. It receives
   a new Evidence ID, links `TRANSCRIBED_FROM` the audio, retains source digest
   and policy provenance, and does not replace the audio.
4. A report combining two fictional project records is denied when their
   projects differ. Omitting the denied source does not make the report valid.
5. A redacted copy is a new artifact. Its source filename remains hidden unless
   separately authorized; redaction never broadens source visibility.
6. A bundle attestation says the submitted set was reviewed. It does not verify
   each member and does not authorize a reader to open denied members.

## Current state versus target state

V1 implements importable, immutable/effectively immutable contracts; exact-byte
SHA-256 construction; structured provenance; records, relations, bundles,
attestations, and derivations; Authority visibility inheritance; flat safe
projection; safe audit summaries; deterministic serialization; and read-only
compatibility adapters. Production behavior is unchanged because no route uses
the core.

The target MarketMatch platform may later add reviewed durable registries,
chain-of-custody events, scoped workflows, search/RAG projections, shared links,
QR access, translations, reports, DT Beach behavior, sourcing/procurement,
receiving, installation/inspection, architectural comparison, and Asset
Passport. None of that target behavior is described as implemented by V1.

## Explicitly deferred and limited

- no durable Evidence registry, relation, bundle, attestation, derivation, or
  chain-of-custody event store;
- no database migration or current-record backfill;
- no binary persistence, duplication, relocation, or retention change;
- no public route, API response shape, debug endpoint, shared link, QR access,
  export, notification, search, RAG, agent, or UI integration;
- no full chain-of-custody or legal-truth determination;
- no DT Beach workflow implementation;
- no universal sourcing/procurement or A1-A6 platform workflow;
- no Asset Passport workflow;
- no policy-management registry or hierarchy inference;
- no recursive nested metadata projection;
- no automatic wall-clock future-time policy;
- no interpretation of an existing hash as byte verification without exact
  bytes at the V1 construction boundary.

## Consequences

Future services can import a small reusable Evidence foundation while current
authentication, cookie identity, privileges, Authority Kernel, Documents,
Library history, Capture textual save, memory-only media, local STT, local
analysis, media attestation, locale preferences, UI, and APIs remain intact.
Any future production or persistence integration requires a separate decision
and regression proof at that seam.
