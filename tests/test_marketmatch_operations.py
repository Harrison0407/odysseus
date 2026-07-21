from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.marketmatch_authority import (
    AuthorityScope, AuthorizationRequest, PartyKind, PartyReference,
    PrincipalContext, ResourceClassification, ResourceContext,
    ScopedCapabilityGrant, VisibilityMode, VisibilityPolicy,
    evaluate_authorization,
)
from src.marketmatch_operations import (
    OPERATIONS_CONTRACT_VERSION, AbandonmentEvaluation, BackoffMethod,
    BackupLifecycleStatus, CancellationPolicy, CompatibilityStatus,
    ConfigurationScopeType, ConfigurationValueType, Criticality,
    DeliveryOutcome, EncryptionStatus, FeatureEvaluationMode, FeatureStatus,
    HealthCheckType, HealthObservationOutcome, IntegrityStatus, JobAttemptOutcome,
    JobDefinitionStatus, JobLifecycleStatus, JitterPolicy, NotificationChannel,
    NotificationPriority, OperationalSignalType, OperationsContractError,
    OperationsErrorCode, OperationsScope, OperationsViewKind, RestoreLifecycleStatus,
    RestoreValidationOutcome, RetryPolicy, SafeMeasurement, SecretStatus,
    ServiceHealthStatus, VerificationOutcome, adapt_current_task_run,
    build_safe_operations_audit, create_backup_policy, create_backup_record,
    create_backup_verification, create_configuration_definition,
    create_configuration_snapshot, create_delivery_attempt,
    create_derived_operations_view, create_feature_flag,
    create_health_check_definition, create_health_observation,
    create_job_attempt, create_job_definition, create_job_request, create_job_run,
    create_notification_intent, create_operational_signal, create_restore_attempt,
    create_restore_plan, create_rollback_point, create_secret_reference,
    derive_service_health, evaluate_abandonment, evaluate_configuration,
    evaluate_dead_letter, evaluate_feature_flag, evaluate_idempotency,
    evaluate_retry, evaluate_rollback, operations_metadata, project_operations_record,
    secret_reference_is_active, transition_job_run, validate_backup_record_id,
    validate_configuration_definition_id, validate_delivery_attempts,
    validate_feature_flag_id, validate_job_attempts, validate_job_id,
    validate_operations_id, validate_secret_reference_id,
)
from src.marketmatch_tenancy import TenantContext
import src.marketmatch_tenancy as tenancy_kernel
from src.marketmatch_truth_accountability import (
    ActorKind, ActorReference, ApprovalStatus, ApprovalType, TargetType,
    create_approval_record,
)


NOW = datetime(2026, 7, 21, 16, tzinfo=timezone.utc)
TENANT = "tenant1:fictional:alpha"
ORG = "org1:fictional:alpha"
PRODUCT = "product:fictional"
WORKSPACE = "workspace:fictional"
PROJECT = "project:fictional"
OWNER = "party:fictional:owner"
POLICY = "operations.visibility"
VERSION = "v1"
ACTOR = "principal:fictional:operator"
CLASSIFICATION = ResourceClassification.CONFIDENTIAL


def assert_code(code: OperationsErrorCode, call) -> None:
    with pytest.raises(OperationsContractError) as caught:
        call()
    assert caught.value.code is code
    assert str(caught.value) == code.value


def context(*, tenant=TENANT, org=ORG, project=PROJECT) -> TenantContext:
    scope = AuthorityScope(org, PRODUCT, WORKSPACE, project,
                           "ctx1:project:fictional", OWNER, False)
    return tenancy_kernel._new_tenant_context(
        tenant_id=tenant, organization_id=org,
        product_context_id="ctx1:product:fictional",
        workspace_context_id="ctx1:workspace:fictional",
        project_context_id="ctx1:project:fictional", scope=scope,
        classification=CLASSIFICATION, policy_id=POLICY, policy_version=VERSION,
    )


def ops_scope(*, tenant=TENANT, org=ORG, project=PROJECT, service="worker"):
    return OperationsScope(context(tenant=tenant, org=org, project=project),
                           "test", service)


def global_scope(service="control"):
    return OperationsScope(None, "test", service, True)


def decision(resource_type, resource_id, capability, *, scope=None, actor=ACTOR,
             fields=frozenset(), allowed=True, policy=POLICY, version=VERSION):
    actual = scope or ops_scope()
    auth_scope = (AuthorityScope(resource_id=resource_id, global_scope=True)
                  if actual.global_scope else
                  AuthorityScope(actual.tenant_context.organization_id,
                      actual.tenant_context.scope.product_id,
                      actual.tenant_context.scope.workspace_id,
                      actual.tenant_context.scope.project_id, resource_id,
                      actual.tenant_context.scope.owner_party_id, False))
    principal = PrincipalContext(actor, PartyReference(actor, PartyKind.PERSON),
        capability_grants=(ScopedCapabilityGrant(capability, auth_scope, "grant:fictional"),)
        if allowed else ())
    vis = VisibilityPolicy(policy, version, VisibilityMode.CONTROLLED_CONFIDENTIALITY,
                           frozenset({capability}), fields,
                           frozenset(ResourceClassification),
                           frozenset({capability}) if actual.global_scope else frozenset())
    return evaluate_authorization(AuthorizationRequest(principal, capability,
        ResourceContext(resource_type, resource_id, auth_scope, CLASSIFICATION, fields), fields), vis)


def config_definition(*, value_type=ConfigurationValueType.BOOLEAN,
                      scope_type=ConfigurationScopeType.TENANT,
                      allowed_values=()):
    return create_configuration_definition(
        definition_id="cfgdef1:fictional:enabled", key="feature.enabled",
        value_type=value_type, scope_type=scope_type, required=True,
        has_default=False, validation_code="bounded.literal",
        allowed_values=allowed_values, effective_at=NOW - timedelta(days=1),
        policy_id=POLICY, policy_version=VERSION, classification=CLASSIFICATION)


def feature(*, scope=None, mode=FeatureEvaluationMode.ON, percent=0, allowlist=(),
            effective_at=NOW - timedelta(hours=1), flag_id="flag1:fictional:alpha"):
    actual = scope or ops_scope()
    return create_feature_flag(feature_flag_id=flag_id, flag_key="feature.alpha",
        scope=actual, status=FeatureStatus.ACTIVE, mode=mode,
        default_enabled=False, percentage_basis_points=percent, allowlist=allowlist,
        effective_at=effective_at, actor_ref=ACTOR,
        authority_decision=decision("operations_feature_flag", flag_id,
                                    "operations.feature.admin", scope=actual),
        policy_id=POLICY, policy_version=VERSION)


