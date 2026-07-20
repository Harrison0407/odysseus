"""Focused contract tests for the MarketMatch authority kernel V1."""

from __future__ import annotations

from dataclasses import asdict, replace
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path

import pytest

import src.marketmatch_authority as authority
from src.marketmatch_authority import (
    AuthorityContractError,
    AuthorityReason,
    AuthorityScope,
    AuthorizationRequest,
    DerivedArtifactKind,
    PartyKind,
    PartyReference,
    PrincipalContext,
    ProjectionBehavior,
    ResourceClassification,
    ResourceContext,
    ScopedCapabilityGrant,
    ScopedRoleAssignment,
    SourceVisibility,
    VisibilityMode,
    VisibilityPolicy,
    build_safe_audit_summary,
    evaluate_authorization,
    inherit_derived_visibility,
    project_authorized_fields,
)


FIELDS = frozenset(
    {
        "verification_status",
        "supplier_identity",
        "factory_identity",
        "factory_address",
        "origin_cost",
        "markup",
        "evidence_summary",
    }
)
PUBLIC_FIELDS = frozenset({"verification_status", "evidence_summary"})
CLASSIFICATIONS = frozenset(ResourceClassification)


def _scope(
    *,
    organization: str = "organization-alpha",
    product: str = "marketmatch",
    project: str = "project-alpha",
    owner: str | None = None,
) -> AuthorityScope:
    return AuthorityScope(
        organization_id=organization,
        product_id=product,
        project_id=project,
        owner_party_id=owner,
    )


def _party(party_id: str = "party-alpha") -> PartyReference:
    return PartyReference(party_id=party_id, kind=PartyKind.ORGANIZATION)


def _grant(
    scope: AuthorityScope | None = None,
    *,
    capability: str = "commercial-record.view",
    active: bool = True,
    allow: bool = True,
    assignment_id: str | None = None,
) -> ScopedCapabilityGrant:
    return ScopedCapabilityGrant(
        capability_code=capability,
        scope=scope or _scope(),
        source_ref="fixture-grant",
        assignment_id=assignment_id,
        active=active,
        allow=allow,
    )


def _principal(
    *,
    grants: tuple[ScopedCapabilityGrant, ...] | None = None,
    roles: tuple[ScopedRoleAssignment, ...] = (),
    authenticated: bool = True,
    party: PartyReference | None = None,
) -> PrincipalContext:
    selected_party = party or _party()
    return PrincipalContext(
        principal_ref="principal-alpha",
        party=selected_party,
        role_assignments=roles,
        capability_grants=grants if grants is not None else (_grant(),),
        authenticated=authenticated,
    )


def _resource(
    scope: AuthorityScope | None = None,
    *,
    classification: ResourceClassification = ResourceClassification.CONFIDENTIAL,
    fields: frozenset[str] = FIELDS,
) -> ResourceContext:
    return ResourceContext(
        resource_type="commercial-record",
        resource_id="record-alpha",
        scope=scope or _scope(),
        classification=classification,
        available_fields=fields,
    )


def _policy(*, controlled: bool = False) -> VisibilityPolicy:
    return VisibilityPolicy(
        policy_id="commercial-visibility",
        version="v1",
        mode=(
            VisibilityMode.CONTROLLED_CONFIDENTIALITY
            if controlled
            else VisibilityMode.TRANSPARENT
        ),
        capabilities=frozenset({"commercial-record.view"}),
        visible_fields=PUBLIC_FIELDS if controlled else FIELDS,
        allowed_classifications=CLASSIFICATIONS,
    )


def _request(
    *,
    principal: PrincipalContext | None = None,
    resource: ResourceContext | None = None,
    capability: str = "commercial-record.view",
    fields: frozenset[str] = FIELDS,
    locale: str | None = None,
    display_timezone: str | None = None,
) -> AuthorizationRequest:
    return AuthorizationRequest(
        principal=_principal() if principal is None else principal,
        capability_code=capability,
        resource=resource or _resource(),
        requested_fields=fields,
        projection_purpose="interactive-view",
        presentation_locale=locale,
        display_timezone=display_timezone,
    )


