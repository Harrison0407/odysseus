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
from src.marketmatch_i18n import normalize_transcript_language


MAX_RESULT_JSON_BYTES = 4 * 1024 * 1024
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
    WORKER_FAILED = "WORKER_FAILED"
    WORKER_PROTOCOL_ERROR = "WORKER_PROTOCOL_ERROR"


class MarketMatchProcessError(Exception):
    """Fixed process-boundary failure that never embeds worker or media data."""

    def __init__(self, code: MarketMatchProcessCode | CanonicalWavCode):
        if type(code) not in (MarketMatchProcessCode, CanonicalWavCode):
            code = MarketMatchProcessCode.WORKER_FAILED
        self.code = code
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


def _fail(code: MarketMatchProcessCode | CanonicalWavCode) -> NoReturn:
    raise MarketMatchProcessError(code) from None


def _fixed_local_base_backend(waveform, language_metadata: dict | None = None):
    """Return exact tuples from the provisioned local faster-whisper base model."""

    from faster_whisper import WhisperModel

    model = WhisperModel(
        "base",
        device="cpu",
        compute_type="int8",
        num_workers=1,
        local_files_only=True,
        revision=FASTER_WHISPER_BASE_REVISION,
    )
    segments, info = model.transcribe(waveform)
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
        for item in segments:
            start = float(item.start)
            end = float(item.end)
            if math.isfinite(end) and end > waveform_duration_seconds:
                end = waveform_duration_seconds
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
            "language": normalize_transcript_language(metadata.get("language")),
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
        encoded = b'{"error":"WORKER_FAILED"}'
    if len(encoded) > MAX_RESULT_JSON_BYTES:
        return b'{"error":"WORKER_PROTOCOL_ERROR"}'
    return encoded


def _marketmatch_stt_child(
    input_connection: Connection,
    result_connection: Connection,
    result_protocol_version: int = 1,
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
                return _fixed_local_base_backend(waveform, language_metadata)

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


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        _fail(MarketMatchProcessCode.WORKER_TIMEOUT)
    return remaining


async def _await_blocking(call: Callable[[], object], deadline: float) -> object:
    """Run one pipe operation without cancelling its thread on route cancellation."""

    task = asyncio.create_task(asyncio.to_thread(call))
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=_remaining(deadline))
    except asyncio.TimeoutError:
        _fail(MarketMatchProcessCode.WORKER_TIMEOUT)
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
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
    start = value["start_ms"]
    end = value["end_ms"]
    text = value["text"]
    if (
        type(start) is not int
        or type(end) is not int
        or type(text) is not str
        or start < previous_end
        or start < 0
        or end < start
        or end > duration_ms + SEGMENT_END_TOLERANCE_MS
    ):
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
    try:
        text.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
    return start, end, text


def _decode_parent_result(payload: bytes) -> MarketMatchProcessResult:
    if type(payload) is not bytes or not payload or len(payload) > MAX_RESULT_JSON_BYTES:
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
    try:
        value = json.loads(payload.decode("utf-8", errors="strict"))
    except Exception:
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
    if type(value) is not dict:
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
    if set(value) == {"error"}:
        raw_code = value["error"]
        if type(raw_code) is not str:
            _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
        try:
            _fail(CanonicalWavCode(raw_code))
        except ValueError:
            try:
                _fail(MarketMatchProcessCode(raw_code))
            except ValueError:
                _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
    legacy_keys = {"duration_ms", "segments", "transcript_text"}
    extended_keys = legacy_keys | {"language", "language_confidence"}
    if set(value) not in (legacy_keys, extended_keys):
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)

    duration_ms = value["duration_ms"]
    supplied_segments = value["segments"]
    transcript_text = value["transcript_text"]
    language = normalize_transcript_language(value.get("language"))
    language_confidence = value.get("language_confidence")
    if (
        type(duration_ms) is not int
        or not 0 < duration_ms <= MAX_DURATION_MS
        or type(supplied_segments) is not list
        or len(supplied_segments) > MAX_SEGMENTS
        or type(transcript_text) is not str
    ):
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
    if language_confidence is not None:
        if (
            type(language_confidence) not in (int, float)
            or not math.isfinite(float(language_confidence))
            or not 0.0 <= float(language_confidence) <= 1.0
        ):
            _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
        language_confidence = float(language_confidence)
    try:
        transcript_size = len(transcript_text.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
    if transcript_size > MAX_TRANSCRIPT_UTF8_BYTES:
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)

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
        if segment_text_size > MAX_TRANSCRIPT_UTF8_BYTES:
            _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
        segments.append(segment)
        texts.append(segment[2])
    if segment_text_size != transcript_size or "".join(texts) != transcript_text:
        _fail(MarketMatchProcessCode.WORKER_PROTOCOL_ERROR)
    return MarketMatchProcessResult(
        duration_ms=duration_ms,
        transcript_text=transcript_text,
        segments=tuple(segments),
        language=language,
        language_confidence=language_confidence,
    )


async def transcribe_in_spawned_process(
    wav_bytes: bytes,
    *,
    deadline: float,
    _context=None,
    _target=None,
) -> MarketMatchProcessResult:
    """Run one byte-bounded transcription child under an absolute deadline."""

    if type(wav_bytes) is not bytes or not wav_bytes or len(wav_bytes) > MAX_WAV_BYTES:
        _fail(MarketMatchProcessCode.INVALID_INPUT)
    if type(deadline) not in (int, float) or not math.isfinite(float(deadline)):
        _fail(MarketMatchProcessCode.INVALID_INPUT)
    absolute_deadline = float(deadline)
    if _remaining(absolute_deadline) <= PROCESS_CLEANUP_RESERVE_SECONDS:
        _fail(MarketMatchProcessCode.WORKER_TIMEOUT)
    work_deadline = absolute_deadline - PROCESS_CLEANUP_RESERVE_SECONDS

    context = _context or multiprocessing.get_context("spawn")
    target = _target or _marketmatch_stt_child
    input_receive, input_send = context.Pipe(duplex=False)
    result_receive, result_send = context.Pipe(duplex=False)
    target_args = (
        (input_receive, result_send, 2)
        if _target is None
        else (input_receive, result_send)
    )
    process = context.Process(target=target, args=target_args, daemon=True)
    registered = False
    first_payload: bytes | None = None
    result_eof = False
    try:
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
            _fail(MarketMatchProcessCode.WORKER_FAILED)
        if first_payload is None:
            _fail(MarketMatchProcessCode.WORKER_FAILED)
        return _decode_parent_result(first_payload)
    except asyncio.CancelledError:
        raise
    except MarketMatchProcessError:
        raise
    except Exception:
        _fail(MarketMatchProcessCode.WORKER_FAILED)
    finally:
        for connection in (input_receive, input_send, result_receive, result_send):
            try:
                connection.close()
            except Exception:
                pass
        if registered:
            _terminate_and_reap(process)
            _unregister_process(process)
        else:
            try:
                process.close()
            except Exception:
                pass


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
    "try_acquire_admission",
)
