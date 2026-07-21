import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request

import routes.marketmatch_locale_routes as locale_routes
from routes.auth_routes import SESSION_COOKIE
from src.marketmatch_i18n import (
    DEFAULT_LOCALE,
    DEFAULT_TIMEZONE,
    SUPPORTED_LOCALES,
    configured_default_locale,
    configured_default_timezone,
    normalize_locale,
    normalize_transcript_language,
    resolve_interface_locale,
)


class AuthManager:
    is_configured = True
    users = {"alice": {}, "bob": {}}

    def get_username_for_token(self, token):
        return {"alice-cookie": "alice", "bob-cookie": "bob"}.get(token)


def _endpoint(method):
    router = locale_routes.setup_marketmatch_locale_routes()
    return next(route.endpoint for route in router.routes if method in route.methods)


def _request(method="GET", payload=None, *, user="alice", cookie=True, extra_headers=None):
    body = b"" if payload is None else json.dumps(payload).encode()
    headers = {}
    if cookie:
        headers["cookie"] = f"{SESSION_COOKIE}={user}-cookie"
    if method == "PUT":
        headers.update({"content-type": "application/json", "content-length": str(len(body))})
    headers.update(extra_headers or {})
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request({
        "type": "http", "method": method, "path": locale_routes.MARKETMATCH_LOCALE_ROUTE,
        "headers": [(key.encode(), value.encode()) for key, value in headers.items()],
        "query_string": b"", "client": ("203.0.113.5", 1234),
        "server": ("test", 80), "scheme": "http", "http_version": "1.1",
        "app": SimpleNamespace(state=SimpleNamespace(auth_manager=AuthManager())),
        "state": {"current_user": user},
    }, receive)
    request.state.current_user = user
    return request


def _json(response):
    return json.loads(response.body)


def test_canonical_locale_set_aliases_and_stt_boundary():
    assert SUPPORTED_LOCALES == ("es", "en", "zh-Hans")
    assert DEFAULT_LOCALE == "es"
    aliases = {
        "es-ES": "es", "es-DO": "es", "en-US": "en", "en-GB": "en",
        "zh-CN": "zh-Hans", "zh-SG": "zh-Hans", "zh-Hans-CN": "zh-Hans",
    }
    for value, expected in aliases.items():
        assert normalize_locale(value) == expected
    assert normalize_locale("zh-TW") is None
    assert normalize_transcript_language("zh-CN") == "zh"
    assert normalize_transcript_language("fr") == "und"
    assert normalize_transcript_language(None) == "und"


def test_locale_and_timezone_configuration_are_independent(monkeypatch):
    monkeypatch.delenv("MARKETMATCH_DEFAULT_LOCALE", raising=False)
    monkeypatch.delenv("MARKETMATCH_DEFAULT_TIMEZONE", raising=False)
    assert configured_default_locale() == ("es", "deployment")
    assert configured_default_timezone() == (DEFAULT_TIMEZONE, "deployment")
    assert resolve_interface_locale(None) == ("es", "deployment")
    assert resolve_interface_locale("en") == ("en", "user")
    assert resolve_interface_locale(None, project_locale="zh-CN", organization_locale="en") == ("zh-Hans", "project")
    assert resolve_interface_locale(None, organization_locale="en-US") == ("en", "organization")

    monkeypatch.setenv("MARKETMATCH_DEFAULT_LOCALE", "invalid")
    monkeypatch.setenv("MARKETMATCH_DEFAULT_TIMEZONE", "Not/AZone")
    assert configured_default_locale() == ("es", "fallback")
    assert configured_default_timezone() == ("UTC", "fallback")


@pytest.mark.asyncio
async def test_authenticated_locale_read_and_canonical_idempotent_update(monkeypatch):
    store = {"alice": {}}
    writes = []
    monkeypatch.setattr(locale_routes, "_load_for_user", lambda user: dict(store.get(user, {})))

    def save(user, prefs):
        store[user] = dict(prefs)
        writes.append((user, dict(prefs)))

    monkeypatch.setattr(locale_routes, "_save_for_user", save)
    first = await _endpoint("GET")(_request())
    assert _json(first) == {
        "locale": "es", "persisted_locale": None, "locale_source": "deployment",
        "timezone": "America/Santo_Domingo", "timezone_source": "deployment",
    }
    update = _endpoint("PUT")
    saved = await update(_request("PUT", {"locale": "zh-CN"}))
    assert saved.status_code == 200
    assert _json(saved)["persisted_locale"] == "zh-Hans"
    assert store == {"alice": {"marketmatch_locale": "zh-Hans"}}
    again = await update(_request("PUT", {"locale": "zh-Hans"}))
    assert again.status_code == 200
    assert writes[-1] == writes[-2]


@pytest.mark.asyncio
async def test_locale_preference_is_owner_scoped_and_rejects_overrides(monkeypatch):
    store = {"alice": {"marketmatch_locale": "en"}, "bob": {"marketmatch_locale": "es"}}
    monkeypatch.setattr(locale_routes, "_load_for_user", lambda user: dict(store[user]))
    monkeypatch.setattr(locale_routes, "_save_for_user", lambda user, prefs: store.__setitem__(user, dict(prefs)))
    endpoint = _endpoint("PUT")
    for field in ("owner", "owner_id", "user", "user_id", "username", "role", "timezone", "session", "provider", "model", "endpoint"):
        response = await endpoint(_request("PUT", {"locale": "zh-Hans", field: "bob"}))
        assert response.status_code == 422
    response = await endpoint(_request("PUT", {"locale": "en-US"}, user="bob"))
    assert response.status_code == 200
    assert store["alice"]["marketmatch_locale"] == "en"
    assert store["bob"]["marketmatch_locale"] == "en"


@pytest.mark.asyncio
async def test_locale_route_rejects_unauthenticated_bearer_and_invalid_values(monkeypatch):
    monkeypatch.setattr(locale_routes, "_load_for_user", lambda user: {})
    monkeypatch.setattr(locale_routes, "_save_for_user", lambda user, prefs: None)
    assert (await _endpoint("GET")(_request(cookie=False))).status_code == 401
    bearer = _request(extra_headers={"authorization": "Bearer secret"})
    assert (await _endpoint("GET")(bearer)).status_code == 403
    invalid = await _endpoint("PUT")(_request("PUT", {"locale": "fr"}))
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_locale_preference_write_failure_is_safe_and_preserves_confirmed_value(monkeypatch):
    store = {"alice": {"marketmatch_locale": "en"}}
    monkeypatch.setattr(locale_routes, "_load_for_user", lambda user: dict(store[user]))

    def fail_save(user, prefs):
        raise OSError("private storage detail")

    monkeypatch.setattr(locale_routes, "_save_for_user", fail_save)
    response = await _endpoint("PUT")(_request("PUT", {"locale": "zh-Hans"}))
    assert response.status_code == 503
    assert _json(response) == {
        "error": "LOCALE_PREFERENCE_UNAVAILABLE",
        "message": "Locale preference could not be saved.",
    }
    assert store["alice"]["marketmatch_locale"] == "en"
    assert "private storage detail" not in response.body.decode()


def test_route_registration_and_no_database_migration():
    app_source = open("app.py", encoding="utf-8").read()
    env_example = open(".env.example", encoding="utf-8").read()
    assert "setup_marketmatch_locale_routes()" in app_source
    assert locale_routes.LOCALE_PREFERENCE_KEY == "marketmatch_locale"
    assert "MARKETMATCH_DEFAULT_LOCALE=es" in env_example
    assert "MARKETMATCH_DEFAULT_TIMEZONE=America/Santo_Domingo" in env_example
