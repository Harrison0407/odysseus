"""Cookie-only raw-WAV route for the controlled MarketMatch Calls pilot."""

from __future__ import annotations

import asyncio
from enum import Enum
import math
import os
import time
from typing import Awaitable, Callable, NoReturn

from fastapi import APIRouter, Request
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse

from core.auth import RESERVED_USERNAMES, normalize_known_username
from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_USER
from routes.auth_routes import SESSION_COOKIE
from src.marketmatch_canonical_wav import MAX_WAV_BYTES, CanonicalWavCode
from src.marketmatch_stt_process import (
    MarketMatchProcessCode,
    MarketMatchProcessError,
    MarketMatchProcessResult,
    release_admission,
    transcribe_in_spawned_process,
    try_acquire_admission,
)


MARKETMATCH_STT_ROUTE = "/api/marketmatch/stt/transcribe"
MARKETMATCH_STT_END_TO_END_TIMEOUT_SECONDS = 180.0
MARKETMATCH_STT_READ_TIMEOUT_SECONDS = 30.0


class MarketMatchRouteCode(str, Enum):
    AUTH_DISABLED = "AUTH_DISABLED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_METHOD_REJECTED = "AUTH_METHOD_REJECTED"
    AUTH_STATE_UNAVAILABLE = "AUTH_STATE_UNAVAILABLE"
    MARKETMATCH_FORBIDDEN = "MARKETMATCH_FORBIDDEN"
    TRANSCRIPTION_BUSY = "TRANSCRIPTION_BUSY"
    INVALID_CONTENT_TYPE = "INVALID_CONTENT_TYPE"
    CONTENT_ENCODING_REJECTED = "CONTENT_ENCODING_REJECTED"
    CONTENT_LENGTH_REQUIRED = "CONTENT_LENGTH_REQUIRED"
    INVALID_CONTENT_LENGTH = "INVALID_CONTENT_LENGTH"
    INPUT_LIMIT_EXCEEDED = "INPUT_LIMIT_EXCEEDED"
    INPUT_SIZE_MISMATCH = "INPUT_SIZE_MISMATCH"
    EMPTY_INPUT = "EMPTY_INPUT"
    INPUT_DISCONNECTED = "INPUT_DISCONNECTED"
    INPUT_READ_FAILED = "INPUT_READ_FAILED"
    INPUT_READ_TIMEOUT = "INPUT_READ_TIMEOUT"


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
    MarketMatchRouteCode.INVALID_CONTENT_TYPE.value: (415, "Content-Type must be audio/wav."),
    MarketMatchRouteCode.CONTENT_ENCODING_REJECTED.value: (415, "Content-Encoding is not supported."),
    MarketMatchRouteCode.CONTENT_LENGTH_REQUIRED.value: (411, "Content-Length is required."),
    MarketMatchRouteCode.INVALID_CONTENT_LENGTH.value: (400, "Content-Length is invalid."),
    MarketMatchRouteCode.INPUT_LIMIT_EXCEEDED.value: (413, "Audio input exceeds the pilot limit."),
    MarketMatchRouteCode.INPUT_SIZE_MISMATCH.value: (400, "Audio input size does not match Content-Length."),
    MarketMatchRouteCode.EMPTY_INPUT.value: (400, "Audio input is empty."),
    MarketMatchRouteCode.INPUT_DISCONNECTED.value: (400, "Audio input was interrupted."),
    MarketMatchRouteCode.INPUT_READ_FAILED.value: (400, "Audio input could not be read."),
    MarketMatchRouteCode.INPUT_READ_TIMEOUT.value: (408, "Audio input timed out."),
    CanonicalWavCode.INVALID_INPUT.value: (400, "Audio input is invalid."),
    CanonicalWavCode.INPUT_LIMIT_EXCEEDED.value: (413, "Audio input exceeds the pilot limit."),
    CanonicalWavCode.INVALID_WAV.value: (422, "Audio input is not a canonical WAV."),
    CanonicalWavCode.UNSUPPORTED_WAV_FORMAT.value: (415, "WAV format is not supported."),
    CanonicalWavCode.DURATION_LIMIT_EXCEEDED.value: (413, "Audio duration exceeds the pilot limit."),
    CanonicalWavCode.INVALID_BACKEND_RESULT.value: (502, "Transcription result was invalid."),
    CanonicalWavCode.SEGMENT_LIMIT_EXCEEDED.value: (502, "Transcription result exceeded the segment limit."),
    CanonicalWavCode.TRANSCRIPT_LIMIT_EXCEEDED.value: (502, "Transcription result exceeded the text limit."),
    CanonicalWavCode.BACKEND_FAILED.value: (503, "Local transcription failed."),
    MarketMatchProcessCode.INVALID_INPUT.value: (400, "Audio input is invalid."),
    MarketMatchProcessCode.WORKER_TIMEOUT.value: (504, "Local transcription timed out."),
    MarketMatchProcessCode.WORKER_FAILED.value: (503, "Local transcription failed."),
    MarketMatchProcessCode.WORKER_PROTOCOL_ERROR.value: (502, "Transcription result was invalid."),
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


