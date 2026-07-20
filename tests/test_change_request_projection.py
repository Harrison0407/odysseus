"""Adversarial coverage for CTCF-CR-PROJ-018: basic package participation
must not expose a Change Request's raw field_name/frozen_current_value/
proposed_new_value/reason. Three permissions are distinguished: (1)
knowing a Change Request exists, (2) reading its raw values, (3) approving
or rejecting it. Decision-button visibility must never substitute for read
authorization.
"""

import pytest
from django.urls import reverse

from apps.governance import services as governance_services
from apps.governance.models import Party, PartyMembership
from apps.procurement import services as procurement_services

pytestmark = pytest.mark.django_db


@pytest.fixture
def package(organization):
    pkg = procurement_services.create_package(organization, "cr-proj-pkg", "CR_PROJ_PACKAGE_SENTINEL", None)
    pkg.is_frozen = True
    pkg.frozen_snapshot = {"roles": {"production_factory": "old-factory-id", "seller_of_record": "old-seller-id"}}
    pkg.save()
    return pkg


def _assign(package, user, role_code):
    party = Party.objects.create(display_name=f"{user.username}-{role_code}", hosting_organization=package.organization)
    PartyMembership.objects.create(party=party, user=user)
    assignment = procurement_services.assign_package_role(package, party, role_code, user)
    return party, assignment


@pytest.fixture
def restricted_buyer(package, manuel):
    _assign(package, manuel, "buyer")
    return manuel


@pytest.fixture
def role_change_authority(package, markeris):
    _, assignment = _assign(package, markeris, "china_procurement_operator")
    governance_services.grant_capability("APPROVE_ROLE_CHANGE", user=markeris, role_assignment=assignment, package=package)
    return markeris


@pytest.fixture
def visibility_change_authority(package, lucia):
    _, assignment = _assign(package, lucia, "china_procurement_operator")
    governance_services.grant_capability("APPROVE_VISIBILITY_CHANGE", user=lucia, role_assignment=assignment, package=package)
    return lucia


