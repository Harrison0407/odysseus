from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.marketmatch_authority import (
    AuthorityScope,
    AuthorizationRequest,
    DerivedArtifactKind,
    PartyKind,
    PartyReference,
    PrincipalContext,
    ResourceClassification,
    ResourceContext,
    ScopedCapabilityGrant,
    VisibilityMode,
    VisibilityPolicy,
    evaluate_authorization,
)
from src.marketmatch_evidence import (
    AcquisitionMethod,
    ContentIntegrityDescriptor,
    EvidenceKind,
    EvidenceRecord,
    MediaKind,
    ProvenanceContext,
    SourceKind,
)
from src.marketmatch_operational_context import ContextType, OperationalContextRecord
from src.marketmatch_truth_accountability import (
    AccountabilityLineage,
    ActorKind,
    ActorReference,
    ApprovalRecord,
    ApprovalRequirement,
    ApprovalSlot,
    ApprovalStatus,
    ApprovalType,
    DecisionOutcome,
    DecisionRecord,
    DecisionStatus,
    DecisionType,
    EventType,
    LineageType,
    MAX_RECORDS_PER_KIND,
    OperationalEvent,
    RecordKind,
    RequirementReason,
    RequirementStatus,
    TRUTH_CONTRACT_VERSION,
    TargetType,
    TruthContractError,
    TruthErrorCode,
    build_rejection_audit_record,
    build_safe_audit_record,
    create_approval_record,
    create_decision_record,
    create_derived_accountability_view,
    create_operational_event,
    evaluate_approval_requirement,
    project_approval_record,
    project_decision_record,
    project_event_record,
    validate_accountability_collection,
    validate_approval_id,
    validate_audit_id,
    validate_decision_id,
    validate_event_id,
)


NOW = datetime(2026, 7, 20, 18, 0, tzinfo=timezone.utc)
POLICY_ID = "marketmatch.truth"
POLICY_VERSION = "v1"
ORG = "org:alpha"
PRODUCT = "marketmatch"
WORKSPACE = "workspace:alpha"
PROJECT = "project:alpha"
OWNER = "party:owner"
CONTEXT_ID = "ctx1:project:alpha"
EVIDENCE_ID = "ev1:document:alpha"

EVENT_FIELDS = frozenset(
    {
        "contract_version", "event_id", "event_type", "actor_ref", "actor_kind",
        "occurred_at", "recorded_at", "received_at", "source_event_id",
        "correlation_id", "classification", "visibility_policy_id",
        "visibility_policy_version", "reason_code", "context_count", "evidence_count",
        "scope_organization_id", "scope_product_id", "scope_workspace_id",
        "scope_project_id", "scope_resource_id", "scope_owner_party_id", "scope_global",
    }
)
DECISION_FIELDS = frozenset(
    {
        "contract_version", "decision_id", "decision_type", "target_type", "target_id",
        "actor_ref", "actor_kind", "issued_at", "effective_at", "expires_at", "outcome",
        "status", "classification", "visibility_policy_id", "visibility_policy_version",
        "reason_code", "basis_count", "scope_organization_id", "scope_product_id",
        "scope_workspace_id", "scope_project_id", "scope_resource_id",
        "scope_owner_party_id", "scope_global",
    }
)
APPROVAL_FIELDS = frozenset(
    {
        "contract_version", "approval_id", "approval_type", "target_type", "target_id",
        "actor_ref", "actor_kind", "status", "recorded_at", "effective_at", "expires_at",
        "classification", "visibility_policy_id", "visibility_policy_version",
        "conditions_code", "reason_code", "support_count", "scope_organization_id",
        "scope_product_id", "scope_workspace_id", "scope_project_id",
        "scope_resource_id", "scope_owner_party_id", "scope_global",
    }
)


def assert_code(code: TruthErrorCode, call) -> None:
    with pytest.raises(TruthContractError) as caught:
        call()
    assert caught.value.code is code
    assert str(caught.value) == code.value
    assert repr(caught.value) == f"TruthContractError('{code.value}')"


def scope_for(
    resource_id: str,
    *,
    organization: str = ORG,
    product: str = PRODUCT,
    workspace: str = WORKSPACE,
    project: str = PROJECT,
    owner: str = OWNER,
) -> AuthorityScope:
    return AuthorityScope(
        organization_id=organization,
        product_id=product,
        workspace_id=workspace,
        project_id=project,
        resource_id=resource_id,
        owner_party_id=owner,
    )


def authority_decision(
    *,
    resource_type: str,
    resource_id: str,
    capability: str,
    actor_ref: str,
    available_fields: frozenset[str],
    visible_fields: frozenset[str] | None = None,
    classification: ResourceClassification = ResourceClassification.CONFIDENTIAL,
    policy_id: str = POLICY_ID,
    policy_version: str = POLICY_VERSION,
    resource_scope: AuthorityScope | None = None,
    grant: bool = True,
    locale: str = "en",
    display_timezone: str = "UTC",
):
    item_scope = resource_scope or scope_for(resource_id)
    principal = PrincipalContext(
        principal_ref=actor_ref,
        party=PartyReference(actor_ref, PartyKind.PERSON),
        capability_grants=(ScopedCapabilityGrant(capability, item_scope, "test:grant"),) if grant else (),
    )
    policy = VisibilityPolicy(
        policy_id=policy_id,
        version=policy_version,
        mode=VisibilityMode.CONTROLLED_CONFIDENTIALITY,
        capabilities=frozenset({capability}),
        visible_fields=available_fields if visible_fields is None else visible_fields,
        allowed_classifications=frozenset(ResourceClassification),
    )
    return evaluate_authorization(
        AuthorizationRequest(
            principal=principal,
            capability_code=capability,
            resource=ResourceContext(
                resource_type=resource_type,
                resource_id=resource_id,
                scope=item_scope,
                classification=classification,
                available_fields=available_fields,
            ),
            requested_fields=available_fields,
            presentation_locale=locale,
            display_timezone=display_timezone,
        ),
        policy,
    )


