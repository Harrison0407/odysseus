"""Adversarial Increment 2 tests for the A1-only gate execution core."""

from datetime import timedelta
import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.governance import services as governance
from apps.governance.models import CapabilityGrant, ChangeRequest, Party, RiskFlag, RoleAssignment
from apps.procurement.models import ProcurementPackage
from apps.procurement_gates import services
from apps.procurement_gates.models import (
    GATE_CODES,
    GateAttempt,
    GateDecision,
    GateEvaluation,
    GatePolicy,
    GatePolicyVersion,
    GateState,
    ImmutableGateHistoryError,
    PackageGateState,
    PackagePolicyAssignment,
    _allow_controlled_assignment_write,
    _allow_controlled_execution_write,
)

pytestmark = pytest.mark.django_db

User = get_user_model()


def _grant(user, capability, *, package=None, organization=None, **kwargs):
    return governance.grant_capability(
        capability,
        granted_to_user=user,
        package=package,
        organization=organization,
        **kwargs,
    )


def _pin(package):
    version = GatePolicyVersion.objects.get(
        policy__is_canonical_default=True,
        status=GatePolicyVersion.Status.PUBLISHED,
    )
    with _allow_controlled_assignment_write():
        return PackagePolicyAssignment.objects.create(
            package=package,
            policy_version=version,
            pinned_at=timezone.now(),
        )


def _add_a1_roles(package, *, omit=None, suffix=""):
    role_codes = {
        "buyer_party": "buyer",
        "seller_or_exporter_party": "seller_of_record",
        "procurement_operator_party": "china_procurement_operator",
        "factory_or_site_party": "production_factory",
        "logistics_authority": "logistics_operator",
        "quality_authority": "quality_operator",
        "approval_authority": "buyer_approver",
    }
    rows = {}
    for requirement, role_code in role_codes.items():
        if requirement == omit:
            continue
        party = Party.objects.create(
            display_name=f"CONFIDENTIAL_{requirement}_{suffix}",
            legal_name=f"SECRET_LEGAL_{requirement}_{suffix}",
            hosting_organization=package.organization,
            is_hidden_by_default=requirement == "factory_or_site_party",
        )
        rows[requirement] = RoleAssignment.objects.create(
            party=party,
            role_code=role_code,
            organization_context=package.organization,
            package=package,
            effective_from=timezone.now().date(),
        )
    return rows


@pytest.fixture
def a1_context(organization, harrison):
    package = ProcurementPackage.objects.create(
        organization=organization,
        code="increment-2-a1",
        name="TOP_SECRET_PACKAGE_NAME",
        notes="TOP_SECRET_PACKAGE_NOTES",
    )
    assignment = _pin(package)
    evaluator = User.objects.create_user(username="a1-evaluator")
    reviewer = User.objects.create_user(username="a1-reviewer")
    decider = User.objects.create_user(username="a1-decider")
    _grant(harrison, services.CREATE_PROCUREMENT_GATE_ATTEMPT, organization=organization)
    _grant(evaluator, services.EVALUATE_PROCUREMENT_GATE, package=package)
    _grant(reviewer, services.REQUEST_PROCUREMENT_GATE_REVIEW, package=package)
    _grant(decider, "APPROVE_GATE", package=package)
    roles = _add_a1_roles(package)
    return {
        "package": package,
        "assignment": assignment,
        "initializer": harrison,
        "evaluator": evaluator,
        "reviewer": reviewer,
        "decider": decider,
        "roles": roles,
    }


def _initialize(context, key="init-a1"):
    return services.initialize_package_gates(
        context["initializer"], context["package"].pk, idempotency_key=key
    )


def _evaluate(context, attempt, key="eval-a1"):
    return services.evaluate_a1(
        context["evaluator"], context["package"].pk, attempt.pk, idempotency_key=key
    )


def _review(context, attempt, key="review-a1"):
    return services.request_a1_review(
        context["reviewer"], context["package"].pk, attempt.pk, idempotency_key=key
    )


def _decide(context, attempt, evaluation, decision="PASSED", key="decision-a1", comment=""):
    return services.decide_a1_advancement(
        context["decider"],
        context["package"].pk,
        attempt.pk,
        evaluation.pk,
        decision,
        idempotency_key=key,
        comment=comment,
    )


