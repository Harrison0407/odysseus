"""Bounded compatibility adapter from current Odysseus auth to authority V1.

The existing middleware, cookie/session manager, administrator calculation,
stored MarketMatch opt-in, and route ownership checks remain authoritative.
This adapter consumes their resolved result; it never validates credentials,
cookies, or sessions and never writes auth state.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from core.auth import MARKETMATCH_PRIVILEGE, RESERVED_USERNAMES, normalize_known_username
from src.marketmatch_authority import (
    AuthorityScope,
    PartyKind,
    PartyReference,
    PrincipalContext,
    ScopedCapabilityGrant,
)


LEGACY_PRIVILEGE_CAPABILITIES = MappingProxyType(
    {
        "can_use_agent": "agent.use",
        "can_use_browser": "browser.use",
        "can_use_bash": "shell.use",
        "can_use_documents": "documents.use",
        "can_use_research": "research.use",
        MARKETMATCH_PRIVILEGE: "marketmatch.use",
        "can_generate_images": "images.generate",
        "can_manage_memory": "memory.manage",
    }
)


def _privilege_scope(privilege: str) -> AuthorityScope:
    if privilege == MARKETMATCH_PRIVILEGE:
        return AuthorityScope(product_id="marketmatch")
    # Existing non-MarketMatch privilege flags are global today.  Encoding that
    # fact explicitly preserves compatibility without claiming a future scope
    # registry exists.
    return AuthorityScope(global_scope=True)


def _principal_from_current_auth(
    current_user: object,
    auth_manager: object,
) -> PrincipalContext | None:
    """Adapt a canonical authenticated username without broadening authority."""

    try:
        users = getattr(auth_manager, "users", None)
        if type(users) is not dict or type(current_user) is not str:
            return None
        normalized = normalize_known_username(users, current_user)
        if normalized is None or normalized != current_user or normalized in RESERVED_USERNAMES:
            return None
        user_record = users.get(normalized)
        if type(user_record) is not dict:
            return None
        effective = auth_manager.get_privileges(normalized)
        if type(effective) is not dict:
            return None
        stored = user_record.get("privileges")
        if stored is not None and type(stored) is not dict:
            return None

        grants: list[ScopedCapabilityGrant] = []
        for privilege, capability in LEGACY_PRIVILEGE_CAPABILITIES.items():
            if effective.get(privilege) is not True:
                continue
            # Existing MarketMatch routes require the explicit stored literal
            # True even for administrators.  Preserve that stricter result.
            if privilege == MARKETMATCH_PRIVILEGE and (
                type(stored) is not dict or stored.get(privilege) is not True
            ):
                continue
            grants.append(
                ScopedCapabilityGrant(
                    capability_code=capability,
                    scope=_privilege_scope(privilege),
                    source_ref=f"legacy-privilege:{privilege}",
                )
            )

        party_ref = f"legacy-user:{normalized}"
        return PrincipalContext(
            principal_ref=party_ref,
            party=PartyReference(party_id=party_ref, kind=PartyKind.PERSON),
            role_assignments=(),
            capability_grants=tuple(grants),
            authenticated=True,
            administrator=bool(user_record.get("is_admin", False)),
        )
    except Exception:
        return None


def principal_from_authenticated_request(request: Any) -> PrincipalContext | None:
    """Consume middleware-resolved ``current_user``; never inspect its cookie."""

    try:
        current_user = getattr(request.state, "current_user", None)
        auth_manager = getattr(request.app.state, "auth_manager", None)
    except Exception:
        return None
    return _principal_from_current_auth(current_user, auth_manager)


__all__ = (
    "LEGACY_PRIVILEGE_CAPABILITIES",
    "principal_from_authenticated_request",
)
