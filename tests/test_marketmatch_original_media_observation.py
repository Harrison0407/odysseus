"""Synthetic tests for pure MarketMatch original-media observation."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
import gc
import hashlib
import inspect
import io
from pathlib import Path
import subprocess
import sys
import traceback
import tracemalloc
import unittest
from unittest import mock
import warnings
import weakref

from src.marketmatch_original_media_observation import (
    MAX_ORIGINAL_MEDIA_BYTES,
    MAX_READ_CALLS,
    READ_CHUNK_BYTES,
    OriginalMediaObservation,
    OriginalMediaObservationCode,
    OriginalMediaObservationError,
    observe_original_media,
)


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_PATH = ROOT / "src" / "marketmatch_original_media_observation.py"
APPROVED_PATHS = {
    "src/marketmatch_original_media_observation.py",
    "tests/test_marketmatch_original_media_observation.py",
}


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


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


class GuardedReader(SyntheticReader):
    @property
    def name(self):
        raise AssertionError("READER_NAME_PRIVACY_CANARY")

    @property
    def path(self):
        raise AssertionError("READER_PATH_PRIVACY_CANARY")

    @property
    def mode(self):
        raise AssertionError("READER_MODE_PRIVACY_CANARY")

    def fileno(self):
        raise AssertionError("READER_FILENO_PRIVACY_CANARY")

    def seek(self, *args):
        raise AssertionError("READER_SEEK_PRIVACY_CANARY")

    def tell(self):
        raise AssertionError("READER_TELL_PRIVACY_CANARY")

    def close(self):
        raise AssertionError("READER_CLOSE_PRIVACY_CANARY")


class FixedResultReader:
    def __init__(self, value: object):
        self.value = value
        self.requests: list[int] = []
        self.close_calls = 0

    def read(self, size: int) -> object:
        self.requests.append(size)
        return self.value

    def close(self) -> None:
        self.close_calls += 1


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


class RepeatingReader:
    """A large logical reader retaining only one fixed source block."""

    def __init__(self, byte_size: int, *, fill: bytes = b"x"):
        if len(fill) != 1:
            raise ValueError("synthetic fill must be one byte")
        self.remaining = byte_size
        self.block = fill * READ_CHUNK_BYTES
        self.requests: list[int] = []
        self.calls = 0

    def read(self, size: int) -> bytes:
        self.requests.append(size)
        self.calls += 1
        if self.remaining == 0:
            return b""
        take = min(size, self.remaining)
        self.remaining -= take
        if take == READ_CHUNK_BYTES:
            return self.block
        return self.block[:take]


def _assert_code(
    test: unittest.TestCase,
    code: OriginalMediaObservationCode,
    source: object,
    *,
    byte_limit: object,
) -> OriginalMediaObservationError:
    with test.assertRaises(OriginalMediaObservationError) as caught:
        observe_original_media(source, byte_limit=byte_limit)  # type: ignore[arg-type]
    test.assertIs(caught.exception.code, code)
    return caught.exception


class ObservationSuccessTests(unittest.TestCase):
    def test_valid_empty_source(self) -> None:
        result = observe_original_media(SyntheticReader(b""), byte_limit=1)
        self.assertEqual(result.byte_size, 0)

    def test_valid_one_byte_source(self) -> None:
        result = observe_original_media(SyntheticReader(b"z"), byte_limit=1)
        self.assertEqual(result.byte_size, 1)
        self.assertEqual(result.sha256, _sha(b"z"))

    def test_valid_small_source(self) -> None:
        content = b"synthetic original media"
        result = observe_original_media(SyntheticReader(content), byte_limit=len(content))
        self.assertEqual(result.byte_size, len(content))

    def test_valid_multi_chunk_source(self) -> None:
        content = b"a" * (READ_CHUNK_BYTES * 2 + 17)
        reader = SyntheticReader(content)
        result = observe_original_media(reader, byte_limit=len(content))
        self.assertEqual(result.sha256, _sha(content))
        self.assertEqual(reader.position, len(content))

    def test_valid_synthetic_one_hundred_mib_source(self) -> None:
        reader = RepeatingReader(MAX_ORIGINAL_MEDIA_BYTES)
        result = observe_original_media(reader, byte_limit=MAX_ORIGINAL_MEDIA_BYTES)
        digest = hashlib.sha256()
        for _ in range(MAX_ORIGINAL_MEDIA_BYTES // READ_CHUNK_BYTES):
            digest.update(reader.block)
        self.assertEqual(result.byte_size, MAX_ORIGINAL_MEDIA_BYTES)
        self.assertEqual(result.sha256, digest.hexdigest())

    def test_zero_limit_accepts_empty_source(self) -> None:
        result = observe_original_media(SyntheticReader(b""), byte_limit=0)
        self.assertEqual(result.byte_size, 0)

    def test_exact_caller_limit_succeeds(self) -> None:
        content = b"exact"
        result = observe_original_media(SyntheticReader(content), byte_limit=len(content))
        self.assertEqual(result.byte_size, len(content))

    def test_short_reads_before_eof(self) -> None:
        content = b"short reads are data"
        reader = SyntheticReader(content, max_read=3)
        result = observe_original_media(reader, byte_limit=len(content))
        self.assertEqual(result.sha256, _sha(content))
        self.assertGreater(len(reader.requests), 3)

    def test_one_byte_reads_for_small_source(self) -> None:
        content = b"one-byte"
        reader = SyntheticReader(content, max_read=1)
        result = observe_original_media(reader, byte_limit=len(content))
        self.assertEqual(result.byte_size, len(content))
        self.assertEqual(len(reader.requests), len(content) + 1)

    def test_only_exact_empty_bytes_is_eof(self) -> None:
        reader = SyntheticReader(b"ab", max_read=1)
        observe_original_media(reader, byte_limit=2)
        self.assertEqual(reader.requests, [3, 2, 1])

    def test_empty_sha256_is_exact(self) -> None:
        result = observe_original_media(SyntheticReader(b""), byte_limit=0)
        self.assertEqual(result.sha256, _sha(b""))

    def test_exact_byte_size_is_reported(self) -> None:
        content = b"count-the-exact-bytes"
        result = observe_original_media(SyntheticReader(content), byte_limit=100)
        self.assertEqual(result.byte_size, len(content))

    def test_exact_sha256_is_reported(self) -> None:
        content = b"hash-the-exact-bytes"
        result = observe_original_media(SyntheticReader(content), byte_limit=100)
        self.assertEqual(result.sha256, hashlib.sha256(content).hexdigest())

    def test_same_bytes_have_deterministic_result(self) -> None:
        content = b"deterministic"
        first = observe_original_media(SyntheticReader(content), byte_limit=100)
        second = observe_original_media(SyntheticReader(content), byte_limit=100)
        self.assertEqual(first, second)

    def test_one_byte_mutation_changes_digest(self) -> None:
        first = observe_original_media(SyntheticReader(b"abc"), byte_limit=3)
        second = observe_original_media(SyntheticReader(b"abd"), byte_limit=3)
        self.assertNotEqual(first.sha256, second.sha256)


class ObservationLimitTests(unittest.TestCase):
    def test_zero_limit_rejects_nonempty_source(self) -> None:
        reader = SyntheticReader(b"x")
        _assert_code(self, OriginalMediaObservationCode.INPUT_LIMIT_EXCEEDED, reader, byte_limit=0)
        self.assertEqual(reader.position, 1)

    def test_limit_plus_one_rejected_immediately(self) -> None:
        reader = SyntheticReader(b"abcd")
        _assert_code(self, OriginalMediaObservationCode.INPUT_LIMIT_EXCEEDED, reader, byte_limit=3)
        self.assertEqual(reader.position, 4)

    def test_negative_byte_limit_rejected(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.INVALID_BYTE_LIMIT, SyntheticReader(b""), byte_limit=-1)

    def test_boolean_byte_limit_rejected(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.INVALID_BYTE_LIMIT, SyntheticReader(b""), byte_limit=True)

    def test_float_byte_limit_rejected(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.INVALID_BYTE_LIMIT, SyntheticReader(b""), byte_limit=1.0)

    def test_string_byte_limit_rejected(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.INVALID_BYTE_LIMIT, SyntheticReader(b""), byte_limit="1")

    def test_integer_subclass_byte_limit_rejected(self) -> None:
        class HostileInt(int):
            def __int__(self):
                raise AssertionError("LIMIT_COERCION_PRIVACY_CANARY")

        error = _assert_code(self, OriginalMediaObservationCode.INVALID_BYTE_LIMIT, SyntheticReader(b""), byte_limit=HostileInt(1))
        self.assertNotIn("LIMIT_COERCION_PRIVACY_CANARY", str(error) + repr(error))

    def test_coercible_byte_limit_rejected_without_coercion(self) -> None:
        class Coercible:
            def __int__(self):
                raise AssertionError("LIMIT_COERCION_PRIVACY_CANARY")

        _assert_code(self, OriginalMediaObservationCode.INVALID_BYTE_LIMIT, SyntheticReader(b""), byte_limit=Coercible())

    def test_byte_limit_above_absolute_cap_rejected(self) -> None:
        _assert_code(
            self,
            OriginalMediaObservationCode.INVALID_BYTE_LIMIT,
            SyntheticReader(b""),
            byte_limit=MAX_ORIGINAL_MEDIA_BYTES + 1,
        )

    def test_invalid_limit_checked_before_source_inspection(self) -> None:
        class HostileSource:
            def __getattribute__(self, name):
                raise AssertionError("SOURCE_INSPECTION_PRIVACY_CANARY")

        error = _assert_code(self, OriginalMediaObservationCode.INVALID_BYTE_LIMIT, HostileSource(), byte_limit=-1)
        self.assertNotIn("SOURCE_INSPECTION_PRIVACY_CANARY", str(error) + repr(error))

    def test_absolute_limit_is_exact(self) -> None:
        self.assertEqual(MAX_ORIGINAL_MEDIA_BYTES, 104_857_600)

    def test_chunk_size_is_exact(self) -> None:
        self.assertEqual(READ_CHUNK_BYTES, 65_536)

    def test_read_call_ceiling_is_exact(self) -> None:
        self.assertEqual(MAX_READ_CALLS, 4_096)


class ObservationSourceBoundaryTests(unittest.TestCase):
    def test_string_source_rejected(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.INVALID_READER, "synthetic/path", byte_limit=1)

    def test_bytes_source_rejected(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.INVALID_READER, b"not-a-reader", byte_limit=1)

    def test_bytearray_source_rejected(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.INVALID_READER, bytearray(b"x"), byte_limit=1)

    def test_memoryview_source_rejected(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.INVALID_READER, memoryview(b"x"), byte_limit=1)

    def test_path_source_rejected(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.INVALID_READER, Path("synthetic.media"), byte_limit=1)

    def test_custom_path_protocol_with_read_is_rejected_without_path_call(self) -> None:
        class PathReader:
            def __fspath__(self):
                raise AssertionError("PATH_PROTOCOL_PRIVACY_CANARY")

            def read(self, size: int) -> bytes:
                del size
                return b""

        _assert_code(self, OriginalMediaObservationCode.INVALID_READER, PathReader(), byte_limit=1)

    def test_integer_descriptor_rejected(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.INVALID_READER, 7, byte_limit=1)

    def test_list_iterable_rejected(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.INVALID_READER, [b"x"], byte_limit=1)

    def test_tuple_iterable_rejected(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.INVALID_READER, (b"x",), byte_limit=1)

    def test_iterator_rejected(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.INVALID_READER, iter((b"x",)), byte_limit=1)

    def test_generator_rejected_without_iteration(self) -> None:
        executed = False

        def source_generator():
            nonlocal executed
            executed = True
            yield b"x"

        generator = source_generator()
        try:
            _assert_code(self, OriginalMediaObservationCode.INVALID_READER, generator, byte_limit=1)
            self.assertFalse(executed)
        finally:
            generator.close()

    def test_async_iterator_rejected(self) -> None:
        class AsyncIterator:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        _assert_code(self, OriginalMediaObservationCode.INVALID_READER, AsyncIterator(), byte_limit=1)

    def test_callback_rejected_without_invocation(self) -> None:
        called = False

        def callback():
            nonlocal called
            called = True

        _assert_code(self, OriginalMediaObservationCode.INVALID_READER, callback, byte_limit=1)
        self.assertFalse(called)

    def test_callable_reader_factory_rejected_without_invocation(self) -> None:
        class FactoryReader(SyntheticReader):
            def __call__(self):
                raise AssertionError("FACTORY_PRIVACY_CANARY")

        _assert_code(self, OriginalMediaObservationCode.INVALID_READER, FactoryReader(b""), byte_limit=1)

    def test_awaitable_source_rejected_without_awaiting(self) -> None:
        class AwaitableReader:
            def __init__(self):
                self.awaited = False

            def __await__(self):
                self.awaited = True
                yield

            def read(self, size: int) -> bytes:
                del size
                return b""

        source = AwaitableReader()
        _assert_code(self, OriginalMediaObservationCode.INVALID_READER, source, byte_limit=1)
        self.assertFalse(source.awaited)

    def test_native_coroutine_source_rejected_without_execution(self) -> None:
        executed = False

        async def coroutine_source():
            nonlocal executed
            executed = True

        source = coroutine_source()
        try:
            _assert_code(self, OriginalMediaObservationCode.INVALID_READER, source, byte_limit=1)
            self.assertFalse(executed)
        finally:
            source.close()

    def test_async_reader_rejected_without_call(self) -> None:
        class AsyncReader:
            def __init__(self):
                self.called = False

            async def read(self, size: int) -> bytes:
                del size
                self.called = True
                return b""

        source = AsyncReader()
        _assert_code(self, OriginalMediaObservationCode.INVALID_READER, source, byte_limit=1)
        self.assertFalse(source.called)

    def test_async_callable_read_object_rejected_without_call(self) -> None:
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
        _assert_code(self, OriginalMediaObservationCode.INVALID_READER, source, byte_limit=1)
        self.assertFalse(source.read.called)

    def test_missing_read_method_rejected(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.INVALID_READER, object(), byte_limit=1)

    def test_noncallable_read_member_rejected(self) -> None:
        class NonCallableRead:
            read = b"not-callable"

        _assert_code(self, OriginalMediaObservationCode.INVALID_READER, NonCallableRead(), byte_limit=1)

    def test_hostile_read_property_maps_to_fixed_error(self) -> None:
        class HostileReadProperty:
            @property
            def read(self):
                raise RuntimeError("READ_PROPERTY_PRIVACY_CANARY")

        error = _assert_code(self, OriginalMediaObservationCode.INVALID_READER, HostileReadProperty(), byte_limit=1)
        self.assertNotIn("READ_PROPERTY_PRIVACY_CANARY", str(error) + repr(error))

    def test_hostile_source_repr_is_not_used(self) -> None:
        class HostileReprReader(SyntheticReader):
            def __repr__(self):
                raise AssertionError("SOURCE_REPR_PRIVACY_CANARY")

        observe_original_media(HostileReprReader(b"safe"), byte_limit=4)


class ObservationReadResultTests(unittest.TestCase):
    def test_bytearray_result_rejected(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.INVALID_READ_RESULT, FixedResultReader(bytearray(b"x")), byte_limit=1)

    def test_memoryview_result_rejected(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.INVALID_READ_RESULT, FixedResultReader(memoryview(b"x")), byte_limit=1)

    def test_string_result_rejected(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.INVALID_READ_RESULT, FixedResultReader("x"), byte_limit=1)

    def test_none_result_rejected(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.INVALID_READ_RESULT, FixedResultReader(None), byte_limit=1)

    def test_integer_result_rejected(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.INVALID_READ_RESULT, FixedResultReader(1), byte_limit=1)

    def test_bytes_subclass_result_rejected_without_coercion(self) -> None:
        class HostileBytes(bytes):
            def __bytes__(self):
                raise AssertionError("BYTES_SUBCLASS_PRIVACY_CANARY")

            def __repr__(self):
                raise AssertionError("BYTES_SUBCLASS_PRIVACY_CANARY")

        error = _assert_code(self, OriginalMediaObservationCode.INVALID_READ_RESULT, FixedResultReader(HostileBytes(b"x")), byte_limit=1)
        self.assertNotIn("BYTES_SUBCLASS_PRIVACY_CANARY", str(error) + repr(error))

    def test_custom_coercible_result_rejected_without_coercion(self) -> None:
        class HostileResult:
            def __bytes__(self):
                raise AssertionError("RESULT_COERCION_PRIVACY_CANARY")

            def __repr__(self):
                raise AssertionError("RESULT_COERCION_PRIVACY_CANARY")

        error = _assert_code(self, OriginalMediaObservationCode.INVALID_READ_RESULT, FixedResultReader(HostileResult()), byte_limit=1)
        self.assertNotIn("RESULT_COERCION_PRIVACY_CANARY", str(error) + repr(error))

    def test_custom_awaitable_result_rejected_without_awaiting(self) -> None:
        class AwaitableResult:
            def __init__(self):
                self.awaited = False

            def __await__(self):
                self.awaited = True
                yield

        value = AwaitableResult()
        _assert_code(self, OriginalMediaObservationCode.INVALID_READ_RESULT, FixedResultReader(value), byte_limit=1)
        self.assertFalse(value.awaited)

    def test_native_coroutine_result_is_not_executed(self) -> None:
        executed = False

        async def returned_coroutine():
            nonlocal executed
            executed = True
            return b""

        value = returned_coroutine()
        _assert_code(self, OriginalMediaObservationCode.INVALID_READ_RESULT, FixedResultReader(value), byte_limit=1)
        self.assertFalse(executed)
        self.assertEqual(inspect.getcoroutinestate(value), inspect.CORO_CLOSED)

    def test_native_coroutine_result_emits_no_warning_or_stderr(self) -> None:
        async def returned_coroutine():
            return b""

        value = returned_coroutine()
        stderr = io.StringIO()
        with warnings.catch_warnings(record=True) as caught, mock.patch("sys.stderr", stderr):
            warnings.simplefilter("always")
            _assert_code(self, OriginalMediaObservationCode.INVALID_READ_RESULT, FixedResultReader(value), byte_limit=1)
            gc.collect()
        self.assertEqual(caught, [])
        self.assertEqual(stderr.getvalue(), "")

    def test_result_larger_than_requested_rejected(self) -> None:
        class OverReturningReader:
            def read(self, size: int) -> bytes:
                return b"x" * (size + 1)

        _assert_code(self, OriginalMediaObservationCode.INVALID_READ_RESULT, OverReturningReader(), byte_limit=1)

    def test_false_is_not_eof(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.INVALID_READ_RESULT, FixedResultReader(False), byte_limit=1)


class ObservationFailureTests(unittest.TestCase):
    def test_reader_normal_exception_maps_to_source_failed(self) -> None:
        _assert_code(self, OriginalMediaObservationCode.SOURCE_FAILED, RaisingReader(ValueError("failure")), byte_limit=1)

    def test_reader_privacy_canary_exception_does_not_leak(self) -> None:
        error = _assert_code(
            self,
            OriginalMediaObservationCode.SOURCE_FAILED,
            RaisingReader(ValueError("READER_EXCEPTION_PRIVACY_CANARY")),
            byte_limit=1,
        )
        self.assertNotIn("READER_EXCEPTION_PRIVACY_CANARY", str(error) + repr(error))

    def test_reader_exception_chain_is_suppressed(self) -> None:
        error = _assert_code(
            self,
            OriginalMediaObservationCode.SOURCE_FAILED,
            RaisingReader(ValueError("CHAIN_PRIVACY_CANARY")),
            byte_limit=1,
        )
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)

    def test_reader_baseexception_propagates(self) -> None:
        class ReaderControlSignal(BaseException):
            pass

        signal = ReaderControlSignal("control")
        with self.assertRaises(ReaderControlSignal) as caught:
            observe_original_media(RaisingReader(signal), byte_limit=1)
        self.assertIs(caught.exception, signal)

    def test_reader_baseexception_is_not_wrapped(self) -> None:
        with self.assertRaises(KeyboardInterrupt):
            observe_original_media(RaisingReader(KeyboardInterrupt()), byte_limit=1)

    def test_partial_bytes_not_exposed_after_source_failure(self) -> None:
        class PartialThenFailure:
            def __init__(self):
                self.calls = 0

            def read(self, size: int) -> bytes:
                del size
                self.calls += 1
                if self.calls == 1:
                    return b"partial"
                raise ValueError("failure")

        error = _assert_code(self, OriginalMediaObservationCode.SOURCE_FAILED, PartialThenFailure(), byte_limit=100)
        self.assertFalse(hasattr(error, "byte_size"))
        self.assertFalse(hasattr(error, "sha256"))
        self.assertNotIn("partial", str(error) + repr(error))

    def test_partial_bytes_not_exposed_after_limit_failure(self) -> None:
        error = _assert_code(self, OriginalMediaObservationCode.INPUT_LIMIT_EXCEEDED, SyntheticReader(b"abcd"), byte_limit=3)
        self.assertFalse(hasattr(error, "byte_size"))
        self.assertFalse(hasattr(error, "sha256"))

    def test_source_not_closed_on_failure(self) -> None:
        source = RaisingReader(ValueError("failure"))
        _assert_code(self, OriginalMediaObservationCode.SOURCE_FAILED, source, byte_limit=1)
        self.assertEqual(source.close_calls, 0)


class ObservationReadLoopTests(unittest.TestCase):
    def test_every_read_request_is_positive(self) -> None:
        reader = SyntheticReader(b"abcdef", max_read=1)
        observe_original_media(reader, byte_limit=6)
        self.assertTrue(all(type(size) is int and size > 0 for size in reader.requests))

    def test_every_read_request_is_chunk_bounded(self) -> None:
        reader = SyntheticReader(b"x" * (READ_CHUNK_BYTES + 1))
        observe_original_media(reader, byte_limit=READ_CHUNK_BYTES + 1)
        self.assertTrue(all(size <= READ_CHUNK_BYTES for size in reader.requests))

    def test_first_read_requests_limit_plus_one_when_smaller_than_chunk(self) -> None:
        reader = SyntheticReader(b"abc")
        observe_original_media(reader, byte_limit=3)
        self.assertEqual(reader.requests[0], 4)

    def test_exact_limit_is_followed_by_one_byte_eof_probe(self) -> None:
        reader = SyntheticReader(b"abc")
        observe_original_media(reader, byte_limit=3)
        self.assertEqual(reader.requests, [4, 1])

    def test_no_extra_read_after_eof(self) -> None:
        reader = SyntheticReader(b"")
        observe_original_media(reader, byte_limit=100)
        self.assertEqual(len(reader.requests), 1)

    def test_no_second_pass(self) -> None:
        reader = SyntheticReader(b"single-pass", max_read=2)
        observe_original_media(reader, byte_limit=100)
        self.assertEqual(reader.position, len(reader.content))
        expected_data_reads = (len(reader.content) + 1) // 2
        self.assertEqual(len(reader.requests), expected_data_reads + 1)

    def test_infinite_tiny_reads_reach_call_ceiling(self) -> None:
        class TinyForever:
            def __init__(self):
                self.calls = 0
                self.requests: list[int] = []

            def read(self, size: int) -> bytes:
                self.calls += 1
                self.requests.append(size)
                return b"x"

        reader = TinyForever()
        _assert_code(
            self,
            OriginalMediaObservationCode.READ_CALL_LIMIT_EXCEEDED,
            reader,
            byte_limit=MAX_ORIGINAL_MEDIA_BYTES,
        )
        self.assertEqual(reader.calls, MAX_READ_CALLS)

    def test_infinite_nul_reads_reach_call_ceiling(self) -> None:
        class NulForever:
            def __init__(self):
                self.calls = 0

            def read(self, size: int) -> bytes:
                del size
                self.calls += 1
                return b"\x00"

        reader = NulForever()
        _assert_code(
            self,
            OriginalMediaObservationCode.READ_CALL_LIMIT_EXCEEDED,
            reader,
            byte_limit=MAX_ORIGINAL_MEDIA_BYTES,
        )
        self.assertEqual(reader.calls, MAX_READ_CALLS)

    def test_final_eof_call_counts_toward_ceiling_and_can_succeed(self) -> None:
        class EofAtCeiling:
            def __init__(self):
                self.calls = 0

            def read(self, size: int) -> bytes:
                del size
                self.calls += 1
                if self.calls == MAX_READ_CALLS:
                    return b""
                return b"x"

        reader = EofAtCeiling()
        result = observe_original_media(reader, byte_limit=MAX_READ_CALLS - 1)
        self.assertEqual(result.byte_size, MAX_READ_CALLS - 1)
        self.assertEqual(reader.calls, MAX_READ_CALLS)

    def test_eof_after_ceiling_is_not_read(self) -> None:
        class EofTooLate:
            def __init__(self):
                self.calls = 0

            def read(self, size: int) -> bytes:
                del size
                self.calls += 1
                if self.calls > MAX_READ_CALLS:
                    return b""
                return b"x"

        reader = EofTooLate()
        _assert_code(
            self,
            OriginalMediaObservationCode.READ_CALL_LIMIT_EXCEEDED,
            reader,
            byte_limit=MAX_ORIGINAL_MEDIA_BYTES,
        )
        self.assertEqual(reader.calls, MAX_READ_CALLS)

    def test_normal_one_hundred_mib_source_is_below_call_ceiling(self) -> None:
        reader = RepeatingReader(MAX_ORIGINAL_MEDIA_BYTES)
        observe_original_media(reader, byte_limit=MAX_ORIGINAL_MEDIA_BYTES)
        expected = MAX_ORIGINAL_MEDIA_BYTES // READ_CHUNK_BYTES + 1
        self.assertEqual(reader.calls, expected)
        self.assertLess(reader.calls, MAX_READ_CALLS)

    def test_reader_returning_exact_empty_bytes_stops_immediately(self) -> None:
        source = FixedResultReader(b"")
        result = observe_original_media(source, byte_limit=100)
        self.assertEqual(result.byte_size, 0)
        self.assertEqual(len(source.requests), 1)


class ObservationOwnershipTests(unittest.TestCase):
    def test_reader_not_closed_on_success(self) -> None:
        source = SyntheticReader(b"borrowed")
        observe_original_media(source, byte_limit=100)
        self.assertEqual(source.close_calls, 0)

    def test_guarded_reader_lifecycle_and_metadata_are_untouched(self) -> None:
        observe_original_media(GuardedReader(b"safe"), byte_limit=4)

    def test_no_seek(self) -> None:
        observe_original_media(GuardedReader(b"safe"), byte_limit=4)

    def test_no_tell(self) -> None:
        observe_original_media(GuardedReader(b"safe"), byte_limit=4)

    def test_no_rewind(self) -> None:
        reader = SyntheticReader(b"advance")
        observe_original_media(reader, byte_limit=100)
        self.assertEqual(reader.position, len(reader.content))

    def test_no_name_access(self) -> None:
        observe_original_media(GuardedReader(b"safe"), byte_limit=4)

    def test_no_path_access(self) -> None:
        observe_original_media(GuardedReader(b"safe"), byte_limit=4)

    def test_no_fileno_access(self) -> None:
        observe_original_media(GuardedReader(b"safe"), byte_limit=4)

    def test_no_mode_access(self) -> None:
        observe_original_media(GuardedReader(b"safe"), byte_limit=4)

    def test_source_not_retained_after_success(self) -> None:
        source = SyntheticReader(b"weak")
        reference = weakref.ref(source)
        result = observe_original_media(source, byte_limit=4)
        del source
        gc.collect()
        self.assertIsNone(reference())
        self.assertEqual(result.byte_size, 4)

    def test_result_has_no_source_field(self) -> None:
        self.assertEqual(
            tuple(item.name for item in fields(OriginalMediaObservation)),
            ("byte_size", "sha256"),
        )

    def test_source_unchanged_except_natural_read_position(self) -> None:
        source = SyntheticReader(b"position", max_read=2)
        original_content = source.content
        original_max_read = source.max_read
        observe_original_media(source, byte_limit=100)
        self.assertIs(source.content, original_content)
        self.assertEqual(source.max_read, original_max_read)
        self.assertEqual(source.position, len(source.content))


class ObservationResultModelTests(unittest.TestCase):
    def test_result_is_frozen(self) -> None:
        result = observe_original_media(SyntheticReader(b"x"), byte_limit=1)
        with self.assertRaises(FrozenInstanceError):
            result.byte_size = 2  # type: ignore[misc]

    def test_result_is_slotted(self) -> None:
        result = observe_original_media(SyntheticReader(b"x"), byte_limit=1)
        self.assertFalse(hasattr(result, "__dict__"))

    def test_result_fields_are_exactly_approved(self) -> None:
        names = tuple(item.name for item in fields(OriginalMediaObservation))
        self.assertEqual(names, ("byte_size", "sha256"))

    def test_result_has_no_payload_bytes(self) -> None:
        result = observe_original_media(SyntheticReader(b"secret"), byte_limit=10)
        self.assertFalse(hasattr(result, "payload_bytes"))
        self.assertNotIn("secret", repr(result))

    def test_result_has_no_operation_id(self) -> None:
        result = observe_original_media(SyntheticReader(b"x"), byte_limit=1)
        self.assertFalse(hasattr(result, "operation_id"))

    def test_result_has_no_media_id(self) -> None:
        result = observe_original_media(SyntheticReader(b"x"), byte_limit=1)
        self.assertFalse(hasattr(result, "media_id"))

    def test_result_has_no_domain(self) -> None:
        result = observe_original_media(SyntheticReader(b"x"), byte_limit=1)
        self.assertFalse(hasattr(result, "domain"))

    def test_result_has_no_authority(self) -> None:
        result = observe_original_media(SyntheticReader(b"x"), byte_limit=1)
        self.assertFalse(hasattr(result, "authority"))

    def test_result_has_no_attestation(self) -> None:
        result = observe_original_media(SyntheticReader(b"x"), byte_limit=1)
        self.assertFalse(hasattr(result, "attestation"))

    def test_result_fields_excluded_from_repr(self) -> None:
        result = observe_original_media(SyntheticReader(b"repr-canary"), byte_limit=100)
        rendered = repr(result)
        self.assertEqual(rendered, "OriginalMediaObservation()")
        self.assertNotIn(result.sha256, rendered)
        self.assertNotIn(str(result.byte_size), rendered)


class ObservationMemoryTests(unittest.TestCase):
    def test_large_observation_has_bounded_traced_memory(self) -> None:
        source = RepeatingReader(8 * 1024 * 1024)
        tracemalloc.start()
        try:
            result = observe_original_media(source, byte_limit=8 * 1024 * 1024)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(result.byte_size, 8 * 1024 * 1024)
        self.assertLess(peak, 2 * 1024 * 1024)

    def test_return_model_does_not_retain_chunk(self) -> None:
        source = RepeatingReader(READ_CHUNK_BYTES)
        result = observe_original_media(source, byte_limit=READ_CHUNK_BYTES)
        self.assertEqual(tuple(item.name for item in fields(result)), ("byte_size", "sha256"))


class ObservationPrivacyTests(unittest.TestCase):
    def test_every_public_error_string_is_fixed_code(self) -> None:
        for code in OriginalMediaObservationCode:
            error = OriginalMediaObservationError(code)
            self.assertEqual(str(error), code.value)
            self.assertEqual(repr(error), f"OriginalMediaObservationError(code={code.value!r})")

    def test_canary_absent_from_exception_string_and_repr(self) -> None:
        canary = "ERROR_PRIVACY_CANARY"
        error = _assert_code(
            self,
            OriginalMediaObservationCode.SOURCE_FAILED,
            RaisingReader(ValueError(canary)),
            byte_limit=1,
        )
        self.assertNotIn(canary, str(error) + repr(error))

    def test_canary_absent_from_uncaught_traceback(self) -> None:
        canary = "TRACEBACK_PRIVACY_CANARY"
        try:
            observe_original_media(RaisingReader(ValueError(canary)), byte_limit=1)
        except OriginalMediaObservationError as error:
            rendered = "".join(traceback.TracebackException.from_exception(error).format())
        else:  # pragma: no cover
            self.fail("observation unexpectedly succeeded")
        self.assertNotIn(canary, rendered)

    def test_stdout_and_stderr_empty_on_success(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            observe_original_media(SyntheticReader(b"quiet"), byte_limit=5)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_stdout_and_stderr_empty_on_failure(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            _assert_code(
                self,
                OriginalMediaObservationCode.SOURCE_FAILED,
                RaisingReader(ValueError("STREAM_PRIVACY_CANARY")),
                byte_limit=1,
            )
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_error_does_not_retain_source(self) -> None:
        source = RaisingReader(ValueError("failure"))
        reference = weakref.ref(source)
        try:
            observe_original_media(source, byte_limit=1)
        except OriginalMediaObservationError as caught:
            error = caught
        else:  # pragma: no cover
            self.fail("observation unexpectedly succeeded")
        del source
        gc.collect()
        self.assertIsNone(reference())
        self.assertFalse(hasattr(error, "source"))

    def test_error_does_not_retain_invalid_return_object(self) -> None:
        class InvalidResult:
            pass

        class OneShotInvalidReader:
            def __init__(self, value: object):
                self.value = value

            def read(self, size: int) -> object:
                del size
                value = self.value
                self.value = None
                return value

        value = InvalidResult()
        reference = weakref.ref(value)
        source = OneShotInvalidReader(value)
        try:
            observe_original_media(source, byte_limit=1)
        except OriginalMediaObservationError as caught:
            error = caught
        else:  # pragma: no cover
            self.fail("observation unexpectedly succeeded")
        del value
        del source
        gc.collect()
        self.assertIsNone(reference())
        self.assertIs(error.code, OriginalMediaObservationCode.INVALID_READ_RESULT)

    def test_public_error_traceback_has_no_sensitive_worker_locals(self) -> None:
        source = RaisingReader(ValueError("failure"))
        try:
            observe_original_media(source, byte_limit=1)
        except OriginalMediaObservationError as caught:
            error = caught
        else:  # pragma: no cover
            self.fail("observation unexpectedly succeeded")

        production_local_names: set[str] = set()
        traceback_node = error.__traceback__
        while traceback_node is not None:
            frame = traceback_node.tb_frame
            if Path(frame.f_code.co_filename).resolve() == PRODUCTION_PATH:
                production_local_names.update(frame.f_locals)
            traceback_node = traceback_node.tb_next
        self.assertTrue(
            production_local_names.isdisjoint(
                {"source", "read_method", "chunk", "result", "digest", "observed_size"}
            )
        )


class ObservationStaticPurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = PRODUCTION_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def _import_roots(self) -> set[str]:
        roots: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        return roots

    def test_standard_library_only_imports(self) -> None:
        allowed = {"__future__", "dataclasses", "enum", "hashlib", "inspect", "typing"}
        self.assertLessEqual(self._import_roots(), allowed)

    def test_no_prohibited_imports(self) -> None:
        prohibited = {
            "os", "pathlib", "tempfile", "shutil", "subprocess", "socket",
            "logging", "time", "datetime", "random", "secrets", "uuid",
            "asyncio", "sqlalchemy", "numpy", "torch", "av",
            "faster_whisper", "chromadb",
        }
        self.assertTrue(self._import_roots().isdisjoint(prohibited))

    def test_no_application_or_approved_contract_imports(self) -> None:
        modules: list[str] = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name.lower() for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module.lower())
        forbidden_tokens = (
            "src.", "app", "route", "core", "database", "upload", "worker",
            "attestation", "marketmatch_stt_input", "whisper", "ffmpeg",
            "chroma", "rag", "sqlalchemy", "numpy", "torch", "av",
        )
        self.assertFalse(any(any(token in module for token in forbidden_tokens) for module in modules))

    def test_no_open_print_logging_or_dynamic_execution_calls(self) -> None:
        forbidden = {"open", "print", "exec", "eval", "compile", "__import__"}
        calls = {
            node.func.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue(calls.isdisjoint(forbidden))

    def test_no_filesystem_environment_network_process_sql_or_time_attributes(self) -> None:
        forbidden = {
            "environ", "getenv", "getcwd", "open", "write", "unlink", "remove",
            "mkdir", "makedirs", "connect", "send", "recv", "Popen", "run",
            "system", "execute", "commit", "rollback", "publish", "now",
            "today", "time", "sleep", "mmap",
        }
        attributes = {node.attr for node in ast.walk(self.tree) if isinstance(node, ast.Attribute)}
        self.assertTrue(attributes.isdisjoint(forbidden))

    def test_source_metadata_descriptor_and_lifecycle_are_not_inspected(self) -> None:
        forbidden = {"name", "path", "fileno", "seek", "tell", "close", "mode"}
        direct = {
            node.attr
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "source"
        }
        self.assertTrue(direct.isdisjoint(forbidden))
        hostile_getattrs = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "source"
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in forbidden
        ]
        self.assertEqual(hostile_getattrs, [])

    def test_only_source_read_is_acquired(self) -> None:
        names = [
            node.args[1].value
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "source"
            and isinstance(node.args[1], ast.Constant)
        ]
        self.assertEqual(names, ["read"])

    def test_no_unbounded_read_syntax(self) -> None:
        read_calls = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "read_method"
        ]
        self.assertEqual(len(read_calls), 1)
        self.assertEqual(len(read_calls[0].args), 1)
        self.assertEqual(read_calls[0].keywords, [])

    def test_observer_does_not_accumulate_chunks(self) -> None:
        targets = [
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"observe_original_media", "_observe_original_media_worker"}
        ]
        self.assertEqual(len(targets), 2)
        mutators = {"append", "extend", "insert", "join"}
        for target in targets:
            self.assertFalse(any(isinstance(node, (ast.List, ast.ListComp, ast.SetComp, ast.DictComp)) for node in ast.walk(target)))
            called_attributes = {
                node.func.attr
                for node in ast.walk(target)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            }
            self.assertTrue(called_attributes.isdisjoint(mutators))

    def test_no_bytesio_tempfile_mmap_or_payload_copy(self) -> None:
        names = {node.id for node in ast.walk(self.tree) if isinstance(node, ast.Name)}
        attributes = {node.attr for node in ast.walk(self.tree) if isinstance(node, ast.Attribute)}
        forbidden = {"BytesIO", "TemporaryFile", "NamedTemporaryFile", "mmap", "bytearray", "memoryview"}
        self.assertTrue(names.isdisjoint(forbidden - {"bytearray", "memoryview"}))
        self.assertTrue(attributes.isdisjoint(forbidden))

    def test_no_randomness_or_identifier_generation(self) -> None:
        forbidden = {"random", "token_hex", "token_urlsafe", "uuid4", "operation_id", "media_id"}
        names = {node.id for node in ast.walk(self.tree) if isinstance(node, ast.Name)}
        attributes = {node.attr for node in ast.walk(self.tree) if isinstance(node, ast.Attribute)}
        self.assertTrue(names.isdisjoint(forbidden))
        self.assertTrue(attributes.isdisjoint(forbidden))

    def test_no_phase3q_attestation_construction(self) -> None:
        called = {
            node.func.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue(
            called.isdisjoint(
                {
                    "validate_original_media_attestation",
                    "bind_attestation_for_phase3p",
                    "ValidatedOriginalMediaAttestation",
                }
            )
        )

    def test_public_signature_is_exact(self) -> None:
        target = next(
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "observe_original_media"
        )
        positional = tuple(arg.arg for arg in target.args.args)
        keyword_only = tuple(arg.arg for arg in target.args.kwonlyargs)
        self.assertEqual(positional, ("source",))
        self.assertEqual(keyword_only, ("byte_limit",))
        self.assertIsNone(target.args.vararg)
        self.assertIsNone(target.args.kwarg)

    def test_no_async_functions_yields_or_awaits(self) -> None:
        prohibited = (ast.AsyncFunctionDef, ast.Await, ast.Yield, ast.YieldFrom)
        self.assertFalse(any(isinstance(node, prohibited) for node in ast.walk(self.tree)))

    def test_no_baseexception_catch(self) -> None:
        caught_names = {
            handler.type.id
            for handler in ast.walk(self.tree)
            if isinstance(handler, ast.ExceptHandler)
            and isinstance(handler.type, ast.Name)
        }
        self.assertNotIn("BaseException", caught_names)

    def test_result_dataclass_has_exact_fields(self) -> None:
        self.assertEqual(tuple(item.name for item in fields(OriginalMediaObservation)), ("byte_size", "sha256"))

    def test_constants_support_normal_full_cap_reader(self) -> None:
        normal_calls = (MAX_ORIGINAL_MEDIA_BYTES + READ_CHUNK_BYTES - 1) // READ_CHUNK_BYTES + 1
        self.assertGreaterEqual(MAX_READ_CALLS, normal_calls)

    def test_source_compiles_without_execution(self) -> None:
        compile(self.source, str(PRODUCTION_PATH), "exec")

    def test_worktree_has_no_unapproved_changed_paths(self) -> None:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        changed = {line[3:] for line in completed.stdout.splitlines() if line}
        self.assertTrue(changed <= APPROVED_PATHS)
        self.assertTrue(all((ROOT / path).is_file() for path in APPROVED_PATHS))


if __name__ == "__main__":
    unittest.main()