class TestInitialization:
    def test_initialization_creates_exactly_one_a1_and_six_projection_rows(self, a1_context):
        package = a1_context["package"]
        before = {
            "status": package.status,
            "is_frozen": package.is_frozen,
            "frozen_snapshot": package.frozen_snapshot,
            "is_on_hold": package.is_on_hold,
        }
        attempt = _initialize(a1_context)

        assert attempt.gate_code == "A1"
        assert attempt.attempt_number == 1
        assert attempt.policy_version_id == a1_context["assignment"].policy_version_id
        assert GateAttempt.objects.filter(package=package).count() == 1
        states = dict(PackageGateState.objects.filter(package=package).values_list("gate_code", "state"))
        assert states == {"A1": GateState.IN_REVIEW, **{code: GateState.NOT_STARTED for code in GATE_CODES[1:]}}
        package.refresh_from_db()
        assert {key: getattr(package, key) for key in before} == before
        assert not GateEvaluation.objects.exists()
        assert not GateDecision.objects.exists()

    def test_identical_initialization_is_idempotent_and_conflict_fails(self, a1_context):
        first = _initialize(a1_context)
        assert _initialize(a1_context).pk == first.pk
        with pytest.raises(services.GateIdempotencyConflict):
            _initialize(a1_context, "another-init-key")
        assert GateAttempt.objects.count() == 1

    def test_exempt_package_never_receives_fabricated_progression(self, a1_context):
        assignment = a1_context["assignment"]
        with _allow_controlled_assignment_write():
            assignment.gate_progression_exempt = True
            assignment.save(update_fields=["gate_progression_exempt", "updated_at"])
        with pytest.raises(services.GateExecutionError):
            _initialize(a1_context)
        assert not GateAttempt.objects.exists()
        assert not PackageGateState.objects.exists()

    def test_initialization_requires_exact_org_capability(self, a1_context):
        grant = CapabilityGrant.objects.get(
            user=a1_context["initializer"],
            capability_code=services.CREATE_PROCUREMENT_GATE_ATTEMPT,
        )
        grant.is_active = False
        grant.save(update_fields=["is_active"])
        with pytest.raises(governance.AuthorizationDenied):
            _initialize(a1_context)
        assert not GateAttempt.objects.exists()
        denied = AuditEvent.objects.filter(action=AuditEvent.Action.PRIVILEGED_ACCESS_DENIED).latest("occurred_at")
        assert denied.metadata["package_id"] == str(a1_context["package"].pk)

    def test_package_scoped_grant_cannot_bootstrap_and_other_package_does_not_authorize(self, a1_context):
        CapabilityGrant.objects.filter(
            user=a1_context["initializer"],
            capability_code=services.CREATE_PROCUREMENT_GATE_ATTEMPT,
        ).delete()
        other = ProcurementPackage.objects.create(
            organization=a1_context["package"].organization, code="other-a1", name="Other"
        )
        _grant(a1_context["initializer"], services.CREATE_PROCUREMENT_GATE_ATTEMPT, package=other)
        with pytest.raises(governance.AuthorizationDenied):
            _initialize(a1_context)