def retry_policy(maximum_attempts=3):
    return RetryPolicy(maximum_attempts, timedelta(seconds=2), timedelta(seconds=5),
                       BackoffMethod.EXPONENTIAL, frozenset({"transient.failure", "worker.timeout"}),
                       timedelta(minutes=2), JitterPolicy.NONE)


def job_definition(*, required_scope_type=ConfigurationScopeType.TENANT):
    return create_job_definition(job_id="job1:fictional:analysis", job_type="analysis.run",
        version="v1", required_scope_type=required_scope_type,
        timeout=timedelta(minutes=5), retry_policy=retry_policy(),
        idempotency_principal_bound=True, cancellation_policy=CancellationPolicy.COOPERATIVE,
        status=JobDefinitionStatus.ACTIVE, policy_id=POLICY, policy_version=VERSION)


def job_request(*, request_id="jreq1:fictional:alpha", scope=None,
                idem="request:fictional:alpha", input_ref="input:fictional:alpha"):
    actual = scope or ops_scope(); definition = job_definition()
    return create_job_request(request_id=request_id, definition=definition, scope=actual,
        requesting_principal_ref=ACTOR, safe_input_ref=input_ref,
        requested_at=NOW, idempotency_key=idem,
        authority_decision=decision("operations_job_request", request_id,
                                    "operations.job.request", scope=actual))


def accepted_idempotency(request, history=()):
    return evaluate_idempotency(request, history, job_definition())


def backup_policy(*, scope=None):
    actual = scope or ops_scope(); rid = "bpol1:fictional:database"
    return create_backup_policy(backup_policy_id=rid, scope=actual,
        resource_type="application.database", trigger_code="manual",
        retention=timedelta(days=30), encryption_required=True,
        verification_required=True, effective_at=NOW - timedelta(days=1),
        actor_ref=ACTOR, authority_decision=decision("operations_backup_policy", rid,
            "operations.backup_policy.admin", scope=actual),
        policy_id=POLICY, policy_version=VERSION)


def completed_backup(*, scope=None):
    return create_backup_record(backup_record_id="brec1:fictional:database:alpha",
        policy=backup_policy(scope=scope), resource_reference="resource:database:primary",
        created_at=NOW, completed_at=NOW + timedelta(minutes=1),
        artifact_reference="artifact:backup:alpha",
        content_digest="sha256:" + "a" * 64, size_bytes=1024,
        encryption_status=EncryptionStatus.ENCRYPTED,
        status=BackupLifecycleStatus.COMPLETED, producer_ref="service:backup",
        classification=CLASSIFICATION)


def verification(*, backup=None, outcomes=(VerificationOutcome.PASSED,) * 3):
    record = backup or completed_backup(); rid = "bver1:fictional:database:alpha"
    return create_backup_verification(verification_id=rid, backup=record,
        verifier_ref=ACTOR, verified_at=NOW + timedelta(minutes=2),
        verification_method_code="archive.integrity",
        integrity_outcome=outcomes[0], compatibility_outcome=outcomes[1],
        restorable_outcome=outcomes[2], reason_code="verification.completed",
        authority_decision=decision("operations_backup_verification", rid,
                                    "operations.backup.verify", scope=record.scope))


def truth_approval(*, scope, protected_record_id, capability, approval_id):
    return create_approval_record(approval_id=approval_id,
        approval_type=ApprovalType.AUTHORIZATION,
        target_type=TargetType.OPERATIONAL_CONTEXT,
        target_id=scope.tenant_context.project_context_id,
        scope=AuthorityScope(scope.tenant_context.organization_id,
            scope.tenant_context.scope.product_id, scope.tenant_context.scope.workspace_id,
            scope.tenant_context.scope.project_id, approval_id,
            scope.tenant_context.scope.owner_party_id, False),
        approver=ActorReference(ACTOR, ActorKind.HUMAN),
        status=ApprovalStatus.APPROVED, recorded_at=NOW,
        effective_at=NOW + timedelta(minutes=1),
        expires_at=NOW + timedelta(hours=1), classification=CLASSIFICATION,
        visibility_policy_id=POLICY, visibility_policy_version=VERSION,
        conditions_code=protected_record_id, reason_code="operations.approved",
        authority_decision=decision("truth_approval", approval_id, capability,
                                    scope=scope), authority_capability=capability)


@pytest.mark.parametrize("validator,value", [
    (validate_configuration_definition_id, "job1:fictional:alpha"),
    (validate_feature_flag_id, "flag1:12345"),
    (validate_secret_reference_id, "secref1:user@example.com"),
    (validate_job_id, "job1:../../password"),
    (validate_backup_record_id, "brec1:" + "a" * 64),
    (validate_job_id, "job1:example.com"),
    (validate_job_id, "job1:token:abc"),
    (validate_job_id, "job1:bad\ncontrol"),
    (validate_job_id, "job1:" + "a" * 200),
])
def test_identifiers_are_type_distinct_bounded_and_safe(validator, value):
    assert_code(OperationsErrorCode.INVALID_IDENTIFIER, lambda: validator(value))


def test_identifier_validation_is_locale_independent(monkeypatch):
    monkeypatch.setenv("LC_ALL", "tr_TR.UTF-8")
    assert validate_operations_id("job", "job1:fictional:alpha") == "job1:fictional:alpha"


@pytest.mark.parametrize("kind,value", [
    (ConfigurationValueType.BOOLEAN, True),
    (ConfigurationValueType.INTEGER, 12),
    (ConfigurationValueType.DECIMAL, Decimal("1.25")),
    (ConfigurationValueType.STRING, "safe value"),
    (ConfigurationValueType.ENUM, "mode.alpha"),
    (ConfigurationValueType.DURATION, timedelta(seconds=3)),
    (ConfigurationValueType.BYTE_SIZE, 4096),
])
def test_configuration_preserves_exact_literal_types(kind, value):
    definition = config_definition(value_type=kind,
        allowed_values=("mode.alpha",) if kind is ConfigurationValueType.ENUM else ())
    item = create_configuration_snapshot(snapshot_id="cfgsnap1:fictional:alpha",
        definition=definition, scope=ops_scope(), value=value, effective_at=NOW,
        source_code="test.fixture")
    assert item.value is value or item.value == value
    assert evaluate_configuration(definition, [item], scope=ops_scope(), as_of=NOW) is item


