"""Synchronous one-pass verification of original MarketMatch media streams.

This module consumes one caller-owned binary reader or iterable of exact
``bytes`` chunks.  It computes size and SHA-256 directly from the observed
original bytes, generates fresh server-side identifiers, and creates a
canonical Phase 3Q attestation declaring ``verified_ingest`` authority.

No media bytes are retained or returned.  The ingest path neither rewinds nor
reiterates its source and uses memory proportional only to the largest current
chunk plus constant-size hashing, identifier, and attestation state.  Reader
calls are synchronous and may block; timeouts and cancellation remain future
integration responsibilities.

The attestation declaration records what this component observed.  Its
unkeyed digest protects canonical subject integrity but does not
cryptographically authenticate an issuer, authorize ownership, prove later
media existence, or establish persistence, publication, SQL, STT, or workflow
completion.

Operation and media identifiers use independent 128-bit cryptographic-random
samples.  This makes collisions negligible but does not reserve identifiers
or guarantee global uniqueness; future persistence must perform collision-safe
reservation.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import inspect
import json
import secrets
from typing import Callable, NoReturn

from src.marketmatch_media_attestation import (
    AttestationAuthority,
    MAX_ORIGINAL_MEDIA_BYTES,
    MediaDomain,
    ORIGINAL_MEDIA_ATTESTATION_SCHEMA,
    OriginalMediaRole,
    validate_original_media_attestation,
)


VERIFIED_MEDIA_INGEST_CHUNK_BYTES = 65_536
MAX_VERIFIED_MEDIA_INGEST_STEPS = 4_096

_IDENTIFIER_RANDOM_BYTES = 16
_MISSING = object()


class VerifiedMediaIngestCode(str, Enum):
    """Fixed privacy-preserving ingest result codes."""

    INVALID_SOURCE = "INVALID_SOURCE"
    INVALID_DOMAIN = "INVALID_DOMAIN"
    INVALID_BYTE_LIMIT = "INVALID_BYTE_LIMIT"
    INVALID_STREAM = "INVALID_STREAM"
    INVALID_ITERABLE = "INVALID_ITERABLE"
    INVALID_CHUNK = "INVALID_CHUNK"
    INVALID_READ_RESULT = "INVALID_READ_RESULT"
    SOURCE_FAILED = "SOURCE_FAILED"
    INPUT_LIMIT_EXCEEDED = "INPUT_LIMIT_EXCEEDED"
    STEP_LIMIT_EXCEEDED = "STEP_LIMIT_EXCEEDED"
    ID_GENERATION_FAILED = "ID_GENERATION_FAILED"
    ATTESTATION_FAILED = "ATTESTATION_FAILED"


class VerifiedMediaIngestError(Exception):
    """A fixed-code failure that never embeds source or media data."""

    def __init__(self, code: VerifiedMediaIngestCode):
        if type(code) is not VerifiedMediaIngestCode:
            code = VerifiedMediaIngestCode.INVALID_SOURCE
        self.code = code
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r})"


@dataclass(frozen=True, slots=True)
class VerifiedOriginalMediaIngest:
    """Immutable metadata result that contains no original media bytes."""

    domain: MediaDomain = field(repr=False)
    operation_id: str = field(repr=False)
    media_id: str = field(repr=False)
    media_role: OriginalMediaRole = field(repr=False)
    byte_size: int = field(repr=False)
    sha256: str = field(repr=False)
    attestation_canonical_bytes: bytes = field(repr=False, hash=False)


def _fail(code: VerifiedMediaIngestCode) -> NoReturn:
    raise VerifiedMediaIngestError(code) from None


def _validate_inputs(domain: object, byte_limit: object) -> tuple[MediaDomain, int]:
    if type(domain) is not MediaDomain:
        _fail(VerifiedMediaIngestCode.INVALID_DOMAIN)
    if (
        type(byte_limit) is not int
        or byte_limit < 0
        or byte_limit > MAX_ORIGINAL_MEDIA_BYTES
    ):
        _fail(VerifiedMediaIngestCode.INVALID_BYTE_LIMIT)
    return domain, byte_limit


def _reject_native_coroutine(
    value: object,
    code: VerifiedMediaIngestCode,
) -> None:
    if not inspect.iscoroutine(value):
        return
    cleanup_failed = False
    try:
        value.close()
    except Exception:
        cleanup_failed = True
    if cleanup_failed:
        _fail(code)
    _fail(code)


def _reject_async_callable(
    value: object,
    code: VerifiedMediaIngestCode,
) -> None:
    failed = False
    is_async = False
    call_method: object = _MISSING
    try:
        is_async = inspect.iscoroutinefunction(value)
        if not is_async:
            call_method = value.__call__
    except Exception:
        failed = True
    if failed:
        _fail(code)
    if call_method is not _MISSING:
        _reject_native_coroutine(call_method, code)
        try:
            is_async = inspect.iscoroutinefunction(call_method)
        except Exception:
            failed = True
    if failed or is_async:
        _fail(code)


def _get_read_method(source: object) -> Callable[[int], object] | None:
    primitive_check_failed = False
    is_primitive = False
    try:
        is_primitive = issubclass(
            type(source),
            (bytes, bytearray, memoryview, str, int, float),
        )
    except Exception:
        primitive_check_failed = True
    if primitive_check_failed:
        _fail(VerifiedMediaIngestCode.INVALID_SOURCE)
    if (
        source is None
        or is_primitive
        or callable(source)
    ):
        _fail(VerifiedMediaIngestCode.INVALID_SOURCE)

    failed = False
    read_method: object = _MISSING
    try:
        read_method = getattr(source, "read", _MISSING)
    except Exception:
        failed = True
    if failed:
        _fail(VerifiedMediaIngestCode.INVALID_SOURCE)
    if read_method is _MISSING:
        return None
    _reject_native_coroutine(read_method, VerifiedMediaIngestCode.INVALID_STREAM)
    if not callable(read_method):
        _fail(VerifiedMediaIngestCode.INVALID_STREAM)
    _reject_async_callable(read_method, VerifiedMediaIngestCode.INVALID_STREAM)
    return read_method


def _lookup_type_descriptor(
    value: object,
    name: str,
    code: VerifiedMediaIngestCode,
    *,
    required: bool,
) -> object:
    lookup_failed = False
    descriptor: object = _MISSING
    try:
        value_type = type(value)
        for base in type.__getattribute__(value_type, "__mro__"):
            namespace = type.__getattribute__(base, "__dict__")
            if name in namespace:
                descriptor = namespace[name]
                break
    except Exception:
        lookup_failed = True
    if lookup_failed or (required and descriptor is _MISSING):
        _fail(code)
    return descriptor


def _bind_special_method(
    value: object,
    name: str,
    code: VerifiedMediaIngestCode,
    *,
    required: bool,
) -> Callable[..., object] | None:
    descriptor = _lookup_type_descriptor(
        value,
        name,
        code,
        required=required,
    )
    if descriptor is _MISSING:
        return None

    descriptor_get = _lookup_type_descriptor(
        descriptor,
        "__get__",
        code,
        required=False,
    )
    bound_method: object = descriptor
    if descriptor_get is not _MISSING:
        _reject_native_coroutine(descriptor_get, code)
        if not callable(descriptor_get):
            _fail(code)
        _reject_async_callable(descriptor_get, code)
        binding_failed = False
        try:
            bound_method = descriptor_get(descriptor, value, type(value))
        except Exception:
            binding_failed = True
        if binding_failed:
            _fail(code)

    _reject_native_coroutine(bound_method, code)
    if not callable(bound_method):
        _fail(code)
    _reject_async_callable(bound_method, code)
    return bound_method


def _update_observation(
    digest: object,
    observed_size: int,
    chunk: bytes,
    byte_limit: int,
) -> int:
    new_size = observed_size + len(chunk)
    if new_size > byte_limit:
        _fail(VerifiedMediaIngestCode.INPUT_LIMIT_EXCEEDED)
    digest.update(chunk)
    return new_size


def _consume_reader(
    read_method: Callable[[int], object],
    byte_limit: int,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    observed_size = 0
    read_calls = 0

    while True:
        if read_calls >= MAX_VERIFIED_MEDIA_INGEST_STEPS:
            _fail(VerifiedMediaIngestCode.STEP_LIMIT_EXCEEDED)
        request_size = min(
            VERIFIED_MEDIA_INGEST_CHUNK_BYTES,
            byte_limit + 1 - observed_size,
        )
        failed = False
        result: object = None
        try:
            result = read_method(request_size)
        except Exception:
            failed = True
        read_calls += 1
        if failed:
            _fail(VerifiedMediaIngestCode.SOURCE_FAILED)
        _reject_native_coroutine(
            result,
            VerifiedMediaIngestCode.INVALID_READ_RESULT,
        )
        if type(result) is not bytes or len(result) > request_size:
            _fail(VerifiedMediaIngestCode.INVALID_READ_RESULT)
        if result == b"":
            break
        observed_size = _update_observation(
            digest,
            observed_size,
            result,
            byte_limit,
        )

    return observed_size, digest.hexdigest()


def _get_next_method(source: object) -> Callable[[], object]:
    iter_method = _bind_special_method(
        source,
        "__iter__",
        VerifiedMediaIngestCode.INVALID_ITERABLE,
        required=False,
    )
    iterator_failed = False
    iterator: object = None
    if iter_method is None:
        try:
            iterator = iter(source)
        except Exception:
            iterator_failed = True
    else:
        try:
            iterator = iter_method()
        except Exception:
            iterator_failed = True
    if iterator_failed:
        _fail(VerifiedMediaIngestCode.INVALID_ITERABLE)
    _reject_native_coroutine(iterator, VerifiedMediaIngestCode.INVALID_ITERABLE)

    next_method = _bind_special_method(
        iterator,
        "__next__",
        VerifiedMediaIngestCode.INVALID_ITERABLE,
        required=True,
    )
    if next_method is None:
        _fail(VerifiedMediaIngestCode.INVALID_ITERABLE)
    return next_method


def _consume_iterator(
    next_method: Callable[[], object],
    byte_limit: int,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    observed_size = 0
    pulls = 0

    while True:
        if pulls >= MAX_VERIFIED_MEDIA_INGEST_STEPS:
            _fail(VerifiedMediaIngestCode.STEP_LIMIT_EXCEEDED)
        failed = False
        ended = False
        chunk: object = None
        try:
            chunk = next_method()
        except StopIteration:
            ended = True
        except Exception:
            failed = True
        pulls += 1
        if failed:
            _fail(VerifiedMediaIngestCode.SOURCE_FAILED)
        if ended:
            break
        _reject_native_coroutine(chunk, VerifiedMediaIngestCode.INVALID_CHUNK)
        if type(chunk) is not bytes:
            _fail(VerifiedMediaIngestCode.INVALID_CHUNK)
        observed_size = _update_observation(
            digest,
            observed_size,
            chunk,
            byte_limit,
        )

    return observed_size, digest.hexdigest()


def _new_identifier_suffix() -> str:
    failed = False
    entropy: object = None
    try:
        entropy = secrets.token_bytes(_IDENTIFIER_RANDOM_BYTES)
    except Exception:
        failed = True
    if failed or type(entropy) is not bytes or len(entropy) != _IDENTIFIER_RANDOM_BYTES:
        _fail(VerifiedMediaIngestCode.ID_GENERATION_FAILED)
    suffix = base64.b32encode(entropy).decode("ascii").rstrip("=").lower()
    if len(suffix) != 26 or not suffix.isalnum() or not suffix.isascii():
        _fail(VerifiedMediaIngestCode.ID_GENERATION_FAILED)
    return suffix


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
    except Exception:
        failed = True
    if failed:
        _fail(VerifiedMediaIngestCode.ATTESTATION_FAILED)
    return encoded


def _create_attestation(
    *,
    domain: MediaDomain,
    operation_id: str,
    media_id: str,
    media_role: OriginalMediaRole,
    byte_size: int,
    sha256: str,
) -> bytes:
    subject: dict[str, object] = {
        "authority": AttestationAuthority.VERIFIED_INGEST.value,
        "byte_size": byte_size,
        "domain": domain.value,
        "media_id": media_id,
        "media_role": media_role.value,
        "operation_id": operation_id,
        "schema": ORIGINAL_MEDIA_ATTESTATION_SCHEMA,
        "sha256": sha256,
    }
    attestation_data = {
        **subject,
        "attestation_digest": hashlib.sha256(_canonical_json(subject)).hexdigest(),
    }

    failed = False
    validated = None
    try:
        validated = validate_original_media_attestation(attestation_data)
    except Exception:
        failed = True
    if failed or validated is None:
        _fail(VerifiedMediaIngestCode.ATTESTATION_FAILED)
    return validated.canonical_bytes


def ingest_verified_original_media(
    source: object,
    *,
    domain: MediaDomain,
    byte_limit: int,
) -> VerifiedOriginalMediaIngest:
    """Consume one original stream once and return verified metadata only.

    ``source`` must be either a synchronous reader exposing ``read(size)`` or
    an iterable whose yielded chunks are exact ``bytes``.  Direct bytes-like
    objects, caller metadata, identifiers, roles, digests, and attestations
    are not accepted.  A zero limit is valid and permits only empty media.
    """

    validated_domain, validated_limit = _validate_inputs(domain, byte_limit)
    read_method = _get_read_method(source)
    if read_method is not None:
        byte_size, sha256 = _consume_reader(read_method, validated_limit)
    else:
        byte_size, sha256 = _consume_iterator(
            _get_next_method(source),
            validated_limit,
        )

    operation_id = (
        f"mmop-{validated_domain.value}-{_new_identifier_suffix()}"
    )
    media_id = (
        f"mmmedia-{validated_domain.value}-{_new_identifier_suffix()}"
    )
    media_role = (
        OriginalMediaRole.ORIGINAL_AUDIO
        if validated_domain is MediaDomain.CALLS
        else OriginalMediaRole.ORIGINAL_VIDEO
    )
    attestation_bytes = _create_attestation(
        domain=validated_domain,
        operation_id=operation_id,
        media_id=media_id,
        media_role=media_role,
        byte_size=byte_size,
        sha256=sha256,
    )

    return VerifiedOriginalMediaIngest(
        domain=validated_domain,
        operation_id=operation_id,
        media_id=media_id,
        media_role=media_role,
        byte_size=byte_size,
        sha256=sha256,
        attestation_canonical_bytes=attestation_bytes,
    )


__all__ = (
    "MAX_VERIFIED_MEDIA_INGEST_STEPS",
    "VERIFIED_MEDIA_INGEST_CHUNK_BYTES",
    "VerifiedMediaIngestCode",
    "VerifiedMediaIngestError",
    "VerifiedOriginalMediaIngest",
    "ingest_verified_original_media",
)
