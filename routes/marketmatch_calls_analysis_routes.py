"""Authenticated, local-only structured analysis for MarketMatch Calls."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import json
import re
from typing import Awaitable, Callable, NoReturn

from fastapi import APIRouter, Request
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator
from starlette.responses import JSONResponse

from core.database import ModelEndpoint, SessionLocal
from routes.marketmatch_stt_routes import (
    MarketMatchRouteError,
    _error_response as _marketmatch_auth_error_response,
    _require_marketmatch_cookie_user,
)
from src.auth_helpers import owner_filter
from src.endpoint_resolver import resolve_endpoint_by_id
from src.llm_core import llm_call_async
from src.model_context import is_local_endpoint
from src.settings import get_user_setting, load_settings


MARKETMATCH_CALLS_ANALYSIS_ROUTE = "/api/marketmatch/calls/analyze"
MAX_TRANSCRIPT_CHARS = 12_000
MAX_REQUEST_BYTES = 64_000
ANALYSIS_TIMEOUT_SECONDS = 120.0
ANALYSIS_MAX_TOKENS = 2_048
MAX_SUMMARY_CHARS = 2_000
MAX_LIST_ITEMS = 20
MAX_LIST_ITEM_CHARS = 1_000
MAX_ACTION_TASK_CHARS = 1_000
MAX_ACTION_OWNER_CHARS = 200
MAX_ACTION_DUE_DATE_CHARS = 200
MAX_ANALYSIS_TEXT_CHARS = 16_000
MAX_RAW_MODEL_OUTPUT_CHARS = 24_000

_KNOWN_ENDPOINT_KINDS = frozenset({"auto", "local", "api", "proxy"})


class AnalysisCode(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"
    INPUT_LIMIT_EXCEEDED = "INPUT_LIMIT_EXCEEDED"
    LOCAL_MODEL_UNAVAILABLE = "LOCAL_MODEL_UNAVAILABLE"
    ANALYSIS_TIMEOUT = "ANALYSIS_TIMEOUT"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"
    INVALID_MODEL_OUTPUT = "INVALID_MODEL_OUTPUT"


_ERROR_RESPONSES: dict[AnalysisCode, tuple[int, str]] = {
    AnalysisCode.INVALID_INPUT: (422, "Transcript input is invalid."),
    AnalysisCode.INPUT_LIMIT_EXCEEDED: (413, "Transcript exceeds the analysis pilot limit."),
    AnalysisCode.LOCAL_MODEL_UNAVAILABLE: (503, "Local analysis is unavailable."),
    AnalysisCode.ANALYSIS_TIMEOUT: (504, "Local analysis timed out."),
    AnalysisCode.ANALYSIS_FAILED: (503, "Local analysis failed."),
    AnalysisCode.INVALID_MODEL_OUTPUT: (502, "Local analysis returned an invalid result."),
}


class AnalysisError(Exception):
    def __init__(self, code: AnalysisCode):
        self.code = code
        super().__init__(code.value)


def _fail(code: AnalysisCode) -> NoReturn:
    raise AnalysisError(code) from None


def _error_response(code: AnalysisCode) -> JSONResponse:
    status, message = _ERROR_RESPONSES.get(
        code, _ERROR_RESPONSES[AnalysisCode.ANALYSIS_FAILED]
    )
    return JSONResponse(
        status_code=status,
        content={"error": code.value, "message": message},
    )


class TranscriptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    transcript: StrictStr = Field(max_length=MAX_TRANSCRIPT_CHARS)

    @field_validator("transcript", mode="before")
    @classmethod
    def _trim_transcript(cls, value: object) -> object:
        if type(value) is not str:
            return value
        value = value.strip()
        if not value:
            raise ValueError("transcript is empty")
        return value


class AnalysisActionItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task: StrictStr = Field(max_length=MAX_ACTION_TASK_CHARS)
    owner: StrictStr | None = Field(max_length=MAX_ACTION_OWNER_CHARS)
    due_date: StrictStr | None = Field(max_length=MAX_ACTION_DUE_DATE_CHARS)

    @field_validator("task")
    @classmethod
    def _validate_task(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("task is empty")
        return value

    @field_validator("owner", "due_date")
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("optional text must be null or non-empty")
        return value


class AnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    summary: StrictStr = Field(max_length=MAX_SUMMARY_CHARS)
    decisions: list[StrictStr] = Field(max_length=MAX_LIST_ITEMS)
    action_items: list[AnalysisActionItem] = Field(max_length=MAX_LIST_ITEMS)
    open_questions: list[StrictStr] = Field(max_length=MAX_LIST_ITEMS)

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("summary is empty")
        return value

    @field_validator("decisions", "open_questions")
    @classmethod
    def _validate_string_list(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            value = value.strip()
            if not value or len(value) > MAX_LIST_ITEM_CHARS:
                raise ValueError("list item is invalid")
            normalized.append(value)
        return normalized


@dataclass(frozen=True)
class LocalAnalysisEndpoint:
    url: str
    model: str
    headers: dict[str, str]


def _setting(key: str, owner: str, settings: dict) -> str:
    value = get_user_setting(key, owner, settings.get(key, ""))
    return value.strip() if type(value) is str else ""


def _selected_endpoint(owner: str) -> tuple[str, str] | None:
    settings = load_settings()
    if type(settings) is not dict:
        return None
    endpoint_id = _setting("utility_endpoint_id", owner, settings)
    model = _setting("utility_model", owner, settings)
    if not endpoint_id:
        endpoint_id = _setting("default_endpoint_id", owner, settings)
        model = _setting("default_model", owner, settings)
    return (endpoint_id, model) if endpoint_id else None


def _endpoint_kind(endpoint_id: str, owner: str) -> str | None:
    db = SessionLocal()
    try:
        query = db.query(ModelEndpoint).filter(
            ModelEndpoint.id == endpoint_id,
            ModelEndpoint.is_enabled == True,
        )
        endpoint = owner_filter(query, ModelEndpoint, owner).first()
        if endpoint is None:
            return None
        raw_kind = getattr(endpoint, "endpoint_kind", None)
        if raw_kind is None or (type(raw_kind) is str and not raw_kind.strip()):
            return "auto"
        if type(raw_kind) is not str:
            return None
        kind = raw_kind.strip().lower()
        return kind if kind in _KNOWN_ENDPOINT_KINDS else None
    except Exception:
        return None
    finally:
        db.close()


def resolve_local_analysis_endpoint(owner: str) -> LocalAnalysisEndpoint | None:
    """Resolve exactly one configured endpoint and prove it local."""

    try:
        selected = _selected_endpoint(owner)
        if selected is None:
            return None
        endpoint_id, configured_model = selected
        kind = _endpoint_kind(endpoint_id, owner)
        if kind not in {"auto", "local"}:
            return None
        resolved = resolve_endpoint_by_id(
            endpoint_id,
            model=configured_model or None,
            owner=owner,
        )
        if resolved is None:
            return None
        url, model, headers = resolved
        if type(url) is not str or not url or type(model) is not str or not model:
            return None
        if type(headers) is not dict or not is_local_endpoint(url):
            return None
        if any(type(key) is not str or type(value) is not str for key, value in headers.items()):
            return None
        return LocalAnalysisEndpoint(url=url, model=model, headers=dict(headers))
    except Exception:
        return None


_SYSTEM_PROMPT = """You create a conservative, reviewable draft analysis of one call transcript.
Use only the supplied transcript. Preserve uncertainty. Do not invent facts, decisions,
owners, deadlines, amounts, dates, locations, approvals, commitments, or identities.
Do not infer a speaker's identity. Use the transcript's dominant language.

