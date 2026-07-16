"""Dedicated synthetic tests for bounded verified MarketMatch STT input."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
import hashlib
import io
from pathlib import Path
import subprocess
import traceback
import unittest
from unittest import mock
import warnings

import src.marketmatch_stt_input as stt_input
from src.marketmatch_stt_input import (
    MAX_STT_INPUT_BYTES,
    MAX_STT_INPUT_READ_CALLS,
    STT_INPUT_CHUNK_BYTES,
    STTInputCode,
    STTInputError,
    VerifiedSTTInputSnapshot,
    snapshot_verified_stt_input,
)


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_PATH = ROOT / "src" / "marketmatch_stt_input.py"


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
        self.close_calls = 0

    def read(self, size: int) -> bytes:
        del size
        raise self.error

    def close(self) -> None:
        self.close_calls += 1


def _snapshot(
    content: bytes,
    *,
    reader: object | None = None,
    expected_size: int | None = None,
    expected_digest: str | None = None,
    limit: int | None = None,
) -> VerifiedSTTInputSnapshot:
    selected_reader = SyntheticReader(content) if reader is None else reader
    return snapshot_verified_stt_input(
        selected_reader,
        expected_byte_size=len(content) if expected_size is None else expected_size,
        expected_sha256=_sha(content) if expected_digest is None else expected_digest,
        byte_limit=max(1, len(content)) if limit is None else limit,
    )


def _assert_code(
    test: unittest.TestCase,
    code: STTInputCode,
    reader: object,
    *,
    expected_size: object,
    expected_digest: object,
    limit: object,
) -> STTInputError:
    with test.assertRaises(STTInputError) as caught:
        snapshot_verified_stt_input(
            reader,
            expected_byte_size=expected_size,  # type: ignore[arg-type]
            expected_sha256=expected_digest,  # type: ignore[arg-type]
            byte_limit=limit,  # type: ignore[arg-type]
        )
    test.assertIs(caught.exception.code, code)
    return caught.exception


class STTInputSuccessTests(unittest.TestCase):
    def test_valid_empty_stream(self) -> None:
        result = _snapshot(b"")
        self.assertEqual(result.payload_bytes, b"")
        self.assertEqual(result.byte_size, 0)

    def test_valid_small_stream(self) -> None:
        content = b"synthetic audio bytes"
        result = _snapshot(content)
        self.assertEqual(result.payload_bytes, content)

    def test_valid_exact_limit_stream(self) -> None:
        content = b"exact-limit"
        result = _snapshot(content, limit=len(content))
        self.assertEqual(result.byte_size, len(content))

    def test_multi_chunk_stream(self) -> None:
        content = b"a" * (STT_INPUT_CHUNK_BYTES * 2 + 17)
        reader = SyntheticReader(content)
        result = _snapshot(content, reader=reader, limit=len(content))
        self.assertEqual(result.payload_bytes, content)
        self.assertGreaterEqual(len(reader.requests), 4)

    def test_short_reads_before_eof(self) -> None:
        content = b"short reads are not eof"
        reader = SyntheticReader(content, max_read=3)
        result = _snapshot(content, reader=reader, limit=len(content))
        self.assertEqual(result.payload_bytes, content)
        self.assertGreater(len(reader.requests), 3)

    def test_one_byte_at_a_time_reader(self) -> None:
        content = b"one-byte"
        reader = SyntheticReader(content, max_read=1)
        result = _snapshot(content, reader=reader, limit=len(content))
        self.assertEqual(result.payload_bytes, content)
        self.assertEqual(reader.position, len(content))

    def test_eof_is_only_exact_empty_bytes(self) -> None:
        content = b"abc"
        reader = SyntheticReader(content, max_read=1)
        _snapshot(content, reader=reader, limit=3)
        self.assertEqual(len(reader.requests), 4)

    def test_expected_size_zero(self) -> None:
        reader = SyntheticReader(b"")
        result = snapshot_verified_stt_input(
            reader,
            expected_byte_size=0,
            expected_sha256=_sha(b""),
            byte_limit=1,
        )
        self.assertEqual(result.byte_size, 0)

    def test_valid_lowercase_sha256(self) -> None:
        content = b"lowercase"
        result = _snapshot(content)
        self.assertEqual(result.sha256, _sha(content))
        self.assertEqual(result.sha256, result.sha256.lower())

    def test_exact_size_reader_is_probed_for_eof(self) -> None:
        content = b"probe"
        reader = SyntheticReader(content)
        _snapshot(content, reader=reader, limit=len(content))
        self.assertEqual(reader.requests, [len(content) + 1, 1])

    def test_snapshot_bytes_are_exact_immutable_bytes(self) -> None:
        result = _snapshot(b"immutable")
        self.assertIs(type(result.payload_bytes), bytes)
        with self.assertRaises(TypeError):
            result.payload_bytes[0] = 0  # type: ignore[index]

    def test_snapshot_object_is_frozen_and_slotted(self) -> None:
        result = _snapshot(b"frozen")
        with self.assertRaises(FrozenInstanceError):
            result.byte_size = 1  # type: ignore[misc]
        self.assertFalse(hasattr(result, "__dict__"))

    def test_return_model_has_only_approved_fields(self) -> None:
        self.assertEqual(
            tuple(item.name for item in fields(VerifiedSTTInputSnapshot)),
            ("byte_size", "sha256", "payload_bytes"),
        )

    def test_payload_bytes_are_excluded_from_repr(self) -> None:
        content = b"REPR_PAYLOAD_PRIVACY_CANARY"
        result = _snapshot(content)
        self.assertNotIn("REPR_PAYLOAD_PRIVACY_CANARY", repr(result))
        self.assertNotIn(repr(content), repr(result))

    def test_expected_metadata_are_excluded_from_repr(self) -> None:
        content = b"metadata"
        result = _snapshot(content)
        rendered = repr(result)
        self.assertNotIn(str(len(content)), rendered)
        self.assertNotIn(_sha(content), rendered)

    def test_deterministic_result(self) -> None:
        content = b"deterministic"
        self.assertEqual(_snapshot(content), _snapshot(content))

    def test_same_bytes_produce_same_digest(self) -> None:
        content = b"same"
        self.assertEqual(_snapshot(content).sha256, _snapshot(bytes(content)).sha256)

    def test_one_byte_mutation_changes_digest(self) -> None:
        self.assertNotEqual(_sha(b"abc"), _sha(b"abd"))

    def test_mutable_reader_source_is_not_retained(self) -> None:
        source = bytearray(b"source")

        class MutableSourceReader:
            def __init__(self):
                self.done = False

            def read(self, size: int) -> bytes:
                del size
                if self.done:
                    return b""
                self.done = True
                return bytes(source)

        result = _snapshot(bytes(source), reader=MutableSourceReader())
        source[:] = b"change"
        self.assertEqual(result.payload_bytes, b"source")

    def test_reader_unchanged_except_natural_position(self) -> None:
        content = b"reader position"
        reader = SyntheticReader(content, max_read=2)
        before_content = reader.content
        before_limit = reader.max_read
        _snapshot(content, reader=reader, limit=len(content))
        self.assertIs(reader.content, before_content)
        self.assertEqual(reader.max_read, before_limit)
        self.assertEqual(reader.position, len(content))


class STTInputMetadataFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = b"metadata"
        self.digest = _sha(self.content)

    def test_negative_expected_size_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_EXPECTED_SIZE, SyntheticReader(self.content), expected_size=-1, expected_digest=self.digest, limit=10)

    def test_boolean_expected_size_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_EXPECTED_SIZE, SyntheticReader(self.content), expected_size=True, expected_digest=self.digest, limit=10)

    def test_float_expected_size_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_EXPECTED_SIZE, SyntheticReader(self.content), expected_size=8.0, expected_digest=self.digest, limit=10)

    def test_expected_size_above_byte_limit_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_EXPECTED_SIZE, SyntheticReader(self.content), expected_size=11, expected_digest=self.digest, limit=10)

    def test_zero_byte_limit_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_BYTE_LIMIT, SyntheticReader(self.content), expected_size=0, expected_digest=self.digest, limit=0)

    def test_negative_byte_limit_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_BYTE_LIMIT, SyntheticReader(self.content), expected_size=0, expected_digest=self.digest, limit=-1)

    def test_boolean_byte_limit_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_BYTE_LIMIT, SyntheticReader(self.content), expected_size=0, expected_digest=self.digest, limit=True)

    def test_float_byte_limit_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_BYTE_LIMIT, SyntheticReader(self.content), expected_size=0, expected_digest=self.digest, limit=10.0)

    def test_byte_limit_above_absolute_cap_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_BYTE_LIMIT, SyntheticReader(self.content), expected_size=0, expected_digest=self.digest, limit=MAX_STT_INPUT_BYTES + 1)

    def test_uppercase_sha256_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_EXPECTED_SHA256, SyntheticReader(self.content), expected_size=len(self.content), expected_digest=self.digest.upper(), limit=10)

    def test_short_digest_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_EXPECTED_SHA256, SyntheticReader(self.content), expected_size=len(self.content), expected_digest="a" * 63, limit=10)

    def test_prefixed_digest_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_EXPECTED_SHA256, SyntheticReader(self.content), expected_size=len(self.content), expected_digest="sha256:" + self.digest, limit=10)

    def test_nonhex_digest_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_EXPECTED_SHA256, SyntheticReader(self.content), expected_size=len(self.content), expected_digest="g" * 64, limit=10)

    def test_nonstring_digest_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_EXPECTED_SHA256, SyntheticReader(self.content), expected_size=len(self.content), expected_digest=123, limit=10)

    def test_metadata_is_validated_before_reader_inspection(self) -> None:
        class HostileReader:
            def __getattribute__(self, name):
                raise AssertionError("METADATA_ORDER_PRIVACY_CANARY")

        error = _assert_code(self, STTInputCode.INVALID_BYTE_LIMIT, HostileReader(), expected_size=0, expected_digest=self.digest, limit=0)
        self.assertNotIn("METADATA_ORDER_PRIVACY_CANARY", str(error) + repr(error))


class STTInputReadFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = b"abcdef"
        self.digest = _sha(self.content)

    def test_exact_size_mismatch_smaller_stream(self) -> None:
        _assert_code(self, STTInputCode.INPUT_SIZE_MISMATCH, SyntheticReader(b"abc"), expected_size=4, expected_digest=_sha(b"abc"), limit=4)

    def test_exact_size_mismatch_larger_stream(self) -> None:
        _assert_code(self, STTInputCode.INPUT_SIZE_MISMATCH, SyntheticReader(b"abc"), expected_size=2, expected_digest=_sha(b"ab"), limit=4)

    def test_digest_mismatch(self) -> None:
        _assert_code(self, STTInputCode.INPUT_DIGEST_MISMATCH, SyntheticReader(self.content), expected_size=len(self.content), expected_digest="0" * 64, limit=len(self.content))

    def test_limit_plus_one_rejected(self) -> None:
        reader = SyntheticReader(b"abcd")
        _assert_code(self, STTInputCode.INPUT_LIMIT_EXCEEDED, reader, expected_size=3, expected_digest=_sha(b"abc"), limit=3)
        self.assertEqual(reader.position, 4)

    def test_reader_returning_bytearray_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_READ_RESULT, FixedResultReader(bytearray(b"x")), expected_size=1, expected_digest=_sha(b"x"), limit=1)

    def test_reader_returning_memoryview_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_READ_RESULT, FixedResultReader(memoryview(b"x")), expected_size=1, expected_digest=_sha(b"x"), limit=1)

    def test_reader_returning_string_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_READ_RESULT, FixedResultReader("x"), expected_size=1, expected_digest=_sha(b"x"), limit=1)

    def test_reader_returning_none_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_READ_RESULT, FixedResultReader(None), expected_size=1, expected_digest=_sha(b"x"), limit=1)

    def test_reader_returning_integer_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_READ_RESULT, FixedResultReader(1), expected_size=1, expected_digest=_sha(b"x"), limit=1)

    def test_reader_returning_bytes_subclass_rejected_without_coercion(self) -> None:
        class HostileBytes(bytes):
            def __bytes__(self):
                raise AssertionError("BYTES_SUBCLASS_PRIVACY_CANARY")

            def __repr__(self):
                raise AssertionError("BYTES_SUBCLASS_PRIVACY_CANARY")

        error = _assert_code(self, STTInputCode.INVALID_READ_RESULT, FixedResultReader(HostileBytes(b"x")), expected_size=1, expected_digest=_sha(b"x"), limit=1)
        self.assertNotIn("BYTES_SUBCLASS_PRIVACY_CANARY", str(error) + repr(error))

    def test_reader_returning_hostile_custom_object_is_not_coerced(self) -> None:
        class HostileObject:
            def __bytes__(self):
                raise AssertionError("HOSTILE_RETURN_PRIVACY_CANARY")

            def __repr__(self):
                raise AssertionError("HOSTILE_RETURN_PRIVACY_CANARY")

        error = _assert_code(self, STTInputCode.INVALID_READ_RESULT, FixedResultReader(HostileObject()), expected_size=1, expected_digest=_sha(b"x"), limit=1)
        self.assertNotIn("HOSTILE_RETURN_PRIVACY_CANARY", str(error) + repr(error))

    def test_reader_returning_more_than_requested_rejected(self) -> None:
        class OverReturningReader:
            def read(self, size: int) -> bytes:
                return b"x" * (size + 1)

        _assert_code(self, STTInputCode.INVALID_READ_RESULT, OverReturningReader(), expected_size=1, expected_digest=_sha(b"x"), limit=1)

    def test_tiny_read_call_limit_prevents_chunk_metadata_dos(self) -> None:
        class OneByteForeverReader:
            def __init__(self):
                self.calls = 0
                self.requests: list[int] = []

            def read(self, size: int) -> bytes:
                self.calls += 1
                self.requests.append(size)
                return b"x"

        reader = OneByteForeverReader()
        _assert_code(
            self,
            STTInputCode.INPUT_READ_LIMIT_EXCEEDED,
            reader,
            expected_size=MAX_STT_INPUT_BYTES,
            expected_digest="0" * 64,
            limit=MAX_STT_INPUT_BYTES,
        )
        self.assertEqual(reader.calls, MAX_STT_INPUT_READ_CALLS)
        self.assertEqual(len(reader.requests), MAX_STT_INPUT_READ_CALLS)
        self.assertTrue(all(0 < size <= STT_INPUT_CHUNK_BYTES for size in reader.requests))

    def test_reader_raising_normal_exception(self) -> None:
        _assert_code(self, STTInputCode.READER_FAILED, RaisingReader(ValueError("normal failure")), expected_size=1, expected_digest=_sha(b"x"), limit=1)

    def test_reader_raising_privacy_canary_exception(self) -> None:
        error = _assert_code(self, STTInputCode.READER_FAILED, RaisingReader(ValueError("READER_EXCEPTION_PRIVACY_CANARY")), expected_size=1, expected_digest=_sha(b"x"), limit=1)
        self.assertNotIn("READER_EXCEPTION_PRIVACY_CANARY", str(error) + repr(error))

    def test_reader_raising_baseexception_subclass(self) -> None:
        class HostileBaseException(BaseException):
            pass

        error = _assert_code(self, STTInputCode.READER_FAILED, RaisingReader(HostileBaseException("BASE_EXCEPTION_PRIVACY_CANARY")), expected_size=1, expected_digest=_sha(b"x"), limit=1)
        self.assertNotIn("BASE_EXCEPTION_PRIVACY_CANARY", str(error) + repr(error))

    def test_reader_exception_chain_is_absent(self) -> None:
        error = _assert_code(self, STTInputCode.READER_FAILED, RaisingReader(ValueError("CHAIN_PRIVACY_CANARY")), expected_size=1, expected_digest=_sha(b"x"), limit=1)
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)

    def test_partial_bytes_are_not_exposed_on_failure(self) -> None:
        class PartialThenFailure:
            def __init__(self):
                self.calls = 0

            def read(self, size: int) -> bytes:
                del size
                self.calls += 1
                if self.calls == 1:
                    return b"partial"
                raise ValueError("failure")

        error = _assert_code(self, STTInputCode.READER_FAILED, PartialThenFailure(), expected_size=8, expected_digest=_sha(b"partial!"), limit=8)
        self.assertFalse(hasattr(error, "payload_bytes"))
        self.assertNotIn("partial", str(error) + repr(error))

    def test_between_chunk_mutation_fails_digest(self) -> None:
        original = b"abcdef"

        class MutatingReader:
            def __init__(self):
                self.chunks = iter((b"abc", b"deg", b""))

            def read(self, size: int) -> bytes:
                del size
                return next(self.chunks)

        _assert_code(self, STTInputCode.INPUT_DIGEST_MISMATCH, MutatingReader(), expected_size=len(original), expected_digest=_sha(original), limit=len(original))

    def test_size_mismatch_prevents_digest_comparison(self) -> None:
        with mock.patch.object(stt_input.hmac, "compare_digest") as compare:
            _assert_code(self, STTInputCode.INPUT_SIZE_MISMATCH, SyntheticReader(b"abc"), expected_size=4, expected_digest=_sha(b"abc"), limit=4)
        compare.assert_not_called()

    def test_digest_comparison_must_pass_before_success(self) -> None:
        with mock.patch.object(stt_input.hmac, "compare_digest", return_value=False) as compare:
            _assert_code(self, STTInputCode.INPUT_DIGEST_MISMATCH, SyntheticReader(self.content), expected_size=len(self.content), expected_digest=self.digest, limit=len(self.content))
        compare.assert_called_once()


class STTInputReaderBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = b"borrowed reader"

    def test_reader_is_not_closed_on_success(self) -> None:
        reader = SyntheticReader(self.content)
        _snapshot(self.content, reader=reader, limit=len(self.content))
        self.assertEqual(reader.close_calls, 0)

    def test_reader_is_not_closed_on_failure(self) -> None:
        reader = RaisingReader(ValueError("failure"))
        _assert_code(self, STTInputCode.READER_FAILED, reader, expected_size=1, expected_digest=_sha(b"x"), limit=1)
        self.assertEqual(reader.close_calls, 0)

    def test_reader_close_method_is_never_called(self) -> None:
        _snapshot(self.content, reader=GuardedReader(self.content), limit=len(self.content))

    def test_reader_name_is_never_inspected(self) -> None:
        _snapshot(self.content, reader=GuardedReader(self.content), limit=len(self.content))

    def test_reader_path_is_never_inspected(self) -> None:
        _snapshot(self.content, reader=GuardedReader(self.content), limit=len(self.content))

    def test_reader_mode_is_never_inspected(self) -> None:
        _snapshot(self.content, reader=GuardedReader(self.content), limit=len(self.content))

    def test_reader_fileno_is_never_inspected(self) -> None:
        _snapshot(self.content, reader=GuardedReader(self.content), limit=len(self.content))

    def test_reader_seek_is_never_called(self) -> None:
        _snapshot(self.content, reader=GuardedReader(self.content), limit=len(self.content))

    def test_reader_tell_is_never_called(self) -> None:
        _snapshot(self.content, reader=GuardedReader(self.content), limit=len(self.content))

    def test_no_unbounded_read_call(self) -> None:
        reader = SyntheticReader(self.content, max_read=2)
        _snapshot(self.content, reader=reader, limit=len(self.content))
        self.assertTrue(reader.requests)
        self.assertTrue(all(type(size) is int and size > 0 for size in reader.requests))

    def test_every_read_request_is_chunk_bounded(self) -> None:
        content = b"x" * (STT_INPUT_CHUNK_BYTES + 10)
        reader = SyntheticReader(content)
        _snapshot(content, reader=reader, limit=len(content))
        self.assertTrue(all(size <= STT_INPUT_CHUNK_BYTES for size in reader.requests))

    def test_cumulative_observed_bytes_never_exceed_limit_plus_one(self) -> None:
        reader = SyntheticReader(b"abcd", max_read=1)
        _assert_code(self, STTInputCode.INPUT_LIMIT_EXCEEDED, reader, expected_size=3, expected_digest=_sha(b"abc"), limit=3)
        self.assertLessEqual(reader.position, 4)

    def test_string_path_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_READER, "/private/path/canary", expected_size=0, expected_digest=_sha(b""), limit=1)

    def test_pathlib_path_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_READER, Path("synthetic.media"), expected_size=0, expected_digest=_sha(b""), limit=1)

    def test_raw_descriptor_integer_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_READER, 7, expected_size=0, expected_digest=_sha(b""), limit=1)

    def test_callback_rejected_without_invocation(self) -> None:
        called = False

        def callback():
            nonlocal called
            called = True

        _assert_code(self, STTInputCode.INVALID_READER, callback, expected_size=0, expected_digest=_sha(b""), limit=1)
        self.assertFalse(called)

    def test_callable_reader_factory_rejected_without_invocation(self) -> None:
        class FactoryReader(SyntheticReader):
            def __call__(self):
                raise AssertionError("FACTORY_PRIVACY_CANARY")

        _assert_code(self, STTInputCode.INVALID_READER, FactoryReader(b""), expected_size=0, expected_digest=_sha(b""), limit=1)

    def test_generator_rejected(self) -> None:
        generator = (item for item in (b"x",))
        _assert_code(self, STTInputCode.INVALID_READER, generator, expected_size=0, expected_digest=_sha(b""), limit=1)

    def test_iterator_without_read_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_READER, iter((b"x",)), expected_size=0, expected_digest=_sha(b""), limit=1)

    def test_async_reader_rejected_without_calling(self) -> None:
        class AsyncReader:
            def __init__(self):
                self.called = False

            async def read(self, size: int) -> bytes:
                del size
                self.called = True
                return b""

        reader = AsyncReader()
        _assert_code(self, STTInputCode.INVALID_READER, reader, expected_size=0, expected_digest=_sha(b""), limit=1)
        self.assertFalse(reader.called)

    def test_async_callable_read_object_rejected_without_warning_or_call(self) -> None:
        class AsyncReadCallable:
            def __init__(self):
                self.calls = 0

            async def __call__(self, size: int) -> bytes:
                del size
                self.calls += 1
                return b""

        class Reader:
            def __init__(self):
                self.read = AsyncReadCallable()

        reader = Reader()
        stderr = io.StringIO()
        with warnings.catch_warnings(record=True) as caught, mock.patch("sys.stderr", stderr):
            warnings.simplefilter("always")
            _assert_code(self, STTInputCode.INVALID_READER, reader, expected_size=0, expected_digest=_sha(b""), limit=1)
        self.assertEqual(reader.read.calls, 0)
        self.assertEqual(caught, [])
        self.assertEqual(stderr.getvalue(), "")

    def test_missing_read_method_rejected(self) -> None:
        _assert_code(self, STTInputCode.INVALID_READER, object(), expected_size=0, expected_digest=_sha(b""), limit=1)

    def test_noncallable_read_member_rejected(self) -> None:
        class NonCallableRead:
            read = b"not callable"

        _assert_code(self, STTInputCode.INVALID_READER, NonCallableRead(), expected_size=0, expected_digest=_sha(b""), limit=1)

    def test_hostile_read_property_does_not_leak(self) -> None:
        class HostileReadProperty:
            @property
            def read(self):
                raise RuntimeError("READ_PROPERTY_PRIVACY_CANARY")

        error = _assert_code(self, STTInputCode.INVALID_READER, HostileReadProperty(), expected_size=0, expected_digest=_sha(b""), limit=1)
        self.assertNotIn("READ_PROPERTY_PRIVACY_CANARY", str(error) + repr(error))


class STTInputPrivacyTests(unittest.TestCase):
    def test_error_string_and_repr_are_fixed(self) -> None:
        canary = "ERROR_PRIVACY_CANARY"
        error = _assert_code(self, STTInputCode.READER_FAILED, RaisingReader(ValueError(canary)), expected_size=1, expected_digest=_sha(b"x"), limit=1)
        self.assertEqual(str(error), STTInputCode.READER_FAILED.value)
        self.assertNotIn(canary, str(error) + repr(error))

    def test_uncaught_traceback_does_not_echo_reader_exception(self) -> None:
        canary = "TRACEBACK_PRIVACY_CANARY"
        try:
            snapshot_verified_stt_input(RaisingReader(ValueError(canary)), expected_byte_size=1, expected_sha256=_sha(b"x"), byte_limit=1)
        except STTInputError as error:
            rendered = "".join(traceback.TracebackException.from_exception(error).format())
        else:  # pragma: no cover
            self.fail("snapshot unexpectedly succeeded")
        self.assertNotIn(canary, rendered)

    def test_stdout_and_stderr_remain_empty_on_success(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            _snapshot(b"quiet")
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_stdout_and_stderr_remain_empty_on_failure(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            _assert_code(self, STTInputCode.READER_FAILED, RaisingReader(ValueError("STREAM_PRIVACY_CANARY")), expected_size=1, expected_digest=_sha(b"x"), limit=1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_reader_repr_is_never_used(self) -> None:
        class HostileReprReader(SyntheticReader):
            def __repr__(self):
                raise AssertionError("READER_REPR_PRIVACY_CANARY")

        _snapshot(b"safe", reader=HostileReprReader(b"safe"), limit=4)

    def test_failure_does_not_retain_reader(self) -> None:
        reader = RaisingReader(ValueError("failure"))
        error = _assert_code(self, STTInputCode.READER_FAILED, reader, expected_size=1, expected_digest=_sha(b"x"), limit=1)
        self.assertFalse(hasattr(error, "reader"))


class STTInputStaticPurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = PRODUCTION_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_standard_library_only_imports(self) -> None:
        allowed = {"__future__", "dataclasses", "enum", "hashlib", "hmac", "inspect", "re", "typing"}
        roots = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        self.assertLessEqual(roots, allowed)

    def test_no_prohibited_imports(self) -> None:
        prohibited = {
            "os", "pathlib", "tempfile", "shutil", "socket", "subprocess",
            "logging", "time", "datetime", "random", "secrets", "uuid",
            "sqlalchemy", "numpy", "torch", "av", "faster_whisper", "chromadb",
        }
        roots = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        self.assertTrue(roots.isdisjoint(prohibited))

    def test_no_application_imports(self) -> None:
        modules = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        self.assertFalse(any(name.startswith(("src.", "routes.", "core.")) for name in modules))

    def test_no_open_print_dynamic_execution_or_logging_calls(self) -> None:
        forbidden = {"open", "print", "exec", "eval", "compile", "__import__"}
        calls = {
            node.func.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue(calls.isdisjoint(forbidden))

    def test_no_filesystem_environment_network_process_or_time_attributes(self) -> None:
        forbidden = {
            "environ", "getenv", "getcwd", "open", "unlink", "remove", "mkdir",
            "makedirs", "connect", "send", "recv", "Popen", "run", "system",
            "now", "today", "time", "sleep", "mmap",
        }
        attributes = {node.attr for node in ast.walk(self.tree) if isinstance(node, ast.Attribute)}
        self.assertTrue(attributes.isdisjoint(forbidden))

    def test_no_reader_path_descriptor_or_lifecycle_inspection(self) -> None:
        forbidden = {"name", "path", "fileno", "seek", "tell", "close", "mode"}
        attributes = {node.attr for node in ast.walk(self.tree) if isinstance(node, ast.Attribute)}
        self.assertTrue(attributes.isdisjoint(forbidden))
        forbidden_getattrs = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in forbidden
        ]
        self.assertEqual(forbidden_getattrs, [])

    def test_reader_getattr_is_limited_to_read(self) -> None:
        getter_names = [
            node.args[1].value
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
        ]
        self.assertEqual(getter_names, ["read"])

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

    def test_no_bytesio_temporary_or_mmap_usage(self) -> None:
        called_names = {
            node.func.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        called_attributes = {
            node.func.attr
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertTrue(called_names.isdisjoint({"BytesIO", "TemporaryFile", "NamedTemporaryFile", "mmap"}))
        self.assertTrue(called_attributes.isdisjoint({"BytesIO", "TemporaryFile", "NamedTemporaryFile", "mmap"}))

    def test_no_decoder_model_stt_ffmpeg_or_rag_imports(self) -> None:
        modules = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.lower() for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module.lower())
        prohibited_tokens = ("whisper", "ffmpeg", "av", "numpy", "torch", "chroma", "rag", "worker", "upload")
        self.assertFalse(any(any(token in name for token in prohibited_tokens) for name in modules))

    def test_no_cancellation_callback_surface(self) -> None:
        target = next(
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "snapshot_verified_stt_input"
        )
        names = tuple(arg.arg for arg in target.args.args + target.args.kwonlyargs)
        self.assertEqual(names, ("reader", "expected_byte_size", "expected_sha256", "byte_limit"))

    def test_sha256_and_constant_time_comparison_present(self) -> None:
        calls = {
            node.func.attr
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertIn("sha256", calls)
        self.assertIn("compare_digest", calls)

    def test_fixed_chunk_and_absolute_limit_are_exact(self) -> None:
        self.assertEqual(MAX_STT_INPUT_BYTES, 104_857_600)
        self.assertEqual(STT_INPUT_CHUNK_BYTES, 65_536)
        self.assertEqual(MAX_STT_INPUT_READ_CALLS, 4_096)
        normal_full_input_calls = (MAX_STT_INPUT_BYTES + STT_INPUT_CHUNK_BYTES - 1) // STT_INPUT_CHUNK_BYTES + 1
        self.assertGreaterEqual(MAX_STT_INPUT_READ_CALLS, normal_full_input_calls)
        self.assertLess(MAX_STT_INPUT_BYTES, 128 * 1024 * 1024)

    def test_source_compiles_without_execution(self) -> None:
        compile(self.source, str(PRODUCTION_PATH), "exec")

    def test_exactly_two_approved_files_changed(self) -> None:
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
            {"src/marketmatch_stt_input.py", "tests/test_marketmatch_stt_input.py"},
        )


if __name__ == "__main__":
    unittest.main()
