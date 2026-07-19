import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request

from routes import marketmatch_calls_analysis_routes as route_module
from routes.auth_routes import SESSION_COOKIE
from src import llm_core


class FakeAuthManager:
    def __init__(self, *, stored=True, effective=True, configured=True):
        self.is_configured = configured
        self.users = {
            "alice": {
                "is_admin": False,
                "privileges": {"can_use_marketmatch": stored},
            }
        }
        self._effective = effective

    def get_username_for_token(self, token):
        return "alice" if token == "valid-cookie" else None

    def get_privileges(self, username):
        assert username == "alice"
        return {"can_use_marketmatch": self._effective}


class CountedReceive:
    def __init__(self, body: bytes):
        self.body = body
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        body, self.body = self.body, b""
        return {"type": "http.request", "body": body, "more_body": False}


def _request(payload=None, *, auth=None, cookie=True, current_user="alice", headers=None):
    body = json.dumps(payload if payload is not None else {"transcript": "hello"}).encode()
    raw_headers = {
        "content-type": "application/json",
        "content-length": str(len(body)),
    }
    if cookie:
        raw_headers["cookie"] = f"{SESSION_COOKIE}=valid-cookie"
    raw_headers.update(headers or {})
    receive = CountedReceive(body)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": route_module.MARKETMATCH_CALLS_ANALYSIS_ROUTE,
        "raw_path": route_module.MARKETMATCH_CALLS_ANALYSIS_ROUTE.encode(),
        "query_string": b"",
        "headers": [(key.encode(), value.encode()) for key, value in raw_headers.items()],
        "client": ("203.0.113.10", 1234),
        "server": ("test", 80),
        "state": {"current_user": current_user, "api_token": False},
        "app": SimpleNamespace(state=SimpleNamespace(auth_manager=auth or FakeAuthManager())),
    }
    return Request(scope, receive=receive), receive


def _valid_result(**overrides):
    result = {
        "summary": "A concise summary.",
        "decisions": ["Use the revised plan."],
        "action_items": [{"task": "Send it.", "owner": None, "due_date": None}],
        "open_questions": ["When will review finish?"],
    }
    result.update(overrides)
    return json.dumps(result)


def _endpoint(resolver=None, invoker=None):
    resolver = resolver or (
        lambda owner: route_module.LocalAnalysisEndpoint(
            "http://127.0.0.1:1234/v1/chat/completions", "local-model", {}
        )
    )

    async def default_invoker(*args, **kwargs):
        return _valid_result()

    router = route_module.setup_marketmatch_calls_analysis_routes(
        resolver, invoker or default_invoker
    )
    return next(
        route.endpoint
        for route in router.routes
        if route.path == route_module.MARKETMATCH_CALLS_ANALYSIS_ROUTE
    )


def _response_json(response):
    return json.loads(response.body)


@pytest.fixture(autouse=True)
def _auth_enabled(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")


@pytest.mark.asyncio
async def test_unauthenticated_rejected_before_body_consumption():
    request, receive = _request(cookie=False)
    response = await _endpoint()(request)
    assert response.status_code == 401
    assert _response_json(response)["error"] == "AUTH_REQUIRED"
    assert receive.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stored,effective", [(False, True), (True, False)])
async def test_marketmatch_privilege_required_before_body(stored, effective):
    request, receive = _request(auth=FakeAuthManager(stored=stored, effective=effective))
    response = await _endpoint()(request)
    assert response.status_code == 403
    assert _response_json(response)["error"] == "MARKETMATCH_FORBIDDEN"
    assert receive.calls == 0


@pytest.mark.asyncio
async def test_authorized_request_invokes_local_model_once_with_safe_options():
    calls = []

    async def invoke(*args, **kwargs):
        calls.append((args, kwargs))
        return _valid_result()

    request, _ = _request({"transcript": "  meeting text  "})
    response = await _endpoint(invoker=invoke)(request)
    assert response.status_code == 200
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0].startswith("http://127.0.0.1:")
    assert args[1] == "local-model"
    assert "meeting text" not in args[0]
    assert args[2][1] == {"role": "user", "content": "Transcript:\nmeeting text"}
    assert kwargs["max_retries"] == 1
    assert kwargs["use_cache"] is False
    assert kwargs["temperature"] == 0.0
    assert kwargs["timeout"] == 120
    assert kwargs["max_tokens"] == 2048
    assert "session_id" not in kwargs


