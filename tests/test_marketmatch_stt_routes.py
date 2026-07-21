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
    monkeypatch.setattr(
        route_module,
        "canonical_wav_file_duration_ms",
        lambda *_args, **_kwargs: 1,
    )
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
            language="zh",
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
        + b'","language":"zh","language_confidence":0.84}'
    )


async def test_route_revalidates_injected_result_with_worker_contract():
    canary = "PRIVATE_TRANSCRIPT_CANARY"

    async def malformed(*_args, **_kwargs):
        return MarketMatchProcessResult(
            duration_ms=10,
            transcript_text=canary,
            segments=((0.0, 10, canary),),
            language="zh-Hans",
        )

    response = await _endpoint(malformed)(_request(CountedReceive()))

    assert response.status_code == 502
    assert response.body == b'{"error":"WORKER_PROTOCOL_ERROR","message":"Transcription result was invalid."}'
    assert canary.encode() not in response.body


async def test_route_accepts_long_canonical_mandarin_result():
    text = "虚构。" * 4_001

    async def long_result(*_args, **_kwargs):
        return MarketMatchProcessResult(
            duration_ms=10,
            transcript_text=text,
            segments=((0, 10, text),),
            language="zh",
            language_confidence=None,
        )

    response = await _endpoint(long_result)(_request(CountedReceive()))

    assert response.status_code == 200
    assert len(text) > 12_000
    assert b'"language":"zh"' in response.body


async def test_requested_mandarin_header_reaches_transcriber_without_ui_locale():
    observed = []

    async def transcriber(wav_path, *, byte_limit, duration_limit_ms, deadline, requested_language):
        observed.append(requested_language)
        return await _ok_transcriber(
            wav_path, byte_limit=byte_limit, duration_limit_ms=duration_limit_ms, deadline=deadline
        )

    request = _request(
        CountedReceive(), headers={"x-marketmatch-transcription-language": "zh"}
    )
    response = await _endpoint(transcriber)(request)
    assert response.status_code == 200
    assert observed == ["zh"]


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


@pytest.mark.parametrize(
    ("duration_ms", "expected_seconds"),
    [(1_000, 600.0), (900_000, 900.0), (3_600_000, 3_600.0)],
)
def test_duration_aware_worker_timeout_policy(duration_ms, expected_seconds):
    assert route_module.transcription_timeout_seconds(duration_ms) == expected_seconds


def test_configured_worker_timeout_policy_is_bounded(monkeypatch):
    monkeypatch.setattr(route_module, "MARKETMATCH_STT_TIMEOUT_MIN_SECONDS", 30.0)
    monkeypatch.setattr(route_module, "MARKETMATCH_STT_TIMEOUT_SECONDS_PER_AUDIO_SECOND", 2.0)
    monkeypatch.setattr(route_module, "MARKETMATCH_STT_TIMEOUT_SECONDS", 120.0)
    assert route_module.transcription_timeout_seconds(1_000) == 30.0
    assert route_module.transcription_timeout_seconds(40_000) == 80.0
    assert route_module.transcription_timeout_seconds(3_600_000) == 120.0


@pytest.mark.parametrize("raw", ["0", "-1", "nan", "inf", "not-a-number"])
def test_malformed_timeout_configuration_fails_safely(monkeypatch, raw):
    monkeypatch.setenv("MARKETMATCH_STT_TIMEOUT_FIXTURE", raw)
    with pytest.raises(ValueError) as caught:
        route_module._timeout_env("MARKETMATCH_STT_TIMEOUT_FIXTURE", 1.0, maximum=10.0)
    assert raw not in str(caught.value)


async def test_fifteen_minute_audio_gets_duration_budget_not_historical_short_timeout(monkeypatch):
    monkeypatch.setattr(
        route_module,
        "canonical_wav_file_duration_ms",
        lambda *_args, **_kwargs: 900_000,
    )
    observed = []

    async def transcriber(*args, deadline, **kwargs):
        del args, kwargs
        observed.append(deadline - asyncio.get_running_loop().time())
        return MarketMatchProcessResult(900_000, "ok", ((0, 900_000, "ok"),))

    response = await _endpoint(transcriber)(_request(CountedReceive()))
    assert response.status_code == 200
    assert observed[0] > 899


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (MarketMatchProcessError(route_module.MarketMatchProcessCode.WORKER_TIMEOUT), 504, b"WORKER_TIMEOUT"),
        (MarketMatchProcessError(route_module.MarketMatchProcessCode.WORKER_CRASHED), 503, b"WORKER_CRASHED"),
    ],
)
async def test_worker_timeout_and_crash_are_not_result_validation(error, status, code):
    async def failed(*_args, **_kwargs):
        raise error

    response = await _endpoint(failed)(_request(CountedReceive()))
    assert response.status_code == status
    assert code in response.body
    assert b"WORKER_PROTOCOL_ERROR" not in response.body


async def test_safe_failure_log_carries_stage_code_duration_and_field_type(caplog):
    async def invalid(*_args, **_kwargs):
        raise MarketMatchProcessError(
            route_module.MarketMatchProcessCode.WORKER_PROTOCOL_ERROR,
            worker_exit_state="clean_exit",
            result_field="segments.order",
            result_type="tuple",
        )

    with caplog.at_level("WARNING", logger="marketmatch.stt"):
        response = await _endpoint(invalid)(_request(CountedReceive()))

    assert response.status_code == 502
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert "stage=worker_result_validation" in rendered
    assert "code=WORKER_PROTOCOL_ERROR" in rendered
    assert "audio_duration_ms=1" in rendered
    assert "worker_exit_state=clean_exit" in rendered
    assert "result_field=segments.order" in rendered
    assert "result_type=tuple" in rendered
    assert "/private/" not in rendered


async def test_canonical_preflight_error_preserves_stable_code_and_stage(monkeypatch, caplog):
    def invalid_canonical(*_args, **_kwargs):
        raise route_module.CanonicalWavError(route_module.CanonicalWavCode.INVALID_WAV)

    monkeypatch.setattr(route_module, "canonical_wav_file_duration_ms", invalid_canonical)
    with caplog.at_level("WARNING", logger="marketmatch.stt"):
        response = await _endpoint(_ok_transcriber)(_request(CountedReceive()))

    assert response.status_code == 422
    assert b'"error":"INVALID_WAV"' in response.body
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert "stage=canonical_validation" in rendered
    assert "code=INVALID_WAV" in rendered


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
