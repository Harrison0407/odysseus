from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.marketmatch_authority import (
    AuthorityScope, AuthorizationRequest, PartyKind, PartyReference, PrincipalContext,
    ResourceClassification, ResourceContext, ScopedCapabilityGrant, VisibilityMode,
    VisibilityPolicy, evaluate_authorization,
)
from src.marketmatch_evidence import (
    AcquisitionMethod, ContentIntegrityDescriptor, EvidenceKind, EvidenceRecord,
    MediaKind, ProvenanceContext, SourceKind,
)
from src.marketmatch_operational_context import ContextType, OperationalContextRecord
from src.marketmatch_truth_accountability import (
    ActorKind, ActorReference, ApprovalStatus, ApprovalType, TargetType,
    create_approval_record,
)
from src.marketmatch_work_orchestration import (
    AssignmentStatus, AttemptStatus, ClaimOutcome, CompletionCriterion,
    CriterionType, DependencyType, EscalationSeverity, EscalationStatus,
    EvaluationStatus, ExecutorKind, ExecutorReference, HandoffStatus,
    RetryPolicy, RetryReason, WORK_CONTRACT_VERSION, WorkContractError,
    SafeWorkAudit, WorkDependency, WorkErrorCode, WorkPriority, WorkStatus, WorkType,
    WorkloadViewKind, adapt_current_task, build_safe_work_audit,
    build_work_rejection_audit, create_assignment, create_completion_claim,
    create_derived_workload_view, create_escalation, create_execution_attempt,
    create_handoff, create_work_item, create_work_transition, evaluate_completion,
    evaluate_retry, project_assignment, project_completion_claim, project_escalation,
    project_execution_attempt, project_handoff, project_work_item,
    validate_assignment_id, validate_attempt_id, validate_claim_id,
    validate_escalation_id, validate_handoff_id, validate_transition_id,
    validate_work_audit_id, validate_work_collection, validate_work_id,
)


NOW = datetime(2026, 7, 20, 18, 0, tzinfo=timezone.utc)
POLICY = "marketmatch.work"
VERSION = "v1"
ORG = "org:alpha"
PRODUCT = "marketmatch"
WORKSPACE = "workspace:alpha"
PROJECT = "project:alpha"
OWNER = "party:owner"
CONTEXT_ID = "ctx1:project:alpha"
EVIDENCE_ID = "ev1:document:alpha"

COMMON_FIELDS = frozenset({
    "contract_version", "classification", "visibility_policy_id", "visibility_policy_version",
    "scope_organization_id", "scope_product_id", "scope_workspace_id",
    "scope_project_id", "scope_resource_id", "scope_owner_party_id", "scope_global",
})
WORK_FIELDS = COMMON_FIELDS | frozenset({
    "work_id", "work_type", "requester_ref", "requester_kind", "created_at",
    "ready_at", "due_at", "priority", "initial_status", "context_count",
    "input_count", "criteria_count",
})
ASSIGN_FIELDS = COMMON_FIELDS | frozenset({
    "assignment_id", "work_id", "executor_id", "executor_kind", "assigner_ref",
    "assigned_at", "responded_at", "acceptance_deadline", "due_at", "status",
    "required_execution_capability", "reason_code", "supersedes_assignment_id",
})
ATTEMPT_FIELDS = COMMON_FIELDS | frozenset({
    "attempt_id", "work_id", "assignment_id", "executor_id", "executor_kind",
    "sequence", "started_at", "ended_at", "status", "outcome_code", "input_count", "result_count",
})
HANDOFF_FIELDS = COMMON_FIELDS | frozenset({
    "handoff_id", "work_id", "source_assignment_id", "target_assignment_id",
    "initiated_by", "initiated_at", "accepted_at", "status", "reason_code",
})
ESCALATION_FIELDS = COMMON_FIELDS | frozenset({
    "escalation_id", "work_id", "raised_by", "target_ref", "target_capability",
    "severity", "reason_code", "raised_at", "response_due_at", "status",
})
CLAIM_FIELDS = COMMON_FIELDS | frozenset({
    "claim_id", "work_id", "assignment_id", "attempt_id", "executor_id",
    "executor_kind", "claimed_at", "outcome", "result_count", "assertion_count",
})


def assert_code(code: WorkErrorCode, call) -> None:
    with pytest.raises(WorkContractError) as caught:
        call()
    assert caught.value.code is code
    assert str(caught.value) == code.value


def scope_for(resource_id: str, *, org: str = ORG, project: str = PROJECT) -> AuthorityScope:
    return AuthorityScope(organization_id=org, product_id=PRODUCT, workspace_id=WORKSPACE,
                          project_id=project, resource_id=resource_id, owner_party_id=OWNER)


def decision(*, resource_type: str, resource_id: str, capability: str, actor: str,
             fields: frozenset[str], scope: AuthorityScope | None = None,
             visible: frozenset[str] | None = None, grant: bool = True,
             policy: str = POLICY, classification=ResourceClassification.CONFIDENTIAL,
             locale: str = "en", tz: str = "UTC"):
    actual_scope = scope or scope_for(resource_id)
    principal = PrincipalContext(
        principal_ref=actor, party=PartyReference(actor, PartyKind.PERSON),
        capability_grants=(ScopedCapabilityGrant(capability, actual_scope, "test:grant"),) if grant else (),
    )
    visibility = VisibilityPolicy(policy, VERSION, VisibilityMode.CONTROLLED_CONFIDENTIALITY,
                                  frozenset({capability}), fields if visible is None else visible,
                                  frozenset(ResourceClassification))
    return evaluate_authorization(
        AuthorizationRequest(principal, capability, ResourceContext(resource_type, resource_id,
                             actual_scope, classification, fields), fields,
                             presentation_locale=locale, display_timezone=tz), visibility)


def human(ref: str = "principal:requester") -> ActorReference:
    return ActorReference(ref, ActorKind.HUMAN)


def human_executor(ref: str = "principal:worker") -> ExecutorReference:
    return ExecutorReference(ref, ExecutorKind.HUMAN)


def context(context_id: str = CONTEXT_ID, *, org: str = ORG, project: str = PROJECT,
            policy: str = POLICY, classification=ResourceClassification.CONFIDENTIAL):
    return OperationalContextRecord(
        context_id, ContextType.PROJECT, scope_for(context_id, org=org, project=project),
        NOW - timedelta(days=1), classification, policy, VERSION,
        owner_party_ref=OWNER, display_name="Fictional Project Alpha",
    )


def evidence(evidence_id: str = EVIDENCE_ID, *, org: str = ORG, project: str = PROJECT,
             policy: str = POLICY, classification=ResourceClassification.CONFIDENTIAL):
    return EvidenceRecord(
        evidence_id, EvidenceKind.DOCUMENT, MediaKind.TEXT,
        scope_for(evidence_id, org=org, project=project), OWNER,
        ContentIntegrityDescriptor.from_bytes(b"fictional"),
        ProvenanceContext(SourceKind.USER, AcquisitionMethod.UPLOAD),
        NOW - timedelta(hours=1), classification, policy, VERSION,
    )


