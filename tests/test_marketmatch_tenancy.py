from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.marketmatch_authority import (
    AuthorityScope, AuthorizationRequest, PartyKind, PartyReference,
    PrincipalContext, ResourceClassification, ResourceContext,
    ScopedCapabilityGrant, VisibilityMode, VisibilityPolicy,
    evaluate_authorization,
)
from src.marketmatch_evidence import (
    AcquisitionMethod, ContentIntegrityDescriptor, EvidenceKind, EvidenceRecord,
    MediaKind, ProvenanceContext, SourceKind,
)
from src.marketmatch_operational_context import (
    ContextErrorCode, ContextType, OperationalContextError,
    OperationalContextRecord, validate_context_id,
)
from src.marketmatch_tenancy import (
    BindingStatus, ContextBinding, MembershipEvaluation, MembershipKind,
    MembershipReason, MembershipRecord, MembershipStatus, OrganizationStatus,
    SafeTenancyAudit, TENANCY_CONTRACT_VERSION, TENANCY_POLICY_VERSION,
    TenantStatus, TenantViewKind, TenancyContractError, TenancyErrorCode,
    adapt_authenticated_principal, build_safe_tenancy_audit, build_tenant_context,
    create_context_binding, create_derived_tenant_view, create_membership,
    create_membership_transition, create_organization, create_tenant,
    evaluate_membership, project_context_binding, project_membership,
    project_organization, project_tenant, validate_binding_id,
    validate_membership_id, validate_membership_transition_id,
    validate_organization_id, validate_tenancy_audit_id,
    validate_tenancy_collection, validate_tenant_id,
    validate_tenant_scoped_source, validate_work_tenant_compatibility,
)
from src.marketmatch_truth_accountability import (
    ActorKind, ActorReference, EventType, create_operational_event,
)
from src.marketmatch_work_orchestration import (
    AssignmentStatus, CompletionCriterion, CriterionType, ExecutorKind,
    ExecutorReference, WorkPriority, WorkStatus, WorkType,
    create_assignment, create_work_item,
)


NOW = datetime(2026, 7, 20, 18, tzinfo=timezone.utc)
TENANT = "tenant1:fictional:alpha"
ORG = "org1:fictional:alpha"
PRODUCT_VALUE = "product:fictional"
WORKSPACE_VALUE = "workspace:fictional"
PROJECT_VALUE = "project:fictional"
OWNER = "party:fictional:owner"
PRINCIPAL = "principal:fictional:member"
POLICY = "tenancy.visibility"
VERSION = "v1"
PRODUCT_CONTEXT = "ctx1:product:fictional"
WORKSPACE_CONTEXT = "ctx1:workspace:fictional"
PROJECT_CONTEXT = "ctx1:project:fictional"

COMMON_FIELDS = frozenset({
    "contract_version", "classification", "visibility_policy_id",
    "visibility_policy_version", "scope_organization_id", "scope_product_id",
    "scope_workspace_id", "scope_project_id", "scope_resource_id",
    "scope_owner_party_id", "scope_global",
})
TENANT_FIELDS = COMMON_FIELDS | frozenset({"tenant_id", "created_at", "status", "display_label"})
ORG_FIELDS = COMMON_FIELDS | frozenset({"organization_id", "tenant_id", "created_at", "status", "display_name"})
MEMBER_FIELDS = COMMON_FIELDS | frozenset({
    "membership_id", "tenant_id", "organization_id", "principal_ref",
    "membership_kind", "initial_status", "created_at", "effective_at",
    "expires_at", "sponsor_ref", "supersedes_membership_id",
})
BINDING_FIELDS = COMMON_FIELDS | frozenset({
    "binding_id", "tenant_id", "organization_id", "context_id", "context_type",
    "parent_context_id", "effective_at", "status", "supersedes_binding_id",
})


def assert_code(code: TenancyErrorCode, call) -> None:
    with pytest.raises(TenancyContractError) as caught:
        call()
    assert caught.value.code is code
    assert str(caught.value) == code.value


def actor(ref: str = "principal:fictional:admin") -> ActorReference:
    return ActorReference(ref, ActorKind.HUMAN)


def auth_decision(*, resource_type: str, resource_id: str, capability: str,
                  actor_ref: str, scope: AuthorityScope, fields: frozenset[str],
                  visible: frozenset[str] | None = None, policy: str = POLICY,
                  classification=ResourceClassification.CONFIDENTIAL,
                  allowed: bool = True, locale: str = "en", tz: str = "UTC"):
    principal = PrincipalContext(
        actor_ref, PartyReference(actor_ref, PartyKind.PERSON),
        capability_grants=(ScopedCapabilityGrant(capability, scope, "test:grant"),) if allowed else (),
    )
    policy_record = VisibilityPolicy(
        policy, VERSION, VisibilityMode.CONTROLLED_CONFIDENTIALITY,
        frozenset({capability}), fields if visible is None else visible,
        frozenset(ResourceClassification),
    )
    return evaluate_authorization(
        AuthorizationRequest(
            principal, capability,
            ResourceContext(resource_type, resource_id, scope, classification, fields),
            fields, presentation_locale=locale, display_timezone=tz,
        ),
        policy_record,
    )


def tenant_scope(tenant_id: str = TENANT) -> AuthorityScope:
    return AuthorityScope(resource_id=tenant_id)


def org_scope(resource_id: str, organization_id: str = ORG) -> AuthorityScope:
    return AuthorityScope(organization_id=organization_id, resource_id=resource_id)


def project_scope(resource_id: str, organization_id: str = ORG,
                  project: str = PROJECT_VALUE, owner: str = OWNER) -> AuthorityScope:
    return AuthorityScope(
        organization_id=organization_id, product_id=PRODUCT_VALUE,
        workspace_id=WORKSPACE_VALUE, project_id=project,
        resource_id=resource_id, owner_party_id=owner,
    )


def tenant(*, tenant_id: str = TENANT, status=TenantStatus.ACTIVE,
           policy: str = POLICY, label: str = "Fictional Tenant"):
    scope = tenant_scope(tenant_id)
    admin = actor()
    decision = auth_decision(
        resource_type="tenancy_tenant", resource_id=tenant_id,
        capability="tenant.create", actor_ref=admin.actor_ref, scope=scope,
        fields=TENANT_FIELDS, policy=policy,
    )
    return create_tenant(
        tenant_id=tenant_id, created_at=NOW - timedelta(days=10), status=status,
        scope=scope, classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=policy, visibility_policy_version=VERSION,
        created_by=admin, authority_decision=decision, display_label=label,
    )


def organization(*, organization_id: str = ORG, tenant_id: str = TENANT,
                 status=OrganizationStatus.ACTIVE, policy: str = POLICY,
                 name: str = "Fictional Organization"):
    scope = org_scope(organization_id, organization_id)
    admin = actor()
    decision = auth_decision(
        resource_type="tenancy_organization", resource_id=organization_id,
        capability="organization.bind", actor_ref=admin.actor_ref, scope=scope,
        fields=ORG_FIELDS, policy=policy,
    )
    return create_organization(
        organization_id=organization_id, tenant_id=tenant_id,
        created_at=NOW - timedelta(days=9), status=status, scope=scope,
        classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=policy, visibility_policy_version=VERSION,
        created_by=admin, authority_decision=decision, display_name=name,
    )


