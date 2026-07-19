"""Tests for the Party/Role/Capability foundation (Controlled
Transparency / Controlled Confidentiality release). A Party is separate
from Role; Role is separate from Capability — holding a role never
implies every action, and an unknown relationship is denied by default.
"""

import datetime

import pytest

from apps.governance import services
from apps.governance.models import CapabilityGrant, Party, PartyMembership, RoleAssignment
from apps.procurement.models import ProcurementPackage

pytestmark = pytest.mark.django_db


@pytest.fixture
def china_org(organization):
    from apps.accounts.models import Organization

    return Organization.objects.create(name="China Trading Co Test", default_currency="USD")


@pytest.fixture
def package_a(china_org):
    return ProcurementPackage.objects.create(organization=china_org, code="pkg-a-gov-test", name="Package A")


@pytest.fixture
def package_b(china_org):
    return ProcurementPackage.objects.create(organization=china_org, code="pkg-b-gov-test", name="Package B")


@pytest.fixture
def trading_co_party(china_org):
    return Party.objects.create(
        party_type=Party.PartyType.ORGANIZATION, display_name="China Trading Co", organization=china_org,
        hosting_organization=china_org,
    )


class TestPartyRoleSeparation:
    def test_one_party_may_exercise_multiple_roles(self, china_org, package_a, harrison, trading_co_party):
        PartyMembership.objects.create(party=trading_co_party, user=harrison)
        services.create_role_assignment(trading_co_party, "seller_of_record", china_org, user=harrison, package=package_a)
        services.create_role_assignment(trading_co_party, "china_procurement_operator", china_org, user=harrison, package=package_a)
        assert services.has_role(harrison, "seller_of_record", package=package_a)
        assert services.has_role(harrison, "china_procurement_operator", package=package_a)

    def test_same_party_has_different_roles_in_different_packages(self, china_org, package_a, package_b, harrison, trading_co_party):
        PartyMembership.objects.create(party=trading_co_party, user=harrison)
        services.create_role_assignment(trading_co_party, "seller_of_record", china_org, user=harrison, package=package_a)
        services.create_role_assignment(trading_co_party, "buyer", china_org, user=harrison, package=package_b)
        assert services.has_role(harrison, "seller_of_record", package=package_a)
        assert not services.has_role(harrison, "seller_of_record", package=package_b)
        assert services.has_role(harrison, "buyer", package=package_b)
        assert not services.has_role(harrison, "buyer", package=package_a)

    def test_role_assignment_effective_dates(self, china_org, package_a, harrison, trading_co_party):
        PartyMembership.objects.create(party=trading_co_party, user=harrison)
        future = datetime.date.today() + datetime.timedelta(days=30)
        services.create_role_assignment(
            trading_co_party, "china_procurement_operator", china_org, user=harrison, package=package_a,
            effective_from=future,
        )
        assert not services.has_role(harrison, "china_procurement_operator", package=package_a)

    def test_role_assignment_expired_is_not_active(self, china_org, package_a, harrison, trading_co_party):
        PartyMembership.objects.create(party=trading_co_party, user=harrison)
        past_start = datetime.date.today() - datetime.timedelta(days=60)
        past_end = datetime.date.today() - datetime.timedelta(days=1)
        services.create_role_assignment(
            trading_co_party, "china_procurement_operator", china_org, user=harrison, package=package_a,
            effective_from=past_start, effective_until=past_end,
        )
        assert not services.has_role(harrison, "china_procurement_operator", package=package_a)


class TestCapabilityBeyondRole:
    def test_role_membership_alone_does_not_grant_sensitive_capability(self, china_org, package_a, harrison, trading_co_party):
        PartyMembership.objects.create(party=trading_co_party, user=harrison)
        services.create_role_assignment(trading_co_party, "china_procurement_operator", china_org, user=harrison, package=package_a)
        # Role-default capabilities include VIEW_FACTORY_QUOTE (low risk)...
        assert services.has_capability(harrison, "VIEW_FACTORY_QUOTE", package=package_a)
        # ...but never APPROVE_CLIENT_QUOTE / AUTHORIZE_DISCLOSURE, which
        # must always come from an explicit CapabilityGrant.
        assert not services.has_capability(harrison, "APPROVE_CLIENT_QUOTE", package=package_a)
        assert not services.has_capability(harrison, "AUTHORIZE_DISCLOSURE", package=package_a)

    def test_explicit_capability_grant_unlocks_sensitive_action(self, china_org, package_a, harrison, trading_co_party):
        PartyMembership.objects.create(party=trading_co_party, user=harrison)
        assignment = services.create_role_assignment(
            trading_co_party, "china_procurement_operator", china_org, user=harrison, package=package_a,
        )
        services.grant_capability("APPROVE_CLIENT_QUOTE", user=harrison, role_assignment=assignment, package=package_a)
        assert services.has_capability(harrison, "APPROVE_CLIENT_QUOTE", package=package_a)

    def test_capability_grant_scoped_to_one_package_only(self, china_org, package_a, package_b, harrison, trading_co_party):
        PartyMembership.objects.create(party=trading_co_party, user=harrison)
        assignment = services.create_role_assignment(
            trading_co_party, "china_procurement_operator", china_org, user=harrison, package=package_a,
        )
        services.grant_capability("APPROVE_CLIENT_QUOTE", user=harrison, role_assignment=assignment, package=package_a)
        assert services.has_capability(harrison, "APPROVE_CLIENT_QUOTE", package=package_a)
        assert not services.has_capability(harrison, "APPROVE_CLIENT_QUOTE", package=package_b)

    def test_unknown_relationship_denied_by_default(self, package_a, manuel):
        # manuel has no Party, no membership, no role assignment at all.
        assert not services.has_capability(manuel, "VIEW_FACTORY_QUOTE", package=package_a)
        assert not services.has_role(manuel, "china_procurement_operator", package=package_a)
        assert services.active_role_assignments(manuel, package=package_a).count() == 0

    def test_unauthenticated_or_none_user_denied(self, package_a):
        assert not services.has_capability(None, "VIEW_FACTORY_QUOTE", package=package_a)


class TestRoleAssignmentSupersession:
    def test_supersede_role_assignment_preserves_history(self, china_org, package_a, harrison, trading_co_party):
        PartyMembership.objects.create(party=trading_co_party, user=harrison)
        original = services.create_role_assignment(
            trading_co_party, "buyer", china_org, user=harrison, package=package_a,
        )
        new_assignment = services.supersede_role_assignment(original, harrison, role_code="buyer_approver")
        original.refresh_from_db()
        assert original.status == RoleAssignment.Status.ENDED
        assert new_assignment.supersedes_id == original.id
        assert new_assignment.version == 2
        assert services.has_role(harrison, "buyer_approver", package=package_a)
        assert not services.has_role(harrison, "buyer", package=package_a)
