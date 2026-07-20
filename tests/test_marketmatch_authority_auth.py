"""Compatibility tests for current auth feeding the authority kernel."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.auth import MARKETMATCH_PRIVILEGE
from src.marketmatch_authority import (
    AuthorityReason,
    AuthorityScope,
    AuthorizationRequest,
    ResourceClassification,
    ResourceContext,
    VisibilityMode,
    VisibilityPolicy,
    evaluate_authorization,
)
from src.marketmatch_authority_auth import (
    LEGACY_PRIVILEGE_CAPABILITIES,
    principal_from_authenticated_request,
)


class FakeAuthManager:
    def __init__(self, users, effective):
        self.users = users
        self._effective = effective

    def get_privileges(self, username):
        return dict(self._effective[username])


def _manager(*, is_admin=False, stored=True, effective=True, extra=None):
    privileges = {MARKETMATCH_PRIVILEGE: stored, "can_use_documents": True}
    if extra:
        privileges.update(extra)
    return FakeAuthManager(
        {"user-alpha": {"is_admin": is_admin, "privileges": privileges}},
        {
            "user-alpha": {
                MARKETMATCH_PRIVILEGE: effective,
                "can_use_documents": True,
                **(extra or {}),
            }
        },
    )


def _capabilities(principal):
    return {grant.capability_code for grant in principal.capability_grants}


def _adapt(manager, current_user="user-alpha", **untrusted):
    request = SimpleNamespace(
        state=SimpleNamespace(current_user=current_user),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=manager)),
        **untrusted,
    )
    return principal_from_authenticated_request(request)


def _marketmatch_decision(principal):
    resource = ResourceContext(
        resource_type="capture",
        resource_id="capture-alpha",
        scope=AuthorityScope(product_id="marketmatch"),
        classification=ResourceClassification.INTERNAL,
        available_fields=frozenset({"transcript_text"}),
    )
    policy = VisibilityPolicy(
        policy_id="marketmatch-current-auth",
        version="v1",
        mode=VisibilityMode.TRANSPARENT,
        capabilities=frozenset({"marketmatch.use"}),
        visible_fields=frozenset({"transcript_text"}),
        allowed_classifications=frozenset({ResourceClassification.INTERNAL}),
    )
    request = AuthorizationRequest(
        principal=principal,
        capability_code="marketmatch.use",
        resource=resource,
        requested_fields=frozenset({"transcript_text"}),
    )
    return evaluate_authorization(request, policy)


def test_explicit_legacy_mapping_preserves_current_marketmatch_allow():
    principal = _adapt(_manager())

    assert principal is not None
    assert "marketmatch.use" in _capabilities(principal)
    assert _marketmatch_decision(principal).allowed is True


def test_current_marketmatch_denials_remain_denied_for_regular_users_and_admins():
    scenarios = (
        _manager(stored=False, effective=False),
        _manager(stored=False, effective=True),
        _manager(is_admin=True, stored=False, effective=True),
        _manager(is_admin=True, stored=True, effective=False),
    )
    for manager in scenarios:
        principal = _adapt(manager)
        assert principal is not None
        assert "marketmatch.use" not in _capabilities(principal)
        decision = _marketmatch_decision(principal)
        assert decision.allowed is False
        assert decision.reason_code is AuthorityReason.MISSING_CAPABILITY


def test_truthy_non_boolean_current_privileges_never_become_capabilities():
    for stored, effective in (("true", True), (1, True), (True, "true"), (True, 1)):
        principal = _adapt(_manager(stored=stored, effective=effective))
        assert principal is not None
        assert "marketmatch.use" not in _capabilities(principal)


def test_admin_status_is_context_only_and_never_an_implicit_grant():
    principal = _adapt(_manager(is_admin=True, stored=False, effective=True))

    assert principal.administrator is True
    assert "marketmatch.use" not in _capabilities(principal)


def test_adapter_maps_known_literal_true_flags_only_and_ignores_unknown_privileges():
    principal = _adapt(
        _manager(extra={"future_unknown_privilege": True, "can_use_agent": "yes"})
    )

    assert _capabilities(principal) == {"documents.use", "marketmatch.use"}
    assert set(LEGACY_PRIVILEGE_CAPABILITIES) == {
        "can_use_agent",
        "can_use_browser",
        "can_use_bash",
        "can_use_documents",
        "can_use_research",
        "can_use_marketmatch",
        "can_generate_images",
        "can_manage_memory",
    }


def test_adapter_rejects_missing_noncanonical_unknown_and_malformed_principals():
    manager = _manager()
    assert _adapt(manager, None) is None
    assert _adapt(manager, "User-Alpha") is None
    assert _adapt(manager, "unknown-user") is None
    assert _adapt(object()) is None

    malformed = FakeAuthManager(
        {"user-alpha": {"privileges": []}},
        {"user-alpha": {MARKETMATCH_PRIVILEGE: True}},
    )
    assert _adapt(malformed) is None


def test_request_adapter_consumes_middleware_identity_without_cookie_or_session_logic():
    manager = _manager()
    request = SimpleNamespace(
        state=SimpleNamespace(current_user="user-alpha"),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=manager)),
        cookies={"session": "PRIVATE_SESSION_CANARY"},
        username="different-user",
    )
    principal = principal_from_authenticated_request(request)

    assert principal is not None
    assert principal.principal_ref == "legacy-user:user-alpha"
    serialized = repr(principal)
    assert "PRIVATE_SESSION_CANARY" not in serialized


def test_adapter_copies_no_password_hash_cookie_session_or_complete_auth_record():
    manager = _manager()
    manager.users["user-alpha"].update(
        {
            "password_hash": "PRIVATE_PASSWORD_HASH_CANARY",
            "totp_secret": "PRIVATE_TOTP_CANARY",
        }
    )
    principal = _adapt(manager)
    serialized = repr(principal)

    assert "PRIVATE_PASSWORD_HASH_CANARY" not in serialized
    assert "PRIVATE_TOTP_CANARY" not in serialized
    assert not hasattr(principal, "privileges")


def test_adapter_introduces_no_route_or_auth_store_integration():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/marketmatch_authority_auth.py").read_text(encoding="utf-8")
    app_source = (root / "app.py").read_text(encoding="utf-8")
    route_sources = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "routes/marketmatch_stt_routes.py",
            "routes/marketmatch_calls_analysis_routes.py",
            "routes/marketmatch_locale_routes.py",
        )
    )

    assert "request.cookies" not in source
    assert "create_session" not in source
    assert "password" not in source.lower()
    assert "marketmatch_authority" not in app_source
    assert "marketmatch_authority" not in route_sources
