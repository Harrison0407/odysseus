"""Focused lifecycle tests for the explicit MarketMatch Calls privilege."""

from __future__ import annotations

import importlib
import io
import json
import logging
import sys
import types
import wave
from pathlib import Path

import pytest

from tests.helpers.import_state import clear_module


def _fresh_auth_module():
    root = Path(__file__).resolve().parent.parent
    core = sys.modules.get("core")
    if core is None:
        core = types.ModuleType("core")
        sys.modules["core"] = core
    core.__path__ = [str(root / "core")]
    clear_module("core.auth")
    module = importlib.import_module("core.auth")
    module._hash_password = lambda password: f"hash:{password}"
    module._verify_password = lambda password, hashed: hashed == f"hash:{password}"
    return module


def _manager(tmp_path):
    module = _fresh_auth_module()
    return module, module.AuthManager(str(tmp_path / "auth.json"))


def _write_legacy(path: Path, privileges_marker):
    user = {
        "password_hash": "PRIVATE_PASSWORD_HASH_CANARY",
        "created": 1,
        "is_admin": True,
    }
    if privileges_marker is not None:
        user["privileges"] = privileges_marker
    path.write_text(json.dumps({"users": {"admin": user}}), encoding="utf-8")


def test_legacy_missing_key_is_atomically_backfilled_false_and_idempotent(
    tmp_path, monkeypatch, caplog
):
    path = tmp_path / "auth.json"
    _write_legacy(path, {})
    module = _fresh_auth_module()
    writes = 0
    original_write = module._atomic_write_json

    def counted_write(*args, **kwargs):
        nonlocal writes
        writes += 1
        return original_write(*args, **kwargs)

    monkeypatch.setattr(module, "_atomic_write_json", counted_write)
    with caplog.at_level(logging.INFO, logger="core.auth"):
        manager = module.AuthManager(str(path))
    assert manager.users["admin"]["privileges"][module.MARKETMATCH_PRIVILEGE] is False
    stored = json.loads(path.read_text())
    assert stored["users"]["admin"]["privileges"][module.MARKETMATCH_PRIVILEGE] is False
    assert writes == 1

    before = path.read_bytes()
    module.AuthManager(str(path))
    assert writes == 1
    assert path.read_bytes() == before
    assert "PRIVATE_PASSWORD_HASH_CANARY" not in caplog.text
    assert "password_hash" not in caplog.text


def test_legacy_missing_privilege_map_is_backfilled_but_malformed_map_is_not(tmp_path):
    missing_path = tmp_path / "missing.json"
    _write_legacy(missing_path, None)
    module = _fresh_auth_module()
    missing = module.AuthManager(str(missing_path))
    assert missing.users["admin"]["privileges"] == {module.MARKETMATCH_PRIVILEGE: False}

    malformed_path = tmp_path / "malformed.json"
    _write_legacy(malformed_path, [])
    malformed = module.AuthManager(str(malformed_path))
    assert malformed.users["admin"]["privileges"] == []
    assert malformed.get_privileges("admin")[module.MARKETMATCH_PRIVILEGE] is False


def test_migration_write_failure_leaves_auth_file_unchanged(tmp_path, monkeypatch):
    path = tmp_path / "auth.json"
    _write_legacy(path, {})
    before = path.read_bytes()
    module = _fresh_auth_module()

    def fail_write(*_args, **_kwargs):
        raise OSError("forced atomic write failure")

    monkeypatch.setattr(module, "_atomic_write_json", fail_write)
    with pytest.raises(OSError, match="forced atomic write failure"):
        module.AuthManager(str(path))
    assert path.read_bytes() == before


@pytest.mark.parametrize("is_admin", [False, True])
def test_new_users_store_explicit_false(tmp_path, is_admin):
    module, manager = _manager(tmp_path)
    assert manager.create_user("alice", "password-123", is_admin=is_admin) is True
    assert manager.users["alice"]["privileges"][module.MARKETMATCH_PRIVILEGE] is False
    assert manager.get_privileges("alice")[module.MARKETMATCH_PRIVILEGE] is False


def test_non_admin_can_be_granted_and_denied(tmp_path):
    module, manager = _manager(tmp_path)
    manager.create_user("alice", "password-123")
    assert manager.set_privileges("alice", {module.MARKETMATCH_PRIVILEGE: True}) is True
    assert manager.get_privileges("alice")[module.MARKETMATCH_PRIVILEGE] is True
    assert manager.set_privileges("alice", {module.MARKETMATCH_PRIVILEGE: False}) is True
    assert manager.get_privileges("alice")[module.MARKETMATCH_PRIVILEGE] is False