def membership(*, membership_id: str = "mem1:fictional:alpha", tenant_id: str = TENANT,
               organization_id: str = ORG, principal_ref: str = PRINCIPAL,
               initial_status=MembershipStatus.ACTIVE, policy: str = POLICY,
               created_at: datetime = NOW - timedelta(days=8),
               effective_at: datetime = NOW - timedelta(days=7),
               expires_at: datetime | None = NOW + timedelta(days=30),
               supersedes: str | None = None):
    scope = org_scope(membership_id, organization_id)
    sponsor = actor()
    capability = "membership.invite" if initial_status is MembershipStatus.INVITED else "membership.activate"
    decision = auth_decision(
        resource_type="tenancy_membership", resource_id=membership_id,
        capability=capability, actor_ref=sponsor.actor_ref, scope=scope,
        fields=MEMBER_FIELDS, policy=policy,
    )
    return create_membership(
        membership_id=membership_id, tenant_id=tenant_id,
        organization_id=organization_id, principal_ref=principal_ref,
        membership_kind=MembershipKind.USER, initial_status=initial_status,
        created_at=created_at, effective_at=effective_at,
        expires_at=expires_at, sponsor=sponsor, scope=scope,
        classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=policy, visibility_policy_version=VERSION,
        authority_decision=decision, supersedes_membership_id=supersedes,
    )


def transition(*, transition_id="memtr1:fictional:activate",
               membership_id="mem1:fictional:alpha",
               from_status=MembershipStatus.INVITED,
               to_status=MembershipStatus.ACTIVE,
               occurred_at=NOW - timedelta(days=6), capability: str | None = None,
               tenant_id=TENANT, organization_id=ORG, policy=POLICY,
               linked_event_id=None):
    scope = org_scope(transition_id, organization_id); administrator = actor()
    cap = capability or f"membership.{to_status.value.lower()}"
    decision = auth_decision(
        resource_type="tenancy_membership_transition", resource_id=transition_id,
        capability=cap, actor_ref=administrator.actor_ref, scope=scope,
        fields=COMMON_FIELDS, policy=policy,
    )
    return create_membership_transition(
        transition_id=transition_id, membership_id=membership_id,
        tenant_id=tenant_id, organization_id=organization_id,
        from_status=from_status, to_status=to_status, actor=administrator,
        occurred_at=occurred_at, scope=scope,
        classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=policy, visibility_policy_version=VERSION,
        reason_code="membership.lifecycle", authority_decision=decision,
        linked_event_id=linked_event_id,
    )


def contexts(*, organization_id: str = ORG, policy: str = POLICY,
             project: str = PROJECT_VALUE, owner: str = OWNER):
    product_scope = AuthorityScope(organization_id=organization_id,
                                   product_id=PRODUCT_VALUE,
                                   resource_id=PRODUCT_CONTEXT,
                                   owner_party_id=owner)
    workspace_scope = AuthorityScope(organization_id=organization_id,
                                     product_id=PRODUCT_VALUE,
                                     workspace_id=WORKSPACE_VALUE,
                                     resource_id=WORKSPACE_CONTEXT,
                                     owner_party_id=owner)
    final_scope = project_scope(PROJECT_CONTEXT, organization_id, project, owner)
    return (
        OperationalContextRecord(
            PRODUCT_CONTEXT, ContextType.PRODUCT, product_scope,
            NOW - timedelta(days=8), ResourceClassification.CONFIDENTIAL,
            policy, VERSION, owner_party_ref=owner, display_name="Fictional Product",
        ),
        OperationalContextRecord(
            WORKSPACE_CONTEXT, ContextType.WORKSPACE, workspace_scope,
            NOW - timedelta(days=7, hours=12), ResourceClassification.CONFIDENTIAL,
            policy, VERSION, parent_context_id=PRODUCT_CONTEXT,
            owner_party_ref=owner, display_name="Fictional Workspace",
        ),
        OperationalContextRecord(
            PROJECT_CONTEXT, ContextType.PROJECT, final_scope,
            NOW - timedelta(days=7), ResourceClassification.CONFIDENTIAL,
            policy, VERSION, parent_context_id=WORKSPACE_CONTEXT,
            owner_party_ref=owner, display_name="Fictional Project",
        ),
    )


def binding(context, *, binding_id: str, tenant_id=TENANT, organization_id=ORG,
            status=BindingStatus.ACTIVE, supersedes=None, policy=POLICY,
            effective_at=NOW - timedelta(days=6)):
    scope = org_scope(binding_id, organization_id); administrator = actor()
    decision = auth_decision(
        resource_type="tenancy_context_binding", resource_id=binding_id,
        capability="context.bind.tenant", actor_ref=administrator.actor_ref,
        scope=scope, fields=BINDING_FIELDS, policy=policy,
    )
    return create_context_binding(
        binding_id=binding_id, tenant_id=tenant_id, organization_id=organization_id,
        context_id=context.context_id, context_type=context.context_type,
        parent_context_id=context.parent_context_id,
        effective_at=effective_at, status=status, scope=scope,
        classification=context.classification,
        visibility_policy_id=policy, visibility_policy_version=VERSION,
        bound_by=administrator, authority_decision=decision,
        supersedes_binding_id=supersedes,
    )


def bindings(context_records=None, **kwargs):
    product, workspace, project = context_records or contexts()
    return (
        binding(product, binding_id="tbind1:product:fictional", **kwargs),
        binding(workspace, binding_id="tbind1:workspace:fictional", **kwargs),
        binding(project, binding_id="tbind1:project:fictional", **kwargs),
    )


def collection(**overrides):
    ctx = contexts()
    values = dict(
        tenants=[tenant()], organizations=[organization()], memberships=[membership()],
        transitions=[], bindings=list(bindings(ctx)), contexts=list(ctx), events=[],
        evaluated_at=NOW,
    )
    values.update(overrides)
    return validate_tenancy_collection(**values)


def tenant_context():
    t = tenant(); org = organization(); ctx = contexts(); b = bindings(ctx)
    return build_tenant_context(
        tenant=t, organization=org, product_binding=b[0], workspace_binding=b[1],
        project_binding=b[2], product=ctx[0], workspace=ctx[1], project=ctx[2],
    )


@pytest.mark.parametrize("validator,value", [
    (validate_tenant_id, TENANT), (validate_organization_id, ORG),
    (validate_membership_id, "mem1:fictional:alpha"),
    (validate_membership_transition_id, "memtr1:fictional:activate"),
    (validate_binding_id, "tbind1:product:fictional"),
    (validate_tenancy_audit_id, "taud1:fictional:alpha"),
])
def test_identifiers_are_type_distinct_language_neutral_and_bounded(validator, value):
    assert validator(value) == value
    assert value != "Fictional Alpha"


@pytest.mark.parametrize("value", [
    "1", "../tenant", " tenant1:alpha:x", "tenant1:alpha:x\n",
    "tenant1:user@example.com", "tenant1:https://example.com", "tenant1:example.com",
    "tenant1:alpha:example.com", "tenant1:row:123", "tenant1:alpha:123",
    "tenant1:token:secret", "tenant1:cost:100:usd", "tenant1:address:main:street",
    "tenant1:hash:" + "a" * 64, "tenant1:alpha:" + "a" * 170,
])
def test_identifier_attacks_reject(value):
    assert_code(TenancyErrorCode.INVALID_IDENTIFIER, lambda: validate_tenant_id(value))


