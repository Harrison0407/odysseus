"""Authenticated MarketMatch interface-locale preference contract."""

from __future__ import annotations

import os
import threading
import json

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError, field_validator
from starlette.responses import JSONResponse

from core.auth import RESERVED_USERNAMES, normalize_known_username
from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_USER
from routes.auth_routes import SESSION_COOKIE
from routes.prefs_routes import _load_for_user, _save_for_user
from src.marketmatch_i18n import (
    configured_default_timezone,
    normalize_locale,
    resolve_interface_locale,
)


MARKETMATCH_LOCALE_ROUTE = "/api/marketmatch/locale"
LOCALE_PREFERENCE_KEY = "marketmatch_locale"
_PREFERENCE_LOCK = threading.RLock()
MAX_LOCALE_REQUEST_BYTES = 1_024


class LocalePreferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    locale: StrictStr

    @field_validator("locale")
    @classmethod
    def _canonicalize_locale(cls, value: str) -> str:
        normalized = normalize_locale(value)
        if normalized is None:
            raise ValueError("unsupported locale")
        return normalized


def _safe_error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message})


def _require_cookie_user(request: Request) -> str:
    """Require a real configured browser-cookie identity without privilege changes."""

    if os.getenv("AUTH_ENABLED", "true").lower() == "false":
        raise PermissionError("AUTH_REQUIRED")
    if (
        request.headers.get("authorization") is not None
        or bool(getattr(request.state, "api_token", False))
        or request.headers.get(INTERNAL_TOOL_HEADER) is not None
        or getattr(request.state, "current_user", None) == INTERNAL_TOOL_USER
    ):
        raise PermissionError("AUTH_METHOD_REJECTED")
    manager = getattr(request.app.state, "auth_manager", None)
    if manager is None or getattr(manager, "is_configured", False) is not True:
        raise PermissionError("AUTH_REQUIRED")
    token = request.cookies.get(SESSION_COOKIE)
    if type(token) is not str or not token:
        raise PermissionError("AUTH_REQUIRED")
    try:
        users = manager.users
        cookie_user = manager.get_username_for_token(token)
    except Exception:
        raise PermissionError("AUTH_REQUIRED") from None
    if type(users) is not dict:
        raise PermissionError("AUTH_REQUIRED")
    user = normalize_known_username(users, cookie_user)
    state_user = normalize_known_username(users, getattr(request.state, "current_user", None))
    if not user or user in RESERVED_USERNAMES or state_user != user:
        raise PermissionError("AUTH_REQUIRED")
    return user


def _response_for_user(user: str) -> dict[str, object]:
    prefs = _load_for_user(user)
    stored = prefs.get(LOCALE_PREFERENCE_KEY) if type(prefs) is dict else None
    persisted = normalize_locale(stored)
    locale, locale_source = resolve_interface_locale(persisted)
    timezone, timezone_source = configured_default_timezone()
    return {
        "locale": locale,
        "persisted_locale": persisted,
        "locale_source": locale_source,
        "timezone": timezone,
        "timezone_source": timezone_source,
    }


async def _read_payload(request: Request) -> object:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ValueError("invalid content type")
    raw_length = request.headers.get("content-length")
    if raw_length is not None:
        if not raw_length.isascii() or not raw_length.isdigit() or int(raw_length) > MAX_LOCALE_REQUEST_BYTES:
            raise ValueError("invalid content length")
    body = bytearray()
    async for chunk in request.stream():
        if type(chunk) is not bytes or len(body) + len(chunk) > MAX_LOCALE_REQUEST_BYTES:
            raise ValueError("invalid body")
        body.extend(chunk)
    return json.loads(bytes(body))


def setup_marketmatch_locale_routes() -> APIRouter:
    router = APIRouter(tags=["marketmatch-locale"])

    @router.get(MARKETMATCH_LOCALE_ROUTE)
    async def get_marketmatch_locale(request: Request):
        try:
            user = _require_cookie_user(request)
            return JSONResponse(content=_response_for_user(user))
        except PermissionError as exc:
            code = str(exc)
            status = 403 if code == "AUTH_METHOD_REJECTED" else 401
            return _safe_error(status, code, "Cookie authentication is required.")

    @router.put(MARKETMATCH_LOCALE_ROUTE)
    async def set_marketmatch_locale(request: Request):
        try:
            user = _require_cookie_user(request)
        except PermissionError as exc:
            code = str(exc)
            status = 403 if code == "AUTH_METHOD_REJECTED" else 401
            return _safe_error(status, code, "Cookie authentication is required.")
        try:
            raw = await _read_payload(request)
            payload = LocalePreferenceRequest.model_validate(raw)
        except (ValidationError, ValueError, TypeError):
            return _safe_error(422, "INVALID_LOCALE", "Locale preference is invalid.")
        try:
            with _PREFERENCE_LOCK:
                prefs = _load_for_user(user)
                prefs[LOCALE_PREFERENCE_KEY] = payload.locale
                _save_for_user(user, prefs)
        except (OSError, TypeError, ValueError):
            return _safe_error(
                503,
                "LOCALE_PREFERENCE_UNAVAILABLE",
                "Locale preference could not be saved.",
            )
        return JSONResponse(content=_response_for_user(user))

    return router


__all__ = (
    "LOCALE_PREFERENCE_KEY",
    "MARKETMATCH_LOCALE_ROUTE",
    "LocalePreferenceRequest",
    "setup_marketmatch_locale_routes",
)
