"""Bounded verification of an already-open MarketMatch STT input stream.

This module consumes only a borrowed reader's ``read(size)`` method.  It never
inspects or reopens a pathname, never takes ownership of the reader, and itself
performs no filesystem, decoder, model, network, persistence, or application
operation.  The caller-supplied reader controls how each read is fulfilled and
may perform I/O or block; timeout and cancellation remain integration concerns.

A successful result proves only that the exact observed byte sequence matched
the caller-supplied size and SHA-256 and did not exceed the explicit limit.  It
does not prove filesystem immutability, ownership, publication, durability,
media validity, STT success, SQL state, or workflow completion.

Immutable chunks are retained until one final join.  For multi-chunk inputs,
peak memory is therefore approximately twice the payload size plus the small
chunk-list and hashing overhead; only the final immutable bytes are retained.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import hmac
import inspect
import re
from typing import Callable, NoReturn


MAX_STT_INPUT_BYTES = 104_857_600
STT_INPUT_CHUNK_BYTES = 65_536
MAX_STT_INPUT_READ_CALLS = 4_096

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class STTInputCode(str, Enum):
    """Fixed privacy-preserving snapshot result codes."""

    INVALID_READER = "INVALID_READER"
    INVALID_EXPECTED_SIZE = "INVALID_EXPECTED_SIZE"
    INVALID_EXPECTED_SHA256 = "INVALID_EXPECTED_SHA256"
    INVALID_BYTE_LIMIT = "INVALID_BYTE_LIMIT"
    INPUT_LIMIT_EXCEEDED = "INPUT_LIMIT_EXCEEDED"
    INPUT_SIZE_MISMATCH = "INPUT_SIZE_MISMATCH"
    INPUT_DIGEST_MISMATCH = "INPUT_DIGEST_MISMATCH"
    INPUT_READ_LIMIT_EXCEEDED = "INPUT_READ_LIMIT_EXCEEDED"
    INVALID_READ_RESULT = "INVALID_READ_RESULT"
    READER_FAILED = "READER_FAILED"


class STTInputError(Exception):
    """A fixed-code failure that never embeds rejected input."""

    def __init__(self, code: STTInputCode):
        if type(code) is not STTInputCode:
            code = STTInputCode.INVALID_READER
        self.code = code
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r})"


@dataclass(frozen=True, slots=True)
class VerifiedSTTInputSnapshot:
    """Immutable verified bytes detached from the borrowed reader."""

    byte_size: int = field(repr=False)
    sha256: str = field(repr=False)
    payload_bytes: bytes = field(repr=False, hash=False)


def _fail(code: STTInputCode) -> NoReturn:
    raise STTInputError(code) from None


def _validate_metadata(
    expected_byte_size: object,
    expected_sha256: object,
    byte_limit: object,
) -> tuple[int, str, int]:
    if type(byte_limit) is not int or not 0 < byte_limit <= MAX_STT_INPUT_BYTES:
        _fail(STTInputCode.INVALID_BYTE_LIMIT)
    if (
        type(expected_byte_size) is not int
        or expected_byte_size < 0
        or expected_byte_size > byte_limit
        or expected_byte_size > MAX_STT_INPUT_BYTES
    ):
        _fail(STTInputCode.INVALID_EXPECTED_SIZE)
    if type(expected_sha256) is not str or _SHA256_RE.fullmatch(expected_sha256) is None:
        _fail(STTInputCode.INVALID_EXPECTED_SHA256)
    return expected_byte_size, expected_sha256, byte_limit


def _borrowed_read_method(reader: object) -> Callable[[int], object]:
    if reader is None or callable(reader):
        _fail(STTInputCode.INVALID_READER)

    failed = False
    read_method: object = None
    try:
        read_method = getattr(reader, "read")
    except BaseException:
        failed = True
    if failed:
        _fail(STTInputCode.INVALID_READER)
    if not callable(read_method):
        _fail(STTInputCode.INVALID_READER)

    async_failed = False
    is_async = False
    try:
        is_async = inspect.iscoroutinefunction(read_method)
        if not is_async:
            is_async = inspect.iscoroutinefunction(read_method.__call__)
    except BaseException:
        async_failed = True
    if async_failed or is_async:
        _fail(STTInputCode.INVALID_READER)
    return read_method


def _read_exact_bytes(
    read_method: Callable[[int], object],
    request_size: int,
) -> bytes:
    failed = False
    result: object = None
    try:
        result = read_method(request_size)
    except BaseException:
        failed = True
    if failed:
        _fail(STTInputCode.READER_FAILED)
    if type(result) is not bytes:
        _fail(STTInputCode.INVALID_READ_RESULT)
    if len(result) > request_size:
        _fail(STTInputCode.INVALID_READ_RESULT)
    return result


def snapshot_verified_stt_input(
    reader: object,
    *,
    expected_byte_size: int,
    expected_sha256: str,
    byte_limit: int,
) -> VerifiedSTTInputSnapshot:
    """Return immutable verified bytes read from one borrowed binary reader.

    Metadata is validated before the reader is inspected.  Every read request
    has an explicit bounded size, and at most ``byte_limit + 1`` returned bytes
    can be accepted from a conforming reader.  Short reads are not EOF; only
    exact ``b""`` terminates the stream.  A fixed call-count ceiling prevents
    pathological tiny reads from causing unbounded CPU and chunk-list growth.
    The reader is neither closed nor retained.  Blocking behavior remains a
    property of the caller-supplied reader and is outside this pure primitive.
    """

    expected_size, expected_digest, limit = _validate_metadata(
        expected_byte_size,
        expected_sha256,
        byte_limit,
    )
    read_method = _borrowed_read_method(reader)

    chunks: list[bytes] = []
    observed_size = 0
    read_calls = 0
    digest = hashlib.sha256()

    while True:
        if read_calls >= MAX_STT_INPUT_READ_CALLS:
            _fail(STTInputCode.INPUT_READ_LIMIT_EXCEEDED)
        remaining_probe = limit + 1 - observed_size
        request_size = min(STT_INPUT_CHUNK_BYTES, remaining_probe)
        chunk = _read_exact_bytes(read_method, request_size)
        read_calls += 1
        if chunk == b"":
            break

        observed_size += len(chunk)
        if observed_size > limit:
            _fail(STTInputCode.INPUT_LIMIT_EXCEEDED)
        if observed_size > expected_size:
            _fail(STTInputCode.INPUT_SIZE_MISMATCH)

        digest.update(chunk)
        chunks.append(chunk)

    if observed_size != expected_size:
        _fail(STTInputCode.INPUT_SIZE_MISMATCH)

    computed_digest = digest.hexdigest()
    if not hmac.compare_digest(computed_digest, expected_digest):
        _fail(STTInputCode.INPUT_DIGEST_MISMATCH)

    if not chunks:
        payload = b""
    elif len(chunks) == 1:
        payload = chunks[0]
    else:
        payload = b"".join(chunks)

    return VerifiedSTTInputSnapshot(
        byte_size=observed_size,
        sha256=computed_digest,
        payload_bytes=payload,
    )


__all__ = (
    "MAX_STT_INPUT_BYTES",
    "MAX_STT_INPUT_READ_CALLS",
    "STT_INPUT_CHUNK_BYTES",
    "STTInputCode",
    "STTInputError",
    "VerifiedSTTInputSnapshot",
    "snapshot_verified_stt_input",
)