def actor(ref: str = "principal:reporter", kind: ActorKind = ActorKind.HUMAN) -> ActorReference:
    return ActorReference(ref, kind)


def context_record(
    context_id: str = CONTEXT_ID,
    *,
    organization: str = ORG,
    project: str = PROJECT,
    policy_id: str = POLICY_ID,
) -> OperationalContextRecord:
    return OperationalContextRecord(
        context_id=context_id,
        context_type=ContextType.PROJECT,
        scope=scope_for(context_id, organization=organization, project=project),
        created_at=NOW - timedelta(days=1),
        classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=policy_id,
        visibility_policy_version=POLICY_VERSION,
        owner_party_ref=OWNER,
        display_name="Fictional Project Alpha",
    )


def evidence_record(
    evidence_id: str = EVIDENCE_ID,
    *,
    organization: str = ORG,
    project: str = PROJECT,
    policy_id: str = POLICY_ID,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        evidence_kind=EvidenceKind.DOCUMENT,
        media_kind=MediaKind.TEXT,
        scope=scope_for(evidence_id, organization=organization, project=project),
        owner_party_ref=OWNER,
        integrity=ContentIntegrityDescriptor.from_bytes(b"fictional evidence"),
        provenance=ProvenanceContext(SourceKind.USER, AcquisitionMethod.UPLOAD),
        created_at=NOW - timedelta(hours=1),
        classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=policy_id,
        visibility_policy_version=POLICY_VERSION,
    )


def event_record(
    event_id: str = "evt1:observed:alpha",
    *,
    event_type: EventType = EventType.OBSERVED,
    actor_ref: str = "principal:reporter",
    occurred_at: datetime = NOW,
    recorded_at: datetime | None = None,
    context_ids: tuple[str, ...] = (CONTEXT_ID,),
    evidence_ids: tuple[str, ...] = (EVIDENCE_ID,),
    source_event_id: str | None = None,
    organization: str = ORG,
    project: str = PROJECT,
    policy_id: str = POLICY_ID,
    classification: ResourceClassification = ResourceClassification.CONFIDENTIAL,
    capability: str = "event.create",
    grant: bool = True,
) -> OperationalEvent:
    item_scope = scope_for(event_id, organization=organization, project=project)
    auth = authority_decision(
        resource_type="truth_event", resource_id=event_id, capability=capability,
        actor_ref=actor_ref, available_fields=EVENT_FIELDS, classification=classification,
        policy_id=policy_id, resource_scope=item_scope, grant=grant,
    )
    return create_operational_event(
        event_id=event_id,
        event_type=event_type,
        scope=item_scope,
        context_ids=context_ids,
        actor=actor(actor_ref),
        occurred_at=occurred_at,
        recorded_at=recorded_at or occurred_at + timedelta(minutes=1),
        received_at=occurred_at + timedelta(seconds=30),
        evidence_ids=evidence_ids,
        source_event_id=source_event_id,
        authority_decision=auth,
        classification=classification,
        visibility_policy_id=policy_id,
        visibility_policy_version=POLICY_VERSION,
        reason_code="observation.recorded",
    )


def decision_record(
    decision_id: str = "dec1:course:alpha",
    *,
    target_type: TargetType = TargetType.EVENT,
    target_id: str = "evt1:observed:alpha",
    actor_ref: str = "principal:decider",
    status: DecisionStatus = DecisionStatus.ISSUED,
    supporting_event_ids: tuple[str, ...] = ("evt1:observed:alpha",),
    prior_decision_ids: tuple[str, ...] = (),
    organization: str = ORG,
    project: str = PROJECT,
    policy_id: str = POLICY_ID,
    classification: ResourceClassification = ResourceClassification.CONFIDENTIAL,
    capability: str = "decision.issue",
    grant: bool = True,
) -> DecisionRecord:
    item_scope = scope_for(decision_id, organization=organization, project=project)
    auth = authority_decision(
        resource_type="truth_decision", resource_id=decision_id, capability=capability,
        actor_ref=actor_ref, available_fields=DECISION_FIELDS, policy_id=policy_id,
        resource_scope=item_scope, grant=grant, classification=classification,
    )
    return create_decision_record(
        decision_id=decision_id,
        decision_type=DecisionType.COURSE_OF_ACTION,
        target_type=target_type,
        target_id=target_id,
        scope=item_scope,
        decision_maker=actor(actor_ref),
        issued_at=NOW + timedelta(minutes=2),
        outcome=DecisionOutcome.SELECTED,
        status=status,
        supporting_event_ids=supporting_event_ids,
        prior_decision_ids=prior_decision_ids,
        authority_decision=auth,
        classification=classification,
        visibility_policy_id=policy_id,
        visibility_policy_version=POLICY_VERSION,
        reason_code="course.selected",
    )


def approval_record(
    approval_id: str = "apr1:review:alpha",
    *,
    target_type: TargetType = TargetType.DECISION,
    target_id: str = "dec1:course:alpha",
    actor_ref: str = "principal:approver",
    status: ApprovalStatus = ApprovalStatus.APPROVED,
    capability: str = "approval.record",
    organization: str = ORG,
    project: str = PROJECT,
    policy_id: str = POLICY_ID,
    expires_at: datetime | None = None,
    grant: bool = True,
) -> ApprovalRecord:
    item_scope = scope_for(approval_id, organization=organization, project=project)
    auth = authority_decision(
        resource_type="truth_approval", resource_id=approval_id, capability=capability,
        actor_ref=actor_ref, available_fields=APPROVAL_FIELDS, policy_id=policy_id,
        resource_scope=item_scope, grant=grant,
    )
    return create_approval_record(
        approval_id=approval_id,
        approval_type=ApprovalType.REVIEW,
        target_type=target_type,
        target_id=target_id,
        scope=item_scope,
        approver=actor(actor_ref),
        status=status,
        recorded_at=NOW + timedelta(minutes=3),
        expires_at=expires_at,
        supporting_event_ids=("evt1:observed:alpha",),
        authority_capability=capability,
        authority_decision=auth,
        classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=policy_id,
        visibility_policy_version=POLICY_VERSION,
        reason_code="review.recorded",
    )