def test_analysis_prompt_has_conservative_classification_rules_and_examples():
    prompt = route_module._messages("test transcript")[0]["content"]
    required_rules = (
        "A decision is not an action item.",
        "Never rewrite a decision as an imperative task.",
        "decided, agreed, approved, selected, chose, confirmed, resolved",
        "Agreement to an option or selection is a decision",
        "task, assignment,\nrequest, promise, obligation, or future action",
        "Discussion language such as discussed, considered, explored, or reviewed",
        "When classification\nis uncertain, omit the item rather than inventing it.",
    )
    assert all(rule in prompt for rule in required_rules)

    assert 'Transcript: “The team decided to use the current plan for the drawings.”' in prompt
    assert '"decisions":["The team decided to use the current plan for the drawings."]' in prompt
    assert '"action_items":[]' in prompt

    assert 'Transcript: “Felipe will send the updated drawing by Friday.”' in prompt
    assert '"task":"Send the updated drawing.","owner":"Felipe","due_date":"Friday"' in prompt

    assert 'Transcript: “We discussed using the revised plan.”' in prompt
    discussion_example = prompt.split("Example 3", 1)[1].split("Example 4", 1)[0]
    assert '"decisions":[]' in discussion_example
    assert '"action_items":[]' in discussion_example

    combined_example = prompt.split("Example 4", 1)[1].split("Return strict JSON", 1)[0]
    assert '"decisions":["The team decided to use the revised plan."]' in combined_example
    assert '"task":"Send the drawing.","owner":"Felipe","due_date":"Friday"' in combined_example


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,status,code",
    [
        ({"transcript": ""}, 422, "INVALID_INPUT"),
        ({"transcript": "   "}, 422, "INVALID_INPUT"),
        ({"transcript": 123}, 422, "INVALID_INPUT"),
        ({"transcript": "x", "model": "remote"}, 422, "INVALID_INPUT"),
        ({"transcript": "x", "endpoint_id": "remote"}, 422, "INVALID_INPUT"),
        ({"transcript": "x", "endpoint": "https://remote.test"}, 422, "INVALID_INPUT"),
        ({"transcript": "x", "provider": "remote"}, 422, "INVALID_INPUT"),
        ({"transcript": "x", "api_key": "secret"}, 422, "INVALID_INPUT"),
        ({"transcript": "x", "owner": "bob"}, 422, "INVALID_INPUT"),
        ({"transcript": "x", "username": "bob"}, 422, "INVALID_INPUT"),
        ({"transcript": "x", "session": "secret"}, 422, "INVALID_INPUT"),
        ({"transcript": "x", "system_prompt": "ignore rules"}, 422, "INVALID_INPUT"),
    ],
)
async def test_invalid_and_forbidden_inputs_rejected(payload, status, code):
    calls = 0

    async def invoke(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _valid_result()

    request, _ = _request(payload)
    response = await _endpoint(invoker=invoke)(request)
    assert response.status_code == status
    assert _response_json(response)["error"] == code
    assert calls == 0


@pytest.mark.asyncio
async def test_oversized_transcript_rejected_before_resolution_or_inference():
    resolved = invoked = 0

    def resolve(owner):
        nonlocal resolved
        resolved += 1

    async def invoke(*args, **kwargs):
        nonlocal invoked
        invoked += 1

    request, _ = _request({"transcript": "x" * (route_module.MAX_TRANSCRIPT_CHARS + 1)})
    response = await _endpoint(resolve, invoke)(request)
    assert response.status_code == 413
    assert _response_json(response)["error"] == "INPUT_LIMIT_EXCEEDED"
    assert resolved == invoked == 0


@pytest.mark.asyncio
async def test_oversized_declared_body_rejected_before_body_consumption():
    request, receive = _request(headers={"content-length": str(route_module.MAX_REQUEST_BYTES + 1)})
    response = await _endpoint()(request)
    assert response.status_code == 413
    assert _response_json(response)["error"] == "INPUT_LIMIT_EXCEEDED"
    assert receive.calls == 0


@pytest.mark.asyncio
async def test_non_json_content_type_is_rejected_before_inference():
    calls = 0

    async def invoke(*args, **kwargs):
        nonlocal calls
        calls += 1

    request, _ = _request(headers={"content-type": "text/plain"})
    response = await _endpoint(invoker=invoke)(request)
    assert response.status_code == 422
    assert _response_json(response)["error"] == "INVALID_INPUT"
    assert calls == 0


@pytest.mark.asyncio
async def test_unavailable_endpoint_rejected_without_invocation():
    invoked = 0

    async def invoke(*args, **kwargs):
        nonlocal invoked
        invoked += 1

    request, _ = _request()
    response = await _endpoint(lambda owner: None, invoke)(request)
    assert response.status_code == 503
    assert _response_json(response) == {
        "error": "LOCAL_MODEL_UNAVAILABLE",
        "message": "Local analysis is unavailable.",
    }
    assert invoked == 0


@pytest.mark.asyncio
async def test_runtime_failure_is_safe_and_has_no_fallback():
    calls = 0

    async def invoke(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("private provider detail")

    request, _ = _request()
    response = await _endpoint(invoker=invoke)(request)
    assert calls == 1
    assert response.status_code == 503
    assert _response_json(response) == {
        "error": "ANALYSIS_FAILED",
        "message": "Local analysis failed.",
    }
    assert b"private provider detail" not in response.body


@pytest.mark.asyncio
async def test_invalid_model_output_returns_only_fixed_safe_error():
    raw_output = "private malformed model output"

    async def invoke(*args, **kwargs):
        return raw_output

    request, _ = _request()
    response = await _endpoint(invoker=invoke)(request)
    assert response.status_code == 502
    assert _response_json(response) == {
        "error": "INVALID_MODEL_OUTPUT",
        "message": "Local analysis returned an invalid result.",
    }
    assert raw_output.encode() not in response.body


@pytest.mark.asyncio
async def test_timeout_is_safe(monkeypatch):
    monkeypatch.setattr(route_module, "ANALYSIS_TIMEOUT_SECONDS", 0.001)

    async def invoke(*args, **kwargs):
        await asyncio.sleep(1)

    request, _ = _request()
    response = await _endpoint(invoker=invoke)(request)
    assert response.status_code == 504
    assert _response_json(response)["error"] == "ANALYSIS_TIMEOUT"


@pytest.mark.asyncio
async def test_request_cancellation_cancels_local_inference():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def invoke(*args, **kwargs):
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    request, _ = _request()
    task = asyncio.create_task(_endpoint(invoker=invoke)(request))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "prefix\n```json\n" + _valid_result() + "\n```",
        "```\n" + _valid_result() + "\n```",
        "```json\n" + _valid_result() + "\n```\nsuffix",
        "```json\n```json\n" + _valid_result() + "\n```\n```",
        json.dumps(["not", "an", "object"]),
        json.dumps({"decisions": [], "action_items": [], "open_questions": []}),
        _valid_result(decisions="wrong"),
        _valid_result(action_items=[{"task": "x"}]),
        _valid_result(action_items=[{"task": "", "owner": None, "due_date": None}]),
        _valid_result(open_questions=["x"] * (route_module.MAX_LIST_ITEMS + 1)),
        _valid_result(extra="not allowed"),
    ],
)
def test_invalid_model_outputs_are_rejected_without_raw_echo(raw):
    with pytest.raises(route_module.AnalysisError) as exc_info:
        route_module.validate_model_output(raw)
    assert exc_info.value.code is route_module.AnalysisCode.INVALID_MODEL_OUTPUT
    assert raw not in str(exc_info.value)


