import asyncio
import os
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from core.middleware import INTERNAL_TOOL_HEADER
from routes import marketmatch_stt_routes as route_module
from routes.auth_routes import SESSION_COOKIE
from src.marketmatch_canonical_wav import CanonicalWavCode
from src.upload_limits import MARKETMATCH_CALL_AUDIO_MAX_BYTES
from src.marketmatch_stt_process import (
    MarketMatchProcessError,
    MarketMatchProcessResult,
    release_admission,
    shutdown_active_workers,
    try_acquire_admission,
)


class FakeAuthManager:
    def __init__(
        self,
        *,
        user="alice",
        configured=True,
        users=None,
        privileges=None,
        token_user=None,
    ):
        self.is_configured = configured
        if privileges is None:
            privileges = {"can_use_marketmatch": True}
        if users is None:
            users = {
                user: {
                    "is_admin": False,
                    "privileges": privileges,
                }
            }
        self.users = users
        self._user = user if token_user is None else token_user
        self._privileges = privileges

    def get_username_for_token(self, token):
        return self._user if token == "valid-cookie" else None

    def get_privileges(self, user):
        del user
        return self._privileges


class CountedReceive:
    def __init__(self, messages=None, *, delay=0):
        self.messages = list(messages or [{"type": "http.request", "body": b"x", "more_body": False}])
        self.calls = 0
        self.delay = delay

    async def __call__(self):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.messages:
            return self.messages.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}


def _request(
    receive,
    *,
    auth_manager=None,
    headers=None,
    current_user="alice",
    api_token=False,
    include_cookie=True,
):
    raw_headers = {
        "content-type": "application/octet-stream",
        "content-length": "1",
    }
    if include_cookie:
        raw_headers["cookie"] = f"{SESSION_COOKIE}=valid-cookie"
    if headers:
        for key, value in headers.items():
            if value is None:
                raw_headers.pop(key.lower(), None)
            else:
                raw_headers[key.lower()] = value
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": route_module.MARKETMATCH_STT_ROUTE,
        "raw_path": route_module.MARKETMATCH_STT_ROUTE.encode(),
        "query_string": b"",
        "headers": [(key.encode(), value.encode()) for key, value in raw_headers.items()],
        "client": ("203.0.113.10", 1234),
        "server": ("test", 80),
        "state": {"current_user": current_user, "api_token": api_token},
        "app": SimpleNamespace(state=SimpleNamespace(auth_manager=auth_manager or FakeAuthManager())),
    }
    return Request(scope, receive=receive)


def _endpoint(transcriber):
    router = route_module.setup_marketmatch_stt_routes(transcriber)
    return next(route.endpoint for route in router.routes if route.path == route_module.MARKETMATCH_STT_ROUTE)


async def _ok_transcriber(wav_path, *, byte_limit, duration_limit_ms, deadline):
    assert wav_path.is_file()
    assert byte_limit > 44
    assert duration_limit_ms == 21_600_000
    assert deadline > 0
    return MarketMatchProcessResult(
        duration_ms=1,
        transcript_text="ok",
        segments=((0, 1, "ok"),),
    )


