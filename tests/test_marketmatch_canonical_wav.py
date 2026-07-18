"""Adversarial tests for the canonical in-memory MarketMatch WAV boundary."""

from __future__ import annotations

import ast
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError, fields
import gc
import io
import math
from pathlib import Path
import struct
import traceback
import unittest
import warnings

import numpy as np

from src.marketmatch_canonical_wav import (
    MAX_DURATION_MS,
    MAX_SEGMENTS,
    MAX_TRANSCRIPT_UTF8_BYTES,
    MAX_WAV_BYTES,
    SAMPLE_RATE,
    SEGMENT_END_TOLERANCE_MS,
    CanonicalTranscript,
    CanonicalTranscriptSegment,
    CanonicalWaveform,
    CanonicalWavCode,
    CanonicalWavError,
    decode_canonical_wav,
    transcribe_canonical_wav,
)


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_PATH = ROOT / "src" / "marketmatch_canonical_wav.py"


def _chunk(chunk_id: bytes, payload: bytes, *, pad: bytes = b"\x00") -> bytes:
    encoded = chunk_id + struct.pack("<I", len(payload)) + payload
    if len(payload) & 1:
        encoded += pad
    return encoded


def _riff(*chunks: bytes, declared_delta: int = 0, trailing: bytes = b"") -> bytes:
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body) + declared_delta) + body + trailing


def _fmt(
    *,
    format_code: int = 1,
    channels: int = 1,
    sample_rate: int = 16_000,
    byte_rate: int = 32_000,
    block_align: int = 2,
    bits_per_sample: int = 16,
    extension: bytes = b"",
) -> bytes:
    payload = struct.pack(
        "<HHIIHH",
        format_code,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
    ) + extension
    return _chunk(b"fmt ", payload)


def _wav_from_pcm(pcm: bytes, *, fmt: bytes | None = None) -> bytes:
    return _riff(_fmt() if fmt is None else fmt, _chunk(b"data", pcm))


def _wav_from_samples(samples: tuple[int, ...] | list[int]) -> bytes:
    return _wav_from_pcm(struct.pack(f"<{len(samples)}h", *samples))


def _assert_code(
    test: unittest.TestCase,
    code: CanonicalWavCode,
    function,
    *args,
    **kwargs,
) -> CanonicalWavError:
    with test.assertRaises(CanonicalWavError) as caught:
        function(*args, **kwargs)
    test.assertIs(caught.exception.code, code)
    return caught.exception


def _decode(content: bytes, *, duration_limit_ms: int = MAX_DURATION_MS) -> CanonicalWaveform:
    return decode_canonical_wav(
        content,
        byte_limit=len(content),
        duration_limit_ms=duration_limit_ms,
    )