@pytest.mark.parametrize("value", [True, "1", Decimal("NaN"), {"all": "environment"}])
def test_configuration_rejects_wrong_or_unbounded_values(value):
    definition = config_definition(value_type=ConfigurationValueType.INTEGER)
    assert_code(OperationsErrorCode.INVALID_CONFIGURATION,
        lambda: create_configuration_snapshot(snapshot_id="cfgsnap1:fictional:alpha",
            definition=definition, scope=ops_scope(), value=value,
            effective_at=NOW, source_code="test.fixture"))


def test_secret_configuration_accepts_reference_only_and_never_value():
    definition = config_definition(value_type=ConfigurationValueType.SECRET_REFERENCE)
    scope = ops_scope()
    ref = create_secret_reference(secret_reference_id="secref1:fictional:provider",
        provider_code="local.encrypted", purpose_code="provider.authentication",
        scope=scope, rotation_reference="rotation:v1", status=SecretStatus.ACTIVE,
        created_at=NOW - timedelta(days=1), policy_id=POLICY,
        policy_version=VERSION, classification=ResourceClassification.RESTRICTED)
    snap = create_configuration_snapshot(snapshot_id="cfgsnap1:fictional:secretref",
        definition=definition, scope=scope, value=ref, effective_at=NOW,
        source_code="test.fixture")
    assert snap.value is ref and "secret_reference_id" in repr(ref)
    assert "value" not in {f.name for f in fields(ref)}
    assert_code(OperationsErrorCode.SECRET_VALUE_REJECTED,
        lambda: create_configuration_snapshot(snapshot_id="cfgsnap1:fictional:raw",
            definition=definition, scope=scope, value="sk-live-value",
            effective_at=NOW, source_code="test.fixture"))
    assert secret_reference_is_active(ref, as_of=NOW, scope=scope)
    assert_code(OperationsErrorCode.INVALID_RECORD,
        lambda: create_secret_reference(secret_reference_id="secref1:fictional:rotation",
            provider_code="local.encrypted", purpose_code="provider.authentication",
            scope=scope, rotation_reference="sk-" + "fictional" * 3,
            status=SecretStatus.ACTIVE, created_at=NOW, policy_id=POLICY,
            policy_version=VERSION, classification=ResourceClassification.RESTRICTED))
    visible = frozenset(operations_metadata(snap))
    auth = decision("operations_configuration", snap.snapshot_id,
                    "operations.configuration.view", scope=scope, fields=visible)
    projected = project_operations_record(snap, auth)
    assert "value" not in projected and ref.secret_reference_id not in projected.values()


def test_non_secret_string_configuration_still_rejects_credential_shaped_value():
    definition = config_definition(value_type=ConfigurationValueType.STRING)
    assert_code(OperationsErrorCode.SECRET_VALUE_REJECTED,
        lambda: create_configuration_snapshot(snapshot_id="cfgsnap1:fictional:unsafe",
            definition=definition, scope=ops_scope(), value="sk-" + "fictional" * 3,
            effective_at=NOW, source_code="test.fixture"))


def test_configuration_conflicts_and_effective_windows_fail_closed():
    definition = config_definition(); scope = ops_scope()
    first = create_configuration_snapshot(snapshot_id="cfgsnap1:fictional:first",
        definition=definition, scope=scope, value=True,
        effective_at=NOW - timedelta(minutes=1), source_code="test.fixture")
    second = create_configuration_snapshot(snapshot_id="cfgsnap1:fictional:second",
        definition=definition, scope=scope, value=False,
        effective_at=NOW, source_code="test.fixture")
    assert_code(OperationsErrorCode.CONFIGURATION_CONFLICT,
                lambda: evaluate_configuration(definition, [first, second], scope=scope, as_of=NOW))
    future = create_configuration_snapshot(snapshot_id="cfgsnap1:fictional:future",
        definition=definition, scope=scope, value=True,
        effective_at=NOW + timedelta(days=1), source_code="test.fixture")
    assert evaluate_configuration(definition, [future], scope=scope, as_of=NOW) is None


def test_feature_modes_are_deterministic_and_never_grant_authority():
    scope = ops_scope()
    for mode, expected, allow, percent in (
        (FeatureEvaluationMode.OFF, False, (), 0),
        (FeatureEvaluationMode.ON, True, (), 0),
        (FeatureEvaluationMode.EXACT_SCOPE, True, (), 0),
        (FeatureEvaluationMode.ALLOWLIST, True, (ACTOR,), 0),
    ):
        result = evaluate_feature_flag(evaluation_id="feval1:fictional:" + mode.name.lower(),
            flag_key="feature.alpha", flags=[feature(scope=scope, mode=mode, allowlist=allow)],
            scope=scope, as_of=NOW, subject_ref=ACTOR)
        assert result.enabled is expected
        assert result.authority_granted is False and result.entitlement_granted is False
    percentage = feature(scope=scope, mode=FeatureEvaluationMode.PERCENTAGE, percent=5000)
    one = evaluate_feature_flag(evaluation_id="feval1:fictional:percentage:one",
        flag_key="feature.alpha", flags=[percentage], scope=scope, as_of=NOW, subject_ref=ACTOR)
    two = evaluate_feature_flag(evaluation_id="feval1:fictional:percentage:two",
        flag_key="feature.alpha", flags=[percentage], scope=scope, as_of=NOW, subject_ref=ACTOR)
    assert one.enabled == two.enabled and one.reason_code == two.reason_code


def test_feature_wrong_tenant_future_and_conflict_do_not_enable():
    alpha = ops_scope(); beta = ops_scope(tenant="tenant1:fictional:beta", org="org1:fictional:beta")
    result = evaluate_feature_flag(evaluation_id="feval1:fictional:wrongtenant",
        flag_key="feature.alpha", flags=[feature(scope=alpha)], scope=beta, as_of=NOW)
    assert result.enabled is False
    future = feature(scope=alpha, effective_at=NOW + timedelta(days=1))
    assert not evaluate_feature_flag(evaluation_id="feval1:fictional:future",
        flag_key="feature.alpha", flags=[future], scope=alpha, as_of=NOW).enabled
    two = feature(scope=alpha, flag_id="flag1:fictional:beta")
    assert_code(OperationsErrorCode.FEATURE_CONFLICT,
        lambda: evaluate_feature_flag(evaluation_id="feval1:fictional:conflict",
            flag_key="feature.alpha", flags=[feature(scope=alpha), two], scope=alpha, as_of=NOW))