@pytest.mark.parametrize(
    ("validator", "valid"),
    [
        (validate_event_id, "evt1:observed:alpha"),
        (validate_decision_id, "dec1:course:alpha"),
        (validate_approval_id, "apr1:review:alpha"),
        (validate_audit_id, "aud1:event:alpha"),
    ],
)
def test_record_identifiers_are_separate_language_neutral_contracts(validator, valid):
    assert validator(valid) == valid
    for invalid in (
        "123", "../secret", " visible ", "user@example.com", "cost:100:usd",
        "ev1:document:alpha", "ctx1:project:alpha", "a" * 200,
        "a" * 64, "\nsecret",
    ):
        assert_code(TruthErrorCode.INVALID_IDENTIFIER, lambda invalid=invalid: validator(invalid))


def test_id_contracts_do_not_cross_accept_or_depend_on_locale():
    ids = ("evt1:reported:uno", "dec1:course:uno", "apr1:review:uno", "aud1:audit:uno")
    validators = (validate_event_id, validate_decision_id, validate_approval_id, validate_audit_id)
    for index, validator in enumerate(validators):
        assert validator(ids[index]) == ids[index]
        for other in ids[:index] + ids[index + 1 :]:
            assert_code(TruthErrorCode.INVALID_IDENTIFIER, lambda other=other: validator(other))


def test_actor_kind_origin_and_secret_boundaries():
    assert actor().actor_kind is ActorKind.HUMAN
    agent = ActorReference("agent:analysis:one", ActorKind.AGENT, "model:local", "v1")
    assert agent.generator_identifier == "model:local"
    for args in (
        ("principal:one", "HUMAN"),
        ("session:token:secret", ActorKind.SYSTEM),
        ("principal:one", ActorKind.AGENT),
        ("principal:one", ActorKind.HUMAN, "model:one", "v1"),
    ):
        assert_code(TruthErrorCode.INVALID_ACTOR, lambda args=args: ActorReference(*args))


def test_event_is_immutable_distinct_and_preserves_timestamp_meanings():
    item = event_record()
    assert item.event_id != EVIDENCE_ID != CONTEXT_ID
    assert item.occurred_at < item.received_at < item.recorded_at
    assert item.context_ids == (CONTEXT_ID,)
    assert item.evidence_ids == (EVIDENCE_ID,)
    assert not hasattr(item, "raw_evidence") and not hasattr(item, "metadata")
    with pytest.raises(FrozenInstanceError):
        item.reason_code = "changed"  # type: ignore[misc]
    assert context_record().context_id == CONTEXT_ID
    assert evidence_record().evidence_id == EVIDENCE_ID


@pytest.mark.parametrize("event_type", ["OBSERVED", "ORDERED", True, 1])
def test_event_rejects_translated_or_workflow_or_nonliteral_types(event_type):
    assert_code(TruthErrorCode.INVALID_EVENT, lambda: event_record(event_type=event_type))


def test_event_chronology_and_authority_fail_closed():
    assert_code(
        TruthErrorCode.CHRONOLOGY_CONFLICT,
        lambda: event_record(recorded_at=NOW - timedelta(seconds=1)),
    )
    assert_code(
        TruthErrorCode.INVALID_TIMESTAMP,
        lambda: event_record(occurred_at=NOW.replace(tzinfo=None)),
    )
    assert_code(
        TruthErrorCode.INVALID_AUTHORITY_DECISION,
        lambda: event_record(capability="event.view"),
    )
    assert_code(
        TruthErrorCode.INVALID_AUTHORITY_DECISION,
        lambda: event_record(grant=False),
    )


def test_tampering_is_detected_before_projection_or_collection_use():
    item = event_record()
    object.__setattr__(item, "reason_code", "forged")
    auth = authority_decision(
        resource_type="truth_event", resource_id=item.event_id, capability="event.view",
        actor_ref="principal:viewer", available_fields=EVENT_FIELDS,
        resource_scope=item.scope,
    )
    assert_code(TruthErrorCode.INVALID_PROJECTION, lambda: project_event_record(item, auth))
    assert_code(
        TruthErrorCode.INVALID_EVENT,
        lambda: validate_accountability_collection(
            events=[item], decisions=[], approvals=[], lineages=[],
            contexts=[context_record()], evidence_records=[evidence_record()],
        ),
    )


def test_decision_is_separate_authority_bound_and_basis_explicit():
    item = decision_record()
    assert item.target_id != item.decision_id
    assert item.supporting_event_ids == ("evt1:observed:alpha",)
    assert item.authority_provenance.capability_code == "decision.issue"
    assert not hasattr(item, "confidence") and not hasattr(item, "model_output")
    assert_code(
        TruthErrorCode.INVALID_DECISION,
        lambda: decision_record(supporting_event_ids=()),
    )
    assert_code(
        TruthErrorCode.INVALID_AUTHORITY_DECISION,
        lambda: decision_record(capability="decision.view"),
    )


def test_decision_unknown_and_translated_enums_and_unknown_version_reject():
    item_scope = scope_for("dec1:bad:alpha")
    auth = authority_decision(
        resource_type="truth_decision", resource_id="dec1:bad:alpha",
        capability="decision.issue", actor_ref="principal:decider",
        available_fields=DECISION_FIELDS, resource_scope=item_scope,
    )
    common = dict(
        decision_id="dec1:bad:alpha", decision_type=DecisionType.CONCLUSION,
        target_type=TargetType.EVENT, target_id="evt1:observed:alpha", scope=item_scope,
        decision_maker=actor("principal:decider"), issued_at=NOW,
        outcome=DecisionOutcome.SELECTED, status=DecisionStatus.ISSUED,
        supporting_event_ids=("evt1:observed:alpha",), authority_decision=auth,
        classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=POLICY_ID, visibility_policy_version=POLICY_VERSION,
    )
    for key, invalid in (("decision_type", "CONCLUSION"), ("status", "emitida"), ("outcome", True)):
        assert_code(TruthErrorCode.INVALID_DECISION, lambda key=key, invalid=invalid: create_decision_record(**(common | {key: invalid})))
    assert_code(
        TruthErrorCode.INVALID_VERSION,
        lambda: create_decision_record(**(common | {"contract_version": "marketmatch-truth-accountability-v2"})),
    )


