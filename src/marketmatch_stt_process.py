"""Isolated local transcription process for the controlled MarketMatch pilot.

Audio and results cross the process boundary only as bounded byte messages over
one-way ``multiprocessing.Connection`` objects.  The worker has no application
state, database, pathname, temporary-file, provider, or network fallback.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import json
import math
import multiprocessing
from multiprocessing.connection import Connection
import os
from pathlib import Path
import threading
import time
from typing import Callable, NoReturn

from src.marketmatch_canonical_wav import (
    MAX_DURATION_MS,
    MAX_SEGMENTS,
    MAX_TRANSCRIPT_UTF8_BYTES,
    MAX_WAV_BYTES,
    SEGMENT_END_TOLERANCE_MS,
    CanonicalTranscript,
    CanonicalWavCode,
    CanonicalWavError,
    transcribe_canonical_wav,
)
from src.marketmatch_canonical_wav_file import (
    MAX_FILE_SEGMENTS,
    MAX_FILE_TRANSCRIPT_UTF8_BYTES,
    transcribe_canonical_wav_file,
)
from src.marketmatch_i18n import normalize_transcript_language


MAX_RESULT_JSON_BYTES = 4 * 1024 * 1024
WHISPER_TIMESTAMP_OVERLAP_TOLERANCE_SECONDS = 0.020001
PROCESS_TERMINATE_GRACE_SECONDS = 0.5
PROCESS_CLEANUP_RESERVE_SECONDS = 1.0
PROCESS_POLL_INTERVAL_SECONDS = 0.01
FASTER_WHISPER_BASE_REVISION = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"
_WORKER_ENV_ALLOWLIST = frozenset(
    {
        "HF_HOME",
        "HF_HUB_CACHE",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "HUGGINGFACE_HUB_CACHE",
        "LOCALAPPDATA",
        "USERPROFILE",
        "XDG_CACHE_HOME",
    }
)


class MarketMatchProcessCode(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"
    WORKER_TIMEOUT = "WORKER_TIMEOUT"
    WORKER_CRASHED = "WORKER_CRASHED"
    WORKER_FAILED = "WORKER_FAILED"
    WORKER_PROTOCOL_ERROR = "WORKER_PROTOCOL_ERROR"


_SAFE_WORKER_EXIT_STATES = frozenset({
    "clean_exit", "nonzero_exit", "protocol_error", "timed_out", "unknown",
})
_SAFE_RESULT_FIELDS = frozenset({
    "worker_result", "duration_ms", "segments", "segments.start_ms",
    "segments.end_ms", "segments.order", "segments.text", "transcript_text",
    "language", "language_confidence",
})
_SAFE_RESULT_TYPES = frozenset({
    "bool", "bytes", "dict", "float", "generator", "int", "list",
    "NoneType", "str", "tuple", "unknown",
})


class MarketMatchProcessError(Exception):
    """Fixed process-boundary failure that never embeds worker or media data."""

    def __init__(
        self,
        code: MarketMatchProcessCode | CanonicalWavCode,
        *,
        worker_exit_state: str | None = None,
        result_field: str | None = None,
        result_type: str | None = None,
    ):
        if type(code) not in (MarketMatchProcessCode, CanonicalWavCode):
            code = MarketMatchProcessCode.WORKER_FAILED
        self.code = code
        self.worker_exit_state = (
            worker_exit_state if worker_exit_state in _SAFE_WORKER_EXIT_STATES else None
        )
        self.result_field = result_field if result_field in _SAFE_RESULT_FIELDS else None
        self.result_type = (
            result_type
            if self.result_field is not None and result_type in _SAFE_RESULT_TYPES
            else None
        )
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r})"


@dataclass(frozen=True, slots=True)
class MarketMatchProcessResult:
    duration_ms: int
    transcript_text: str
    segments: tuple[tuple[int, int, str], ...]
    language: str = "und"
    language_confidence: float | None = None


_ADMISSION_GUARD = threading.Lock()
_ADMISSION_TOKEN: object | None = None
_ACTIVE_LOCK = threading.RLock()
_ACTIVE_PROCESSES: set[object] = set()
_SPAWN_START_LOCK = threading.RLock()
_STANDARD_DESCRIPTOR_FLAGS = (
    (0, os.O_RDONLY),
    (1, os.O_WRONLY),
    (2, os.O_WRONLY),
)


def _standard_descriptor_is_valid(descriptor: int) -> bool:
    """Return whether a standard descriptor still names a usable kernel object."""

    try:
        os.fstat(descriptor)
    except OSError:
        return False
    return True


def _repair_invalid_standard_descriptors() -> tuple[int, ...]:
    """Fill only invalid standard descriptors before multiprocessing allocates FDs.

    A terminal descriptor can remain allocated yet become unusable after its
    controlling terminal is revoked.  Opening the null device first and then
    using ``dup2`` handles both that case and an ordinary closed descriptor.
    Temporary descriptors are non-inheritable and always closed here.  The
    repaired standard descriptor itself remains inheritable, as a standard
    stream must, so every fresh interpreter sees the local null device.
    """

    repaired: list[int] = []
    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    for descriptor, access_mode in _STANDARD_DESCRIPTOR_FLAGS:
        if _standard_descriptor_is_valid(descriptor):
            continue
        replacement = os.open(os.devnull, access_mode | close_on_exec)
        try:
            if replacement == descriptor:
                os.set_inheritable(descriptor, True)
            elif not _standard_descriptor_is_valid(descriptor):
                os.dup2(replacement, descriptor, inheritable=True)
            else:
                continue
            if not _standard_descriptor_is_valid(descriptor):
                raise OSError("standard descriptor repair failed")
            repaired.append(descriptor)
        finally:
            if replacement != descriptor:
                os.close(replacement)
    return tuple(repaired)


def try_acquire_admission() -> object | None:
    """Atomically admit at most one transcription in this app process."""

    global _ADMISSION_TOKEN
    with _ADMISSION_GUARD:
        if _ADMISSION_TOKEN is not None:
            return None
        lease = object()
        _ADMISSION_TOKEN = lease
        return lease


def release_admission(lease: object | None = None) -> None:
    """Release one matching lease, or force-clear it during shutdown/tests."""

    global _ADMISSION_TOKEN
    with _ADMISSION_GUARD:
        if lease is None or _ADMISSION_TOKEN is lease:
            _ADMISSION_TOKEN = None


def _register_process(process: object) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_PROCESSES.add(process)


def _unregister_process(process: object) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_PROCESSES.discard(process)


def _terminate_and_reap(process: object) -> None:
    """Terminate, escalate when necessary, reap, and close one child handle."""

    try:
        alive = bool(process.is_alive())
    except Exception:
        alive = False
    if alive:
        try:
            process.terminate()
        except Exception:
            pass
        try:
            process.join(PROCESS_TERMINATE_GRACE_SECONDS)
        except Exception:
            pass
        try:
            alive = bool(process.is_alive())
        except Exception:
            alive = False
        if alive:
            try:
                process.kill()
            except Exception:
                pass
            try:
                process.join()
            except Exception:
                pass
    else:
        try:
            process.join()
        except Exception:
            pass
    try:
        process.close()
    except Exception:
        pass


def shutdown_active_workers() -> None:
    """Synchronously reap every registered pilot worker during app shutdown."""

    with _ACTIVE_LOCK:
        processes = tuple(_ACTIVE_PROCESSES)
    for process in processes:
        _terminate_and_reap(process)
        _unregister_process(process)
    release_admission()


def active_worker_count() -> int:
    with _ACTIVE_LOCK:
        return len(_ACTIVE_PROCESSES)


def _safe_type(value: object) -> str:
    name = type(value).__name__
    return name if name in _SAFE_RESULT_TYPES else "unknown"


def _fail(
    code: MarketMatchProcessCode | CanonicalWavCode,
    *,
    worker_exit_state: str | None = None,
    result_field: str | None = None,
    result_value: object = None,
    result_type: str | None = None,
) -> NoReturn:
    safe_state = worker_exit_state if worker_exit_state in _SAFE_WORKER_EXIT_STATES else None
    safe_field = result_field if result_field in _SAFE_RESULT_FIELDS else None
    safe_type = None
    if safe_field is not None:
        safe_type = result_type if result_type in _SAFE_RESULT_TYPES else _safe_type(result_value)
    raise MarketMatchProcessError(
        code,
        worker_exit_state=safe_state,
        result_field=safe_field,
        result_type=safe_type,
    ) from None


def _with_worker_exit_state(
    error: MarketMatchProcessError,
    state: str,
) -> MarketMatchProcessError:
    return MarketMatchProcessError(
        error.code,
        worker_exit_state=state if state in _SAFE_WORKER_EXIT_STATES else "unknown",
        result_field=error.result_field,
        result_type=error.result_type,
    )


def _fixed_local_base_backend(
    waveform,
    language_metadata: dict | None = None,
    requested_language: str | None = None,
):
    """Return exact tuples from the provisioned local faster-whisper base model."""

    try:
        from faster_whisper import WhisperModel

        model = WhisperModel(
            "base",
            device="cpu",
            compute_type="int8",
            num_workers=1,
            local_files_only=True,
            revision=FASTER_WHISPER_BASE_REVISION,
        )
    except Exception:
        raise CanonicalWavError(CanonicalWavCode.MODEL_UNAVAILABLE) from None
    if requested_language is None:
        segments, info = model.transcribe(waveform)
    else:
        segments, info = model.transcribe(waveform, language=requested_language)
    # This trusted engine adapter is the only language-normalization boundary.
    # Everything after it validates the canonical wire value verbatim.
    if type(language_metadata) is dict:
        language_metadata["language"] = normalize_transcript_language(
            getattr(info, "language", None)
        )
        probability = getattr(info, "language_probability", None)
        if type(probability) in (int, float) and math.isfinite(float(probability)):
            numeric = float(probability)
            language_metadata["language_confidence"] = numeric if 0.0 <= numeric <= 1.0 else None
        else:
            language_metadata["language_confidence"] = None

    # Whisper timestamps use a coarser frame grid than the input PCM duration.
    # For short, valid recordings its final segment can therefore end after the
    # last sample (for example 1.0 s for a 250 ms WAV).  Keep every other strict
    # backend check intact, but normalize that one trusted-model boundary to the
    # duration proved by the canonical waveform before the generic validator
    # checks ordering, finiteness, text, and transcript bounds.
    waveform_duration_seconds = len(waveform) / 16_000

    def _bounded_segments():
        previous_end = 0.0
        for item in segments:
            start = float(item.start)
            end = float(item.end)
            # Faster Whisper timestamps are relative to a padded 30-second
            # inference window.  On a partial final window it can emit a
            # segment whose start is already beyond the proved PCM duration.
            # Such a segment describes padding, not source audio, and must not
            # enter the canonical transcript contract.
            if math.isfinite(start) and start >= waveform_duration_seconds:
                continue
            if math.isfinite(end) and end > waveform_duration_seconds:
                end = waveform_duration_seconds
            # Faster Whisper timestamps live on a 20 ms grid.  At a long-audio
            # window boundary, adjacent valid segments can overlap by one grid
            # step.  Canonicalize only that documented quantum here; larger
            # overlaps and reversed intervals still reach the strict validator.
            if (
                math.isfinite(start)
                and math.isfinite(end)
                and start < previous_end
                and previous_end - start <= WHISPER_TIMESTAMP_OVERLAP_TOLERANCE_SECONDS
                and end >= previous_end
            ):
                start = previous_end
            if math.isfinite(end):
                previous_end = end
            yield start, end, str(item.text)

    return _bounded_segments()


def _scrub_worker_environment() -> None:
    """Drop inherited application configuration before audio/model handling."""

    preserved = {
        key: value
        for key, value in os.environ.items()
        if key in _WORKER_ENV_ALLOWLIST
    }
    os.environ.clear()
    os.environ.update(preserved)


def _success_payload(
    transcript: CanonicalTranscript,
    language_metadata: dict | None = None,
    *,
    include_language_metadata: bool = True,
) -> dict:
    metadata = language_metadata if type(language_metadata) is dict else {}
    payload = {
        "duration_ms": transcript.duration_ms,
        "segments": [
            {
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
                "text": item.text,
            }
            for item in transcript.segments
        ],
        "transcript_text": transcript.transcript_text,
    }
    if include_language_metadata:
        payload.update({
            "language": metadata.get("language", "und"),
            "language_confidence": metadata.get("language_confidence")
            if type(metadata.get("language_confidence")) is float
            else None,
        })
    return payload


def _encode_worker_message(message: dict) -> bytes:
    try:
        encoded = json.dumps(
            message,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
    except Exception:
        encoded = b'{"error":"WORKER_PROTOCOL_ERROR"}'
    if len(encoded) > MAX_RESULT_JSON_BYTES:
        return b'{"error":"WORKER_PROTOCOL_ERROR"}'
    return encoded


def _marketmatch_stt_child(
    input_connection: Connection,
    result_connection: Connection,
    result_protocol_version: int = 1,
    requested_language: str | None = None,
) -> None:
    """Spawn target. Receive one WAV message and send one bounded JSON message."""

    _scrub_worker_environment()
    try:
        try:
            wav_bytes = input_connection.recv_bytes(MAX_WAV_BYTES)
        except Exception:
            result_connection.send_bytes(
                _encode_worker_message({"error": MarketMatchProcessCode.WORKER_PROTOCOL_ERROR.value})
            )
            return
        finally:
            try:
                input_connection.close()
            except Exception:
                pass

        try:
            language_metadata: dict[str, object] = {}

            def _backend_with_metadata(waveform):
                if requested_language is None:
                    return _fixed_local_base_backend(waveform, language_metadata)
                return _fixed_local_base_backend(waveform, language_metadata, requested_language)

            transcript = transcribe_canonical_wav(
                wav_bytes,
                byte_limit=MAX_WAV_BYTES,
                duration_limit_ms=MAX_DURATION_MS,
                backend=_backend_with_metadata,
                transcript_utf8_limit=MAX_TRANSCRIPT_UTF8_BYTES,
                segment_limit=MAX_SEGMENTS,
            )
            message = _success_payload(
                transcript,
                language_metadata,
                include_language_metadata=result_protocol_version >= 2,
            )
        except CanonicalWavError as error:
            message = {"error": error.code.value}
            if error.result_field is not None and error.result_type is not None:
                message.update({
                    "result_field": error.result_field,
                    "result_type": error.result_type,
                })
        except Exception:
            message = {"error": MarketMatchProcessCode.WORKER_FAILED.value}
        result_connection.send_bytes(_encode_worker_message(message))
    except Exception:
        # A broken result pipe is reported by the parent as a fixed worker error.
        pass
    finally:
        try:
            input_connection.close()
        except Exception:
            pass
        try:
            result_connection.close()
        except Exception:
            pass


def _marketmatch_stt_file_child(
    wav_path: str,
    result_connection: Connection,
    byte_limit: int,
    duration_limit_ms: int,
    requested_language: str | None = None,
) -> None:
    """Spawn target for one trusted temporary canonical-WAV pathname."""

    _scrub_worker_environment()
    try:
        try:
            language_metadata: dict[str, object] = {}

            def _backend_with_metadata(waveform):
                if requested_language is None:
                    return _fixed_local_base_backend(waveform, language_metadata)
                return _fixed_local_base_backend(waveform, language_metadata, requested_language)

            transcript = transcribe_canonical_wav_file(
                Path(wav_path),
                byte_limit=byte_limit,
                duration_limit_ms=duration_limit_ms,
                backend=_backend_with_metadata,
                transcript_utf8_limit=MAX_FILE_TRANSCRIPT_UTF8_BYTES,
                segment_limit=MAX_FILE_SEGMENTS,
            )
            message = _success_payload(transcript, language_metadata)
        except CanonicalWavError as error:
            message = {"error": error.code.value}
            if error.result_field is not None and error.result_type is not None:
                message.update({
                    "result_field": error.result_field,
                    "result_type": error.result_type,
                })
        except Exception:
            message = {"error": MarketMatchProcessCode.WORKER_FAILED.value}
        result_connection.send_bytes(_encode_worker_message(message))
    except Exception:
        pass
    finally:
        try:
            result_connection.close()
        except Exception:
            pass


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        _fail(
            MarketMatchProcessCode.WORKER_TIMEOUT,
            worker_exit_state="timed_out",
        )
    return remaining


async def _await_blocking(call: Callable[[], object], deadline: float) -> object:
    """Run one pipe operation without cancelling its thread on route cancellation."""

    task = asyncio.create_task(asyncio.to_thread(call))
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=_remaining(deadline))
    except asyncio.TimeoutError:
        _fail(
            MarketMatchProcessCode.WORKER_TIMEOUT,
            worker_exit_state="timed_out",
        )
    finally:
        if task.done():
            try:
                task.result()
            except (asyncio.CancelledError, Exception):
                pass
        else:
            def _consume_background_result(completed: asyncio.Task) -> None:
                try:
                    completed.result()
                except (asyncio.CancelledError, Exception):
                    pass

            task.add_done_callback(_consume_background_result)


def _validate_segment(value: object, *, previous_end: int, duration_ms: int) -> tuple[int, int, str]:
    if type(value) is not dict or set(value) != {"start_ms", "end_ms", "text"}:
        _fail(
            MarketMatchProcessCode.WORKER_PROTOCOL_ERROR,
            result_field="segments",
            result_value=value,
        )
    start = value["start_ms"]
    end = value["end_ms"]
    text = value["text"]
    if (
        type(start) is not int
        or type(end) is not int
        or type(text) is not str
        or start < 0
        or end < start
        or end > duration_ms + SEGMENT_END_TOLERANCE_MS
    ):
        field = (
            "segments.start_ms" if type(start) is not int or start < 0
            else "segments.end_ms" if type(end) is not int or end < start or end > duration_ms + SEGMENT_END_TOLERANCE_MS
            else "segments.text"
        )
        _fail(
            MarketMatchProcessCode.WORKER_PROTOCOL_ERROR,
            result_field=field,
            result_value={"segments.start_ms": start, "segments.end_ms": end, "segments.text": text}[field],
        )
    if start < previous_end:
        _fail(
            MarketMatchProcessCode.WORKER_PROTOCOL_ERROR,
            result_field="segments.order",
            result_value=value,
        )
    try:
        text.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        _fail(
            MarketMatchProcessCode.WORKER_PROTOCOL_ERROR,
            result_field="segments.text",
            result_value=text,
        )
    return start, end, text


def _decode_parent_result(
    payload: bytes,
    *,
    duration_limit_ms: int = MAX_DURATION_MS,
    transcript_utf8_limit: int = MAX_TRANSCRIPT_UTF8_BYTES,
    segment_limit: int = MAX_SEGMENTS,
) -> MarketMatchProcessResult:
    if type(payload) is not bytes or not payload or len(payload) > MAX_RESULT_JSON_BYTES:
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
    try:
        value = json.loads(payload.decode("utf-8", errors="strict"))
    except Exception:
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
    if type(value) is not dict:
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
    error_keys = set(value)
    if error_keys in ({"error"}, {"error", "result_field", "result_type"}):
        raw_code = value["error"]
        if type(raw_code) is not str:
            _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
        result_field = value.get("result_field")
        result_type = value.get("result_type")
        if error_keys != {"error"} and (
            result_field not in _SAFE_RESULT_FIELDS or result_type not in _SAFE_RESULT_TYPES
        ):
            _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
        try:
            _fail(
                CanonicalWavCode(raw_code),
                result_field=result_field,
                result_type=result_type,
            )
        except ValueError:
            try:
                _fail(
                    MarketMatchProcessCode(raw_code),
                    result_field=result_field,
                    result_type=result_type,
                )
            except ValueError:
                _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
    legacy_keys = {"duration_ms", "segments", "transcript_text"}
    extended_keys = legacy_keys | {"language", "language_confidence"}
    if set(value) not in (legacy_keys, extended_keys):
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)

    duration_ms = value["duration_ms"]
    supplied_segments = value["segments"]
    transcript_text = value["transcript_text"]
    language = value.get("language", "und")
    language_confidence = value.get("language_confidence")
    if (
        type(duration_ms) is not int
        or not 0 < duration_ms <= duration_limit_ms
    ):
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR, result_field="duration_ms", result_value=duration_ms)
    if type(supplied_segments) is not list or len(supplied_segments) > segment_limit or not supplied_segments:
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR, result_field="segments", result_value=supplied_segments)
    if type(transcript_text) is not str or not transcript_text:
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR, result_field="transcript_text", result_value=transcript_text)
    if language not in {"es", "en", "zh", "und"}:
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR, result_field="language", result_value=language)
    if language_confidence is not None:
        if (
            type(language_confidence) not in (int, float)
            or not math.isfinite(float(language_confidence))
            or not 0.0 <= float(language_confidence) <= 1.0
        ):
            _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR, result_field="language_confidence", result_value=language_confidence)
        language_confidence = float(language_confidence)
    try:
        transcript_size = len(transcript_text.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        _fail(
            MarketMatchProcessCode.WORKER_PROTOCOL_ERROR,
            result_field="transcript_text",
            result_value=transcript_text,
        )
    if transcript_size > transcript_utf8_limit:
        _fail(
            MarketMatchProcessCode.WORKER_PROTOCOL_ERROR,
            result_field="transcript_text",
            result_value=transcript_text,
        )

    segments: list[tuple[int, int, str]] = []
    texts: list[str] = []
    segment_text_size = 0
    previous_end = 0
    for supplied in supplied_segments:
        segment = _validate_segment(
            supplied,
            previous_end=previous_end,
            duration_ms=duration_ms,
        )
        previous_end = segment[1]
        segment_text_size += len(segment[2].encode("utf-8", errors="strict"))
        if segment_text_size > transcript_utf8_limit:
            _fail(
                MarketMatchProcessCode.WORKER_PROTOCOL_ERROR,
                result_field="segments.text",
                result_value=segment[2],
            )
        segments.append(segment)
        texts.append(segment[2])
    if segment_text_size != transcript_size or "".join(texts) != transcript_text:
        _fail(
            MarketMatchProcessCode.WORKER_PROTOCOL_ERROR,
            result_field="transcript_text",
            result_value=transcript_text,
        )
    return MarketMatchProcessResult(
        duration_ms=duration_ms,
        transcript_text=transcript_text,
        segments=tuple(segments),
        language=language,
        language_confidence=language_confidence,
    )


def validate_marketmatch_process_result(
    result: object,
    *,
    duration_limit_ms: int,
    transcript_utf8_limit: int = MAX_FILE_TRANSCRIPT_UTF8_BYTES,
    segment_limit: int = MAX_FILE_SEGMENTS,
) -> MarketMatchProcessResult:
    """Revalidate an in-process result with the exact worker wire contract."""

    if type(result) is not MarketMatchProcessResult:
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
    if type(result.segments) is not tuple:
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
    segments = []
    for segment in result.segments:
        if type(segment) is not tuple or len(segment) != 3:
            _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
        segments.append(
            {"start_ms": segment[0], "end_ms": segment[1], "text": segment[2]}
        )
    payload = {
        "duration_ms": result.duration_ms,
        "segments": segments,
        "transcript_text": result.transcript_text,
        "language": result.language,
        "language_confidence": result.language_confidence,
    }
    return _decode_parent_result(
        _encode_worker_message(payload),
        duration_limit_ms=duration_limit_ms,
        transcript_utf8_limit=transcript_utf8_limit,
        segment_limit=segment_limit,
    )


async def transcribe_in_spawned_process(
    wav_bytes: bytes,
    *,
    deadline: float,
    requested_language: str | None = None,
    _context=None,
    _target=None,
) -> MarketMatchProcessResult:
    """Run one byte-bounded transcription child under an absolute deadline."""

    if type(wav_bytes) is not bytes or not wav_bytes or len(wav_bytes) > MAX_WAV_BYTES:
        _fail(MarketMatchProcessCode.INVALID_INPUT)
    if requested_language not in {None, "es", "en", "zh"}:
        _fail(MarketMatchProcessCode.INVALID_INPUT)
    if type(deadline) not in (int, float) or not math.isfinite(float(deadline)):
        _fail(MarketMatchProcessCode.INVALID_INPUT)
    absolute_deadline = float(deadline)
    if _remaining(absolute_deadline) <= PROCESS_CLEANUP_RESERVE_SECONDS:
        _fail(MarketMatchProcessCode.WORKER_TIMEOUT)
    work_deadline = absolute_deadline - PROCESS_CLEANUP_RESERVE_SECONDS

    target = _target or _marketmatch_stt_child
    connections: tuple[object, ...] = ()
    process = None
    registered = False
    first_payload: bytes | None = None
    result_eof = False
    try:
        with _SPAWN_START_LOCK:
            _repair_invalid_standard_descriptors()
            context = _context or multiprocessing.get_context("spawn")
            input_receive, input_send = context.Pipe(duplex=False)
            result_receive, result_send = context.Pipe(duplex=False)
            connections = (input_receive, input_send, result_receive, result_send)
            target_args = (
                ((input_receive, result_send, 2) if requested_language is None
                 else (input_receive, result_send, 2, requested_language))
                if _target is None else (input_receive, result_send)
            )
            process = context.Process(target=target, args=target_args, daemon=True)
            process.start()
            registered = True
            _register_process(process)
        _remaining(work_deadline)
        input_receive.close()
        result_send.close()

        await _await_blocking(lambda: input_send.send_bytes(wav_bytes), work_deadline)
        input_send.close()

        while True:
            _remaining(work_deadline)
            if not result_eof and result_receive.poll(0):
                try:
                    received = await _await_blocking(
                        lambda: result_receive.recv_bytes(MAX_RESULT_JSON_BYTES),
                        work_deadline,
                    )
                except EOFError:
                    result_eof = True
                    continue
                except OSError:
                    _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
                if type(received) is not bytes:
                    _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
                if first_payload is not None:
                    _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
                first_payload = received
                continue

            process.join(0)
            if not process.is_alive():
                if not result_eof and result_receive.poll(0):
                    continue
                break
            await asyncio.sleep(min(PROCESS_POLL_INTERVAL_SECONDS, _remaining(work_deadline)))

        process.join()
        if process.exitcode != 0:
            _fail(
                MarketMatchProcessCode.WORKER_CRASHED,
                worker_exit_state="nonzero_exit",
            )
        if first_payload is None:
            _fail(
                MarketMatchProcessCode.WORKER_FAILED,
                worker_exit_state="clean_exit",
            )
        try:
            return _decode_parent_result(first_payload)
        except MarketMatchProcessError as error:
            raise _with_worker_exit_state(error, "clean_exit") from None
    except asyncio.CancelledError:
        raise
    except MarketMatchProcessError:
        raise
    except Exception:
        _fail(MarketMatchProcessCode.WORKER_FAILED)
    finally:
        for connection in connections:
            try:
                connection.close()
            except Exception:
                pass
        if process is not None:
            _terminate_and_reap(process)
        if registered:
            _unregister_process(process)


async def transcribe_canonical_file_in_spawned_process(
    wav_path: Path,
    *,
    byte_limit: int,
    duration_limit_ms: int,
    deadline: float,
    requested_language: str | None = None,
    _context=None,
    _target=None,
) -> MarketMatchProcessResult:
    """Transcribe a generated file in a killable local worker process."""

    if (
        not isinstance(wav_path, Path)
        or not wav_path.is_absolute()
        or type(byte_limit) is not int
        or byte_limit <= 44
        or type(duration_limit_ms) is not int
        or duration_limit_ms <= 0
        or type(deadline) not in (int, float)
        or not math.isfinite(float(deadline))
        or requested_language not in {None, "es", "en", "zh"}
    ):
        _fail(MarketMatchProcessCode.INVALID_INPUT)
    absolute_deadline = float(deadline)
    if _remaining(absolute_deadline) <= PROCESS_CLEANUP_RESERVE_SECONDS:
        _fail(MarketMatchProcessCode.WORKER_TIMEOUT)
    work_deadline = absolute_deadline - PROCESS_CLEANUP_RESERVE_SECONDS
    target = _target or _marketmatch_stt_file_child
    connections: tuple[object, ...] = ()
    process = None
    registered = False
    first_payload: bytes | None = None
    result_eof = False
    try:
        with _SPAWN_START_LOCK:
            _repair_invalid_standard_descriptors()
            context = _context or multiprocessing.get_context("spawn")
            result_receive, result_send = context.Pipe(duplex=False)
            connections = (result_receive, result_send)
            args = (
                ((os.fspath(wav_path), result_send, byte_limit, duration_limit_ms)
                 if requested_language is None else
                 (os.fspath(wav_path), result_send, byte_limit, duration_limit_ms, requested_language))
                if _target is None else (os.fspath(wav_path), result_send)
            )
            process = context.Process(target=target, args=args, daemon=True)
            process.start()
            registered = True
            _register_process(process)
        result_send.close()
        while True:
            _remaining(work_deadline)
            if not result_eof and result_receive.poll(0):
                try:
                    received = await _await_blocking(
                        lambda: result_receive.recv_bytes(MAX_RESULT_JSON_BYTES), work_deadline
                    )
                except EOFError:
                    result_eof = True
                    continue
                except OSError:
                    _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
                if first_payload is not None or type(received) is not bytes:
                    _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
                first_payload = received
                continue
            process.join(0)
            if not process.is_alive():
                if not result_eof and result_receive.poll(0):
                    continue
                break
            await asyncio.sleep(min(PROCESS_POLL_INTERVAL_SECONDS, _remaining(work_deadline)))
        process.join()
        if process.exitcode != 0:
            _fail(
                MarketMatchProcessCode.WORKER_CRASHED,
                worker_exit_state="nonzero_exit",
            )
        if first_payload is None:
            _fail(
                MarketMatchProcessCode.WORKER_FAILED,
                worker_exit_state="clean_exit",
            )
        try:
            return _decode_parent_result(
                first_payload,
                duration_limit_ms=duration_limit_ms,
                transcript_utf8_limit=MAX_FILE_TRANSCRIPT_UTF8_BYTES,
                segment_limit=MAX_FILE_SEGMENTS,
            )
        except MarketMatchProcessError as error:
            raise _with_worker_exit_state(error, "clean_exit") from None
    except asyncio.CancelledError:
        raise
    except MarketMatchProcessError:
        raise
    except Exception:
        _fail(MarketMatchProcessCode.WORKER_FAILED)
    finally:
        for connection in connections:
            try:
                connection.close()
            except Exception:
                pass
        if process is not None:
            _terminate_and_reap(process)
        if registered:
            _unregister_process(process)


__all__ = (
    "FASTER_WHISPER_BASE_REVISION",
    "MAX_RESULT_JSON_BYTES",
    "PROCESS_CLEANUP_RESERVE_SECONDS",
    "MarketMatchProcessCode",
    "MarketMatchProcessError",
    "MarketMatchProcessResult",
    "active_worker_count",
    "release_admission",
    "shutdown_active_workers",
    "transcribe_in_spawned_process",
    "transcribe_canonical_file_in_spawned_process",
    "try_acquire_admission",
    "validate_marketmatch_process_result",
)