class TestAttemptProtection:
    def test_attempt_uniqueness_and_open_constraint_are_database_enforced(self, a1_context):
        attempt = _initialize(a1_context)
        with pytest.raises(IntegrityError), transaction.atomic():
            with _allow_controlled_execution_write():
                GateAttempt.objects.create(
                    package=attempt.package,
                    assignment=attempt.assignment,
                    policy_version=attempt.policy_version,
                    gate_code="A1",
                    attempt_number=1,
                    attempt_creation_capability=services.CREATE_PROCUREMENT_GATE_ATTEMPT,
                    decision_capability="APPROVE_GATE",
                    opened_at=timezone.now(),
                    idempotency_key="constraint-duplicate",
                    request_fingerprint="a" * 64,
                )

    def test_instance_queryset_base_manager_bulk_and_delete_bypasses_fail(self, a1_context):
        attempt = _initialize(a1_context)
        attempt.closure_code = "FAILED"
        with pytest.raises(ValidationError):
            attempt.save()
        with pytest.raises(ValidationError):
            GateAttempt.objects.filter(pk=attempt.pk).update(closure_code="FAILED")
        with pytest.raises(ValidationError):
            GateAttempt._base_manager.filter(pk=attempt.pk).update(closure_code="FAILED")
        with pytest.raises(ValidationError):
            GateAttempt.objects.bulk_update([attempt], ["closure_code"])
        with pytest.raises(ValidationError):
            attempt.delete()
        with pytest.raises(ValidationError):
            GateAttempt.objects.filter(pk=attempt.pk).delete()
        with pytest.raises(ProtectedError):
            a1_context["package"].delete()

    def test_direct_create_and_bulk_create_are_blocked(self, a1_context):
        assignment = a1_context["assignment"]
        kwargs = dict(
            package=a1_context["package"], assignment=assignment,
            policy_version=assignment.policy_version, gate_code="A1", attempt_number=1,
            attempt_creation_capability=services.CREATE_PROCUREMENT_GATE_ATTEMPT,
            decision_capability="APPROVE_GATE", opened_at=timezone.now(),
            idempotency_key="direct", request_fingerprint="b" * 64,
        )
        with pytest.raises(ValidationError):
            GateAttempt.objects.create(**kwargs)
        with pytest.raises(ValidationError):
            GateAttempt.objects.bulk_create([GateAttempt(**kwargs)])


