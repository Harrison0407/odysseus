"""Strict in-memory PCM WAV boundary for a controlled MarketMatch pilot.

Only one canonical RIFF/WAVE representation is accepted: a 16-byte PCM
``fmt `` chunk followed by one nonempty ``data`` chunk containing mono,
16 kHz, signed little-endian 16-bit samples.  All other chunks and layouts are
rejected.  Duration is derived from the validated sample bytes, never from a
backend declaration.

The decoder performs no filesystem, pathname, network, process, model, or
application operation.  It converts the samples to normalized float32 and
returns a read-only NumPy view backed by immutable bytes.  Peak conversion
memory is bounded by the 20 MiB encoded input, one float32 conversion array,
and one immutable float32 backing copy (about 92 MiB total at the ten-minute
format maximum, including the caller's encoded bytes).

The transcription function accepts an injected synchronous callable.  The
callable receives only the validated NumPy waveform and must return a finite
iterable of exact built-in ``(start_seconds, end_seconds, text)`` tuples.  No
backend duration, language, model identity, path, or metadata is accepted.
Ordinary backend ``Exception`` failures are reduced to fixed errors;
``BaseException`` control-flow signals propagate unchanged by explicit policy.
This module does not load a model and makes no persistence, authorization,
publication, workflow-completion, or transcription-correctness claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import inspect
import math
import struct
from typing import Callable, NoReturn

import numpy as np


MAX_WAV_BYTES = 20 * 1024 * 1024
MAX_DURATION_MS = 10 * 60 * 1_000
MAX_SEGMENTS = 4_096
MAX_TRANSCRIPT_UTF8_BYTES = 262_144

SAMPLE_RATE = 16_000
CHANNELS = 1
BITS_PER_SAMPLE = 16
BLOCK_ALIGN = 2
BYTE_RATE = 32_000
SEGMENT_END_TOLERANCE_MS = 20

_RIFF_HEADER_SIZE = 12
_CHUNK_HEADER_SIZE = 8
_PCM_FMT_SIZE = 16
_MIN_CANONICAL_WAV_SIZE = _RIFF_HEADER_SIZE + _CHUNK_HEADER_SIZE + _PCM_FMT_SIZE + _CHUNK_HEADER_SIZE + 2
_MAX_SAMPLE_COUNT = SAMPLE_RATE * (MAX_DURATION_MS // 1_000)


class CanonicalWavCode(str, Enum):
    """Fixed, non-echoing media-boundary failure codes."""

    INVALID_INPUT = "INVALID_INPUT"
    INPUT_LIMIT_EXCEEDED = "INPUT_LIMIT_EXCEEDED"
    INVALID_WAV = "INVALID_WAV"
    UNSUPPORTED_WAV_FORMAT = "UNSUPPORTED_WAV_FORMAT"
    DURATION_LIMIT_EXCEEDED = "DURATION_LIMIT_EXCEEDED"
    INVALID_BACKEND_RESULT = "INVALID_BACKEND_RESULT"
    SEGMENT_LIMIT_EXCEEDED = "SEGMENT_LIMIT_EXCEEDED"
    TRANSCRIPT_LIMIT_EXCEEDED = "TRANSCRIPT_LIMIT_EXCEEDED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    BACKEND_FAILED = "BACKEND_FAILED"


_SAFE_RESULT_FIELDS = frozenset({
    "worker_result",
    "segments",
    "segments.start_ms",
    "segments.end_ms",
    "segments.order",
    "segments.text",
    "transcript_text",
})
_SAFE_RESULT_TYPES = frozenset({
    "bool", "bytes", "dict", "float", "generator", "int", "list",
    "NoneType", "str", "tuple", "unknown",
})


class CanonicalWavError(Exception):
    """A fixed-code failure that never embeds rejected values."""

    def __init__(
        self,
        code: CanonicalWavCode,
        *,
        result_field: str | None = None,
        result_type: str | None = None,
    ):
        if type(code) is not CanonicalWavCode:
            code = CanonicalWavCode.INVALID_INPUT
        self.code = code
        self.result_field = result_field if result_field in _SAFE_RESULT_FIELDS else None
        self.result_type = (
            result_type
            if self.result_field is not None and result_type in _SAFE_RESULT_TYPES
            else None
        )
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r})"


@dataclass(frozen=True, slots=True, eq=False)
class CanonicalWaveform:
    """A validated waveform whose array is backed by immutable bytes."""

    sample_count: int = field(repr=False)
    duration_ms: int = field(repr=False)
    waveform: np.ndarray = field(repr=False, compare=False, hash=False)

    def __repr__(self) -> str:
        return "CanonicalWaveform(<validated>)"


@dataclass(frozen=True, slots=True)
class CanonicalTranscriptSegment:
    """One validated, ordered transcript segment."""

    start_ms: int
    end_ms: int
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class CanonicalTranscript:
    """Bounded immutable transcription output detached from backend objects."""

    duration_ms: int = field(repr=False)
    segments: tuple[CanonicalTranscriptSegment, ...] = field(repr=False)
    transcript_text: str = field(repr=False)

    def __repr__(self) -> str:
        return "CanonicalTranscript(<validated>)"


def _safe_result_type(value: object) -> str:
    name = type(value).__name__
    return name if name in _SAFE_RESULT_TYPES else "unknown"


def _fail(
    code: CanonicalWavCode,
    *,
    result_field: str | None = None,
    result_value: object = None,
) -> NoReturn:
    safe_field = result_field if result_field in _SAFE_RESULT_FIELDS else None
    safe_type = _safe_result_type(result_value) if safe_field is not None else None
    raise CanonicalWavError(
        code,
        result_field=safe_field,
        result_type=safe_type,
    ) from None


def _validate_decode_inputs(
    wav_bytes: object,
    byte_limit: object,
    duration_limit_ms: object,
) -> tuple[bytes, int, int]:
    if type(byte_limit) is not int or not 0 <= byte_limit <= MAX_WAV_BYTES:
        _fail(CanonicalWavCode.INVALID_INPUT)
    if (
        type(duration_limit_ms) is not int
        or not 0 < duration_limit_ms <= MAX_DURATION_MS
    ):
        _fail(CanonicalWavCode.INVALID_INPUT)
    if type(wav_bytes) is not bytes:
        _fail(CanonicalWavCode.INVALID_INPUT)
    if len(wav_bytes) > byte_limit or len(wav_bytes) > MAX_WAV_BYTES:
        _fail(CanonicalWavCode.INPUT_LIMIT_EXCEEDED)
    if len(wav_bytes) < _MIN_CANONICAL_WAV_SIZE:
        _fail(CanonicalWavCode.INVALID_WAV)
    return wav_bytes, byte_limit, duration_limit_ms


def _read_u32(data: bytes, offset: int) -> int:
    try:
        return struct.unpack_from("<I", data, offset)[0]
    except (struct.error, OverflowError):
        _fail(CanonicalWavCode.INVALID_WAV)


def _parse_canonical_wav(data: bytes) -> tuple[int, int, int]:
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        _fail(CanonicalWavCode.INVALID_WAV)
    if _read_u32(data, 4) != len(data) - 8:
        _fail(CanonicalWavCode.INVALID_WAV)

    chunks: list[tuple[bytes, int, int]] = []
    offset = _RIFF_HEADER_SIZE
    while offset < len(data):
        if len(data) - offset < _CHUNK_HEADER_SIZE:
            _fail(CanonicalWavCode.INVALID_WAV)
        chunk_id = data[offset : offset + 4]
        chunk_size = _read_u32(data, offset + 4)
        payload_start = offset + _CHUNK_HEADER_SIZE
        payload_end = payload_start + chunk_size
        if payload_end < payload_start or payload_end > len(data):
            _fail(CanonicalWavCode.INVALID_WAV)
        padded_end = payload_end + (chunk_size & 1)
        if padded_end < payload_end or padded_end > len(data):
            _fail(CanonicalWavCode.INVALID_WAV)
        if chunk_size & 1 and data[payload_end:padded_end] != b"\x00":
            _fail(CanonicalWavCode.INVALID_WAV)
        chunks.append((chunk_id, payload_start, chunk_size))
        if len(chunks) > 2:
            if any(item[0] not in (b"fmt ", b"data") for item in chunks):
                _fail(CanonicalWavCode.UNSUPPORTED_WAV_FORMAT)
            _fail(CanonicalWavCode.INVALID_WAV)
        offset = padded_end

    if offset != len(data):
        _fail(CanonicalWavCode.INVALID_WAV)
    if len(chunks) != 2:
        known = tuple(chunk_id for chunk_id, _, _ in chunks)
        if any(chunk_id not in (b"fmt ", b"data") for chunk_id in known):
            _fail(CanonicalWavCode.UNSUPPORTED_WAV_FORMAT)
        _fail(CanonicalWavCode.INVALID_WAV)
    if any(chunk_id not in (b"fmt ", b"data") for chunk_id, _, _ in chunks):
        _fail(CanonicalWavCode.UNSUPPORTED_WAV_FORMAT)
    if chunks[0][0] != b"fmt " or chunks[1][0] != b"data":
        _fail(CanonicalWavCode.INVALID_WAV)

    _, fmt_start, fmt_size = chunks[0]
    if fmt_size != _PCM_FMT_SIZE:
        _fail(CanonicalWavCode.UNSUPPORTED_WAV_FORMAT)
    try:
        (
            format_code,
            channels,
            sample_rate,
            byte_rate,
            block_align,
            bits_per_sample,
        ) = struct.unpack_from("<HHIIHH", data, fmt_start)
    except (struct.error, OverflowError):
        _fail(CanonicalWavCode.INVALID_WAV)

    if format_code != 1:
        _fail(CanonicalWavCode.UNSUPPORTED_WAV_FORMAT)
    if (
        channels != CHANNELS
        or sample_rate != SAMPLE_RATE
        or bits_per_sample != BITS_PER_SAMPLE
        or block_align != BLOCK_ALIGN
        or byte_rate != BYTE_RATE
    ):
        _fail(CanonicalWavCode.UNSUPPORTED_WAV_FORMAT)

    _, data_start, data_size = chunks[1]
    if data_size == 0 or data_size % BLOCK_ALIGN != 0:
        _fail(CanonicalWavCode.INVALID_WAV)
    sample_count = data_size // BLOCK_ALIGN
    if sample_count > _MAX_SAMPLE_COUNT:
        _fail(CanonicalWavCode.DURATION_LIMIT_EXCEEDED)
    return data_start, data_size, sample_count


def _immutable_float32_waveform(
    data: bytes,
    *,
    data_start: int,
    sample_count: int,
) -> np.ndarray:
    conversion_failed = False
    immutable_float_bytes = b""
    try:
        pcm = np.frombuffer(data, dtype=np.dtype("<i2"), count=sample_count, offset=data_start)
        normalized = pcm.astype(np.float32)
        normalized *= np.float32(1.0 / 32768.0)
        immutable_float_bytes = normalized.tobytes(order="C")
        waveform = np.frombuffer(immutable_float_bytes, dtype=np.float32)
        waveform.setflags(write=False)
    except Exception:
        conversion_failed = True
    if conversion_failed:
        _fail(CanonicalWavCode.INVALID_WAV)
    return waveform


def decode_canonical_wav(
    wav_bytes: bytes,
    *,
    byte_limit: int,
    duration_limit_ms: int,
) -> CanonicalWaveform:
    """Validate and decode one exact canonical PCM WAV from immutable bytes.

    Empty audio is rejected.  The byte limit covers the complete RIFF object.
    Duration is compared as exact integer sample-time arithmetic and the public
    millisecond duration is rounded upward so it never understates the source.
    """

    data, _, duration_limit = _validate_decode_inputs(
        wav_bytes,
        byte_limit,
        duration_limit_ms,
    )
    data_start, _, sample_count = _parse_canonical_wav(data)
    if sample_count * 1_000 > duration_limit * SAMPLE_RATE:
        _fail(CanonicalWavCode.DURATION_LIMIT_EXCEEDED)

    waveform = _immutable_float32_waveform(
        data,
        data_start=data_start,
        sample_count=sample_count,
    )
    duration_ms = (sample_count * 1_000 + SAMPLE_RATE - 1) // SAMPLE_RATE
    return CanonicalWaveform(
        sample_count=sample_count,
        duration_ms=duration_ms,
        waveform=waveform,
    )


def validate_canonical_wav_layout(
    header: bytes,
    *,
    total_size: int,
    byte_limit: int,
    duration_limit_ms: int,
    maximum_byte_limit: int,
    maximum_duration_ms: int,
) -> int:
    """Validate the exact FFmpeg canonical layout from a bounded 44-byte header.

    This pure structural boundary lets a trusted file owner validate long WAVs
    without copying their complete encoded bytes into Python memory. It accepts
    only the same PCM parameters and exact ``fmt``/``data`` order as the
    in-memory adapter and returns the sample-derived count.
    """

    if (
        type(header) is not bytes
        or len(header) != 44
        or any(type(value) is not int for value in (
            total_size, byte_limit, duration_limit_ms, maximum_byte_limit,
            maximum_duration_ms,
        ))
        or not 46 <= byte_limit <= maximum_byte_limit
        or not 0 < duration_limit_ms <= maximum_duration_ms
    ):
        _fail(CanonicalWavCode.INVALID_INPUT)
    if total_size > byte_limit:
        _fail(CanonicalWavCode.INPUT_LIMIT_EXCEEDED)
    if total_size < 46:
        _fail(CanonicalWavCode.INVALID_WAV)
    if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        _fail(CanonicalWavCode.INVALID_WAV)
    if _read_u32(header, 4) != total_size - 8:
        _fail(CanonicalWavCode.INVALID_WAV)
    if header[12:16] != b"fmt " or _read_u32(header, 16) != _PCM_FMT_SIZE:
        _fail(CanonicalWavCode.UNSUPPORTED_WAV_FORMAT)
    try:
        fmt = struct.unpack_from("<HHIIHH", header, 20)
    except (struct.error, OverflowError):
        _fail(CanonicalWavCode.INVALID_WAV)
    if fmt[0] != 1:
        _fail(CanonicalWavCode.UNSUPPORTED_WAV_FORMAT)
    if fmt[1:] != (CHANNELS, SAMPLE_RATE, BYTE_RATE, BLOCK_ALIGN, BITS_PER_SAMPLE):
        _fail(CanonicalWavCode.UNSUPPORTED_WAV_FORMAT)
    if header[36:40] != b"data":
        _fail(CanonicalWavCode.INVALID_WAV)
    data_size = _read_u32(header, 40)
    if data_size == 0 or data_size % BLOCK_ALIGN or 44 + data_size != total_size:
        _fail(CanonicalWavCode.INVALID_WAV)
    sample_count = data_size // BLOCK_ALIGN
    if sample_count * 1_000 > duration_limit_ms * SAMPLE_RATE:
        _fail(CanonicalWavCode.DURATION_LIMIT_EXCEEDED)
    return sample_count


def _validate_transcription_limits(
    transcript_utf8_limit: object,
    segment_limit: object,
    *,
    maximum_transcript_utf8_bytes: int = MAX_TRANSCRIPT_UTF8_BYTES,
    maximum_segments: int = MAX_SEGMENTS,
) -> tuple[int, int]:
    if (
        type(transcript_utf8_limit) is not int
        or not 0 <= transcript_utf8_limit <= maximum_transcript_utf8_bytes
    ):
        _fail(CanonicalWavCode.INVALID_INPUT)
    if type(segment_limit) is not int or not 0 <= segment_limit <= maximum_segments:
        _fail(CanonicalWavCode.INVALID_INPUT)
    return transcript_utf8_limit, segment_limit


def _validate_waveform(
    value: object,
    *,
    maximum_sample_count: int = _MAX_SAMPLE_COUNT,
    require_immutable_bytes_backing: bool = True,
) -> CanonicalWaveform:
    if type(value) is not CanonicalWaveform:
        _fail(CanonicalWavCode.INVALID_INPUT)
    if (
        type(value.sample_count) is not int
        or not 0 < value.sample_count <= maximum_sample_count
        or type(value.duration_ms) is not int
        or value.duration_ms
        != (value.sample_count * 1_000 + SAMPLE_RATE - 1) // SAMPLE_RATE
        or type(value.waveform) is not np.ndarray
        or value.waveform.dtype != np.dtype(np.float32)
        or value.waveform.shape != (value.sample_count,)
        or not value.waveform.flags.c_contiguous
        or value.waveform.flags.writeable
        or (require_immutable_bytes_backing and type(value.waveform.base) is not bytes)
        or value.waveform.nbytes != value.sample_count * 4
    ):
        _fail(CanonicalWavCode.INVALID_INPUT)
    try:
        if not bool(np.isfinite(value.waveform).all()):
            _fail(CanonicalWavCode.INVALID_INPUT)
        if bool((value.waveform < -1.0).any()) or bool((value.waveform > (32767.0 / 32768.0)).any()):
            _fail(CanonicalWavCode.INVALID_INPUT)
    except CanonicalWavError:
        raise
    except Exception:
        _fail(CanonicalWavCode.INVALID_INPUT)
    return value


def _close_unexecuted_coroutine(value: object) -> None:
    if not inspect.iscoroutine(value):
        return
    close_failed = False
    try:
        value.close()
    except Exception:
        close_failed = True
    if close_failed:
        _fail(CanonicalWavCode.INVALID_BACKEND_RESULT)


def _call_backend(
    backend: object,
    waveform: np.ndarray,
) -> object:
    if not callable(backend) or inspect.iscoroutinefunction(backend):
        _fail(CanonicalWavCode.INVALID_INPUT)
    failed = False
    result: object = None
    try:
        result = backend(waveform)
    except CanonicalWavError:
        raise
    except Exception:
        failed = True
    if failed:
        _fail(
            CanonicalWavCode.BACKEND_FAILED,
            result_field="worker_result",
            result_value=result,
        )
    if inspect.isawaitable(result):
        _close_unexecuted_coroutine(result)
        _fail(
            CanonicalWavCode.INVALID_BACKEND_RESULT,
            result_field="worker_result",
            result_value=result,
        )
    return result


def _backend_iterator(result: object):
    failed = False
    iterator: object = None
    try:
        iterator = iter(result)
    except Exception:
        failed = True
    if failed:
        _fail(CanonicalWavCode.INVALID_BACKEND_RESULT)
    return iterator


def _next_backend_segment(iterator: object) -> tuple[bool, object]:
    failed = False
    try:
        return False, next(iterator)
    except StopIteration:
        return True, None
    except Exception:
        failed = True
    if failed:
        _fail(CanonicalWavCode.BACKEND_FAILED)


def _timestamp_seconds(value: object, *, result_field: str) -> float:
    if type(value) not in (int, float):
        _fail(
            CanonicalWavCode.INVALID_BACKEND_RESULT,
            result_field=result_field,
            result_value=value,
        )
    try:
        numeric = float(value)
    except (OverflowError, ValueError):
        _fail(CanonicalWavCode.INVALID_BACKEND_RESULT)
    if not math.isfinite(numeric) or numeric < 0:
        _fail(
            CanonicalWavCode.INVALID_BACKEND_RESULT,
            result_field=result_field,
            result_value=value,
        )
    return numeric


def _validated_segment(
    value: object,
    *,
    previous_end: float,
    maximum_end: float,
    remaining_text_bytes: int,
) -> tuple[CanonicalTranscriptSegment, float, int]:
    if type(value) is not tuple or len(value) != 3:
        _fail(
            CanonicalWavCode.INVALID_BACKEND_RESULT,
            result_field="segments",
            result_value=value,
        )
    start = _timestamp_seconds(value[0], result_field="segments.start_ms")
    end = _timestamp_seconds(value[1], result_field="segments.end_ms")
    text = value[2]
    if start > end or start < previous_end:
        _fail(
            CanonicalWavCode.INVALID_BACKEND_RESULT,
            result_field="segments.order",
            result_value=value,
        )
    if end > maximum_end:
        _fail(
            CanonicalWavCode.INVALID_BACKEND_RESULT,
            result_field="segments.end_ms",
            result_value=value[1],
        )
    if type(text) is not str:
        _fail(
            CanonicalWavCode.INVALID_BACKEND_RESULT,
            result_field="segments.text",
            result_value=text,
        )
    if len(text) > remaining_text_bytes:
        _fail(CanonicalWavCode.TRANSCRIPT_LIMIT_EXCEEDED)
    encode_failed = False
    text_size = 0
    try:
        text_size = len(text.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        encode_failed = True
    if encode_failed:
        _fail(CanonicalWavCode.INVALID_BACKEND_RESULT)
    if text_size > remaining_text_bytes:
        _fail(CanonicalWavCode.TRANSCRIPT_LIMIT_EXCEEDED)
    start_ms = int(round(start * 1_000))
    end_ms = int(round(end * 1_000))
    return CanonicalTranscriptSegment(start_ms=start_ms, end_ms=end_ms, text=text), end, text_size


def _transcribe_validated_waveform(
    waveform: CanonicalWaveform,
    *,
    backend: Callable[[np.ndarray], object],
    transcript_utf8_limit: int = MAX_TRANSCRIPT_UTF8_BYTES,
    segment_limit: int = MAX_SEGMENTS,
    maximum_sample_count: int = _MAX_SAMPLE_COUNT,
    require_immutable_bytes_backing: bool = True,
    maximum_transcript_utf8_bytes: int = MAX_TRANSCRIPT_UTF8_BYTES,
    maximum_segments: int = MAX_SEGMENTS,
) -> CanonicalTranscript:
    """Invoke one injected sync backend and bound its lazy segment output.

    The backend receives only the validated read-only float32 array.  It must
    yield exact built-in tuples ``(start_seconds, end_seconds, text)``.  Segment
    intervals may not overlap, and their ends may exceed the sample-derived
    duration by at most 20 ms, matching Whisper's timestamp quantum.  Text is
    concatenated exactly as supplied and bounded by its strict UTF-8 length.
    """

    trusted_waveform = _validate_waveform(
        waveform,
        maximum_sample_count=maximum_sample_count,
        require_immutable_bytes_backing=require_immutable_bytes_backing,
    )
    transcript_limit, segments_limit = _validate_transcription_limits(
        transcript_utf8_limit,
        segment_limit,
        maximum_transcript_utf8_bytes=maximum_transcript_utf8_bytes,
        maximum_segments=maximum_segments,
    )
    backend_result = _call_backend(backend, trusted_waveform.waveform)
    iterator = _backend_iterator(backend_result)

    segments: list[CanonicalTranscriptSegment] = []
    texts: list[str] = []
    transcript_size = 0
    previous_end = 0.0
    maximum_end = trusted_waveform.sample_count / SAMPLE_RATE + SEGMENT_END_TOLERANCE_MS / 1_000

    while True:
        done, raw_segment = _next_backend_segment(iterator)
        if done:
            break
        if len(segments) >= segments_limit:
            _fail(CanonicalWavCode.SEGMENT_LIMIT_EXCEEDED)
        segment, previous_end, text_size = _validated_segment(
            raw_segment,
            previous_end=previous_end,
            maximum_end=maximum_end,
            remaining_text_bytes=transcript_limit - transcript_size,
        )
        transcript_size += text_size
        if transcript_size > transcript_limit:
            _fail(CanonicalWavCode.TRANSCRIPT_LIMIT_EXCEEDED)
        segments.append(segment)
        texts.append(segment.text)

    return CanonicalTranscript(
        duration_ms=trusted_waveform.duration_ms,
        segments=tuple(segments),
        transcript_text="".join(texts),
    )


def transcribe_canonical_wav(
    wav_bytes: bytes,
    *,
    byte_limit: int,
    duration_limit_ms: int,
    backend: Callable[[np.ndarray], object],
    transcript_utf8_limit: int = MAX_TRANSCRIPT_UTF8_BYTES,
    segment_limit: int = MAX_SEGMENTS,
) -> CanonicalTranscript:
    """Decode canonical WAV bytes and transcribe the resulting waveform.

    The public transcription boundary accepts bytes rather than a caller-built
    :class:`CanonicalWaveform`, so waveform provenance cannot bypass the RIFF
    validator.  Decoding occurs exactly once before the private waveform
    consumer invokes the injected backend.
    """

    waveform = decode_canonical_wav(
        wav_bytes,
        byte_limit=byte_limit,
        duration_limit_ms=duration_limit_ms,
    )
    return _transcribe_validated_waveform(
        waveform,
        backend=backend,
        transcript_utf8_limit=transcript_utf8_limit,
        segment_limit=segment_limit,
    )


__all__ = (
    "BITS_PER_SAMPLE",
    "BLOCK_ALIGN",
    "BYTE_RATE",
    "CHANNELS",
    "MAX_DURATION_MS",
    "MAX_SEGMENTS",
    "MAX_TRANSCRIPT_UTF8_BYTES",
    "MAX_WAV_BYTES",
    "SAMPLE_RATE",
    "SEGMENT_END_TOLERANCE_MS",
    "CanonicalTranscript",
    "CanonicalTranscriptSegment",
    "CanonicalWaveform",
    "CanonicalWavCode",
    "CanonicalWavError",
    "decode_canonical_wav",
    "transcribe_canonical_wav",
    "validate_canonical_wav_layout",
)