def test_party_identity_is_independent_from_role_authority():
    party = _party()
    principal = _principal(party=party, grants=(), roles=())

    assert party == principal.party
    assert not hasattr(party, "role_code")
    assert not hasattr(party, "capability_code")
    assert (
        evaluate_authorization(_request(principal=principal), _policy()).reason_code
        is AuthorityReason.MISSING_CAPABILITY
    )


def test_one_party_can_hold_roles_in_two_scopes_without_roles_granting_authority():
    party = _party()
    assignments = (
        ScopedRoleAssignment(
            "assignment-alpha", party.party_id, "reviewer", _scope(project="project-alpha")
        ),
        ScopedRoleAssignment(
            "assignment-beta", party.party_id, "observer", _scope(project="project-beta")
        ),
    )
    principal = _principal(party=party, grants=(), roles=assignments)

    assert len(principal.role_assignments) == 2
    assert evaluate_authorization(_request(principal=principal), _policy()).allowed is False


@pytest.mark.parametrize(
    ("role_scope", "resource_scope"),
    [
        (_scope(project="project-alpha"), _scope(project="project-beta")),
        (_scope(organization="organization-alpha"), _scope(organization="organization-beta")),
    ],
)
def test_role_linked_grant_does_not_escape_role_scope(role_scope, resource_scope):
    party = _party()
    role = ScopedRoleAssignment(
        "assignment-alpha", party.party_id, "reviewer", role_scope
    )
    grant = _grant(
        AuthorityScope(global_scope=True), assignment_id=role.assignment_id
    )
    principal = _principal(party=party, roles=(role,), grants=(grant,))
    decision = evaluate_authorization(
        _request(principal=principal, resource=_resource(resource_scope)), _policy()
    )

    assert decision.allowed is False
    assert decision.reason_code is AuthorityReason.SCOPE_MISMATCH


@pytest.mark.parametrize(
    ("grant_scope", "resource_scope"),
    [
        (_scope(project="project-alpha"), _scope(project="project-beta")),
        (_scope(organization="organization-alpha"), _scope(organization="organization-beta")),
        (_scope(product="marketmatch"), _scope(product="property-management")),
    ],
)
def test_role_or_capability_scope_never_crosses_project_organization_or_product(grant_scope, resource_scope):
    principal = _principal(grants=(_grant(grant_scope),))
    decision = evaluate_authorization(
        _request(principal=principal, resource=_resource(resource_scope)),
        _policy(),
    )

    assert decision.allowed is False
    assert decision.reason_code is AuthorityReason.SCOPE_MISMATCH


@pytest.mark.parametrize(
    ("grant_scope", "resource_scope"),
    [
        (
            _scope(),
            AuthorityScope(product_id="marketmatch", project_id="project-alpha"),
        ),
        (
            AuthorityScope(workspace_id="workspace-alpha"),
            AuthorityScope(workspace_id="workspace-beta"),
        ),
        (
            AuthorityScope(resource_id="resource-alpha"),
            AuthorityScope(resource_id="resource-beta"),
        ),
        (
            AuthorityScope(owner_party_id="party-alpha"),
            AuthorityScope(owner_party_id="party-beta"),
        ),
        (
            AuthorityScope(organization_id="organization-alpha"),
            AuthorityScope(
                organization_id="organization-alpha", project_id="project-alpha"
            ),
        ),
        (
            AuthorityScope(
                organization_id="organization-alpha", project_id="project-alpha"
            ),
            AuthorityScope(organization_id="organization-alpha"),
        ),
    ],
)
def test_unspecified_or_mismatched_scope_dimensions_are_never_wildcards(
    grant_scope, resource_scope
):
    decision = evaluate_authorization(
        _request(
            principal=_principal(grants=(_grant(grant_scope),)),
            resource=_resource(resource_scope),
        ),
        _policy(),
    )

    assert decision.allowed is False
    assert decision.reason_code is AuthorityReason.SCOPE_MISMATCH


@pytest.mark.parametrize(
    "scope",
    [
        AuthorityScope(organization_id=""),
        AuthorityScope(organization_id=" organization-alpha"),
        AuthorityScope(organization_id="organization alpha"),
        AuthorityScope(organization_id="organization-alpha\n"),
        AuthorityScope(global_scope=True, organization_id="organization-alpha"),
        AuthorityScope(global_scope=1),
    ],
)
def test_malformed_empty_whitespace_or_conflicting_resource_scope_denies(scope):
    decision = evaluate_authorization(_request(resource=_resource(scope)), _policy())
    assert decision.allowed is False
    assert decision.reason_code is AuthorityReason.AMBIGUOUS_SCOPE