def test_factory_sealed_feature_evaluation_and_unknown_version():
    from src.marketmatch_operations import FeatureEvaluation
    assert_code(OperationsErrorCode.INVALID_RECORD, lambda: FeatureEvaluation())
    assert_code(OperationsErrorCode.INVALID_VERSION,
        lambda: create_configuration_definition(definition_id="cfgdef1:fictional:version",
            key="version.test", value_type=ConfigurationValueType.BOOLEAN,
            scope_type=ConfigurationScopeType.TENANT, required=True, has_default=False,
            validation_code="literal", effective_at=NOW, policy_id=POLICY,
            policy_version=VERSION, classification=CLASSIFICATION,
            contract_version="marketmatch-operations-reliability-v2"))


def test_job_lifecycle_retry_timeout_cancellation_and_terminal_protection():
    request = job_request(); idem = accepted_idempotency(request)
    run = create_job_run(run_id="jrun1:fictional:alpha", request=request,
                         idempotency_evaluation=idem)
    accepted = transition_job_run(run, to_status=JobLifecycleStatus.ACCEPTED, at=NOW)
    running = transition_job_run(accepted, to_status=JobLifecycleStatus.RUNNING, at=NOW + timedelta(seconds=1))
    timed = transition_job_run(running, to_status=JobLifecycleStatus.TIMED_OUT, at=NOW + timedelta(seconds=2))
    assert timed.status is JobLifecycleStatus.TIMED_OUT
    assert_code(OperationsErrorCode.INVALID_TRANSITION,
                lambda: transition_job_run(timed, to_status=JobLifecycleStatus.SUCCEEDED,
                                           at=NOW + timedelta(seconds=3)))
    running2 = transition_job_run(create_job_run(run_id="jrun1:fictional:beta", request=request,
        idempotency_evaluation=idem, status=JobLifecycleStatus.ACCEPTED),
        to_status=JobLifecycleStatus.RUNNING, at=NOW)
    cancelled = transition_job_run(running2, to_status=JobLifecycleStatus.CANCELLED,
        at=NOW + timedelta(seconds=1), cancellation_reference="cancel:user")
    assert cancelled.status is JobLifecycleStatus.CANCELLED


def test_retry_is_bounded_and_unknown_or_cancelled_reasons_deny():
    policy = retry_policy()
    allowed = evaluate_retry(policy, attempt_ordinal=1, outcome=JobAttemptOutcome.FAILED,
        reason_code="transient.failure", finished_at=NOW + timedelta(seconds=2),
        first_started_at=NOW, run_status=JobLifecycleStatus.FAILED)
    assert allowed.allowed and allowed.delay == timedelta(seconds=2)
    second = evaluate_retry(policy, attempt_ordinal=2, outcome=JobAttemptOutcome.TIMED_OUT,
        reason_code="worker.timeout", finished_at=NOW + timedelta(seconds=4),
        first_started_at=NOW, run_status=JobLifecycleStatus.TIMED_OUT)
    assert second.delay == timedelta(seconds=4)
    for outcome, reason in ((JobAttemptOutcome.CANCELLED, "transient.failure"),
                            (JobAttemptOutcome.FAILED, "unknown.failure")):
        assert not evaluate_retry(policy, attempt_ordinal=1, outcome=outcome,
            reason_code=reason, finished_at=NOW, first_started_at=NOW,
            run_status=JobLifecycleStatus.CANCELLED if outcome is JobAttemptOutcome.CANCELLED
            else JobLifecycleStatus.FAILED).allowed
    assert not evaluate_retry(policy, attempt_ordinal=3, outcome=JobAttemptOutcome.FAILED,
        reason_code="transient.failure", finished_at=NOW, first_started_at=NOW,
        run_status=JobLifecycleStatus.FAILED).allowed


def test_retry_deadline_and_backoff_cannot_overflow():
    policy = RetryPolicy(64, timedelta(days=1), timedelta(days=2), BackoffMethod.EXPONENTIAL,
                         frozenset({"transient.failure"}), timedelta(days=10))
    result = evaluate_retry(policy, attempt_ordinal=63, outcome=JobAttemptOutcome.FAILED,
        reason_code="transient.failure", finished_at=NOW, first_started_at=NOW - timedelta(days=9),
        run_status=JobLifecycleStatus.FAILED, deadline=NOW + timedelta(hours=1))
    assert not result.allowed
    overflow = evaluate_retry(policy, attempt_ordinal=63,
        outcome=JobAttemptOutcome.FAILED, reason_code="transient.failure",
        finished_at=datetime.max.replace(tzinfo=timezone.utc),
        first_started_at=datetime.max.replace(tzinfo=timezone.utc) - timedelta(days=1),
        run_status=JobLifecycleStatus.FAILED)
    assert not overflow.allowed and overflow.reason_code == "deadline_overflow"


def test_idempotency_suppresses_duplicate_and_isolates_tenants():
    first = job_request(); duplicate = job_request(request_id="jreq1:fictional:duplicate")
    result = evaluate_idempotency(duplicate, [first], job_definition())
    assert result.duplicate and not result.accepted
    beta = job_request(request_id="jreq1:fictional:beta",
        scope=ops_scope(tenant="tenant1:fictional:beta", org="org1:fictional:beta"))
    assert evaluate_idempotency(beta, [first], job_definition()).accepted
    collision = job_request(request_id="jreq1:fictional:collision", input_ref="input:fictional:other")
    assert evaluate_idempotency(collision, [first], job_definition()).conflicting


def test_run_acceptance_is_bound_to_idempotency_and_blocks_duplicate_execution():
    request = job_request(); accepted = accepted_idempotency(request)
    first = create_job_run(run_id="jrun1:fictional:first", request=request,
                           idempotency_evaluation=accepted)
    assert_code(OperationsErrorCode.IDEMPOTENCY_CONFLICT,
        lambda: create_job_run(run_id="jrun1:fictional:second", request=request,
            idempotency_evaluation=accepted, existing_runs=[first]))
    unrelated = job_request(request_id="jreq1:fictional:other", idem="request:fictional:other")
    assert_code(OperationsErrorCode.INVALID_TRANSITION,
        lambda: create_job_run(run_id="jrun1:fictional:unrelated", request=request,
            idempotency_evaluation=accepted_idempotency(unrelated)))