@pytest.mark.parametrize(
    "raw",
    [
        json.loads(_valid_result()),
        " \r\n\t" + _valid_result() + " \n",
        "\ufeff  " + _valid_result() + " \n",
        "```json\n" + _valid_result() + "\n```",
        "  ```json\r\n" + _valid_result() + "\r\n```  ",
    ],
)
def test_safe_model_output_wrappers_are_normalized_before_strict_validation(raw):
    result = route_module.validate_model_output(raw)
    assert result.summary == "A concise summary."
    assert result.decisions == ["Use the revised plan."]


@pytest.mark.parametrize(
    "raw",
    [
        {"choices": [{"message": {"content": _valid_result()}}]},
        {"summary": "x", "decisions": [], "action_items": [], "open_questions": [], "extra": "x"},
    ],
)
def test_decoded_objects_still_require_the_exact_analysis_schema(raw):
    with pytest.raises(route_module.AnalysisError) as exc_info:
        route_module.validate_model_output(raw)
    assert exc_info.value.code is route_module.AnalysisCode.INVALID_MODEL_OUTPUT


def test_bounded_output_fields_and_total_are_enforced():
    too_long = "x" * (route_module.MAX_SUMMARY_CHARS + 1)
    with pytest.raises(route_module.AnalysisError):
        route_module.validate_model_output(_valid_result(summary=too_long))
    item_too_long = "x" * (route_module.MAX_LIST_ITEM_CHARS + 1)
    with pytest.raises(route_module.AnalysisError):
        route_module.validate_model_output(_valid_result(decisions=[item_too_long]))
    large_items = ["x" * route_module.MAX_LIST_ITEM_CHARS] * 16
    with pytest.raises(route_module.AnalysisError):
        route_module.validate_model_output(
            _valid_result(decisions=large_items, open_questions=["y"])
        )


