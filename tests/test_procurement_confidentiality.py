"""Tests for the ProcurementPackage commercial-layer lifecycle:
Factory RFQ/Quote, Internal Commercial Sheet, Client Quote, package
freeze, and Change Requests. Confidentiality/visibility-specific HTTP
and cross-classification tests live in test_confidentiality_http.py;
this file covers the underlying service-layer mechanics and separation
of duties.
"""

import datetime

import pytest

from apps.accounts.models import Organization
from apps.governance import services as governance_services
from apps.governance.models import Classification, Party, PartyMembership, VisibilityMode
from apps.procurement import services
from apps.procurement.models import ClientQuote, ProcurementPackage, Supplier

pytestmark = pytest.mark.django_db


@pytest.fixture
def china_org():
    return Organization.objects.create(name="China Trading Co PC Test", default_currency="USD")


@pytest.fixture
def hidden_factory_supplier(china_org):
    return Supplier.objects.create(organization=china_org, name="Hidden Tile Factory Test", country="China", address="Secret Industrial Zone, Foshan")


@pytest.fixture
def factory_party(china_org, hidden_factory_supplier):
    return Party.objects.create(
        party_type=Party.PartyType.ORGANIZATION, display_name="Hidden Tile Factory Test",
        supplier=hidden_factory_supplier, hosting_organization=china_org, is_hidden_by_default=True,
    )


@pytest.fixture
def trading_co_party(china_org):
    return Party.objects.create(
        party_type=Party.PartyType.ORGANIZATION, display_name="China Trading Co", organization=china_org,
        hosting_organization=china_org,
    )


@pytest.fixture
def package(china_org):
    return services.create_package(china_org, "pkg-pc-test", "Test Package", None)


@pytest.fixture
def china_ops_user(china_org, package, trading_co_party, harrison):
    PartyMembership.objects.create(party=trading_co_party, user=harrison)
    services.assign_package_role(package, trading_co_party, "china_procurement_operator", harrison)
    return harrison


class TestPackageDefaults:
    def test_default_visibility_mode_is_controlled_confidentiality(self, package):
        assert package.visibility_mode == VisibilityMode.CONTROLLED_CONFIDENTIALITY

    def test_controlled_transparency_mode_supported(self, china_org):
        transparent_package = services.create_package(
            china_org, "pkg-transparent-test", "Transparent Package", None, visibility_mode=VisibilityMode.CONTROLLED_TRANSPARENCY,
        )
        assert transparent_package.visibility_mode == VisibilityMode.CONTROLLED_TRANSPARENCY


class TestFactoryQuoteSubmission:
    def test_unauthorized_user_cannot_submit_factory_quote(self, package, hidden_factory_supplier, factory_party, manuel):
        with pytest.raises(services.PackageError):
            services.submit_factory_quote(package, factory_party, hidden_factory_supplier, manuel, reference="FQ-001")

    def test_china_ops_user_can_submit_factory_quote(self, package, hidden_factory_supplier, factory_party, china_ops_user):
        quotation = services.submit_factory_quote(
            package, factory_party, hidden_factory_supplier, china_ops_user, reference="FQ-001", total_amount=10000,
        )
        assert quotation.package == package
        assert quotation.classification == "source_private"
        assert quotation.factory_party == factory_party


class TestInternalCommercialSheetAndClientQuoteSeparation:
    def test_prepare_and_approve_client_quote_requires_separate_capability(self, package, china_ops_user, trading_co_party, manuel):
        sheet = services.create_internal_commercial_sheet(
            package, china_ops_user, factory_price=10, inland_transport=1, freight=2, markup_value=5,
            recommended_sell_price=20,
        )
        assert sheet.landed_cost == 13
        assert sheet.margin_amount == 7

        quote = services.create_client_quote(
            package, china_ops_user, visible_seller_party=trading_co_party, product_description="Ceramic tile",
            quantity=1000, sell_price=20, source_internal_sheet=sheet,
        )
        assert quote.status == ClientQuote.Status.DRAFT

        # The preparer (china_ops_user) does NOT automatically hold
        # APPROVE_CLIENT_QUOTE — role membership alone never grants it.
        with pytest.raises(services.PackageError):
            services.approve_client_quote(quote, china_ops_user)

        # A distinct approver, explicitly granted the capability, can.
        buyer_approver_party = Party.objects.create(
            party_type=Party.PartyType.INDIVIDUAL, display_name="Buyer Approver Test",
            hosting_organization=package.organization,
        )
        PartyMembership.objects.create(party=buyer_approver_party, user=manuel)
        assignment = services.assign_package_role(package, buyer_approver_party, "buyer_approver", china_ops_user)
        governance_services.grant_capability("APPROVE_CLIENT_QUOTE", user=manuel, role_assignment=assignment, package=package)

        approved = services.approve_client_quote(quote, manuel)
        assert approved.status == ClientQuote.Status.APPROVED
        assert approved.approved_by == manuel

    def test_client_quote_never_carries_internal_cost_fields(self, package, china_ops_user, trading_co_party):
        sheet = services.create_internal_commercial_sheet(package, china_ops_user, factory_price=10, markup_value=5, recommended_sell_price=20)
        quote = services.create_client_quote(
            package, china_ops_user, visible_seller_party=trading_co_party, product_description="Ceramic tile",
            quantity=1000, sell_price=20, source_internal_sheet=sheet,
        )
        assert not hasattr(quote, "factory_price")
        assert not hasattr(quote, "markup_value")
        assert not hasattr(quote, "margin_amount")