def test_failed_run_cannot_retry_without_matching_positive_evaluation():
    request = job_request(); run = create_job_run(run_id="jrun1:fictional:retry",
        request=request, idempotency_evaluation=accepted_idempotency(request),
        status=JobLifecycleStatus.ACCEPTED)
    running = transition_job_run(run, to_status=JobLifecycleStatus.RUNNING, at=NOW)
    failed = transition_job_run(running, to_status=JobLifecycleStatus.FAILED,
                                at=NOW + timedelta(seconds=1))
    assert_code(OperationsErrorCode.RETRY_NOT_ALLOWED,
        lambda: transition_job_run(failed, to_status=JobLifecycleStatus.ACCEPTED,
                                   at=NOW + timedelta(seconds=2)))
    retry = evaluate_retry(retry_policy(), attempt_ordinal=1,
        outcome=JobAttemptOutcome.FAILED, reason_code="transient.failure",
        finished_at=NOW + timedelta(seconds=1), first_started_at=NOW,
        run_status=JobLifecycleStatus.FAILED)
    again = transition_job_run(failed, to_status=JobLifecycleStatus.ACCEPTED,
                               at=NOW + timedelta(seconds=3), retry_evaluation=retry)
    assert again.status is JobLifecycleStatus.ACCEPTED


def test_attempts_enforce_ordinal_history_and_usage_deduplication():
    request = job_request(); run = create_job_run(run_id="jrun1:fictional:attempts",
        request=request, idempotency_evaluation=accepted_idempotency(request),
        status=JobLifecycleStatus.ACCEPTED)
    run = transition_job_run(run, to_status=JobLifecycleStatus.RUNNING, at=NOW)
    retry = evaluate_retry(retry_policy(), attempt_ordinal=1, outcome=JobAttemptOutcome.FAILED,
        reason_code="transient.failure", finished_at=NOW + timedelta(seconds=1),
        first_started_at=NOW, run_status=JobLifecycleStatus.FAILED)
    first = create_job_attempt(attempt_id="jatt1:fictional:first", run=run,
        attempt_ordinal=1, started_at=NOW, finished_at=NOW + timedelta(seconds=1),
        outcome=JobAttemptOutcome.FAILED, error_code="transient.failure",
        retry_evaluation=retry, usage_event_id="usage1:fictional:first")
    third = create_job_attempt(attempt_id="jatt1:fictional:third", run=run,
        attempt_ordinal=3, started_at=NOW + timedelta(seconds=2), outcome=JobAttemptOutcome.RUNNING)
    assert_code(OperationsErrorCode.INVALID_RECORD, lambda: validate_job_attempts(run, [first, third]))
    assert evaluate_dead_letter(transition_job_run(run, to_status=JobLifecycleStatus.FAILED,
        at=NOW + timedelta(seconds=2)), [first], retry_policy()).dead_letter is False


def test_abandonment_requires_an_expired_known_lease():
    request = job_request()
    run = create_job_run(run_id="jrun1:fictional:lease", request=request,
                         idempotency_evaluation=accepted_idempotency(request),
                         status=JobLifecycleStatus.ACCEPTED)
    unknown = evaluate_abandonment(run, last_heartbeat_at=None, lease_expires_at=None, as_of=NOW)
    assert not unknown.abandoned and unknown.reason_code == "lease_unknown"
    assert evaluate_abandonment(run, last_heartbeat_at=NOW - timedelta(minutes=2),
        lease_expires_at=NOW - timedelta(minutes=1), as_of=NOW).abandoned


def test_notification_intent_and_delivery_are_reference_only_and_do_not_send():
    scope = ops_scope(); rid = "nint1:fictional:alpha"
    intent = create_notification_intent(intent_id=rid, scope=scope,
        notification_type="job.completed", recipient_reference="party:fictional:recipient",
        template_reference="template:job:v1", source_record_type="job_run",
        source_record_id="jrun1:fictional:alpha", requested_at=NOW,
        priority=NotificationPriority.NORMAL, actor_ref=ACTOR,
        authority_decision=decision("operations_notification_intent", rid,
                                    "operations.notification.request", scope=scope),
        policy_id=POLICY, policy_version=VERSION, classification=CLASSIFICATION)
    attempt = create_delivery_attempt(delivery_attempt_id="datt1:fictional:first",
        intent=intent, channel=NotificationChannel.EMAIL, attempt_ordinal=1,
        attempted_at=NOW + timedelta(seconds=1), outcome=DeliveryOutcome.ACKNOWLEDGED,
        acknowledgement_code="provider.accepted", provider_reference="provider:fictional")
    assert validate_delivery_attempts(intent, [attempt]) == (attempt,)
    assert "body" not in {f.name for f in fields(intent)}
    assert_code(OperationsErrorCode.INVALID_RECORD,
        lambda: create_notification_intent(intent_id="nint1:fictional:unsafe", scope=scope,
            notification_type="job.completed", recipient_reference="user@example.com",
            template_reference="template:v1", source_record_type="job_run",
            source_record_id="jrun1:fictional:alpha", requested_at=NOW,
            priority=NotificationPriority.NORMAL, actor_ref=ACTOR,
            authority_decision=decision("operations_notification_intent", "nint1:fictional:unsafe",
                "operations.notification.request", scope=scope), policy_id=POLICY,
            policy_version=VERSION, classification=CLASSIFICATION))


def test_health_freshness_missing_critical_and_noncritical_degradation():
    scope = ops_scope(service="api")
    critical = create_health_check_definition(check_id="hchk1:fictional:database",
        scope=scope, dependency_code="database", check_type=HealthCheckType.DEPENDENCY,
        expected_interval=timedelta(seconds=10), stale_after=timedelta(seconds=30),
        criticality=Criticality.CRITICAL, policy_id=POLICY, policy_version=VERSION)
    missing = derive_service_health([critical], [], scope=scope, service_code="api", as_of=NOW)
    assert missing.status is ServiceHealthStatus.UNAVAILABLE and not missing.authority_granted
    healthy = create_health_observation(signal_id="osig1:fictional:database", definition=critical,
        observed_at=NOW, outcome=HealthObservationOutcome.HEALTHY,
        reason_code="probe.succeeded", latency_ms=Decimal("2.5"))
    assert derive_service_health([critical], [healthy], scope=scope,
                                 service_code="api", as_of=NOW).status is ServiceHealthStatus.HEALTHY
    stale = derive_service_health([critical], [healthy], scope=scope,
                                  service_code="api", as_of=NOW + timedelta(minutes=1))
    assert stale.status is ServiceHealthStatus.UNKNOWN
    future = create_health_observation(signal_id="osig1:fictional:future", definition=critical,
        observed_at=NOW + timedelta(minutes=1), outcome=HealthObservationOutcome.HEALTHY,
        reason_code="probe.succeeded")
    assert derive_service_health([critical], [future], scope=scope,
        service_code="api", as_of=NOW).status is ServiceHealthStatus.UNAVAILABLE


