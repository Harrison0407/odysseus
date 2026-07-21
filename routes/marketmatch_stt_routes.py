"""Cookie-authenticated bounded raw-media ingress for MarketMatch Capture."""

from __future__ import annotations

import asyncio
from enum import Enum
import logging
import math
import os
from pathlib import Path
import time
from typing import Awaitable, Callable, NoReturn

from fastapi import APIRouter, Request
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse

from core.auth import RESERVED_USERNAMES, normalize_known_username
from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_USER
from routes.auth_routes import SESSION_COOKIE
from src.marketmatch_audio import (
    MARKETMATCH_AUDIO_MAX_DURATION_SECONDS,
    MarketMatchAudioCode,
    MarketMatchAudioError,
    canonical_wav_max_bytes,
    cleanup_private_workdir,
    create_private_workdir,
    prepare_canonical_audio,
)
from src.marketmatch_canonical_wav import CanonicalWavCode, CanonicalWavError
from src.marketmatch_canonical_wav_file import canonical_wav_file_duration_ms
from src.marketmatch_stt_process import (
    MarketMatchProcessCode,
    MarketMatchProcessError,
    MarketMatchProcessResult,
    release_admission,
    transcribe_canonical_file_in_spawned_process,
    try_acquire_admission,
    validate_marketmatch_process_result,
)
from src.upload_limits import MARKETMATCH_CALL_AUDIO_MAX_BYTES


def _timeout_env(name: str, default: float, *, maximum: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive number")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} exceeds its hard maximum")
    return value


MARKETMATCH_STT_ROUTE = "/api/marketmatch/stt/transcribe"
MARKETMATCH_STT_TIMEOUT_HARD_MAX_SECONDS = 28_800.0
MARKETMATCH_STT_UPLOAD_TIMEOUT_HARD_MAX_SECONDS = 3_600.0
MARKETMATCH_STT_TIMEOUT_MIN_SECONDS = _timeout_env(
    "MARKETMATCH_STT_TIMEOUT_MIN_SECONDS", 600.0,
    maximum=MARKETMATCH_STT_TIMEOUT_HARD_MAX_SECONDS,
)
MARKETMATCH_STT_TIMEOUT_SECONDS_PER_AUDIO_SECOND = _timeout_env(
    "MARKETMATCH_STT_TIMEOUT_SECONDS_PER_AUDIO_SECOND", 1.0,
    maximum=60.0,
)
# Backward compatible: the existing setting is the configurable worker-policy
# maximum, not a fixed deadline applied equally to every audio duration.
MARKETMATCH_STT_TIMEOUT_SECONDS = _timeout_env(
    "MARKETMATCH_STT_TIMEOUT_SECONDS", 28_800.0,
    maximum=MARKETMATCH_STT_TIMEOUT_HARD_MAX_SECONDS,
)
MARKETMATCH_STT_READ_TIMEOUT_SECONDS = _timeout_env(
    "MARKETMATCH_STT_UPLOAD_TIMEOUT_SECONDS", 600.0,
    maximum=MARKETMATCH_STT_UPLOAD_TIMEOUT_HARD_MAX_SECONDS,
)
if MARKETMATCH_STT_TIMEOUT_MIN_SECONDS > MARKETMATCH_STT_TIMEOUT_SECONDS:
    raise ValueError("MARKETMATCH_STT_TIMEOUT_MIN_SECONDS exceeds MARKETMATCH_STT_TIMEOUT_SECONDS")

_LOGGER = logging.getLogger("marketmatch.stt")


def transcription_timeout_seconds(duration_ms: int) -> float:
    """Return the deterministic bounded local-worker budget for proved audio."""

    if type(duration_ms) is not int or not 0 < duration_ms <= int(
        MARKETMATCH_AUDIO_MAX_DURATION_SECONDS * 1_000
    ):
        raise ValueError("duration_ms is outside the admitted audio duration")
    scaled = duration_ms / 1_000 * MARKETMATCH_STT_TIMEOUT_SECONDS_PER_AUDIO_SECOND
    return min(
        MARKETMATCH_STT_TIMEOUT_SECONDS,
        max(MARKETMATCH_STT_TIMEOUT_MIN_SECONDS, scaled),
    )


