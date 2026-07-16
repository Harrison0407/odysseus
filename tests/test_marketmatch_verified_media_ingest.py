"""Dedicated synthetic tests for verified original-media ingest."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
import gc
import hashlib
import inspect
import io
from pathlib import Path
import re
import subprocess
import traceback
import unittest
from unittest import mock
import warnings

import src.marketmatch_verified_media_ingest as ingest_module
from src.marketmatch_media_attestation import (
    AttestationAuthority,
    MediaDomain,
    OriginalMediaRole,
    bind_attestation_for_phase3p,
    validate_original_media_attestation,
)
from src.marketmatch_verified_media_ingest import (
    MAX_VERIFIED_MEDIA_INGEST_STEPS,
    VERIFIED_MEDIA_INGEST_CHUNK_BYTES,
    VerifiedMediaIngestCode,
    VerifiedMediaIngestError,
    VerifiedOriginalMediaIngest,
    ingest_verified_original_media,
)


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_PATH = ROOT / "src" / "marketmatch_verified_media_ingest.py"
MAX_MEDIA_BYTES = 104_857_600
OPERATION_RE = re.compile(r"mmop-(calls|videos)-[a-z0-9]{26}\Z", re.ASCII)
MEDIA_RE = re.compile(r"mmmedia-(calls|videos)-[a-z0-9]{26}\Z", re.ASCII)


class SyntheticReader:
    def __init__(self, content: bytes, *, max_read: int | None = None):
        self.content = content
        self.max_read = max_read
        self.position = 0
        self.requests: list[int] = []
        self.close_calls = 0

    def read(self, size: int) -> bytes:
        self.requests.append(size)
        if self.position >= len(self.content):
            return b""
        take = size if self.max_read is None else min(size, self.max_read)
        end = min(len(self.content), self.position + take)
        chunk = self.content[self.position:end]
        self.position = end
        return chunk

    def close(self) -> None:
        self.close_calls += 1


class GuardedMetadataReader(SyntheticReader):
    @property
    def name(self):
        raise AssertionError("NAME_METADATA_CANARY")

    @property
    def filename(self):
        raise AssertionError("FILENAME_METADATA_CANARY")

    @property
    def mime_type(self):
        raise AssertionError("MIME_METADATA_CANARY")

    @property
    def content_length(self):
        raise AssertionError("CONTENT_LENGTH_METADATA_CANARY")

    @property
    def headers(self):
        raise AssertionError("HEADERS_METADATA_CANARY")

    @property
    def metadata(self):
        raise AssertionError("METADATA_CANARY")

    def seek(self, *args):
        raise AssertionError("SEEK_CANARY")

    def tell(self):
        raise AssertionError("TELL_CANARY")

    def close(self):
        raise AssertionError("CLOSE_CANARY")


class FixedReadResult:
    def __init__(self, value: object):
        self.value = value
        self.requests: list[int] = []

    def read(self, size: int) -> object:
        self.requests.append(size)
        return self.value


class RaisingReader:
    def __init__(self, error: BaseException):
        self.error = error
        self.calls = 0
        self.close_calls = 0

    def read(self, size: int) -> bytes:
        del size
        self.calls += 1
        raise self.error

    def close(self) -> None:
        self.close_calls += 1


class OneShotIterable:
    def __init__(self, chunks: tuple[bytes, ...]):
        self.chunks = chunks
        self.iter_calls = 0
        self.next_calls = 0

    def __iter__(self):
        self.iter_calls += 1
        if self.iter_calls != 1:
            raise AssertionError("ITERATED_TWICE_CANARY")
        return self

    def __next__(self) -> bytes:
        self.next_calls += 1
        index = self.next_calls - 1
        if index >= len(self.chunks):
            raise StopIteration
        return self.chunks[index]


class RepeatingMaxReader:
    def __init__(self, total_size: int, chunk: bytes):
        self.remaining = total_size
        self.chunk = chunk
        self.requests: list[int] = []
        self.max_live_chunk = 0

    def read(self, size: int) -> bytes:
        self.requests.append(size)
        if self.remaining == 0:
            return b""
        take = min(size, self.remaining, len(self.chunk))
        self.remaining -= take
        self.max_live_chunk = max(self.max_live_chunk, take)
        if take == len(self.chunk):
            return self.chunk
        return self.chunk[:take]


def _ingest(
    source: object,
    *,
    domain: MediaDomain = MediaDomain.CALLS,
    limit: int = 1_000_000,
) -> VerifiedOriginalMediaIngest:
    return ingest_verified_original_media(
        source,
        domain=domain,
        byte_limit=limit,
    )


def _assert_code(
    test: unittest.TestCase,
    code: VerifiedMediaIngestCode,
    source: object,
    *,
    domain: object = MediaDomain.CALLS,
    limit: object = 10,
) -> VerifiedMediaIngestError:
    with test.assertRaises(VerifiedMediaIngestError) as caught:
        ingest_verified_original_media(
            source,
            domain=domain,  # type: ignore[arg-type]
            byte_limit=limit,  # type: ignore[arg-type]
        )
    test.assertIs(caught.exception.code, code)
    return caught.exception


def _assert_quiet_code(
    test: unittest.TestCase,
    code: VerifiedMediaIngestCode,
    source: object,
    *,
    domain: object = MediaDomain.CALLS,
    limit: object = 10,
) -> VerifiedMediaIngestError:
    stderr = io.StringIO()
    with warnings.catch_warnings(record=True) as caught, mock.patch(
        "sys.stderr",
        stderr,
    ):
        warnings.simplefilter("always")
        error = _assert_code(
            test,
            code,
            source,
            domain=domain,
            limit=limit,
        )
        gc.collect()
    test.assertEqual(caught, [])
    test.assertEqual(stderr.getvalue(), "")
    return error


class VerifiedMediaIngestSuccessTests(unittest.TestCase):
    def test_empty_reader_is_valid(self) -> None:
        result = _ingest(SyntheticReader(b""), limit=0)
        self.assertEqual(result.byte_size, 0)
        self.assertEqual(result.sha256, hashlib.sha256(b"").hexdigest())

    def test_empty_iterable_is_valid(self) -> None:
        result = _ingest([], limit=0)
        self.assertEqual(result.byte_size, 0)

    def test_empty_chunks_are_valid_until_iterable_end(self) -> None:
        result = _ingest([b"", b""], limit=0)
        self.assertEqual(result.byte_size, 0)

    def test_one_byte_reader(self) -> None:
        result = _ingest(SyntheticReader(b"x"), limit=1)
        self.assertEqual(result.byte_size, 1)
        self.assertEqual(result.sha256, hashlib.sha256(b"x").hexdigest())

    def test_one_byte_iterable(self) -> None:
        result = _ingest([b"x"], limit=1)
        self.assertEqual(result.byte_size, 1)

    def test_normal_multichunk_iterable(self) -> None:
        chunks = (b"synthetic ", b"original ", b"media")
        result = _ingest(chunks, limit=sum(map(len, chunks)))
        expected = b"".join(chunks)
        self.assertEqual(result.byte_size, len(expected))
        self.assertEqual(result.sha256, hashlib.sha256(expected).hexdigest())

    def test_normal_multichunk_reader(self) -> None:
        content = b"reader boundary content"
        reader = SyntheticReader(content, max_read=3)
        result = _ingest(reader, limit=len(content))
        self.assertEqual(result.sha256, hashlib.sha256(content).hexdigest())
        self.assertGreater(len(reader.requests), 3)

    def test_exact_limit_reader(self) -> None:
        content = b"exact"
        result = _ingest(SyntheticReader(content), limit=len(content))
        self.assertEqual(result.byte_size, len(content))

    def test_exact_limit_iterable(self) -> None:
        content = b"exact"
        result = _ingest([content], limit=len(content))
        self.assertEqual(result.byte_size, len(content))

    def test_chunk_boundaries_produce_identical_size_and_digest(self) -> None:
        content = b"abcdefghijklmno"
        results = (
            _ingest(SyntheticReader(content), limit=len(content)),
            _ingest(SyntheticReader(content, max_read=1), limit=len(content)),
            _ingest([content[:2], content[2:8], content[8:]], limit=len(content)),
        )
        self.assertEqual({result.byte_size for result in results}, {len(content)})
        self.assertEqual({result.sha256 for result in results}, {hashlib.sha256(content).hexdigest()})

    def test_iterable_is_consumed_once_without_rewind(self) -> None:
        source = OneShotIterable((b"one", b"pass"))
        result = _ingest(source, limit=7)
        self.assertEqual(result.byte_size, 7)
        self.assertEqual(source.iter_calls, 1)
        self.assertEqual(source.next_calls, 3)

    def test_reader_is_not_rewound_closed_or_inspected(self) -> None:
        content = b"guarded"
        result = _ingest(GuardedMetadataReader(content), limit=len(content))
        self.assertEqual(result.byte_size, len(content))

    def test_reader_natural_position_advances_once(self) -> None:
        content = b"one pass position"
        reader = SyntheticReader(content, max_read=2)
        _ingest(reader, limit=len(content))
        self.assertEqual(reader.position, len(content))
        self.assertEqual(reader.close_calls, 0)

    def test_ids_are_fresh_and_format_valid(self) -> None:
        entropy = [bytes([index]) * 16 for index in range(1, 5)]
        with mock.patch.object(ingest_module.secrets, "token_bytes", side_effect=entropy) as token_bytes:
            first = _ingest([], limit=0)
            second = _ingest([], limit=0)
        self.assertEqual(token_bytes.call_count, 4)
        self.assertNotEqual(first.operation_id, second.operation_id)
        self.assertNotEqual(first.media_id, second.media_id)
        self.assertIsNotNone(OPERATION_RE.fullmatch(first.operation_id))
        self.assertIsNotNone(MEDIA_RE.fullmatch(first.media_id))

    def test_calls_role_binding(self) -> None:
        result = _ingest([], domain=MediaDomain.CALLS, limit=0)
        self.assertIs(result.domain, MediaDomain.CALLS)
        self.assertIs(result.media_role, OriginalMediaRole.ORIGINAL_AUDIO)
        self.assertTrue(result.operation_id.startswith("mmop-calls-"))
        self.assertTrue(result.media_id.startswith("mmmedia-calls-"))

    def test_videos_role_binding(self) -> None:
        result = _ingest([], domain=MediaDomain.VIDEOS, limit=0)
        self.assertIs(result.domain, MediaDomain.VIDEOS)
        self.assertIs(result.media_role, OriginalMediaRole.ORIGINAL_VIDEO)
        self.assertTrue(result.operation_id.startswith("mmop-videos-"))
        self.assertTrue(result.media_id.startswith("mmmedia-videos-"))

    def test_phase3q_attestation_is_canonical_and_exact(self) -> None:
        content = b"attested bytes"
        result = _ingest([content], limit=len(content))
        attestation = validate_original_media_attestation(result.attestation_canonical_bytes)
        self.assertIs(attestation.domain, result.domain)
        self.assertEqual(attestation.operation_id, result.operation_id)
        self.assertEqual(attestation.media_id, result.media_id)
        self.assertIs(attestation.media_role, result.media_role)
        self.assertEqual(attestation.byte_size, result.byte_size)
        self.assertEqual(attestation.sha256, result.sha256)
        self.assertIs(attestation.authority, AttestationAuthority.VERIFIED_INGEST)
        self.assertEqual(attestation.canonical_bytes, result.attestation_canonical_bytes)

    def test_attestation_binds_for_phase3p(self) -> None:
        content = b"future phase 3p"
        result = _ingest([content], limit=len(content))
        attestation = validate_original_media_attestation(result.attestation_canonical_bytes)
        metadata = bind_attestation_for_phase3p(
            attestation,
            expected_domain=result.domain,
            expected_operation_id=result.operation_id,
            byte_limit=len(content),
        )
        self.assertEqual(metadata.expected_byte_size, len(content))
        self.assertEqual(metadata.expected_sha256, result.sha256)

    def test_result_is_frozen_slotted_and_exact(self) -> None:
        result = _ingest([], limit=0)
        with self.assertRaises(FrozenInstanceError):
            result.byte_size = 1  # type: ignore[misc]
        self.assertFalse(hasattr(result, "__dict__"))
        self.assertEqual(
            tuple(item.name for item in fields(VerifiedOriginalMediaIngest)),
            (
                "domain", "operation_id", "media_id", "media_role",
                "byte_size", "sha256", "attestation_canonical_bytes",
            ),
        )

    def test_result_contains_no_media_bytes_or_mutable_containers(self) -> None:
        content = b"RAW_MEDIA_CONTENT_PRIVATE_CANARY"
        result = _ingest([content], limit=len(content))
        self.assertNotIn(content, result.attestation_canonical_bytes)
        self.assertNotIn("RAW_MEDIA_CONTENT_PRIVATE_CANARY", repr(result))
        self.assertFalse(any(isinstance(getattr(result, item.name), (dict, list, bytearray, memoryview)) for item in fields(result)))

    def test_synthetic_100_mib_stream_uses_reusable_chunk(self) -> None:
        chunk = b"z" * VERIFIED_MEDIA_INGEST_CHUNK_BYTES
        reader = RepeatingMaxReader(MAX_MEDIA_BYTES, chunk)
        result = _ingest(reader, limit=MAX_MEDIA_BYTES)
        expected = hashlib.sha256()
        for _ in range(MAX_MEDIA_BYTES // len(chunk)):
            expected.update(chunk)
        self.assertEqual(result.byte_size, MAX_MEDIA_BYTES)
        self.assertEqual(result.sha256, expected.hexdigest())
        self.assertEqual(reader.max_live_chunk, VERIFIED_MEDIA_INGEST_CHUNK_BYTES)
        self.assertEqual(len(reader.requests), MAX_MEDIA_BYTES // VERIFIED_MEDIA_INGEST_CHUNK_BYTES + 1)


class VerifiedMediaIngestLimitTests(unittest.TestCase):
    def test_limit_zero_accepts_empty(self) -> None:
        self.assertEqual(_ingest([], limit=0).byte_size, 0)

    def test_limit_zero_rejects_nonempty_reader(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INPUT_LIMIT_EXCEEDED, SyntheticReader(b"x"), limit=0)

    def test_limit_zero_rejects_nonempty_iterable(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INPUT_LIMIT_EXCEEDED, [b"x"], limit=0)

    def test_normal_limit_accepted(self) -> None:
        self.assertEqual(_ingest([b"abc"], limit=10).byte_size, 3)

    def test_100_mib_limit_accepted_for_empty(self) -> None:
        self.assertEqual(_ingest([], limit=MAX_MEDIA_BYTES).byte_size, 0)

    def test_negative_limit_rejected_before_source_inspection(self) -> None:
        class HostileSource:
            def __getattribute__(self, name):
                raise AssertionError("LIMIT_ORDER_CANARY")

        _assert_code(self, VerifiedMediaIngestCode.INVALID_BYTE_LIMIT, HostileSource(), limit=-1)

    def test_boolean_limit_rejected(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_BYTE_LIMIT, [], limit=True)

    def test_float_limit_rejected(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_BYTE_LIMIT, [], limit=1.0)

    def test_limit_above_100_mib_rejected(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_BYTE_LIMIT, [], limit=MAX_MEDIA_BYTES + 1)

    def test_limit_plus_one_reader_rejected_after_one_extra_observed_byte(self) -> None:
        reader = SyntheticReader(b"abcd")
        _assert_code(self, VerifiedMediaIngestCode.INPUT_LIMIT_EXCEEDED, reader, limit=3)
        self.assertEqual(reader.position, 4)
        self.assertEqual(reader.requests, [4])

    def test_limit_plus_one_iterable_rejected_immediately(self) -> None:
        class GuardedOverflowIterable:
            def __init__(self):
                self.calls = 0

            def __iter__(self):
                return self

            def __next__(self):
                self.calls += 1
                if self.calls == 1:
                    return b"abcd"
                raise AssertionError("ITERATED_AFTER_OVERFLOW_CANARY")

        source = GuardedOverflowIterable()
        _assert_code(self, VerifiedMediaIngestCode.INPUT_LIMIT_EXCEEDED, source, limit=3)
        self.assertEqual(source.calls, 1)

    def test_reader_step_limit_prevents_tiny_read_amplification(self) -> None:
        class OneByteForeverReader:
            def __init__(self):
                self.calls = 0

            def read(self, size: int) -> bytes:
                self.calls += 1
                self.asserted_sizes.append(size)
                return b"x"

            asserted_sizes: list[int] = []

        reader = OneByteForeverReader()
        _assert_code(self, VerifiedMediaIngestCode.STEP_LIMIT_EXCEEDED, reader, limit=MAX_MEDIA_BYTES)
        self.assertEqual(reader.calls, MAX_VERIFIED_MEDIA_INGEST_STEPS)
        self.assertEqual(len(reader.asserted_sizes), MAX_VERIFIED_MEDIA_INGEST_STEPS)

    def test_iterable_step_limit_prevents_tiny_chunk_amplification(self) -> None:
        class OneByteForeverIterable:
            def __init__(self):
                self.calls = 0

            def __iter__(self):
                return self

            def __next__(self) -> bytes:
                self.calls += 1
                return b"x"

        source = OneByteForeverIterable()
        _assert_code(self, VerifiedMediaIngestCode.STEP_LIMIT_EXCEEDED, source, limit=MAX_MEDIA_BYTES)
        self.assertEqual(source.calls, MAX_VERIFIED_MEDIA_INGEST_STEPS)


class VerifiedMediaIngestSourceValidationTests(unittest.TestCase):
    def test_invalid_domain_string_rejected(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_DOMAIN, [], domain="calls", limit=0)

    def test_wrong_domain_enum_rejected(self) -> None:
        class OtherDomain(str):
            pass

        _assert_code(self, VerifiedMediaIngestCode.INVALID_DOMAIN, [], domain=OtherDomain("calls"), limit=0)

    def test_direct_bytes_source_rejected(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_SOURCE, b"media")

    def test_direct_bytes_subclass_rejected_without_protocol_invocation(self) -> None:
        class HostileBytes(bytes):
            iterated = False

            def __iter__(self):
                type(self).iterated = True
                raise AssertionError("BYTES_SUBCLASS_ITER_CANARY")

        source = HostileBytes(b"media")
        _assert_code(self, VerifiedMediaIngestCode.INVALID_SOURCE, source)
        self.assertFalse(HostileBytes.iterated)

    def test_string_source_rejected(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_SOURCE, "media")

    def test_bytearray_source_rejected(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_SOURCE, bytearray(b"media"))

    def test_direct_bytearray_subclass_rejected_without_protocol_invocation(self) -> None:
        class HostileBytearray(bytearray):
            iterated = False

            def __iter__(self):
                type(self).iterated = True
                raise AssertionError("BYTEARRAY_SUBCLASS_ITER_CANARY")

        source = HostileBytearray(b"media")
        _assert_code(self, VerifiedMediaIngestCode.INVALID_SOURCE, source)
        self.assertFalse(HostileBytearray.iterated)

    def test_hostile_class_property_does_not_leak(self) -> None:
        class HostileClassProperty:
            @property
            def __class__(self):
                raise ValueError("CLASS_PRIVATE_CANARY")

        error = _assert_code(
            self,
            VerifiedMediaIngestCode.INVALID_ITERABLE,
            HostileClassProperty(),
        )
        rendered = str(error) + repr(error)
        self.assertNotIn("CLASS_PRIVATE_CANARY", rendered)
        self.assertIsNone(error.__context__)

    def test_memoryview_source_rejected(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_SOURCE, memoryview(b"media"))

    def test_none_source_rejected(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_SOURCE, None)

    def test_integer_source_rejected(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_SOURCE, 1)

    def test_callback_source_rejected_without_invocation(self) -> None:
        called = False

        def callback():
            nonlocal called
            called = True

        _assert_code(self, VerifiedMediaIngestCode.INVALID_SOURCE, callback)
        self.assertFalse(called)

    def test_mixed_iterable_rejected_at_first_nonbytes_chunk(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_CHUNK, [b"valid", "invalid"], limit=10)

    def test_str_chunk_rejected(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_CHUNK, ["x"])

    def test_bytearray_chunk_rejected(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_CHUNK, [bytearray(b"x")])

    def test_memoryview_chunk_rejected(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_CHUNK, [memoryview(b"x")])

    def test_none_chunk_rejected(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_CHUNK, [None])

    def test_integer_chunk_rejected(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_CHUNK, [1])

    def test_bytes_subclass_chunk_rejected_without_coercion(self) -> None:
        class HostileBytes(bytes):
            def __bytes__(self):
                raise AssertionError("BYTES_COERCION_CANARY")

            def __repr__(self):
                raise AssertionError("BYTES_REPR_CANARY")

        error = _assert_code(self, VerifiedMediaIngestCode.INVALID_CHUNK, [HostileBytes(b"x")])
        self.assertNotIn("CANARY", str(error) + repr(error))

    def test_malformed_read_returns_str(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_READ_RESULT, FixedReadResult("x"))

    def test_malformed_read_returns_bytearray(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_READ_RESULT, FixedReadResult(bytearray(b"x")))

    def test_malformed_read_returns_memoryview(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_READ_RESULT, FixedReadResult(memoryview(b"x")))

    def test_malformed_read_returns_none(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_READ_RESULT, FixedReadResult(None))

    def test_malformed_read_returns_integer(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_READ_RESULT, FixedReadResult(1))

    def test_malformed_read_returns_bytes_subclass(self) -> None:
        class HostileBytes(bytes):
            pass

        _assert_code(self, VerifiedMediaIngestCode.INVALID_READ_RESULT, FixedReadResult(HostileBytes(b"x")))

    def test_malformed_reader_overreturns_requested_size(self) -> None:
        class OverReturningReader:
            def read(self, size: int) -> bytes:
                return b"x" * (size + 1)

        _assert_code(self, VerifiedMediaIngestCode.INVALID_READ_RESULT, OverReturningReader(), limit=1)

    def test_noncallable_read_member_rejected(self) -> None:
        class InvalidReader:
            read = b"not callable"

        _assert_code(self, VerifiedMediaIngestCode.INVALID_STREAM, InvalidReader())

    def test_async_reader_rejected_without_invocation(self) -> None:
        class AsyncReader:
            def __init__(self):
                self.called = False

            async def read(self, size: int) -> bytes:
                del size
                self.called = True
                return b""

        source = AsyncReader()
        _assert_code(self, VerifiedMediaIngestCode.INVALID_STREAM, source, limit=0)
        self.assertFalse(source.called)

    def test_coroutine_read_property_is_closed_without_warning(self) -> None:
        events: list[str] = []

        async def returned_read():
            events.append("COROUTINE_READ_PROPERTY_BODY_CANARY")

        class Reader:
            @property
            def read(self):
                return returned_read()

        _assert_quiet_code(
            self,
            VerifiedMediaIngestCode.INVALID_STREAM,
            Reader(),
            limit=0,
        )
        self.assertEqual(events, [])

    def test_sync_read_returning_coroutine_is_closed_without_warning(self) -> None:
        events: list[str] = []

        async def returned_chunk():
            events.append("COROUTINE_READ_RESULT_BODY_CANARY")
            return b""

        class Reader:
            def read(self, size: int):
                del size
                return returned_chunk()

        _assert_quiet_code(
            self,
            VerifiedMediaIngestCode.INVALID_READ_RESULT,
            Reader(),
            limit=0,
        )
        self.assertEqual(events, [])

    def test_async_callable_read_rejected_without_warning(self) -> None:
        class AsyncRead:
            def __init__(self):
                self.called = False

            async def __call__(self, size: int) -> bytes:
                del size
                self.called = True
                return b""

        class Reader:
            def __init__(self):
                self.read = AsyncRead()

        source = Reader()
        stderr = io.StringIO()
        with warnings.catch_warnings(record=True) as caught, mock.patch("sys.stderr", stderr):
            warnings.simplefilter("always")
            _assert_code(self, VerifiedMediaIngestCode.INVALID_STREAM, source, limit=0)
        self.assertFalse(source.read.called)
        self.assertEqual(caught, [])
        self.assertEqual(stderr.getvalue(), "")

    def test_coroutine_call_property_is_closed_without_warning(self) -> None:
        events: list[str] = []

        async def returned_call():
            events.append("COROUTINE_CALL_PROPERTY_BODY_CANARY")

        class CoroutineCallProperty:
            @property
            def __call__(self):
                return returned_call()

        class Reader:
            def __init__(self):
                self.read = CoroutineCallProperty()

        _assert_quiet_code(
            self,
            VerifiedMediaIngestCode.INVALID_STREAM,
            Reader(),
            limit=0,
        )
        self.assertEqual(events, [])

    def test_noniterable_source_rejected(self) -> None:
        _assert_code(self, VerifiedMediaIngestCode.INVALID_ITERABLE, object())

    def test_instance_fake_iteration_protocol_is_not_invoked(self) -> None:
        class FakeProtocolSource:
            def __init__(self):
                self.lookups: list[str] = []
                self.protocol_calls = 0

            def __getattr__(self, name: str):
                self.lookups.append(name)
                if name == "read":
                    raise AttributeError("READ_MISSING_PRIVATE_CANARY")
                if name == "__iter__":
                    def fake_iter():
                        self.protocol_calls += 1
                        return self

                    return fake_iter
                if name == "__next__":
                    def fake_next():
                        self.protocol_calls += 1
                        return b"fake"

                    return fake_next
                raise AttributeError("PROTOCOL_PRIVATE_CANARY")

        source = FakeProtocolSource()
        error = _assert_code(
            self,
            VerifiedMediaIngestCode.INVALID_ITERABLE,
            source,
        )
        self.assertEqual(source.lookups, ["read"])
        self.assertEqual(source.protocol_calls, 0)
        self.assertIsNone(error.__context__)

    def test_sync_iter_returning_coroutine_is_closed_without_warning(self) -> None:
        events: list[str] = []

        async def returned_iterator():
            events.append("COROUTINE_ITER_RESULT_BODY_CANARY")

        class Source:
            def __iter__(self):
                return returned_iterator()

        _assert_quiet_code(
            self,
            VerifiedMediaIngestCode.INVALID_ITERABLE,
            Source(),
        )
        self.assertEqual(events, [])

    def test_async_iter_is_rejected_without_invocation_or_warning(self) -> None:
        class Source:
            def __init__(self):
                self.called = False

            async def __iter__(self):
                self.called = True
                return self

        source = Source()
        _assert_quiet_code(
            self,
            VerifiedMediaIngestCode.INVALID_ITERABLE,
            source,
        )
        self.assertFalse(source.called)

    def test_sync_next_returning_coroutine_is_closed_without_warning(self) -> None:
        events: list[str] = []

        async def returned_chunk():
            events.append("COROUTINE_NEXT_RESULT_BODY_CANARY")
            return b""

        class Source:
            def __iter__(self):
                return self

            def __next__(self):
                return returned_chunk()

        _assert_quiet_code(
            self,
            VerifiedMediaIngestCode.INVALID_CHUNK,
            Source(),
        )
        self.assertEqual(events, [])

    def test_async_next_is_rejected_without_invocation_or_warning(self) -> None:
        class Source:
            def __init__(self):
                self.called = False

            def __iter__(self):
                return self

            async def __next__(self):
                self.called = True
                return b""

        source = Source()
        _assert_quiet_code(
            self,
            VerifiedMediaIngestCode.INVALID_ITERABLE,
            source,
        )
        self.assertFalse(source.called)

    def test_source_iter_exception_is_fixed_and_private(self) -> None:
        class HostileIterable:
            def __iter__(self):
                raise ValueError("ITER_PRIVATE_CANARY")

        error = _assert_code(self, VerifiedMediaIngestCode.INVALID_ITERABLE, HostileIterable())
        self.assertNotIn("ITER_PRIVATE_CANARY", str(error) + repr(error))
        self.assertIsNone(error.__context__)

    def test_source_next_exception_is_fixed_and_private(self) -> None:
        class HostileIterator:
            def __iter__(self):
                return self

            def __next__(self):
                raise ValueError("NEXT_PRIVATE_CANARY")

        error = _assert_code(self, VerifiedMediaIngestCode.SOURCE_FAILED, HostileIterator())
        self.assertNotIn("NEXT_PRIVATE_CANARY", str(error) + repr(error))
        self.assertIsNone(error.__context__)

    def test_reader_exception_is_fixed_and_private(self) -> None:
        source = RaisingReader(ValueError("READ_PRIVATE_CANARY"))
        error = _assert_code(self, VerifiedMediaIngestCode.SOURCE_FAILED, source)
        self.assertNotIn("READ_PRIVATE_CANARY", str(error) + repr(error))
        self.assertIsNone(error.__context__)
        self.assertEqual(source.close_calls, 0)

    def test_reader_baseexception_is_not_caught(self) -> None:
        class StopNow(BaseException):
            pass

        with self.assertRaises(StopNow):
            _ingest(RaisingReader(StopNow("BASE_PRIVATE_CANARY")))

    def test_iterator_baseexception_is_not_caught(self) -> None:
        class StopNow(BaseException):
            pass

        class Iterator:
            def __iter__(self):
                return self

            def __next__(self):
                raise StopNow("BASE_PRIVATE_CANARY")

        with self.assertRaises(StopNow):
            _ingest(Iterator())

    def test_read_requests_are_explicit_positive_and_bounded(self) -> None:
        content = b"x" * (VERIFIED_MEDIA_INGEST_CHUNK_BYTES + 10)
        reader = SyntheticReader(content)
        _ingest(reader, limit=len(content))
        self.assertTrue(reader.requests)
        self.assertTrue(all(type(size) is int and 0 < size <= VERIFIED_MEDIA_INGEST_CHUNK_BYTES for size in reader.requests))

    def test_client_metadata_is_never_inspected(self) -> None:
        content = b"metadata independent"
        result = _ingest(GuardedMetadataReader(content), limit=len(content))
        self.assertEqual(result.sha256, hashlib.sha256(content).hexdigest())

    def test_public_signature_accepts_no_client_metadata(self) -> None:
        signature = inspect.signature(ingest_verified_original_media)
        self.assertEqual(tuple(signature.parameters), ("source", "domain", "byte_limit"))

    def test_bad_entropy_result_is_fixed_failure(self) -> None:
        with mock.patch.object(ingest_module.secrets, "token_bytes", return_value=b"short"):
            _assert_code(self, VerifiedMediaIngestCode.ID_GENERATION_FAILED, [], limit=0)


class VerifiedMediaIngestPrivacyTests(unittest.TestCase):
    def test_error_string_and_repr_are_fixed(self) -> None:
        canary = "INGEST" + "_ERROR_PRIVATE_CANARY"
        error = _assert_code(self, VerifiedMediaIngestCode.INVALID_CHUNK, [canary])
        self.assertEqual(str(error), VerifiedMediaIngestCode.INVALID_CHUNK.value)
        self.assertNotIn(canary, str(error) + repr(error))

    def test_uncaught_traceback_does_not_echo_source_exception(self) -> None:
        canary = "TRACEBACK" + "_INGEST_PRIVATE_CANARY"
        try:
            _ingest(RaisingReader(ValueError(canary)))
        except VerifiedMediaIngestError as error:
            rendered = "".join(traceback.TracebackException.from_exception(error).format())
        else:  # pragma: no cover
            self.fail("ingest unexpectedly succeeded")
        self.assertNotIn(canary, rendered)

    def test_stdout_and_stderr_remain_empty(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            _ingest([], limit=0)
            _assert_code(self, VerifiedMediaIngestCode.INVALID_CHUNK, ["private"])
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_result_repr_exposes_no_metadata(self) -> None:
        result = _ingest([b"repr private"], limit=12)
        rendered = repr(result)
        self.assertNotIn(result.operation_id, rendered)
        self.assertNotIn(result.media_id, rendered)
        self.assertNotIn(result.sha256, rendered)
        self.assertNotIn("repr private", rendered)

    def test_source_repr_is_never_used(self) -> None:
        class HostileReprIterable:
            def __iter__(self):
                return iter((b"safe",))

            def __repr__(self):
                raise AssertionError("SOURCE_REPR_CANARY")

        self.assertEqual(_ingest(HostileReprIterable(), limit=4).byte_size, 4)


class VerifiedMediaIngestStaticPurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = PRODUCTION_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename=str(PRODUCTION_PATH))

    def test_only_standard_library_and_phase3q_imports(self) -> None:
        allowed_roots = {
            "__future__", "base64", "dataclasses", "enum", "hashlib",
            "inspect", "json", "secrets", "typing", "src",
        }
        roots: set[str] = set()
        imports_from_src: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
                if node.module.startswith("src"):
                    imports_from_src.add(node.module)
        self.assertTrue(roots <= allowed_roots)
        self.assertEqual(imports_from_src, {"src.marketmatch_media_attestation"})

    def test_no_forbidden_dependency_imports(self) -> None:
        prohibited = {
            "os", "pathlib", "tempfile", "shutil", "socket", "subprocess",
            "logging", "time", "datetime", "random", "uuid", "sqlalchemy",
            "requests", "numpy", "torch", "av", "faster_whisper", "chromadb",
        }
        roots: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0].lower() for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0].lower())
        self.assertTrue(roots.isdisjoint(prohibited))

    def test_no_routes_database_upload_worker_model_or_frontend_imports(self) -> None:
        modules: list[str] = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name.lower() for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module.lower())
        forbidden_tokens = (
            "route", "database", "sql", "orm", "upload", "worker", "queue",
            "cache", "whisper", "ffmpeg", "chroma", "rag", "frontend",
            "torch", "numpy", "av",
        )
        allowed = "src.marketmatch_media_attestation"
        self.assertFalse(any(any(token in name for token in forbidden_tokens) for name in modules if name != allowed))

    def test_no_filesystem_network_process_logging_or_persistence_calls(self) -> None:
        forbidden_names = {"open", "print", "exec", "eval", "compile", "__import__"}
        forbidden_attrs = {
            "environ", "getenv", "getcwd", "open", "write", "unlink", "remove",
            "mkdir", "makedirs", "connect", "send", "recv", "Popen", "run",
            "system", "execute", "commit", "rollback", "publish", "save",
            "dump", "dumps_log",
        }
        names = {
            node.func.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        attrs = {node.attr for node in ast.walk(self.tree) if isinstance(node, ast.Attribute)}
        self.assertTrue(names.isdisjoint(forbidden_names))
        self.assertTrue(attrs.isdisjoint(forbidden_attrs))

    def test_no_baseexception_catch(self) -> None:
        caught_names = {
            node.type.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.ExceptHandler)
            and isinstance(node.type, ast.Name)
        }
        self.assertNotIn("BaseException", caught_names)

    def test_no_source_rewind_close_or_metadata_inspection(self) -> None:
        forbidden = {
            "seek", "tell", "name", "filename", "mime_type",
            "content_length", "headers", "metadata", "path", "fileno",
        }
        attrs = {node.attr for node in ast.walk(self.tree) if isinstance(node, ast.Attribute)}
        self.assertTrue(attrs.isdisjoint(forbidden))

        close_calls = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "close"
        ]
        self.assertEqual(len(close_calls), 1)
        self.assertIsInstance(close_calls[0].func.value, ast.Name)
        self.assertEqual(close_calls[0].func.value.id, "value")

    def test_getattr_is_limited_to_read(self) -> None:
        names = [
            node.args[1].value
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
        ]
        self.assertEqual(names, ["read"])

    def test_no_unbounded_reader_call_syntax(self) -> None:
        calls = [
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "read_method"
        ]
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0].args), 1)
        self.assertEqual(calls[0].keywords, [])

    def test_no_media_accumulation_or_full_copy_calls(self) -> None:
        forbidden_calls = {"BytesIO", "bytearray", "memoryview", "bytes", "join"}
        called_names = {
            node.func.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        called_attrs = {
            node.func.attr
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertTrue(called_names.isdisjoint(forbidden_calls))
        self.assertTrue(called_attrs.isdisjoint(forbidden_calls))
        self.assertFalse(any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"append", "extend"}
            for node in ast.walk(self.tree)
        ))

    def test_hash_update_receives_only_current_chunk_or_read_result(self) -> None:
        updates = [
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
        ]
        self.assertEqual(len(updates), 1)
        self.assertEqual(len(updates[0].args), 1)
        self.assertIsInstance(updates[0].args[0], ast.Name)
        self.assertEqual(updates[0].args[0].id, "chunk")

    def test_identifier_generation_uses_fixed_secrets_entropy(self) -> None:
        token_calls = [
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "token_bytes"
        ]
        self.assertEqual(len(token_calls), 1)
        self.assertEqual(len(token_calls[0].args), 1)

    def test_public_signature_has_no_client_metadata(self) -> None:
        target = next(
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "ingest_verified_original_media"
        )
        names = tuple(arg.arg for arg in target.args.args + target.args.kwonlyargs)
        self.assertEqual(names, ("source", "domain", "byte_limit"))

    def test_fixed_resource_bounds(self) -> None:
        self.assertEqual(VERIFIED_MEDIA_INGEST_CHUNK_BYTES, 65_536)
        self.assertEqual(MAX_VERIFIED_MEDIA_INGEST_STEPS, 4_096)
        normal_calls = MAX_MEDIA_BYTES // VERIFIED_MEDIA_INGEST_CHUNK_BYTES + 1
        self.assertGreaterEqual(MAX_VERIFIED_MEDIA_INGEST_STEPS, normal_calls)

    def test_result_model_has_exact_fields(self) -> None:
        self.assertEqual(
            tuple(item.name for item in fields(VerifiedOriginalMediaIngest)),
            (
                "domain", "operation_id", "media_id", "media_role",
                "byte_size", "sha256", "attestation_canonical_bytes",
            ),
        )

    def test_source_compiles_without_execution(self) -> None:
        compile(self.source, str(PRODUCTION_PATH), "exec")

    def test_exactly_two_authorized_files_changed(self) -> None:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        changed = {line[3:] for line in completed.stdout.splitlines() if line}
        self.assertEqual(
            changed,
            {
                "src/marketmatch_verified_media_ingest.py",
                "tests/test_marketmatch_verified_media_ingest.py",
            },
        )


if __name__ == "__main__":
    unittest.main()
