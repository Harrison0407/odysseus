"""Phase 2 containment tests for usernames used as persistent owner keys."""

import contextlib
import json
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.helpers.import_state import clear_module


class _OwnerColumn:
    def __eq__(self, other):
        return ("owner ==", other)


class _FakeApiToken:
    owner = _OwnerColumn()


class _FakeQuery:
    def filter(self, *_conditions):
        return self

    def delete(self, *args, **kwargs):
        return 0


class _FakeSession:
    def query(self, model):
        assert model is _FakeApiToken
        return _FakeQuery()


@pytest.fixture(autouse=True)
def _stub_api_token_purge(monkeypatch):
    @contextlib.contextmanager
    def _fake_db_session():
        yield _FakeSession()

    db_stub = types.ModuleType("core.database")
    db_stub.get_db_session = _fake_db_session
    db_stub.ApiToken = _FakeApiToken
    monkeypatch.setitem(sys.modules, "core.database", db_stub)


def _manager(monkeypatch, auth_path):
    clear_module("core.auth")
    import core.auth as auth_mod

    monkeypatch.setattr(auth_mod, "_hash_password", lambda password: f"hash:{password}")
    monkeypatch.setattr(
        auth_mod,
        "_verify_password",
        lambda password, hashed: hashed == f"hash:{password}",
    )
    return auth_mod.AuthManager(str(auth_path))


def _seed(monkeypatch, tmp_path):
    manager = _manager(monkeypatch, tmp_path / "auth.json")
    assert manager.create_user("admin", "admin-password", is_admin=True)
    assert manager.create_user("alice", "alice-password")
    assert manager.create_user("bob", "bob-password")
    return manager


def _retired_on_disk(tmp_path):
    data = json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))
    return set(data.get("retired_usernames", []))


def test_delete_then_recreate_retired_username_is_denied(monkeypatch, tmp_path):
    manager = _seed(monkeypatch, tmp_path)

    assert manager.delete_user("alice", "admin") is True

    assert manager.create_user("alice", "new-password") is False
    assert "alice" not in manager.users
    assert _retired_on_disk(tmp_path) == {"alice"}


def test_delete_then_rename_into_retired_username_is_denied(monkeypatch, tmp_path):
    manager = _seed(monkeypatch, tmp_path)

    assert manager.delete_user("alice", "admin") is True

    assert manager.rename_user("bob", "alice", "admin") is False
    assert "bob" in manager.users
    assert "alice" not in manager.users


def test_rename_then_recreate_source_username_is_denied(monkeypatch, tmp_path):
    manager = _seed(monkeypatch, tmp_path)

    assert manager.rename_user("alice", "alice2", "admin") is True

    assert manager.create_user("alice", "new-password") is False
    assert "alice2" in manager.users
    assert "alice" not in manager.users


def test_rename_then_rename_another_user_into_source_is_denied(monkeypatch, tmp_path):
    manager = _seed(monkeypatch, tmp_path)

    assert manager.rename_user("alice", "alice2", "admin") is True

    assert manager.rename_user("bob", "alice", "admin") is False
    assert "bob" in manager.users
    assert "alice2" in manager.users