def _safe_failure_stage(code: str, current_stage: str) -> str:
    if code in {
        CanonicalWavCode.INVALID_BACKEND_RESULT.value,
        CanonicalWavCode.SEGMENT_LIMIT_EXCEEDED.value,
        CanonicalWavCode.TRANSCRIPT_LIMIT_EXCEEDED.value,
        MarketMatchProcessCode.WORKER_PROTOCOL_ERROR.value,
    }:
        return "worker_result_validation"
    if code == MarketMatchProcessCode.WORKER_TIMEOUT.value:
        return "transcription"
    if code in {
        MarketMatchProcessCode.WORKER_CRASHED.value,
        MarketMatchProcessCode.WORKER_FAILED.value,
        CanonicalWavCode.BACKEND_FAILED.value,
        CanonicalWavCode.MODEL_UNAVAILABLE.value,
    }:
        return "worker"
    if code == MarketMatchAudioCode.AUDIO_PROBE_TIMEOUT.value:
        return "probe"
    if code in {
        MarketMatchAudioCode.CONVERSION_TIMEOUT.value,
        MarketMatchAudioCode.CONVERSION_FAILED.value,
    }:
        return "conversion"
    return current_stage


def _log_safe_failure(
    *,
    code: str,
    stage: str,
    started: float,
    audio_duration_ms: int | None,
    worker_exit_state: str | None = None,
    result_field: str | None = None,
    result_type: str | None = None,
) -> None:
    _LOGGER.warning(
        "marketmatch_stt_failure stage=%s code=%s elapsed=%.3fs "
        "audio_duration_ms=%s worker_exit_state=%s result_field=%s result_type=%s",
        _safe_failure_stage(code, stage),
        code,
        max(0.0, time.monotonic() - started),
        audio_duration_ms if audio_duration_ms is not None else "unknown",
        worker_exit_state or "not_started",
        result_field or "none",
        result_type or "none",
    )


class MarketMatchRouteCode(str, Enum):
    AUTH_DISABLED = "AUTH_DISABLED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_METHOD_REJECTED = "AUTH_METHOD_REJECTED"
    AUTH_STATE_UNAVAILABLE = "AUTH_STATE_UNAVAILABLE"
    MARKETMATCH_FORBIDDEN = "MARKETMATCH_FORBIDDEN"
    TRANSCRIPTION_BUSY = "TRANSCRIPTION_BUSY"
    INVALID_CONTENT_TYPE = "INVALID_CONTENT_TYPE"
    CONTENT_ENCODING_REJECTED = "CONTENT_ENCODING_REJECTED"
    INVALID_CONTENT_LENGTH = "INVALID_CONTENT_LENGTH"
    INPUT_LIMIT_EXCEEDED = "INPUT_LIMIT_EXCEEDED"
    INPUT_SIZE_MISMATCH = "INPUT_SIZE_MISMATCH"
    EMPTY_INPUT = "EMPTY_INPUT"
    INPUT_DISCONNECTED = "INPUT_DISCONNECTED"
    INPUT_READ_FAILED = "INPUT_READ_FAILED"
    INPUT_READ_TIMEOUT = "INPUT_READ_TIMEOUT"
    INVALID_TRANSCRIPTION_LANGUAGE = "INVALID_TRANSCRIPTION_LANGUAGE"


class MarketMatchRouteError(Exception):
    def __init__(self, code: MarketMatchRouteCode):
        if type(code) is not MarketMatchRouteCode:
            code = MarketMatchRouteCode.INPUT_READ_FAILED
        self.code = code
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r})"