Classify each supported statement by what the transcript explicitly communicates:

DECISIONS
A statement is a decision only when the transcript explicitly says or clearly states
that someone decided, agreed, approved, selected, chose, confirmed, resolved, or will
proceed with a selected option. Preserve the decision as a declarative statement.
Agreement to an option or selection is a decision; agreement or a promise to perform
specific work is an action item, not a duplicate decision.

ACTION ITEMS
Create an action item only when the transcript explicitly contains a task, assignment,
request, promise, obligation, or future action. Valid action language includes “Felipe
will send the drawing,” “Please inspect the fifth floor,” “Maria must confirm the
quantities,” and “John agreed to prepare the report.” A decision is not an action item.
Never rewrite a decision as an imperative task. Do not create an action item merely
because a statement describes a decision, current state, preference, or plan without an
explicit task, contains an action verb grammatically, or could logically lead to future
work. Do not duplicate one statement across decisions and action_items.

DISCUSSION, SUGGESTIONS, AND QUESTIONS
Discussion language such as discussed, considered, explored, or reviewed is neither a
decision nor an action unless the transcript separately records a selection or explicit
task. A suggestion or preference is not a decision unless explicitly accepted and is not
an action unless explicitly requested or assigned. Put an explicit unresolved question
only in open_questions; do not convert it into a decision or action. When classification
is uncertain, omit the item rather than inventing it.