def test_retired_usernames_persist_across_manager_restart(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.json"
    manager = _seed(monkeypatch, tmp_path)
    assert manager.delete_user("alice", "admin") is True

    restarted = _manager(monkeypatch, auth_path)

    assert restarted.create_user("alice", "new-password") is False
    assert restarted.rename_user("bob", "alice", "admin") is False


def test_retired_username_normalization_blocks_case_based_reuse(monkeypatch, tmp_path):
    manager = _seed(monkeypatch, tmp_path)
    assert manager.rename_user("Alice", "Alice2", "Admin") is True

    assert manager.create_user(" ALICE ", "new-password") is False
    assert manager.rename_user("BOB", "Alice", "ADMIN") is False
    assert _retired_on_disk(tmp_path) == {"alice"}


def test_concurrent_create_delete_and_rename_cannot_bypass_retirement(monkeypatch, tmp_path):
    manager = _seed(monkeypatch, tmp_path)
    barrier = threading.Barrier(3)

    def delete_alice():
        barrier.wait()
        return manager.delete_user("alice", "admin")

    def create_alice():
        barrier.wait()
        return manager.create_user("ALICE", "replacement-password")

    def rename_bob_to_alice():
        barrier.wait()
        return manager.rename_user("bob", "Alice", "admin")

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(
            pool.map(
                lambda operation: operation(),
                (delete_alice, create_alice, rename_bob_to_alice),
            )
        )

    assert results.count(True) == 1
    assert "alice" not in manager.users
    assert "bob" in manager.users
    assert manager.create_user("alice", "later-password") is False
    assert _retired_on_disk(tmp_path) == {"alice"}


def test_successful_rename_retires_only_the_source(monkeypatch, tmp_path):
    manager = _seed(monkeypatch, tmp_path)

    assert manager.rename_user("alice", "alice2", "admin") is True

    assert _retired_on_disk(tmp_path) == {"alice"}
    assert manager.rename_user("alice2", "alice3", "admin") is True
    assert _retired_on_disk(tmp_path) == {"alice", "alice2"}
    assert "alice3" in manager.users


def test_failed_auth_config_rename_restores_identity_and_retirement(monkeypatch, tmp_path):
    manager = _seed(monkeypatch, tmp_path)
    original_save = manager._save
    save_attempts = 0

    def fail_once():
        nonlocal save_attempts
        save_attempts += 1
        if save_attempts == 1:
            raise OSError("forced auth persistence failure")
        return original_save()

    monkeypatch.setattr(manager, "_save", fail_once)

    assert manager.rename_user("alice", "alice2", "admin") is False
    assert "alice" in manager.users
    assert "alice2" not in manager.users
    assert _retired_on_disk(tmp_path) == set()


def test_failed_session_rename_restores_identity_retirement_and_session(monkeypatch, tmp_path):
    manager = _seed(monkeypatch, tmp_path)
    alice_token = manager.create_session_trusted("alice")
    original_save_sessions = manager._save_sessions
    save_attempts = 0

    def fail_once():
        nonlocal save_attempts
        save_attempts += 1
        if save_attempts == 1:
            return False
        return original_save_sessions()

    monkeypatch.setattr(manager, "_save_sessions", fail_once)

    assert manager.rename_user("alice", "alice2", "admin") is False
    assert "alice" in manager.users
    assert "alice2" not in manager.users
    assert manager.get_username_for_token(alice_token) == "alice"
    assert _retired_on_disk(tmp_path) == set()


def test_failed_delete_persistence_restores_user_without_retirement(monkeypatch, tmp_path):
    manager = _seed(monkeypatch, tmp_path)
    original_save = manager._save
    save_attempts = 0

    def fail_once():
        nonlocal save_attempts
        save_attempts += 1
        if save_attempts == 1:
            raise OSError("forced auth persistence failure")
        return original_save()

    monkeypatch.setattr(manager, "_save", fail_once)

    assert manager.delete_user("alice", "admin") is False
    assert "alice" in manager.users
    assert _retired_on_disk(tmp_path) == set()


def test_rollback_is_bound_to_the_exact_pending_rename(monkeypatch, tmp_path):
    manager = _seed(monkeypatch, tmp_path)
    assert manager.rename_user(
        "alice",
        "alice2",
        "admin",
        prepare_rollback=True,
    ) is True

    assert manager.rollback_user_rename("bob", "alice", "admin") is False
    assert "alice2" in manager.users
    assert "bob" in manager.users
    assert "alice" not in manager.users
    assert _retired_on_disk(tmp_path) == {"alice"}

    assert manager.rollback_user_rename("alice2", "alice", "admin") is True
    assert "alice" in manager.users
    assert "alice2" not in manager.users
    assert _retired_on_disk(tmp_path) == set()


def test_finalized_rename_cannot_be_rolled_back(monkeypatch, tmp_path):
    manager = _seed(monkeypatch, tmp_path)
    assert manager.rename_user(
        "alice",
        "alice2",
        "admin",
        prepare_rollback=True,
    ) is True
    assert manager.finalize_user_rename("alice2", "alice") is True

    assert manager.rollback_user_rename("alice2", "alice", "admin") is False
    assert "alice2" in manager.users
    assert "alice" not in manager.users
    assert _retired_on_disk(tmp_path) == {"alice"}


def test_existing_users_receive_safe_marketmatch_default(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "users": {
                    "alice": {
                        "password_hash": "hash:alice-password",
                        "created": 1,
                        "is_admin": False,
                        "privileges": {},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    manager = _manager(monkeypatch, auth_path)

    assert manager.get_privileges("alice")["can_use_marketmatch"] is False
    stored = json.loads(auth_path.read_text(encoding="utf-8"))
    assert stored["users"]["alice"]["privileges"]["can_use_marketmatch"] is False