_ERROR_RESPONSES: dict[str, tuple[int, str]] = {
    MarketMatchRouteCode.AUTH_DISABLED.value: (403, "Authenticated mode is required."),
    MarketMatchRouteCode.AUTH_REQUIRED.value: (401, "Cookie authentication is required."),
    MarketMatchRouteCode.AUTH_METHOD_REJECTED.value: (403, "Cookie authentication is required."),
    MarketMatchRouteCode.AUTH_STATE_UNAVAILABLE.value: (403, "Authorization state is unavailable."),
    MarketMatchRouteCode.MARKETMATCH_FORBIDDEN.value: (403, "MarketMatch access is not allowed."),
    MarketMatchRouteCode.TRANSCRIPTION_BUSY.value: (429, "A transcription is already active."),
    MarketMatchRouteCode.INVALID_CONTENT_TYPE.value: (415, "Audio content type is not supported."),
    MarketMatchRouteCode.CONTENT_ENCODING_REJECTED.value: (415, "Content-Encoding is not supported."),
    MarketMatchRouteCode.INVALID_CONTENT_LENGTH.value: (400, "Content-Length is invalid."),
    MarketMatchRouteCode.INPUT_LIMIT_EXCEEDED.value: (413, "Audio input exceeds 200 MiB."),
    MarketMatchRouteCode.INPUT_SIZE_MISMATCH.value: (400, "Audio input size does not match Content-Length."),
    MarketMatchRouteCode.EMPTY_INPUT.value: (400, "Audio input is empty."),
    MarketMatchRouteCode.INPUT_DISCONNECTED.value: (400, "Audio input was interrupted."),
    MarketMatchRouteCode.INPUT_READ_FAILED.value: (400, "Audio input could not be read."),
    MarketMatchRouteCode.INPUT_READ_TIMEOUT.value: (408, "Audio input timed out."),
    MarketMatchRouteCode.INVALID_TRANSCRIPTION_LANGUAGE.value: (422, "Transcription language is invalid."),
    CanonicalWavCode.INVALID_INPUT.value: (400, "Audio input is invalid."),
    CanonicalWavCode.INPUT_LIMIT_EXCEEDED.value: (413, "Decoded audio exceeds the configured limit."),
    CanonicalWavCode.INVALID_WAV.value: (422, "Audio input is not a canonical WAV."),
    CanonicalWavCode.UNSUPPORTED_WAV_FORMAT.value: (415, "WAV format is not supported."),
    CanonicalWavCode.DURATION_LIMIT_EXCEEDED.value: (413, "Audio duration exceeds the configured limit."),
    CanonicalWavCode.INVALID_BACKEND_RESULT.value: (502, "Transcription result was invalid."),
    CanonicalWavCode.SEGMENT_LIMIT_EXCEEDED.value: (502, "Transcription result exceeded the segment limit."),
    CanonicalWavCode.TRANSCRIPT_LIMIT_EXCEEDED.value: (502, "Transcription result exceeded the text limit."),
    CanonicalWavCode.MODEL_UNAVAILABLE.value: (503, "The local transcription model is unavailable."),
    CanonicalWavCode.BACKEND_FAILED.value: (503, "Local transcription failed."),
    MarketMatchProcessCode.INVALID_INPUT.value: (400, "Audio input is invalid."),
    MarketMatchProcessCode.WORKER_TIMEOUT.value: (504, "Local transcription timed out."),
    MarketMatchProcessCode.WORKER_CRASHED.value: (503, "The local transcription worker stopped unexpectedly."),
    MarketMatchProcessCode.WORKER_FAILED.value: (503, "Local transcription failed."),
    MarketMatchProcessCode.WORKER_PROTOCOL_ERROR.value: (502, "Transcription result was invalid."),
    MarketMatchAudioCode.FFMPEG_UNAVAILABLE.value: (503, "Local FFmpeg is unavailable."),
    MarketMatchAudioCode.UNSUPPORTED_FORMAT.value: (415, "Audio format or codec is not supported."),
    MarketMatchAudioCode.MALFORMED_AUDIO.value: (422, "Audio container is malformed or incomplete."),
    MarketMatchAudioCode.NO_AUDIO_STREAM.value: (422, "The file contains no audio stream."),
    MarketMatchAudioCode.EXCESSIVE_STREAMS.value: (422, "The media contains too many streams."),
    MarketMatchAudioCode.EXTERNAL_MEDIA_REJECTED.value: (415, "External media references are not allowed."),
    MarketMatchAudioCode.DURATION_LIMIT_EXCEEDED.value: (413, "Audio duration exceeds the configured limit."),
    MarketMatchAudioCode.DECODED_OUTPUT_LIMIT_EXCEEDED.value: (413, "Decoded audio exceeds the configured limit."),
    MarketMatchAudioCode.CONVERSION_FAILED.value: (422, "Local audio conversion failed."),
    MarketMatchAudioCode.AUDIO_PROBE_TIMEOUT.value: (504, "Local audio inspection timed out."),
    MarketMatchAudioCode.CONVERSION_TIMEOUT.value: (504, "Local audio conversion timed out."),
}