def test_cross_kernel_identifiers_cannot_masquerade_as_tenancy_ids():
    for value in ("ctx1:project:fictional", "ev1:document:fictional",
                  "evt1:created:fictional", "dec1:tenant:fictional",
                  "apr1:tenant:fictional", "wrk1:review:fictional",
                  "asg1:worker:fictional", "principal:fictional:member"):
        assert_code(TenancyErrorCode.INVALID_IDENTIFIER, lambda value=value: validate_membership_id(value))


def test_tenant_and_organization_are_distinct_immutable_authority_bound_records():
    t = tenant(); org = organization()
    assert t.tenant_id != org.organization_id and org.tenant_id == t.tenant_id
    assert t.authority_provenance.capability_code == "tenant.create"
    assert org.authority_provenance.capability_code == "organization.bind"
    assert not hasattr(t, "billing_plan") and not hasattr(org, "address")
    with pytest.raises(Exception): t.status = TenantStatus.SUSPENDED


def test_tenancy_administration_scopes_cannot_smuggle_operational_dimensions():
    tenant_id = "tenant1:fictional:scoped"
    scope = AuthorityScope(product_id=PRODUCT_VALUE, resource_id=tenant_id)
    administrator = actor()
    decision = auth_decision(
        resource_type="tenancy_tenant", resource_id=tenant_id,
        capability="tenant.create", actor_ref=administrator.actor_ref,
        scope=scope, fields=TENANT_FIELDS,
    )
    assert_code(TenancyErrorCode.INVALID_SCOPE, lambda: create_tenant(
        tenant_id=tenant_id, created_at=NOW, status=TenantStatus.ACTIVE,
        scope=scope, classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=POLICY, visibility_policy_version=VERSION,
        created_by=administrator, authority_decision=decision,
    ))


@pytest.mark.parametrize("status", [True, "ACTIVE", "activo", 1])
def test_tenant_and_organization_statuses_are_literal(status):
    assert_code(TenancyErrorCode.INVALID_TENANT, lambda: tenant(status=status))
    assert_code(TenancyErrorCode.INVALID_ORGANIZATION, lambda: organization(status=status))


def test_organization_missing_or_cross_tenant_fails_collection():
    assert_code(TenancyErrorCode.MISSING_REFERENCE,
                lambda: collection(tenants=[], organizations=[organization()]))
    other_tenant = tenant(tenant_id="tenant1:fictional:other")
    assert_code(TenancyErrorCode.MISSING_REFERENCE,
                lambda: collection(tenants=[other_tenant], organizations=[organization()]))


def test_hostile_tenant_or_organization_mutation_is_detected():
    t = tenant(); object.__setattr__(t, "tenant_id", "tenant1:fictional:other")
    assert_code(TenancyErrorCode.INVALID_TENANT_CONTEXT,
                lambda: collection(tenants=[t]))
    org = organization(); object.__setattr__(org, "tenant_id", "tenant1:fictional:other")
    assert_code(TenancyErrorCode.INVALID_TENANT_CONTEXT,
                lambda: collection(organizations=[org]))


def test_membership_is_explicit_and_separate_from_auth_role_and_capability():
    item = membership()
    assert item.principal_ref == PRINCIPAL and item.membership_kind is MembershipKind.USER
    assert item.authority_provenance.capability_code == "membership.activate"
    assert not hasattr(item, "password") and not hasattr(item, "role") and not hasattr(item, "capabilities")


@pytest.mark.parametrize("status", [True, "ACTIVE", "activo", 1, None])
def test_membership_state_is_literal_and_silence_never_activates(status):
    assert_code(TenancyErrorCode.INVALID_MEMBERSHIP,
                lambda: membership(initial_status=status))


def test_invitation_is_not_activation_and_contains_no_token():
    invited = membership(initial_status=MembershipStatus.INVITED)
    result = evaluate_membership(
        principal_ref=PRINCIPAL, tenant_context=tenant_context(), tenant=tenant(),
        organization=organization(), memberships=[invited], transitions=[], evaluated_at=NOW,
    )
    assert not result.effective and result.reason_code is MembershipReason.INACTIVE
    assert not hasattr(invited, "invitation_token")


@pytest.mark.parametrize("kind", ["USER", "HUMAN", "AGENT", True])
def test_unknown_or_automated_membership_kinds_reject(kind):
    scope = org_scope("mem1:fictional:kind"); sponsor = actor()
    decision = auth_decision(resource_type="tenancy_membership",
                             resource_id="mem1:fictional:kind",
                             capability="membership.activate", actor_ref=sponsor.actor_ref,
                             scope=scope, fields=MEMBER_FIELDS)
    assert_code(TenancyErrorCode.INVALID_MEMBERSHIP, lambda: create_membership(
        membership_id="mem1:fictional:kind", tenant_id=TENANT, organization_id=ORG,
        principal_ref=PRINCIPAL, membership_kind=kind, initial_status=MembershipStatus.ACTIVE,
        created_at=NOW - timedelta(days=8), effective_at=NOW - timedelta(days=7),
        expires_at=None, sponsor=sponsor, scope=scope,
        classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=POLICY, visibility_policy_version=VERSION,
        authority_decision=decision,
    ))


def test_membership_chronology_and_sensitive_principal_fail_closed():
    assert_code(TenancyErrorCode.CHRONOLOGY_CONFLICT,
                lambda: membership(effective_at=NOW - timedelta(days=9)))
    assert_code(TenancyErrorCode.CHRONOLOGY_CONFLICT,
                lambda: membership(expires_at=NOW - timedelta(days=9)))
    assert_code(TenancyErrorCode.INVALID_MEMBERSHIP,
                lambda: membership(principal_ref="session:token:secret"))


def test_lifecycle_activation_suspension_reactivation_and_revocation_preserve_history():
    invited = membership(initial_status=MembershipStatus.INVITED)
    activated = transition()
    suspended = transition(
        transition_id="memtr1:fictional:suspend", from_status=MembershipStatus.ACTIVE,
        to_status=MembershipStatus.SUSPENDED, occurred_at=NOW - timedelta(days=5),
    )
    reactivated = transition(
        transition_id="memtr1:fictional:reactivate", from_status=MembershipStatus.SUSPENDED,
        to_status=MembershipStatus.ACTIVE, occurred_at=NOW - timedelta(days=4),
    )
    revoked = transition(
        transition_id="memtr1:fictional:revoke", from_status=MembershipStatus.ACTIVE,
        to_status=MembershipStatus.REVOKED, occurred_at=NOW - timedelta(days=3),
    )
    validated = collection(memberships=[invited], transitions=[activated, suspended, reactivated, revoked])
    assert dict(validated.membership_states)[invited.membership_id] is MembershipStatus.REVOKED
    assert invited.initial_status is MembershipStatus.INVITED and len([activated, suspended, reactivated, revoked]) == 4