def test_valid_empty_lists_null_fields_and_html_as_plain_data():
    raw = _valid_result(
        summary="<script>alert('x')</script>",
        decisions=[],
        action_items=[{"task": "Review safely.", "owner": None, "due_date": None}],
        open_questions=[],
    )
    result = route_module.validate_model_output(raw)
    assert result.summary == "<script>alert('x')</script>"
    assert result.decisions == []
    assert result.action_items[0].owner is None
    assert result.action_items[0].due_date is None


@pytest.mark.parametrize(
    "kind,url,accepted",
    [
        ("local", "https://explicit-local.invalid/v1/chat/completions", True),
        ("auto", "http://127.0.0.1:1234/v1/chat/completions", True),
        ("api", "https://api.example/v1/chat/completions", False),
        ("proxy", "https://proxy.example/v1/chat/completions", False),
        ("mystery", "http://127.0.0.1:1234/v1/chat/completions", False),
        (None, "http://127.0.0.1:1234/v1/chat/completions", False),
    ],
)
def test_local_endpoint_enforcement(monkeypatch, kind, url, accepted):
    monkeypatch.setattr(route_module, "_selected_endpoint", lambda owner: ("ep-1", "model-1"))
    monkeypatch.setattr(route_module, "_endpoint_kind", lambda endpoint_id, owner: kind)
    resolve_calls = []
    local_checks = []

    def resolve(endpoint_id, model=None, owner=None):
        resolve_calls.append((endpoint_id, model, owner))
        return url, "model-1", {}

    def local(candidate):
        local_checks.append(candidate)
        return candidate.startswith("http://127.0.0.1:") or kind == "local"

    monkeypatch.setattr(route_module, "resolve_endpoint_by_id", resolve)
    monkeypatch.setattr(route_module, "is_local_endpoint", local)
    result = route_module.resolve_local_analysis_endpoint("alice")
    assert (result is not None) is accepted
    if kind not in {"auto", "local"}:
        assert resolve_calls == []
        assert local_checks == []


def test_auto_remote_endpoint_is_rejected_after_existing_local_check(monkeypatch):
    monkeypatch.setattr(route_module, "_selected_endpoint", lambda owner: ("ep-1", "model-1"))
    monkeypatch.setattr(route_module, "_endpoint_kind", lambda endpoint_id, owner: "auto")
    monkeypatch.setattr(
        route_module,
        "resolve_endpoint_by_id",
        lambda *args, **kwargs: ("https://remote.example/v1/chat/completions", "model-1", {}),
    )
    monkeypatch.setattr(route_module, "is_local_endpoint", lambda url: False)
    assert route_module.resolve_local_analysis_endpoint("alice") is None


@pytest.mark.parametrize(
    "resolved",
    [
        None,
        ("", "model-1", {}),
        ("http://127.0.0.1:1234/v1/chat/completions", "", {}),
        ("http://127.0.0.1:1234/v1/chat/completions", "model-1", "bad"),
        ("http://127.0.0.1:1234/v1/chat/completions", "model-1", {1: "bad"}),
    ],
)
def test_malformed_resolved_endpoint_is_rejected(monkeypatch, resolved):
    monkeypatch.setattr(route_module, "_selected_endpoint", lambda owner: ("ep-1", "model-1"))
    monkeypatch.setattr(route_module, "_endpoint_kind", lambda endpoint_id, owner: "local")
    monkeypatch.setattr(route_module, "resolve_endpoint_by_id", lambda *args, **kwargs: resolved)
    monkeypatch.setattr(route_module, "is_local_endpoint", lambda url: True)
    assert route_module.resolve_local_analysis_endpoint("alice") is None