def _fail(code: MarketMatchRouteCode) -> NoReturn:
    raise MarketMatchRouteError(code) from None


def _error_response(code: str) -> JSONResponse:
    status, message = _ERROR_RESPONSES.get(
        code,
        (503, "Local transcription failed."),
    )
    return JSONResponse(
        status_code=status,
        content={"error": code if code in _ERROR_RESPONSES else MarketMatchProcessCode.WORKER_FAILED.value, "message": message},
    )


def _require_marketmatch_cookie_user(request: Request) -> str:
    """Prove a configured cookie identity and explicit stored pilot privilege."""

    if os.getenv("AUTH_ENABLED", "true").lower() == "false":
        _fail(MarketMatchRouteCode.AUTH_DISABLED)
    auth_manager = getattr(request.app.state, "auth_manager", None)
    if auth_manager is None or getattr(auth_manager, "is_configured", False) is not True:
        _fail(MarketMatchRouteCode.AUTH_STATE_UNAVAILABLE)
    if request.headers.get("authorization") is not None or bool(
        getattr(request.state, "api_token", False)
    ):
        _fail(MarketMatchRouteCode.AUTH_METHOD_REJECTED)
    if (
        request.headers.get(INTERNAL_TOOL_HEADER) is not None
        or getattr(request.state, "current_user", None) == INTERNAL_TOOL_USER
    ):
        _fail(MarketMatchRouteCode.AUTH_METHOD_REJECTED)

    token = request.cookies.get(SESSION_COOKIE)
    if type(token) is not str or not token:
        _fail(MarketMatchRouteCode.AUTH_REQUIRED)
    try:
        users = auth_manager.users
        cookie_user = auth_manager.get_username_for_token(token)
    except Exception:
        _fail(MarketMatchRouteCode.AUTH_STATE_UNAVAILABLE)
    if type(users) is not dict:
        _fail(MarketMatchRouteCode.AUTH_STATE_UNAVAILABLE)
    known_user = normalize_known_username(users, cookie_user)
    if not known_user or known_user in RESERVED_USERNAMES:
        _fail(MarketMatchRouteCode.AUTH_REQUIRED)

    state_user = getattr(request.state, "current_user", None)
    normalized_state_user = normalize_known_username(users, state_user)
    if normalized_state_user != known_user or state_user != known_user:
        _fail(MarketMatchRouteCode.AUTH_METHOD_REJECTED)

    user_record = users.get(known_user)
    if type(user_record) is not dict:
        _fail(MarketMatchRouteCode.AUTH_STATE_UNAVAILABLE)
    stored_privileges = user_record.get("privileges")
    if type(stored_privileges) is not dict or "can_use_marketmatch" not in stored_privileges:
        _fail(MarketMatchRouteCode.AUTH_STATE_UNAVAILABLE)
    try:
        effective_privileges = auth_manager.get_privileges(known_user)
    except Exception:
        _fail(MarketMatchRouteCode.AUTH_STATE_UNAVAILABLE)
    if type(effective_privileges) is not dict:
        _fail(MarketMatchRouteCode.AUTH_STATE_UNAVAILABLE)
    if (
        stored_privileges.get("can_use_marketmatch") is not True
        or effective_privileges.get("can_use_marketmatch") is not True
    ):
        _fail(MarketMatchRouteCode.MARKETMATCH_FORBIDDEN)
    return known_user


def _validated_ingress_headers(request: Request) -> int | None:
    content_types = request.headers.getlist("content-type")
    if len(content_types) != 1:
        _fail(MarketMatchRouteCode.INVALID_CONTENT_TYPE)
    content_type = content_types[0]
    normalized_type = content_type.split(";", 1)[0].strip().lower() if type(content_type) is str else ""
    if not (
        normalized_type.startswith("audio/")
        or normalized_type in {
            "application/octet-stream", "video/mp4", "video/quicktime", "video/webm"
        }
    ):
        _fail(MarketMatchRouteCode.INVALID_CONTENT_TYPE)
    if request.headers.get("content-encoding") is not None:
        _fail(MarketMatchRouteCode.CONTENT_ENCODING_REJECTED)
    raw_lengths = request.headers.getlist("content-length")
    if not raw_lengths:
        return None
    if len(raw_lengths) != 1:
        _fail(MarketMatchRouteCode.INVALID_CONTENT_LENGTH)
    raw_length = raw_lengths[0]
    if type(raw_length) is not str or not raw_length.isascii() or not raw_length.isdigit():
        _fail(MarketMatchRouteCode.INVALID_CONTENT_LENGTH)
    try:
        content_length = int(raw_length, 10)
    except (ValueError, OverflowError):
        _fail(MarketMatchRouteCode.INVALID_CONTENT_LENGTH)
    if content_length < 0:
        _fail(MarketMatchRouteCode.INVALID_CONTENT_LENGTH)
    if content_length == 0:
        _fail(MarketMatchRouteCode.EMPTY_INPUT)
    if content_length > MARKETMATCH_CALL_AUDIO_MAX_BYTES:
        _fail(MarketMatchRouteCode.INPUT_LIMIT_EXCEEDED)
    return content_length