def test_invalid_transition_prior_state_chronology_and_viewer_authority_reject():
    assert_code(TenancyErrorCode.INVALID_LIFECYCLE,
                lambda: transition(from_status=MembershipStatus.REVOKED,
                                   to_status=MembershipStatus.ACTIVE))
    invited = membership(initial_status=MembershipStatus.INVITED)
    too_early = transition(occurred_at=NOW - timedelta(days=9))
    assert_code(TenancyErrorCode.INVALID_LIFECYCLE,
                lambda: collection(memberships=[invited], transitions=[too_early]))
    assert_code(TenancyErrorCode.INVALID_AUTHORITY_DECISION,
                lambda: transition(capability="membership.view"))


def test_suspended_revoked_expired_future_and_wrong_principal_are_ineffective():
    ctx = tenant_context(); t = tenant(); org = organization()
    invited = membership(initial_status=MembershipStatus.INVITED)
    active = transition()
    for state in (MembershipStatus.SUSPENDED, MembershipStatus.REVOKED):
        changed = transition(transition_id=f"memtr1:fictional:{state.value.lower()}",
                             from_status=MembershipStatus.ACTIVE, to_status=state,
                             occurred_at=NOW - timedelta(days=5))
        result = evaluate_membership(principal_ref=PRINCIPAL, tenant_context=ctx,
                                     tenant=t, organization=org, memberships=[invited],
                                     transitions=[active, changed], evaluated_at=NOW)
        assert not result.effective and result.reason_code is MembershipReason.INACTIVE
    expired = membership(expires_at=NOW - timedelta(hours=1))
    future = membership(membership_id="mem1:fictional:future",
                        effective_at=NOW + timedelta(days=1), expires_at=NOW + timedelta(days=2))
    assert evaluate_membership(principal_ref=PRINCIPAL, tenant_context=ctx, tenant=t,
                               organization=org, memberships=[expired], transitions=[],
                               evaluated_at=NOW).reason_code is MembershipReason.EXPIRED
    assert evaluate_membership(principal_ref=PRINCIPAL, tenant_context=ctx, tenant=t,
                               organization=org, memberships=[future], transitions=[],
                               evaluated_at=NOW).reason_code is MembershipReason.NOT_YET_EFFECTIVE
    assert evaluate_membership(principal_ref="principal:fictional:other", tenant_context=ctx,
                               tenant=t, organization=org, memberships=[membership()],
                               transitions=[], evaluated_at=NOW).reason_code is MembershipReason.WRONG_PRINCIPAL


def test_future_transition_does_not_activate_membership_early():
    invited = membership(initial_status=MembershipStatus.INVITED)
    future_activation = transition(
        transition_id="memtr1:fictional:future",
        occurred_at=NOW + timedelta(days=1),
    )
    result = evaluate_membership(
        principal_ref=PRINCIPAL, tenant_context=tenant_context(), tenant=tenant(),
        organization=organization(), memberships=[invited],
        transitions=[future_activation], evaluated_at=NOW,
    )
    assert not result.effective and result.reason_code is MembershipReason.INACTIVE
    validated = collection(memberships=[invited], transitions=[future_activation])
    assert dict(validated.membership_states)[invited.membership_id] is MembershipStatus.INVITED


def test_duplicate_active_memberships_are_conflicts_not_newest_wins():
    first = membership(); second = membership(membership_id="mem1:fictional:duplicate")
    validated = collection(memberships=[first, second])
    assert validated.conflicting_membership_ids == tuple(sorted(
        (first.membership_id, second.membership_id)
    ))
    result = evaluate_membership(principal_ref=PRINCIPAL, tenant_context=tenant_context(),
                                 tenant=tenant(), organization=organization(),
                                 memberships=[first, second], transitions=[], evaluated_at=NOW)
    assert not result.effective and result.reason_code is MembershipReason.CONFLICT
    pairs = []
    for item in (second, first):
        decision = auth_decision(
            resource_type="tenancy_membership", resource_id=item.membership_id,
            capability="membership.view", actor_ref="principal:fictional:viewer",
            scope=item.scope, fields=MEMBER_FIELDS,
            visible=frozenset({"membership_id"}),
        )
        pairs.append((item, decision))
    view = create_derived_tenant_view(
        pairs, view_kind=TenantViewKind.UNRESOLVED_MEMBERSHIP_CONFLICTS,
        validated=validated, evaluated_at=NOW,
    )
    assert view.record_ids == validated.conflicting_membership_ids


def test_membership_supersession_preserves_prior_record_and_selects_explicit_replacement():
    prior = membership(expires_at=None)
    replacement = membership(
        membership_id="mem1:fictional:replacement",
        created_at=NOW - timedelta(days=5),
        effective_at=NOW - timedelta(days=4), expires_at=None,
        supersedes=prior.membership_id,
    )
    validated = collection(memberships=[prior, replacement])
    assert dict(validated.membership_states)[prior.membership_id] is MembershipStatus.SUPERSEDED
    assert prior.initial_status is MembershipStatus.ACTIVE
    result = evaluate_membership(
        principal_ref=PRINCIPAL, tenant_context=tenant_context(), tenant=tenant(),
        organization=organization(), memberships=[prior, replacement],
        transitions=[], evaluated_at=NOW,
    )
    assert result.effective and result.membership_id == replacement.membership_id


def test_tenant_context_is_exact_canonical_and_language_neutral():
    value = tenant_context()
    assert value.tenant_id == TENANT and value.organization_id == ORG
    assert value.product_context_id == PRODUCT_CONTEXT
    assert value.workspace_context_id == WORKSPACE_CONTEXT
    assert value.project_context_id == PROJECT_CONTEXT
    assert value.scope.project_id == PROJECT_VALUE and value.scope.owner_party_id == OWNER


def test_context_missing_dimensions_aliases_cross_owner_and_cross_tenant_reject():
    value = tenant_context()
    bad_scope = AuthorityScope(organization_id=ORG, product_id=PRODUCT_VALUE,
                               workspace_id=WORKSPACE_VALUE, resource_id=PROJECT_CONTEXT,
                               owner_party_id=OWNER)
    assert_code(TenancyErrorCode.INVALID_TENANT_CONTEXT,
                lambda: type(value)(TENANT, ORG, PRODUCT_CONTEXT, WORKSPACE_CONTEXT,
                                    PROJECT_CONTEXT, bad_scope,
                                    ResourceClassification.CONFIDENTIAL, POLICY, VERSION))
    cross_contexts = contexts(organization_id="org1:fictional:other")
    cross_bindings = bindings(cross_contexts)
    assert_code(TenancyErrorCode.TENANT_CONFLICT, lambda: build_tenant_context(
        tenant=tenant(), organization=organization(), product_binding=cross_bindings[0],
        workspace_binding=cross_bindings[1], project_binding=cross_bindings[2],
        product=cross_contexts[0], workspace=cross_contexts[1], project=cross_contexts[2],
    ))
    with pytest.raises(OperationalContextError) as caught:
        validate_context_id("Fictional Project")
    assert caught.value.code == ContextErrorCode.INVALID_IDENTIFIER.value


def test_context_bindings_validate_product_workspace_project_containment():
    validated = collection()
    assert len(validated.active_binding_ids) == 3
    ctx = contexts(); bad_workspace = binding(
        ctx[1], binding_id="tbind1:workspace:bad",
    )
    object.__setattr__(bad_workspace, "parent_context_id", PROJECT_CONTEXT)
    assert_code(TenancyErrorCode.INVALID_TENANT_CONTEXT,
                lambda: collection(bindings=[bindings(ctx)[0], bad_workspace, bindings(ctx)[2]]))