def test_utility_selection_does_not_fall_back_when_configured(monkeypatch):
    monkeypatch.setattr(
        route_module,
        "load_settings",
        lambda: {
            "utility_endpoint_id": "utility",
            "utility_model": "utility-model",
            "default_endpoint_id": "remote-default",
            "default_model": "remote-model",
        },
    )
    monkeypatch.setattr(
        route_module,
        "get_user_setting",
        lambda key, owner, default=None: default,
    )
    assert route_module._selected_endpoint("alice") == ("utility", "utility-model")


def test_default_selected_only_when_utility_absent(monkeypatch):
    monkeypatch.setattr(
        route_module,
        "load_settings",
        lambda: {
            "utility_endpoint_id": "",
            "utility_model": "",
            "default_endpoint_id": "default",
            "default_model": "default-model",
        },
    )
    monkeypatch.setattr(
        route_module,
        "get_user_setting",
        lambda key, owner, default=None: default,
    )
    assert route_module._selected_endpoint("alice") == ("default", "default-model")


def test_route_contains_no_persistence_or_fallback_calls():
    source = open(route_module.__file__, encoding="utf-8").read()
    assert "llm_call_async_with_fallback" not in source
    assert "resolve_endpoint(" not in source
    assert ".commit(" not in source
    assert ".add(" not in source
    assert "Document(" not in source
    assert "Chat(" not in source
    assert "Message(" not in source
    assert "logger." not in source
    assert "print(" not in source


def test_app_registers_route_and_only_an_exact_timeout_exemption():
    source = open("app.py", encoding="utf-8").read()
    exact_start = source.index("_TIMEOUT_EXEMPT_EXACT =")
    exact_end = source.index("\n}", exact_start)
    exact_block = source[exact_start:exact_end]
    prefixes_start = source.index("_TIMEOUT_EXEMPT_PREFIXES =")
    prefixes_end = source.index("\n)", prefixes_start)
    prefixes_block = source[prefixes_start:prefixes_end]
    assert route_module.MARKETMATCH_CALLS_ANALYSIS_ROUTE in exact_block
    assert route_module.MARKETMATCH_CALLS_ANALYSIS_ROUTE not in prefixes_block
    assert "setup_marketmatch_calls_analysis_routes()" in source


@pytest.mark.asyncio
async def test_llm_primitive_cache_opt_out_is_explicit_and_default_is_unchanged(monkeypatch):
    network_calls = []
    cache_writes = []

    class FakeClient:
        async def post(self, url, **kwargs):
            network_calls.append((url, kwargs))
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                request=request,
                json={"choices": [{"message": {"content": "fresh"}}]},
            )

    monkeypatch.setattr(llm_core, "_get_cached_response", lambda key: "cached")
    monkeypatch.setattr(llm_core, "_set_cached_response", lambda key, value: cache_writes.append(value))
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: FakeClient())
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda url, model: None)

    messages = [{"role": "user", "content": "unique cache test"}]
    cached = await llm_core.llm_call_async(
        "http://127.0.0.1:54321/v1", "local-model", messages, max_retries=1
    )
    assert cached == "cached"
    assert network_calls == []

    fresh = await llm_core.llm_call_async(
        "http://127.0.0.1:54321/v1",
        "local-model",
        messages,
        max_retries=1,
        use_cache=False,
    )
    assert fresh == "fresh"
    assert len(network_calls) == 1
    assert cache_writes == []


@pytest.mark.asyncio
async def test_llm_primitive_prefers_final_content_over_reasoning_content(monkeypatch):
    final = _valid_result()

    class FakeClient:
        async def post(self, url, **kwargs):
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": final,
                                "reasoning_content": "not final JSON",
                            }
                        }
                    ]
                },
            )

    monkeypatch.setattr(llm_core, "_get_http_client", lambda: FakeClient())
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda url, model: None)

    result = await llm_core.llm_call_async(
        "http://127.0.0.1:54321/v1",
        "local-model",
        [{"role": "user", "content": "response extraction test"}],
        max_retries=1,
        use_cache=False,
    )
    assert result == final