def _requested_transcription_language(request: Request) -> str | None:
    values = request.headers.getlist("x-marketmatch-transcription-language")
    if not values:
        return None
    if len(values) != 1 or values[0] not in {"auto", "es", "en", "zh"}:
        _fail(MarketMatchRouteCode.INVALID_TRANSCRIPTION_LANGUAGE)
    return None if values[0] == "auto" else values[0]


async def _stream_raw_audio(
    request: Request,
    *,
    destination: Path,
    expected_size: int | None,
    deadline: float,
) -> int:
    observed = 0

    async def _consume() -> int:
        nonlocal observed
        try:
            with destination.open("xb") as output:
                async for chunk in request.stream():
                    if type(chunk) is not bytes:
                        _fail(MarketMatchRouteCode.INPUT_READ_FAILED)
                    if not chunk:
                        continue
                    if len(chunk) > MARKETMATCH_CALL_AUDIO_MAX_BYTES - observed:
                        observed = MARKETMATCH_CALL_AUDIO_MAX_BYTES + 1
                        _fail(MarketMatchRouteCode.INPUT_LIMIT_EXCEEDED)
                    output.write(chunk)
                    observed += len(chunk)
                    if expected_size is not None and observed > expected_size:
                        _fail(MarketMatchRouteCode.INPUT_SIZE_MISMATCH)
                output.flush()
        except MarketMatchRouteError:
            raise
        except ClientDisconnect:
            _fail(MarketMatchRouteCode.INPUT_DISCONNECTED)
        except Exception:
            _fail(MarketMatchRouteCode.INPUT_READ_FAILED)
        if observed == 0:
            _fail(MarketMatchRouteCode.EMPTY_INPUT)
        if expected_size is not None and observed != expected_size:
            _fail(MarketMatchRouteCode.INPUT_SIZE_MISMATCH)
        return observed

    remaining_total = deadline - time.monotonic()
    if not math.isfinite(remaining_total) or remaining_total <= 0:
        _fail(MarketMatchRouteCode.INPUT_READ_TIMEOUT)
    read_timeout = min(MARKETMATCH_STT_READ_TIMEOUT_SECONDS, remaining_total)
    try:
        async with asyncio.timeout(read_timeout):
            return await _consume()
    except TimeoutError:
        _fail(MarketMatchRouteCode.INPUT_READ_TIMEOUT)


def _success_response(result: MarketMatchProcessResult) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={
            "duration_ms": result.duration_ms,
            "segments": [
                {"start_ms": start, "end_ms": end, "text": text}
                for start, end, text in result.segments
            ],
            "transcript_text": result.transcript_text,
            "language": result.language,
            "language_confidence": result.language_confidence,
        },
    )