def test_explicit_global_grant_requires_policy_opt_in():
    principal = _principal(grants=(_grant(AuthorityScope(global_scope=True)),))
    denied = evaluate_authorization(_request(principal=principal), _policy())
    allowed_policy = replace(
        _policy(), global_capabilities=frozenset({"commercial-record.view"})
    )
    allowed = evaluate_authorization(_request(principal=principal), allowed_policy)

    assert denied.reason_code is AuthorityReason.SCOPE_MISMATCH
    assert allowed.allowed is True


def test_role_labels_and_locales_do_not_change_authority():
    party = _party()
    role = ScopedRoleAssignment("assignment-alpha", party.party_id, "reviewer", _scope())
    grant = _grant(assignment_id=role.assignment_id)
    principal = _principal(party=party, grants=(grant,), roles=(role,))

    outcomes = {
        locale: evaluate_authorization(_request(principal=principal, locale=locale), _policy())
        for locale in ("es", "en", "zh-Hans")
    }
    assert {decision.allowed for decision in outcomes.values()} == {True}
    assert {decision.reason_code for decision in outcomes.values()} == {AuthorityReason.ALLOWED}
    assert {decision.visible_fields for decision in outcomes.values()} == {FIELDS}


def test_correctly_scoped_capability_allows_and_missing_or_unknown_capability_denies():
    assert evaluate_authorization(_request(), _policy()).allowed is True

    missing = _principal(grants=())
    assert (
        evaluate_authorization(_request(principal=missing), _policy()).reason_code
        is AuthorityReason.MISSING_CAPABILITY
    )

    unknown = evaluate_authorization(_request(capability="unknown.execute"), _policy())
    assert unknown.allowed is False
    assert unknown.reason_code is AuthorityReason.UNKNOWN_CAPABILITY

    translated = evaluate_authorization(
        _request(capability="ver registro comercial"), _policy()
    )
    assert translated.allowed is False
    assert translated.reason_code is AuthorityReason.UNKNOWN_CAPABILITY


@pytest.mark.parametrize(("active", "allow"), [("true", True), (1, True), (True, "true"), (True, 1)])
def test_non_boolean_grant_state_never_becomes_authority(active, allow):
    grant = _grant()
    object.__setattr__(grant, "active", active)
    object.__setattr__(grant, "allow", allow)
    decision = evaluate_authorization(
        _request(principal=_principal(grants=(grant,))), _policy()
    )

    assert decision.allowed is False
    assert decision.reason_code is AuthorityReason.INVALID_PRINCIPAL


def test_duplicate_or_conflicting_matching_grants_deny_as_ambiguous():
    duplicate = (_grant(), _grant())
    conflict = (_grant(), _grant(allow=False))

    for grants in (duplicate, conflict):
        decision = evaluate_authorization(
            _request(principal=_principal(grants=grants)), _policy()
        )
        assert decision.allowed is False
        assert decision.reason_code is AuthorityReason.AMBIGUOUS_GRANT


def test_same_capability_in_other_scope_does_not_conflict_with_exact_grant():
    grants = (_grant(), _grant(_scope(project="project-beta")))
    decision = evaluate_authorization(
        _request(principal=_principal(grants=grants)), _policy()
    )
    assert decision.allowed is True


def test_role_linked_capability_requires_exactly_one_existing_role_assignment():
    grant = _grant(assignment_id="assignment-missing")
    decision = evaluate_authorization(
        _request(principal=_principal(grants=(grant,), roles=())), _policy()
    )
    assert decision.allowed is False
    assert decision.reason_code is AuthorityReason.INACTIVE_ASSIGNMENT


@pytest.mark.parametrize(
    ("grant", "reason"),
    [
        (_grant(active=False), AuthorityReason.INACTIVE_ASSIGNMENT),
        (_grant(allow=False), AuthorityReason.EXPLICIT_DENY),
    ],
)
def test_inactive_or_non_allowing_grants_deny(grant, reason):
    decision = evaluate_authorization(_request(principal=_principal(grants=(grant,))), _policy())
    assert decision.allowed is False
    assert decision.reason_code is reason