class CanonicalWavStructureTests(unittest.TestCase):
    def test_empty_data_wav_is_rejected(self) -> None:
        _assert_code(self, CanonicalWavCode.INVALID_WAV, _decode, _wav_from_pcm(b""))

    def test_valid_one_sample(self) -> None:
        result = _decode(_wav_from_samples([1]))
        self.assertEqual(result.sample_count, 1)
        self.assertEqual(result.duration_ms, 1)

    def test_valid_one_second(self) -> None:
        result = _decode(_wav_from_pcm(b"\x00\x00" * SAMPLE_RATE))
        self.assertEqual(result.sample_count, SAMPLE_RATE)
        self.assertEqual(result.duration_ms, 1_000)

    def test_valid_exact_duration_limit(self) -> None:
        samples = SAMPLE_RATE * 2
        content = _wav_from_pcm(b"\x00\x00" * samples)
        result = _decode(content, duration_limit_ms=2_000)
        self.assertEqual(result.duration_ms, 2_000)

    def test_ten_minute_absolute_duration_limit(self) -> None:
        samples = SAMPLE_RATE * 600
        content = _wav_from_pcm(b"\x00\x00" * samples)
        result = _decode(content)
        self.assertEqual(result.duration_ms, MAX_DURATION_MS)
        self.assertLess(len(content), MAX_WAV_BYTES)

    def test_duration_limit_plus_one_sample(self) -> None:
        content = _wav_from_pcm(b"\x00\x00" * (SAMPLE_RATE + 1))
        _assert_code(
            self,
            CanonicalWavCode.DURATION_LIMIT_EXCEEDED,
            _decode,
            content,
            duration_limit_ms=1_000,
        )

    def test_exact_byte_limit(self) -> None:
        content = _wav_from_samples([1, 2, 3])
        result = decode_canonical_wav(
            content,
            byte_limit=len(content),
            duration_limit_ms=1,
        )
        self.assertEqual(result.sample_count, 3)

    def test_byte_limit_plus_one(self) -> None:
        content = _wav_from_samples([1])
        _assert_code(
            self,
            CanonicalWavCode.INPUT_LIMIT_EXCEEDED,
            decode_canonical_wav,
            content,
            byte_limit=len(content) - 1,
            duration_limit_ms=1,
        )

    def test_absolute_byte_ceiling(self) -> None:
        content = b"x" * (MAX_WAV_BYTES + 1)
        _assert_code(
            self,
            CanonicalWavCode.INPUT_LIMIT_EXCEEDED,
            decode_canonical_wav,
            content,
            byte_limit=MAX_WAV_BYTES,
            duration_limit_ms=1,
        )

    def test_invalid_limit_types_and_ranges(self) -> None:
        content = _wav_from_samples([1])
        for value in (True, -1, MAX_WAV_BYTES + 1, 1.5, "46"):
            with self.subTest(byte_limit=value):
                _assert_code(
                    self,
                    CanonicalWavCode.INVALID_INPUT,
                    decode_canonical_wav,
                    content,
                    byte_limit=value,
                    duration_limit_ms=1,
                )
        for value in (True, 0, -1, MAX_DURATION_MS + 1, 1.5, "1"):
            with self.subTest(duration_limit=value):
                _assert_code(
                    self,
                    CanonicalWavCode.INVALID_INPUT,
                    decode_canonical_wav,
                    content,
                    byte_limit=len(content),
                    duration_limit_ms=value,
                )

    def test_only_exact_immutable_bytes_are_accepted(self) -> None:
        content = _wav_from_samples([1])
        for value in (bytearray(content), memoryview(content), "RIFF", None):
            with self.subTest(kind=type(value).__name__):
                _assert_code(
                    self,
                    CanonicalWavCode.INVALID_INPUT,
                    decode_canonical_wav,
                    value,
                    byte_limit=len(content),
                    duration_limit_ms=1,
                )

    def test_truncated_riff_header(self) -> None:
        for content in (b"", b"RIFF", b"RIFF\x00\x00\x00\x00WAV"):
            with self.subTest(length=len(content)):
                _assert_code(
                    self,
                    CanonicalWavCode.INVALID_WAV,
                    decode_canonical_wav,
                    content,
                    byte_limit=100,
                    duration_limit_ms=1,
                )

    def test_wrong_riff_or_wave_identifier(self) -> None:
        valid = _wav_from_samples([1])
        for content in (b"RIFX" + valid[4:], valid[:8] + b"AVI " + valid[12:]):
            _assert_code(self, CanonicalWavCode.INVALID_WAV, _decode, content)

    def test_incorrect_riff_declared_size(self) -> None:
        for delta in (-1, 1):
            content = _riff(_fmt(), _chunk(b"data", b"\x00\x00"), declared_delta=delta)
            _assert_code(self, CanonicalWavCode.INVALID_WAV, _decode, content)

    def test_missing_fmt_or_data(self) -> None:
        for content in (_riff(_chunk(b"data", b"\x00\x00")), _riff(_fmt())):
            _assert_code(self, CanonicalWavCode.INVALID_WAV, _decode, content)

    def test_multiple_fmt_or_data_chunks(self) -> None:
        contents = (
            _riff(_fmt(), _fmt(), _chunk(b"data", b"\x00\x00")),
            _riff(_fmt(), _chunk(b"data", b"\x00\x00"), _chunk(b"data", b"\x00\x00")),
        )
        for content in contents:
            _assert_code(self, CanonicalWavCode.INVALID_WAV, _decode, content)

    def test_data_before_fmt_is_rejected(self) -> None:
        content = _riff(_chunk(b"data", b"\x00\x00"), _fmt())
        _assert_code(self, CanonicalWavCode.INVALID_WAV, _decode, content)

    def test_extra_chunks_are_rejected(self) -> None:
        for chunk_id in (b"LIST", b"JUNK", b"cue ", b"bext"):
            content = _riff(_fmt(), _chunk(chunk_id, b"xx"), _chunk(b"data", b"\x00\x00"))
            _assert_code(self, CanonicalWavCode.UNSUPPORTED_WAV_FORMAT, _decode, content)

    def test_valid_padding_on_unsupported_odd_chunk_is_parsed_then_rejected(self) -> None:
        content = _riff(_fmt(), _chunk(b"JUNK", b"x"), _chunk(b"data", b"\x00\x00"))
        _assert_code(self, CanonicalWavCode.UNSUPPORTED_WAV_FORMAT, _decode, content)

    def test_exact_two_chunk_unknown_identifier_is_unsupported(self) -> None:
        for content in (
            _riff(_fmt(), _chunk(b"JUNK", b"xx")),
            _riff(_chunk(b"JUNK", b"x" * 16), _chunk(b"data", b"\x00\x00")),
        ):
            _assert_code(self, CanonicalWavCode.UNSUPPORTED_WAV_FORMAT, _decode, content)

    def test_malformed_chunk_padding(self) -> None:
        content = _riff(_fmt(), _chunk(b"JUNK", b"x", pad=b"!"), _chunk(b"data", b"\x00\x00"))
        _assert_code(self, CanonicalWavCode.INVALID_WAV, _decode, content)

    def test_odd_data_length_is_rejected(self) -> None:
        content = _riff(_fmt(), _chunk(b"data", b"x"))
        _assert_code(self, CanonicalWavCode.INVALID_WAV, _decode, content)

    def test_format_code_and_fmt_extension_rejected(self) -> None:
        for fmt in (_fmt(format_code=3), _fmt(extension=b"\x00\x00")):
            _assert_code(
                self,
                CanonicalWavCode.UNSUPPORTED_WAV_FORMAT,
                _decode,
                _wav_from_pcm(b"\x00\x00", fmt=fmt),
            )

    def test_noncanonical_format_fields_rejected(self) -> None:
        formats = (
            _fmt(channels=2),
            _fmt(sample_rate=8_000),
            _fmt(bits_per_sample=8),
            _fmt(bits_per_sample=24, block_align=3, byte_rate=48_000),
            _fmt(bits_per_sample=32, block_align=4, byte_rate=64_000),
            _fmt(block_align=4),
            _fmt(byte_rate=16_000),
        )
        for fmt in formats:
            with self.subTest(fmt=fmt):
                _assert_code(
                    self,
                    CanonicalWavCode.UNSUPPORTED_WAV_FORMAT,
                    _decode,
                    _wav_from_pcm(b"\x00\x00", fmt=fmt),
                )

    def test_declared_chunk_length_overflow(self) -> None:
        body = b"WAVE" + b"fmt " + struct.pack("<I", 0xFFFFFFFF)
        content = b"RIFF" + struct.pack("<I", len(body)) + body
        _assert_code(self, CanonicalWavCode.INVALID_WAV, _decode, content)

    def test_truncated_chunk_payload(self) -> None:
        content = _wav_from_samples([1])[:-1]
        content = content[:4] + struct.pack("<I", len(content) - 8) + content[8:]
        _assert_code(self, CanonicalWavCode.INVALID_WAV, _decode, content)

    def test_trailing_bytes_rejected(self) -> None:
        valid = _wav_from_samples([1])
        _assert_code(self, CanonicalWavCode.INVALID_WAV, _decode, valid + b"x")
        body_extended = valid[:4] + struct.pack("<I", len(valid) - 8 + 1) + valid[8:] + b"x"
        _assert_code(self, CanonicalWavCode.INVALID_WAV, _decode, body_extended)