class TestA1Evaluation:
    def test_complete_a1_is_satisfied_but_never_passes_gate(self, a1_context):
        attempt = _initialize(a1_context)
        evaluation = _evaluate(a1_context, attempt)
        assert evaluation.result_code == GateEvaluation.Result.SATISFIED
        assert evaluation.overall_ready is True
        assert evaluation.blocker_codes == []
        assert services.compute_gate_state(a1_context["package"], "A1") == GateState.READY
        assert not GateDecision.objects.exists()
        attempt.refresh_from_db()
        assert attempt.closed_at is None

    @pytest.mark.parametrize("requirement", list(services.A1_ROLE_REQUIREMENTS))
    def test_each_required_party_or_authority_omission_blocks_independently(self, a1_context, requirement):
        a1_context["roles"][requirement].delete()
        attempt = _initialize(a1_context)
        evaluation = _evaluate(a1_context, attempt)
        assert evaluation.result_code == GateEvaluation.Result.BLOCKED
        assert services.A1_ROLE_BLOCKERS[requirement] in evaluation.blocker_codes
        assert evaluation.requirement_results[requirement]["satisfied"] is False

    def test_expired_and_wrong_package_roles_do_not_qualify(self, a1_context):
        buyer = a1_context["roles"]["buyer_party"]
        buyer.effective_until = timezone.now().date() - timedelta(days=1)
        buyer.save(update_fields=["effective_until"])
        wrong = ProcurementPackage.objects.create(
            organization=a1_context["package"].organization, code="wrong-role-package", name="Wrong"
        )
        Party.objects.create(display_name="WRONG_PACKAGE_PARTY", hosting_organization=wrong.organization)
        attempt = _initialize(a1_context)
        evaluation = _evaluate(a1_context, attempt)
        assert "A1_BUYER_ROLE_MISSING" in evaluation.blocker_codes

    def test_duplicate_critical_role_identity_is_an_explicit_conflict(self, a1_context):
        extra = Party.objects.create(
            display_name="SECOND_BUYER_SECRET", hosting_organization=a1_context["package"].organization
        )
        RoleAssignment.objects.create(
            party=extra, role_code="buyer", organization_context=a1_context["package"].organization,
            package=a1_context["package"], effective_from=timezone.now().date(),
        )
        evaluation = _evaluate(a1_context, _initialize(a1_context))
        assert "A1_CRITICAL_ROLE_CONFLICT" in evaluation.blocker_codes
        assert evaluation.requirement_results["critical_role_conflicts_clear"]["satisfied"] is False

    def test_evaluation_requires_exact_package_scope(self, a1_context):
        attempt = _initialize(a1_context)
        CapabilityGrant.objects.filter(
            user=a1_context["evaluator"], capability_code=services.EVALUATE_PROCUREMENT_GATE
        ).delete()
        other = ProcurementPackage.objects.create(
            organization=a1_context["package"].organization, code="eval-other-scope", name="Other"
        )
        _grant(a1_context["evaluator"], services.EVALUATE_PROCUREMENT_GATE, package=other)
        with pytest.raises(governance.AuthorizationDenied):
            _evaluate(a1_context, attempt)
        assert not GateEvaluation.objects.exists()

    @pytest.mark.parametrize("blocker", ["risk", "change", "hold"])
    def test_risk_change_and_hold_each_block(self, a1_context, blocker):
        attempt = _initialize(a1_context)
        if blocker == "risk":
            RiskFlag.objects.create(
                package=a1_context["package"], level=RiskFlag.Level.HIGH_RISK,
                indicator_codes=["IDENTITY_CONFLICT"], notes="SENSITIVE_RISK_NOTES",
            )
            expected = "A1_UNRESOLVED_BLOCKING_RISK"
        elif blocker == "change":
            ChangeRequest.objects.create(
                package=a1_context["package"], field_name="seller_of_record",
                frozen_current_value="SECRET_OLD", proposed_new_value="SECRET_NEW", reason="SECRET_REASON",
            )
            expected = "A1_UNRESOLVED_BLOCKING_CHANGE"
        else:
            a1_context["package"].is_on_hold = True
            a1_context["package"].save(update_fields=["is_on_hold"])
            expected = "A1_PACKAGE_ON_HOLD"
        assert expected in _evaluate(a1_context, attempt).blocker_codes

    def test_package_status_never_implies_a1_satisfaction(self, a1_context):
        for assignment in a1_context["roles"].values():
            assignment.delete()
        a1_context["package"].status = ProcurementPackage.Status.CLOSED
        a1_context["package"].save(update_fields=["status"])
        evaluation = _evaluate(a1_context, _initialize(a1_context))
        assert evaluation.result_code == GateEvaluation.Result.BLOCKED

    def test_evaluation_idempotency_immutability_and_confidentiality(self, a1_context):
        attempt = _initialize(a1_context)
        evaluation = _evaluate(a1_context, attempt)
        assert _evaluate(a1_context, attempt).pk == evaluation.pk
        other_package = ProcurementPackage.objects.create(
            organization=a1_context["package"].organization,
            code="evaluation-key-conflict",
            name="Other evaluation target",
        )
        _pin(other_package)
        _grant(a1_context["evaluator"], services.EVALUATE_PROCUREMENT_GATE, package=other_package)
        other_attempt = services.initialize_package_gates(
            a1_context["initializer"], other_package.pk, idempotency_key="init-other-evaluation"
        )
        with pytest.raises(services.GateIdempotencyConflict):
            services.evaluate_a1(
                a1_context["evaluator"], other_package.pk, other_attempt.pk,
                idempotency_key="eval-a1",
            )
        evaluation.overall_ready = False
        with pytest.raises(ValidationError):
            evaluation.save()
        with pytest.raises(ValidationError):
            GateEvaluation.objects.filter(pk=evaluation.pk).update(overall_ready=False)
        with pytest.raises(ValidationError):
            GateEvaluation._base_manager.filter(pk=evaluation.pk).update(overall_ready=False)
        with pytest.raises(ValidationError):
            GateEvaluation.objects.bulk_update([evaluation], ["overall_ready"])
        with pytest.raises(ValidationError):
            evaluation.delete()
        serialized = json.dumps({
            "requirements": evaluation.requirement_results,
            "summary": evaluation.safe_summary,
            "blockers": evaluation.blocker_codes,
        })
        assert "TOP_SECRET_PACKAGE" not in serialized
        assert "CONFIDENTIAL_" not in serialized
        assert "SECRET_LEGAL" not in serialized


