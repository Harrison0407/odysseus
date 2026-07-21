"""Real lock/race tests. They execute only against PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, connections

from apps.governance import services as governance_services
from apps.procurement_gates import services
from apps.procurement_gates.models import GatePolicyVersion, PackagePolicyAssignment


pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="requires real PostgreSQL row locks"),
]


def _valid_schema():
    return services.canonical_gate_schema()


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