def work(work_id: str = "wrk1:review:alpha", *, initial=WorkStatus.READY,
         criteria=(CompletionCriterion(CriterionType.RESULT_REFERENCE_REQUIRED),),
         org: str = ORG, project: str = PROJECT, policy: str = POLICY,
         classification=ResourceClassification.CONFIDENTIAL, due_at=NOW + timedelta(days=1),
         source_work_ids=()):
    s = scope_for(work_id, org=org, project=project)
    auth = decision(resource_type="work_item", resource_id=work_id, capability="work.create",
                    actor="principal:requester", fields=WORK_FIELDS, scope=s, policy=policy,
                    classification=classification)
    return create_work_item(
        work_id=work_id, work_type=WorkType.REVIEW, scope=s, context_ids=(CONTEXT_ID,),
        requester=human(), created_at=NOW, ready_at=NOW, due_at=due_at,
        priority=WorkPriority.NORMAL, initial_status=initial, classification=classification,
        visibility_policy_id=policy, visibility_policy_version=VERSION,
        authority_decision=auth, required_capabilities=("work.execute",),
        input_evidence_ids=(EVIDENCE_ID,), completion_criteria=criteria,
        source_work_ids=source_work_ids,
    )


def assignment(assignment_id="asg1:worker:alpha", *, work_id="wrk1:review:alpha",
               executor=None, status=AssignmentStatus.ACCEPTED, supersedes=None,
               org=ORG, project=PROJECT, policy=POLICY,
               assigned_at=NOW + timedelta(minutes=1), include_response=True,
               response_actor=None):
    executor = executor or human_executor()
    s = scope_for(assignment_id, org=org, project=project)
    auth = decision(resource_type="work_assignment", resource_id=assignment_id,
                    capability="work.assign", actor="principal:assigner", fields=ASSIGN_FIELDS,
                    scope=s, policy=policy)
    response_auth = None
    if type(status) is AssignmentStatus and status in {
        AssignmentStatus.ACCEPTED, AssignmentStatus.DECLINED,
    }:
        response_auth = decision(
            resource_type="work_assignment", resource_id=assignment_id,
            capability=f"work.assignment.{status.value.lower()}",
            actor=response_actor or executor.executor_id, fields=ASSIGN_FIELDS,
            scope=s, policy=policy,
        )
    return create_assignment(
        assignment_id=assignment_id, work_id=work_id, assignee=executor,
        assigner=human("principal:assigner"), assigned_at=assigned_at, status=status,
        responded_at=(assigned_at + timedelta(seconds=30)) if include_response and status in {
            AssignmentStatus.ACCEPTED, AssignmentStatus.DECLINED,
            AssignmentStatus.SUPERSEDED, AssignmentStatus.WITHDRAWN,
            AssignmentStatus.EXPIRED, AssignmentStatus.INVALIDATED,
        } else None,
        required_execution_capability="work.execute", scope=s,
        classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=policy, visibility_policy_version=VERSION,
        authority_decision=auth, response_authority_decision=response_auth,
        acceptance_deadline=assigned_at + timedelta(hours=1),
        due_at=assigned_at + timedelta(days=1), reason_code="assignment.recorded",
        supersedes_assignment_id=supersedes,
    )


def attempt(attempt_id="atm1:run:alpha", *, work_id="wrk1:review:alpha",
            assignment_id="asg1:worker:alpha", executor=None, sequence=1,
            status=AttemptStatus.SUCCEEDED, started_at=NOW + timedelta(minutes=2),
            org=ORG, project=PROJECT, policy=POLICY, include_result=True):
    executor = executor or human_executor()
    s = scope_for(attempt_id, org=org, project=project)
    auth = decision(resource_type="work_attempt", resource_id=attempt_id,
                    capability="work.execute", actor=executor.executor_id,
                    fields=ATTEMPT_FIELDS, scope=s, policy=policy)
    return create_execution_attempt(
        attempt_id=attempt_id, work_id=work_id, assignment_id=assignment_id,
        executor=executor, sequence=sequence, started_at=started_at,
        ended_at=None if status is AttemptStatus.STARTED else started_at + timedelta(minutes=1),
        status=status, scope=s, classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=policy, visibility_policy_version=VERSION,
        authority_decision=auth, input_evidence_ids=(EVIDENCE_ID,),
        result_evidence_ids=(EVIDENCE_ID,) if status is AttemptStatus.SUCCEEDED and include_result else (),
        outcome_code="attempt.finished",
    )


def claim(claim_id="clm1:result:alpha", *, attempt_id="atm1:run:alpha",
          assignment_id="asg1:worker:alpha", executor=None, policy=POLICY):
    executor = executor or human_executor()
    s = scope_for(claim_id)
    auth = decision(resource_type="work_completion_claim", resource_id=claim_id,
                    capability="work.completion.claim", actor=executor.executor_id,
                    fields=CLAIM_FIELDS, scope=s, policy=policy)
    return create_completion_claim(
        claim_id=claim_id, work_id="wrk1:review:alpha", assignment_id=assignment_id,
        attempt_id=attempt_id, claimed_by=executor, claimed_at=NOW + timedelta(minutes=4),
        outcome=ClaimOutcome.CLAIMED, scope=s,
        classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=policy, visibility_policy_version=VERSION,
        authority_decision=auth, result_evidence_ids=(EVIDENCE_ID,),
    )


def collection(**overrides):
    values = dict(
        work_items=[work()], assignments=[assignment()], transitions=[], attempts=[attempt()],
        dependencies=[], handoffs=[], escalations=[], claims=[claim()], contexts=[context()],
        evidence_records=[evidence()], events=[], decisions=[], approvals=[], evaluated_at=NOW,
    )
    values.update(overrides)
    return validate_work_collection(**values)


@pytest.mark.parametrize("validator,valid", [
    (validate_work_id, "wrk1:review:alpha"), (validate_assignment_id, "asg1:worker:alpha"),
    (validate_attempt_id, "atm1:run:alpha"), (validate_transition_id, "trn1:start:alpha"),
    (validate_handoff_id, "hnd1:transfer:alpha"), (validate_escalation_id, "esc1:blocker:alpha"),
    (validate_claim_id, "clm1:result:alpha"), (validate_work_audit_id, "waud1:work:alpha"),
])
def test_identifiers_are_distinct_bounded_and_language_neutral(validator, valid):
    assert validator(valid) == valid
    for invalid in ("123", "../secret", " label ", "user@example.com", "cost:100:usd",
                    "evt1:observed:alpha", "ctx1:project:alpha", "ev1:document:alpha",
                    "a" * 64, "a" * 200, "line\nbreak"):
        assert_code(WorkErrorCode.INVALID_IDENTIFIER, lambda invalid=invalid: validator(invalid))