def test_inactive_role_assignment_invalidates_its_capability_grant():
    party = _party()
    role = ScopedRoleAssignment(
        "assignment-alpha", party.party_id, "reviewer", _scope(), active=False
    )
    principal = _principal(
        party=party,
        roles=(role,),
        grants=(_grant(assignment_id=role.assignment_id),),
    )
    decision = evaluate_authorization(_request(principal=principal), _policy())

    assert decision.allowed is False
    assert decision.reason_code is AuthorityReason.INACTIVE_ASSIGNMENT


def test_missing_principal_missing_scope_and_ambiguous_scope_fail_closed():
    missing_principal = replace(_request(), principal=None)
    assert (
        evaluate_authorization(missing_principal, _policy()).reason_code
        is AuthorityReason.MISSING_PRINCIPAL
    )

    missing_scope = _resource(AuthorityScope())
    assert (
        evaluate_authorization(_request(resource=missing_scope), _policy()).reason_code
        is AuthorityReason.MISSING_RESOURCE_SCOPE
    )

    ambiguous_scope = _resource(
        AuthorityScope(organization_id=("organization-alpha", "organization-beta"))
    )
    assert (
        evaluate_authorization(_request(resource=ambiguous_scope), _policy()).reason_code
        is AuthorityReason.AMBIGUOUS_SCOPE
    )


def test_owner_scope_matches_only_the_same_party():
    owner_scope = _scope(owner="party-alpha")
    grant = _grant(owner_scope)
    principal = _principal(grants=(grant,))
    allowed = evaluate_authorization(
        _request(principal=principal, resource=_resource(owner_scope)), _policy()
    )
    denied = evaluate_authorization(
        _request(principal=principal, resource=_resource(_scope(owner="party-beta"))), _policy()
    )

    assert allowed.allowed is True
    assert denied.reason_code is AuthorityReason.SCOPE_MISMATCH


def test_invalid_classification_and_policy_fail_closed():
    invalid_resource = _resource(classification="private")
    assert (
        evaluate_authorization(_request(resource=invalid_resource), _policy()).reason_code
        is AuthorityReason.INVALID_CLASSIFICATION
    )

    invalid_policy = replace(_policy(), capabilities=frozenset())
    assert evaluate_authorization(_request(), invalid_policy).reason_code is AuthorityReason.INVALID_POLICY


def test_evaluation_exception_returns_value_free_denial(monkeypatch):
    monkeypatch.setattr(
        authority,
        "_valid_policy",
        lambda _policy: (_ for _ in ()).throw(RuntimeError("PRIVATE_CANARY")),
    )
    decision = evaluate_authorization(_request(), _policy())

    assert decision.allowed is False
    assert decision.reason_code is AuthorityReason.EVALUATION_ERROR
    assert "PRIVATE_CANARY" not in repr(decision)


def test_transparent_policy_exposes_only_explicitly_requested_and_available_fields():
    requested = frozenset({"verification_status", "origin_cost", "not_declared"})
    decision = evaluate_authorization(_request(fields=requested), _policy())

    assert decision.allowed is True
    assert decision.visible_fields == frozenset({"verification_status", "origin_cost"})
    assert decision.omitted_fields == frozenset({"not_declared"})


def test_controlled_confidentiality_hides_commercial_identity_and_terms():
    decision = evaluate_authorization(_request(), _policy(controlled=True))
    record = {
        "verification_status": "verified",
        "supplier_identity": "PRIVATE_SUPPLIER_CANARY",
        "factory_identity": "PRIVATE_FACTORY_CANARY",
        "factory_address": "PRIVATE_ADDRESS_CANARY",
        "origin_cost": 10,
        "markup": 4,
        "evidence_summary": "Evidence exists.",
        "evidence_source_identity": "PRIVATE_EVIDENCE_SOURCE_CANARY",
        "unexpected_private_field": "PRIVATE_UNKNOWN_CANARY",
    }
    before = dict(record)
    projected = project_authorized_fields(record, decision)

    assert decision.allowed is True
    assert decision.visible_fields == PUBLIC_FIELDS
    assert decision.omitted_fields == FIELDS - PUBLIC_FIELDS
    assert projected == {
        "verification_status": "verified",
        "evidence_summary": "Evidence exists.",
    }
    assert record == before
    assert "unexpected_private_field" not in projected
    assert "evidence_source_identity" not in projected