def test_context_cannot_have_two_active_tenant_bindings():
    ctx = contexts(); originals = list(bindings(ctx))
    duplicate = binding(ctx[2], binding_id="tbind1:project:duplicate")
    assert_code(TenancyErrorCode.CONFLICTING_CONTEXT_BINDING,
                lambda: collection(bindings=originals + [duplicate]))


def test_context_rebinding_history_preserves_prior_versions_and_one_active_terminal():
    ctx = contexts(); original = list(bindings(ctx))
    invalidation = binding(
        ctx[0], binding_id="tbind1:product:invalidated",
        status=BindingStatus.INVALIDATED,
        supersedes=original[0].binding_id,
        effective_at=NOW - timedelta(days=5),
    )
    replacement = binding(
        ctx[0], binding_id="tbind1:product:replacement",
        supersedes=invalidation.binding_id,
        effective_at=NOW - timedelta(days=4),
    )
    validated = collection(bindings=[*original, invalidation, replacement])
    assert replacement.binding_id in validated.active_binding_ids
    assert original[0].binding_id not in validated.active_binding_ids
    assert original[0].status is BindingStatus.ACTIVE
    assert invalidation.status is BindingStatus.INVALIDATED


def test_membership_evaluation_is_explicit_and_never_grants_capability():
    result = evaluate_membership(principal_ref=PRINCIPAL, tenant_context=tenant_context(),
                                 tenant=tenant(), organization=organization(),
                                 memberships=[membership()], transitions=[], evaluated_at=NOW)
    assert (result.effective, result.reason_code, result.membership_id,
            result.tenant_id, result.organization_id) == (
                True, MembershipReason.EFFECTIVE,
                "mem1:fictional:alpha", TENANT, ORG,
            )
    assert result.capability_granted is False
    missing = evaluate_membership(principal_ref=PRINCIPAL, tenant_context=tenant_context(),
                                  tenant=tenant(), organization=organization(),
                                  memberships=[], transitions=[], evaluated_at=NOW)
    assert missing.reason_code is MembershipReason.NO_MEMBERSHIP


def test_effective_membership_state_cannot_be_caller_supplied_or_mutated():
    assert_code(TenancyErrorCode.INVALID_MEMBERSHIP,
                lambda: MembershipEvaluation(
                    True, MembershipReason.EFFECTIVE,
                    "mem1:fictional:alpha", TENANT, ORG,
                ))
    invited = membership(initial_status=MembershipStatus.INVITED)
    result = evaluate_membership(
        principal_ref=PRINCIPAL, tenant_context=tenant_context(), tenant=tenant(),
        organization=organization(), memberships=[invited], transitions=[],
        evaluated_at=NOW,
    )
    object.__setattr__(result, "effective", True)
    work, assignment = work_and_assignment()
    assert_code(TenancyErrorCode.MEMBERSHIP_INACTIVE,
                lambda: validate_work_tenant_compatibility(
                    work=work, assignment=assignment,
                    tenant_context=tenant_context(), membership=invited,
                    evaluation=result,
                ))


def test_wrong_tenant_organization_and_policy_membership_fail_closed():
    ctx = tenant_context(); t = tenant(); org = organization()
    wrong_tenant = membership(tenant_id="tenant1:fictional:other")
    assert evaluate_membership(principal_ref=PRINCIPAL, tenant_context=ctx, tenant=t,
                               organization=org, memberships=[wrong_tenant], transitions=[],
                               evaluated_at=NOW).reason_code is MembershipReason.WRONG_TENANT
    wrong_org = membership(organization_id="org1:fictional:other")
    assert evaluate_membership(principal_ref=PRINCIPAL, tenant_context=ctx, tenant=t,
                               organization=org, memberships=[wrong_org], transitions=[],
                               evaluated_at=NOW).reason_code is MembershipReason.WRONG_ORGANIZATION
    wrong_policy = membership(policy="tenancy.other")
    assert evaluate_membership(principal_ref=PRINCIPAL, tenant_context=ctx, tenant=t,
                               organization=org, memberships=[wrong_policy], transitions=[],
                               evaluated_at=NOW).reason_code is MembershipReason.POLICY_MISMATCH


def test_mutated_membership_and_forged_authority_decision_fail_closed():
    item = membership(); object.__setattr__(item, "principal_ref", "principal:fictional:other")
    result = evaluate_membership(principal_ref=PRINCIPAL, tenant_context=tenant_context(),
                                 tenant=tenant(), organization=organization(),
                                 memberships=[item], transitions=[], evaluated_at=NOW)
    assert result.reason_code is MembershipReason.MALFORMED
    scope = org_scope("mem1:fictional:forged"); sponsor = actor()
    denied = auth_decision(resource_type="tenancy_membership",
                           resource_id="mem1:fictional:forged",
                           capability="membership.activate", actor_ref=sponsor.actor_ref,
                           scope=scope, fields=MEMBER_FIELDS, allowed=False)
    assert_code(TenancyErrorCode.INVALID_AUTHORITY_DECISION, lambda: create_membership(
        membership_id="mem1:fictional:forged", tenant_id=TENANT, organization_id=ORG,
        principal_ref=PRINCIPAL, membership_kind=MembershipKind.USER,
        initial_status=MembershipStatus.ACTIVE, created_at=NOW - timedelta(days=8),
        effective_at=NOW - timedelta(days=7), expires_at=None, sponsor=sponsor,
        scope=scope, classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=POLICY, visibility_policy_version=VERSION,
        authority_decision=denied,
    ))


def test_projection_is_flat_new_authority_bound_and_hides_member_identity():
    item = membership(); before = item._integrity
    decision = auth_decision(resource_type="tenancy_membership",
                             resource_id=item.membership_id, capability="membership.view",
                             actor_ref="principal:fictional:viewer", scope=item.scope,
                             fields=MEMBER_FIELDS,
                             visible=frozenset({"membership_id", "initial_status"}))
    projected = project_membership(item, decision)
    assert projected == {"membership_id": item.membership_id, "initial_status": "ACTIVE"}
    assert projected is not item and item._integrity == before
    assert "principal_ref" not in projected and "sponsor_ref" not in projected


def test_creation_or_membership_administration_authority_is_not_projection_authority():
    item = tenant(); administrator = actor()
    creation = auth_decision(
        resource_type="tenancy_tenant", resource_id=item.tenant_id,
        capability="tenant.create", actor_ref=administrator.actor_ref,
        scope=item.scope, fields=TENANT_FIELDS,
    )
    assert_code(TenancyErrorCode.INVALID_AUTHORITY_DECISION,
                lambda: project_tenant(item, creation))


def test_all_projectors_hide_names_and_return_scalars_only():
    records = ((tenant(), project_tenant, "tenancy_tenant", "tenant_id", TENANT_FIELDS),
               (organization(), project_organization, "tenancy_organization", "organization_id", ORG_FIELDS),
               (membership(), project_membership, "tenancy_membership", "membership_id", MEMBER_FIELDS),
               (bindings()[0], project_context_binding, "tenancy_context_binding", "binding_id", BINDING_FIELDS))
    for record, projector, resource_type, id_field, fields in records:
        record_id = getattr(record, id_field)
        decision = auth_decision(resource_type=resource_type, resource_id=record_id,
                                 capability="tenancy.view", actor_ref="principal:fictional:viewer",
                                 scope=record.scope, fields=fields, visible=frozenset({id_field}))
        output = projector(record, decision)
        assert output == {id_field: record_id}
        assert all(value is None or type(value) in (str, int, float, bool) for value in output.values())