def test_executor_kinds_are_literal_separate_and_secret_safe():
    assert human_executor().executor_kind is ExecutorKind.HUMAN
    assert ExecutorReference("agent:research:one", ExecutorKind.AGENT, "engine:local", "v1")
    assert ExecutorReference("workflow:review:one", ExecutorKind.WORKFLOW, "workflow:local", "v1")
    assert ExecutorReference("service:transcribe:one", ExecutorKind.EXTERNAL_SERVICE, "service:local", "v1")
    for args in (("principal:one", "HUMAN"), ("token:secret", ExecutorKind.HUMAN),
                 ("agent:one", ExecutorKind.AGENT),
                 ("principal:one", ExecutorKind.HUMAN, "provider:one", "v1")):
        assert_code(WorkErrorCode.INVALID_EXECUTOR, lambda args=args: ExecutorReference(*args))


def test_work_item_is_immutable_distinct_and_contains_references_not_raw_data():
    item = work()
    assert item.work_id != CONTEXT_ID != EVIDENCE_ID
    assert item.context_ids == (CONTEXT_ID,) and item.input_evidence_ids == (EVIDENCE_ID,)
    assert item.initial_status is WorkStatus.READY
    assert not hasattr(item, "raw_evidence") and not hasattr(item, "metadata") and not hasattr(item, "model_output")
    with pytest.raises(FrozenInstanceError): item.priority = WorkPriority.HIGH  # type: ignore[misc]


@pytest.mark.parametrize("invalid", ["REVIEW", "installed", True, 1])
def test_unknown_translated_or_workflow_types_reject(invalid):
    s = scope_for("wrk1:bad:type")
    auth = decision(resource_type="work_item", resource_id="wrk1:bad:type", capability="work.create",
                    actor="principal:requester", fields=WORK_FIELDS, scope=s)
    assert_code(WorkErrorCode.INVALID_WORK_ITEM, lambda: create_work_item(
        work_id="wrk1:bad:type", work_type=invalid, scope=s, context_ids=(CONTEXT_ID,),
        requester=human(), created_at=NOW, priority=WorkPriority.NORMAL,
        initial_status=WorkStatus.READY, classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=POLICY, visibility_policy_version=VERSION,
        authority_decision=auth, required_capabilities=("work.execute",),
    ))


def test_work_chronology_scope_version_and_authority_fail_closed():
    assert_code(WorkErrorCode.CHRONOLOGY_CONFLICT,
                lambda: work(due_at=NOW - timedelta(seconds=1)))
    s = scope_for("wrk1:version:bad")
    auth = decision(resource_type="work_item", resource_id="wrk1:version:bad",
                    capability="work.create", actor="principal:requester", fields=WORK_FIELDS, scope=s)
    assert_code(WorkErrorCode.INVALID_VERSION, lambda: create_work_item(
        work_id="wrk1:version:bad", work_type=WorkType.REVIEW, scope=s,
        context_ids=(CONTEXT_ID,), requester=human(), created_at=NOW,
        priority=WorkPriority.NORMAL, initial_status=WorkStatus.READY,
        classification=ResourceClassification.CONFIDENTIAL, visibility_policy_id=POLICY,
        visibility_policy_version=VERSION, authority_decision=auth,
        required_capabilities=("work.execute",), contract_version="marketmatch-work-orchestration-v2",
    ))


def test_assignment_is_explicit_authority_bound_and_acceptance_literal():
    item = assignment()
    assert item.assigner.actor_ref == "principal:assigner"
    assert item.assignee.executor_id == "principal:worker"
    assert item.authority_provenance.capability_code == "work.assign"
    for invalid in (True, "true", "ACCEPTED", 1):
        assert_code(WorkErrorCode.INVALID_ASSIGNMENT,
                    lambda invalid=invalid: assignment(status=invalid))


def test_assignment_authority_does_not_grant_execution_and_reassignment_preserves_history():
    first = assignment()
    second = assignment("asg1:worker:beta", executor=human_executor("principal:worker2"),
                        supersedes=first.assignment_id, assigned_at=NOW + timedelta(minutes=2))
    assert second.supersedes_assignment_id == first.assignment_id
    assert first.assignee.executor_id == "principal:worker"
    bad_executor = human_executor("principal:assigner")
    unauthorized_attempt = attempt(executor=bad_executor)
    assert_code(WorkErrorCode.EXECUTOR_MISMATCH,
                lambda: collection(attempts=[unauthorized_attempt], claims=[]))


def test_collection_detects_duplicate_active_assignments_wrong_scope_and_missing_work():
    first = assignment()
    second = assignment("asg1:worker:beta", executor=human_executor("principal:worker2"))
    assert_code(WorkErrorCode.DUPLICATE_ACTIVE_ASSIGNMENT,
                lambda: collection(assignments=[first, second], attempts=[], claims=[]))
    cross = assignment("asg1:cross:project", project="project:other")
    assert_code(WorkErrorCode.SCOPE_CONFLICT,
                lambda: collection(assignments=[cross], attempts=[], claims=[]))


def transition(transition_id, from_status, to_status, when, *, linked_decision=None, linked_approval=None):
    s = scope_for(transition_id)
    capability = f"work.transition.{to_status.value.lower()}"
    auth = decision(resource_type="work_transition", resource_id=transition_id,
                    capability=capability, actor="principal:operator", fields=COMMON_FIELDS,
                    scope=s)
    return create_work_transition(
        transition_id=transition_id, work_id="wrk1:review:alpha", from_status=from_status,
        to_status=to_status, actor=human("principal:operator"), occurred_at=when,
        scope=s, classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=POLICY, visibility_policy_version=VERSION,
        authority_decision=auth, linked_decision_id=linked_decision,
        linked_approval_id=linked_approval, reason_code="status.changed",
    )


def test_status_history_derives_current_state_and_invalid_transitions_reject():
    one = transition("trn1:assign:alpha", WorkStatus.READY, WorkStatus.ASSIGNED, NOW + timedelta(minutes=1))
    two = transition("trn1:accept:alpha", WorkStatus.ASSIGNED, WorkStatus.ACCEPTED, NOW + timedelta(minutes=2))
    three = transition("trn1:start:alpha", WorkStatus.ACCEPTED, WorkStatus.IN_PROGRESS, NOW + timedelta(minutes=3))
    validated = collection(transitions=[one, two, three], attempts=[], claims=[])
    assert dict(validated.materialized_statuses)["wrk1:review:alpha"] is WorkStatus.IN_PROGRESS
    assert_code(WorkErrorCode.INVALID_STATUS_TRANSITION,
                lambda: transition("trn1:bad:alpha", WorkStatus.READY, WorkStatus.COMPLETED, NOW))


