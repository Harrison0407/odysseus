"""HTTP-level tests for the Controlled Transparency / Confidentiality
release: role-based package projection, cross-organization denial (404,
never a leak through counts/ids), API non-disclosure, and the full
factory-quote -> internal-sheet -> client-quote -> freeze -> change-
request -> disclosure-grant HTTP lifecycle with real separation of
duties enforced end-to-end (not just at the service layer).
"""

import pytest
from django.urls import reverse

from apps.accounts.models import Organization, UserProfile
from apps.governance import services as governance_services
from apps.governance.models import Party, PartyMembership
from apps.procurement import services
from apps.procurement.models import ClientQuote, ProcurementPackage, PurchaseOrder, Supplier

pytestmark = pytest.mark.django_db


@pytest.fixture
def china_org():
    return Organization.objects.create(name="China Trading Co HTTP Test", default_currency="USD")


@pytest.fixture
def package(china_org):
    return services.create_package(china_org, "pkg-http-test", "HTTP Test Package", None)


@pytest.fixture
def trading_co_party(china_org):
    return Party.objects.create(
        party_type=Party.PartyType.ORGANIZATION, display_name="China Trading Co HTTP", organization=china_org,
        hosting_organization=china_org,
    )


@pytest.fixture
def factory_supplier(china_org):
    return Supplier.objects.create(organization=china_org, name="Hidden Factory HTTP Test", country="China", address="Confidential Address HTTP Test")


@pytest.fixture
def factory_party(china_org, factory_supplier):
    return Party.objects.create(
        party_type=Party.PartyType.ORGANIZATION, display_name="Hidden Factory HTTP Test", supplier=factory_supplier,
        hosting_organization=china_org, is_hidden_by_default=True,
    )


@pytest.fixture
def china_ops_user(package, trading_co_party, harrison):
    PartyMembership.objects.create(party=trading_co_party, user=harrison)
    services.assign_package_role(package, trading_co_party, "china_procurement_operator", harrison)
    return harrison