def test_hostile_mapping_and_caller_effective_state_reject_without_leak():
    item = membership(); decision = auth_decision(
        resource_type="tenancy_membership", resource_id=item.membership_id,
        capability="membership.view", actor_ref="principal:fictional:viewer",
        scope=item.scope, fields=MEMBER_FIELDS,
    )
    class Hostile(dict):
        def __getitem__(self, key): raise RuntimeError("email secret@example.com token=secret")
    for value in (Hostile(), {"membership_id": item.membership_id,
                              "effective_status": "ACTIVE", "principal_ref": PRINCIPAL}):
        assert_code(TenancyErrorCode.INVALID_PROJECTION,
                    lambda value=value: project_membership(value, decision))


def test_safe_audit_contains_only_bounded_identifiers_and_codes():
    audit = build_safe_tenancy_audit(
        audit_id="taud1:membership:fictional", action_code="membership.evaluate",
        record_type="tenancy_membership", record_id="mem1:fictional:alpha",
        tenant_id=TENANT, outcome_code="denied", reason_code="membership.inactive",
        policy_id=POLICY, policy_version=VERSION, scope_reference=ORG,
        timestamp=NOW, correlation_id="correlation:fictional",
    )
    assert audit.outcome_code == "denied" and audit.tenant_id == TENANT
    text = repr(audit).lower()
    for forbidden in ("password", "email", "cookie", "session", "bearer", "address",
                      "tax", "bank", "supplier", "factory", "cost", "margin"):
        assert forbidden not in text
    assert not hasattr(audit, "metadata") and not hasattr(audit, "authorized")


@pytest.mark.parametrize("field,value", [
    ("action_code", "membership.unknown"),
    ("outcome_code", "maybe"),
    ("reason_code", "membership.unknown"),
])
def test_audit_taxonomy_is_closed(field, value):
    values = dict(
        audit_id="taud1:membership:fictional", action_code="membership.evaluate",
        record_type="tenancy_membership", record_id="mem1:fictional:alpha",
        tenant_id=TENANT, outcome_code="denied", reason_code="membership.inactive",
        policy_id=POLICY, policy_version=VERSION, scope_reference=ORG,
        timestamp=NOW,
    )
    values[field] = value
    assert_code(TenancyErrorCode.INVALID_AUDIT,
                lambda: build_safe_tenancy_audit(**values))


@pytest.mark.parametrize("unsafe", [
    "line\nbreak", "user@example.com", "token:secret", "cookie:secret",
    "cost:100:usd", "address:main:street", "a" * 170,
])
def test_audit_rejects_confidential_or_injected_values(unsafe):
    assert_code(TenancyErrorCode.INVALID_AUDIT, lambda: build_safe_tenancy_audit(
        audit_id="taud1:membership:fictional", action_code="membership.evaluate",
        record_type="tenancy_membership", record_id="mem1:fictional:alpha",
        tenant_id=TENANT, outcome_code="denied", reason_code=unsafe,
        policy_id=POLICY, policy_version=VERSION, scope_reference=ORG, timestamp=NOW,
    ))


def test_audit_contract_cannot_be_constructed_directly():
    assert_code(TenancyErrorCode.INVALID_AUDIT, lambda: SafeTenancyAudit())


def test_derived_membership_views_authorize_every_source_and_order_deterministically():
    first = membership(); second = membership(membership_id="mem1:fictional:beta",
                                               principal_ref="principal:fictional:beta")
    validated = type(collection())
    base = collection(memberships=[first])
    # Two different principals are valid active memberships in one tenant.
    full = validate_tenancy_collection(
        tenants=[tenant()], organizations=[organization()], memberships=[first, second],
        transitions=[], bindings=list(bindings()), contexts=list(contexts()), evaluated_at=NOW,
    )
    sources = []
    for item in (second, first):
        decision = auth_decision(resource_type="tenancy_membership",
                                 resource_id=item.membership_id, capability="membership.view",
                                 actor_ref="principal:fictional:viewer", scope=item.scope,
                                 fields=MEMBER_FIELDS, visible=frozenset({"membership_id"}))
        sources.append((item, decision))
    view = create_derived_tenant_view(sources, view_kind=TenantViewKind.ACTIVE_MEMBERSHIPS,
                                      validated=full, evaluated_at=NOW)
    assert view.record_ids == tuple(sorted((first.membership_id, second.membership_id)))
    assert view.derived and not hasattr(view, "principal_refs")
    assert validated and base


def test_derived_binding_views_and_denied_cross_tenant_sources_fail_closed():
    b = bindings(); validated = collection()
    pairs = []
    for item in b:
        decision = auth_decision(resource_type="tenancy_context_binding",
                                 resource_id=item.binding_id, capability="binding.view",
                                 actor_ref="principal:fictional:viewer", scope=item.scope,
                                 fields=BINDING_FIELDS, visible=frozenset({"binding_id"}))
        pairs.append((item, decision))
    projects = create_derived_tenant_view(pairs, view_kind=TenantViewKind.TENANT_PROJECTS,
                                          validated=validated, evaluated_at=NOW)
    assert projects.record_ids == ("tbind1:project:fictional",)
    denied = auth_decision(resource_type="tenancy_context_binding",
                           resource_id=b[0].binding_id, capability="binding.view",
                           actor_ref="principal:fictional:viewer", scope=b[0].scope,
                           fields=BINDING_FIELDS, allowed=False)
    assert_code(TenancyErrorCode.SOURCE_NOT_AUTHORIZED,
                lambda: create_derived_tenant_view([(b[0], denied)],
                    view_kind=TenantViewKind.TENANT_PRODUCTS,
                    validated=validated, evaluated_at=NOW))
    other_tenant_id = "tenant1:fictional:other"
    other_org_id = "org1:fictional:other"
    other = membership(membership_id="mem1:fictional:other",
                       tenant_id=other_tenant_id, organization_id=other_org_id)
    other_decision = auth_decision(resource_type="tenancy_membership",
        resource_id=other.membership_id, capability="membership.view",
        actor_ref="principal:fictional:viewer", scope=other.scope,
        fields=MEMBER_FIELDS, visible=frozenset({"membership_id"}))
    good = membership(); good_decision = auth_decision(resource_type="tenancy_membership",
        resource_id=good.membership_id, capability="membership.view",
        actor_ref="principal:fictional:viewer", scope=good.scope,
        fields=MEMBER_FIELDS, visible=frozenset({"membership_id"}))
    cross_validated = validate_tenancy_collection(
        tenants=[tenant(), tenant(tenant_id=other_tenant_id)],
        organizations=[organization(), organization(
            organization_id=other_org_id, tenant_id=other_tenant_id,
        )], memberships=[good, other], transitions=[], bindings=list(b),
        contexts=list(contexts()), evaluated_at=NOW,
    )
    assert_code(TenancyErrorCode.SOURCE_SCOPE_CONFLICT,
                lambda: create_derived_tenant_view([(good, good_decision), (other, other_decision)],
                    view_kind=TenantViewKind.ACTIVE_MEMBERSHIPS,
                    validated=cross_validated, evaluated_at=NOW))