def test_attempt_is_separate_retry_record_and_requires_accepted_matching_assignment():
    first = attempt()
    second = attempt("atm1:run:beta", sequence=2, status=AttemptStatus.FAILED,
                     started_at=NOW + timedelta(minutes=5))
    assert first.attempt_id != second.attempt_id and first.status is AttemptStatus.SUCCEEDED
    assert not hasattr(first, "stdout") and not hasattr(first, "stderr") and not hasattr(first, "exception")
    assert_code(WorkErrorCode.ASSIGNMENT_NOT_ACCEPTED,
                lambda: collection(assignments=[assignment(status=AssignmentStatus.ASSIGNED)],
                                   attempts=[first], claims=[]))
    assert_code(WorkErrorCode.EXECUTOR_MISMATCH,
                lambda: collection(assignments=[assignment(executor=human_executor("principal:other"))],
                                   attempts=[first], claims=[]))


@pytest.mark.parametrize("status", [AttemptStatus.FAILED, AttemptStatus.TIMED_OUT, AttemptStatus.CANCELLED])
def test_failed_timeout_and_cancelled_attempts_remain_distinct(status):
    item = attempt(status=status)
    assert item.status is status and item.ended_at is not None


def test_retry_policy_is_bounded_literal_and_deterministic():
    policy = RetryPolicy("retry:standard", 3, frozenset({"transient.failure"}))
    failed = attempt(status=AttemptStatus.FAILED)
    result = evaluate_retry(policy, [failed], reason_code="transient.failure", now=NOW,
                            due_at=NOW + timedelta(days=1), executor=human_executor())
    assert result.allowed and result.next_sequence == 2
    succeeded = attempt()
    assert evaluate_retry(policy, [succeeded], reason_code="transient.failure", now=NOW,
                          due_at=NOW + timedelta(days=1), executor=human_executor()).reason_code is RetryReason.PRIOR_ATTEMPT_SUCCEEDED
    for invalid in (True, -1, 1000):
        assert_code(WorkErrorCode.INVALID_RETRY_POLICY,
                    lambda invalid=invalid: RetryPolicy("retry:bad", invalid, frozenset({"x"})))


def test_retry_after_cancellation_deadline_or_executor_change_denies():
    policy = RetryPolicy("retry:standard", 3, frozenset({"transient.failure"}))
    cancelled = attempt(status=AttemptStatus.CANCELLED)
    assert evaluate_retry(policy, [cancelled], reason_code="transient.failure", now=NOW,
                          due_at=NOW + timedelta(days=1), executor=human_executor()).reason_code is RetryReason.PRIOR_ATTEMPT_CANCELLED
    failed = attempt(status=AttemptStatus.FAILED)
    assert evaluate_retry(policy, [failed], reason_code="transient.failure", now=NOW + timedelta(days=2),
                          due_at=NOW + timedelta(days=1), executor=human_executor()).reason_code is RetryReason.DEADLINE_EXCEEDED


def test_dependencies_are_directional_cycle_safe_and_do_not_complete_work():
    other = work("wrk1:review:beta")
    dep = WorkDependency("wrk1:review:alpha", other.work_id, DependencyType.REQUIRES,
                         NOW, "dependency.pending", POLICY, VERSION)
    validated = collection(work_items=[work(), other], dependencies=[dep])
    assert validated.unresolved_blockers == ("wrk1:review:alpha",)
    reverse = WorkDependency(other.work_id, "wrk1:review:alpha", DependencyType.REQUIRES,
                             NOW, "dependency.pending", POLICY, VERSION)
    assert_code(WorkErrorCode.DEPENDENCY_CYCLE,
                lambda: collection(work_items=[work(), other], dependencies=[dep, reverse]))
    assert_code(WorkErrorCode.INVALID_DEPENDENCY,
                lambda: WorkDependency("wrk1:review:alpha", "wrk1:review:alpha",
                                       DependencyType.REQUIRES, NOW, None, POLICY, VERSION))


def handoff(handoff_id="hnd1:transfer:alpha", *, source="asg1:worker:alpha",
            target="asg1:worker:beta", status=HandoffStatus.OFFERED, accepted_at=None,
            target_executor=None, project=PROJECT):
    target_executor = target_executor or human_executor("principal:worker2")
    s = scope_for(handoff_id, project=project)
    auth = decision(resource_type="work_handoff", resource_id=handoff_id,
                    capability="work.handoff", actor="principal:assigner",
                    fields=HANDOFF_FIELDS, scope=s)
    acceptance_auth = None
    if status is HandoffStatus.ACCEPTED:
        acceptance_auth = decision(
            resource_type="work_handoff", resource_id=handoff_id,
            capability="work.handoff.accept", actor=target_executor.executor_id,
            fields=HANDOFF_FIELDS, scope=s,
        )
    return create_handoff(
        handoff_id=handoff_id, work_id="wrk1:review:alpha",
        source_assignment_id=source, target_assignment_id=target,
        target_executor=target_executor,
        initiated_by=human("principal:assigner"), initiated_at=NOW + timedelta(minutes=3),
        accepted_at=accepted_at, status=status, reason_code="capacity.transfer",
        scope=s, classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=POLICY, visibility_policy_version=VERSION,
        authority_decision=auth, acceptance_authority_decision=acceptance_auth,
    )


def test_handoff_is_explicit_preserves_assignments_and_does_not_grant_execution():
    source = assignment()
    target = assignment("asg1:worker:beta", executor=human_executor("principal:worker2"),
                        status=AssignmentStatus.OFFERED)
    transfer = handoff()
    validated = collection(assignments=[source, target], attempts=[], claims=[], handoffs=[transfer])
    assert validated and source.status is AssignmentStatus.ACCEPTED
    assert transfer.source_assignment_id != transfer.target_assignment_id
    assert_code(WorkErrorCode.INVALID_HANDOFF, lambda: handoff(target=source.assignment_id))
    assert_code(WorkErrorCode.INVALID_HANDOFF,
                lambda: handoff(status=HandoffStatus.ACCEPTED, accepted_at=None))


def test_handoff_scope_cycles_and_duplicate_active_records_fail_closed():
    a = assignment(); b = assignment("asg1:worker:beta", executor=human_executor("principal:worker2"),
                                     status=AssignmentStatus.OFFERED)
    first = handoff(status=HandoffStatus.DECLINED)
    second = handoff("hnd1:transfer:beta", source=b.assignment_id,
                     target=a.assignment_id, target_executor=a.assignee,
                     status=HandoffStatus.DECLINED)
    assert_code(WorkErrorCode.HANDOFF_CYCLE,
                lambda: collection(assignments=[a, b], attempts=[], claims=[], handoffs=[first, second]))
    cross = handoff("hnd1:cross:project", project="project:other")
    assert_code(WorkErrorCode.SCOPE_CONFLICT,
                lambda: collection(assignments=[a, b], attempts=[], claims=[], handoffs=[cross]))


