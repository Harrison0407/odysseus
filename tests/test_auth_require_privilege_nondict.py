import types

import pytest

from src import auth_helpers
from src.auth_helpers import require_privilege


class _Mgr:
    def __init__(self, privs):
        self._privs = privs

    def get_privileges(self, user):
        return self._privs


def _request(mgr, *, current_user="bob", api_token=False):
    state = types.SimpleNamespace(auth_manager=mgr)
    return types.SimpleNamespace(
        app=types.SimpleNamespace(state=state),
        state=types.SimpleNamespace(current_user=current_user, api_token=api_token),
        client=types.SimpleNamespace(host="203.0.113.10"),
    )


def test_require_privilege_tolerates_non_dict_privileges(monkeypatch):
    # A corrupt auth.json can make get_privileges return a non-dict (e.g. a
    # list). The privs.get(...) call sits outside the try, so the old code
    # raised AttributeError and turned a privilege check into a 500. It should
    # fall back to the documented fail-open behaviour.
    monkeypatch.setattr(auth_helpers, "require_user", lambda request: "bob")
    req = _request(_Mgr(["do_x"]))
    assert require_privilege(req, "do_x") == "bob"


def test_require_privilege_still_blocks_disallowed(monkeypatch):
    monkeypatch.setattr(auth_helpers, "require_user", lambda request: "bob")
    req = _request(_Mgr({"do_x": False}))
    with pytest.raises(Exception):
        require_privilege(req, "do_x")


@pytest.mark.parametrize("privileges", [{}, [], None])
def test_require_privilege_strict_denies_missing_or_malformed_state(monkeypatch, privileges):
    monkeypatch.setattr(auth_helpers, "require_user", lambda request: "bob")
    req = _request(_Mgr(privileges))

    with pytest.raises(Exception) as exc:
        require_privilege(req, "can_use_marketmatch", strict=True)

    assert exc.value.status_code == 403


def test_require_privilege_strict_denies_explicit_false(monkeypatch):
    monkeypatch.setattr(auth_helpers, "require_user", lambda request: "bob")
    req = _request(_Mgr({"can_use_marketmatch": False}))

    with pytest.raises(Exception) as exc:
        require_privilege(req, "can_use_marketmatch", strict=True)

    assert exc.value.status_code == 403


@pytest.mark.parametrize("malformed_value", ["false", 1, [], {}])
def test_require_privilege_strict_denies_non_boolean_values(monkeypatch, malformed_value):
    monkeypatch.setattr(auth_helpers, "require_user", lambda request: "bob")
    req = _request(_Mgr({"can_use_marketmatch": malformed_value}))

    with pytest.raises(Exception) as exc:
        require_privilege(req, "can_use_marketmatch", strict=True)

    assert exc.value.status_code == 403


def test_require_privilege_strict_denies_lookup_exception(monkeypatch):
    class _BrokenManager:
        def get_privileges(self, user):
            raise RuntimeError("corrupt privilege store")

    monkeypatch.setattr(auth_helpers, "require_user", lambda request: "bob")

    with pytest.raises(Exception) as exc:
        require_privilege(_request(_BrokenManager()), "can_use_marketmatch", strict=True)

    assert exc.value.status_code == 403


def test_require_privilege_strict_denies_absent_auth_manager(monkeypatch):
    monkeypatch.setattr(auth_helpers, "require_user", lambda request: "bob")

    with pytest.raises(Exception) as exc:
        require_privilege(_request(None), "can_use_marketmatch", strict=True)

    assert exc.value.status_code == 403


def test_require_privilege_strict_denies_anonymous_when_auth_enabled(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    req = _request(types.SimpleNamespace(is_configured=True), current_user=None)

    with pytest.raises(Exception) as exc:
        require_privilege(req, "can_use_marketmatch", strict=True)

    assert exc.value.status_code == 401


def test_require_privilege_strict_denies_anonymous_first_run_loopback(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    req = _request(types.SimpleNamespace(is_configured=False), current_user=None)
    req.client.host = "127.0.0.1"

    with pytest.raises(Exception) as exc:
        require_privilege(req, "can_use_marketmatch", strict=True)

    assert exc.value.status_code == 401


def test_require_privilege_strict_preserves_operator_disabled_auth(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    req = _request(None, current_user=None)

    assert require_privilege(req, "can_use_marketmatch", strict=True) == ""


def test_require_privilege_strict_rejects_normal_api_token(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    req = _request(_Mgr({"can_use_marketmatch": True}), current_user="api", api_token=True)

    with pytest.raises(Exception) as exc:
        require_privilege(req, "can_use_marketmatch", strict=True)

    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "manager",
    [None, _Mgr({}), _Mgr([])],
)
def test_require_privilege_non_strict_remains_fail_open(monkeypatch, manager):
    monkeypatch.setattr(auth_helpers, "require_user", lambda request: "bob")

    assert require_privilege(_request(manager), "unknown_privilege") == "bob"


def test_require_privilege_non_strict_lookup_exception_remains_fail_open(monkeypatch):
    class _BrokenManager:
        def get_privileges(self, user):
            raise RuntimeError("corrupt privilege store")

    monkeypatch.setattr(auth_helpers, "require_user", lambda request: "bob")

    assert require_privilege(_request(_BrokenManager()), "unknown_privilege") == "bob"