Examples:

Example 1
Transcript: “The team decided to use the current plan for the drawings.”
Expected:
{"summary":"The team selected the current plan for the drawings.","decisions":["The team decided to use the current plan for the drawings."],"action_items":[],"open_questions":[]}

Example 2
Transcript: “Felipe will send the updated drawing by Friday.”
Expected:
{"summary":"Felipe is expected to send the updated drawing by Friday.","decisions":[],"action_items":[{"task":"Send the updated drawing.","owner":"Felipe","due_date":"Friday"}],"open_questions":[]}

Example 3
Transcript: “We discussed using the revised plan.”
Expected:
{"summary":"The revised plan was discussed.","decisions":[],"action_items":[],"open_questions":[]}

Example 4
Transcript: “The team decided to use the revised plan. Felipe will send the drawing by Friday.”
Expected:
{"summary":"The team selected the revised plan, and Felipe is expected to send the drawing by Friday.","decisions":["The team decided to use the revised plan."],"action_items":[{"task":"Send the drawing.","owner":"Felipe","due_date":"Friday"}],"open_questions":[]}

Return strict JSON only, with exactly this shape:
{"summary":"string","decisions":["string"],"action_items":[{"task":"string","owner":"string or null","due_date":"string or null"}],"open_questions":["string"]}
Use empty arrays when no decisions, action items, or open questions are supported. Use
null for an unstated owner or due date. Do not include reasoning, markdown fences,
commentary, or extra keys."""


def _messages(transcript: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": "Transcript:\n" + transcript},
    ]


def _total_text_chars(result: AnalysisResponse) -> int:
    total = len(result.summary)
    total += sum(len(item) for item in result.decisions)
    total += sum(len(item) for item in result.open_questions)
    for item in result.action_items:
        total += len(item.task)
        total += len(item.owner or "")
        total += len(item.due_date or "")
    return total


def validate_model_output(raw: object) -> AnalysisResponse:
    if type(raw) is dict:
        try:
            encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError, RecursionError):
            _fail(AnalysisCode.INVALID_MODEL_OUTPUT)
        if len(encoded) > MAX_RAW_MODEL_OUTPUT_CHARS:
            _fail(AnalysisCode.INVALID_MODEL_OUTPUT)
        parsed = raw
    elif type(raw) is str:
        if not raw or len(raw) > MAX_RAW_MODEL_OUTPUT_CHARS:
            _fail(AnalysisCode.INVALID_MODEL_OUTPUT)
        candidate = raw.strip()
        if candidate.startswith("\ufeff"):
            candidate = candidate[1:].strip()
        fenced = re.fullmatch(r"```json[ \t]*\r?\n(.*?)\r?\n```", candidate, re.DOTALL)
        if fenced is not None:
            candidate = fenced.group(1)
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            _fail(AnalysisCode.INVALID_MODEL_OUTPUT)
    else:
        _fail(AnalysisCode.INVALID_MODEL_OUTPUT)
    if type(parsed) is not dict:
        _fail(AnalysisCode.INVALID_MODEL_OUTPUT)
    try:
        result = AnalysisResponse.model_validate(parsed)
    except ValidationError:
        _fail(AnalysisCode.INVALID_MODEL_OUTPUT)
    if _total_text_chars(result) > MAX_ANALYSIS_TEXT_CHARS:
        _fail(AnalysisCode.INVALID_MODEL_OUTPUT)
    return result


async def _read_request_json(request: Request) -> object:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        _fail(AnalysisCode.INVALID_INPUT)
    raw_length = request.headers.get("content-length")
    if raw_length is not None:
        if not raw_length.isascii() or not raw_length.isdigit():
            _fail(AnalysisCode.INVALID_INPUT)
        if int(raw_length, 10) > MAX_REQUEST_BYTES:
            _fail(AnalysisCode.INPUT_LIMIT_EXCEEDED)
    body = bytearray()
    try:
        async for chunk in request.stream():
            if type(chunk) is not bytes:
                _fail(AnalysisCode.INVALID_INPUT)
            if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                _fail(AnalysisCode.INPUT_LIMIT_EXCEEDED)
            body.extend(chunk)
    except AnalysisError:
        raise
    except Exception:
        _fail(AnalysisCode.INVALID_INPUT)
    try:
        return json.loads(bytes(body))
    except (json.JSONDecodeError, UnicodeDecodeError):
        _fail(AnalysisCode.INVALID_INPUT)


EndpointResolver = Callable[[str], LocalAnalysisEndpoint | None]
InferenceCall = Callable[..., Awaitable[object]]


def setup_marketmatch_calls_analysis_routes(
    endpoint_resolver: EndpointResolver = resolve_local_analysis_endpoint,
    inference_call: InferenceCall = llm_call_async,
) -> APIRouter:
    router = APIRouter()

    @router.post(MARKETMATCH_CALLS_ANALYSIS_ROUTE)
    async def analyze_calls_transcript(request: Request):
        try:
            owner = _require_marketmatch_cookie_user(request)
            raw_payload = await _read_request_json(request)
            try:
                payload = TranscriptRequest.model_validate(raw_payload)
            except ValidationError as exc:
                if any(error.get("type") == "string_too_long" for error in exc.errors()):
                    _fail(AnalysisCode.INPUT_LIMIT_EXCEEDED)
                _fail(AnalysisCode.INVALID_INPUT)

            endpoint = endpoint_resolver(owner)
            if endpoint is None:
                _fail(AnalysisCode.LOCAL_MODEL_UNAVAILABLE)

            try:
                async with asyncio.timeout(ANALYSIS_TIMEOUT_SECONDS):
                    raw_result = await inference_call(
                        endpoint.url,
                        endpoint.model,
                        _messages(payload.transcript),
                        headers=endpoint.headers,
                        temperature=0.0,
                        max_tokens=ANALYSIS_MAX_TOKENS,
                        timeout=int(ANALYSIS_TIMEOUT_SECONDS),
                        max_retries=1,
                        workload="foreground",
                        use_cache=False,
                    )
            except (TimeoutError, asyncio.TimeoutError):
                _fail(AnalysisCode.ANALYSIS_TIMEOUT)
            except asyncio.CancelledError:
                raise
            except HTTPException:
                _fail(AnalysisCode.ANALYSIS_FAILED)
            except Exception:
                _fail(AnalysisCode.ANALYSIS_FAILED)

            result = validate_model_output(raw_result)
            return JSONResponse(content=result.model_dump(mode="json"))
        except MarketMatchRouteError as exc:
            return _marketmatch_auth_error_response(exc.code.value)
        except AnalysisError as exc:
            return _error_response(exc.code)

    return router


__all__ = [
    "ANALYSIS_TIMEOUT_SECONDS",
    "AnalysisActionItem",
    "AnalysisCode",
    "AnalysisResponse",
    "LocalAnalysisEndpoint",
    "MARKETMATCH_CALLS_ANALYSIS_ROUTE",
    "MAX_TRANSCRIPT_CHARS",
    "resolve_local_analysis_endpoint",
    "setup_marketmatch_calls_analysis_routes",
    "validate_model_output",
]