def escalation(escalation_id="esc1:blocker:alpha", *, status=EscalationStatus.OPEN,
               severity=EscalationSeverity.HIGH, target="capability:operations"):
    s = scope_for(escalation_id)
    auth = decision(resource_type="work_escalation", resource_id=escalation_id,
                    capability="work.escalate", actor="principal:worker",
                    fields=ESCALATION_FIELDS, scope=s)
    return create_escalation(
        escalation_id=escalation_id, work_id="wrk1:review:alpha",
        raised_by=human("principal:worker"), target_ref=target,
        target_capability="work.resolve", severity=severity,
        reason_code="dependency.blocked", raised_at=NOW + timedelta(minutes=5),
        response_due_at=NOW + timedelta(hours=1), status=status, scope=s,
        classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=POLICY, visibility_policy_version=VERSION,
        authority_decision=auth,
    )


def test_escalation_is_not_assignment_or_approval_and_duplicate_open_detected():
    item = escalation()
    assert item.target_capability == "work.resolve"
    assert not hasattr(item, "assignee") and not hasattr(item, "approval_id")
    other = escalation("esc1:blocker:beta")
    assert_code(WorkErrorCode.DUPLICATE_UNRESOLVED_ESCALATION,
                lambda: collection(escalations=[item, other]))
    assert_code(WorkErrorCode.INVALID_ESCALATION,
                lambda: escalation(severity="alto"))


def test_completion_claim_is_not_verification_and_failed_attempt_cannot_support_claim():
    item = claim()
    assert item.outcome is ClaimOutcome.CLAIMED
    assert not hasattr(item, "verified") and not hasattr(item, "approval_status")
    failed = attempt(status=AttemptStatus.FAILED)
    assert_code(WorkErrorCode.INVALID_COMPLETION_CLAIM,
                lambda: collection(attempts=[failed], claims=[item]))


def approval(approval_id="apr1:review:work", *, approver="principal:verifier",
             status=ApprovalStatus.APPROVED, policy=POLICY, expires_at=None,
             target_id=CONTEXT_ID):
    s = scope_for(approval_id)
    auth = decision(resource_type="truth_approval", resource_id=approval_id,
                    capability="approval.verify", actor=approver,
                    fields=frozenset({"approval_id"}), scope=s, policy=policy)
    return create_approval_record(
        approval_id=approval_id, approval_type=ApprovalType.REVIEW,
        target_type=TargetType.OPERATIONAL_CONTEXT, target_id=target_id,
        scope=s, approver=human(approver), status=status,
        recorded_at=NOW + timedelta(minutes=5), authority_decision=auth,
        expires_at=expires_at,
        authority_capability="approval.verify",
        classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=policy, visibility_policy_version=VERSION,
    )


def claim_with_approval(approval_id="apr1:review:work"):
    executor = human_executor()
    s = scope_for("clm1:result:approved")
    auth = decision(resource_type="work_completion_claim", resource_id="clm1:result:approved",
                    capability="work.completion.claim", actor=executor.executor_id,
                    fields=CLAIM_FIELDS, scope=s)
    return create_completion_claim(
        claim_id="clm1:result:approved", work_id="wrk1:review:alpha",
        assignment_id="asg1:worker:alpha", attempt_id="atm1:run:alpha",
        claimed_by=executor, claimed_at=NOW + timedelta(minutes=6),
        outcome=ClaimOutcome.CLAIMED, scope=s,
        classification=ResourceClassification.CONFIDENTIAL,
        visibility_policy_id=POLICY, visibility_policy_version=VERSION,
        authority_decision=auth, result_evidence_ids=(EVIDENCE_ID,),
        assertion_approval_ids=(approval_id,),
    )


def test_completion_verification_requires_independent_valid_approval():
    criteria = (
        CompletionCriterion(CriterionType.RESULT_REFERENCE_REQUIRED),
        CompletionCriterion(CriterionType.APPROVAL_REQUIRED, capability_code="approval.verify"),
        CompletionCriterion(CriterionType.INDEPENDENT_VERIFIER_REQUIRED,
                            capability_code="approval.verify"),
    )
    w = work(criteria=criteria); c = claim_with_approval(); a = attempt(); asg = assignment()
    approved = approval()
    result = evaluate_completion(w, c, a, asg, approvals=[approved], decisions=[], events=[],
                                 completed_dependency_ids=frozenset())
    assert result.status is EvaluationStatus.SATISFIED
    for status in (ApprovalStatus.REJECTED, ApprovalStatus.WITHDRAWN,
                   ApprovalStatus.EXPIRED, ApprovalStatus.INVALIDATED):
        invalid = approval(status=status)
        result = evaluate_completion(w, c, a, asg, approvals=[invalid], decisions=[], events=[],
                                     completed_dependency_ids=frozenset())
        assert result.status is EvaluationStatus.UNSATISFIED


def test_executor_cannot_self_verify_without_explicit_policy():
    criteria = (CompletionCriterion(CriterionType.INDEPENDENT_VERIFIER_REQUIRED,
                                    capability_code="approval.verify"),)
    w = work(criteria=criteria); c = claim_with_approval(); a = attempt(); asg = assignment()
    own = approval(approver="principal:worker")
    denied = evaluate_completion(w, c, a, asg, approvals=[own], decisions=[], events=[],
                                 completed_dependency_ids=frozenset())
    assert denied.status is EvaluationStatus.UNSATISFIED
    allowed = evaluate_completion(w, c, a, asg, approvals=[own], decisions=[], events=[],
                                  completed_dependency_ids=frozenset(), allow_self_verification=True)
    assert allowed.status is EvaluationStatus.SATISFIED


def test_empty_completion_criteria_never_silently_complete():
    w = work(criteria=())
    result = evaluate_completion(w, claim(), attempt(), assignment(), approvals=[], decisions=[],
                                 events=[], completed_dependency_ids=frozenset())
    assert result.status is EvaluationStatus.UNSATISFIED
    assert result.reason_codes == ("NO_COMPLETION_CRITERIA",)


def test_cancellation_requires_decision_reference_and_preserves_attempt_history():
    assert_code(WorkErrorCode.INVALID_TRANSITION,
                lambda: transition("trn1:cancel:alpha", WorkStatus.IN_PROGRESS,
                                   WorkStatus.CANCELLED, NOW + timedelta(minutes=5)))
    assert attempt().status is AttemptStatus.SUCCEEDED
    assert_code(WorkErrorCode.INVALID_STATUS_TRANSITION,
                lambda: transition("trn1:cancel:completed", WorkStatus.COMPLETED,
                                   WorkStatus.CANCELLED, NOW + timedelta(minutes=5),
                                   linked_decision="dec1:cancel:alpha"))


def test_projection_is_flat_new_authority_bound_and_hides_protected_fields():
    item = work()
    visible = frozenset({"work_id", "work_type", "priority"})
    auth = decision(resource_type="work_item", resource_id=item.work_id,
                    capability="work.view", actor="principal:viewer", fields=WORK_FIELDS,
                    visible=visible, scope=item.scope)
    projected = project_work_item(item, auth)
    assert projected == {"work_id": item.work_id, "work_type": "REVIEW", "priority": "NORMAL"}
    assert projected is not item and "requester_ref" not in projected and "input_evidence_ids" not in projected
    assert all(value is None or type(value) in (str, int, float, bool) for value in projected.values())