def test_record_permission_does_not_automatically_expose_every_field():
    decision = evaluate_authorization(_request(), _policy(controlled=True))
    assert decision.allowed is True
    assert decision.visible_fields != FIELDS


def test_projector_denial_is_empty_and_unknown_behavior_is_rejected():
    denied = evaluate_authorization(_request(principal=_principal(grants=())), _policy())
    assert project_authorized_fields({"verification_status": "verified"}, denied) == {}
    with pytest.raises(AuthorityContractError) as exc:
        project_authorized_fields({}, denied, behavior="redact")
    assert exc.value.code == "UNSAFE_PROJECTION_BEHAVIOR"


def test_flat_only_projection_omits_nested_and_mutable_values_without_aliasing():
    decision = evaluate_authorization(_request(), _policy())
    nested = {"source_identity": "PRIVATE_SOURCE_CANARY"}
    actions = ["PRIVATE_ACTION_CANARY"]
    record = {
        "verification_status": "verified",
        "evidence_summary": nested,
        "supplier_identity": actions,
        "origin_cost": 10,
    }
    projected = project_authorized_fields(record, decision)

    assert projected == {"verification_status": "verified", "origin_cost": 10}
    assert projected is not record
    assert nested not in projected.values()
    assert actions not in projected.values()
    projected["origin_cost"] = 99
    assert record["origin_cost"] == 10


def test_projection_wraps_mapping_failures_without_confidential_exception_text():
    class HostileMapping(dict):
        def items(self):
            raise RuntimeError("PRIVATE_CONFIDENTIAL_CANARY")

    decision = evaluate_authorization(_request(), _policy())
    with pytest.raises(AuthorityContractError) as exc:
        project_authorized_fields(HostileMapping(), decision)
    assert exc.value.code == "INVALID_PROJECTION_INPUT"
    assert "PRIVATE_CONFIDENTIAL_CANARY" not in str(exc.value)


def _source(
    source_id: str,
    *,
    classification: ResourceClassification,
    fields: frozenset[str],
    scope: AuthorityScope | None = None,
    authorized: bool = True,
    policy_id: str = "commercial-visibility",
    policy_version: str = "v1",
) -> SourceVisibility:
    return SourceVisibility(
        source_resource_id=source_id,
        classification=classification,
        visible_fields=fields,
        required_scopes=(scope or _scope(),),
        authorized=authorized,
        policy_id=policy_id,
        policy_version=policy_version,
    )


@pytest.mark.parametrize("kind", list(DerivedArtifactKind))
def test_each_derived_artifact_kind_cannot_broaden_visibility(kind):
    source = _source(
        "source-alpha",
        classification=ResourceClassification.CONFIDENTIAL,
        fields=PUBLIC_FIELDS,
    )
    inherited = inherit_derived_visibility((source,), artifact_kind=kind)

    assert inherited.allowed is True
    assert inherited.visible_fields == PUBLIC_FIELDS
    assert inherited.classification is ResourceClassification.CONFIDENTIAL


def test_combining_sources_intersects_fields_and_preserves_all_scope_constraints():
    scope_a = replace(_scope(project="project-alpha"), resource_id="resource-alpha")
    scope_b = replace(_scope(project="project-alpha"), resource_id="resource-beta")
    first = _source(
        "source-alpha",
        classification=ResourceClassification.INTERNAL,
        fields=frozenset({"verification_status", "evidence_summary"}),
        scope=scope_a,
    )
    second = _source(
        "source-beta",
        classification=ResourceClassification.RESTRICTED,
        fields=frozenset({"verification_status", "origin_cost"}),
        scope=scope_b,
    )
    inherited = inherit_derived_visibility(
        (first, second), artifact_kind=DerivedArtifactKind.SUMMARY
    )

    assert inherited.visible_fields == frozenset({"verification_status"})
    assert inherited.classification is ResourceClassification.RESTRICTED
    assert inherited.required_scopes == (scope_a, scope_b)