@pytest.mark.parametrize("status", [True, 1, "true", "APPROVED", "aprobado"])
def test_approval_status_is_literal_enum_only(status):
    item_scope = scope_for("apr1:bad:alpha")
    auth = authority_decision(
        resource_type="truth_approval", resource_id="apr1:bad:alpha",
        capability="approval.record", actor_ref="principal:approver",
        available_fields=APPROVAL_FIELDS, resource_scope=item_scope,
    )
    assert_code(
        TruthErrorCode.INVALID_APPROVAL,
        lambda: create_approval_record(
            approval_id="apr1:bad:alpha", approval_type=ApprovalType.REVIEW,
            target_type=TargetType.DECISION, target_id="dec1:course:alpha", scope=item_scope,
            approver=actor("principal:approver"), status=status, recorded_at=NOW,
            authority_decision=auth, classification=ResourceClassification.CONFIDENTIAL,
            visibility_policy_id=POLICY_ID, visibility_policy_version=POLICY_VERSION,
        ),
    )


def test_approval_is_separate_does_not_mutate_target_and_requires_approval_capability():
    decision = decision_record()
    before = decision._integrity
    approval = approval_record()
    assert approval.approval_id != decision.decision_id
    assert approval.target_id == decision.decision_id
    assert decision._integrity == before
    assert approval.authority_provenance.capability_code == "approval.record"
    assert_code(
        TruthErrorCode.INVALID_APPROVAL,
        lambda: approval_record(capability="decision.issue"),
    )


def test_approval_requirement_no_approval_and_one_approval_are_explicit():
    no_approval = ApprovalRequirement(
        "requirement:none", TargetType.DECISION, "dec1:course:alpha",
        scope_for("dec1:course:alpha"), (), POLICY_ID, POLICY_VERSION,
    )
    result = evaluate_approval_requirement(no_approval, [], evaluated_at=NOW + timedelta(hours=1))
    assert result.status is RequirementStatus.SATISFIED
    assert result.reason_code is RequirementReason.NO_APPROVAL_REQUIRED

    required = ApprovalRequirement(
        "requirement:review", TargetType.DECISION, "dec1:course:alpha",
        scope_for("dec1:course:alpha"), (ApprovalSlot("approval.record"),),
        POLICY_ID, POLICY_VERSION,
    )
    missing = evaluate_approval_requirement(required, [], evaluated_at=NOW + timedelta(hours=1))
    assert missing.status is RequirementStatus.UNRESOLVED
    assert missing.reason_code is RequirementReason.MISSING_APPROVAL
    satisfied = evaluate_approval_requirement(
        required, [approval_record()], evaluated_at=NOW + timedelta(hours=1)
    )
    assert satisfied.status is RequirementStatus.SATISFIED


def test_approval_requirement_rejects_inactive_expired_and_duplicate_slot_authority():
    requirement = ApprovalRequirement(
        "requirement:multi", TargetType.DECISION, "dec1:course:alpha",
        scope_for("dec1:course:alpha"),
        (ApprovalSlot("approval.record"), ApprovalSlot("approval.release")),
        POLICY_ID, POLICY_VERSION,
    )
    first = approval_record()
    second = approval_record(
        "apr1:release:alpha", capability="approval.release", actor_ref="principal:approver"
    )
    result = evaluate_approval_requirement(requirement, [first, second], evaluated_at=NOW + timedelta(hours=1))
    assert result.status is RequirementStatus.UNRESOLVED
    expired = approval_record("apr1:expired:alpha", expires_at=NOW + timedelta(minutes=4))
    one = ApprovalRequirement(
        "requirement:one", TargetType.DECISION, "dec1:course:alpha",
        scope_for("dec1:course:alpha"), (ApprovalSlot("approval.record"),), POLICY_ID, POLICY_VERSION,
    )
    result = evaluate_approval_requirement(one, [expired], evaluated_at=NOW + timedelta(hours=1))
    assert result.reason_code is RequirementReason.APPROVAL_EXPIRED
    withdrawn = approval_record("apr1:withdrawn:alpha", status=ApprovalStatus.WITHDRAWN)
    result = evaluate_approval_requirement(one, [withdrawn], evaluated_at=NOW + timedelta(hours=1))
    assert result.reason_code is RequirementReason.APPROVAL_INACTIVE


def test_contradictory_approvals_remain_records_and_are_detected():
    approved = approval_record("apr1:approved:alpha")
    rejected = approval_record("apr1:rejected:alpha", status=ApprovalStatus.REJECTED)
    requirement = ApprovalRequirement(
        "requirement:one", TargetType.DECISION, "dec1:course:alpha",
        scope_for("dec1:course:alpha"), (ApprovalSlot("approval.record"),), POLICY_ID, POLICY_VERSION,
    )
    result = evaluate_approval_requirement(requirement, [approved, rejected], evaluated_at=NOW + timedelta(hours=1))
    assert result.status is RequirementStatus.CONFLICT
    assert result.reason_code is RequirementReason.CONFLICTING_APPROVALS
    assert approved.status is ApprovalStatus.APPROVED
    assert rejected.status is ApprovalStatus.REJECTED


def test_collection_validates_references_scope_policy_and_conflicts_without_resolution():
    event = event_record()
    decision = decision_record()
    approval = approval_record()
    validated = validate_accountability_collection(
        events=[event], decisions=[decision], approvals=[approval], lineages=[],
        contexts=[context_record()], evidence_records=[evidence_record()],
    )
    assert validated.events == (event,)
    assert validated.decisions == (decision,)
    assert validated.approvals == (approval,)

    assert_code(
        TruthErrorCode.MISSING_REFERENCE,
        lambda: validate_accountability_collection(
            events=[event], decisions=[decision], approvals=[approval], lineages=[],
            contexts=[], evidence_records=[evidence_record()],
        ),
    )
    other_org = context_record(organization="org:other")
    assert_code(
        TruthErrorCode.SCOPE_CONFLICT,
        lambda: validate_accountability_collection(
            events=[event], decisions=[decision], approvals=[approval], lineages=[],
            contexts=[other_org], evidence_records=[evidence_record()],
        ),
    )