def test_derived_view_rejects_unvalidated_or_wrong_kind_sources():
    validated = collection()
    extra = membership(membership_id="mem1:fictional:unvalidated",
                       principal_ref="principal:fictional:unvalidated")
    decision = auth_decision(
        resource_type="tenancy_membership", resource_id=extra.membership_id,
        capability="membership.view", actor_ref="principal:fictional:viewer",
        scope=extra.scope, fields=MEMBER_FIELDS,
        visible=frozenset({"membership_id"}),
    )
    assert_code(TenancyErrorCode.SOURCE_NOT_AUTHORIZED,
                lambda: create_derived_tenant_view(
                    [(extra, decision)], view_kind=TenantViewKind.ACTIVE_MEMBERSHIPS,
                    validated=validated, evaluated_at=NOW,
                ))
    bound = bindings()[0]
    bound_decision = auth_decision(
        resource_type="tenancy_context_binding", resource_id=bound.binding_id,
        capability="binding.view", actor_ref="principal:fictional:viewer",
        scope=bound.scope, fields=BINDING_FIELDS,
        visible=frozenset({"binding_id"}),
    )
    assert_code(TenancyErrorCode.SOURCE_NOT_AUTHORIZED,
                lambda: create_derived_tenant_view(
                    [(bound, bound_decision)],
                    view_kind=TenantViewKind.ACTIVE_MEMBERSHIPS,
                    validated=validated, evaluated_at=NOW,
                ))
    existing = membership()
    existing_decision = auth_decision(
        resource_type="tenancy_membership", resource_id=existing.membership_id,
        capability="membership.view", actor_ref="principal:fictional:viewer",
        scope=existing.scope, fields=MEMBER_FIELDS,
        visible=frozenset({"membership_id"}),
    )
    assert_code(TenancyErrorCode.SOURCE_NOT_AUTHORIZED,
                lambda: create_derived_tenant_view(
                    [(existing, existing_decision)],
                    view_kind=TenantViewKind.ACTIVE_MEMBERSHIPS,
                    validated=validated, evaluated_at=NOW + timedelta(seconds=1),
                ))


def evidence_record(*, organization_id=ORG, project=PROJECT_VALUE, policy=POLICY):
    evidence_id = "ev1:document:fictional"
    return EvidenceRecord(
        evidence_id, EvidenceKind.DOCUMENT, MediaKind.TEXT,
        project_scope(evidence_id, organization_id, project), OWNER,
        ContentIntegrityDescriptor.from_bytes(b"fictional evidence"),
        ProvenanceContext(SourceKind.USER, AcquisitionMethod.UPLOAD),
        NOW - timedelta(days=1), ResourceClassification.CONFIDENTIAL,
        policy, VERSION,
    )


def truth_event(*, organization_id=ORG, policy=POLICY,
                occurred_at=NOW - timedelta(days=6)):
    event_id = "evt1:membership:fictional"
    scope = project_scope(event_id, organization_id)
    originator = actor()
    decision = auth_decision(
        resource_type="truth_event", resource_id=event_id,
        capability="event.create", actor_ref=originator.actor_ref,
        scope=scope, fields=frozenset({"event_id"}), policy=policy,
    )
    return create_operational_event(
        event_id=event_id, event_type=EventType.UPDATED, scope=scope,
        context_ids=(PROJECT_CONTEXT,), actor=originator,
        occurred_at=occurred_at,
        recorded_at=occurred_at + timedelta(minutes=1),
        authority_decision=decision,
        classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=policy, visibility_policy_version=VERSION,
        reason_code="membership.lifecycle",
    )


def test_context_evidence_truth_and_work_types_are_imported_without_conflation():
    ctx = tenant_context(); evidence = evidence_record(); event = truth_event()
    assert validate_tenant_scoped_source(ctx, evidence)
    assert validate_tenant_scoped_source(ctx, contexts()[2])
    assert validate_tenant_scoped_source(ctx, event)
    cross = evidence_record(organization_id="org1:fictional:other")
    assert_code(TenancyErrorCode.TENANT_CONFLICT,
                lambda: validate_tenant_scoped_source(ctx, cross))
    source = Path(__file__).parents[1].joinpath("src/marketmatch_tenancy.py").read_text()
    imports = {node.module for node in ast.walk(ast.parse(source))
               if isinstance(node, ast.ImportFrom)}
    for module in ("src.marketmatch_authority", "src.marketmatch_evidence",
                   "src.marketmatch_operational_context",
                   "src.marketmatch_truth_accountability",
                   "src.marketmatch_work_orchestration"):
        assert module in imports


def test_membership_transition_event_link_is_integrity_scope_policy_and_time_bound():
    invited = membership(initial_status=MembershipStatus.INVITED)
    event = truth_event()
    linked = transition(linked_event_id=event.event_id)
    assert collection(memberships=[invited], transitions=[linked], events=[event])
    later_event = truth_event(occurred_at=NOW - timedelta(days=5))
    assert_code(TenancyErrorCode.CHRONOLOGY_CONFLICT,
                lambda: collection(memberships=[invited], transitions=[linked],
                                   events=[later_event]))
    object.__setattr__(event, "reason_code", "changed.after.creation")
    assert_code(TenancyErrorCode.INVALID_TENANT_CONTEXT,
                lambda: collection(memberships=[invited], transitions=[linked],
                                   events=[event]))


def work_and_assignment(*, executor=PRINCIPAL, organization_id=ORG):
    work_id = "wrk1:review:tenancy"; work_scope = project_scope(work_id, organization_id)
    requester = actor("principal:fictional:requester")
    work_auth = auth_decision(resource_type="work_item", resource_id=work_id,
                              capability="work.create", actor_ref=requester.actor_ref,
                              scope=work_scope, fields=frozenset({"work_id"}))
    work = create_work_item(
        work_id=work_id, work_type=WorkType.REVIEW, scope=work_scope,
        context_ids=(PROJECT_CONTEXT,), requester=requester, created_at=NOW,
        ready_at=NOW, due_at=NOW + timedelta(days=1), priority=WorkPriority.NORMAL,
        initial_status=WorkStatus.READY,
        classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=POLICY, visibility_policy_version=VERSION,
        authority_decision=work_auth, required_capabilities=("work.execute",),
        completion_criteria=(CompletionCriterion(CriterionType.EXECUTOR_DECLARATION_REQUIRED),),
    )
    assignment_id = "asg1:tenancy:fictional"; assignment_scope = project_scope(assignment_id, organization_id)
    assigner = actor("principal:fictional:assigner")
    executor_ref = ExecutorReference(executor, ExecutorKind.HUMAN)
    assignment_auth = auth_decision(resource_type="work_assignment",
        resource_id=assignment_id, capability="work.assign", actor_ref=assigner.actor_ref,
        scope=assignment_scope, fields=frozenset({"assignment_id"}))
    response_auth = auth_decision(resource_type="work_assignment",
        resource_id=assignment_id, capability="work.assignment.accepted", actor_ref=executor,
        scope=assignment_scope, fields=frozenset({"assignment_id"}))
    assignment = create_assignment(
        assignment_id=assignment_id, work_id=work_id, assignee=executor_ref,
        assigner=assigner, assigned_at=NOW + timedelta(minutes=1),
        responded_at=NOW + timedelta(minutes=2), status=AssignmentStatus.ACCEPTED,
        required_execution_capability="work.execute", scope=assignment_scope,
        classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=POLICY, visibility_policy_version=VERSION,
        authority_decision=assignment_auth, response_authority_decision=response_auth,
    )
    return work, assignment


