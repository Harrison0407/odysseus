"""Pure in-memory verification of sealed MarketMatch artifact payloads.

This module proves only that an explicit set of in-memory payload bytes matches
the size and SHA-256 declarations in an already validated sealed manifest.  It
makes no claim about filesystems, publication, durability, SQL state, owner
authorization, STT correctness, workflow completion, application visibility,
Chroma, or RAG.

Inputs are handled deterministically without I/O or mutable retained state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import hmac
from typing import NoReturn

from src.marketmatch_artifact_manifest import (
    MAX_BYTE_SIZE,
    MAX_MANIFEST_BYTES,
    MAX_OPERATION_ID_LENGTH,
    MAX_PAYLOAD_COUNT,
    MAX_RELATIVE_NAME_LENGTH,
    ManifestDomain,
    ManifestValidationError,
    RolePolicy,
    SCHEMA_VERSION,
    ValidatedManifest,
    ValidatedPayload,
    validate_manifest,
)


MAX_PAYLOAD_BYTES = MAX_BYTE_SIZE
MAX_AGGREGATE_PAYLOAD_BYTES = 134_217_728
_MAX_ROLE_LENGTH = 32
_MAX_STAGE_LENGTH = 32


class PayloadVerificationCode(str, Enum):
    """Fixed privacy-preserving payload verification codes."""

    INVALID_MANIFEST_OBJECT = "INVALID_MANIFEST_OBJECT"
    INVALID_PAYLOAD_CONTAINER = "INVALID_PAYLOAD_CONTAINER"
    INVALID_PAYLOAD_ROLE = "INVALID_PAYLOAD_ROLE"
    INVALID_PAYLOAD_TYPE = "INVALID_PAYLOAD_TYPE"
    MISSING_PAYLOAD = "MISSING_PAYLOAD"
    UNDECLARED_PAYLOAD = "UNDECLARED_PAYLOAD"
    DUPLICATE_PAYLOAD = "DUPLICATE_PAYLOAD"
    PAYLOAD_SIZE_MISMATCH = "PAYLOAD_SIZE_MISMATCH"
    PAYLOAD_DIGEST_MISMATCH = "PAYLOAD_DIGEST_MISMATCH"
    PAYLOAD_COUNT_MISMATCH = "PAYLOAD_COUNT_MISMATCH"
    DOMAIN_STAGE_ROLE_MISMATCH = "DOMAIN_STAGE_ROLE_MISMATCH"
    PAYLOAD_LIMIT_EXCEEDED = "PAYLOAD_LIMIT_EXCEEDED"
    PRIVACY_POLICY_VIOLATION = "PRIVACY_POLICY_VIOLATION"


class PayloadVerificationError(Exception):
    """A fixed-code failure that never embeds rejected input."""

    def __init__(self, code: PayloadVerificationCode):
        if not isinstance(code, PayloadVerificationCode):
            code = PayloadVerificationCode.INVALID_PAYLOAD_CONTAINER
        self.code = code
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r})"


@dataclass(frozen=True, slots=True)
class VerifiedPayload:
    """One immutable payload declaration paired with verified immutable bytes."""

    role: str
    relative_name: str
    byte_size: int
    sha256: str
    payload_bytes: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class VerifiedPayloadSet:
    """An immutable, manifest-ordered set of verified in-memory payloads."""

    payloads: tuple[VerifiedPayload, ...]


def _fail(code: PayloadVerificationCode) -> NoReturn:
    raise PayloadVerificationError(code) from None


def _trusted_manifest(value: object) -> ValidatedManifest:
    if type(value) is not ValidatedManifest:
        _fail(PayloadVerificationCode.INVALID_MANIFEST_OBJECT)
    if (
        type(value.schema) is not str
        or type(value.domain) is not ManifestDomain
        or type(value.operation_id) is not str
        or type(value.stage) is not str
        or type(value.manifest_digest) is not str
        or type(value.canonical_bytes) is not bytes
        or len(value.schema) > len(SCHEMA_VERSION)
        or len(value.operation_id) > MAX_OPERATION_ID_LENGTH
        or len(value.stage) > _MAX_STAGE_LENGTH
        or len(value.manifest_digest) > 64
        or len(value.canonical_bytes) > MAX_MANIFEST_BYTES
        or type(value.payloads) is not tuple
        or not 1 <= len(value.payloads) <= MAX_PAYLOAD_COUNT
    ):
        _fail(PayloadVerificationCode.INVALID_MANIFEST_OBJECT)
    if any(type(payload) is not ValidatedPayload for payload in value.payloads):
        _fail(PayloadVerificationCode.INVALID_MANIFEST_OBJECT)
    if any(
        type(payload.role) is not str
        or type(payload.relative_name) is not str
        or type(payload.byte_size) is not int
        or type(payload.sha256) is not str
        or len(payload.role) > _MAX_ROLE_LENGTH
        or len(payload.relative_name) > MAX_RELATIVE_NAME_LENGTH
        or not 0 <= payload.byte_size <= MAX_BYTE_SIZE
        or len(payload.sha256) > 64
        for payload in value.payloads
    ):
        _fail(PayloadVerificationCode.INVALID_MANIFEST_OBJECT)

    roles = tuple(payload.role for payload in value.payloads)
    if len(roles) != len(frozenset(roles)):
        _fail(PayloadVerificationCode.DUPLICATE_PAYLOAD)
    try:
        policy = RolePolicy(
            domain=value.domain,
            stage=value.stage,
            required_roles=roles,
        )
        trusted = validate_manifest(
            value.canonical_bytes,
            expected_domain=value.domain,
            role_policy=policy,
        )
    except ManifestValidationError:
        _fail(PayloadVerificationCode.DOMAIN_STAGE_ROLE_MISMATCH)
    except Exception:
        _fail(PayloadVerificationCode.INVALID_MANIFEST_OBJECT)
    if trusted != value:
        _fail(PayloadVerificationCode.DOMAIN_STAGE_ROLE_MISMATCH)
    return trusted


def _payload_size(value: object) -> int:
    if type(value) is bytes:
        return len(value)
    if type(value) is memoryview:
        try:
            if (
                not value.readonly
                or value.ndim != 1
                or value.itemsize != 1
                or value.format not in ("B", "b", "c")
                or not value.c_contiguous
                or type(value.obj) is not bytes
            ):
                _fail(PayloadVerificationCode.INVALID_PAYLOAD_TYPE)
            return value.nbytes
        except PayloadVerificationError:
            raise
        except Exception:
            _fail(PayloadVerificationCode.INVALID_PAYLOAD_TYPE)
    _fail(PayloadVerificationCode.INVALID_PAYLOAD_TYPE)


def _immutable_bytes(value: object) -> bytes:
    if type(value) is bytes:
        return value
    if type(value) is memoryview:
        try:
            return value.tobytes()
        except Exception:
            _fail(PayloadVerificationCode.INVALID_PAYLOAD_TYPE)
    _fail(PayloadVerificationCode.INVALID_PAYLOAD_TYPE)


def verify_payloads(
    manifest: ValidatedManifest,
    payloads: dict[str, object],
) -> VerifiedPayloadSet:
    """Verify explicit in-memory payload bytes against a sealed manifest.

    ``payloads`` must be an exact built-in dictionary with one exact string key
    per declared manifest role.  Content must be exact ``bytes`` or a read-only,
    contiguous, one-byte ``memoryview`` backed by exact immutable ``bytes``;
    memoryviews are copied before hashing and are never retained.  Mutable
    bytearrays, external-buffer views, and lazy/custom mappings are rejected.
    """

    trusted = _trusted_manifest(manifest)
    if type(payloads) is not dict:
        _fail(PayloadVerificationCode.INVALID_PAYLOAD_CONTAINER)
    if len(payloads) > MAX_PAYLOAD_COUNT:
        _fail(PayloadVerificationCode.PAYLOAD_LIMIT_EXCEEDED)

    try:
        supplied = tuple(payloads.items())
    except Exception:
        _fail(PayloadVerificationCode.INVALID_PAYLOAD_CONTAINER)
    supplied_roles = tuple(role for role, _ in supplied)
    if any(
        type(role) is not str
        or not role
        or len(role) > _MAX_ROLE_LENGTH
        or not role.isascii()
        for role in supplied_roles
    ):
        _fail(PayloadVerificationCode.INVALID_PAYLOAD_ROLE)
    if len(supplied_roles) != len(frozenset(supplied_roles)):
        _fail(PayloadVerificationCode.DUPLICATE_PAYLOAD)

    declared_roles = tuple(payload.role for payload in trusted.payloads)
    supplied_role_set = frozenset(supplied_roles)
    declared_role_set = frozenset(declared_roles)
    if supplied_role_set - declared_role_set:
        _fail(PayloadVerificationCode.UNDECLARED_PAYLOAD)
    if declared_role_set - supplied_role_set:
        _fail(PayloadVerificationCode.MISSING_PAYLOAD)
    if len(supplied) != len(trusted.payloads):
        _fail(PayloadVerificationCode.PAYLOAD_COUNT_MISMATCH)

    supplied_by_role = dict(supplied)
    aggregate_size = 0
    for declaration in trusted.payloads:
        size = _payload_size(supplied_by_role[declaration.role])
        if size > MAX_PAYLOAD_BYTES:
            _fail(PayloadVerificationCode.PAYLOAD_LIMIT_EXCEEDED)
        aggregate_size += size
        if aggregate_size > MAX_AGGREGATE_PAYLOAD_BYTES:
            _fail(PayloadVerificationCode.PAYLOAD_LIMIT_EXCEEDED)
        if size != declaration.byte_size:
            _fail(PayloadVerificationCode.PAYLOAD_SIZE_MISMATCH)

    verified: tuple[VerifiedPayload, ...] = ()
    for declaration in trusted.payloads:
        content = _immutable_bytes(supplied_by_role[declaration.role])
        if len(content) != declaration.byte_size:
            _fail(PayloadVerificationCode.PAYLOAD_SIZE_MISMATCH)
        digest = hashlib.sha256(content).hexdigest()
        if not hmac.compare_digest(digest, declaration.sha256):
            _fail(PayloadVerificationCode.PAYLOAD_DIGEST_MISMATCH)
        verified += (
            VerifiedPayload(
                role=declaration.role,
                relative_name=declaration.relative_name,
                byte_size=declaration.byte_size,
                sha256=digest,
                payload_bytes=content,
            ),
        )
    return VerifiedPayloadSet(payloads=verified)


__all__ = (
    "MAX_AGGREGATE_PAYLOAD_BYTES",
    "MAX_PAYLOAD_BYTES",
    "PayloadVerificationCode",
    "PayloadVerificationError",
    "VerifiedPayload",
    "VerifiedPayloadSet",
    "verify_payloads",
)
