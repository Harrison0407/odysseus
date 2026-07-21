"""File-backed sibling of the strict canonical WAV adapter for long audio."""

from __future__ import annotations

import mmap
import os
from pathlib import Path
import stat
from typing import Callable

import numpy as np

from src.marketmatch_canonical_wav import (
    BYTE_RATE,
    MAX_SEGMENTS,
    MAX_TRANSCRIPT_UTF8_BYTES,
    SAMPLE_RATE,
    CanonicalTranscript,
    CanonicalWaveform,
    CanonicalWavCode,
    CanonicalWavError,
    _fail,
    _transcribe_validated_waveform,
    validate_canonical_wav_layout,
)


MAX_FILE_DURATION_MS = 6 * 60 * 60 * 1_000
MAX_FILE_SEGMENTS = 32_768
MAX_FILE_TRANSCRIPT_UTF8_BYTES = 1_048_576
_MIN_CANONICAL_WAV_SIZE = 46


def decode_canonical_wav_file(
    path: Path,
    *,
    byte_limit: int,
    duration_limit_ms: int,
) -> CanonicalWaveform:
    """Apply the exact canonical layout checks without copying encoded bytes."""

    if (
        not isinstance(path, Path)
        or type(byte_limit) is not int
        or byte_limit < _MIN_CANONICAL_WAV_SIZE
        or type(duration_limit_ms) is not int
        or not 0 < duration_limit_ms <= MAX_FILE_DURATION_MS
    ):
        _fail(CanonicalWavCode.INVALID_INPUT)
    descriptor = None
    mapping = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        details = os.fstat(descriptor)
        size = details.st_size
        if not stat.S_ISREG(details.st_mode):
            _fail(CanonicalWavCode.INVALID_INPUT)
        header = os.pread(descriptor, 44, 0)
        sample_count = validate_canonical_wav_layout(
            header,
            total_size=size,
            byte_limit=byte_limit,
            duration_limit_ms=duration_limit_ms,
            maximum_byte_limit=44 + (MAX_FILE_DURATION_MS // 1_000 * BYTE_RATE),
            maximum_duration_ms=MAX_FILE_DURATION_MS,
        )
        mapping = mmap.mmap(descriptor, 0, access=mmap.ACCESS_READ)
        pcm = np.frombuffer(mapping, dtype=np.dtype("<i2"), count=sample_count, offset=44)
        waveform = pcm.astype(np.float32)
        waveform *= np.float32(1.0 / 32768.0)
        del pcm
        waveform.setflags(write=False)
    except CanonicalWavError:
        raise
    except Exception:
        _fail(CanonicalWavCode.INVALID_WAV)
    finally:
        if mapping is not None:
            try:
                mapping.close()
            except Exception:
                pass
        if descriptor is not None:
            try:
                os.close(descriptor)
            except Exception:
                pass
    return CanonicalWaveform(
        sample_count=sample_count,
        duration_ms=(sample_count * 1_000 + SAMPLE_RATE - 1) // SAMPLE_RATE,
        waveform=waveform,
    )


def transcribe_canonical_wav_file(
    path: Path,
    *,
    byte_limit: int,
    duration_limit_ms: int,
    backend: Callable[[np.ndarray], object],
    transcript_utf8_limit: int = MAX_FILE_TRANSCRIPT_UTF8_BYTES,
    segment_limit: int = MAX_FILE_SEGMENTS,
) -> CanonicalTranscript:
    waveform = decode_canonical_wav_file(
        path, byte_limit=byte_limit, duration_limit_ms=duration_limit_ms
    )
    return _transcribe_validated_waveform(
        waveform,
        backend=backend,
        transcript_utf8_limit=transcript_utf8_limit,
        segment_limit=segment_limit,
        maximum_sample_count=duration_limit_ms * SAMPLE_RATE // 1_000,
        require_immutable_bytes_backing=False,
        maximum_transcript_utf8_bytes=MAX_FILE_TRANSCRIPT_UTF8_BYTES,
        maximum_segments=MAX_FILE_SEGMENTS,
    )


__all__ = (
    "MAX_FILE_DURATION_MS", "MAX_FILE_SEGMENTS", "MAX_FILE_TRANSCRIPT_UTF8_BYTES",
    "decode_canonical_wav_file", "transcribe_canonical_wav_file",
)