def _validated_content_length(request: Request) -> int:
    content_types = request.headers.getlist("content-type")
    if len(content_types) != 1:
        _fail(MarketMatchRouteCode.INVALID_CONTENT_TYPE)
    content_type = content_types[0]
    if type(content_type) is not str or content_type.strip().lower() != "audio/wav":
        _fail(MarketMatchRouteCode.INVALID_CONTENT_TYPE)
    if request.headers.get("content-encoding") is not None:
        _fail(MarketMatchRouteCode.CONTENT_ENCODING_REJECTED)
    raw_lengths = request.headers.getlist("content-length")
    if not raw_lengths:
        _fail(MarketMatchRouteCode.CONTENT_LENGTH_REQUIRED)
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
    if content_length > MAX_WAV_BYTES:
        _fail(MarketMatchRouteCode.INPUT_LIMIT_EXCEEDED)
    return content_length


async def _read_raw_wav(request: Request, *, expected_size: int, deadline: float) -> bytes:
    chunks: list[bytes] = []
    observed = 0

    async def _consume() -> bytes:
        nonlocal observed
        try:
            async for chunk in request.stream():
                if type(chunk) is not bytes:
                    _fail(MarketMatchRouteCode.INPUT_READ_FAILED)
                if not chunk:
                    continue
                remaining_expected = expected_size - observed
                remaining_absolute = MAX_WAV_BYTES - observed
                if len(chunk) > remaining_absolute:
                    observed = MAX_WAV_BYTES + 1
                    _fail(MarketMatchRouteCode.INPUT_LIMIT_EXCEEDED)
                if len(chunk) > remaining_expected:
                    observed = min(expected_size + 1, MAX_WAV_BYTES + 1)
                    _fail(MarketMatchRouteCode.INPUT_SIZE_MISMATCH)
                chunks.append(chunk)
                observed += len(chunk)
        except MarketMatchRouteError:
            raise
        except ClientDisconnect:
            _fail(MarketMatchRouteCode.INPUT_DISCONNECTED)
        except Exception:
            _fail(MarketMatchRouteCode.INPUT_READ_FAILED)
        if observed == 0:
            _fail(MarketMatchRouteCode.EMPTY_INPUT)
        if observed != expected_size:
            _fail(MarketMatchRouteCode.INPUT_SIZE_MISMATCH)
        try:
            return b"".join(chunks)
        except Exception:
            _fail(MarketMatchRouteCode.INPUT_READ_FAILED)

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
    transcriber: Callable[..., Awaitable[MarketMatchProcessResult]] = transcribe_in_spawned_process,
) -> APIRouter:
    router = APIRouter(tags=["marketmatch-stt"])

    @router.post(MARKETMATCH_STT_ROUTE)
    async def transcribe_marketmatch_call(request: Request):
        admission_lease: object | None = None
        try:
            _require_marketmatch_cookie_user(request)
            admission_lease = try_acquire_admission()
            if admission_lease is None:
                _fail(MarketMatchRouteCode.TRANSCRIPTION_BUSY)
            deadline = time.monotonic() + MARKETMATCH_STT_END_TO_END_TIMEOUT_SECONDS
            expected_size = _validated_content_length(request)
            wav_bytes = await _read_raw_wav(
                request,
                expected_size=expected_size,
                deadline=deadline,
            )
            result = await transcriber(wav_bytes, deadline=deadline)
            return _success_response(result)
        except MarketMatchRouteError as error:
            return _error_response(error.code.value)
        except MarketMatchProcessError as error:
            return _error_response(error.code.value)
        except asyncio.CancelledError:
            raise
        except Exception:
            return _error_response(MarketMatchProcessCode.WORKER_FAILED.value)
        finally:
            if admission_lease is not None:
                release_admission(admission_lease)

    return router


__all__ = (
    "MARKETMATCH_STT_END_TO_END_TIMEOUT_SECONDS",
    "MARKETMATCH_STT_READ_TIMEOUT_SECONDS",
    "MARKETMATCH_STT_ROUTE",
    "MarketMatchRouteCode",
    "setup_marketmatch_stt_routes",
)