def test_collection_detects_conflicting_active_decisions():
    event = event_record()
    first = decision_record()
    second = decision_record("dec1:course:beta")
    assert_code(
        TruthErrorCode.CONFLICTING_DECISIONS,
        lambda: validate_accountability_collection(
            events=[event], decisions=[first, second], approvals=[], lineages=[],
            contexts=[context_record()], evidence_records=[evidence_record()],
        ),
    )


def test_lineage_preserves_history_and_rejects_self_and_cycles():
    original = event_record()
    corrected = event_record(
        "evt1:corrected:alpha", event_type=EventType.CORRECTED,
        source_event_id=original.event_id, occurred_at=NOW + timedelta(minutes=2),
    )
    relation = AccountabilityLineage(
        RecordKind.EVENT, corrected.event_id, original.event_id,
        LineageType.CORRECTS, NOW + timedelta(minutes=4), POLICY_ID, POLICY_VERSION,
    )
    validated = validate_accountability_collection(
        events=[original, corrected], decisions=[], approvals=[], lineages=[relation],
        contexts=[context_record()], evidence_records=[evidence_record()],
    )
    assert {item.event_id for item in validated.events} == {original.event_id, corrected.event_id}
    assert original.event_type is EventType.OBSERVED
    assert_code(
        TruthErrorCode.INVALID_LINEAGE,
        lambda: AccountabilityLineage(
            RecordKind.EVENT, original.event_id, original.event_id,
            LineageType.CORRECTS, NOW, POLICY_ID, POLICY_VERSION,
        ),
    )
    reverse = AccountabilityLineage(
        RecordKind.EVENT, original.event_id, corrected.event_id,
        LineageType.SUPERSEDES, NOW + timedelta(minutes=5), POLICY_ID, POLICY_VERSION,
    )
    assert_code(
        TruthErrorCode.LINEAGE_CYCLE,
        lambda: validate_accountability_collection(
            events=[original, corrected], decisions=[], approvals=[],
            lineages=[relation, reverse], contexts=[context_record()],
            evidence_records=[evidence_record()],
        ),
    )


def test_projection_is_authority_bound_flat_new_and_restrictive():
    item = event_record()
    visible = frozenset({"event_id", "event_type", "recorded_at"})
    auth = authority_decision(
        resource_type="truth_event", resource_id=item.event_id, capability="event.view",
        actor_ref="principal:viewer", available_fields=EVENT_FIELDS,
        visible_fields=visible, resource_scope=item.scope,
    )
    projected = project_event_record(item, auth)
    assert projected == {
        "event_id": item.event_id,
        "event_type": "OBSERVED",
        "recorded_at": "2026-07-20T18:01:00Z",
    }
    assert projected is not item
    assert "actor_ref" not in projected and "evidence_ids" not in projected
    assert all(value is None or type(value) in (str, int, float, bool) for value in projected.values())

    wrong = authority_decision(
        resource_type="truth_event", resource_id="evt1:other:alpha", capability="event.view",
        actor_ref="principal:viewer", available_fields=EVENT_FIELDS,
        resource_scope=scope_for("evt1:other:alpha"),
    )
    assert_code(TruthErrorCode.INVALID_AUTHORITY_DECISION, lambda: project_event_record(item, wrong))


def test_all_projectors_hide_notes_actors_and_source_references_when_not_visible():
    records = (event_record(), decision_record(), approval_record())
    functions = (project_event_record, project_decision_record, project_approval_record)
    resource_types = ("truth_event", "truth_decision", "truth_approval")
    id_fields = ("event_id", "decision_id", "approval_id")
    field_sets = (EVENT_FIELDS, DECISION_FIELDS, APPROVAL_FIELDS)
    for record, function, resource_type, id_field, fields in zip(
        records, functions, resource_types, id_fields, field_sets, strict=True
    ):
        record_id = getattr(record, id_field)
        auth = authority_decision(
            resource_type=resource_type, resource_id=record_id, capability=f"{id_field.removesuffix('_id')}.view",
            actor_ref="principal:viewer", available_fields=fields,
            visible_fields=frozenset({id_field}), resource_scope=record.scope,
        )
        assert function(record, auth) == {id_field: record_id}


class HostileMapping(dict):
    def items(self):
        raise RuntimeError("token:secret address:private cost:999")


def test_hostile_projection_mapping_has_fixed_safe_error():
    auth = authority_decision(
        resource_type="truth_event", resource_id="evt1:hostile:alpha", capability="event.view",
        actor_ref="principal:viewer", available_fields=EVENT_FIELDS,
        resource_scope=scope_for("evt1:hostile:alpha"),
    )
    assert_code(TruthErrorCode.INVALID_PROJECTION, lambda: project_event_record(HostileMapping(), auth))


def test_audit_builders_emit_only_bounded_codes_and_never_source_notes():
    item = event_record()
    auth = authority_decision(
        resource_type="truth_event", resource_id=item.event_id, capability="event.project",
        actor_ref="principal:reporter", available_fields=EVENT_FIELDS,
        visible_fields=frozenset({"event_id", "actor_ref"}), resource_scope=item.scope,
    )
    audit = build_safe_audit_record(
        audit_id="aud1:event:alpha", action_code="event.project",
        target_type="truth_event", target_id=item.event_id, decision=auth,
        timestamp=NOW, actor=item.actor, correlation_id="correlation:alpha",
    )
    values = audit._integrity
    assert audit.actor_ref == "principal:reporter"
    assert audit.reason_code == "ALLOWED"
    joined = " ".join(str(value) for value in values)
    for forbidden in ("observation.recorded", "fictional evidence", "cost", "cookie", "session", "token"):
        assert forbidden not in joined
    rejection = build_rejection_audit_record(
        audit_id="aud1:rejected:alpha", action_code="record.reject",
        target_type="malformed_record", target_id="invalid:record:alpha",
        reason_code=TruthErrorCode.INVALID_EVENT, policy_id=POLICY_ID,
        policy_version=POLICY_VERSION, timestamp=NOW,
    )
    assert rejection.reason_code == "INVALID_EVENT"
    assert not hasattr(rejection, "metadata") and not hasattr(rejection, "exception")