class TestChangeRequestDetailAuthorization:
    def test_restricted_buyer_sees_only_safe_summary(self, client, package, restricted_buyer):
        cr = governance_services.request_change(
            package, "production_factory", "PROPOSED_FACTORY_SENTINEL", "REQUEST_REASON_SENTINEL", restricted_buyer,
        )
        # Requester exception intentionally does not apply here since
        # restricted_buyer IS the requester in this fixture; use a
        # different unrelated viewer to prove the pure "no detailed
        # authority" case.
        unrelated_buyer_party, _ = _assign(package, restricted_buyer, "buyer_approver")
        from django.contrib.auth import get_user_model

        User = get_user_model()
        other_buyer = User.objects.create_user(username="cr_other_buyer", password="testpass123")
        from apps.accounts.models import UserProfile

        UserProfile.objects.create(user=other_buyer, organization=package.organization)
        _assign(package, other_buyer, "buyer")

        client.force_login(other_buyer)
        response = client.get(reverse("procurement:package-detail", args=[package.pk]))
        body = response.content.decode()
        assert "old-factory-id" not in body
        assert "PROPOSED_FACTORY_SENTINEL" not in body
        assert "REQUEST_REASON_SENTINEL" not in body
        row = next(r for r in response.context["change_requests"] if r["id"] == cr.id)
        assert row["detailed"] is False
        assert "field_name" not in row
        assert "frozen_current_value" not in row
        assert "proposed_new_value" not in row
        assert "reason" not in row
        assert row["can_decide"] is False

    def test_requester_sees_their_own_request_detail(self, client, package, restricted_buyer):
        governance_services.request_change(
            package, "production_factory", "PROPOSED_FACTORY_SENTINEL_2", "REQUEST_REASON_SENTINEL_2", restricted_buyer,
        )
        client.force_login(restricted_buyer)
        response = client.get(reverse("procurement:package-detail", args=[package.pk]))
        row = response.context["change_requests"][0]
        assert row["detailed"] is True
        assert row["proposed_new_value"] == "PROPOSED_FACTORY_SENTINEL_2"

    def test_role_change_authority_sees_role_field_detail(self, client, package, role_change_authority):
        cr = governance_services.request_change(
            package, "production_factory", "PROPOSED_FACTORY_SENTINEL_3", "reason", role_change_authority,
        )
        client.force_login(role_change_authority)
        response = client.get(reverse("procurement:package-detail", args=[package.pk]))
        row = next(r for r in response.context["change_requests"] if r["id"] == cr.id)
        assert row["detailed"] is True
        assert row["can_decide"] is True

    def test_role_change_authority_does_not_see_visibility_field_detail(self, client, package, role_change_authority):
        """APPROVE_ROLE_CHANGE must not expose a visibility_mode Change
        Request's raw values -- authority for one field category must not
        reveal another."""
        cr = governance_services.request_change(
            package, "visibility_mode", "controlled_transparency", "VISIBILITY_REASON_SENTINEL", role_change_authority,
        )
        # A distinct viewer who only holds APPROVE_ROLE_CHANGE (not the
        # requester, not APPROVE_VISIBILITY_CHANGE) must not see detail.
        from django.contrib.auth import get_user_model

        User = get_user_model()
        role_only_viewer = User.objects.create_user(username="cr_role_only_viewer", password="testpass123")
        from apps.accounts.models import UserProfile

        UserProfile.objects.create(user=role_only_viewer, organization=package.organization)
        _, assignment = _assign(package, role_only_viewer, "china_procurement_operator")
        governance_services.grant_capability("APPROVE_ROLE_CHANGE", user=role_only_viewer, role_assignment=assignment, package=package)

        client.force_login(role_only_viewer)
        response = client.get(reverse("procurement:package-detail", args=[package.pk]))
        body = response.content.decode()
        assert "VISIBILITY_REASON_SENTINEL" not in body
        row = next(r for r in response.context["change_requests"] if r["id"] == cr.id)
        assert row["detailed"] is False
        assert row["can_decide"] is False

    def test_visibility_authority_sees_visibility_field_detail_only(self, client, package, visibility_change_authority):
        cr = governance_services.request_change(
            package, "visibility_mode", "controlled_transparency", "VISIBILITY_REASON_SENTINEL_4", visibility_change_authority,
        )
        client.force_login(visibility_change_authority)
        response = client.get(reverse("procurement:package-detail", args=[package.pk]))
        row = next(r for r in response.context["change_requests"] if r["id"] == cr.id)
        assert row["detailed"] is True
        assert row["reason"] == "VISIBILITY_REASON_SENTINEL_4"

    def test_package_b_authority_does_not_reveal_package_a_change_request(self, package, organization, role_change_authority):
        cr = governance_services.request_change(
            package, "production_factory", "PACKAGE_A_SENTINEL", "reason", role_change_authority,
        )
        package_b = procurement_services.create_package(organization, "cr-proj-pkg-b", "CR_PROJ_PACKAGE_B", None)
        from django.contrib.auth import get_user_model

        User = get_user_model()
        package_b_actor = User.objects.create_user(username="cr_package_b_actor", password="testpass123")
        from apps.accounts.models import UserProfile

        UserProfile.objects.create(user=package_b_actor, organization=organization)
        _, assignment_b = _assign(package_b, package_b_actor, "china_procurement_operator")
        governance_services.grant_capability("APPROVE_ROLE_CHANGE", user=package_b_actor, role_assignment=assignment_b, package=package_b)

        assert governance_services.can_view_change_request_detail(package_b_actor, cr) is False
        projection = governance_services.change_request_projection(package_b_actor, cr)
        assert projection["detailed"] is False
        assert "PACKAGE_A_SENTINEL" not in str(projection)

    def test_different_organization_actor_cannot_discover_package(self, client, package, other_org_user):
        governance_services.request_change(package, "production_factory", "value", "reason", other_org_user)
        client.force_login(other_org_user)
        response = client.get(reverse("procurement:package-detail", args=[package.pk]))
        assert response.status_code == 404

    def test_approve_reject_and_hold_recomputation_still_work(self, client, package, role_change_authority):
        first = governance_services.request_change(package, "production_factory", "new-1", "reason", role_change_authority)
        second = governance_services.request_change(package, "seller_of_record", "new-2", "reason", role_change_authority)
        assert package.change_requests.count() == 2

        client.force_login(role_change_authority)
        response = client.get(reverse("procurement:package-detail", args=[package.pk]))
        rows = {r["id"]: r for r in response.context["change_requests"]}
        assert rows[first.id]["can_decide"] is True
        assert rows[second.id]["can_decide"] is True

        governance_services.approve_change_request(first, role_change_authority)
        package.refresh_from_db()
        assert package.is_on_hold  # second CR still pending
        governance_services.reject_change_request(second, role_change_authority)
        package.refresh_from_db()
        assert not package.is_on_hold