def test_all_projectors_hide_executor_requester_reason_and_results():
    a = assignment(); at = attempt(); h = handoff(); e = escalation(); c = claim()
    records = (
        (a, project_assignment, "work_assignment", "assignment_id", ASSIGN_FIELDS),
        (at, project_execution_attempt, "work_attempt", "attempt_id", ATTEMPT_FIELDS),
        (h, project_handoff, "work_handoff", "handoff_id", HANDOFF_FIELDS),
        (e, project_escalation, "work_escalation", "escalation_id", ESCALATION_FIELDS),
        (c, project_completion_claim, "work_completion_claim", "claim_id", CLAIM_FIELDS),
    )
    for record, projector, resource_type, id_field, fields in records:
        record_id = getattr(record, id_field)
        auth = decision(resource_type=resource_type, resource_id=record_id,
                        capability="work.view", actor="principal:viewer", fields=fields,
                        visible=frozenset({id_field}), scope=record.scope)
        assert projector(record, auth) == {id_field: record_id}


class HostileMapping(dict):
    def items(self):
        raise RuntimeError("token:secret cost:999 address:private")


def test_hostile_mapping_and_computed_status_cannot_leak_or_forge():
    item = work()
    auth = decision(resource_type="work_item", resource_id=item.work_id,
                    capability="work.view", actor="principal:viewer", fields=WORK_FIELDS,
                    scope=item.scope)
    assert_code(WorkErrorCode.INVALID_PROJECTION, lambda: project_work_item(HostileMapping(), auth))
    mapping = project_work_item(item, auth)
    mapping["current_status"] = "COMPLETED"
    mapping["criteria_count"] = 999
    assert_code(WorkErrorCode.INVALID_PROJECTION, lambda: project_work_item(mapping, auth))


def test_mutated_record_and_mutated_authority_decision_fail_closed():
    item = work(); object.__setattr__(item, "priority", WorkPriority.CRITICAL)
    auth = decision(resource_type="work_item", resource_id=item.work_id,
                    capability="work.view", actor="principal:viewer", fields=WORK_FIELDS,
                    scope=item.scope)
    assert_code(WorkErrorCode.INVALID_PROJECTION, lambda: project_work_item(item, auth))
    clean = work(); auth2 = decision(resource_type="work_item", resource_id=clean.work_id,
                    capability="work.view", actor="principal:viewer", fields=WORK_FIELDS,
                    scope=clean.scope)
    object.__setattr__(auth2, "allowed", False)
    assert_code(WorkErrorCode.INVALID_PROJECTION, lambda: project_work_item(clean, auth2))


def test_safe_audit_contains_only_bound_codes_and_never_protected_content():
    item = attempt()
    auth = decision(resource_type="work_attempt", resource_id=item.attempt_id,
                    capability="work.execute", actor="principal:worker", fields=ATTEMPT_FIELDS,
                    visible=frozenset({"attempt_id", "executor_kind"}), scope=item.scope)
    audit = build_safe_work_audit(
        audit_id="waud1:attempt:alpha", action_code="work.execute",
        record_type="work_attempt", record_id=item.attempt_id, decision=auth,
        timestamp=NOW, executor=item.executor, correlation_id="correlation:alpha",
    )
    text = repr(audit)
    assert audit.executor_kind == "HUMAN" and audit.outcome_code == "ALLOWED"
    for forbidden in ("fictional", "prompt", "model response", "stdout", "stderr",
                      "cookie", "session", "token", "cost", "address"):
        assert forbidden not in text.lower()
    rejected = build_work_rejection_audit(
        audit_id="waud1:reject:alpha", action_code="work.reject",
        record_type="malformed_record", record_id="invalid:work:alpha",
        reason_code=WorkErrorCode.INVALID_WORK_ITEM, policy_id=POLICY,
        policy_version=VERSION, timestamp=NOW,
    )
    assert rejected.reason_code == "INVALID_WORK_ITEM"


@pytest.mark.parametrize("unsafe", ["line\nbreak", "token:secret", "cost:100:usd",
                                    "user@example.com", "a" * 200])
def test_audit_rejects_injection_sensitive_and_excessive_values(unsafe):
    assert_code(WorkErrorCode.INVALID_AUDIT, lambda: build_work_rejection_audit(
        audit_id="waud1:reject:alpha", action_code="work.reject",
        record_type="malformed_record", record_id=unsafe,
        reason_code=WorkErrorCode.INVALID_WORK_ITEM, policy_id=POLICY,
        policy_version=VERSION, timestamp=NOW,
    ))


def test_derived_workload_views_authorize_every_source_and_order_deterministically():
    first = work(); second = work("wrk1:review:beta", due_at=NOW + timedelta(minutes=1))
    pairs = []
    for item in (second, first):
        auth = decision(resource_type="work_item", resource_id=item.work_id,
                        capability="work.view", actor="principal:viewer", fields=WORK_FIELDS,
                        visible=frozenset({"work_id"}), scope=item.scope)
        pairs.append((item, auth))
    view = create_derived_workload_view(
        pairs, view_kind=WorkloadViewKind.OVERDUE,
        materialized_statuses={first.work_id: WorkStatus.READY, second.work_id: WorkStatus.READY},
        evaluated_at=NOW + timedelta(minutes=2),
    )
    assert view.work_ids == (second.work_id,) and view.derived
    assert not hasattr(view, "decision_id") and not hasattr(view, "event_id")


def test_derived_view_denied_hidden_or_cross_scope_source_blocks_instead_of_omitting():
    first = work()
    denied = decision(resource_type="work_item", resource_id=first.work_id,
                      capability="work.view", actor="principal:viewer", fields=WORK_FIELDS,
                      scope=first.scope, grant=False)
    assert_code(WorkErrorCode.SOURCE_NOT_AUTHORIZED, lambda: create_derived_workload_view(
        [(first, denied)], view_kind=WorkloadViewKind.ACTIVE,
        materialized_statuses={first.work_id: WorkStatus.READY}, evaluated_at=NOW,
    ))
    other = work("wrk1:review:other", org="org:other")
    pairs = [(item, decision(resource_type="work_item", resource_id=item.work_id,
             capability="work.view", actor="principal:viewer", fields=WORK_FIELDS,
             visible=frozenset({"work_id"}), scope=item.scope)) for item in (first, other)]
    assert_code(WorkErrorCode.SOURCE_SCOPE_CONFLICT, lambda: create_derived_workload_view(
        pairs, view_kind=WorkloadViewKind.ACTIVE,
        materialized_statuses={first.work_id: WorkStatus.READY, other.work_id: WorkStatus.READY},
        evaluated_at=NOW,
    ))