def test_empty_malformed_mixed_or_policy_conflicting_sources_deny_derivation():
    empty = inherit_derived_visibility((), artifact_kind=DerivedArtifactKind.SUMMARY)
    assert empty.reason_code is AuthorityReason.INVALID_DERIVATION

    malformed_source = _source(
        "source-alpha",
        classification=ResourceClassification.CONFIDENTIAL,
        fields=PUBLIC_FIELDS,
    )
    object.__setattr__(malformed_source, "policy_version", "bad version")
    malformed = inherit_derived_visibility(
        (malformed_source,), artifact_kind=DerivedArtifactKind.SUMMARY
    )
    assert malformed.reason_code is AuthorityReason.SOURCE_NOT_AUTHORIZED

    allowed_source = _source(
        "source-alpha",
        classification=ResourceClassification.INTERNAL,
        fields=PUBLIC_FIELDS,
    )
    denied_source = _source(
        "source-beta",
        classification=ResourceClassification.INTERNAL,
        fields=PUBLIC_FIELDS,
        authorized=False,
    )
    mixed = inherit_derived_visibility(
        (allowed_source, denied_source), artifact_kind=DerivedArtifactKind.ANALYSIS
    )
    assert mixed.reason_code is AuthorityReason.SOURCE_NOT_AUTHORIZED

    other_policy = replace(allowed_source, policy_version="v2")
    policy_conflict = inherit_derived_visibility(
        (allowed_source, other_policy), artifact_kind=DerivedArtifactKind.EXPORT
    )
    assert policy_conflict.reason_code is AuthorityReason.SOURCE_POLICY_CONFLICT


@pytest.mark.parametrize(
    ("first_scope", "second_scope"),
    [
        (
            _scope(organization="organization-alpha"),
            _scope(organization="organization-beta"),
        ),
        (_scope(product="marketmatch"), _scope(product="property-management")),
        (
            replace(_scope(), workspace_id="workspace-alpha"),
            replace(_scope(), workspace_id="workspace-beta"),
        ),
        (_scope(project="project-alpha"), _scope(project="project-beta")),
        (_scope(owner="party-alpha"), _scope(owner="party-beta")),
    ],
)
def test_incompatible_tenancy_or_owner_scopes_deny_derived_artifact(
    first_scope, second_scope
):
    first = _source(
        "source-alpha",
        classification=ResourceClassification.INTERNAL,
        fields=PUBLIC_FIELDS,
        scope=first_scope,
    )
    second = _source(
        "source-beta",
        classification=ResourceClassification.CONFIDENTIAL,
        fields=PUBLIC_FIELDS,
        scope=second_scope,
    )
    inherited = inherit_derived_visibility(
        (first, second), artifact_kind=DerivedArtifactKind.REPORT
    )

    assert inherited.allowed is False
    assert inherited.reason_code is AuthorityReason.SOURCE_SCOPE_CONFLICT
    assert inherited.classification is ResourceClassification.RESTRICTED
    assert inherited.visible_fields == frozenset()


def test_unauthorized_or_invalid_source_fails_derived_visibility_closed():
    source = _source(
        "source-alpha",
        classification=ResourceClassification.CONFIDENTIAL,
        fields=PUBLIC_FIELDS,
        authorized=False,
    )
    inherited = inherit_derived_visibility((source,), artifact_kind=DerivedArtifactKind.TRANSLATION)

    assert inherited.allowed is False
    assert inherited.visible_fields == frozenset()
    assert inherited.classification is ResourceClassification.RESTRICTED
    assert inherited.reason_code is AuthorityReason.SOURCE_NOT_AUTHORIZED


def test_locale_and_timezone_changes_never_change_decision_or_reason_code():
    decisions = [
        evaluate_authorization(
            _request(locale=locale, display_timezone=display_timezone),
            _policy(controlled=True),
        )
        for locale, display_timezone in (
            ("es", "America/Santo_Domingo"),
            ("en", "UTC"),
            ("zh-Hans", "Asia/Shanghai"),
        )
    ]
    authoritative = [
        (item.allowed, item.reason_code, item.effective_scope, item.visible_fields, item.omitted_fields)
        for item in decisions
    ]
    assert authoritative[0] == authoritative[1] == authoritative[2]