class WaveformConversionTests(unittest.TestCase):
    def test_shape_dtype_contiguity_and_normalization(self) -> None:
        result = _decode(_wav_from_samples([-32768, -16384, 0, 16384, 32767]))
        self.assertEqual(result.waveform.shape, (5,))
        self.assertEqual(result.waveform.dtype, np.dtype(np.float32))
        self.assertTrue(result.waveform.flags.c_contiguous)
        np.testing.assert_array_equal(
            result.waveform,
            np.array([-1.0, -0.5, 0.0, 0.5, 32767 / 32768], dtype=np.float32),
        )

    def test_waveform_is_backed_by_immutable_bytes(self) -> None:
        result = _decode(_wav_from_samples([1, 2]))
        self.assertFalse(result.waveform.flags.writeable)
        self.assertIs(type(result.waveform.base), bytes)
        with self.assertRaises(ValueError):
            result.waveform.setflags(write=True)
        with self.assertRaises(ValueError):
            result.waveform[0] = 0.0

    def test_caller_mutation_cannot_affect_waveform(self) -> None:
        mutable = bytearray(_wav_from_samples([100, 200]))
        immutable = bytes(mutable)
        result = _decode(immutable)
        mutable[-2:] = struct.pack("<h", -300)
        np.testing.assert_array_equal(
            result.waveform,
            np.array([100 / 32768, 200 / 32768], dtype=np.float32),
        )
        self.assertIsNot(result.waveform.base, immutable)

    def test_waveform_model_is_frozen_and_nonidentifying(self) -> None:
        result = _decode(_wav_from_samples([1]))
        with self.assertRaises(FrozenInstanceError):
            result.duration_ms = 9  # type: ignore[misc]
        self.assertEqual(repr(result), "CanonicalWaveform(<validated>)")
        self.assertEqual(
            tuple(item.name for item in fields(result)),
            ("sample_count", "duration_ms", "waveform"),
        )

    def test_conversion_does_not_retain_pcm_view(self) -> None:
        content = _wav_from_samples([1, 2, 3])
        result = _decode(content)
        self.assertIs(type(result.waveform.base), bytes)
        self.assertNotIn(content, (result.waveform.base,))


class BackendBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wav_bytes = _wav_from_pcm(b"\x00\x00" * SAMPLE_RATE)
        self.waveform = _decode(self.wav_bytes)

    def transcribe(self, backend, **limits) -> CanonicalTranscript:
        return transcribe_canonical_wav(
            self.wav_bytes,
            byte_limit=len(self.wav_bytes),
            duration_limit_ms=1_000,
            backend=backend,
            transcript_utf8_limit=limits.get("transcript_utf8_limit", MAX_TRANSCRIPT_UTF8_BYTES),
            segment_limit=limits.get("segment_limit", MAX_SEGMENTS),
        )

    def test_backend_receives_only_numpy_waveform(self) -> None:
        captured = []

        def backend(value):
            captured.append(value)
            return []

        result = self.transcribe(backend)
        self.assertEqual(len(captured), 1)
        self.assertIs(type(captured[0]), np.ndarray)
        self.assertFalse(captured[0].flags.writeable)
        np.testing.assert_array_equal(captured[0], self.waveform.waveform)
        self.assertEqual(result.transcript_text, "")

    def test_valid_lazy_segment_iterator(self) -> None:
        def backend(_waveform):
            def segments():
                yield (0.0, 0.4, "hello")
                yield (0.4, 1.0, " world")
            return segments()

        result = self.transcribe(backend)
        self.assertEqual(result.duration_ms, 1_000)
        self.assertEqual(result.transcript_text, "hello world")
        self.assertEqual(
            result.segments,
            (
                CanonicalTranscriptSegment(0, 400, "hello"),
                CanonicalTranscriptSegment(400, 1_000, " world"),
            ),
        )

    def test_generator_exception_is_private_backend_failure(self) -> None:
        canary = "PRIVATE_BACKEND_GENERATOR_CANARY"

        def backend(_waveform):
            yield (0.0, 0.1, "ok")
            raise ValueError(canary)

        error = _assert_code(self, CanonicalWavCode.BACKEND_FAILED, self.transcribe, backend)
        self.assertNotIn(canary, str(error))
        self.assertIsNone(error.__context__)

    def test_infinite_iterator_is_bounded(self) -> None:
        calls = 0

        def backend(_waveform):
            nonlocal calls
            while True:
                calls += 1
                yield (0.0, 0.0, "")

        _assert_code(
            self,
            CanonicalWavCode.SEGMENT_LIMIT_EXCEEDED,
            self.transcribe,
            backend,
            segment_limit=3,
        )
        self.assertEqual(calls, 4)

    def test_exact_segment_limit_succeeds(self) -> None:
        result = self.transcribe(
            lambda _: iter(((0.0, 0.0, "a"), (0.0, 0.0, "b"))),
            segment_limit=2,
        )
        self.assertEqual(len(result.segments), 2)

    def test_exact_transcript_limit_and_plus_one(self) -> None:
        result = self.transcribe(lambda _: [(0.0, 0.1, "é")], transcript_utf8_limit=2)
        self.assertEqual(result.transcript_text, "é")
        _assert_code(
            self,
            CanonicalWavCode.TRANSCRIPT_LIMIT_EXCEEDED,
            self.transcribe,
            lambda _: [(0.0, 0.1, "é")],
            transcript_utf8_limit=1,
        )

    def test_oversized_text_is_rejected_before_utf8_encoding(self) -> None:
        _assert_code(
            self,
            CanonicalWavCode.TRANSCRIPT_LIMIT_EXCEEDED,
            self.transcribe,
            lambda _: [(0.0, 0.1, "\ud800\ud800")],
            transcript_utf8_limit=1,
        )

    def test_invalid_transcription_limits(self) -> None:
        for name, values in (
            ("segment_limit", (True, -1, MAX_SEGMENTS + 1, 1.5, "1")),
            (
                "transcript_utf8_limit",
                (True, -1, MAX_TRANSCRIPT_UTF8_BYTES + 1, 1.5, "1"),
            ),
        ):
            for value in values:
                with self.subTest(name=name, value=value):
                    _assert_code(
                        self,
                        CanonicalWavCode.INVALID_INPUT,
                        self.transcribe,
                        lambda _: [],
                        **{name: value},
                    )

    def test_invalid_timestamps(self) -> None:
        invalid = (
            (True, 0.1, "x"),
            ("0", 0.1, "x"),
            (float("nan"), 0.1, "x"),
            (0.0, float("inf"), "x"),
            (-0.1, 0.1, "x"),
            (0.2, 0.1, "x"),
        )
        for segment in invalid:
            with self.subTest(segment=segment[:2]):
                _assert_code(
                    self,
                    CanonicalWavCode.INVALID_BACKEND_RESULT,
                    self.transcribe,
                    lambda _, value=segment: [value],
                )

    def test_nonmonotonic_and_overlapping_segments_rejected(self) -> None:
        for segments in (
            [(0.2, 0.4, "a"), (0.1, 0.5, "b")],
            [(0.0, 0.7, "a"), (0.6, 0.8, "b")],
        ):
            _assert_code(
                self,
                CanonicalWavCode.INVALID_BACKEND_RESULT,
                self.transcribe,
                lambda _, value=segments: value,
            )

    def test_segment_duration_tolerance(self) -> None:
        tolerance_seconds = SEGMENT_END_TOLERANCE_MS / 1_000
        result = self.transcribe(lambda _: [(0.0, 1.0 + tolerance_seconds, "ok")])
        self.assertEqual(result.segments[0].end_ms, 1_020)
        _assert_code(
            self,
            CanonicalWavCode.INVALID_BACKEND_RESULT,
            self.transcribe,
            lambda _: [(0.0, 1.0 + tolerance_seconds + 0.001, "bad")],
        )

    def test_invalid_segment_shapes_and_text(self) -> None:
        class TextSubclass(str):
            pass

        class HostileSegment:
            @property
            def start(self):
                raise AssertionError("HOSTILE_PROPERTY_CANARY")

        invalid = (
            [0.0, 0.1, "x"],
            (0.0, 0.1),
            (0.0, 0.1, TextSubclass("x")),
            (0.0, 0.1, None),
            HostileSegment(),
        )
        for segment in invalid:
            with self.subTest(kind=type(segment).__name__):
                _assert_code(
                    self,
                    CanonicalWavCode.INVALID_BACKEND_RESULT,
                    self.transcribe,
                    lambda _, value=segment: [value],
                )

    def test_surrogate_text_is_rejected(self) -> None:
        _assert_code(
            self,
            CanonicalWavCode.INVALID_BACKEND_RESULT,
            self.transcribe,
            lambda _: [(0.0, 0.1, "\ud800")],
        )

    def test_backend_exception_is_private_and_unchained(self) -> None:
        canary = "PRIVATE_BACKEND_EXCEPTION_CANARY"

        def backend(_waveform):
            raise ValueError(canary)

        error = _assert_code(self, CanonicalWavCode.BACKEND_FAILED, self.transcribe, backend)
        self.assertNotIn(canary, str(error))
        self.assertNotIn(canary, repr(error))
        self.assertIsNone(error.__context__)
        self.assertTrue(error.__suppress_context__)

    def test_backend_baseexception_propagates(self) -> None:
        class StopNow(BaseException):
            pass

        def backend(_waveform):
            raise StopNow

        with self.assertRaises(StopNow):
            self.transcribe(backend)

    def test_generator_baseexception_propagates(self) -> None:
        class StopNow(BaseException):
            pass

        def backend(_waveform):
            yield (0.0, 0.1, "ok")
            raise StopNow

        with self.assertRaises(StopNow):
            self.transcribe(backend)

    def test_native_coroutine_is_rejected_closed_and_not_executed(self) -> None:
        executed = False

        async def result():
            nonlocal executed
            executed = True
            return []

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _assert_code(
                self,
                CanonicalWavCode.INVALID_BACKEND_RESULT,
                self.transcribe,
                lambda _: result(),
            )
            gc.collect()
        self.assertFalse(executed)
        self.assertEqual(caught, [])

    def test_async_backend_function_is_rejected_without_execution(self) -> None:
        executed = False

        async def backend(_waveform):
            nonlocal executed
            executed = True
            return []

        _assert_code(self, CanonicalWavCode.INVALID_INPUT, self.transcribe, backend)
        self.assertFalse(executed)

    def test_backend_cannot_mutate_waveform(self) -> None:
        mutation_errors = []

        def backend(value):
            try:
                value[0] = 1.0
            except Exception as error:
                mutation_errors.append(type(error))
            try:
                value.setflags(write=True)
            except Exception as error:
                mutation_errors.append(type(error))
            return []

        self.transcribe(backend)
        self.assertEqual(mutation_errors, [ValueError, ValueError])

    def test_public_transcription_boundary_rejects_forged_waveform(self) -> None:
        backend_called = False
        forged = CanonicalWaveform(
            sample_count=1,
            duration_ms=1,
            waveform=np.frombuffer(np.float32(0.123).tobytes(), dtype=np.float32),
        )

        def backend(_value):
            nonlocal backend_called
            backend_called = True
            return []

        _assert_code(
            self,
            CanonicalWavCode.INVALID_INPUT,
            transcribe_canonical_wav,
            forged,
            byte_limit=46,
            duration_limit_ms=1,
            backend=backend,
        )
        self.assertFalse(backend_called)

    def test_result_models_are_frozen_and_text_is_absent_from_repr(self) -> None:
        canary = "TRANSCRIPT_REPR_PRIVACY_CANARY"
        result = self.transcribe(lambda _: [(0.0, 0.1, canary)])
        with self.assertRaises(FrozenInstanceError):
            result.duration_ms = 5  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            result.segments[0].text = "changed"  # type: ignore[misc]
        self.assertNotIn(canary, repr(result))
        self.assertNotIn(canary, repr(result.segments[0]))
        self.assertIs(type(result.segments), tuple)