def test_metrics_reject_nan_infinity_and_signal_is_not_truth_event():
    for value in (Decimal("NaN"), Decimal("Infinity")):
        assert_code(OperationsErrorCode.INVALID_SIGNAL,
                    lambda value=value: SafeMeasurement("latency", value, "milliseconds"))
    signal = create_operational_signal(signal_id="osig1:fictional:job",
        scope=ops_scope(), signal_type=OperationalSignalType.JOB_COMPLETED,
        source_record_type="job_run", source_record_id="jrun1:fictional:alpha",
        occurred_at=NOW, reason_code="job.completed",
        measurements=(SafeMeasurement("duration", Decimal("12"), "milliseconds"),))
    assert signal.official_event is False


def test_completed_backup_is_not_verified_and_verification_is_factory_sealed():
    backup = completed_backup()
    assert backup.status is BackupLifecycleStatus.COMPLETED and backup.verified is False
    verified = verification(backup=backup)
    assert verified.verified
    from src.marketmatch_operations import BackupVerification
    assert_code(OperationsErrorCode.INVALID_RECORD, lambda: BackupVerification())
    failed = verification(backup=backup, outcomes=(VerificationOutcome.PASSED,
        VerificationOutcome.UNKNOWN, VerificationOutcome.PASSED))
    assert failed.verified is False
    object.__setattr__(failed, "verified", True)
    rid = "rplan1:fictional:forged"
    assert_code(OperationsErrorCode.BACKUP_NOT_VERIFIED,
        lambda: create_restore_plan(restore_plan_id=rid, backup=backup,
            verification=failed, target_scope=backup.scope,
            compatibility_policy_code="database.v1",
            expected_rollback_point_id="rbp1:fictional:before:restore",
            requested_actor_ref=ACTOR, created_at=NOW + timedelta(minutes=3),
            expires_at=NOW + timedelta(hours=1),
            authority_decision=decision("operations_restore_plan", rid,
                "operations.restore.request", scope=backup.scope), approval_required=False))


def test_backup_chronology_encryption_and_paths_fail_closed():
    policy = backup_policy()
    assert_code(OperationsErrorCode.INVALID_BACKUP,
        lambda: create_backup_record(backup_record_id="brec1:fictional:unsafe", policy=policy,
            resource_reference="resource:db", created_at=NOW,
            completed_at=NOW - timedelta(seconds=1), artifact_reference="artifact:safe",
            encryption_status=EncryptionStatus.ENCRYPTED,
            status=BackupLifecycleStatus.COMPLETED, producer_ref="service:backup",
            classification=CLASSIFICATION))
    assert_code(OperationsErrorCode.INVALID_RECORD,
        lambda: create_backup_record(backup_record_id="brec1:fictional:artifact", policy=policy,
            resource_reference="resource:db", created_at=NOW,
            completed_at=NOW + timedelta(seconds=1),
            artifact_reference="/Users/alice/private.tar.gz",
            encryption_status=EncryptionStatus.ENCRYPTED,
            status=BackupLifecycleStatus.COMPLETED, producer_ref="service:backup",
            classification=CLASSIFICATION))


def test_restore_requires_verified_compatible_same_tenant_backup_and_is_inert():
    backup = completed_backup(); verified = verification(backup=backup); scope = backup.scope
    rid = "rplan1:fictional:database:alpha"
    plan = create_restore_plan(restore_plan_id=rid, backup=backup, verification=verified,
        target_scope=scope, compatibility_policy_code="database.v1",
        expected_rollback_point_id="rbp1:fictional:before:restore",
        requested_actor_ref=ACTOR, created_at=NOW + timedelta(minutes=3),
        expires_at=NOW + timedelta(hours=1),
        authority_decision=decision("operations_restore_plan", rid,
                                    "operations.restore.request", scope=scope),
        approval_required=False)
    attempt = create_restore_attempt(restore_attempt_id="ratt1:fictional:first", plan=plan,
        started_at=NOW + timedelta(minutes=4), finished_at=NOW + timedelta(minutes=5),
        lifecycle_status=RestoreLifecycleStatus.COMPLETED,
        validation_outcome=RestoreValidationOutcome.NOT_PERFORMED,
        reason_code="restore.execution.completed")
    assert attempt.validation_outcome is RestoreValidationOutcome.NOT_PERFORMED
    failed_verification = verification(backup=backup, outcomes=(VerificationOutcome.PASSED,
        VerificationOutcome.UNKNOWN, VerificationOutcome.PASSED))
    assert_code(OperationsErrorCode.BACKUP_NOT_VERIFIED,
        lambda: create_restore_plan(restore_plan_id="rplan1:fictional:unverified",
            backup=backup, verification=failed_verification, target_scope=scope,
            compatibility_policy_code="database.v1",
            expected_rollback_point_id="rbp1:fictional:before:restore",
            requested_actor_ref=ACTOR, created_at=NOW + timedelta(minutes=3),
            expires_at=NOW + timedelta(hours=1),
            authority_decision=decision("operations_restore_plan", "rplan1:fictional:unverified",
                "operations.restore.request", scope=scope), approval_required=False))


def test_restore_approval_and_cross_tenant_checks_are_distinct():
    backup = completed_backup(); verified = verification(backup=backup); scope = backup.scope
    rid = "rplan1:fictional:approval"
    assert_code(OperationsErrorCode.INVALID_APPROVAL,
        lambda: create_restore_plan(restore_plan_id=rid, backup=backup, verification=verified,
            target_scope=scope, compatibility_policy_code="database.v1",
            expected_rollback_point_id="rbp1:fictional:before:restore",
            requested_actor_ref=ACTOR, created_at=NOW + timedelta(minutes=3),
            expires_at=NOW + timedelta(hours=1),
            authority_decision=decision("operations_restore_plan", rid,
                "operations.restore.request", scope=scope), approval_required=True))
    approved_id = "rplan1:fictional:approved"
    approval = truth_approval(scope=scope, protected_record_id=approved_id,
        capability="approval.restore", approval_id="apr1:fictional:restore")
    approved = create_restore_plan(restore_plan_id=approved_id, backup=backup,
        verification=verified, target_scope=scope,
        compatibility_policy_code="database.v1",
        expected_rollback_point_id="rbp1:fictional:before:restore",
        requested_actor_ref=ACTOR, created_at=NOW + timedelta(minutes=3),
        expires_at=NOW + timedelta(hours=1),
        authority_decision=decision("operations_restore_plan", approved_id,
                                    "operations.restore.request", scope=scope),
        approval_required=True, approval=approval)
    assert approved.approval_id == approval.approval_id
    beta = ops_scope(tenant="tenant1:fictional:beta", org="org1:fictional:beta")
    assert_code(OperationsErrorCode.TENANT_CONFLICT,
        lambda: create_restore_plan(restore_plan_id="rplan1:fictional:beta",
            backup=backup, verification=verified, target_scope=beta,
            compatibility_policy_code="database.v1",
            expected_rollback_point_id="rbp1:fictional:before:restore",
            requested_actor_ref=ACTOR, created_at=NOW + timedelta(minutes=3),
            expires_at=NOW + timedelta(hours=1),
            authority_decision=decision("operations_restore_plan", "rplan1:fictional:beta",
                "operations.restore.request", scope=beta), approval_required=False))


