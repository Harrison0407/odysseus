"""Bounded observation of one borrowed original-media byte stream.

This module consumes only a synchronous borrowed reader's ``read(size)``
method.  It counts and hashes the exact built-in ``bytes`` returned before a
clean ``b""`` EOF, without retaining the source or payload.  The caller owns
the reader and remains responsible for authorization, source selection,
transport completion, blocking behavior, cancellation, and lifecycle.

A successful observation proves only that the observed byte sequence stayed
within the supplied limit and that its returned size and SHA-256 describe that
same sequence.  It does not prove original-upload authenticity, ownership,
domain, operation or media identity, persistence, publication, durability,
reachability, attestation authority, STT correctness, or workflow completion.

Memory retained by the observer is limited to the current returned chunk,
SHA-256 state, and fixed scalar state.  A hostile reader can allocate memory
internally before returning; this primitive cannot prevent that behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import inspect
from typing import Callable, NoReturn


MAX_ORIGINAL_MEDIA_BYTES = 104_857_600
READ_CHUNK_BYTES = 65_536
MAX_READ_CALLS = 4_096


class OriginalMediaObservationCode(str, Enum):
    """Fixed privacy-preserving observation failure codes."""

    INVALID_READER = "INVALID_READER"
    INVALID_BYTE_LIMIT = "INVALID_BYTE_LIMIT"
    INVALID_READ_RESULT = "INVALID_READ_RESULT"
    SOURCE_FAILED = "SOURCE_FAILED"
    INPUT_LIMIT_EXCEEDED = "INPUT_LIMIT_EXCEEDED"
    READ_CALL_LIMIT_EXCEEDED = "READ_CALL_LIMIT_EXCEEDED"


class OriginalMediaObservationError(Exception):
    """A fixed-code failure that never embeds rejected input."""

    def __init__(self, code: OriginalMediaObservationCode):
        if type(code) is not OriginalMediaObservationCode:
            code = OriginalMediaObservationCode.INVALID_READER
        self.code = code
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r})"


@dataclass(frozen=True, slots=True)
class OriginalMediaObservation:
    """Immutable metadata for exactly one successfully observed sequence."""

    byte_size: int = field(repr=False)
    sha256: str = field(repr=False)


def _fail(code: OriginalMediaObservationCode) -> NoReturn:
    raise OriginalMediaObservationError(code) from None


def _validate_byte_limit(byte_limit: object) -> int:
    if (
        type(byte_limit) is not int
        or byte_limit < 0
        or byte_limit > MAX_ORIGINAL_MEDIA_BYTES
    ):
        _fail(OriginalMediaObservationCode.INVALID_BYTE_LIMIT)
    return byte_limit


def _is_async_callable(value: object) -> bool:
    failed = False
    result = False
    try:
        result = inspect.iscoroutinefunction(value)
        if not result:
            result = inspect.iscoroutinefunction(type(value).__call__)
    except Exception:
        failed = True
    if failed:
        _fail(OriginalMediaObservationCode.INVALID_READER)
    return result


def _borrowed_read_method(source: object) -> Callable[[int], object]:
    if (
        source is None
        or isinstance(source, (str, bytes, bytearray, memoryview, int))
        or callable(source)
    ):
        _fail(OriginalMediaObservationCode.INVALID_READER)

    inspection_failed = False
    is_awaitable = False
    is_path_protocol = False
    try:
        is_awaitable = inspect.isawaitable(source)
        is_path_protocol = getattr(type(source), "__fspath__", None) is not None
    except Exception:
        inspection_failed = True
    if inspection_failed or is_awaitable or is_path_protocol:
        _fail(OriginalMediaObservationCode.INVALID_READER)

    lookup_failed = False
    read_method: object = None
    try:
        read_method = getattr(source, "read")
    except Exception:
        lookup_failed = True
    if lookup_failed or not callable(read_method):
        _fail(OriginalMediaObservationCode.INVALID_READER)
    if _is_async_callable(read_method):
        _fail(OriginalMediaObservationCode.INVALID_READER)
    return read_method


def _close_unexecuted_native_coroutine(value: object) -> None:
    if not inspect.iscoroutine(value):
        return
    close_failed = False
    try:
        value.close()
    except Exception:
        close_failed = True
    if close_failed:
        _fail(OriginalMediaObservationCode.INVALID_READ_RESULT)


def _read_exact_bytes(
    read_method: Callable[[int], object],
    request_size: int,
) -> bytes:
    read_failed = False
    result: object = None
    try:
        result = read_method(request_size)
    except Exception:
        read_failed = True
    if read_failed:
        _fail(OriginalMediaObservationCode.SOURCE_FAILED)
    if type(result) is not bytes:
        _close_unexecuted_native_coroutine(result)
        _fail(OriginalMediaObservationCode.INVALID_READ_RESULT)
    if len(result) > request_size:
        _fail(OriginalMediaObservationCode.INVALID_READ_RESULT)
    return result


def _observe_original_media_worker(
    source: object,
    *,
    byte_limit: int,
) -> OriginalMediaObservation:
    limit = _validate_byte_limit(byte_limit)
    read_method = _borrowed_read_method(source)

    observed_size = 0
    read_calls = 0
    digest = hashlib.sha256()

    while True:
        if read_calls >= MAX_READ_CALLS:
            _fail(OriginalMediaObservationCode.READ_CALL_LIMIT_EXCEEDED)

        remaining_probe = limit + 1 - observed_size
        request_size = min(READ_CHUNK_BYTES, remaining_probe)
        chunk = _read_exact_bytes(read_method, request_size)
        read_calls += 1

        if chunk == b"":
            break

        observed_size += len(chunk)
        if observed_size > limit:
            _fail(OriginalMediaObservationCode.INPUT_LIMIT_EXCEEDED)
        digest.update(chunk)

    return OriginalMediaObservation(
        byte_size=observed_size,
        sha256=digest.hexdigest(),
    )


def observe_original_media(
    source: object,
    *,
    byte_limit: int,
) -> OriginalMediaObservation:
    """Count and hash one bounded sequence from a borrowed sync reader.

    ``byte_limit`` is validated before source protocol validation.  Each read
    is bounded and positive, short nonempty reads are data, and only exact
    built-in ``b""`` ends the sequence.  The final EOF probe counts against the
    fixed read-call ceiling.  The source is advanced naturally but is never
    closed, retained, or rewound.  No source instance attribute other than
    ``read`` is accessed; callable, awaitable, and path-protocol checks inspect
    only the object or its type.

    Expected worker failures are reduced to their fixed code before a fresh
    public error is raised.  Consequently, the public error traceback does not
    retain the borrowed source, bound read method, or rejected result object.
    """

    failure_code: OriginalMediaObservationCode | None = None
    try:
        return _observe_original_media_worker(source, byte_limit=byte_limit)
    except OriginalMediaObservationError as error:
        failure_code = error.code

    del source
    del byte_limit
    if failure_code is None:  # pragma: no cover - defensive invariant
        failure_code = OriginalMediaObservationCode.INVALID_READER
    _fail(failure_code)


__all__ = (
    "MAX_ORIGINAL_MEDIA_BYTES",
    "MAX_READ_CALLS",
    "READ_CHUNK_BYTES",
    "OriginalMediaObservation",
    "OriginalMediaObservationCode",
    "OriginalMediaObservationError",
    "observe_original_media",
)