class PrivacyAndPurityTests(unittest.TestCase):
    def test_input_and_backend_canaries_absent_from_errors_and_streams(self) -> None:
        canary = "MEDIA_PRIVACY_CANARY"
        stdout = io.StringIO()
        stderr = io.StringIO()

        def backend(_waveform):
            raise RuntimeError(canary)

        with redirect_stdout(stdout), redirect_stderr(stderr):
            error = _assert_code(
                self,
                CanonicalWavCode.BACKEND_FAILED,
            transcribe_canonical_wav,
            _wav_from_samples([1]),
            byte_limit=46,
            duration_limit_ms=1,
            backend=backend,
            )
        rendered = "\n".join((str(error), repr(error), stdout.getvalue(), stderr.getvalue()))
        self.assertNotIn(canary, rendered)

    def test_canary_absent_from_public_traceback(self) -> None:
        canary = "TRACEBACK_PRIVACY_CANARY"

        def backend(_waveform):
            raise ValueError(canary)

        try:
            content = _wav_from_samples([1])
            transcribe_canonical_wav(
                content,
                byte_limit=len(content),
                duration_limit_ms=1,
                backend=backend,
            )
        except CanonicalWavError as error:
            rendered = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        else:  # pragma: no cover
            self.fail("expected fixed backend failure")
        self.assertNotIn(canary, rendered)

    def test_fixed_error_contract(self) -> None:
        error = CanonicalWavError(CanonicalWavCode.INVALID_WAV)
        self.assertEqual(str(error), "INVALID_WAV")
        self.assertEqual(repr(error), "CanonicalWavError(code='INVALID_WAV')")
        hostile = CanonicalWavError("SECRET" )  # type: ignore[arg-type]
        self.assertEqual(str(hostile), "INVALID_INPUT")

    def test_production_imports_are_narrow(self) -> None:
        tree = ast.parse(PRODUCTION_PATH.read_text(encoding="utf-8"))
        allowed = {
            "__future__",
            "dataclasses",
            "enum",
            "inspect",
            "math",
            "struct",
            "typing",
            "numpy",
        }
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertLessEqual(imported, allowed)

    def test_no_forbidden_imports_or_calls(self) -> None:
        tree = ast.parse(PRODUCTION_PATH.read_text(encoding="utf-8"))
        forbidden_imports = {
            "os", "pathlib", "tempfile", "shutil", "socket", "subprocess",
            "logging", "time", "datetime", "random", "secrets", "uuid",
            "fastapi", "starlette", "sqlalchemy", "av", "ffmpeg", "librosa",
            "scipy", "soundfile", "faster_whisper", "torch", "chromadb",
        }
        imported = set()
        calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.add(node.func.attr)
        self.assertFalse(imported & forbidden_imports)
        self.assertFalse(calls & {"open", "print", "exec", "eval", "compile", "system", "popen"})

    def test_no_path_environment_network_process_or_persistence_symbols(self) -> None:
        tree = ast.parse(PRODUCTION_PATH.read_text(encoding="utf-8"))
        attributes = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        forbidden = {
            "environ", "getenv", "name", "path", "fileno", "unlink", "remove",
            "rename", "replace", "mkdir", "makedirs", "connect", "request",
            "commit", "execute", "write_text", "write_bytes",
        }
        self.assertFalse(attributes & forbidden)

    def test_no_logging_or_printing(self) -> None:
        source = PRODUCTION_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        self.assertNotIn("logging", names)
        self.assertNotIn("print", names)

    def test_public_models_have_only_approved_fields(self) -> None:
        self.assertEqual(
            tuple(item.name for item in fields(CanonicalWaveform)),
            ("sample_count", "duration_ms", "waveform"),
        )
        self.assertEqual(
            tuple(item.name for item in fields(CanonicalTranscriptSegment)),
            ("start_ms", "end_ms", "text"),
        )
        self.assertEqual(
            tuple(item.name for item in fields(CanonicalTranscript)),
            ("duration_ms", "segments", "transcript_text"),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