def test_locale_and_timezone_do_not_change_identity_authority_or_status():
    item = work()
    outputs = []
    for locale, tz in (("es", "America/Santo_Domingo"), ("en", "UTC"),
                       ("zh-Hans", "Asia/Shanghai")):
        auth = decision(resource_type="work_item", resource_id=item.work_id,
                        capability="work.view", actor="principal:viewer", fields=WORK_FIELDS,
                        visible=frozenset({"work_id", "initial_status"}), scope=item.scope,
                        locale=locale, tz=tz)
        outputs.append(project_work_item(item, auth))
    assert outputs[0] == outputs[1] == outputs[2]


def test_current_task_adapter_fails_honestly_without_fabricating_scope_or_completion():
    assert_code(WorkErrorCode.UNSUPPORTED_ADAPTER,
                lambda: adapt_current_task({"id": "1", "status": "completed", "result": "raw"}))


def test_module_is_pure_reuses_all_committed_kernels_and_has_no_runtime_engine():
    path = Path(__file__).parents[1] / "src" / "marketmatch_work_orchestration.py"
    source = path.read_text(encoding="utf-8"); tree = ast.parse(source)
    imports = {node.module for node in ast.walk(tree)
               if isinstance(node, ast.ImportFrom) and node.module}
    assert {"src.marketmatch_authority", "src.marketmatch_evidence",
            "src.marketmatch_operational_context",
            "src.marketmatch_truth_accountability"} <= imports
    forbidden = ("requests", "httpx", "openai", "langgraph", "deerflow", "crewai",
                 "sqlalchemy", "sqlite3", "celery", "redis", "rq")
    assert not any(f"import {name}" in source or f"from {name}" in source for name in forbidden)
    for token in ("localStorage", "sessionStorage", "open(", ".write(", ".execute(",
                  "SessionLocal", "create_task(", "subprocess."):
        assert token not in source


def test_no_production_route_import_migration_or_runtime_data_change_contract():
    root = Path(__file__).parents[1]

    def imports_work_kernel(source: str) -> bool:
        tree = ast.parse(source)
        return any(
            (
                isinstance(node, ast.ImportFrom)
                and (
                    node.module in {
                        "src.marketmatch_work_orchestration",
                        "marketmatch_work_orchestration",
                    }
                    or (
                        node.module == "src"
                        and any(
                            alias.name == "marketmatch_work_orchestration"
                            for alias in node.names
                        )
                    )
                )
            )
            or (
                isinstance(node, ast.Import)
                and any(
                    alias.name in {
                        "src.marketmatch_work_orchestration",
                        "marketmatch_work_orchestration",
                    }
                    for alias in node.names
                )
            )
            for node in ast.walk(tree)
        )

    tenancy_kernel = root / "src" / "marketmatch_tenancy.py"
    assert imports_work_kernel(tenancy_kernel.read_text(encoding="utf-8"))
    route_modules = [root / "app.py", *(root / "routes").rglob("*.py")]
    assert all(not imports_work_kernel(path.read_text(encoding="utf-8"))
               for path in route_modules)
    assert imports_work_kernel(
        "from src.marketmatch_work_orchestration import WorkItem\n"
    )
    assert imports_work_kernel("from src import marketmatch_work_orchestration\n")
    assert not any("work_orchestration" in p.name for p in (root / "alembic" / "versions").glob("*.py"))


def test_adr_covers_boundaries_codes_examples_and_deferrals():
    adr = (Path(__file__).parents[1] / "docs" / "adr" /
           "0005-marketmatch-work-orchestration-kernel-v1.md").read_text(encoding="utf-8")
    required = ("Work Item versus Event", "Assignment versus authority", "executor",
                "attempt", "retry", "Handoff", "dependency", "escalation",
                "Completion Claim", "verified completion", "cancellation request",
                "Evidence", "Operational Context", "Truth & Accountability",
                "immutable history", "safe projection", "safe audit",
                "derived workload", "locale", "timezone", "current implementation",
                "compatibility adapters", "persistence", "runtime-engine",
                "AI Control Plane", "route", "Stable code glossary",
                "reason-code glossary", "fictional")
    assert all(term.lower() in adr.lower() for term in required)
    assert "GCO" not in adr


def test_adversarial_source_work_self_reference_and_cycles_fail_closed():
    self_ref = work(source_work_ids=("wrk1:review:alpha",))
    assert_code(WorkErrorCode.INVALID_WORK_ITEM,
                lambda: collection(work_items=[self_ref], assignments=[], attempts=[], claims=[]))
    first = work(source_work_ids=("wrk1:review:beta",))
    second = work("wrk1:review:beta", source_work_ids=(first.work_id,))
    assert_code(WorkErrorCode.DEPENDENCY_CYCLE,
                lambda: collection(work_items=[first, second], assignments=[], attempts=[], claims=[]))


def test_adversarial_duplicate_completion_criterion_cannot_reuse_one_source():
    duplicate = (
        CompletionCriterion(CriterionType.EVIDENCE_REQUIRED),
        CompletionCriterion(CriterionType.EVIDENCE_REQUIRED),
    )
    assert_code(WorkErrorCode.INVALID_CRITERIA, lambda: work(criteria=duplicate))


def test_adversarial_expired_approval_timestamp_does_not_verify_completion():
    criteria = (CompletionCriterion(CriterionType.APPROVAL_REQUIRED,
                                    capability_code="approval.verify"),)
    w = work(criteria=criteria); c = claim_with_approval(); a = attempt(); asg = assignment()
    expired = approval(expires_at=NOW + timedelta(minutes=6))
    result = evaluate_completion(
        w, c, a, asg, approvals=[expired], decisions=[], events=[],
        completed_dependency_ids=frozenset(), evaluated_at=NOW + timedelta(hours=1),
    )
    assert result.status is EvaluationStatus.UNSATISFIED


def test_adversarial_completed_history_preserves_prior_attempt_and_requires_criteria():
    approved = approval()
    transitions = [
        transition("trn1:assign:done", WorkStatus.READY, WorkStatus.ASSIGNED, NOW + timedelta(seconds=30)),
        transition("trn1:accept:done", WorkStatus.ASSIGNED, WorkStatus.ACCEPTED, NOW + timedelta(minutes=1)),
        transition("trn1:start:done", WorkStatus.ACCEPTED, WorkStatus.IN_PROGRESS, NOW + timedelta(minutes=2)),
        transition("trn1:claim:done", WorkStatus.IN_PROGRESS, WorkStatus.COMPLETION_CLAIMED, NOW + timedelta(minutes=5)),
        transition("trn1:complete:done", WorkStatus.COMPLETION_CLAIMED, WorkStatus.COMPLETED,
                   NOW + timedelta(minutes=7), linked_approval=approved.approval_id),
    ]
    validated = collection(transitions=transitions, approvals=[approved])
    assert dict(validated.materialized_statuses)["wrk1:review:alpha"] is WorkStatus.COMPLETED
    no_criteria = work(criteria=())
    assert_code(WorkErrorCode.CRITERIA_UNSATISFIED,
                lambda: collection(work_items=[no_criteria], transitions=transitions,
                                   approvals=[approved]))