def setup_marketmatch_stt_routes(
    transcriber: Callable[..., Awaitable[MarketMatchProcessResult]] = transcribe_canonical_file_in_spawned_process,
) -> APIRouter:
    router = APIRouter(tags=["marketmatch-stt"])

    @router.post(MARKETMATCH_STT_ROUTE)
    async def transcribe_marketmatch_call(request: Request):
        admission_lease: object | None = None
        started = time.monotonic()
        stage = "authorization"
        audio_duration_ms: int | None = None
        try:
            _require_marketmatch_cookie_user(request)
            requested_language = _requested_transcription_language(request)
            admission_lease = try_acquire_admission()
            if admission_lease is None:
                _fail(MarketMatchRouteCode.TRANSCRIPTION_BUSY)
            expected_size = _validated_ingress_headers(request)
            workspace = create_private_workdir()
            try:
                workdir = Path(workspace.name)
                encoded_path = workdir / "encoded.input"
                stage = "upload"
                upload_deadline = time.monotonic() + MARKETMATCH_STT_READ_TIMEOUT_SECONDS
                await _stream_raw_audio(
                    request,
                    destination=encoded_path,
                    expected_size=expected_size,
                    deadline=upload_deadline,
                )
                stage = "conversion"
                canonical_path = await prepare_canonical_audio(encoded_path, workdir)
                duration_limit_ms = int(MARKETMATCH_AUDIO_MAX_DURATION_SECONDS * 1_000)
                byte_limit = canonical_wav_max_bytes(MARKETMATCH_AUDIO_MAX_DURATION_SECONDS)
                stage = "canonical_validation"
                audio_duration_ms = canonical_wav_file_duration_ms(
                    canonical_path,
                    byte_limit=byte_limit,
                    duration_limit_ms=duration_limit_ms,
                )
                worker_timeout = transcription_timeout_seconds(audio_duration_ms)
                stage = "transcription"
                transcriber_kwargs = {
                    "byte_limit": byte_limit,
                    "duration_limit_ms": duration_limit_ms,
                    "deadline": time.monotonic() + worker_timeout,
                }
                if requested_language is not None:
                    transcriber_kwargs["requested_language"] = requested_language
                raw_result = await transcriber(canonical_path, **transcriber_kwargs)
                stage = "result_validation"
                result = validate_marketmatch_process_result(
                    raw_result,
                    duration_limit_ms=duration_limit_ms,
                )
            finally:
                cleanup_private_workdir(workspace)
            stage = "response"
            return _success_response(result)
        except MarketMatchRouteError as error:
            _log_safe_failure(
                code=error.code.value,
                stage=stage,
                started=started,
                audio_duration_ms=audio_duration_ms,
            )
            return _error_response(error.code.value)
        except MarketMatchProcessError as error:
            _log_safe_failure(
                code=error.code.value,
                stage=stage,
                started=started,
                audio_duration_ms=audio_duration_ms,
                worker_exit_state=error.worker_exit_state,
                result_field=error.result_field,
                result_type=error.result_type,
            )
            return _error_response(error.code.value)
        except CanonicalWavError as error:
            _log_safe_failure(
                code=error.code.value,
                stage=stage,
                started=started,
                audio_duration_ms=audio_duration_ms,
                result_field=error.result_field,
                result_type=error.result_type,
            )
            return _error_response(error.code.value)
        except MarketMatchAudioError as error:
            _log_safe_failure(
                code=error.code.value,
                stage=stage,
                started=started,
                audio_duration_ms=audio_duration_ms,
            )
            return _error_response(error.code.value)
        except TimeoutError:
            _log_safe_failure(
                code=MarketMatchProcessCode.WORKER_TIMEOUT.value,
                stage=stage,
                started=started,
                audio_duration_ms=audio_duration_ms,
                worker_exit_state="timed_out",
            )
            return _error_response(MarketMatchProcessCode.WORKER_TIMEOUT.value)
        except asyncio.CancelledError:
            _log_safe_failure(
                code="TRANSCRIPTION_CANCELLED",
                stage=stage,
                started=started,
                audio_duration_ms=audio_duration_ms,
                worker_exit_state="cancelled",
            )
            raise
        except Exception:
            _log_safe_failure(
                code=MarketMatchProcessCode.WORKER_FAILED.value,
                stage=stage,
                started=started,
                audio_duration_ms=audio_duration_ms,
            )
            return _error_response(MarketMatchProcessCode.WORKER_FAILED.value)
        finally:
            if admission_lease is not None:
                release_admission(admission_lease)

    return router


__all__ = (
    "MARKETMATCH_STT_READ_TIMEOUT_SECONDS",
    "MARKETMATCH_STT_ROUTE",
    "MARKETMATCH_STT_TIMEOUT_MIN_SECONDS",
    "MARKETMATCH_STT_TIMEOUT_SECONDS",
    "MARKETMATCH_STT_TIMEOUT_SECONDS_PER_AUDIO_SECOND",
    "MarketMatchRouteCode",
    "setup_marketmatch_stt_routes",
    "transcription_timeout_seconds",
)