@pytest.mark.parametrize("unsafe", ["line\nbreak", "cost:100:usd", "token:secret", "user@example.com", "a" * 200])
def test_audit_rejects_injected_or_sensitive_identifiers(unsafe):
    assert_code(
        TruthErrorCode.INVALID_AUDIT,
        lambda: build_rejection_audit_record(
            audit_id="aud1:rejected:alpha", action_code="record.reject",
            target_type="malformed_record", target_id=unsafe,
            reason_code=TruthErrorCode.INVALID_EVENT, policy_id=POLICY_ID,
            policy_version=POLICY_VERSION, timestamp=NOW,
        ),
    )


def source_decision(record, resource_type: str, available: frozenset[str], visible: frozenset[str]):
    return authority_decision(
        resource_type=resource_type,
        resource_id=getattr(record, {"truth_event": "event_id", "truth_decision": "decision_id", "truth_approval": "approval_id", "evidence": "evidence_id", "operational_context": "context_id"}[resource_type]),
        capability="source.view", actor_ref="principal:viewer", available_fields=available,
        visible_fields=visible, resource_scope=record.scope,
        classification=record.classification,
        policy_id=record.visibility_policy_id,
        policy_version=record.visibility_policy_version,
    )


def test_derived_accountability_view_requires_all_sources_and_inherits_restrictions():
    event = event_record()
    decision = decision_record()
    event_auth = source_decision(event, "truth_event", EVENT_FIELDS, frozenset({"event_id", "event_type"}))
    decision_auth = source_decision(decision, "truth_decision", DECISION_FIELDS, frozenset({"decision_id"}))
    derived = create_derived_accountability_view(
        [(event, event_auth), (decision, decision_auth)],
        artifact_kind=DerivedArtifactKind.SUMMARY,
        generator=ActorReference("system:summary", ActorKind.SYSTEM),
    )
    assert derived.visible_fields == frozenset()
    assert derived.classification is ResourceClassification.CONFIDENTIAL
    assert derived.source_resource_ids == tuple(sorted((event.event_id, decision.decision_id)))
    assert derived.derived is True
    assert not hasattr(derived, "decision_id") and not hasattr(derived, "approval_id")


def test_derived_view_denied_or_incompatible_source_blocks_instead_of_omitting():
    event = event_record()
    denied = authority_decision(
        resource_type="truth_event", resource_id=event.event_id, capability="source.view",
        actor_ref="principal:viewer", available_fields=EVENT_FIELDS,
        resource_scope=event.scope, grant=False,
    )
    assert_code(
        TruthErrorCode.INVALID_AUTHORITY_DECISION,
        lambda: create_derived_accountability_view(
            [(event, denied)], artifact_kind=DerivedArtifactKind.SUMMARY,
            generator=ActorReference("system:summary", ActorKind.SYSTEM),
        ),
    )
    other = event_record("evt1:observed:other", organization="org:other")
    first_auth = source_decision(event, "truth_event", EVENT_FIELDS, frozenset({"event_id"}))
    other_auth = source_decision(other, "truth_event", EVENT_FIELDS, frozenset({"event_id"}))
    assert_code(
        TruthErrorCode.SOURCE_SCOPE_CONFLICT,
        lambda: create_derived_accountability_view(
            [(event, first_auth), (other, other_auth)],
            artifact_kind=DerivedArtifactKind.REPORT,
            generator=ActorReference("system:report", ActorKind.SYSTEM),
        ),
    )


def test_evidence_and_context_integrations_preserve_separate_authorization():
    evidence = evidence_record()
    context = context_record()
    evidence_fields = frozenset({"evidence_id", "evidence_kind", "classification"})
    context_fields = frozenset({"context_id", "context_type", "classification"})
    evidence_auth = source_decision(evidence, "evidence", evidence_fields, frozenset({"evidence_id"}))
    context_auth = source_decision(context, "operational_context", context_fields, frozenset({"context_id"}))
    derived = create_derived_accountability_view(
        [(evidence, evidence_auth), (context, context_auth)],
        artifact_kind=DerivedArtifactKind.REPORT,
        generator=ActorReference("system:report", ActorKind.SYSTEM),
    )
    assert derived.visible_fields == frozenset()
    assert EVIDENCE_ID in derived.source_resource_ids
    assert CONTEXT_ID in derived.source_resource_ids


def test_locale_and_timezone_do_not_change_authority_or_canonical_chronology():
    item = event_record()
    decisions = [
        authority_decision(
            resource_type="truth_event", resource_id=item.event_id, capability="event.view",
            actor_ref="principal:viewer", available_fields=EVENT_FIELDS,
            visible_fields=frozenset({"event_id", "recorded_at"}), resource_scope=item.scope,
            locale=locale, display_timezone=display_timezone,
        )
        for locale, display_timezone in (
            ("es", "America/Santo_Domingo"), ("en", "UTC"), ("zh-Hans", "Asia/Shanghai")
        )
    ]
    assert decisions[0].allowed == decisions[1].allowed == decisions[2].allowed
    assert project_event_record(item, decisions[0]) == project_event_record(item, decisions[1]) == project_event_record(item, decisions[2])


def test_module_is_pure_and_reuses_committed_kernels_without_new_frameworks():
    path = Path(__file__).parents[1] / "src" / "marketmatch_truth_accountability.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "src.marketmatch_authority" in imports
    assert "src.marketmatch_evidence" in imports
    assert "src.marketmatch_operational_context" in imports
    forbidden = ("requests", "httpx", "openai", "langgraph", "deerflow", "sqlalchemy", "sqlite3")
    assert not any(f"import {name}" in source or f"from {name}" in source for name in forbidden)
    for token in ("localStorage", "sessionStorage", "open(", ".write(", ".execute(", "requests.", "httpx."):
        assert token not in source