def test_adversarial_safe_audit_cannot_be_constructed_with_protected_values():
    assert_code(WorkErrorCode.INVALID_AUDIT, lambda: SafeWorkAudit(
        "waud1:bad:alpha", "work.execute", "work_attempt", "atm1:run:alpha",
        "HUMAN", "ALLOWED", "token:secret", POLICY, VERSION,
        "atm1:run:alpha", "2026-07-20T18:00:00Z", None, WORK_CONTRACT_VERSION,
    ))


def test_adversarial_derived_view_rejects_malformed_or_cross_scope_assignments():
    item = work()
    auth = decision(resource_type="work_item", resource_id=item.work_id,
                    capability="work.view", actor="principal:viewer", fields=WORK_FIELDS,
                    visible=frozenset({"work_id"}), scope=item.scope)
    malformed = assignment(); object.__setattr__(malformed, "status", AssignmentStatus.INVALIDATED)
    malformed_auth = decision(
        resource_type="work_assignment", resource_id=malformed.assignment_id,
        capability="work.view", actor="principal:viewer", fields=ASSIGN_FIELDS,
        visible=frozenset({"assignment_id", "work_id", "status", "executor_id"}),
        scope=malformed.scope,
    )
    assert_code(WorkErrorCode.SOURCE_NOT_AUTHORIZED,
                lambda: create_derived_workload_view(
                    [(item, auth)], view_kind=WorkloadViewKind.ASSIGNED_TO,
                    materialized_statuses={item.work_id: WorkStatus.ASSIGNED},
                    evaluated_at=NOW, assignments=[(malformed, malformed_auth)],
                    assigned_executor_id="principal:worker",
                ))
    cross = assignment("asg1:cross:view", project="project:other")
    cross_auth = decision(
        resource_type="work_assignment", resource_id=cross.assignment_id,
        capability="work.view", actor="principal:viewer", fields=ASSIGN_FIELDS,
        visible=frozenset({"assignment_id", "work_id", "status", "executor_id"}),
        scope=cross.scope,
    )
    assert_code(WorkErrorCode.SOURCE_SCOPE_CONFLICT,
                lambda: create_derived_workload_view(
                    [(item, auth)], view_kind=WorkloadViewKind.ASSIGNED_TO,
                    materialized_statuses={item.work_id: WorkStatus.ASSIGNED},
                    evaluated_at=NOW, assignments=[(cross, cross_auth)],
                    assigned_executor_id="principal:worker",
                ))
    visible_assignment = assignment()
    denied_auth = decision(
        resource_type="work_assignment", resource_id=visible_assignment.assignment_id,
        capability="work.view", actor="principal:viewer", fields=ASSIGN_FIELDS,
        scope=visible_assignment.scope, grant=False,
    )
    assert_code(WorkErrorCode.SOURCE_NOT_AUTHORIZED,
                lambda: create_derived_workload_view(
                    [(item, auth)], view_kind=WorkloadViewKind.ASSIGNED_TO,
                    materialized_statuses={item.work_id: WorkStatus.ASSIGNED},
                    evaluated_at=NOW, assignments=[(visible_assignment, denied_auth)],
                    assigned_executor_id="principal:worker",
                ))


def test_adversarial_acceptance_requires_explicit_response_timestamp():
    assert_code(WorkErrorCode.INVALID_ASSIGNMENT,
                lambda: assignment(status=AssignmentStatus.ACCEPTED, include_response=False))


def test_adversarial_retry_while_prior_attempt_active_denies():
    policy = RetryPolicy("retry:standard", 3, frozenset({"transient.failure"}))
    active = attempt(status=AttemptStatus.STARTED)
    result = evaluate_retry(policy, [active], reason_code="transient.failure", now=NOW,
                            due_at=NOW + timedelta(days=1), executor=human_executor())
    assert result.reason_code is RetryReason.PRIOR_ATTEMPT_ACTIVE


def test_adversarial_claim_cannot_fabricate_result_not_produced_by_attempt():
    successful_without_result = attempt(include_result=False)
    assert_code(WorkErrorCode.INVALID_COMPLETION_CLAIM,
                lambda: collection(attempts=[successful_without_result], claims=[claim()]))


def test_adversarial_assignment_acceptance_is_assignee_authority_bound():
    accepted = assignment()
    assert accepted.response_authority_provenance.principal_ref == "principal:worker"
    assert accepted.response_authority_provenance.capability_code == "work.assignment.accepted"
    assert_code(
        WorkErrorCode.INVALID_AUTHORITY_DECISION,
        lambda: assignment(response_actor="principal:assigner"),
    )


def test_adversarial_superseded_assignment_cannot_execute():
    first = assignment()
    second = assignment(
        "asg1:worker:beta", executor=human_executor("principal:worker2"),
        supersedes=first.assignment_id, assigned_at=NOW + timedelta(minutes=2),
    )
    assert_code(
        WorkErrorCode.ASSIGNMENT_NOT_ACCEPTED,
        lambda: collection(assignments=[first, second], attempts=[attempt()], claims=[]),
    )


def test_adversarial_handoff_acceptance_requires_target_executor_authority():
    accepted = handoff(
        status=HandoffStatus.ACCEPTED,
        accepted_at=NOW + timedelta(minutes=4),
    )
    assert accepted.acceptance_authority_provenance.principal_ref == "principal:worker2"
    source = assignment()
    wrong_target = assignment(
        "asg1:worker:beta", executor=human_executor("principal:worker3"),
        status=AssignmentStatus.OFFERED,
    )
    assert_code(
        WorkErrorCode.EXECUTOR_MISMATCH,
        lambda: collection(
            assignments=[source, wrong_target], attempts=[], claims=[], handoffs=[accepted],
        ),
    )


def test_adversarial_unrelated_approval_cannot_verify_completion():
    criteria = (CompletionCriterion(CriterionType.APPROVAL_REQUIRED,
                                    capability_code="approval.verify"),)
    result = evaluate_completion(
        work(criteria=criteria), claim_with_approval(), attempt(), assignment(),
        approvals=[approval(target_id="ctx1:project:unrelated")], decisions=[], events=[],
        completed_dependency_ids=frozenset(),
    )
    assert result.status is EvaluationStatus.UNSATISFIED


def test_adversarial_nested_authority_provenance_mutation_is_detected():
    item = work()
    object.__setattr__(item.authority_provenance, "capability_code", "work.verify")
    assert_code(
        WorkErrorCode.INVALID_WORK_ITEM,
        lambda: collection(work_items=[item], attempts=[], claims=[]),
    )


def test_adversarial_rejection_audit_rejects_unknown_record_type():
    assert_code(
        WorkErrorCode.INVALID_AUDIT,
        lambda: build_work_rejection_audit(
            audit_id="waud1:reject:unknown", action_code="work.reject",
            record_type="unknown_record", record_id="invalid:work:alpha",
            reason_code=WorkErrorCode.INVALID_WORK_ITEM, policy_id=POLICY,
            policy_version=VERSION, timestamp=NOW,
        ),
    )