@pytest.fixture(autouse=True)
def _clean_pilot_state(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    shutdown_active_workers()
    release_admission()
    async def passthrough(path, workdir):
        del workdir
        return path
    monkeypatch.setattr(route_module, "prepare_canonical_audio", passthrough)
    yield
    shutdown_active_workers()
    release_admission()


@pytest.mark.parametrize(
    "case",
    [
        "auth_disabled",
        "unconfigured",
        "authorization",
        "api_token",
        "internal_header",
        "internal_state",
        "missing_cookie",
        "invalid_cookie",
        "unknown_user",
        "reserved_user",
        "state_missing",
        "state_mismatch",
        "missing_privileges",
        "malformed_privileges",
        "missing_marketmatch",
        "false_marketmatch",
        "nonboolean_marketmatch",
        "privilege_lookup_failure",
    ],
)
async def test_rejected_authorization_never_receives_body(monkeypatch, case):
    receive = CountedReceive()
    manager = FakeAuthManager()
    transcriber_calls = 0
    kwargs = {}
    headers = {}
    if case == "auth_disabled":
        monkeypatch.setenv("AUTH_ENABLED", "false")
    elif case == "unconfigured":
        manager.is_configured = False
    elif case == "authorization":
        headers["authorization"] = "Bearer ody_fixture"
    elif case == "api_token":
        kwargs["api_token"] = True
    elif case == "internal_header":
        headers[INTERNAL_TOOL_HEADER] = "fixture"
    elif case == "internal_state":
        kwargs["current_user"] = "internal-tool"
    elif case == "missing_cookie":
        kwargs["include_cookie"] = False
    elif case == "invalid_cookie":
        headers["cookie"] = f"{SESSION_COOKIE}=invalid"
    elif case == "unknown_user":
        manager._user = "unknown"
    elif case == "reserved_user":
        manager.users["internal-tool"] = {
            "is_admin": False,
            "privileges": {"can_use_marketmatch": True},
        }
        manager._user = "internal-tool"
        kwargs["current_user"] = "internal-tool"
    elif case == "state_missing":
        kwargs["current_user"] = None
    elif case == "state_mismatch":
        manager.users["bob"] = {
            "is_admin": False,
            "privileges": {"can_use_marketmatch": True},
        }
        kwargs["current_user"] = "bob"
    elif case == "missing_privileges":
        manager.users["alice"].pop("privileges")
    elif case == "malformed_privileges":
        manager.users["alice"]["privileges"] = []
    elif case == "missing_marketmatch":
        manager.users["alice"]["privileges"] = {}
        manager._privileges = {}
    elif case == "false_marketmatch":
        manager.users["alice"]["privileges"]["can_use_marketmatch"] = False
        manager._privileges = {"can_use_marketmatch": False}
    elif case == "nonboolean_marketmatch":
        manager.users["alice"]["privileges"]["can_use_marketmatch"] = 1
        manager._privileges = {"can_use_marketmatch": 1}
    elif case == "privilege_lookup_failure":
        def broken(_user):
            raise RuntimeError("PRIVATE_BACKEND_CANARY")

        manager.get_privileges = broken

    async def transcriber(*args, **kwargs):
        nonlocal transcriber_calls
        transcriber_calls += 1
        return await _ok_transcriber(*args, **kwargs)

    request = _request(receive, auth_manager=manager, headers=headers, **kwargs)
    response = await _endpoint(transcriber)(request)

    assert response.status_code in {401, 403}
    assert receive.calls == 0
    assert transcriber_calls == 0


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"content-type": "multipart/form-data; boundary=x"}, 415),
        ({"content-encoding": "identity"}, 415),
        ({"content-length": ""}, 400),
        ({"content-length": "-1"}, 400),
        ({"content-length": "+1"}, 400),
        ({"content-length": "1.0"}, 400),
        ({"content-length": str(MARKETMATCH_CALL_AUDIO_MAX_BYTES + 1)}, 413),
    ],
)
async def test_rejected_ingress_headers_never_receive_or_spawn(headers, status):
    receive = CountedReceive()
    calls = 0

    async def transcriber(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await _ok_transcriber(*args, **kwargs)

    response = await _endpoint(transcriber)(_request(receive, headers=headers))

    assert response.status_code == status
    assert receive.calls == 0
    assert calls == 0


async def test_authorized_request_consumes_body_and_returns_bounded_shape():
    receive = CountedReceive([{"type": "http.request", "body": b"x", "more_body": False}])
    response = await _endpoint(_ok_transcriber)(_request(receive))

    assert response.status_code == 200
    assert receive.calls == 1
    assert response.body == (
        b'{"duration_ms":1,"segments":[{"start_ms":0,"end_ms":1,"text":"ok"}],'
        b'"transcript_text":"ok","language":"und","language_confidence":null}'
    )
    assert try_acquire_admission() is not None
    release_admission()


async def test_genuine_worker_language_metadata_survives_route_response():
    async def transcriber(wav_path, *, byte_limit, duration_limit_ms, deadline):
        assert wav_path.is_file() and byte_limit > 44 and duration_limit_ms > 0 and deadline > 0
        return MarketMatchProcessResult(
            duration_ms=10,
            transcript_text="那個窗戶",
            segments=((0, 10, "那個窗戶"),),
            language="zh-Hans",
            language_confidence=0.84,
        )

    receive = CountedReceive([{"type": "http.request", "body": b"x", "more_body": False}])
    response = await _endpoint(transcriber)(_request(receive))

    assert response.status_code == 200
    assert response.body == (
        b'{"duration_ms":10,"segments":[{"start_ms":0,"end_ms":10,"text":"'
        + "那個窗戶".encode()
        + b'"}],"transcript_text":"'
        + "那個窗戶".encode()
        + b'","language":"zh-Hans","language_confidence":0.84}'
    )


async def test_localhost_bypass_without_cookie_identity_never_receives_body(monkeypatch):
    monkeypatch.setenv("LOCALHOST_BYPASS", "true")
    receive = CountedReceive()
    request = _request(receive, current_user=None, include_cookie=False)
    request.scope["client"] = ("127.0.0.1", 1234)
    response = await _endpoint(_ok_transcriber)(request)
    assert response.status_code == 401
    assert receive.calls == 0


async def test_exact_limit_is_accepted(monkeypatch):
    monkeypatch.setattr(route_module, "MARKETMATCH_CALL_AUDIO_MAX_BYTES", 4)
    receive = CountedReceive([{"type": "http.request", "body": b"abcd", "more_body": False}])
    response = await _endpoint(_ok_transcriber)(
        _request(receive, headers={"content-length": "4"})
    )
    assert response.status_code == 200
    assert receive.calls == 1


async def test_missing_content_length_is_streamed_and_accepted(monkeypatch):
    monkeypatch.setattr(route_module, "MARKETMATCH_CALL_AUDIO_MAX_BYTES", 4)
    receive = CountedReceive([{"type": "http.request", "body": b"abcd", "more_body": False}])
    response = await _endpoint(_ok_transcriber)(
        _request(receive, headers={"content-length": None})
    )
    assert response.status_code == 200
    assert receive.calls == 1


def test_exact_200_mib_header_boundary_is_admitted_before_body():
    request = _request(CountedReceive(), headers={"content-length": "209715200"})
    assert route_module._validated_ingress_headers(request) == 209715200


def test_200_mib_plus_one_header_rejects_before_body():
    receive = CountedReceive()
    request = _request(receive, headers={"content-length": "209715201"})
    with pytest.raises(route_module.MarketMatchRouteError) as raised:
        route_module._validated_ingress_headers(request)
    assert raised.value.code is route_module.MarketMatchRouteCode.INPUT_LIMIT_EXCEEDED
    assert receive.calls == 0


async def test_limit_plus_one_stops_after_first_overflow_message(monkeypatch):
    monkeypatch.setattr(route_module, "MARKETMATCH_CALL_AUDIO_MAX_BYTES", 4)
    receive = CountedReceive(
        [
            {"type": "http.request", "body": b"abcde", "more_body": True},
            {"type": "http.request", "body": b"private", "more_body": False},
        ]
    )
    response = await _endpoint(_ok_transcriber)(
        _request(receive, headers={"content-length": "4"})
    )
    assert response.status_code == 413
    assert receive.calls == 1


@pytest.mark.parametrize(
    ("declared", "body"),
    [("2", b"x"), ("1", b"xx")],
)
async def test_dishonest_content_length_is_rejected(declared, body):
    receive = CountedReceive([{"type": "http.request", "body": body, "more_body": False}])
    response = await _endpoint(_ok_transcriber)(
        _request(receive, headers={"content-length": declared})
    )
    assert response.status_code == 400


async def test_disconnect_is_fixed_failure_and_does_not_spawn():
    calls = 0

    async def transcriber(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await _ok_transcriber(*args, **kwargs)

    receive = CountedReceive([{"type": "http.disconnect"}])
    response = await _endpoint(transcriber)(_request(receive))
    assert response.status_code == 400
    assert b"INPUT_DISCONNECTED" in response.body
    assert calls == 0


async def test_empty_stream_is_rejected_without_spawn():
    calls = 0

    async def transcriber(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await _ok_transcriber(*args, **kwargs)

    receive = CountedReceive([{"type": "http.request", "body": b"", "more_body": False}])
    response = await _endpoint(transcriber)(_request(receive))
    assert response.status_code == 400
    assert b"EMPTY_INPUT" in response.body
    assert calls == 0


async def test_read_phase_timeout_is_fixed_and_releases_slot(monkeypatch):
    monkeypatch.setattr(route_module, "MARKETMATCH_STT_READ_TIMEOUT_SECONDS", 0.01)
    receive = CountedReceive(delay=1)
    response = await _endpoint(_ok_transcriber)(_request(receive))
    assert response.status_code == 408
    assert b"INPUT_READ_TIMEOUT" in response.body
    assert try_acquire_admission() is not None
    release_admission()


async def test_canonical_error_is_mapped_without_dynamic_detail():
    async def invalid_wav(*args, **kwargs):
        del args, kwargs
        raise MarketMatchProcessError(CanonicalWavCode.INVALID_WAV)

    response = await _endpoint(invalid_wav)(_request(CountedReceive()))
    assert response.status_code == 422
    assert response.body == b'{"error":"INVALID_WAV","message":"Audio input is not a canonical WAV."}'
    assert try_acquire_admission() is not None
    release_admission()


async def test_busy_second_request_does_not_receive_body():
    assert try_acquire_admission() is not None
    receive = CountedReceive()
    response = await _endpoint(_ok_transcriber)(_request(receive))
    assert response.status_code == 429
    assert receive.calls == 0


async def test_cancellation_releases_admission_and_removes_workspace(monkeypatch):
    created = []
    original_create = route_module.create_private_workdir

    def tracked_create():
        workspace = original_create()
        created.append(workspace.name)
        return workspace

    monkeypatch.setattr(route_module, "create_private_workdir", tracked_create)
    async def cancelled(*args, **kwargs):
        del args, kwargs
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _endpoint(cancelled)(_request(CountedReceive()))
    assert created and all(not os.path.exists(path) for path in created)
    assert try_acquire_admission() is not None
    release_admission()


async def test_public_errors_do_not_echo_sensitive_values():
    canaries = (
        "AUDIO_PRIVATE_CANARY",
        "TRANSCRIPT_PRIVATE_CANARY",
        "/private/model/path",
        "alice",
        "BACKEND_PRIVATE_CANARY",
    )

    class BrokenManager(FakeAuthManager):
        def get_username_for_token(self, token):
            del token
            raise RuntimeError(" ".join(canaries))

    response = await _endpoint(_ok_transcriber)(
        _request(CountedReceive(), auth_manager=BrokenManager())
    )
    rendered = response.body.decode()
    assert response.status_code == 403
    assert all(canary not in rendered for canary in canaries)


def test_route_source_has_no_fastapi_body_parser_or_generic_stt_dependency():
    source = open(route_module.__file__, encoding="utf-8").read()
    forbidden = (
        "UploadFile",
        "File(",
        "Form(",
        "request.body",
        "request.form",
        "STTService",
        "get_stt_service",
    )
    assert all(token not in source for token in forbidden)
    assert os.path.basename(route_module.__file__) == "marketmatch_stt_routes.py"


def test_app_registers_exact_timeout_exemption_and_shutdown_cleanup():
    source = open("app.py", encoding="utf-8").read()
    exact_start = source.index("_TIMEOUT_EXEMPT_EXACT =")
    exact_end = source.index("\n}", exact_start)
    exact_block = source[exact_start:exact_end]
    assert route_module.MARKETMATCH_STT_ROUTE in exact_block
    assert "setup_marketmatch_stt_routes" in source
    assert "await shutdown_active_media_processes()" in source
    assert "await asyncio.to_thread(shutdown_active_workers)" in source