def test_no_current_route_imports_truth_kernel_and_no_migration_was_added():
    root = Path(__file__).parents[1]

    protected_kernels = {
        "src.marketmatch_truth_accountability",
        "src.marketmatch_work_orchestration",
    }

    def imported_protected_kernels(source: str) -> set[str]:
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in protected_kernels:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(
                    alias.name for alias in node.names if alias.name in protected_kernels
                )
        return imported

    def route_source_is_isolated(source: str) -> bool:
        return not imported_protected_kernels(source)

    work_kernel = root / "src" / "marketmatch_work_orchestration.py"
    assert imported_protected_kernels(work_kernel.read_text(encoding="utf-8")) == {
        "src.marketmatch_truth_accountability"
    }

    route_modules = [root / "app.py", *(root / "routes").rglob("*.py")]
    assert all(
        route_source_is_isolated(path.read_text(encoding="utf-8"))
        for path in route_modules
    )

    synthetic_route_import = (
        "from src.marketmatch_truth_accountability import OperationalEvent\n"
    )
    assert imported_protected_kernels(synthetic_route_import) == {
        "src.marketmatch_truth_accountability"
    }
    assert not route_source_is_isolated(synthetic_route_import)
    assert not any("truth" in path.name.lower() for path in (root / "alembic" / "versions").glob("*.py"))


def test_adr_documents_contract_boundaries_glossary_and_deferrals():
    adr = (
        Path(__file__).parents[1]
        / "docs"
        / "adr"
        / "0004-marketmatch-truth-accountability-kernel-v1.md"
    ).read_text(encoding="utf-8")
    required = (
        "Event", "Evidence", "Operational Context", "Decision", "Approval",
        "SafeAuditRecord", "facts", "conclusions", "immutable", "chronology",
        "Authority", "projection", "derived accountability", "Locale", "timezone",
        "Current state", "Compatibility adapters", "Persistence", "Stable code glossary",
        "Fictional examples", "no database", "no current endpoint",
        "does not determine legal truth",
    )
    assert all(term.lower() in adr.lower() for term in required)
    assert "GCO" not in adr


def test_adversarial_generator_provenance_cannot_be_attached_to_non_agent_origin():
    for kind in (ActorKind.HUMAN, ActorKind.SYSTEM, ActorKind.EXTERNAL_SYSTEM):
        assert_code(
            TruthErrorCode.INVALID_ACTOR,
            lambda kind=kind: ActorReference("system:origin", kind, "model:local", "v1"),
        )


def test_adversarial_requirement_scope_is_exact_and_cannot_be_global_or_mismatched():
    for invalid_scope in (
        AuthorityScope(global_scope=True),
        scope_for("dec1:other:alpha"),
        AuthorityScope(
            organization_id=None, product_id=PRODUCT, workspace_id=WORKSPACE,
            project_id=PROJECT, resource_id="dec1:course:alpha", owner_party_id=OWNER,
        ),
    ):
        assert_code(
            TruthErrorCode.INVALID_REQUIREMENT,
            lambda invalid_scope=invalid_scope: ApprovalRequirement(
                "requirement:bad", TargetType.DECISION, "dec1:course:alpha",
                invalid_scope, (ApprovalSlot("approval.record"),), POLICY_ID, POLICY_VERSION,
            ),
        )


def event_mapping(item: OperationalEvent) -> dict[str, object]:
    auth = authority_decision(
        resource_type="truth_event", resource_id=item.event_id, capability="event.view",
        actor_ref="principal:viewer", available_fields=EVENT_FIELDS,
        resource_scope=item.scope,
    )
    return project_event_record(item, auth)


def test_adversarial_mapping_cannot_forge_computed_counts_or_translated_codes():
    item = event_record()
    mapping = event_mapping(item)
    mapping["context_count"] = 999
    mapping["evidence_count"] = 999
    auth = authority_decision(
        resource_type="truth_event", resource_id=item.event_id, capability="event.view",
        actor_ref="principal:viewer", available_fields=EVENT_FIELDS,
        resource_scope=item.scope,
    )
    projected = project_event_record(mapping, auth)
    assert "context_count" not in projected and "evidence_count" not in projected
    mapping["event_type"] = "observado"
    assert_code(TruthErrorCode.INVALID_PROJECTION, lambda: project_event_record(mapping, auth))
    del mapping["event_type"]
    assert_code(TruthErrorCode.INVALID_PROJECTION, lambda: project_event_record(mapping, auth))


def test_adversarial_audit_is_bound_to_authority_resource_type_and_rejects_mutated_actor():
    item = event_record()
    auth = authority_decision(
        resource_type="truth_event", resource_id=item.event_id, capability="event.project",
        actor_ref="principal:reporter", available_fields=EVENT_FIELDS,
        resource_scope=item.scope,
    )
    assert_code(
        TruthErrorCode.INVALID_AUDIT,
        lambda: build_safe_audit_record(
            audit_id="aud1:wrong:type", action_code="event.project",
            target_type="truth_decision", target_id=item.event_id,
            decision=auth, timestamp=NOW,
        ),
    )
    context_target = CONTEXT_ID
    wrong_identity_auth = authority_decision(
        resource_type="truth_event", resource_id=context_target,
        capability="event.project", actor_ref="principal:viewer",
        available_fields=EVENT_FIELDS, resource_scope=scope_for(context_target),
    )
    assert_code(
        TruthErrorCode.INVALID_AUDIT,
        lambda: build_safe_audit_record(
            audit_id="aud1:wrong:identity", action_code="event.project",
            target_type="truth_event", target_id=context_target,
            decision=wrong_identity_auth, timestamp=NOW,
        ),
    )
    compromised = actor("principal:reporter")
    object.__setattr__(compromised, "actor_ref", "principal:forged")
    assert_code(
        TruthErrorCode.INVALID_AUDIT,
        lambda: build_safe_audit_record(
            audit_id="aud1:bad:actor", action_code="event.project",
            target_type="truth_event", target_id=item.event_id,
            decision=auth, timestamp=NOW, actor=compromised,
        ),
    )