def test_work_assignment_requires_effective_same_tenant_membership_but_membership_grants_no_execution():
    work, assignment = work_and_assignment(); member = membership(); ctx = tenant_context()
    evaluation = evaluate_membership(principal_ref=PRINCIPAL, tenant_context=ctx,
                                     tenant=tenant(), organization=organization(),
                                     memberships=[member], transitions=[], evaluated_at=NOW)
    assert validate_work_tenant_compatibility(work=work, assignment=assignment,
                                              tenant_context=ctx, membership=member,
                                              evaluation=evaluation)
    assert evaluation.capability_granted is False
    invited = membership(initial_status=MembershipStatus.INVITED)
    inactive = evaluate_membership(
        principal_ref=PRINCIPAL, tenant_context=ctx, tenant=tenant(),
        organization=organization(), memberships=[invited], transitions=[],
        evaluated_at=NOW,
    )
    assert_code(TenancyErrorCode.MEMBERSHIP_INACTIVE,
                lambda: validate_work_tenant_compatibility(work=work, assignment=assignment,
                    tenant_context=ctx, membership=member, evaluation=inactive))


def test_cross_tenant_executor_assignment_and_context_scope_reject():
    work, assignment = work_and_assignment(executor="principal:fictional:other")
    member = membership()
    evaluation = evaluate_membership(
        principal_ref=PRINCIPAL, tenant_context=tenant_context(), tenant=tenant(),
        organization=organization(), memberships=[member], transitions=[],
        evaluated_at=NOW,
    )
    assert_code(TenancyErrorCode.WORK_TENANT_CONFLICT,
                lambda: validate_work_tenant_compatibility(work=work, assignment=assignment,
                    tenant_context=tenant_context(), membership=member, evaluation=evaluation))
    cross_work, cross_assignment = work_and_assignment(organization_id="org1:fictional:other")
    assert_code(TenancyErrorCode.WORK_TENANT_CONFLICT,
                lambda: validate_work_tenant_compatibility(work=cross_work,
                    assignment=cross_assignment, tenant_context=tenant_context(),
                    membership=member, evaluation=evaluation))


def test_mutated_work_or_assignment_cannot_pass_tenant_compatibility():
    member = membership(); ctx = tenant_context()
    evaluation = evaluate_membership(
        principal_ref=PRINCIPAL, tenant_context=ctx, tenant=tenant(),
        organization=organization(), memberships=[member], transitions=[],
        evaluated_at=NOW,
    )
    work, assignment = work_and_assignment()
    object.__setattr__(work, "work_type", WorkType.ANALYZE)
    assert_code(TenancyErrorCode.WORK_TENANT_CONFLICT,
                lambda: validate_work_tenant_compatibility(
                    work=work, assignment=assignment, tenant_context=ctx,
                    membership=member, evaluation=evaluation,
                ))


def test_authentication_and_administrator_status_are_not_adapted_to_membership():
    for value in ({"username": "admin", "is_admin": True},
                  {"authenticated": True, "email": "member@example.com"},
                  {"owner": "member", "folder": "Fictional Project"}):
        assert_code(TenancyErrorCode.UNSUPPORTED_ADAPTER,
                    lambda value=value: adapt_authenticated_principal(value))


def test_locale_and_timezone_do_not_change_tenancy_identity_or_evaluation():
    item = membership(); outputs = []
    for locale, tz in (("es", "America/Santo_Domingo"), ("en", "UTC"),
                       ("zh-Hans", "Asia/Shanghai")):
        decision = auth_decision(resource_type="tenancy_membership",
            resource_id=item.membership_id, capability="membership.view",
            actor_ref="principal:fictional:viewer", scope=item.scope,
            fields=MEMBER_FIELDS, visible=frozenset({"membership_id", "initial_status"}),
            locale=locale, tz=tz)
        outputs.append(project_membership(item, decision))
    assert outputs[0] == outputs[1] == outputs[2]


def test_unknown_major_version_and_collection_bounds_fail_closed():
    scope = tenant_scope("tenant1:fictional:version"); administrator = actor()
    decision = auth_decision(resource_type="tenancy_tenant",
                             resource_id="tenant1:fictional:version",
                             capability="tenant.create", actor_ref=administrator.actor_ref,
                             scope=scope, fields=TENANT_FIELDS)
    assert_code(TenancyErrorCode.INVALID_VERSION, lambda: create_tenant(
        tenant_id="tenant1:fictional:version", created_at=NOW,
        status=TenantStatus.ACTIVE, scope=scope,
        classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=POLICY, visibility_policy_version=VERSION,
        created_by=administrator, authority_decision=decision,
        contract_version="marketmatch-tenancy-membership-v2",
    ))
    assert_code(TenancyErrorCode.COLLECTION_LIMIT_EXCEEDED,
                lambda: validate_tenancy_collection(
                    tenants=[tenant()] * 2049, organizations=[], memberships=[],
                    transitions=[], bindings=[], contexts=[], evaluated_at=NOW))


def test_module_is_pure_has_no_dependency_or_runtime_integration():
    path = Path(__file__).parents[1] / "src" / "marketmatch_tenancy.py"
    source = path.read_text(encoding="utf-8"); tree = ast.parse(source)
    forbidden = ("requests", "httpx", "openai", "langgraph", "deerflow", "sqlalchemy",
                 "sqlite3", "smtplib", "celery", "redis")
    assert not any(f"import {name}" in source or f"from {name}" in source for name in forbidden)
    for token in ("localStorage", "sessionStorage", "open(", ".write(", ".execute(",
                  "requests.", "httpx.", "send_email"):
        assert token not in source
    assert all(not isinstance(node, (ast.Global, ast.Nonlocal)) for node in ast.walk(tree))


def test_no_production_route_import_or_migration_runtime_change():
    root = Path(__file__).parents[1]
    route_files = [root / "app.py", *(root / "routes").rglob("*.py")]
    assert all("marketmatch_tenancy" not in path.read_text(encoding="utf-8") for path in route_files)
    assert not any("tenan" in path.name.lower() or "member" in path.name.lower()
                   for path in (root / "alembic" / "versions").glob("*.py"))


def test_adr_documents_required_boundaries_codes_examples_and_deferrals():
    adr = (Path(__file__).parents[1] / "docs" / "adr" /
           "0006-marketmatch-tenancy-membership-kernel-v1.md").read_text(encoding="utf-8")
    required = ("authentication versus membership", "membership versus authorization",
                "membership versus role", "organization versus tenant",
                "tenant ownership versus record ownership", "Tenant Context",
                "invitation versus activation", "suspension versus revocation",
                "revocation versus expiration", "cross-tenant", "Authority",
                "Evidence", "Truth", "Work", "projection", "audit", "derived",
                "Current implementation versus target state", "Compatibility adapters",
                "Persistence", "billing", "SSO", "SCIM", "Stable code glossary",
                "Reason-code glossary", "Fictional", "Limitations")
    assert all(term.lower() in adr.lower() for term in required)
    assert "GCO" not in adr