def test_safe_audit_summary_contains_identifiers_but_no_record_or_hidden_values():
    decision = evaluate_authorization(_request(), _policy(controlled=True))
    summary = build_safe_audit_summary(
        decision,
        request_id="request-alpha",
        timestamp=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    serialized = repr(asdict(summary))

    assert summary.allowed is True
    assert summary.reason_code == "ALLOWED"
    assert summary.timestamp == "2026-01-02T00:00:00+00:00"
    for canary in (
        "PRIVATE_SUPPLIER_CANARY",
        "PRIVATE_ADDRESS_CANARY",
        "origin_cost",
        "markup",
    ):
        assert canary not in serialized


@pytest.mark.parametrize(
    "unsafe",
    [
        "123 Main Street",
        "origin cost $12.50",
        "margin 20%",
        "factory-address:123-main-street",
        "supplier:private-company",
        "line-one\nline-two",
        "control\x00value",
        "x" * 300,
    ],
)
def test_protected_or_injectable_resource_identifiers_are_never_audited(unsafe):
    resource = replace(_resource(), resource_id=unsafe)
    decision = evaluate_authorization(_request(resource=resource), _policy())
    summary = build_safe_audit_summary(decision, request_id=unsafe)
    serialized = repr(asdict(summary))

    assert decision.allowed is False
    assert decision.reason_code is AuthorityReason.INVALID_RESOURCE
    assert summary.resource_id == "invalid"
    assert summary.request_id is None
    assert unsafe not in serialized


def test_malformed_decision_cannot_reach_audit_or_leak_exception_values():
    decision = evaluate_authorization(_request(), _policy())
    object.__setattr__(decision, "resource_id", "PRIVATE ADDRESS CANARY\n")
    with pytest.raises(AuthorityContractError) as exc:
        build_safe_audit_summary(decision)
    assert exc.value.code == "INVALID_AUTHORIZATION_DECISION"
    assert "PRIVATE ADDRESS CANARY" not in str(exc.value)


def test_evaluator_is_deterministic_for_equal_inputs():
    request = _request()
    policy = _policy(controlled=True)
    assert evaluate_authorization(request, policy) == evaluate_authorization(request, policy)


def test_decision_is_kernel_issued_frozen_and_cannot_be_replaced_to_add_fields():
    decision = evaluate_authorization(_request(), _policy(controlled=True))
    with pytest.raises(AuthorityContractError):
        authority.AuthorizationDecision()
    with pytest.raises(FrozenInstanceError):
        decision.visible_fields = FIELDS
    with pytest.raises(TypeError):
        replace(decision, visible_fields=FIELDS)
    assert project_authorized_fields(
        {"origin_cost": 10, "verification_status": "verified"}, decision
    ) == {"verification_status": "verified"}
    object.__setattr__(decision, "visible_fields", FIELDS)
    with pytest.raises(AuthorityContractError) as exc:
        project_authorized_fields({"origin_cost": 10}, decision)
    assert exc.value.code == "INVALID_PROJECTION_INPUT"


def test_documented_uppercase_error_codes_are_bounded_and_malformed_codes_are_replaced():
    assert AuthorityContractError("INVALID_PROJECTION_INPUT").code == "INVALID_PROJECTION_INPUT"
    for unsafe in (
        "SAFE_BUT_UNDOCUMENTED_CODE",
        "lowercase",
        "BAD-CODE",
        "BAD CODE",
        "BAD\nCODE",
        "",
    ):
        assert AuthorityContractError(unsafe).code == "INVALID_AUTHORITY_CONTRACT"


def test_kernel_has_no_browser_storage_remote_or_model_dependency_and_no_named_identity_logic():
    source = Path(authority.__file__).read_text(encoding="utf-8").lower()
    for forbidden in (
        "localstorage",
        "sessionstorage",
        "requests.",
        "httpx",
        "llm_call",
        "factory = part",
        "buyer = part",
        "supplier = part",
        "lawson",
        "harrison",
        "miguel",
        "maría luisa",
    ):
        assert forbidden not in source


def test_architecture_record_documents_contract_current_state_and_deferrals():
    root = Path(__file__).resolve().parents[1]
    document = (
        root / "docs/adr/0001-marketmatch-authority-commercial-visibility-kernel-v1.md"
    ).read_text(encoding="utf-8")
    for required in (
        "Party is not Role",
        "Scope and Capability",
        "Authorization and field projection",
        "Derived-artifact inheritance",
        "Current-auth compatibility",
        "Reason-code glossary",
        "Current state versus target state",
        "Deferred items",
        "No database migration",
        "must construct `ResourceContext` from authoritative",
        "flat-only",
    ):
        assert required in document