def test_adversarial_collection_limits_apply_to_context_and_evidence_registries():
    context = context_record()
    evidence = evidence_record()
    assert_code(
        TruthErrorCode.COLLECTION_LIMIT_EXCEEDED,
        lambda: validate_accountability_collection(
            events=[], decisions=[], approvals=[], lineages=[],
            contexts=[context] * (MAX_RECORDS_PER_KIND + 1), evidence_records=[],
        ),
    )
    assert_code(
        TruthErrorCode.COLLECTION_LIMIT_EXCEEDED,
        lambda: validate_accountability_collection(
            events=[], decisions=[], approvals=[], lineages=[],
            contexts=[], evidence_records=[evidence] * (MAX_RECORDS_PER_KIND + 1),
        ),
    )


def test_adversarial_correction_cannot_predate_original_recording():
    original = event_record()
    corrected = event_record(
        "evt1:corrected:early", event_type=EventType.CORRECTED,
        source_event_id=original.event_id, occurred_at=NOW + timedelta(seconds=30),
    )
    assert_code(
        TruthErrorCode.CHRONOLOGY_CONFLICT,
        lambda: validate_accountability_collection(
            events=[original, corrected], decisions=[], approvals=[], lineages=[],
            contexts=[context_record()], evidence_records=[evidence_record()],
        ),
    )


def test_adversarial_derived_view_rejects_duplicate_sources_instead_of_double_counting():
    item = event_record()
    auth = source_decision(item, "truth_event", EVENT_FIELDS, frozenset({"event_id"}))
    assert_code(
        TruthErrorCode.SOURCE_NOT_AUTHORIZED,
        lambda: create_derived_accountability_view(
            [(item, auth), (item, auth)], artifact_kind=DerivedArtifactKind.SUMMARY,
            generator=ActorReference("system:summary", ActorKind.SYSTEM),
        ),
    )


def test_adversarial_cross_project_and_policy_basis_fail_closed():
    event = event_record()
    cross_project = decision_record("dec1:cross:project", project="project:other")
    assert_code(
        TruthErrorCode.SCOPE_CONFLICT,
        lambda: validate_accountability_collection(
            events=[event], decisions=[cross_project], approvals=[], lineages=[],
            contexts=[context_record()], evidence_records=[evidence_record()],
        ),
    )
    cross_policy = decision_record("dec1:cross:policy", policy_id="marketmatch.other")
    assert_code(
        TruthErrorCode.POLICY_CONFLICT,
        lambda: validate_accountability_collection(
            events=[event], decisions=[cross_policy], approvals=[], lineages=[],
            contexts=[context_record()], evidence_records=[evidence_record()],
        ),
    )


def test_adversarial_authority_decision_mutation_and_record_mutation_fail_closed():
    item = decision_record()
    auth = authority_decision(
        resource_type="truth_decision", resource_id=item.decision_id,
        capability="decision.view", actor_ref="principal:viewer",
        available_fields=DECISION_FIELDS, resource_scope=item.scope,
    )
    object.__setattr__(auth, "allowed", False)
    assert_code(TruthErrorCode.INVALID_AUTHORITY_DECISION, lambda: project_decision_record(item, auth))


def test_adversarial_empty_derived_sources_and_fake_ai_provenance_reject():
    assert_code(
        TruthErrorCode.SOURCE_NOT_AUTHORIZED,
        lambda: create_derived_accountability_view(
            [], artifact_kind=DerivedArtifactKind.SUMMARY,
            generator=ActorReference("system:summary", ActorKind.SYSTEM),
        ),
    )
    assert_code(
        TruthErrorCode.INVALID_ACTOR,
        lambda: ActorReference("agent:summary", ActorKind.AGENT),
    )


def test_adversarial_mutated_approval_requirement_and_slot_fail_closed():
    slot = ApprovalSlot("approval.record")
    requirement = ApprovalRequirement(
        "requirement:tamper", TargetType.DECISION, "dec1:course:alpha",
        scope_for("dec1:course:alpha"), (slot,), POLICY_ID, POLICY_VERSION,
    )
    object.__setattr__(slot, "required_count", 0)
    assert_code(
        TruthErrorCode.INVALID_REQUIREMENT,
        lambda: evaluate_approval_requirement(requirement, [], evaluated_at=NOW),
    )


def test_adversarial_correction_and_decision_basis_cannot_broaden_visibility():
    source = event_record(classification=ResourceClassification.RESTRICTED)
    correction = event_record(
        "evt1:corrected:public", event_type=EventType.CORRECTED,
        source_event_id=source.event_id, occurred_at=NOW + timedelta(minutes=2),
        classification=ResourceClassification.PUBLIC,
    )
    assert_code(
        TruthErrorCode.VISIBILITY_CONFLICT,
        lambda: validate_accountability_collection(
            events=[source, correction], decisions=[], approvals=[], lineages=[],
            contexts=[context_record()], evidence_records=[evidence_record()],
        ),
    )
    decision = decision_record(classification=ResourceClassification.PUBLIC)
    assert_code(
        TruthErrorCode.VISIBILITY_CONFLICT,
        lambda: validate_accountability_collection(
            events=[source], decisions=[decision], approvals=[], lineages=[],
            contexts=[context_record()], evidence_records=[evidence_record()],
        ),
    )


def test_adversarial_duplicate_or_conflicting_lineage_is_rejected():
    original = event_record()
    corrected = event_record(
        "evt1:corrected:duplicate", event_type=EventType.CORRECTED,
        source_event_id=original.event_id, occurred_at=NOW + timedelta(minutes=2),
    )
    relation = AccountabilityLineage(
        RecordKind.EVENT, corrected.event_id, original.event_id,
        LineageType.CORRECTS, NOW + timedelta(minutes=4), POLICY_ID, POLICY_VERSION,
    )
    assert_code(
        TruthErrorCode.INVALID_LINEAGE,
        lambda: validate_accountability_collection(
            events=[original, corrected], decisions=[], approvals=[],
            lineages=[relation, relation], contexts=[context_record()],
            evidence_records=[evidence_record()],
        ),
    )