class TestPackageFreezeAndChangeRequest:
    def test_freeze_requires_capability(self, package, manuel):
        with pytest.raises(services.PackageError):
            services.freeze_package(package, manuel, incoterm="FOB", currency="USD", payment_terms="30/70")

    def test_freeze_snapshots_roles_and_terms(self, package, china_ops_user, trading_co_party, factory_party):
        services.assign_package_role(package, factory_party, "production_factory", china_ops_user)
        governance_services.grant_capability(
            "APPROVE_GATE", user=china_ops_user,
            role_assignment=package.role_assignments.filter(role_code="china_procurement_operator").first(),
            package=package,
        )
        frozen = services.freeze_package(package, china_ops_user, incoterm="FOB", currency="USD", payment_terms="30/70 deposit/balance")
        assert frozen.is_frozen
        assert frozen.frozen_snapshot["incoterm"] == "FOB"
        assert frozen.frozen_snapshot["roles"]["production_factory"] == str(factory_party.id)

    def test_change_request_requires_frozen_package(self, package, china_ops_user):
        with pytest.raises(governance_services.AuthorizationDenied):
            governance_services.request_change(package, "production_factory", "new-value", "test reason", china_ops_user)

    def test_change_request_puts_package_on_hold_and_requires_approval_capability(self, package, china_ops_user, factory_party, manuel):
        governance_services.grant_capability(
            "APPROVE_GATE", user=china_ops_user,
            role_assignment=package.role_assignments.filter(role_code="china_procurement_operator").first(),
            package=package,
        )
        services.freeze_package(package, china_ops_user, incoterm="FOB", currency="USD", payment_terms="deposit")
        change_request = governance_services.request_change(
            package, "production_factory", "new-factory-party-id", "Factory changed capacity", china_ops_user,
        )
        package.refresh_from_db()
        assert package.is_on_hold

        with pytest.raises(governance_services.AuthorizationDenied):
            governance_services.approve_change_request(change_request, manuel)

        assignment = package.role_assignments.filter(role_code="china_procurement_operator").first()
        governance_services.grant_capability("APPROVE_ROLE_CHANGE", user=china_ops_user, role_assignment=assignment, package=package)
        approved = governance_services.approve_change_request(change_request, china_ops_user)
        assert approved.status == "approved"
        package.refresh_from_db()
        assert not package.is_on_hold
        assert package.frozen_snapshot["roles"]["production_factory"] == "new-factory-party-id"

    def test_visibility_mode_change_requires_change_request(self, package, china_ops_user):
        governance_services.grant_capability(
            "APPROVE_GATE", user=china_ops_user,
            role_assignment=package.role_assignments.filter(role_code="china_procurement_operator").first(),
            package=package,
        )
        services.freeze_package(package, china_ops_user, incoterm="FOB", currency="USD", payment_terms="deposit")
        change_request = governance_services.request_change(
            package, "visibility_mode", VisibilityMode.CONTROLLED_TRANSPARENCY, "Client requested transparency", china_ops_user,
        )
        assignment = package.role_assignments.filter(role_code="china_procurement_operator").first()
        governance_services.grant_capability("APPROVE_VISIBILITY_CHANGE", user=china_ops_user, role_assignment=assignment, package=package)
        governance_services.approve_change_request(change_request, china_ops_user)
        package.refresh_from_db()
        assert package.visibility_mode == VisibilityMode.CONTROLLED_TRANSPARENCY
        assert package.visibility_mode_version == 2