def test_admin_can_change_only_marketmatch_without_overriding_mandatory_privileges(tmp_path):
    module, manager = _manager(tmp_path)
    manager.create_user("admin", "password-123", is_admin=True)

    assert manager.set_privileges(
        "admin",
        {module.MARKETMATCH_PRIVILEGE: True, "can_use_bash": False, "block_all_models": True},
    ) is True
    effective = manager.get_privileges("admin")
    assert effective[module.MARKETMATCH_PRIVILEGE] is True
    assert effective["can_use_bash"] is True
    assert effective["block_all_models"] is False

    assert manager.set_privileges("admin", {module.MARKETMATCH_PRIVILEGE: False}) is True
    assert manager.get_privileges("admin")[module.MARKETMATCH_PRIVILEGE] is False
    assert manager.set_privileges("admin", {"can_use_bash": False}) is False
    assert manager.get_privileges("admin")["can_use_bash"] is True


async def test_supported_privilege_api_updates_admin_marketmatch_only(tmp_path):
    from routes.auth_routes import SESSION_COOKIE, setup_auth_routes

    module, manager = _manager(tmp_path)
    manager.create_user("admin", "password-123", is_admin=True)
    token = manager.create_session_trusted("admin")
    router = setup_auth_routes(manager)
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/auth/users/{username}/privileges"
    )

    class Request:
        cookies = {SESSION_COOKIE: token}

        async def json(self):
            return {
                module.MARKETMATCH_PRIVILEGE: True,
                "can_use_bash": False,
            }

    result = await endpoint(username="admin", request=Request())
    assert result["ok"] is True
    assert result["privileges"][module.MARKETMATCH_PRIVILEGE] is True
    assert result["privileges"]["can_use_bash"] is True
    assert manager.users["admin"]["privileges"][module.MARKETMATCH_PRIVILEGE] is True


def test_admin_marketmatch_opt_in_survives_role_round_trip(tmp_path):
    module, manager = _manager(tmp_path)
    manager.create_user("admin", "password-123", is_admin=True)
    manager.create_user("alice", "password-123")
    assert manager.set_privileges("alice", {module.MARKETMATCH_PRIVILEGE: True}) is True
    assert manager.set_admin("alice", True, "admin") is module.SetAdminResult.OK
    assert manager.get_privileges("alice")[module.MARKETMATCH_PRIVILEGE] is True
    assert manager.set_privileges("alice", {module.MARKETMATCH_PRIVILEGE: False}) is True
    assert manager.set_admin("alice", False, "admin") is module.SetAdminResult.OK
    assert manager.get_privileges("alice")[module.MARKETMATCH_PRIVILEGE] is False


def _canonical_wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\0\0" * 160)
    return output.getvalue()


@pytest.mark.parametrize(
    ("stored", "expected_code"),
    [({}, "AUTH_STATE_UNAVAILABLE"), ({"can_use_marketmatch": False}, "MARKETMATCH_FORBIDDEN")],
)
async def test_admin_without_stored_opt_in_is_blocked_before_body_or_worker(stored, expected_code):
    from tests.test_marketmatch_stt_routes import (
        CountedReceive,
        FakeAuthManager,
        _endpoint,
        _request,
    )

    users = {"alice": {"is_admin": True, "privileges": stored}}
    manager = FakeAuthManager(users=users, privileges={"can_use_marketmatch": True})
    receive = CountedReceive()
    worker_calls = 0

    async def transcriber(*_args, **_kwargs):
        nonlocal worker_calls
        worker_calls += 1
        raise AssertionError("worker must not run")

    response = await _endpoint(transcriber)(_request(receive, auth_manager=manager))
    assert response.status_code == 403
    assert json.loads(response.body)["error"] == expected_code
    assert receive.calls == 0
    assert worker_calls == 0


async def test_admin_stored_true_authorizes_calls(tmp_path, monkeypatch):
    from src.marketmatch_stt_process import MarketMatchProcessResult
    from tests.test_marketmatch_stt_routes import CountedReceive, _endpoint, _request

    monkeypatch.setenv("AUTH_ENABLED", "true")
    module, manager = _manager(tmp_path)
    manager.create_user("admin", "password-123", is_admin=True)
    manager.set_privileges("admin", {module.MARKETMATCH_PRIVILEGE: True})

    class GateManager:
        is_configured = True
        users = manager.users

        @staticmethod
        def get_username_for_token(token):
            return "alice" if token == "valid-cookie" else None

        @staticmethod
        def get_privileges(username):
            return manager.get_privileges(username)

    manager._config["users"]["alice"] = manager._config["users"].pop("admin")
    wav_bytes = _canonical_wav()
    receive = CountedReceive([{"type": "http.request", "body": wav_bytes, "more_body": False}])
    worker_calls = 0

    async def transcriber(wav_path, *, byte_limit, duration_limit_ms, deadline):
        nonlocal worker_calls
        worker_calls += 1
        assert wav_path.is_file()
        assert byte_limit > 44 and duration_limit_ms == 21_600_000 and deadline > 0
        return MarketMatchProcessResult(duration_ms=10, transcript_text="", segments=())

    request = _request(
        receive,
        auth_manager=GateManager(),
        headers={"content-length": str(len(wav_bytes))},
    )
    response = await _endpoint(transcriber)(request)
    assert response.status_code == 200
    assert receive.calls == 1
    assert worker_calls == 1