class TestReviewDecisionAndProjection:
    def test_review_then_approval_passes_a1_and_only_readies_a2(self, a1_context):
        package = a1_context["package"]
        status = package.status
        attempt = _initialize(a1_context)
        evaluation = _evaluate(a1_context, attempt)
        reviewed = _review(a1_context, attempt)
        assert _review(a1_context, attempt).pk == reviewed.pk
        decision = _decide(a1_context, attempt, evaluation)

        assert decision.outcome == GateDecision.Outcome.PASSED
        assert services.compute_gate_state(package, "A1") == GateState.PASSED
        assert services.derive_current_gate(package) == "A2"
        assert services.compute_gate_state(package, "A2") == GateState.NOT_STARTED
        states = dict(PackageGateState.objects.filter(package=package).values_list("gate_code", "state"))
        assert states["A1"] == GateState.PASSED
        assert all(states[code] == GateState.NOT_STARTED for code in GATE_CODES[1:])
        assert not GateAttempt.objects.filter(package=package, gate_code="A2").exists()
        package.refresh_from_db()
        assert package.status == status
        assert package.is_frozen is False

    def test_rejection_is_historical_and_new_attempt_increments(self, a1_context):
        attempt = _initialize(a1_context)
        evaluation = _evaluate(a1_context, attempt)
        _review(a1_context, attempt)
        decision = _decide(
            a1_context, attempt, evaluation, decision="FAILED", key="return-a1", comment="RETURN_REQUIRED"
        )
        assert decision.outcome == GateDecision.Outcome.FAILED
        assert services.derive_current_gate(a1_context["package"]) == "A1"
        _grant(a1_context["initializer"], services.CREATE_PROCUREMENT_GATE_ATTEMPT, package=a1_context["package"])
        second = services.open_a1_attempt(
            a1_context["initializer"], a1_context["package"].pk, idempotency_key="reattempt-a1"
        )
        assert second.attempt_number == 2
        assert attempt.evaluations.filter(pk=evaluation.pk).exists()
        assert GateDecision.objects.filter(pk=decision.pk).exists()

    def test_requester_preparer_and_superuser_alone_cannot_decide(self, a1_context):
        attempt = _initialize(a1_context)
        evaluation = _evaluate(a1_context, attempt)
        _review(a1_context, attempt)
        _grant(a1_context["evaluator"], "APPROVE_GATE", package=a1_context["package"])
        with pytest.raises(services.GateExecutionError):
            services.decide_a1_advancement(
                a1_context["evaluator"], a1_context["package"].pk, attempt.pk, evaluation.pk,
                "PASSED", idempotency_key="self-decision", comment="",
            )
        superuser = User.objects.create_superuser(username="gate-superuser", password="x")
        with pytest.raises(governance.AuthorizationDenied):
            services.decide_a1_advancement(
                superuser, a1_context["package"].pk, attempt.pk, evaluation.pk,
                "PASSED", idempotency_key="superuser-decision", comment="",
            )
        assert not GateDecision.objects.exists()

    def test_review_requires_exact_package_capability(self, a1_context):
        attempt = _initialize(a1_context)
        _evaluate(a1_context, attempt)
        CapabilityGrant.objects.filter(
            user=a1_context["reviewer"], capability_code=services.REQUEST_PROCUREMENT_GATE_REVIEW
        ).delete()
        with pytest.raises(governance.AuthorizationDenied):
            _review(a1_context, attempt)
        attempt.refresh_from_db()
        assert attempt.review_requested_at is None

    def test_review_revalidates_current_requirements_inside_lock(self, a1_context):
        attempt = _initialize(a1_context)
        _evaluate(a1_context, attempt)
        a1_context["roles"]["buyer_party"].delete()
        with pytest.raises(services.GateExecutionError, match="stale"):
            _review(a1_context, attempt)
        attempt.refresh_from_db()
        assert attempt.review_requested_at is None

    def test_wrong_package_expired_and_stale_authority_fail_closed(self, a1_context):
        attempt = _initialize(a1_context)
        first = _evaluate(a1_context, attempt, "eval-first")
        second = _evaluate(a1_context, attempt, "eval-second")
        _review(a1_context, attempt)
        with pytest.raises(services.GateExecutionError):
            _decide(a1_context, attempt, first, key="stale-decision")

        grant = CapabilityGrant.objects.get(user=a1_context["decider"], capability_code="APPROVE_GATE")
        grant.effective_until = timezone.now().date() - timedelta(days=1)
        grant.save(update_fields=["effective_until"])
        with pytest.raises(governance.AuthorizationDenied):
            _decide(a1_context, attempt, second, key="expired-decision")

    def test_decision_idempotency_and_conflicting_key(self, a1_context):
        attempt = _initialize(a1_context)
        evaluation = _evaluate(a1_context, attempt)
        _review(a1_context, attempt)
        decision = _decide(a1_context, attempt, evaluation)
        assert _decide(a1_context, attempt, evaluation).pk == decision.pk
        with pytest.raises(services.GateIdempotencyConflict):
            services.decide_a1_advancement(
                a1_context["decider"], a1_context["package"].pk, attempt.pk, evaluation.pk,
                "FAILED", idempotency_key="decision-a1", comment="conflict",
            )
        assert GateDecision.objects.count() == 1

    def test_pure_computation_writes_nothing_and_rebuild_is_deterministic(self, a1_context):
        attempt = _initialize(a1_context)
        before = (
            GateAttempt.objects.count(), GateEvaluation.objects.count(), GateDecision.objects.count(),
            PackageGateState.objects.count(), AuditEvent.objects.count(),
        )
        assert services.compute_gate_state(a1_context["package"], "A1") == GateState.IN_REVIEW
        assert services.derive_current_gate(a1_context["package"]) == "A1"
        after = (
            GateAttempt.objects.count(), GateEvaluation.objects.count(), GateDecision.objects.count(),
            PackageGateState.objects.count(), AuditEvent.objects.count(),
        )
        assert after == before
        first = [(row.gate_code, row.state) for row in services.rebuild_gate_state(a1_context["package"].pk)]
        second = [(row.gate_code, row.state) for row in services.rebuild_gate_state(a1_context["package"].pk)]
        assert second == first
        assert attempt.pk

    def test_projection_and_decision_direct_mutation_are_blocked(self, a1_context):
        attempt = _initialize(a1_context)
        evaluation = _evaluate(a1_context, attempt)
        _review(a1_context, attempt)
        decision = _decide(a1_context, attempt, evaluation)
        state = PackageGateState.objects.get(package=a1_context["package"], gate_code="A1")
        state.state = GateState.FAILED
        with pytest.raises(ValidationError):
            state.save()
        with pytest.raises(ValidationError):
            PackageGateState.objects.filter(pk=state.pk).update(state=GateState.FAILED)
        with pytest.raises(ValidationError):
            PackageGateState.objects.filter(pk=state.pk).delete()
        decision.comment = "tampered"
        with pytest.raises(ValidationError):
            decision.save()
        with pytest.raises(ValidationError):
            GateDecision.objects.filter(pk=decision.pk).delete()
        for model in (GateAttempt, GateEvaluation, GateDecision, PackageGateState):
            assert model not in admin.site._registry

    def test_failed_advancement_rolls_back_success_history(self, a1_context):
        attempt = _initialize(a1_context)
        stale = _evaluate(a1_context, attempt, "rollback-stale")
        current = _evaluate(a1_context, attempt, "rollback-current")
        _review(a1_context, attempt)
        with pytest.raises(services.GateExecutionError):
            _decide(a1_context, attempt, stale, key="rollback-decision")
        assert current.pk
        assert not GateDecision.objects.exists()
        attempt.refresh_from_db()
        assert attempt.closed_at is None
        assert not AuditEvent.objects.filter(action=AuditEvent.Action.GATE_PASSED).exists()
        assert AuditEvent.objects.filter(action=AuditEvent.Action.GATE_ADVANCEMENT_BLOCKED).exists()

    def test_success_audits_are_identifier_safe_and_rollback_has_no_success(self, a1_context):
        attempt = _initialize(a1_context)
        evaluation = _evaluate(a1_context, attempt)
        _review(a1_context, attempt)
        _decide(a1_context, attempt, evaluation)
        events = AuditEvent.objects.filter(action__in=[
            AuditEvent.Action.PACKAGE_GATES_INITIALIZED,
            AuditEvent.Action.GATE_ATTEMPT_OPENED,
            AuditEvent.Action.GATE_EVALUATED,
            AuditEvent.Action.GATE_REVIEW_REQUESTED,
            AuditEvent.Action.GATE_PASSED,
        ])
        assert events.count() == 5
        serialized = json.dumps(list(events.values_list("summary", "metadata")), default=str)
        for sentinel in (
            "TOP_SECRET_PACKAGE_NAME", "TOP_SECRET_PACKAGE_NOTES", "CONFIDENTIAL_",
            "SECRET_LEGAL", "gate_schema", "factory_price", "markup",
        ):
            assert sentinel not in serialized
