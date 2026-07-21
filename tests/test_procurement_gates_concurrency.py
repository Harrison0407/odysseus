"""Real lock/race tests. They execute only against PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, connections
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.governance import services as governance_services
from apps.governance.models import Party, RoleAssignment
from apps.procurement.models import ProcurementPackage
from apps.procurement_gates import services
from apps.procurement_gates.models import (
    GateDecision, GatePolicyVersion, PackageGateState, PackagePolicyAssignment,
)


pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="requires real PostgreSQL row locks"),
]


def _valid_schema():
    return services.canonical_gate_schema()


@pytest.fixture
def package_a(organization):
    return ProcurementPackage.objects.create(
        organization=organization, code="pg-concurrency-package", name="PG concurrency package"
    )


@pytest.fixture
def platform_admin(harrison):
    governance_services.grant_capability("PUBLISH_GATE_POLICY", granted_to_user=harrison)
    return harrison


def _prepare_open_a1(package, initializer, *, suffix=""):
    governance_services.grant_capability(
        "ASSIGN_GATE_POLICY", granted_to_user=initializer, package=package
    )
    services.assign_policy_to_package(initializer, package.pk)
    governance_services.grant_capability(
        services.CREATE_PROCUREMENT_GATE_ATTEMPT,
        granted_to_user=initializer,
        organization=package.organization,
    )
    for role_code in (
        "buyer", "seller_of_record", "china_procurement_operator", "production_factory",
        "logistics_operator", "quality_operator", "buyer_approver",
    ):
        party = Party.objects.create(
            display_name=f"PG {role_code} {suffix}", hosting_organization=package.organization
        )
        RoleAssignment.objects.create(
            party=party, role_code=role_code, organization_context=package.organization,
            package=package, effective_from=timezone.now().date(),
        )
    return services.initialize_package_gates(
        initializer, package.pk, idempotency_key=f"pg-init-{suffix or 'base'}"
    )


def _prepare_review(package, initializer, *, suffix=""):
    attempt = _prepare_open_a1(package, initializer, suffix=suffix)
    User = get_user_model()
    evaluator = User.objects.create_user(username=f"pg-evaluator-{suffix}")
    reviewer = User.objects.create_user(username=f"pg-reviewer-{suffix}")
    governance_services.grant_capability(
        services.EVALUATE_PROCUREMENT_GATE, granted_to_user=evaluator, package=package
    )
    governance_services.grant_capability(
        services.REQUEST_PROCUREMENT_GATE_REVIEW, granted_to_user=reviewer, package=package
    )
    evaluation = services.evaluate_a1(
        evaluator, package.pk, attempt.pk, idempotency_key=f"pg-eval-{suffix}"
    )
    services.request_a1_review(
        reviewer, package.pk, attempt.pk, idempotency_key=f"pg-review-{suffix}"
    )
    return attempt, evaluation


def test_concurrent_assignment_serializes_on_package(package_a, harrison):
    services.seed_canonical_policy()
    governance_services.grant_capability(
        "ASSIGN_GATE_POLICY", granted_to_user=harrison, package=package_a,
    )

    def assign():
        try:
            return services.assign_policy_to_package(harrison, package_a.pk).pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: assign(), range(2)))
    assert ids[0] == ids[1]
    assert PackagePolicyAssignment.objects.filter(package=package_a).count() == 1


def test_concurrent_canonical_withdrawals_leave_one_published(platform_admin):
    policy = services.seed_canonical_policy()
    v1 = policy.versions.get(version_number=1)
    v2 = services.publish_policy_version(
        services.create_draft_policy_version(
            policy=policy, actor=platform_admin, gate_schema=_valid_schema(), supersedes=v1,
        ), platform_admin,
    )

    def withdraw(version_id):
        try:
            version = GatePolicyVersion.objects.get(pk=version_id)
            try:
                services.withdraw_policy_version(version, platform_admin, "concurrency test")
                return "withdrawn"
            except services.GatePolicyStateError:
                return "protected"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(withdraw, [v1.pk, v2.pk]))
    assert sorted(outcomes) == ["protected", "withdrawn"]
    assert policy.versions.filter(status=GatePolicyVersion.Status.PUBLISHED).count() == 1


def test_simultaneous_initialization_creates_one_a1_attempt(package_a, harrison):
    services.seed_canonical_policy()
    governance_services.grant_capability(
        "ASSIGN_GATE_POLICY", granted_to_user=harrison, package=package_a
    )
    services.assign_policy_to_package(harrison, package_a.pk)
    governance_services.grant_capability(
        services.CREATE_PROCUREMENT_GATE_ATTEMPT,
        granted_to_user=harrison,
        organization=package_a.organization,
    )

    def initialize():
        connections.close_all()
        try:
            return services.initialize_package_gates(
                harrison, package_a.pk, idempotency_key="pg-same-initialization"
            ).pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: initialize(), range(2)))
    assert ids[0] == ids[1]
    assert package_a.gate_attempts.filter(gate_code="A1").count() == 1
    assert PackageGateState.objects.filter(package=package_a).count() == 6


def test_simultaneous_reattempt_creation_serializes_on_package(package_a, harrison):
    services.seed_canonical_policy()
    attempt, evaluation = _prepare_review(package_a, harrison, suffix="reattempt")
    User = get_user_model()
    decider = User.objects.create_user(username="pg-return-decider")
    governance_services.grant_capability("APPROVE_GATE", granted_to_user=decider, package=package_a)
    services.decide_a1_advancement(
        decider, package_a.pk, attempt.pk, evaluation.pk, "FAILED",
        idempotency_key="pg-return", comment="return",
    )
    governance_services.grant_capability(
        services.CREATE_PROCUREMENT_GATE_ATTEMPT, granted_to_user=harrison, package=package_a
    )

    def reattempt(key):
        connections.close_all()
        try:
            try:
                return services.open_a1_attempt(harrison, package_a.pk, idempotency_key=key).pk
            except services.GateExecutionError:
                return "blocked"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reattempt, ["pg-reattempt-one", "pg-reattempt-two"]))
    assert outcomes.count("blocked") == 1
    assert package_a.gate_attempts.filter(gate_code="A1", closed_at__isnull=True).count() == 1
    assert package_a.gate_attempts.order_by("-attempt_number").first().attempt_number == 2


def test_simultaneous_a1_decisions_append_one_effective_decision(package_a, harrison):
    services.seed_canonical_policy()
    attempt, evaluation = _prepare_review(package_a, harrison, suffix="decision")
    User = get_user_model()
    deciders = [
        User.objects.create_user(username="pg-decider-one"),
        User.objects.create_user(username="pg-decider-two"),
    ]
    for decider in deciders:
        governance_services.grant_capability("APPROVE_GATE", granted_to_user=decider, package=package_a)

    def decide(args):
        decider, key = args
        connections.close_all()
        try:
            try:
                return services.decide_a1_advancement(
                    decider, package_a.pk, attempt.pk, evaluation.pk, "PASSED",
                    idempotency_key=key, comment="",
                ).pk
            except services.GateExecutionError:
                return "blocked"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(decide, zip(deciders, ["pg-decision-one", "pg-decision-two"])))
    assert outcomes.count("blocked") == 1
    assert GateDecision.objects.filter(attempt=attempt).count() == 1


def test_simultaneous_idempotent_decision_does_not_duplicate_a2_activation(package_a, harrison):
    services.seed_canonical_policy()
    attempt, evaluation = _prepare_review(package_a, harrison, suffix="a2-cache")
    User = get_user_model()
    decider = User.objects.create_user(username="pg-a2-decider")
    governance_services.grant_capability("APPROVE_GATE", granted_to_user=decider, package=package_a)

    def decide():
        connections.close_all()
        try:
            return services.decide_a1_advancement(
                decider, package_a.pk, attempt.pk, evaluation.pk, "PASSED",
                idempotency_key="pg-a2-same-decision", comment="",
            ).pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: decide(), range(2)))
    assert ids[0] == ids[1]
    assert PackageGateState.objects.filter(package=package_a, gate_code="A2").count() == 1
    assert not package_a.gate_attempts.filter(gate_code="A2").exists()
