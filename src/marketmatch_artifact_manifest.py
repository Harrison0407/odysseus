"""Pure sealed-manifest validation for future MarketMatch artifacts.

Version 1 proves only that manifest data satisfies this module's closed schema,
that its declared roles satisfy an explicit immutable role policy, and that
the SHA-256 digest of its canonical subject matches ``manifest_digest``.

It does not prove that payload bytes exist or match their declarations.  It
makes no claim about filesystems, publication, durability, SQL state, owner
authorization, application visibility, STT, ffmpeg, Chroma, or RAG.

The module is deterministic and side-effect free.  It accepts exact ``bytes``
or already-parsed built-in pure data and retains no caller-owned object.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import hmac
import json
import re
from typing import NoReturn


SCHEMA_VERSION = "marketmatch-artifact-manifest-v1"

MAX_MANIFEST_BYTES = 16_384
MAX_PAYLOAD_COUNT = 16
MAX_OPERATION_ID_LENGTH = 48
MAX_RELATIVE_NAME_LENGTH = 40
MAX_BYTE_SIZE = 1_073_741_824
MAX_NESTING_DEPTH = 3


class ManifestDomain(str, Enum):
    """Closed MarketMatch artifact domains."""

    CALLS = "calls"
    VIDEOS = "videos"


class ManifestCode(str, Enum):
    """Fixed privacy-preserving validation result codes."""

    INVALID_INPUT_TYPE = "INVALID_INPUT_TYPE"
    INVALID_JSON = "INVALID_JSON"
    DUPLICATE_JSON_KEY = "DUPLICATE_JSON_KEY"
    TRAILING_JSON_DATA = "TRAILING_JSON_DATA"
    UNKNOWN_SCHEMA = "UNKNOWN_SCHEMA"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    INVALID_DOMAIN = "INVALID_DOMAIN"
    INVALID_OPERATION_ID = "INVALID_OPERATION_ID"
    INVALID_STAGE = "INVALID_STAGE"
    INVALID_ROLE_POLICY = "INVALID_ROLE_POLICY"
    UNKNOWN_ROLE = "UNKNOWN_ROLE"
    DUPLICATE_ROLE = "DUPLICATE_ROLE"
    MISSING_REQUIRED_ROLE = "MISSING_REQUIRED_ROLE"
    INVALID_RELATIVE_NAME = "INVALID_RELATIVE_NAME"
    INVALID_BYTE_SIZE = "INVALID_BYTE_SIZE"
    INVALID_SHA256 = "INVALID_SHA256"
    INVALID_PAYLOAD_COUNT = "INVALID_PAYLOAD_COUNT"
    INVALID_PAYLOAD_ORDER = "INVALID_PAYLOAD_ORDER"
    NON_CANONICAL_JSON = "NON_CANONICAL_JSON"
    MANIFEST_DIGEST_MISSING = "MANIFEST_DIGEST_MISSING"
    MANIFEST_DIGEST_MISMATCH = "MANIFEST_DIGEST_MISMATCH"
    PRIVACY_POLICY_VIOLATION = "PRIVACY_POLICY_VIOLATION"


class ManifestValidationError(Exception):
    """A fixed-code failure that never embeds rejected input."""

    def __init__(self, code: ManifestCode):
        if not isinstance(code, ManifestCode):
            code = ManifestCode.INVALID_INPUT_TYPE
        self.code = code
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r})"


@dataclass(frozen=True, slots=True)
class RolePolicy:
    """Closed required and optional roles for exactly one domain and stage."""

    domain: ManifestDomain
    stage: str
    required_roles: tuple[str, ...]
    optional_roles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_role_policy(self)


@dataclass(frozen=True, slots=True)
class ValidatedPayload:
    """One immutable, internally validated payload declaration."""

    role: str
    relative_name: str
    byte_size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ValidatedManifest:
    """Immutable validated representation detached from caller-owned data."""

    schema: str
    domain: ManifestDomain
    operation_id: str
    stage: str
    payloads: tuple[ValidatedPayload, ...]
    manifest_digest: str
    canonical_bytes: bytes


_TOP_LEVEL_FIELDS = frozenset(
    {"schema", "domain", "operation_id", "stage", "payloads", "manifest_digest"}
)
_PAYLOAD_FIELDS = frozenset({"role", "relative_name", "byte_size", "sha256"})

_STAGE_ROLE_MATRIX = (
    (ManifestDomain.CALLS, "rag_ready", "rag_ready", ("rag_ready",)),
    (ManifestDomain.CALLS, "summary", "summary", ("summary",)),
    (ManifestDomain.CALLS, "transcript", "transcript", ("transcript",)),
    (ManifestDomain.VIDEOS, "audio_extraction", "extracted_audio", ("extracted_audio",)),
    (
        ManifestDomain.VIDEOS,
        "clip_export",
        "clip_export",
        ("clip_export", "clip_metadata"),
    ),
    (ManifestDomain.VIDEOS, "clip_metadata", "clip_metadata", ("clip_metadata",)),
    (ManifestDomain.VIDEOS, "rag_ready", "rag_ready", ("rag_ready",)),
    (ManifestDomain.VIDEOS, "summary", "summary", ("summary",)),
    (ManifestDomain.VIDEOS, "transcript", "transcript", ("transcript",)),
)

_OPERATION_ID_RE = re.compile(r"mmop-(calls|videos)-[a-z0-9]{26}\Z", re.ASCII)
_RELATIVE_NAME_RE = re.compile(r"mma-[a-z0-9]{32}\Z", re.ASCII)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_MAX_ROLE_LENGTH = 32
_MAX_STAGE_LENGTH = 32


class _DuplicateKeyError(Exception):
    pass


class _DisallowedNumberError(Exception):
    pass


def _fail(code: ManifestCode) -> NoReturn:
    raise ManifestValidationError(code) from None


def _contract_for_stage(
    domain: ManifestDomain,
    stage: str,
) -> tuple[str, tuple[str, ...]] | None:
    for candidate_domain, candidate_stage, primary_role, roles in _STAGE_ROLE_MATRIX:
        if candidate_domain is domain and candidate_stage == stage:
            return primary_role, roles
    return None


def _validate_role_tuple(value: object) -> tuple[str, ...]:
    if type(value) is not tuple:
        _fail(ManifestCode.INVALID_ROLE_POLICY)
    if not value or len(value) > MAX_PAYLOAD_COUNT:
        _fail(ManifestCode.INVALID_ROLE_POLICY)
    if any(
        type(role) is not str
        or len(role) > _MAX_ROLE_LENGTH
        or not role.isascii()
        for role in value
    ):
        _fail(ManifestCode.INVALID_ROLE_POLICY)
    if value != tuple(sorted(value)) or len(value) != len(frozenset(value)):
        _fail(ManifestCode.INVALID_ROLE_POLICY)
    return value


def _validate_role_policy(policy: object) -> RolePolicy:
    if type(policy) is not RolePolicy:
        _fail(ManifestCode.INVALID_ROLE_POLICY)
    if type(policy.domain) is not ManifestDomain or type(policy.stage) is not str:
        _fail(ManifestCode.INVALID_ROLE_POLICY)
    stage_contract = _contract_for_stage(policy.domain, policy.stage)
    if stage_contract is None:
        _fail(ManifestCode.INVALID_ROLE_POLICY)
    primary_role, allowed_for_stage = stage_contract
    required = _validate_role_tuple(policy.required_roles)
    optional = policy.optional_roles
    if type(optional) is not tuple:
        _fail(ManifestCode.INVALID_ROLE_POLICY)
    if len(optional) > MAX_PAYLOAD_COUNT or any(
        type(role) is not str
        or len(role) > _MAX_ROLE_LENGTH
        or not role.isascii()
        for role in optional
    ):
        _fail(ManifestCode.INVALID_ROLE_POLICY)
    if optional != tuple(sorted(optional)) or len(optional) != len(frozenset(optional)):
        _fail(ManifestCode.INVALID_ROLE_POLICY)
    if frozenset(required) & frozenset(optional):
        _fail(ManifestCode.INVALID_ROLE_POLICY)
    policy_roles = frozenset(required + optional)
    if (
        not policy_roles
        or len(required) + len(optional) > MAX_PAYLOAD_COUNT
        or primary_role not in required
        or not policy_roles <= frozenset(allowed_for_stage)
    ):
        _fail(ManifestCode.INVALID_ROLE_POLICY)
    return policy


def _duplicate_rejecting_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = tuple(key for key, _ in pairs)
    if len(keys) != len(frozenset(keys)):
        raise _DuplicateKeyError
    return {key: value for key, value in pairs}


def _parse_integer(token: str) -> int:
    if len(token) > 20:
        raise _DisallowedNumberError
    return int(token, 10)


def _reject_number(token: str) -> NoReturn:
    del token
    raise _DisallowedNumberError


def _parse_json_bytes(raw: bytes) -> dict[str, object]:
    if len(raw) > MAX_MANIFEST_BYTES:
        _fail(ManifestCode.INVALID_JSON)
    if not raw or raw.startswith(b"\xef\xbb\xbf"):
        _fail(ManifestCode.INVALID_JSON)
    if raw != raw.strip():
        _fail(ManifestCode.NON_CANONICAL_JSON)
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        _fail(ManifestCode.NON_CANONICAL_JSON)

    decoder = json.JSONDecoder(
        object_pairs_hook=_duplicate_rejecting_object,
        parse_float=_reject_number,
        parse_int=_parse_integer,
        parse_constant=_reject_number,
        strict=True,
    )
    try:
        value, end = decoder.raw_decode(text)
    except _DuplicateKeyError:
        _fail(ManifestCode.DUPLICATE_JSON_KEY)
    except _DisallowedNumberError:
        _fail(ManifestCode.INVALID_JSON)
    except (json.JSONDecodeError, RecursionError, ValueError):
        _fail(ManifestCode.INVALID_JSON)

    if end != len(text):
        if text[end:].strip():
            _fail(ManifestCode.TRAILING_JSON_DATA)
        _fail(ManifestCode.NON_CANONICAL_JSON)
    if type(value) is not dict:
        _fail(ManifestCode.INVALID_INPUT_TYPE)
    return value


def _require_exact_fields(
    value: object,
    expected: frozenset[str],
    *,
    digest_is_special: bool = False,
) -> dict[str, object]:
    if type(value) is not dict:
        _fail(ManifestCode.INVALID_INPUT_TYPE)
    if len(value) > len(expected):
        _fail(ManifestCode.UNKNOWN_FIELD)
    keys = tuple(value.keys())
    if any(type(key) is not str for key in keys):
        _fail(ManifestCode.INVALID_INPUT_TYPE)
    present = frozenset(keys)
    if present - expected:
        _fail(ManifestCode.UNKNOWN_FIELD)
    missing = expected - present
    if digest_is_special and "manifest_digest" in missing:
        _fail(ManifestCode.MANIFEST_DIGEST_MISSING)
    if missing:
        _fail(ManifestCode.MISSING_FIELD)
    return value


def _require_ascii_string(
    value: object,
    code: ManifestCode,
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


def _validate_closed_nesting(value: object, depth: int = 0) -> None:
    if depth > MAX_NESTING_DEPTH:
        _fail(ManifestCode.INVALID_INPUT_TYPE)
    if type(value) is dict:
        if len(value) > len(_TOP_LEVEL_FIELDS):
            _fail(ManifestCode.UNKNOWN_FIELD)
        for key, item in value.items():
            if type(key) is not str:
                _fail(ManifestCode.INVALID_INPUT_TYPE)
            _validate_closed_nesting(item, depth + 1)
    elif type(value) is list:
        if len(value) > MAX_PAYLOAD_COUNT:
            _fail(ManifestCode.INVALID_PAYLOAD_COUNT)
        for item in value:
            _validate_closed_nesting(item, depth + 1)


def _validate_domain(value: object, expected_domain: ManifestDomain) -> ManifestDomain:
    if type(expected_domain) is not ManifestDomain:
        _fail(ManifestCode.INVALID_DOMAIN)
    domain_text = _require_ascii_string(
        value,
        ManifestCode.INVALID_DOMAIN,
        maximum_length=max(len(domain.value) for domain in ManifestDomain),
    )
    try:
        domain = ManifestDomain(domain_text)
    except ValueError:
        _fail(ManifestCode.INVALID_DOMAIN)
    if domain is not expected_domain:
        _fail(ManifestCode.INVALID_DOMAIN)
    return domain


def _validate_operation_id(value: object, domain: ManifestDomain) -> str:
    operation_id = _require_ascii_string(
        value,
        ManifestCode.INVALID_OPERATION_ID,
        maximum_length=MAX_OPERATION_ID_LENGTH,
    )
    match = _OPERATION_ID_RE.fullmatch(operation_id)
    if match is None or match.group(1) != domain.value:
        _fail(ManifestCode.INVALID_OPERATION_ID)
    return operation_id


def _validate_stage(value: object, domain: ManifestDomain) -> str:
    stage = _require_ascii_string(
        value,
        ManifestCode.INVALID_STAGE,
        maximum_length=_MAX_STAGE_LENGTH,
    )
    if _contract_for_stage(domain, stage) is None:
        _fail(ManifestCode.INVALID_STAGE)
    return stage


def _validate_relative_name(value: object) -> str:
    relative_name = _require_ascii_string(
        value,
        ManifestCode.INVALID_RELATIVE_NAME,
        maximum_length=MAX_RELATIVE_NAME_LENGTH,
    )
    if _RELATIVE_NAME_RE.fullmatch(relative_name) is None:
        _fail(ManifestCode.INVALID_RELATIVE_NAME)
    return relative_name


def _validate_byte_size(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_BYTE_SIZE:
        _fail(ManifestCode.INVALID_BYTE_SIZE)
    return value


def _validate_sha256(value: object) -> str:
    digest = _require_ascii_string(
        value,
        ManifestCode.INVALID_SHA256,
        maximum_length=64,
    )
    if _SHA256_RE.fullmatch(digest) is None:
        _fail(ManifestCode.INVALID_SHA256)
    return digest


def _validate_payloads(
    value: object,
    *,
    allowed_roles: tuple[str, ...],
    required_roles: tuple[str, ...],
) -> tuple[ValidatedPayload, ...]:
    if type(value) is not list:
        _fail(ManifestCode.INVALID_INPUT_TYPE)
    if not 1 <= len(value) <= MAX_PAYLOAD_COUNT:
        _fail(ManifestCode.INVALID_PAYLOAD_COUNT)

    payloads = tuple(
        _validate_payload(entry, allowed_roles=allowed_roles)
        for entry in value
    )
    roles = tuple(payload.role for payload in payloads)
    if len(roles) != len(frozenset(roles)):
        _fail(ManifestCode.DUPLICATE_ROLE)
    names = tuple(payload.relative_name for payload in payloads)
    if len(names) != len(frozenset(names)):
        _fail(ManifestCode.INVALID_RELATIVE_NAME)
    if not frozenset(required_roles) <= frozenset(roles):
        _fail(ManifestCode.MISSING_REQUIRED_ROLE)
    ordering = tuple((payload.role, payload.relative_name) for payload in payloads)
    if ordering != tuple(sorted(ordering)):
        _fail(ManifestCode.INVALID_PAYLOAD_ORDER)
    return payloads


def _validate_payload(
    value: object,
    *,
    allowed_roles: tuple[str, ...],
) -> ValidatedPayload:
    payload = _require_exact_fields(value, _PAYLOAD_FIELDS)
    role = _require_ascii_string(
        payload["role"],
        ManifestCode.UNKNOWN_ROLE,
        maximum_length=_MAX_ROLE_LENGTH,
    )
    if role not in allowed_roles:
        _fail(ManifestCode.UNKNOWN_ROLE)
    return ValidatedPayload(
        role=role,
        relative_name=_validate_relative_name(payload["relative_name"]),
        byte_size=_validate_byte_size(payload["byte_size"]),
        sha256=_validate_sha256(payload["sha256"]),
    )


def _canonical_json(value: dict[str, object]) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail(ManifestCode.INVALID_INPUT_TYPE)
    if len(encoded) > MAX_MANIFEST_BYTES:
        _fail(ManifestCode.INVALID_JSON)
    return encoded


def _subject_data(
    *,
    domain: ManifestDomain,
    operation_id: str,
    stage: str,
    payloads: tuple[ValidatedPayload, ...],
) -> dict[str, object]:
    return {
        "domain": domain.value,
        "operation_id": operation_id,
        "payloads": [
            {
                "byte_size": payload.byte_size,
                "relative_name": payload.relative_name,
                "role": payload.role,
                "sha256": payload.sha256,
            }
            for payload in payloads
        ],
        "schema": SCHEMA_VERSION,
        "stage": stage,
    }


def validate_manifest(
    manifest: bytes | dict[str, object],
    *,
    expected_domain: ManifestDomain,
    role_policy: RolePolicy,
) -> ValidatedManifest:
    """Validate one sealed-stage manifest without performing any I/O.

    Byte input must already be exact canonical JSON.  Parsed input must use
    exact built-in JSON container and scalar types.  The returned digest proves
    only the internally consistent declaration described in the module
    documentation; future code must independently verify payload bytes.
    """

    policy = _validate_role_policy(role_policy)
    if type(expected_domain) is not ManifestDomain:
        _fail(ManifestCode.INVALID_DOMAIN)
    if policy.domain is not expected_domain:
        _fail(ManifestCode.INVALID_ROLE_POLICY)

    original_bytes: bytes | None
    if type(manifest) is bytes:
        original_bytes = manifest
        data = _parse_json_bytes(manifest)
    elif type(manifest) is dict:
        original_bytes = None
        data = manifest
    else:
        _fail(ManifestCode.INVALID_INPUT_TYPE)

    top = _require_exact_fields(data, _TOP_LEVEL_FIELDS, digest_is_special=True)
    _validate_closed_nesting(top)
    schema = _require_ascii_string(
        top["schema"],
        ManifestCode.UNKNOWN_SCHEMA,
        maximum_length=len(SCHEMA_VERSION),
    )
    if schema != SCHEMA_VERSION:
        _fail(ManifestCode.UNKNOWN_SCHEMA)
    domain = _validate_domain(top["domain"], expected_domain)
    operation_id = _validate_operation_id(top["operation_id"], domain)
    stage = _validate_stage(top["stage"], domain)
    if policy.stage != stage:
        _fail(ManifestCode.INVALID_ROLE_POLICY)

    allowed_roles = policy.required_roles + policy.optional_roles
    payloads = _validate_payloads(
        top["payloads"],
        allowed_roles=allowed_roles,
        required_roles=policy.required_roles,
    )
    supplied_digest = _validate_sha256(top["manifest_digest"])

    subject = _subject_data(
        domain=domain,
        operation_id=operation_id,
        stage=stage,
        payloads=payloads,
    )
    computed_digest = hashlib.sha256(_canonical_json(subject)).hexdigest()
    if not hmac.compare_digest(computed_digest, supplied_digest):
        _fail(ManifestCode.MANIFEST_DIGEST_MISMATCH)

    canonical_bytes = _canonical_json({**subject, "manifest_digest": computed_digest})
    if original_bytes is not None and not hmac.compare_digest(original_bytes, canonical_bytes):
        _fail(ManifestCode.NON_CANONICAL_JSON)

    return ValidatedManifest(
        schema=SCHEMA_VERSION,
        domain=domain,
        operation_id=operation_id,
        stage=stage,
        payloads=payloads,
        manifest_digest=computed_digest,
        canonical_bytes=canonical_bytes,
    )


__all__ = (
    "MAX_BYTE_SIZE",
    "MAX_MANIFEST_BYTES",
    "MAX_NESTING_DEPTH",
    "MAX_OPERATION_ID_LENGTH",
    "MAX_PAYLOAD_COUNT",
    "MAX_RELATIVE_NAME_LENGTH",
    "ManifestCode",
    "ManifestDomain",
    "ManifestValidationError",
    "RolePolicy",
    "SCHEMA_VERSION",
    "ValidatedManifest",
    "ValidatedPayload",
    "validate_manifest",
)