@pytest.fixture
def client_user(organization):
    """A DT-Beach-side client user, in a DIFFERENT organization than
    the package's hosting organization — proves the package layer is
    genuinely cross-organization-aware, not merely single-tenant."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user = User.objects.create_user(username="client_http_test", password="testpass123")
    UserProfile.objects.create(user=user, organization=organization)
    return user


@pytest.fixture
def dt_beach_party(organization, package):
    return Party.objects.create(
        party_type=Party.PartyType.ORGANIZATION, display_name="DT Beach HTTP Test", organization=organization,
        hosting_organization=package.organization,
    )


class TestPackageDetailProjection:
    def test_china_ops_sees_factory_quote_and_cost(self, client, package, china_ops_user, factory_party, factory_supplier):
        services.submit_factory_quote(package, factory_party, factory_supplier, china_ops_user, reference="FQ-HTTP-1", total_amount=1000)
        client.force_login(china_ops_user)
        response = client.get(reverse("procurement:package-detail", args=[package.pk]))
        assert response.status_code == 200
        content = response.content.decode()
        assert "FQ-HTTP-1" in content
        assert "Hidden Factory HTTP Test" in content
        assert "Confidential Address HTTP Test" in content

    def test_client_cannot_see_factory_quote_or_identity(self, client, package, china_ops_user, factory_party, factory_supplier,
                                                          client_user, dt_beach_party):
        services.submit_factory_quote(package, factory_party, factory_supplier, china_ops_user, reference="FQ-HTTP-2", total_amount=2000)
        services.assign_package_role(package, dt_beach_party, "buyer", china_ops_user, client_visible=True)
        PartyMembership.objects.create(party=dt_beach_party, user=client_user)

        client.force_login(client_user)
        response = client.get(reverse("procurement:package-detail", args=[package.pk]))
        assert response.status_code == 200
        content = response.content.decode()
        assert "FQ-HTTP-2" not in content
        assert "Hidden Factory HTTP Test" not in content
        assert "Confidential Address HTTP Test" not in content

    def test_client_sees_approved_client_quote_and_verification_statement(self, client, package, china_ops_user, client_user, dt_beach_party):
        quote = services.create_client_quote(
            package, china_ops_user, visible_seller_party=None, product_description="Ceramic tile HTTP test",
            quantity=100, sell_price=55,
        )
        services.create_verification_assertion(
            package, "production_verified", "Production verified at an authorized site.", china_ops_user,
        )
        services.assign_package_role(package, dt_beach_party, "buyer", china_ops_user, client_visible=True)
        PartyMembership.objects.create(party=dt_beach_party, user=client_user)

        client.force_login(client_user)
        response = client.get(reverse("procurement:package-detail", args=[package.pk]))
        content = response.content.decode()
        assert "Ceramic tile HTTP test" in content
        assert "Production verified at an authorized site." in content


class TestCrossOrganizationDenial:
    def test_unrelated_user_gets_404_not_leak(self, client, package):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        other_org = Organization.objects.create(name="Totally Unrelated Org HTTP Test")
        outsider = User.objects.create_user(username="outsider_http_test", password="testpass123")
        UserProfile.objects.create(user=outsider, organization=other_org)

        client.force_login(outsider)
        response = client.get(reverse("procurement:package-detail", args=[package.pk]))
        assert response.status_code == 404

        # The package list must not leak its existence either.
        list_response = client.get(reverse("procurement:package-list"))
        assert package.name not in list_response.content.decode()

    def test_governance_admin_screens_require_senior_authorization(self, client, package, trading_co_party, manuel):
        # manuel holds a package-scoped role (so package-level
        # capabilities may apply) but not the can_override_gates
        # senior-authorization role used to gate the governance admin
        # screens themselves — those remain denied regardless.
        PartyMembership.objects.create(party=trading_co_party, user=manuel)
        services.assign_package_role(package, trading_co_party, "china_procurement_operator", manuel)
        client.force_login(manuel)
        response = client.get(reverse("governance:party-list"))
        assert response.status_code == 404
        response = client.get(reverse("governance:privileged-audit"))
        assert response.status_code == 404

    def test_authorized_admin_can_view_governance_screens(self, client, harrison):
        client.force_login(harrison)
        response = client.get(reverse("governance:privileged-audit"))
        assert response.status_code == 200


class TestAPINonDisclosure:
    def test_client_cannot_see_upstream_factory_po_via_api(self, client, package, china_ops_user, factory_supplier,
                                                            client_user, dt_beach_party, organization):
        PurchaseOrder.objects.create(
            organization=package.organization, supplier=factory_supplier, po_number="UPSTREAM-HTTP-TEST-1",
            package=package, po_kind=PurchaseOrder.POKind.UPSTREAM_FACTORY, classification="source_private",
        )
        services.assign_package_role(package, dt_beach_party, "buyer", china_ops_user, client_visible=True)
        PartyMembership.objects.create(party=dt_beach_party, user=client_user)

        client.force_login(client_user)
        response = client.get("/api/v1/purchase-orders/")
        assert response.status_code == 200
        assert "UPSTREAM-HTTP-TEST-1" not in response.content.decode()

    def test_china_ops_org_sees_its_own_upstream_po_via_api(self, client, package, china_ops_user, factory_supplier):
        PurchaseOrder.objects.create(
            organization=package.organization, supplier=factory_supplier, po_number="UPSTREAM-HTTP-TEST-2",
            package=package, po_kind=PurchaseOrder.POKind.UPSTREAM_FACTORY, classification="source_private",
        )
        UserProfile.objects.filter(user=china_ops_user).update(organization=package.organization)
        client.force_login(china_ops_user)
        response = client.get("/api/v1/purchase-orders/")
        assert "UPSTREAM-HTTP-TEST-2" in response.content.decode()


class TestFullHTTPLifecycleWithSeparationOfDuties:
    def test_prepare_and_approve_are_distinct_users_via_http(self, client, package, china_ops_user, dt_beach_party, organization):
        quote = services.create_client_quote(
            package, china_ops_user, visible_seller_party=None, product_description="Full lifecycle tile",
            quantity=500, sell_price=60,
        )

        # Preparer cannot approve their own quote via HTTP.
        client.force_login(china_ops_user)
        response = client.post(reverse("procurement:client-quote-approve", args=[quote.pk]))
        assert response.status_code == 302
        quote.refresh_from_db()
        assert quote.status == ClientQuote.Status.DRAFT

        # A distinct, explicitly-capable approver succeeds.
        from django.contrib.auth import get_user_model

        User = get_user_model()
        approver = User.objects.create_user(username="approver_http_test", password="testpass123")
        UserProfile.objects.create(user=approver, organization=organization)
        approver_party = Party.objects.create(
            party_type=Party.PartyType.INDIVIDUAL, display_name="Approver HTTP Test", hosting_organization=package.organization,
        )
        PartyMembership.objects.create(party=approver_party, user=approver)
        assignment = services.assign_package_role(package, approver_party, "buyer_approver", china_ops_user)
        governance_services.grant_capability("APPROVE_CLIENT_QUOTE", user=approver, role_assignment=assignment, package=package)

        client.force_login(approver)
        response = client.post(reverse("procurement:client-quote-approve", args=[quote.pk]))
        assert response.status_code == 302
        quote.refresh_from_db()
        assert quote.status == ClientQuote.Status.APPROVED
        assert quote.approved_by == approver

    def test_freeze_then_change_request_then_approve_via_http(self, client, package, china_ops_user):
        assignment = package.role_assignments.filter(role_code="china_procurement_operator").first()
        governance_services.grant_capability("APPROVE_GATE", user=china_ops_user, role_assignment=assignment, package=package)

        client.force_login(china_ops_user)
        response = client.post(
            reverse("procurement:package-freeze", args=[package.pk]),
            {"incoterm": "FOB", "currency": "USD", "payment_terms": "30/70"},
        )
        assert response.status_code == 302
        package.refresh_from_db()
        assert package.is_frozen

        response = client.post(
            reverse("procurement:change-request-create", args=[package.pk]),
            {"field_name": "exporter_of_record", "proposed_new_value": "new-value-http", "reason": "Client requested change"},
        )
        assert response.status_code == 302
        package.refresh_from_db()
        assert package.is_on_hold

        change_request = package.change_requests.first()
        # Unauthorized approval attempt (no APPROVE_ROLE_CHANGE yet) leaves it pending.
        response = client.post(reverse("procurement:change-request-decide", args=[change_request.pk, "approve"]))
        change_request.refresh_from_db()
        assert change_request.status == "pending"

        governance_services.grant_capability("APPROVE_ROLE_CHANGE", user=china_ops_user, role_assignment=assignment, package=package)
        response = client.post(reverse("procurement:change-request-decide", args=[change_request.pk, "approve"]))
        assert response.status_code == 302
        change_request.refresh_from_db()
        package.refresh_from_db()
        assert change_request.status == "approved"
        assert not package.is_on_hold


class TestDeniedAttemptsAreAudited:
    def test_denied_freeze_attempt_creates_privileged_access_denied_event(self, package, manuel):
        from apps.audit.models import AuditEvent

        with pytest.raises(services.PackageError):
            services.freeze_package(package, manuel, incoterm="FOB", currency="USD", payment_terms="x")
        assert AuditEvent.objects.filter(action=AuditEvent.Action.PRIVILEGED_ACCESS_DENIED).exists()

    def test_denied_disclosure_attempt_creates_privileged_access_denied_event(self, package, manuel, china_org):
        from apps.audit.models import AuditEvent

        factory_party = Party.objects.create(display_name="Factory Denied Test", hosting_organization=china_org)
        with pytest.raises(governance_services.AuthorizationDenied):
            governance_services.create_disclosure_grant(factory_party, package, china_org, ["manufacturer_name"], "reason", manuel)
        assert AuditEvent.objects.filter(action=AuditEvent.Action.PRIVILEGED_ACCESS_DENIED).exists()