def test_rollback_requires_integrity_compatibility_and_authority():
    backup = completed_backup(); backup_check = verification(backup=backup)
    scope = backup.scope; rid = "rbp1:fictional:database:alpha"
    assert_code(OperationsErrorCode.INVALID_ROLLBACK,
        lambda: create_rollback_point(rollback_point_id="rbp1:fictional:unverified",
            scope=scope, resource_type="application.database",
            resource_reference="resource:database:primary", source_backup=backup,
            created_at=NOW + timedelta(minutes=3), integrity_status=IntegrityStatus.VALID,
            compatibility_status=CompatibilityStatus.COMPATIBLE, actor_ref=ACTOR,
            authority_decision=decision("operations_rollback_point",
                "rbp1:fictional:unverified", "operations.rollback.propose", scope=scope),
            policy_id=POLICY, policy_version=VERSION))
    point = create_rollback_point(rollback_point_id=rid, scope=scope,
        resource_type="application.database", resource_reference="resource:database:primary",
        source_backup=backup, backup_verification=backup_check,
        created_at=NOW + timedelta(minutes=3),
        integrity_status=IntegrityStatus.VALID,
        compatibility_status=CompatibilityStatus.COMPATIBLE, actor_ref=ACTOR,
        authority_decision=decision("operations_rollback_point", rid,
                                    "operations.rollback.propose", scope=scope),
        policy_id=POLICY, policy_version=VERSION)
    evaluation = evaluate_rollback(point, as_of=NOW, approval_required=False)
    assert evaluation.may_propose and evaluation.executed is False
    unknown_id = "rbp1:fictional:database:unknown"
    unknown = create_rollback_point(rollback_point_id=unknown_id, scope=scope,
        resource_type="application.database", resource_reference="resource:database:primary",
        source_backup=backup, backup_verification=backup_check,
        created_at=NOW + timedelta(minutes=3),
        integrity_status=IntegrityStatus.UNKNOWN,
        compatibility_status=CompatibilityStatus.UNKNOWN, actor_ref=ACTOR,
        authority_decision=decision("operations_rollback_point", unknown_id,
                                    "operations.rollback.propose", scope=scope),
        policy_id=POLICY, policy_version=VERSION)
    assert not evaluate_rollback(unknown, as_of=NOW, approval_required=False).may_propose


def test_projection_requires_exact_auth_and_omits_secret_backup_path_and_digest():
    backup = completed_backup(); fields_set = frozenset(operations_metadata(backup))
    auth = decision("operations_backup_record", backup.backup_record_id,
                    "operations.backup_metadata.view", scope=backup.scope, fields=fields_set)
    projected = project_operations_record(backup, auth)
    assert "artifact_reference" not in projected and "content_digest" not in projected
    assert projected["verified"] is False
    wrong = decision("operations_backup_record", backup.backup_record_id,
                     "operations.backup_metadata.view",
                     scope=ops_scope(tenant="tenant1:fictional:beta", org="org1:fictional:beta"),
                     fields=fields_set)
    assert_code(OperationsErrorCode.INVALID_AUTHORITY,
                lambda: project_operations_record(backup, wrong))
    assert_code(OperationsErrorCode.INVALID_PROJECTION,
                lambda: project_operations_record({"content_digest": "stolen"}, auth))


def test_secret_projection_contains_reference_metadata_only():
    scope = ops_scope(); ref = create_secret_reference(
        secret_reference_id="secref1:fictional:projection", provider_code="local.encrypted",
        purpose_code="provider.authentication", scope=scope, rotation_reference="rotation:v1",
        status=SecretStatus.ACTIVE, created_at=NOW, policy_id=POLICY,
        policy_version=VERSION, classification=ResourceClassification.RESTRICTED)
    visible = frozenset(operations_metadata(ref))
    auth = decision("operations_secret_reference", ref.secret_reference_id,
                    "operations.secret_reference.view", scope=scope, fields=visible)
    projected = project_operations_record(ref, auth)
    assert set(projected).isdisjoint({"value", "password", "token", "credential"})


def test_safe_audit_is_closed_scalar_and_value_free():
    item = build_safe_operations_audit(audit_id="oaudit1:fictional:job",
        action_code="job.completed", record_type="job_run",
        record_id="jrun1:fictional:alpha", scope=ops_scope(), outcome_code="completed",
        reason_code="job.completed", policy_id=POLICY, policy_version=VERSION,
        timestamp=NOW, correlation_id="correlation:fictional")
    assert item.tenant_id == TENANT
    assert all(type(getattr(item, f.name)) in (str, type(None))
               for f in fields(item) if not f.name.startswith("_"))
    assert_code(OperationsErrorCode.INVALID_AUDIT,
        lambda: build_safe_operations_audit(audit_id="oaudit1:fictional:unsafe",
            action_code="job.completed", record_type="job_run",
            record_id="jrun1:fictional:alpha", scope=ops_scope(), outcome_code="completed",
            reason_code="exception:password=leak", policy_id=POLICY,
            policy_version=VERSION, timestamp=NOW))


