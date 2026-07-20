"""Pure, persistence-free contracts for MarketMatch Evidence Core V1.

Evidence records describe artifacts and observations; they are not conclusions,
authorization grants, or legal findings.  This module performs no I/O, logging,
localization, persistence, network access, or model invocation.  Callers must
authorize and project metadata before any downstream processing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping, NoReturn, Sequence
from urllib.parse import urlsplit

from src.marketmatch_authority import (
    AuthorityReason,
    AuthorityScope,
    AuthorizationDecision,
    DerivedArtifactKind,
    DerivedVisibility,
    ProjectionBehavior,
    ResourceClassification,
    SourceVisibility,
    inherit_derived_visibility,
    project_authorized_fields,
)


EVIDENCE_CONTRACT_VERSION = "marketmatch-evidence-v1"
EVIDENCE_POLICY_VERSION = "marketmatch-evidence-policy-v1"

_ID_RE = re.compile(r"[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*\Z", re.ASCII)
_FIELD_RE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*\Z", re.ASCII)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_MIME_RE = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*\Z", re.ASCII)
_MAX_ID_BYTES = 160
_MAX_CODE_BYTES = 128
_MAX_TITLE_CHARS = 200
_MAX_DESCRIPTION_CHARS = 1_000
_MAX_NOTE_CHARS = 500
_MAX_FILENAME_CHARS = 255
_MAX_EXTERNAL_REFERENCE_CHARS = 512
_MAX_SOURCE_SYSTEM_CHARS = 128
_MAX_BYTE_LENGTH = (1 << 63) - 1
_MAX_RELATIONS = 4_096
_MAX_BUNDLE_MEMBERS = 4_096
_INTEGRITY_MARKER = object()
_PROVENANCE_MARKER = object()
_RECORD_MARKER = object()
_RELATION_MARKER = object()
_BUNDLE_MARKER = object()
_DERIVATION_MARKER = object()

_SENSITIVE_ID_PARTS = frozenset(
    {
        "address", "authorization", "avenue", "cookie", "cost", "credential",
        "factory", "hash", "margin", "markup", "password", "secret", "session",
        "street", "supplier", "token",
    }
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?:authorization|cookie|credential|password|secret|session[_-]?token|token)\s*[:=]|bearer\s+[a-z0-9]",
    re.IGNORECASE,
)
_LINEAGE_RELATIONS = frozenset(
    {
        "DERIVED_FROM", "REDACTED_FROM", "TRANSLATED_FROM", "TRANSCRIBED_FROM",
        "EXTRACTED_FROM", "THUMBNAIL_OF",
    }
)
_PROJECTION_FIELDS = frozenset(
    {
        "contract_version", "evidence_id", "evidence_kind", "media_kind",
        "owner_party_ref", "classification", "visibility_policy_id",
        "visibility_policy_version", "created_at", "title", "description",
        "integrity_algorithm", "integrity_digest", "integrity_byte_length",
        "integrity_basis", "mime_type", "original_filename", "source_kind",
        "acquisition_method", "source_system", "capturing_party_ref",
        "capture_timestamp", "received_timestamp", "ingestion_timestamp",
        "external_reference", "device_reference", "scope_organization_id",
        "scope_product_id", "scope_workspace_id", "scope_project_id",
        "scope_resource_id", "scope_owner_party_id", "scope_global",
    }
)
_FORBIDDEN_PROJECTION_FIELDS = frozenset(
    {"bytes", "content", "raw", "raw_bytes", "transcript_text", "document_text", "provenance"}
)
_CLASSIFICATION_RANK = {
    ResourceClassification.PUBLIC: 0,
    ResourceClassification.INTERNAL: 1,
    ResourceClassification.CONFIDENTIAL: 2,
    ResourceClassification.RESTRICTED: 3,
}


class EvidenceErrorCode(str, Enum):
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    INVALID_VERSION = "INVALID_VERSION"
    INVALID_ENUM = "INVALID_ENUM"
    INVALID_INTEGRITY = "INVALID_INTEGRITY"
    INVALID_PROVENANCE = "INVALID_PROVENANCE"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    INVALID_RECORD = "INVALID_RECORD"
    INVALID_RELATION = "INVALID_RELATION"
    LINEAGE_CYCLE = "LINEAGE_CYCLE"
    INVALID_BUNDLE = "INVALID_BUNDLE"
    UNKNOWN_BUNDLE_MEMBER = "UNKNOWN_BUNDLE_MEMBER"
    BUNDLE_SCOPE_CONFLICT = "BUNDLE_SCOPE_CONFLICT"
    MEMBER_NOT_AUTHORIZED = "MEMBER_NOT_AUTHORIZED"
    INVALID_ATTESTATION = "INVALID_ATTESTATION"
    INVALID_DERIVATION = "INVALID_DERIVATION"
    SOURCE_NOT_AUTHORIZED = "SOURCE_NOT_AUTHORIZED"
    SOURCE_SCOPE_CONFLICT = "SOURCE_SCOPE_CONFLICT"
    SOURCE_POLICY_CONFLICT = "SOURCE_POLICY_CONFLICT"
    INVALID_PROJECTION = "INVALID_PROJECTION"
    INVALID_AUDIT = "INVALID_AUDIT"
    INVALID_COMPATIBILITY_RECORD = "INVALID_COMPATIBILITY_RECORD"


class EvidenceContractError(ValueError):
    """Fixed-code failure that never serializes a rejected value."""

    def __init__(self, code: EvidenceErrorCode):
        if type(code) is not EvidenceErrorCode:
            code = EvidenceErrorCode.INVALID_RECORD
        self.code = code
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r})"


class EvidenceKind(str, Enum):
    DOCUMENT = "DOCUMENT"
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"
    TRANSCRIPT = "TRANSCRIPT"
    OBSERVATION = "OBSERVATION"
    MEASUREMENT = "MEASUREMENT"
    SYSTEM_RECORD = "SYSTEM_RECORD"
    ANALYSIS = "ANALYSIS"
    REPORT = "REPORT"
    EXTERNAL_REFERENCE = "EXTERNAL_REFERENCE"


class MediaKind(str, Enum):
    NONE = "NONE"
    TEXT = "TEXT"
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"
    APPLICATION = "APPLICATION"
    EXTERNAL = "EXTERNAL"


class DigestAlgorithm(str, Enum):
    SHA256 = "sha256"


class IntegrityBasis(str, Enum):
    BYTES_VERIFIED = "BYTES_VERIFIED"
    METADATA_ONLY = "METADATA_ONLY"
    EXTERNAL_REFERENCE = "EXTERNAL_REFERENCE"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class SourceKind(str, Enum):
    USER = "USER"
    SYSTEM = "SYSTEM"
    DEVICE = "DEVICE"
    EXTERNAL_SYSTEM = "EXTERNAL_SYSTEM"
    DOCUMENT_LIBRARY = "DOCUMENT_LIBRARY"
    CAPTURE = "CAPTURE"
    UPLOAD = "UPLOAD"
    MEDIA_ATTESTATION = "MEDIA_ATTESTATION"


class AcquisitionMethod(str, Enum):
    UPLOAD = "UPLOAD"
    CAPTURE = "CAPTURE"
    RECORDING = "RECORDING"
    IMPORT = "IMPORT"
    GENERATED = "GENERATED"
    SYSTEM_OBSERVATION = "SYSTEM_OBSERVATION"
    EXTERNAL_REFERENCE = "EXTERNAL_REFERENCE"


class RelationType(str, Enum):
    DERIVED_FROM = "DERIVED_FROM"
    SUPERSEDES = "SUPERSEDES"
    DUPLICATE_CONTENT_OF = "DUPLICATE_CONTENT_OF"
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    REDACTED_FROM = "REDACTED_FROM"
    TRANSLATED_FROM = "TRANSLATED_FROM"
    TRANSCRIBED_FROM = "TRANSCRIBED_FROM"
    EXTRACTED_FROM = "EXTRACTED_FROM"
    THUMBNAIL_OF = "THUMBNAIL_OF"
    INCLUDED_IN = "INCLUDED_IN"
    ASSOCIATED_WITH = "ASSOCIATED_WITH"


class BundlePurpose(str, Enum):
    COLLECTION = "COLLECTION"
    REVIEW = "REVIEW"
    VERIFICATION = "VERIFICATION"
    EXPORT = "EXPORT"
    REPORT = "REPORT"


class AttestationTargetKind(str, Enum):
    EVIDENCE = "EVIDENCE"
    BUNDLE = "BUNDLE"


class AttestationType(str, Enum):
    INTEGRITY = "INTEGRITY"
    REVIEW = "REVIEW"
    VERIFICATION = "VERIFICATION"
    COMPLETENESS = "COMPLETENESS"


class VerificationStatus(str, Enum):
    UNREVIEWED = "UNREVIEWED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    SUPERSEDED = "SUPERSEDED"
    INVALIDATED = "INVALIDATED"


class AttestationMethod(str, Enum):
    MANUAL_REVIEW = "MANUAL_REVIEW"
    BYTE_DIGEST = "BYTE_DIGEST"
    SYSTEM_VALIDATION = "SYSTEM_VALIDATION"
    SOURCE_CONFIRMATION = "SOURCE_CONFIRMATION"


class TransformationType(str, Enum):
    TRANSCRIPTION = "TRANSCRIPTION"
    TRANSLATION = "TRANSLATION"
    SUMMARY = "SUMMARY"
    ANALYSIS = "ANALYSIS"
    EXTRACTION = "EXTRACTION"
    REDACTION = "REDACTION"
    THUMBNAIL = "THUMBNAIL"
    EXPORT = "EXPORT"
    REPORT = "REPORT"


class GeneratorKind(str, Enum):
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"
    LOCAL_MODEL = "LOCAL_MODEL"
    EXTERNAL_SYSTEM = "EXTERNAL_SYSTEM"


def _fail(code: EvidenceErrorCode) -> NoReturn:
    raise EvidenceContractError(code) from None


def _enum(value: object, enum_type: type[Enum], code: EvidenceErrorCode) -> None:
    if type(value) is not enum_type:
        _fail(code)


def _parts(value: str) -> frozenset[str]:
    return frozenset(part for part in re.split(r"[._:-]+", value.lower()) if part)


def _valid_code(value: object, *, maximum: int = _MAX_CODE_BYTES, sensitive: bool = True) -> bool:
    if type(value) is not str or not value or _ID_RE.fullmatch(value) is None:
        return False
    try:
        if len(value.encode("ascii")) > maximum:
            return False
    except UnicodeError:
        return False
    return not sensitive or not (_parts(value) & _SENSITIVE_ID_PARTS)


def _require_code(value: object, code: EvidenceErrorCode, *, sensitive: bool = True) -> str:
    if not _valid_code(value, sensitive=sensitive):
        _fail(code)
    return value


def validate_evidence_id(value: object) -> str:
    if (
        not _valid_code(value, maximum=_MAX_ID_BYTES)
        or not value.startswith("ev1:")
        or value.count(":") < 2
        or _SHA256_RE.fullmatch(value) is not None
    ):
        _fail(EvidenceErrorCode.INVALID_IDENTIFIER)
    return value


def _validate_typed_id(value: object, prefix: str) -> str:
    if not _valid_code(value, maximum=_MAX_ID_BYTES) or not value.startswith(prefix):
        _fail(EvidenceErrorCode.INVALID_IDENTIFIER)
    return value


def _utc(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(EvidenceErrorCode.INVALID_TIMESTAMP)
    try:
        normalized = value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        _fail(EvidenceErrorCode.INVALID_TIMESTAMP)
    return normalized


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_text(value: object, *, maximum: int, allow_newlines: bool = False) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        _fail(EvidenceErrorCode.INVALID_PROVENANCE)
    if any(
        (ord(char) < 32 and (not allow_newlines or char not in "\n\t")) or ord(char) == 127
        for char in value
    ):
        _fail(EvidenceErrorCode.INVALID_PROVENANCE)
    if _SECRET_ASSIGNMENT_RE.search(value):
        _fail(EvidenceErrorCode.INVALID_PROVENANCE)
    return value


def _filename(value: object) -> str:
    text = _safe_text(value, maximum=_MAX_FILENAME_CHARS)
    if text in {".", ".."} or ".." in text or "/" in text or "\\" in text:
        _fail(EvidenceErrorCode.INVALID_PROVENANCE)
    return text


def _external_reference(value: object) -> str:
    text = _safe_text(value, maximum=_MAX_EXTERNAL_REFERENCE_CHARS)
    try:
        parsed = urlsplit(text)
    except ValueError:
        _fail(EvidenceErrorCode.INVALID_PROVENANCE)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        _fail(EvidenceErrorCode.INVALID_PROVENANCE)
    return text


def _require_scope(scope: object) -> AuthorityScope:
    if type(scope) is not AuthorityScope:
        _fail(EvidenceErrorCode.INVALID_RECORD)
    probe = inherit_derived_visibility(
        (
            SourceVisibility(
                source_resource_id="scope-probe",
                classification=ResourceClassification.RESTRICTED,
                visible_fields=frozenset(),
                required_scopes=(scope,),
                authorized=True,
                policy_id="evidence.scope.probe",
                policy_version="v1",
            ),
        ),
        artifact_kind=DerivedArtifactKind.ANALYSIS,
    )
    if not probe.allowed:
        _fail(EvidenceErrorCode.INVALID_RECORD)
    return scope


def _require_version(value: object) -> str:
    if value != EVIDENCE_CONTRACT_VERSION:
        _fail(EvidenceErrorCode.INVALID_VERSION)
    return value


@dataclass(frozen=True, slots=True, init=False)
class ContentIntegrityDescriptor:
    algorithm: DigestAlgorithm | None
    digest: str | None = field(repr=False)
    byte_length: int | None
    basis: IntegrityBasis
    mime_type: str | None
    original_filename: str | None = field(repr=False)
    _marker: object = field(repr=False, compare=False)
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs
        _fail(EvidenceErrorCode.INVALID_INTEGRITY)

    @classmethod
    def from_bytes(
        cls,
        content: bytes,
        *,
        mime_type: str | None = None,
        original_filename: str | None = None,
    ) -> ContentIntegrityDescriptor:
        if type(content) is not bytes:
            _fail(EvidenceErrorCode.INVALID_INTEGRITY)
        return _new_integrity(
            DigestAlgorithm.SHA256,
            hashlib.sha256(content).hexdigest(),
            len(content),
            IntegrityBasis.BYTES_VERIFIED,
            mime_type,
            original_filename,
        )

    @classmethod
    def metadata_only(
        cls,
        *,
        algorithm: DigestAlgorithm | None = None,
        digest: str | None = None,
        byte_length: int | None = None,
        mime_type: str | None = None,
        original_filename: str | None = None,
    ) -> ContentIntegrityDescriptor:
        return _new_integrity(
            algorithm, digest, byte_length, IntegrityBasis.METADATA_ONLY,
            mime_type, original_filename,
        )

    @classmethod
    def external_reference(
        cls,
        *,
        mime_type: str | None = None,
        original_filename: str | None = None,
    ) -> ContentIntegrityDescriptor:
        return _new_integrity(
            None, None, None, IntegrityBasis.EXTERNAL_REFERENCE,
            mime_type, original_filename,
        )

    @classmethod
    def not_available(
        cls,
        *,
        mime_type: str | None = None,
        original_filename: str | None = None,
    ) -> ContentIntegrityDescriptor:
        return _new_integrity(
            None, None, None, IntegrityBasis.NOT_AVAILABLE,
            mime_type, original_filename,
        )


def _integrity_tuple(value: ContentIntegrityDescriptor) -> tuple[object, ...]:
    return (
        value.algorithm, value.digest, value.byte_length, value.basis,
        value.mime_type, value.original_filename,
    )


def _new_integrity(
    algorithm: object,
    digest: object,
    byte_length: object,
    basis: object,
    mime_type: object,
    original_filename: object,
) -> ContentIntegrityDescriptor:
    _enum(basis, IntegrityBasis, EvidenceErrorCode.INVALID_INTEGRITY)
    if algorithm is not None:
        _enum(algorithm, DigestAlgorithm, EvidenceErrorCode.INVALID_INTEGRITY)
    if (algorithm is None) != (digest is None):
        _fail(EvidenceErrorCode.INVALID_INTEGRITY)
    if digest is not None and (type(digest) is not str or _SHA256_RE.fullmatch(digest.lower()) is None):
        _fail(EvidenceErrorCode.INVALID_INTEGRITY)
    if type(digest) is str:
        digest = digest.lower()
    if byte_length is not None and (
        type(byte_length) is not int or not 0 <= byte_length <= _MAX_BYTE_LENGTH
    ):
        _fail(EvidenceErrorCode.INVALID_INTEGRITY)
    if basis is IntegrityBasis.BYTES_VERIFIED:
        if algorithm is not DigestAlgorithm.SHA256 or digest is None or byte_length is None:
            _fail(EvidenceErrorCode.INVALID_INTEGRITY)
    elif basis in {IntegrityBasis.EXTERNAL_REFERENCE, IntegrityBasis.NOT_AVAILABLE}:
        if algorithm is not None or digest is not None or byte_length is not None:
            _fail(EvidenceErrorCode.INVALID_INTEGRITY)
    if mime_type is not None and (
        type(mime_type) is not str or len(mime_type) > 127 or _MIME_RE.fullmatch(mime_type.lower()) is None
    ):
        _fail(EvidenceErrorCode.INVALID_INTEGRITY)
    if type(mime_type) is str:
        mime_type = mime_type.lower()
    if original_filename is not None:
        original_filename = _filename(original_filename)

    value = object.__new__(ContentIntegrityDescriptor)
    for name, item in (
        ("algorithm", algorithm), ("digest", digest), ("byte_length", byte_length),
        ("basis", basis), ("mime_type", mime_type), ("original_filename", original_filename),
    ):
        object.__setattr__(value, name, item)
    object.__setattr__(value, "_marker", _INTEGRITY_MARKER)
    object.__setattr__(value, "_integrity", _integrity_tuple(value))
    return value


def _valid_integrity(value: object) -> bool:
    try:
        return (
            type(value) is ContentIntegrityDescriptor
            and value._marker is _INTEGRITY_MARKER
            and value._integrity == _integrity_tuple(value)
        )
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class ProvenanceContext:
    source_kind: SourceKind
    acquisition_method: AcquisitionMethod
    source_system: str | None = field(default=None, repr=False)
    capturing_party_ref: str | None = field(default=None, repr=False)
    capture_timestamp: datetime | None = None
    received_timestamp: datetime | None = None
    ingestion_timestamp: datetime | None = None
    external_reference: str | None = field(default=None, repr=False)
    original_filename: str | None = field(default=None, repr=False)
    device_reference: str | None = field(default=None, repr=False)
    safe_note: str | None = field(default=None, repr=False)
    _marker: object = field(init=False, repr=False, compare=False)
    _integrity: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _enum(self.source_kind, SourceKind, EvidenceErrorCode.INVALID_PROVENANCE)
        _enum(self.acquisition_method, AcquisitionMethod, EvidenceErrorCode.INVALID_PROVENANCE)
        if self.source_system is not None:
            if not _valid_code(self.source_system, maximum=_MAX_SOURCE_SYSTEM_CHARS):
                _fail(EvidenceErrorCode.INVALID_PROVENANCE)
        if self.capturing_party_ref is not None:
            _require_code(self.capturing_party_ref, EvidenceErrorCode.INVALID_PROVENANCE)
        if self.device_reference is not None:
            _require_code(self.device_reference, EvidenceErrorCode.INVALID_PROVENANCE)
        if self.external_reference is not None:
            _external_reference(self.external_reference)
        if self.original_filename is not None:
            _filename(self.original_filename)
        if self.safe_note is not None:
            _safe_text(self.safe_note, maximum=_MAX_NOTE_CHARS, allow_newlines=True)
        normalized = []
        for name in ("capture_timestamp", "received_timestamp", "ingestion_timestamp"):
            value = getattr(self, name)
            current = None if value is None else _utc(value)
            object.__setattr__(self, name, current)
            normalized.append(current)
        captured, received, ingested = normalized
        if captured is not None and ingested is not None and captured > ingested:
            _fail(EvidenceErrorCode.INVALID_PROVENANCE)
        if received is not None and ingested is not None and received > ingested:
            _fail(EvidenceErrorCode.INVALID_PROVENANCE)
        if self.acquisition_method is AcquisitionMethod.EXTERNAL_REFERENCE and self.external_reference is None:
            _fail(EvidenceErrorCode.INVALID_PROVENANCE)
        object.__setattr__(self, "_marker", _PROVENANCE_MARKER)
        object.__setattr__(self, "_integrity", _provenance_tuple(self))


def _provenance_tuple(value: ProvenanceContext) -> tuple[object, ...]:
    return (
        value.source_kind, value.acquisition_method, value.source_system,
        value.capturing_party_ref, value.capture_timestamp, value.received_timestamp,
        value.ingestion_timestamp, value.external_reference, value.original_filename,
        value.device_reference, value.safe_note,
    )


def _valid_provenance(value: object) -> bool:
    try:
        return (
            type(value) is ProvenanceContext
            and value._marker is _PROVENANCE_MARKER
            and value._integrity == _provenance_tuple(value)
        )
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_id: str
    evidence_kind: EvidenceKind
    media_kind: MediaKind
    scope: AuthorityScope
    owner_party_ref: str | None
    integrity: ContentIntegrityDescriptor
    provenance: ProvenanceContext
    created_at: datetime
    classification: ResourceClassification
    visibility_policy_id: str
    visibility_policy_version: str
    title: str | None = field(default=None, repr=False)
    description: str | None = field(default=None, repr=False)
    contract_version: str = EVIDENCE_CONTRACT_VERSION
    _marker: object = field(init=False, repr=False, compare=False)
    _record_integrity: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        validate_evidence_id(self.evidence_id)
        _enum(self.evidence_kind, EvidenceKind, EvidenceErrorCode.INVALID_ENUM)
        _enum(self.media_kind, MediaKind, EvidenceErrorCode.INVALID_ENUM)
        _enum(self.classification, ResourceClassification, EvidenceErrorCode.INVALID_RECORD)
        _require_scope(self.scope)
        if not _valid_integrity(self.integrity) or not _valid_provenance(self.provenance):
            _fail(EvidenceErrorCode.INVALID_RECORD)
        created = _utc(self.created_at)
        object.__setattr__(self, "created_at", created)
        _require_version(self.contract_version)
        _require_code(self.visibility_policy_id, EvidenceErrorCode.INVALID_RECORD)
        _require_code(self.visibility_policy_version, EvidenceErrorCode.INVALID_RECORD, sensitive=False)
        if self.owner_party_ref is not None:
            _require_code(self.owner_party_ref, EvidenceErrorCode.INVALID_RECORD)
        if self.scope.owner_party_id is not None and self.owner_party_ref != self.scope.owner_party_id:
            _fail(EvidenceErrorCode.INVALID_RECORD)
        if self.provenance.source_kind in {
            SourceKind.USER, SourceKind.DOCUMENT_LIBRARY, SourceKind.CAPTURE, SourceKind.UPLOAD,
        } and self.owner_party_ref is None:
            _fail(EvidenceErrorCode.INVALID_RECORD)
        if self.title is not None:
            _safe_text(self.title, maximum=_MAX_TITLE_CHARS)
        if self.description is not None:
            _safe_text(self.description, maximum=_MAX_DESCRIPTION_CHARS, allow_newlines=True)
        if self.integrity.original_filename != self.provenance.original_filename and (
            self.integrity.original_filename is not None and self.provenance.original_filename is not None
        ):
            _fail(EvidenceErrorCode.INVALID_PROVENANCE)
        if self.provenance.ingestion_timestamp is not None and self.created_at < self.provenance.ingestion_timestamp:
            _fail(EvidenceErrorCode.INVALID_PROVENANCE)
        object.__setattr__(self, "_marker", _RECORD_MARKER)
        object.__setattr__(self, "_record_integrity", _record_tuple(self))


def _record_tuple(value: EvidenceRecord) -> tuple[object, ...]:
    return (
        value.evidence_id, value.evidence_kind, value.media_kind, value.scope,
        value.owner_party_ref, value.integrity._integrity, value.provenance._integrity,
        value.created_at, value.classification, value.visibility_policy_id,
        value.visibility_policy_version, value.title, value.description, value.contract_version,
    )


def _valid_record(value: object) -> bool:
    try:
        return (
            type(value) is EvidenceRecord
            and value._marker is _RECORD_MARKER
            and _valid_integrity(value.integrity)
            and _valid_provenance(value.provenance)
            and value._record_integrity == _record_tuple(value)
        )
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class EvidenceRelation:
    source_evidence_id: str
    target_evidence_id: str
    relation_type: RelationType
    created_at: datetime
    creator_party_ref: str | None = field(default=None, repr=False)
    reason_code: str | None = None
    policy_version: str = EVIDENCE_POLICY_VERSION
    contract_version: str = EVIDENCE_CONTRACT_VERSION
    _marker: object = field(init=False, repr=False, compare=False)
    _integrity: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        validate_evidence_id(self.source_evidence_id)
        validate_evidence_id(self.target_evidence_id)
        if self.source_evidence_id == self.target_evidence_id:
            _fail(EvidenceErrorCode.INVALID_RELATION)
        _enum(self.relation_type, RelationType, EvidenceErrorCode.INVALID_RELATION)
        object.__setattr__(self, "created_at", _utc(self.created_at))
        if self.creator_party_ref is not None:
            _require_code(self.creator_party_ref, EvidenceErrorCode.INVALID_RELATION)
        if self.reason_code is not None:
            _require_code(self.reason_code, EvidenceErrorCode.INVALID_RELATION, sensitive=False)
        _require_code(self.policy_version, EvidenceErrorCode.INVALID_RELATION, sensitive=False)
        _require_version(self.contract_version)
        object.__setattr__(self, "_marker", _RELATION_MARKER)
        object.__setattr__(self, "_integrity", _relation_tuple(self))


def _relation_tuple(value: EvidenceRelation) -> tuple[object, ...]:
    return (
        value.source_evidence_id, value.target_evidence_id, value.relation_type,
        value.created_at, value.creator_party_ref, value.reason_code,
        value.policy_version, value.contract_version,
    )


def _valid_relation(value: object) -> bool:
    try:
        return (
            type(value) is EvidenceRelation
            and value._marker is _RELATION_MARKER
            and value._integrity == _relation_tuple(value)
        )
    except Exception:
        return False


def canonicalize_relations(relations: Sequence[EvidenceRelation]) -> tuple[EvidenceRelation, ...]:
    if (
        type(relations) not in (list, tuple)
        or len(relations) > _MAX_RELATIONS
        or any(not _valid_relation(item) for item in relations)
    ):
        _fail(EvidenceErrorCode.INVALID_RELATION)
    unique = set(relations)
    ordered = tuple(sorted(unique, key=lambda item: (
        item.source_evidence_id, item.target_evidence_id, item.relation_type.value,
        item.created_at.isoformat(), item.creator_party_ref or "", item.reason_code or "",
    )))
    graph: dict[str, set[str]] = {}
    for relation in ordered:
        if relation.relation_type.value in _LINEAGE_RELATIONS:
            graph.setdefault(relation.source_evidence_id, set()).add(relation.target_evidence_id)

    state: dict[str, int] = {}
    for start in sorted(graph):
        if state.get(start) == 2:
            continue
        stack: list[tuple[str, bool]] = [(start, False)]
        while stack:
            node, exiting = stack.pop()
            if exiting:
                state[node] = 2
                continue
            if state.get(node) == 1:
                _fail(EvidenceErrorCode.LINEAGE_CYCLE)
            if state.get(node) == 2:
                continue
            state[node] = 1
            stack.append((node, True))
            for target in sorted(graph.get(node, ()), reverse=True):
                if state.get(target) == 1:
                    _fail(EvidenceErrorCode.LINEAGE_CYCLE)
                if state.get(target) != 2:
                    stack.append((target, False))
    return ordered


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    bundle_id: str
    purpose: BundlePurpose
    member_evidence_ids: tuple[str, ...]
    scope: AuthorityScope
    owner_party_ref: str | None
    created_at: datetime
    contract_version: str = EVIDENCE_CONTRACT_VERSION
    _marker: object = field(init=False, repr=False, compare=False)
    _integrity: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _validate_typed_id(self.bundle_id, "bundle1:")
        _enum(self.purpose, BundlePurpose, EvidenceErrorCode.INVALID_BUNDLE)
        if (
            type(self.member_evidence_ids) is not tuple
            or not self.member_evidence_ids
            or len(self.member_evidence_ids) > _MAX_BUNDLE_MEMBERS
            or any(type(value) is not str for value in self.member_evidence_ids)
        ):
            _fail(EvidenceErrorCode.INVALID_BUNDLE)
        for value in self.member_evidence_ids:
            validate_evidence_id(value)
        normalized = tuple(sorted(set(self.member_evidence_ids)))
        object.__setattr__(self, "member_evidence_ids", normalized)
        _require_scope(self.scope)
        if self.owner_party_ref is not None:
            _require_code(self.owner_party_ref, EvidenceErrorCode.INVALID_BUNDLE)
        if self.scope.owner_party_id is not None and self.owner_party_ref != self.scope.owner_party_id:
            _fail(EvidenceErrorCode.INVALID_BUNDLE)
        object.__setattr__(self, "created_at", _utc(self.created_at))
        _require_version(self.contract_version)
        object.__setattr__(self, "_marker", _BUNDLE_MARKER)
        object.__setattr__(self, "_integrity", _bundle_tuple(self))


def _bundle_tuple(value: EvidenceBundle) -> tuple[object, ...]:
    return (
        value.bundle_id, value.purpose, value.member_evidence_ids, value.scope,
        value.owner_party_ref, value.created_at, value.contract_version,
    )


def _valid_bundle(value: object) -> bool:
    try:
        return (
            type(value) is EvidenceBundle
            and value._marker is _BUNDLE_MARKER
            and value._integrity == _bundle_tuple(value)
        )
    except Exception:
        return False


def validate_bundle_members(
    bundle: EvidenceBundle,
    records: Sequence[EvidenceRecord],
) -> tuple[EvidenceRecord, ...]:
    if not _valid_bundle(bundle) or type(records) not in (list, tuple):
        _fail(EvidenceErrorCode.INVALID_BUNDLE)
    by_id: dict[str, EvidenceRecord] = {}
    for record in records:
        if not _valid_record(record) or record.evidence_id in by_id:
            _fail(EvidenceErrorCode.INVALID_BUNDLE)
        by_id[record.evidence_id] = record
    if set(by_id) != set(bundle.member_evidence_ids):
        _fail(EvidenceErrorCode.UNKNOWN_BUNDLE_MEMBER)
    ordered = tuple(by_id[value] for value in bundle.member_evidence_ids)
    policy = ordered[0].visibility_policy_id, ordered[0].visibility_policy_version
    sources = tuple(
        SourceVisibility(
            source_resource_id=record.evidence_id,
            classification=record.classification,
            visible_fields=frozenset(),
            required_scopes=(bundle.scope, record.scope),
            authorized=True,
            policy_id=policy[0],
            policy_version=policy[1],
        )
        for record in ordered
    )
    result = inherit_derived_visibility(sources, artifact_kind=DerivedArtifactKind.REPORT)
    if result.reason_code is AuthorityReason.SOURCE_SCOPE_CONFLICT:
        _fail(EvidenceErrorCode.BUNDLE_SCOPE_CONFLICT)
    if not result.allowed:
        _fail(EvidenceErrorCode.INVALID_BUNDLE)
    return ordered


def authorize_bundle_members(
    bundle: EvidenceBundle,
    bundle_decision: AuthorizationDecision,
    member_decisions: Sequence[AuthorizationDecision],
) -> tuple[str, ...]:
    if not _valid_bundle(bundle) or type(member_decisions) not in (list, tuple):
        _fail(EvidenceErrorCode.MEMBER_NOT_AUTHORIZED)
    _validate_decision(bundle_decision, resource_type="evidence_bundle", resource_id=bundle.bundle_id)
    if not bundle_decision.allowed:
        _fail(EvidenceErrorCode.MEMBER_NOT_AUTHORIZED)
    if len(member_decisions) != len(bundle.member_evidence_ids):
        _fail(EvidenceErrorCode.MEMBER_NOT_AUTHORIZED)
    found: set[str] = set()
    for decision in member_decisions:
        _validate_decision(decision, resource_type="evidence", resource_id=None)
        if not decision.allowed or decision.resource_id not in bundle.member_evidence_ids:
            _fail(EvidenceErrorCode.MEMBER_NOT_AUTHORIZED)
        if decision.resource_id in found:
            _fail(EvidenceErrorCode.MEMBER_NOT_AUTHORIZED)
        found.add(decision.resource_id)
    if found != set(bundle.member_evidence_ids):
        _fail(EvidenceErrorCode.MEMBER_NOT_AUTHORIZED)
    return bundle.member_evidence_ids


@dataclass(frozen=True, slots=True)
class EvidenceAttestation:
    attestation_id: str
    target_kind: AttestationTargetKind
    target_id: str
    attestation_type: AttestationType
    status: VerificationStatus
    verifier_party_ref: str = field(repr=False)
    method: AttestationMethod = AttestationMethod.MANUAL_REVIEW
    timestamp: datetime = field(default_factory=lambda: _fail(EvidenceErrorCode.INVALID_TIMESTAMP))
    policy_version: str = EVIDENCE_POLICY_VERSION
    reason_code: str | None = None
    safe_note: str | None = field(default=None, repr=False)
    supporting_evidence_ids: tuple[str, ...] = ()
    contract_version: str = EVIDENCE_CONTRACT_VERSION

    def __post_init__(self) -> None:
        _validate_typed_id(self.attestation_id, "att1:")
        _enum(self.target_kind, AttestationTargetKind, EvidenceErrorCode.INVALID_ATTESTATION)
        if self.target_kind is AttestationTargetKind.EVIDENCE:
            validate_evidence_id(self.target_id)
        else:
            _validate_typed_id(self.target_id, "bundle1:")
        _enum(self.attestation_type, AttestationType, EvidenceErrorCode.INVALID_ATTESTATION)
        _enum(self.status, VerificationStatus, EvidenceErrorCode.INVALID_ATTESTATION)
        _enum(self.method, AttestationMethod, EvidenceErrorCode.INVALID_ATTESTATION)
        _require_code(self.verifier_party_ref, EvidenceErrorCode.INVALID_ATTESTATION)
        object.__setattr__(self, "timestamp", _utc(self.timestamp))
        _require_code(self.policy_version, EvidenceErrorCode.INVALID_ATTESTATION, sensitive=False)
        if self.reason_code is not None:
            _require_code(self.reason_code, EvidenceErrorCode.INVALID_ATTESTATION, sensitive=False)
        if self.safe_note is not None:
            _safe_text(self.safe_note, maximum=_MAX_NOTE_CHARS, allow_newlines=True)
        if (
            type(self.supporting_evidence_ids) is not tuple
            or len(self.supporting_evidence_ids) > _MAX_BUNDLE_MEMBERS
            or any(type(item) is not str for item in self.supporting_evidence_ids)
        ):
            _fail(EvidenceErrorCode.INVALID_ATTESTATION)
        for item in self.supporting_evidence_ids:
            validate_evidence_id(item)
        normalized = tuple(sorted(set(self.supporting_evidence_ids)))
        object.__setattr__(self, "supporting_evidence_ids", normalized)
        _require_version(self.contract_version)


@dataclass(frozen=True, slots=True)
class SourceIntegrityReference:
    evidence_id: str
    contract_version: str
    algorithm: DigestAlgorithm | None
    digest: str | None = field(repr=False)
    integrity_basis: IntegrityBasis


@dataclass(frozen=True, slots=True)
class SourcePolicyReference:
    evidence_id: str
    policy_id: str
    policy_version: str
    classification: ResourceClassification
    visible_fields: frozenset[str]


@dataclass(frozen=True, slots=True, init=False)
class EvidenceDerivation:
    derived_evidence_id: str
    source_evidence_ids: tuple[str, ...]
    transformation_type: TransformationType
    generator_kind: GeneratorKind
    generator_identifier: str | None
    generator_version: str | None
    generation_timestamp: datetime
    source_integrity_references: tuple[SourceIntegrityReference, ...]
    source_policy_references: tuple[SourcePolicyReference, ...]
    inherited_visibility: DerivedVisibility
    review_status: VerificationStatus
    contract_version: str = EVIDENCE_CONTRACT_VERSION
    _marker: object = field(repr=False, compare=False)
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs
        _fail(EvidenceErrorCode.INVALID_DERIVATION)


def _derivation_tuple(value: EvidenceDerivation) -> tuple[object, ...]:
    return (
        value.derived_evidence_id, value.source_evidence_ids, value.transformation_type,
        value.generator_kind, value.generator_identifier, value.generator_version,
        value.generation_timestamp, value.source_integrity_references,
        value.source_policy_references, value.inherited_visibility, value.review_status,
        value.contract_version,
    )


def _new_derivation(**values: object) -> EvidenceDerivation:
    result = object.__new__(EvidenceDerivation)
    for name in (
        "derived_evidence_id", "source_evidence_ids", "transformation_type",
        "generator_kind", "generator_identifier", "generator_version",
        "generation_timestamp", "source_integrity_references", "source_policy_references",
        "inherited_visibility", "review_status", "contract_version",
    ):
        object.__setattr__(result, name, values[name])
    object.__setattr__(result, "_marker", _DERIVATION_MARKER)
    object.__setattr__(result, "_integrity", _derivation_tuple(result))
    return result


def _authority_artifact(transformation: TransformationType) -> DerivedArtifactKind:
    return {
        TransformationType.TRANSLATION: DerivedArtifactKind.TRANSLATION,
        TransformationType.SUMMARY: DerivedArtifactKind.SUMMARY,
        TransformationType.ANALYSIS: DerivedArtifactKind.ANALYSIS,
        TransformationType.EXPORT: DerivedArtifactKind.EXPORT,
        TransformationType.REPORT: DerivedArtifactKind.REPORT,
        TransformationType.THUMBNAIL: DerivedArtifactKind.PREVIEW,
        TransformationType.REDACTION: DerivedArtifactKind.PREVIEW,
        TransformationType.TRANSCRIPTION: DerivedArtifactKind.ANALYSIS,
        TransformationType.EXTRACTION: DerivedArtifactKind.ANALYSIS,
    }[transformation]


def _validate_decision(
    decision: object,
    *,
    resource_type: str,
    resource_id: str | None,
) -> AuthorizationDecision:
    try:
        project_authorized_fields({}, decision, behavior=ProjectionBehavior.OMIT)
    except Exception:
        _fail(EvidenceErrorCode.INVALID_PROJECTION)
    if (
        type(decision) is not AuthorizationDecision
        or decision.resource_type != resource_type
        or (resource_id is not None and decision.resource_id != resource_id)
    ):
        _fail(EvidenceErrorCode.INVALID_PROJECTION)
    return decision


def _decision_matches_record(
    decision: AuthorizationDecision,
    record: EvidenceRecord,
) -> bool:
    return (
        decision.policy_id == record.visibility_policy_id
        and decision.policy_version == record.visibility_policy_version
        and (not decision.allowed or decision.effective_scope == record.scope)
    )


def inherit_evidence_visibility(
    records: Sequence[EvidenceRecord],
    decisions: Sequence[AuthorizationDecision],
    transformation_type: TransformationType,
) -> DerivedVisibility:
    _enum(transformation_type, TransformationType, EvidenceErrorCode.INVALID_DERIVATION)
    if type(records) not in (list, tuple) or type(decisions) not in (list, tuple) or not records:
        _fail(EvidenceErrorCode.INVALID_DERIVATION)
    if len(records) != len(decisions):
        _fail(EvidenceErrorCode.SOURCE_NOT_AUTHORIZED)
    seen: set[str] = set()
    sources: list[SourceVisibility] = []
    for record, decision in zip(records, decisions):
        if not _valid_record(record) or record.evidence_id in seen:
            _fail(EvidenceErrorCode.INVALID_DERIVATION)
        seen.add(record.evidence_id)
        _validate_decision(decision, resource_type="evidence", resource_id=record.evidence_id)
        if (
            not decision.allowed
            or decision.policy_id != record.visibility_policy_id
            or decision.policy_version != record.visibility_policy_version
            or decision.effective_scope != record.scope
        ):
            _fail(EvidenceErrorCode.SOURCE_NOT_AUTHORIZED)
        sources.append(
            SourceVisibility(
                source_resource_id=record.evidence_id,
                classification=record.classification,
                visible_fields=decision.visible_fields,
                required_scopes=(record.scope,),
                authorized=True,
                policy_id=decision.policy_id,
                policy_version=decision.policy_version,
            )
        )
    inherited = inherit_derived_visibility(
        tuple(sources), artifact_kind=_authority_artifact(transformation_type)
    )
    if inherited.reason_code is AuthorityReason.SOURCE_SCOPE_CONFLICT:
        _fail(EvidenceErrorCode.SOURCE_SCOPE_CONFLICT)
    if inherited.reason_code is AuthorityReason.SOURCE_POLICY_CONFLICT:
        _fail(EvidenceErrorCode.SOURCE_POLICY_CONFLICT)
    if not inherited.allowed:
        _fail(EvidenceErrorCode.SOURCE_NOT_AUTHORIZED)
    return inherited


def _derived_scope_preserves_sources(
    derived: AuthorityScope,
    sources: Sequence[EvidenceRecord],
) -> bool:
    if derived.global_scope and any(not record.scope.global_scope for record in sources):
        return False
    for name in ("organization_id", "product_id", "workspace_id", "project_id", "owner_party_id"):
        values = {getattr(record.scope, name) for record in sources if getattr(record.scope, name) is not None}
        if values and getattr(derived, name) not in values:
            return False
    return True


def create_evidence_derivation(
    derived_record: EvidenceRecord,
    source_records: Sequence[EvidenceRecord],
    source_decisions: Sequence[AuthorizationDecision],
    *,
    transformation_type: TransformationType,
    generator_kind: GeneratorKind,
    generation_timestamp: datetime,
    generator_identifier: str | None = None,
    generator_version: str | None = None,
    review_status: VerificationStatus = VerificationStatus.UNREVIEWED,
) -> EvidenceDerivation:
    if not _valid_record(derived_record):
        _fail(EvidenceErrorCode.INVALID_DERIVATION)
    _enum(generator_kind, GeneratorKind, EvidenceErrorCode.INVALID_DERIVATION)
    _enum(review_status, VerificationStatus, EvidenceErrorCode.INVALID_DERIVATION)
    generated_at = _utc(generation_timestamp)
    if derived_record.created_at != generated_at:
        _fail(EvidenceErrorCode.INVALID_DERIVATION)
    if derived_record.provenance.acquisition_method is not AcquisitionMethod.GENERATED:
        _fail(EvidenceErrorCode.INVALID_DERIVATION)
    if (generator_identifier is None) != (generator_version is None):
        _fail(EvidenceErrorCode.INVALID_DERIVATION)
    if generator_kind is GeneratorKind.LOCAL_MODEL and generator_identifier is None:
        _fail(EvidenceErrorCode.INVALID_DERIVATION)
    if generator_kind is GeneratorKind.HUMAN and generator_identifier is not None:
        _fail(EvidenceErrorCode.INVALID_DERIVATION)
    if generator_identifier is not None:
        _require_code(generator_identifier, EvidenceErrorCode.INVALID_DERIVATION)
        _require_code(generator_version, EvidenceErrorCode.INVALID_DERIVATION, sensitive=False)
    inherited = inherit_evidence_visibility(source_records, source_decisions, transformation_type)
    sources = tuple(source_records)
    if derived_record.evidence_id in {record.evidence_id for record in sources}:
        _fail(EvidenceErrorCode.INVALID_DERIVATION)
    expected_policy = inherited.source_policies[0]
    if (
        (derived_record.visibility_policy_id, derived_record.visibility_policy_version) != expected_policy
        or derived_record.classification is not inherited.classification
        or not _derived_scope_preserves_sources(derived_record.scope, sources)
    ):
        _fail(EvidenceErrorCode.INVALID_DERIVATION)
    integrity_refs = tuple(
        SourceIntegrityReference(
            evidence_id=record.evidence_id,
            contract_version=record.contract_version,
            algorithm=record.integrity.algorithm,
            digest=record.integrity.digest,
            integrity_basis=record.integrity.basis,
        )
        for record in sources
    )
    policy_refs = tuple(
        SourcePolicyReference(
            evidence_id=record.evidence_id,
            policy_id=decision.policy_id,
            policy_version=decision.policy_version,
            classification=record.classification,
            visible_fields=decision.visible_fields,
        )
        for record, decision in zip(sources, source_decisions)
    )
    return _new_derivation(
        derived_evidence_id=derived_record.evidence_id,
        source_evidence_ids=tuple(record.evidence_id for record in sources),
        transformation_type=transformation_type,
        generator_kind=generator_kind,
        generator_identifier=generator_identifier,
        generator_version=generator_version,
        generation_timestamp=generated_at,
        source_integrity_references=integrity_refs,
        source_policy_references=policy_refs,
        inherited_visibility=inherited,
        review_status=review_status,
        contract_version=EVIDENCE_CONTRACT_VERSION,
    )


def evidence_metadata(record: EvidenceRecord) -> dict[str, Any]:
    if not _valid_record(record):
        _fail(EvidenceErrorCode.INVALID_RECORD)
    provenance = record.provenance
    return {
        "contract_version": record.contract_version,
        "evidence_id": record.evidence_id,
        "evidence_kind": record.evidence_kind.value,
        "media_kind": record.media_kind.value,
        "owner_party_ref": record.owner_party_ref,
        "classification": record.classification.value,
        "visibility_policy_id": record.visibility_policy_id,
        "visibility_policy_version": record.visibility_policy_version,
        "created_at": _iso(record.created_at),
        "title": record.title,
        "description": record.description,
        "integrity_algorithm": record.integrity.algorithm.value if record.integrity.algorithm else None,
        "integrity_digest": record.integrity.digest,
        "integrity_byte_length": record.integrity.byte_length,
        "integrity_basis": record.integrity.basis.value,
        "mime_type": record.integrity.mime_type,
        "original_filename": record.integrity.original_filename or provenance.original_filename,
        "source_kind": provenance.source_kind.value,
        "acquisition_method": provenance.acquisition_method.value,
        "source_system": provenance.source_system,
        "capturing_party_ref": provenance.capturing_party_ref,
        "capture_timestamp": _iso(provenance.capture_timestamp),
        "received_timestamp": _iso(provenance.received_timestamp),
        "ingestion_timestamp": _iso(provenance.ingestion_timestamp),
        "external_reference": provenance.external_reference,
        "device_reference": provenance.device_reference,
        "scope_organization_id": record.scope.organization_id,
        "scope_product_id": record.scope.product_id,
        "scope_workspace_id": record.scope.workspace_id,
        "scope_project_id": record.scope.project_id,
        "scope_resource_id": record.scope.resource_id,
        "scope_owner_party_id": record.scope.owner_party_id,
        "scope_global": record.scope.global_scope,
    }


def serialize_evidence_record(record: EvidenceRecord) -> bytes:
    """Serialize deterministic metadata; callers must still authorize its use."""

    return json.dumps(
        evidence_metadata(record), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def project_evidence_metadata(
    record: EvidenceRecord | Mapping[str, Any],
    decision: AuthorizationDecision,
) -> dict[str, Any]:
    """Project a flat metadata mapping through an authentic Authority decision."""

    try:
        if type(record) is EvidenceRecord:
            source = evidence_metadata(record)
            expected_id = record.evidence_id
            expected_scope = record.scope
            expected_policy = (
                record.visibility_policy_id,
                record.visibility_policy_version,
            )
        elif isinstance(record, Mapping):
            source = {}
            for key, value in record.items():
                if type(key) is str and key in _PROJECTION_FIELDS and key not in _FORBIDDEN_PROJECTION_FIELDS:
                    source[key] = value
            expected_id = source.get("evidence_id")
            validate_evidence_id(expected_id)
            required_scope_fields = {
                "scope_organization_id", "scope_product_id", "scope_workspace_id",
                "scope_project_id", "scope_resource_id", "scope_owner_party_id", "scope_global",
                "visibility_policy_id", "visibility_policy_version",
            }
            if not required_scope_fields <= source.keys():
                _fail(EvidenceErrorCode.INVALID_PROJECTION)
            expected_scope = AuthorityScope(
                organization_id=source["scope_organization_id"],
                product_id=source["scope_product_id"],
                workspace_id=source["scope_workspace_id"],
                project_id=source["scope_project_id"],
                resource_id=source["scope_resource_id"],
                owner_party_id=source["scope_owner_party_id"],
                global_scope=source["scope_global"],
            )
            _require_scope(expected_scope)
            if expected_scope.resource_id != expected_id:
                _fail(EvidenceErrorCode.INVALID_PROJECTION)
            expected_policy = (
                source["visibility_policy_id"], source["visibility_policy_version"]
            )
        else:
            _fail(EvidenceErrorCode.INVALID_PROJECTION)
        _validate_decision(decision, resource_type="evidence", resource_id=expected_id)
        if (
            expected_policy != (decision.policy_id, decision.policy_version)
            or (decision.allowed and decision.effective_scope != expected_scope)
        ):
            _fail(EvidenceErrorCode.INVALID_PROJECTION)
        return project_authorized_fields(source, decision, behavior=ProjectionBehavior.OMIT)
    except EvidenceContractError:
        raise
    except Exception:
        _fail(EvidenceErrorCode.INVALID_PROJECTION)


@dataclass(frozen=True, slots=True)
class EvidenceAuditSummary:
    evidence_id: str
    evidence_kind: str
    action: str
    allowed: bool
    reason_code: str
    policy_id: str
    policy_version: str
    integrity_algorithm: str | None
    integrity_basis: str
    timestamp: str
    correlation_id: str | None


def build_safe_evidence_audit_summary(
    record: EvidenceRecord,
    decision: AuthorizationDecision,
    *,
    action: str,
    timestamp: datetime,
    correlation_id: str | None = None,
) -> EvidenceAuditSummary:
    if not _valid_record(record):
        _fail(EvidenceErrorCode.INVALID_AUDIT)
    try:
        _validate_decision(decision, resource_type="evidence", resource_id=record.evidence_id)
    except EvidenceContractError:
        _fail(EvidenceErrorCode.INVALID_AUDIT)
    if not _decision_matches_record(decision, record):
        _fail(EvidenceErrorCode.INVALID_AUDIT)
    if not _valid_code(action) or action != decision.action_code:
        _fail(EvidenceErrorCode.INVALID_AUDIT)
    safe_correlation = None
    if correlation_id is not None:
        if not _valid_code(correlation_id, maximum=128):
            _fail(EvidenceErrorCode.INVALID_AUDIT)
        safe_correlation = correlation_id
    return EvidenceAuditSummary(
        evidence_id=record.evidence_id,
        evidence_kind=record.evidence_kind.value,
        action=action,
        allowed=decision.allowed,
        reason_code=decision.reason_code.value,
        policy_id=decision.policy_id,
        policy_version=decision.policy_version,
        integrity_algorithm=record.integrity.algorithm.value if record.integrity.algorithm else None,
        integrity_basis=record.integrity.basis.value,
        timestamp=_iso(_utc(timestamp)) or "",
        correlation_id=safe_correlation,
    )


__all__ = (
    "AcquisitionMethod", "AttestationMethod", "AttestationTargetKind", "AttestationType",
    "BundlePurpose", "ContentIntegrityDescriptor", "DigestAlgorithm", "EVIDENCE_CONTRACT_VERSION",
    "EVIDENCE_POLICY_VERSION", "EvidenceAttestation", "EvidenceAuditSummary", "EvidenceBundle",
    "EvidenceContractError", "EvidenceDerivation", "EvidenceErrorCode", "EvidenceKind",
    "EvidenceRecord", "EvidenceRelation", "GeneratorKind", "IntegrityBasis", "MediaKind",
    "ProvenanceContext", "RelationType", "SourceIntegrityReference", "SourceKind",
    "SourcePolicyReference", "TransformationType", "VerificationStatus", "authorize_bundle_members",
    "build_safe_evidence_audit_summary", "canonicalize_relations", "create_evidence_derivation",
    "evidence_metadata", "inherit_evidence_visibility", "project_evidence_metadata",
    "serialize_evidence_record", "validate_bundle_members", "validate_evidence_id",
)
