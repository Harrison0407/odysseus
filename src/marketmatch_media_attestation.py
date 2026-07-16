"""Pure validation of MarketMatch original-media provenance declarations.

Version 1 is a closed data contract.  A valid attestation proves only that its
data is structurally valid, that its canonical subject digest matches, and
that it declares the sole permitted ``verified_ingest`` authority together
with internally consistent domain, operation, media role, size, and SHA-256
metadata.

``verified_ingest`` means that a future approved ingest implementation must
compute the byte count and SHA-256 while ingesting the exact original byte
stream, complete that computation before creating the attestation, and never
accept those values from client-provided metadata.  This pure module cannot
prove that an ingest implementation followed those rules.  The unkeyed
attestation digest provides canonical subject integrity, not issuer identity
or cryptographic authentication of the authority claim.

The contract does not prove that media exists, that it has not been replaced,
that ownership was authorized, or that SQL, publication, durability, STT, or
workflow completion occurred.  It performs no I/O and retains no caller-owned
object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import hmac
import json
import re
from typing import NoReturn


ORIGINAL_MEDIA_ATTESTATION_SCHEMA = "marketmatch-original-media-attestation-v1"
MAX_ATTESTATION_BYTES = 4_096
MAX_ORIGINAL_MEDIA_BYTES = 104_857_600

_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema",
        "domain",
        "operation_id",
        "media_id",
        "media_role",
        "byte_size",
        "sha256",
        "authority",
        "attestation_digest",
    }
)
_OPERATION_ID_RE = re.compile(r"mmop-(calls|videos)-[a-z0-9]{26}\Z", re.ASCII)
_MEDIA_ID_RE = re.compile(r"mmmedia-(calls|videos)-[a-z0-9]{26}\Z", re.ASCII)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_MAX_OPERATION_ID_LENGTH = len("mmop-videos-") + 26
_MAX_MEDIA_ID_LENGTH = len("mmmedia-videos-") + 26


class MediaDomain(str, Enum):
    """Closed original-media domains."""

    CALLS = "calls"
    VIDEOS = "videos"


class OriginalMediaRole(str, Enum):
    """Closed domain-bound roles for original media only."""

    ORIGINAL_AUDIO = "original_audio"
    ORIGINAL_VIDEO = "original_video"


class AttestationAuthority(str, Enum):
    """Closed logical authority declaration for version 1."""

    VERIFIED_INGEST = "verified_ingest"


class MediaAttestationCode(str, Enum):
    """Fixed privacy-preserving validation result codes."""

    INVALID_INPUT_TYPE = "INVALID_INPUT_TYPE"
    INVALID_JSON = "INVALID_JSON"
    DUPLICATE_JSON_KEY = "DUPLICATE_JSON_KEY"
    NON_CANONICAL_JSON = "NON_CANONICAL_JSON"
    UNKNOWN_SCHEMA = "UNKNOWN_SCHEMA"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    INVALID_DOMAIN = "INVALID_DOMAIN"
    INVALID_OPERATION_ID = "INVALID_OPERATION_ID"
    INVALID_MEDIA_ID = "INVALID_MEDIA_ID"
    INVALID_MEDIA_ROLE = "INVALID_MEDIA_ROLE"
    DOMAIN_ROLE_MISMATCH = "DOMAIN_ROLE_MISMATCH"
    INVALID_BYTE_SIZE = "INVALID_BYTE_SIZE"
    INVALID_SHA256 = "INVALID_SHA256"
    INVALID_AUTHORITY = "INVALID_AUTHORITY"
    ATTESTATION_DIGEST_MISSING = "ATTESTATION_DIGEST_MISSING"
    ATTESTATION_DIGEST_MISMATCH = "ATTESTATION_DIGEST_MISMATCH"
    EXPECTED_DOMAIN_MISMATCH = "EXPECTED_DOMAIN_MISMATCH"
    EXPECTED_OPERATION_MISMATCH = "EXPECTED_OPERATION_MISMATCH"
    INVALID_BYTE_LIMIT = "INVALID_BYTE_LIMIT"
    INPUT_LIMIT_EXCEEDED = "INPUT_LIMIT_EXCEEDED"


class MediaAttestationError(Exception):
    """A fixed-code failure that never embeds rejected input."""

    def __init__(self, code: MediaAttestationCode):
        if type(code) is not MediaAttestationCode:
            code = MediaAttestationCode.INVALID_INPUT_TYPE
        self.code = code
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r})"


@dataclass(frozen=True, slots=True)
class ValidatedOriginalMediaAttestation:
    """Immutable validated declaration detached from caller-owned data."""

    schema: str = field(repr=False)
    domain: MediaDomain = field(repr=False)
    operation_id: str = field(repr=False)
    media_id: str = field(repr=False)
    media_role: OriginalMediaRole = field(repr=False)
    byte_size: int = field(repr=False)
    sha256: str = field(repr=False)
    authority: AttestationAuthority = field(repr=False)
    attestation_digest: str = field(repr=False)
    canonical_bytes: bytes = field(repr=False, hash=False)


@dataclass(frozen=True, slots=True)
class Phase3PExpectedMetadata:
    """Only the explicit metadata that a Phase 3P caller may consume."""

    expected_byte_size: int = field(repr=False)
    expected_sha256: str = field(repr=False)


class _DuplicateKeyError(Exception):
    pass


class _DisallowedNumberError(Exception):
    pass


def _fail(code: MediaAttestationCode) -> NoReturn:
    raise MediaAttestationError(code) from None


def _duplicate_rejecting_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    keys = tuple(key for key, _ in pairs)
    if len(keys) != len(frozenset(keys)):
        raise _DuplicateKeyError
    return {key: value for key, value in pairs}


def _parse_integer(token: str) -> int:
    if len(token) > 12:
        raise _DisallowedNumberError
    return int(token, 10)


def _reject_number(token: str) -> NoReturn:
    del token
    raise _DisallowedNumberError


def _parse_json_bytes(raw: bytes) -> dict[str, object]:
    if not raw or len(raw) > MAX_ATTESTATION_BYTES:
        _fail(MediaAttestationCode.INVALID_JSON)
    if raw.startswith(b"\xef\xbb\xbf") or raw != raw.strip():
        _fail(MediaAttestationCode.NON_CANONICAL_JSON)

    decode_failed = False
    text = ""
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        decode_failed = True
    if decode_failed:
        _fail(MediaAttestationCode.NON_CANONICAL_JSON)

    decoder = json.JSONDecoder(
        object_pairs_hook=_duplicate_rejecting_object,
        parse_float=_reject_number,
        parse_int=_parse_integer,
        parse_constant=_reject_number,
        strict=True,
    )
    parse_code: MediaAttestationCode | None = None
    value: object = None
    end = 0
    try:
        value, end = decoder.raw_decode(text)
    except _DuplicateKeyError:
        parse_code = MediaAttestationCode.DUPLICATE_JSON_KEY
    except _DisallowedNumberError:
        parse_code = MediaAttestationCode.INVALID_JSON
    except (json.JSONDecodeError, RecursionError, ValueError):
        parse_code = MediaAttestationCode.INVALID_JSON
    if parse_code is not None:
        _fail(parse_code)
    if end != len(text):
        _fail(MediaAttestationCode.NON_CANONICAL_JSON)
    if type(value) is not dict:
        _fail(MediaAttestationCode.INVALID_INPUT_TYPE)
    return value


def _require_exact_fields(value: object) -> dict[str, object]:
    if type(value) is not dict:
        _fail(MediaAttestationCode.INVALID_INPUT_TYPE)
    if len(value) > len(_TOP_LEVEL_FIELDS):
        _fail(MediaAttestationCode.UNKNOWN_FIELD)
    keys = tuple(value.keys())
    if any(type(key) is not str for key in keys):
        _fail(MediaAttestationCode.INVALID_INPUT_TYPE)
    present = frozenset(keys)
    if present - _TOP_LEVEL_FIELDS:
        _fail(MediaAttestationCode.UNKNOWN_FIELD)
    missing = _TOP_LEVEL_FIELDS - present
    if "attestation_digest" in missing:
        _fail(MediaAttestationCode.ATTESTATION_DIGEST_MISSING)
    if missing:
        _fail(MediaAttestationCode.MISSING_FIELD)
    return value


def _require_ascii_string(
    value: object,
    code: MediaAttestationCode,
    *,
    maximum_length: int,
) -> str:
    if (
        type(value) is not str
        or len(value) > maximum_length
        or not value.isascii()
    ):
        _fail(code)
    return value


def _validate_schema(value: object) -> str:
    schema = _require_ascii_string(
        value,
        MediaAttestationCode.UNKNOWN_SCHEMA,
        maximum_length=len(ORIGINAL_MEDIA_ATTESTATION_SCHEMA),
    )
    if schema != ORIGINAL_MEDIA_ATTESTATION_SCHEMA:
        _fail(MediaAttestationCode.UNKNOWN_SCHEMA)
    return schema


def _validate_domain(value: object) -> MediaDomain:
    text = _require_ascii_string(
        value,
        MediaAttestationCode.INVALID_DOMAIN,
        maximum_length=max(len(item.value) for item in MediaDomain),
    )
    for domain in MediaDomain:
        if text == domain.value:
            return domain
    _fail(MediaAttestationCode.INVALID_DOMAIN)


def _validate_operation_id(value: object, domain: MediaDomain) -> str:
    operation_id = _require_ascii_string(
        value,
        MediaAttestationCode.INVALID_OPERATION_ID,
        maximum_length=_MAX_OPERATION_ID_LENGTH,
    )
    match = _OPERATION_ID_RE.fullmatch(operation_id)
    if match is None or match.group(1) != domain.value:
        _fail(MediaAttestationCode.INVALID_OPERATION_ID)
    return operation_id


def _validate_media_id(value: object, domain: MediaDomain) -> str:
    media_id = _require_ascii_string(
        value,
        MediaAttestationCode.INVALID_MEDIA_ID,
        maximum_length=_MAX_MEDIA_ID_LENGTH,
    )
    match = _MEDIA_ID_RE.fullmatch(media_id)
    if match is None or match.group(1) != domain.value:
        _fail(MediaAttestationCode.INVALID_MEDIA_ID)
    return media_id


def _validate_media_role(
    value: object,
    domain: MediaDomain,
) -> OriginalMediaRole:
    text = _require_ascii_string(
        value,
        MediaAttestationCode.INVALID_MEDIA_ROLE,
        maximum_length=max(len(item.value) for item in OriginalMediaRole),
    )
    role: OriginalMediaRole | None = None
    for candidate in OriginalMediaRole:
        if text == candidate.value:
            role = candidate
            break
    if role is None:
        _fail(MediaAttestationCode.INVALID_MEDIA_ROLE)
    required_role = (
        OriginalMediaRole.ORIGINAL_AUDIO
        if domain is MediaDomain.CALLS
        else OriginalMediaRole.ORIGINAL_VIDEO
    )
    if role is not required_role:
        _fail(MediaAttestationCode.DOMAIN_ROLE_MISMATCH)
    return role


def _validate_byte_size(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_ORIGINAL_MEDIA_BYTES:
        _fail(MediaAttestationCode.INVALID_BYTE_SIZE)
    return value


def _validate_sha256(value: object) -> str:
    digest = _require_ascii_string(
        value,
        MediaAttestationCode.INVALID_SHA256,
        maximum_length=64,
    )
    if _SHA256_RE.fullmatch(digest) is None:
        _fail(MediaAttestationCode.INVALID_SHA256)
    return digest


def _validate_authority(value: object) -> AttestationAuthority:
    text = _require_ascii_string(
        value,
        MediaAttestationCode.INVALID_AUTHORITY,
        maximum_length=len(AttestationAuthority.VERIFIED_INGEST.value),
    )
    if text != AttestationAuthority.VERIFIED_INGEST.value:
        _fail(MediaAttestationCode.INVALID_AUTHORITY)
    return AttestationAuthority.VERIFIED_INGEST


def _validate_attestation_digest(value: object) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(MediaAttestationCode.ATTESTATION_DIGEST_MISMATCH)
    return value


def _subject_data(
    *,
    domain: MediaDomain,
    operation_id: str,
    media_id: str,
    media_role: OriginalMediaRole,
    byte_size: int,
    sha256: str,
    authority: AttestationAuthority,
) -> dict[str, object]:
    return {
        "authority": authority.value,
        "byte_size": byte_size,
        "domain": domain.value,
        "media_id": media_id,
        "media_role": media_role.value,
        "operation_id": operation_id,
        "schema": ORIGINAL_MEDIA_ATTESTATION_SCHEMA,
        "sha256": sha256,
    }


def _canonical_json(value: dict[str, object]) -> bytes:
    failed = False
    encoded = b""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        failed = True
    if failed or len(encoded) > MAX_ATTESTATION_BYTES:
        _fail(MediaAttestationCode.INVALID_INPUT_TYPE)
    return encoded


def validate_original_media_attestation(
    attestation: bytes | dict[str, object],
) -> ValidatedOriginalMediaAttestation:
    """Validate one closed original-media attestation without performing I/O.

    Exact ``bytes`` input must already be canonical JSON.  Parsed input must be
    an exact built-in ``dict`` containing only exact built-in scalar values.
    The returned object is detached from the caller's mapping.
    """

    original_bytes: bytes | None
    if type(attestation) is bytes:
        original_bytes = attestation
        data = _parse_json_bytes(attestation)
    elif type(attestation) is dict:
        original_bytes = None
        if len(attestation) > len(_TOP_LEVEL_FIELDS):
            _fail(MediaAttestationCode.UNKNOWN_FIELD)
        data = dict(attestation)
    else:
        _fail(MediaAttestationCode.INVALID_INPUT_TYPE)

    top = _require_exact_fields(data)
    schema = _validate_schema(top["schema"])
    domain = _validate_domain(top["domain"])
    operation_id = _validate_operation_id(top["operation_id"], domain)
    media_id = _validate_media_id(top["media_id"], domain)
    media_role = _validate_media_role(top["media_role"], domain)
    byte_size = _validate_byte_size(top["byte_size"])
    sha256 = _validate_sha256(top["sha256"])
    authority = _validate_authority(top["authority"])
    supplied_digest = _validate_attestation_digest(top["attestation_digest"])

    subject = _subject_data(
        domain=domain,
        operation_id=operation_id,
        media_id=media_id,
        media_role=media_role,
        byte_size=byte_size,
        sha256=sha256,
        authority=authority,
    )
    computed_digest = hashlib.sha256(_canonical_json(subject)).hexdigest()
    if not hmac.compare_digest(computed_digest, supplied_digest):
        _fail(MediaAttestationCode.ATTESTATION_DIGEST_MISMATCH)

    canonical_bytes = _canonical_json(
        {**subject, "attestation_digest": computed_digest}
    )
    if original_bytes is not None and not hmac.compare_digest(
        original_bytes,
        canonical_bytes,
    ):
        _fail(MediaAttestationCode.NON_CANONICAL_JSON)

    return ValidatedOriginalMediaAttestation(
        schema=schema,
        domain=domain,
        operation_id=operation_id,
        media_id=media_id,
        media_role=media_role,
        byte_size=byte_size,
        sha256=sha256,
        authority=authority,
        attestation_digest=computed_digest,
        canonical_bytes=canonical_bytes,
    )


def _same_validated_attestation(
    supplied: ValidatedOriginalMediaAttestation,
    reparsed: ValidatedOriginalMediaAttestation,
) -> bool:
    if (
        type(supplied.schema) is not str
        or type(supplied.domain) is not MediaDomain
        or type(supplied.operation_id) is not str
        or type(supplied.media_id) is not str
        or type(supplied.media_role) is not OriginalMediaRole
        or type(supplied.byte_size) is not int
        or type(supplied.sha256) is not str
        or type(supplied.authority) is not AttestationAuthority
        or type(supplied.attestation_digest) is not str
        or type(supplied.canonical_bytes) is not bytes
    ):
        return False

    _validate_schema(supplied.schema)
    _validate_operation_id(supplied.operation_id, supplied.domain)
    _validate_media_id(supplied.media_id, supplied.domain)
    _validate_media_role(supplied.media_role.value, supplied.domain)
    _validate_byte_size(supplied.byte_size)
    _validate_sha256(supplied.sha256)
    _validate_authority(supplied.authority.value)
    _validate_attestation_digest(supplied.attestation_digest)
    return (
        supplied.schema == reparsed.schema
        and supplied.domain is reparsed.domain
        and hmac.compare_digest(supplied.operation_id, reparsed.operation_id)
        and hmac.compare_digest(supplied.media_id, reparsed.media_id)
        and supplied.media_role is reparsed.media_role
        and supplied.byte_size == reparsed.byte_size
        and hmac.compare_digest(supplied.sha256, reparsed.sha256)
        and supplied.authority is reparsed.authority
        and hmac.compare_digest(
            supplied.attestation_digest,
            reparsed.attestation_digest,
        )
        and hmac.compare_digest(supplied.canonical_bytes, reparsed.canonical_bytes)
    )


def bind_attestation_for_phase3p(
    attestation: ValidatedOriginalMediaAttestation,
    *,
    expected_domain: MediaDomain,
    expected_operation_id: str,
    byte_limit: int,
) -> Phase3PExpectedMetadata:
    """Return only size and SHA-256 after strict Phase 3P metadata binding.

    The canonical bytes are revalidated so a manually altered data-model
    instance cannot bypass the contract.  This function performs no stream
    read and does not call Phase 3P.  Acceptance establishes only the declared
    metadata relationship; future ingest integration must establish the real
    ``verified_ingest`` authority.
    """

    if type(attestation) is not ValidatedOriginalMediaAttestation:
        _fail(MediaAttestationCode.INVALID_INPUT_TYPE)
    if type(expected_domain) is not MediaDomain:
        _fail(MediaAttestationCode.EXPECTED_DOMAIN_MISMATCH)
    if type(byte_limit) is not int or not 0 < byte_limit <= MAX_ORIGINAL_MEDIA_BYTES:
        _fail(MediaAttestationCode.INVALID_BYTE_LIMIT)
    if type(expected_operation_id) is not str:
        _fail(MediaAttestationCode.EXPECTED_OPERATION_MISMATCH)
    operation_match = _OPERATION_ID_RE.fullmatch(expected_operation_id)
    if operation_match is None or operation_match.group(1) != expected_domain.value:
        _fail(MediaAttestationCode.EXPECTED_OPERATION_MISMATCH)

    reparsed = validate_original_media_attestation(attestation.canonical_bytes)
    if not _same_validated_attestation(attestation, reparsed):
        _fail(MediaAttestationCode.INVALID_INPUT_TYPE)
    if reparsed.domain is not expected_domain:
        _fail(MediaAttestationCode.EXPECTED_DOMAIN_MISMATCH)
    if not hmac.compare_digest(reparsed.operation_id, expected_operation_id):
        _fail(MediaAttestationCode.EXPECTED_OPERATION_MISMATCH)
    if reparsed.authority is not AttestationAuthority.VERIFIED_INGEST:
        _fail(MediaAttestationCode.INVALID_AUTHORITY)
    required_role = (
        OriginalMediaRole.ORIGINAL_AUDIO
        if expected_domain is MediaDomain.CALLS
        else OriginalMediaRole.ORIGINAL_VIDEO
    )
    if reparsed.media_role is not required_role:
        _fail(MediaAttestationCode.DOMAIN_ROLE_MISMATCH)
    if reparsed.byte_size > byte_limit:
        _fail(MediaAttestationCode.INPUT_LIMIT_EXCEEDED)

    return Phase3PExpectedMetadata(
        expected_byte_size=reparsed.byte_size,
        expected_sha256=reparsed.sha256,
    )


__all__ = (
    "AttestationAuthority",
    "MAX_ATTESTATION_BYTES",
    "MAX_ORIGINAL_MEDIA_BYTES",
    "MediaAttestationCode",
    "MediaAttestationError",
    "MediaDomain",
    "ORIGINAL_MEDIA_ATTESTATION_SCHEMA",
    "OriginalMediaRole",
    "Phase3PExpectedMetadata",
    "ValidatedOriginalMediaAttestation",
    "bind_attestation_for_phase3p",
    "validate_original_media_attestation",
)