def test_derived_view_denied_or_cross_tenant_source_blocks_entire_view():
    scope = ops_scope(); flag = feature(scope=scope)
    evaluation = evaluate_feature_flag(evaluation_id="feval1:fictional:view",
        flag_key="feature.alpha", flags=[flag], scope=scope, as_of=NOW)
    visible = frozenset(operations_metadata(evaluation))
    allowed = decision("operations_feature_evaluation", evaluation.evaluation_id,
        "operations.feature.view", scope=scope, fields=visible)
    view = create_derived_operations_view([(evaluation, allowed)],
        view_kind=OperationsViewKind.EFFECTIVE_FEATURE_FLAGS, scope=scope)
    assert view.record_ids == (evaluation.evaluation_id,) and not view.official_event
    denied = decision("operations_feature_evaluation", evaluation.evaluation_id,
        "operations.feature.view", scope=scope, fields=visible, allowed=False)
    assert_code(OperationsErrorCode.SOURCE_NOT_AUTHORIZED,
        lambda: create_derived_operations_view([(evaluation, denied)],
            view_kind=OperationsViewKind.EFFECTIVE_FEATURE_FLAGS, scope=scope))
    assert_code(OperationsErrorCode.SOURCE_NOT_AUTHORIZED,
        lambda: create_derived_operations_view([(evaluation, allowed)],
            view_kind=OperationsViewKind.ACTIVE_JOBS, scope=scope))
    beta = ops_scope(tenant="tenant1:fictional:beta", org="org1:fictional:beta")
    beta_flag = feature(scope=beta, flag_id="flag1:fictional:beta")
    beta_eval = evaluate_feature_flag(evaluation_id="feval1:fictional:beta",
        flag_key="feature.alpha", flags=[beta_flag], scope=beta, as_of=NOW)
    beta_fields = frozenset(operations_metadata(beta_eval))
    beta_auth = decision("operations_feature_evaluation", beta_eval.evaluation_id,
        "operations.feature.view", scope=beta, fields=beta_fields)
    assert_code(OperationsErrorCode.TENANT_CONFLICT,
        lambda: create_derived_operations_view([(evaluation, allowed), (beta_eval, beta_auth)],
            view_kind=OperationsViewKind.EFFECTIVE_FEATURE_FLAGS, scope=scope))


def test_health_view_refuses_stale_derived_result():
    scope = ops_scope(service="worker")
    check = create_health_check_definition(check_id="hchk1:fictional:worker",
        scope=scope, dependency_code="worker", check_type=HealthCheckType.LIVENESS,
        expected_interval=timedelta(seconds=10), stale_after=timedelta(seconds=20),
        criticality=Criticality.CRITICAL, policy_id=POLICY, policy_version=VERSION)
    observation = create_health_observation(signal_id="osig1:fictional:worker",
        definition=check, observed_at=NOW, outcome=HealthObservationOutcome.HEALTHY,
        reason_code="probe.succeeded")
    health = derive_service_health([check], [observation], scope=scope,
                                   service_code="worker", as_of=NOW)
    visible = frozenset(operations_metadata(health))
    auth = decision("operations_health", health.service_code, "operations.health.view",
                    scope=scope, fields=visible)
    assert_code(OperationsErrorCode.SOURCE_NOT_AUTHORIZED,
        lambda: create_derived_operations_view([(health, auth)],
            view_kind=OperationsViewKind.SERVICE_HEALTH, scope=scope,
            as_of=NOW + timedelta(seconds=21)))


def test_task_run_adapter_never_accepts_payload_or_fabricates_context_idempotency():
    request = job_request()
    run = adapt_current_task_run({"status": "success", "started_at": NOW,
        "finished_at": NOW + timedelta(seconds=1)}, run_id="jrun1:fictional:legacy",
        request=request, idempotency_evaluation=accepted_idempotency(request),
        status_as_of=NOW + timedelta(seconds=2))
    assert run.status is JobLifecycleStatus.SUCCEEDED and run.request_id == request.request_id
    assert_code(OperationsErrorCode.UNSUPPORTED_ADAPTER,
        lambda: adapt_current_task_run({"status": "success", "started_at": NOW,
            "finished_at": NOW, "result": "protected body"},
            run_id="jrun1:fictional:unsafe", request=request,
            idempotency_evaluation=accepted_idempotency(request), status_as_of=NOW))
    assert_code(OperationsErrorCode.UNSUPPORTED_ADAPTER,
        lambda: adapt_current_task_run({"status": True, "started_at": NOW},
            run_id="jrun1:fictional:boolean", request=request,
            idempotency_evaluation=accepted_idempotency(request), status_as_of=NOW))


def test_records_are_immutable_and_mutation_is_detected():
    flag = feature()
    with pytest.raises(FrozenInstanceError):
        flag.status = FeatureStatus.RETIRED
    object.__setattr__(flag, "status", FeatureStatus.RETIRED)
    assert_code(OperationsErrorCode.INVALID_FEATURE,
        lambda: evaluate_feature_flag(evaluation_id="feval1:fictional:mutated",
            flag_key="feature.alpha", flags=[flag], scope=ops_scope(), as_of=NOW))


def test_pure_kernel_has_no_runtime_io_dependencies_or_execution_calls():
    path = Path(__file__).parents[1] / "src" / "marketmatch_operations.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {node.names[0].name.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, ast.Import)}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module}
    assert imported.isdisjoint({"os", "pathlib", "subprocess", "socket", "httpx",
                                "requests", "sqlalchemy", "smtplib", "tarfile"})
    direct_calls = {node.func.id for node in ast.walk(tree)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    attribute_calls = {node.func.attr for node in ast.walk(tree)
                       if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert direct_calls.isdisjoint({"open", "sleep"})
    assert attribute_calls.isdisjoint({"send", "sendmail", "post", "connect", "execute",
                                        "commit", "write_text", "write_bytes"})


def test_production_routes_do_not_import_operations_kernel():
    root = Path(__file__).parents[1]
    route_sources = [root / "app.py", *sorted((root / "routes").rglob("*.py"))]
    assert all("marketmatch_operations" not in path.read_text(encoding="utf-8")
               for path in route_sources)


def test_adr_declares_boundaries_and_required_distinctions():
    path = Path(__file__).parents[1] / "docs" / "adr" / "0008-marketmatch-operations-reliability-foundations-v1.md"
    text = path.read_text(encoding="utf-8")
    for phrase in (
        "Feature flags versus Authority", "Configuration versus secrets",
        "Job, Request, Run, and Attempt", "Notification intent versus delivery",
        "Operational Signal is not a Truth Event", "completion never implies",
        "Restore completion versus verification", "Tenant is never inferred",
        "No production route imports the kernel", "Incident contract is deferred",
    ):
        assert phrase in text
    assert "feature flags are deployed" in text
    assert "backups run\nautomatically" in text
