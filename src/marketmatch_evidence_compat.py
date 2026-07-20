"""Read-only compatibility adapters for current evidence-like structures.

Existing Documents, Capture, uploads, media observations, and original-media
attestations remain authoritative.  These adapters neither mutate nor persist
their inputs.  A recorded digest is represented as metadata-only unless exact
bytes are supplied to :meth:`ContentIntegrityDescriptor.from_bytes`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, NoReturn

from src.marketmatch_authority import AuthorityScope, ResourceClassification
from src.marketmatch_evidence import (
    AcquisitionMethod,
    ContentIntegrityDescriptor,
    DigestAlgorithm,
    EvidenceContractError,
    EvidenceErrorCode,
    EvidenceKind,
    EvidenceRecord,
    MediaKind,
    ProvenanceContext,
    SourceKind,
)
from src.marketmatch_media_attestation import ValidatedOriginalMediaAttestation
from src.marketmatch_original_media_observation import OriginalMediaObservation


_CAPTURE_MARKERS = (
    "# MarketMatch Capture\n",
    "# MarketMatch Calls Transcript\n",
)


def _fail() -> NoReturn:
    raise EvidenceContractError(EvidenceErrorCode.INVALID_COMPATIBILITY_RECORD) from None


def _field(value: object, name: str) -> object:
    try:
        if isinstance(value, Mapping):
            return value.get(name)
        return getattr(value, name, None)
    except Exception:
        _fail()


def _aware(value: object, *, current_naive_utc: bool = False) -> datetime:
    if type(value) is not datetime:
        _fail()
    if value.tzinfo is None:
        if not current_naive_utc:
            _fail()
        value = value.replace(tzinfo=timezone.utc)
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        _fail()


def _legacy_owner(value: object) -> str:
    if type(value) is not str or not value:
        _fail()
    return f"legacy-user:{value}"


def _document_id(value: object) -> str:
    if type(value) is not str or not value:
        _fail()
    return f"ev1:document:{value}"


def document_to_evidence(
    document: object,
    *,
    visibility_policy_id: str,
    visibility_policy_version: str,
    classification: ResourceClassification = ResourceClassification.INTERNAL,
) -> EvidenceRecord:
    """Adapt current Document metadata without reading or hashing its content."""

    try:
        evidence_id = _document_id(_field(document, "id"))
        owner = _legacy_owner(_field(document, "owner"))
        created_at = _aware(_field(document, "created_at"), current_naive_utc=True)
        title = _field(document, "title")
        language = _field(document, "language")
        if type(title) is not str or not title:
            _fail()
        media_kind = MediaKind.TEXT
        mime_type = "text/markdown" if language == "markdown" else "text/plain"
        provenance = ProvenanceContext(
            source_kind=SourceKind.DOCUMENT_LIBRARY,
            acquisition_method=AcquisitionMethod.SYSTEM_OBSERVATION,
            source_system="odysseus.documents",
            capturing_party_ref=owner,
            ingestion_timestamp=created_at,
        )
        return EvidenceRecord(
            evidence_id=evidence_id,
            evidence_kind=EvidenceKind.DOCUMENT,
            media_kind=media_kind,
            scope=AuthorityScope(
                product_id="marketmatch",
                resource_id=evidence_id,
                owner_party_id=owner,
            ),
            owner_party_ref=owner,
            integrity=ContentIntegrityDescriptor.not_available(mime_type=mime_type),
            provenance=provenance,
            created_at=created_at,
            classification=classification,
            visibility_policy_id=visibility_policy_id,
            visibility_policy_version=visibility_policy_version,
            title=title,
        )
    except EvidenceContractError:
        raise
    except Exception:
        _fail()


def capture_document_to_evidence(
    document: object,
    *,
    visibility_policy_id: str,
    visibility_policy_version: str,
    classification: ResourceClassification = ResourceClassification.INTERNAL,
) -> EvidenceRecord:
    """Adapt a current textual Capture record stored as a Document row."""

    content = _field(document, "current_content")
    if type(content) is not str or not content.startswith(_CAPTURE_MARKERS):
        _fail()
    record = document_to_evidence(
        document,
        visibility_policy_id=visibility_policy_id,
        visibility_policy_version=visibility_policy_version,
        classification=classification,
    )
    provenance = ProvenanceContext(
        source_kind=SourceKind.CAPTURE,
        acquisition_method=AcquisitionMethod.CAPTURE,
        source_system="marketmatch.capture",
        capturing_party_ref=record.owner_party_ref,
        capture_timestamp=record.created_at,
        ingestion_timestamp=record.created_at,
    )
    return EvidenceRecord(
        evidence_id=record.evidence_id,
        evidence_kind=EvidenceKind.DOCUMENT,
        media_kind=MediaKind.TEXT,
        scope=record.scope,
        owner_party_ref=record.owner_party_ref,
        integrity=record.integrity,
        provenance=provenance,
        created_at=record.created_at,
        classification=record.classification,
        visibility_policy_id=record.visibility_policy_id,
        visibility_policy_version=record.visibility_policy_version,
        title=record.title,
    )


def upload_metadata_to_evidence(
    metadata: Mapping[str, Any],
    *,
    evidence_kind: EvidenceKind,
    media_kind: MediaKind,
    ingestion_timestamp: datetime,
    visibility_policy_id: str,
    visibility_policy_version: str,
    classification: ResourceClassification = ResourceClassification.INTERNAL,
) -> EvidenceRecord:
    """Adapt one owner-scoped uploads.json row without trusting its hash as observed bytes."""

    if not isinstance(metadata, Mapping):
        _fail()
    try:
        upload_id = _field(metadata, "id")
        owner = _legacy_owner(_field(metadata, "owner"))
        if type(upload_id) is not str or not upload_id:
            _fail()
        evidence_id = f"ev1:upload:{upload_id}"
        digest = _field(metadata, "hash")
        byte_length = _field(metadata, "size")
        mime_type = _field(metadata, "mime")
        safe_filename = _field(metadata, "name")
        integrity = ContentIntegrityDescriptor.metadata_only(
            algorithm=DigestAlgorithm.SHA256 if digest is not None else None,
            digest=digest,
            byte_length=byte_length,
            mime_type=mime_type,
            original_filename=safe_filename,
        )
        ingested = _aware(ingestion_timestamp)
        provenance = ProvenanceContext(
            source_kind=SourceKind.UPLOAD,
            acquisition_method=AcquisitionMethod.UPLOAD,
            source_system="odysseus.uploads",
            capturing_party_ref=owner,
            ingestion_timestamp=ingested,
            original_filename=safe_filename,
        )
        return EvidenceRecord(
            evidence_id=evidence_id,
            evidence_kind=evidence_kind,
            media_kind=media_kind,
            scope=AuthorityScope(
                product_id="marketmatch", resource_id=evidence_id, owner_party_id=owner
            ),
            owner_party_ref=owner,
            integrity=integrity,
            provenance=provenance,
            created_at=ingested,
            classification=classification,
            visibility_policy_id=visibility_policy_id,
            visibility_policy_version=visibility_policy_version,
            title=safe_filename,
        )
    except EvidenceContractError:
        raise
    except Exception:
        _fail()


def media_attestation_to_integrity(
    attestation: ValidatedOriginalMediaAttestation,
    *,
    mime_type: str | None = None,
    original_filename: str | None = None,
) -> ContentIntegrityDescriptor:
    """Preserve a validated declaration as metadata-only, never byte-verified."""

    if type(attestation) is not ValidatedOriginalMediaAttestation:
        _fail()
    return ContentIntegrityDescriptor.metadata_only(
        algorithm=DigestAlgorithm.SHA256,
        digest=attestation.sha256,
        byte_length=attestation.byte_size,
        mime_type=mime_type,
        original_filename=original_filename,
    )


def media_observation_to_integrity(
    observation: OriginalMediaObservation,
    *,
    mime_type: str | None = None,
    original_filename: str | None = None,
) -> ContentIntegrityDescriptor:
    """Preserve current observation output without upgrading a constructible object to bytes proof."""

    if type(observation) is not OriginalMediaObservation:
        _fail()
    return ContentIntegrityDescriptor.metadata_only(
        algorithm=DigestAlgorithm.SHA256,
        digest=observation.sha256,
        byte_length=observation.byte_size,
        mime_type=mime_type,
        original_filename=original_filename,
    )


__all__ = (
    "capture_document_to_evidence",
    "document_to_evidence",
    "media_attestation_to_integrity",
    "media_observation_to_integrity",
    "upload_metadata_to_evidence",
)
