"""Adversarial regression coverage for the authorization-foundation remediation."""

import datetime
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Organization, UserProfile
from apps.audit import services as audit_services
from apps.audit.models import AuditEvent, EvidenceBundle
from apps.core.storage import document_storage
from apps.documents.models import Document, DocumentType, DocumentVersion
from apps.governance import services as governance_services
from apps.governance.models import ChangeRequest, Classification, Party, PartyMembership, RiskFlag, VisibilityMode
from apps.procurement import services as procurement_services
from apps.procurement.models import ClientQuote, ProcurementPackage, PurchaseOrder, PurchaseOrderLine, Supplier, VerificationAssertion
from apps.shipments.models import ManifestLine, ManifestLineSource, ManifestPurpose, Shipment, ShipmentManifest, ShipmentManifestVersion

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def package(organization):
    return ProcurementPackage.objects.create(
        organization=organization, code="foundation-remediation-a", name="FOUNDATION_SENTINEL_PACKAGE_A",
    )


@pytest.fixture
def package_b(organization):
    return ProcurementPackage.objects.create(
        organization=organization, code="foundation-remediation-b", name="FOUNDATION_SENTINEL_PACKAGE_B",
    )


def assign_user(package, user, role_code, actor=None):
    party = Party.objects.create(
        party_type=Party.PartyType.INDIVIDUAL,
        display_name=f"{user.username}-{role_code}-{package.code}",
        organization=user.profile.organization,
        hosting_organization=package.organization,
    )
    PartyMembership.objects.create(party=party, user=user)
    assignment = procurement_services.assign_package_role(package, party, role_code, actor or user)
    return party, assignment


@pytest.fixture
def internal_actor(package, markeris):
    assign_user(package, markeris, "china_procurement_operator")
    return markeris


@pytest.fixture
def buyer(package, other_org_user):
    assign_user(package, other_org_user, "buyer")
    return other_org_user


class TestPackageAuthorization:
    def test_same_org_unrelated_user_cannot_list_or_open_package(self, client, package, manuel):
        client.force_login(manuel)
        detail = client.get(reverse("procurement:package-detail", args=[package.pk]))
        listing = client.get(reverse("procurement:package-list"))
        assert detail.status_code == 404
        assert "FOUNDATION_SENTINEL_PACKAGE_A" not in detail.content.decode()
        assert "FOUNDATION_SENTINEL_PACKAGE_A" not in listing.content.decode()

    def test_different_org_unrelated_user_cannot_list_or_open_package(self, client, package, other_org_user):
        client.force_login(other_org_user)
        detail = client.get(reverse("procurement:package-detail", args=[package.pk]))
        listing = client.get(reverse("procurement:package-list"))
        assert detail.status_code == 404
        assert "FOUNDATION_SENTINEL_PACKAGE_A" not in detail.content.decode()
        assert "FOUNDATION_SENTINEL_PACKAGE_A" not in listing.content.decode()

    def test_existing_executive_authority_retains_hosted_administration(self, client, package, harrison):
        client.force_login(harrison)
        assert client.get(reverse("procurement:package-detail", args=[package.pk])).status_code == 200


class TestQuoteAndAssertionProjection:
    def test_external_buyer_never_receives_draft_quote_but_internal_actor_does(self, client, package, internal_actor, buyer):
        draft_sentinel = "DRAFT_QUOTE_SENTINEL_9C31"
        procurement_services.create_internal_commercial_sheet(
            package, internal_actor, factory_price=4321.1234, markup_value=8765.4321,
            recommended_sell_price=9876.5432,
        )
        quote = procurement_services.create_client_quote(
            package, internal_actor, visible_seller_party=None, product_description=draft_sentinel,
            quantity=1, sell_price=10,
        )
        client.force_login(buyer)
        response = client.get(reverse("procurement:package-detail", args=[package.pk]))
        assert draft_sentinel not in response.content.decode()
        assert "4321.1234" not in response.content.decode()
        assert "8765.4321" not in response.content.decode()
        assert "9876.5432" not in response.content.decode()
        assert list(response.context["client_quotes"]) == []

        client.force_login(internal_actor)
        response = client.get(reverse("procurement:package-detail", args=[package.pk]))
        assert draft_sentinel in response.content.decode()
        assert str(list(response.context["internal_sheets"])[0].factory_price) == "4321.1234"

        ClientQuote.objects.filter(pk=quote.pk).update(status=ClientQuote.Status.APPROVED)
        client.force_login(buyer)
        assert draft_sentinel in client.get(reverse("procurement:package-detail", args=[package.pk])).content.decode()

    def test_expired_and_revoked_assertions_are_absent(self, client, package, internal_actor, buyer):
        expired = procurement_services.create_verification_assertion(
            package, "expired_assertion", "EXPIRED_ASSERTION_SENTINEL_2B7E", internal_actor,
            valid_until=timezone.now() - datetime.timedelta(seconds=1),
        )
        active = procurement_services.create_verification_assertion(
            package, "active_assertion", "ACTIVE_ASSERTION_SENTINEL_2B7E", internal_actor,
            valid_until=timezone.now() + datetime.timedelta(days=1),
        )
        client.force_login(buyer)
        body = client.get(reverse("procurement:package-detail", args=[package.pk])).content.decode()
        assert expired.client_visible_wording not in body
        assert active.client_visible_wording in body

        with pytest.raises(procurement_services.PackageError):
            procurement_services.revoke_verification_assertion(active, buyer)
        assert AuditEvent.objects.filter(
            action=AuditEvent.Action.PRIVILEGED_ACCESS_DENIED, object_id=active.pk,
        ).exists()
        active.refresh_from_db()
        assert not active.is_revoked

        procurement_services.revoke_verification_assertion(active, internal_actor)
        client.force_login(buyer)
        body = client.get(reverse("procurement:package-detail", args=[package.pk])).content.decode()
        assert active.client_visible_wording not in body


class TestDisclosureAndVisibilityModes:
    def test_live_field_projection_disappears_after_revocation_and_expiry(self, client, package, internal_actor, buyer):
        factory = Party.objects.create(
            display_name="DISCLOSED_FACTORY_SENTINEL_41AA", hosting_organization=package.organization,
            is_hidden_by_default=True,
        )
        assignment = package.role_assignments.get(role_code="china_procurement_operator")
        governance_services.grant_capability(
            "AUTHORIZE_DISCLOSURE", user=internal_actor, role_assignment=assignment, package=package,
        )
        grant = governance_services.create_disclosure_grant(
            factory, package, buyer.profile.organization, ["manufacturer_name"], "approved disclosure", internal_actor,
        )
        client.force_login(buyer)
        body = client.get(reverse("procurement:package-detail", args=[package.pk])).content.decode()
        assert "DISCLOSED_FACTORY_SENTINEL_41AA" in body
        assert "factory_address" not in body

        governance_services.revoke_disclosure_grant(grant, internal_actor)
        body = client.get(reverse("procurement:package-detail", args=[package.pk])).content.decode()
        assert "DISCLOSED_FACTORY_SENTINEL_41AA" not in body

        governance_services.create_disclosure_grant(
            factory, package, buyer.profile.organization, ["manufacturer_name"], "expired", internal_actor,
            expires_at=timezone.now() - datetime.timedelta(seconds=1),
        )
        body = client.get(reverse("procurement:package-detail", args=[package.pk])).content.decode()
        assert "DISCLOSED_FACTORY_SENTINEL_41AA" not in body

    def test_unauthorized_and_cross_package_revocation_are_denied_and_audited(
        self, client, package, package_b, internal_actor, buyer,
    ):
        factory = Party.objects.create(display_name="Factory", hosting_organization=package.organization)
        assignment = package.role_assignments.get(role_code="china_procurement_operator")
        governance_services.grant_capability(
            "AUTHORIZE_DISCLOSURE", user=internal_actor, role_assignment=assignment, package=package,
        )
        grant = governance_services.create_disclosure_grant(
            factory, package, buyer.profile.organization, ["manufacturer_name"], "reason", internal_actor,
        )
        client.force_login(buyer)
        response = client.post(reverse("procurement:disclosure-grant-revoke", args=[grant.pk]))
        assert response.status_code == 404
        grant.refresh_from_db()
        assert grant.revoked_at is None
        _, other_assignment = assign_user(package_b, buyer, "buyer_approver")
        governance_services.grant_capability(
            "AUTHORIZE_DISCLOSURE", user=buyer, role_assignment=other_assignment, package=package_b,
        )
        with pytest.raises(governance_services.AuthorizationDenied):
            governance_services.revoke_disclosure_grant(grant, buyer)
        grant.refresh_from_db()
        assert grant.revoked_at is None
        assert AuditEvent.objects.filter(
            action=AuditEvent.Action.PRIVILEGED_ACCESS_DENIED, object_id=grant.pk,
        ).exists()

    def test_visibility_modes_are_policy_metadata_and_neither_bypasses_disclosure(self, client, organization, buyer):
        confidential = buyer.party_memberships.first().party.role_assignments.first().package
        transparent = ProcurementPackage.objects.create(
            organization=organization, code="transparent-policy-test", name="Transparent policy test",
            visibility_mode=VisibilityMode.CONTROLLED_TRANSPARENCY,
        )
        assign_user(transparent, buyer, "buyer")
        sentinel = "MODE_GOVERNED_FACTORY_SENTINEL_E18D"
        factory = Party.objects.create(display_name=sentinel, hosting_organization=organization)

        client.force_login(buyer)
        assert sentinel not in client.get(reverse("procurement:package-detail", args=[confidential.pk])).content.decode()
        assert sentinel not in client.get(reverse("procurement:package-detail", args=[transparent.pk])).content.decode()

        approver = User.objects.create_user(username="visibility_discloser", password="testpass123")
        UserProfile.objects.create(user=approver, organization=organization)
        _, assignment = assign_user(transparent, approver, "china_procurement_operator")
        governance_services.grant_capability(
            "AUTHORIZE_DISCLOSURE", user=approver, role_assignment=assignment, package=transparent,
        )
        governance_services.create_disclosure_grant(
            factory, transparent, buyer.profile.organization, ["manufacturer_name"], "transparent agreement", approver,
        )
        assert sentinel in client.get(reverse("procurement:package-detail", args=[transparent.pk])).content.decode()


class TestEvidenceIsolation:
    def test_legitimate_buyer_cannot_create_evidence_bundle_via_http(self, client, package, buyer):
        client.force_login(buyer)
        response = client.post(
            reverse("procurement:evidence-bundle-create", args=[package.pk]),
            {"bundle_type": EvidenceBundle.BundleType.QUALITY_CONTROL, "minimum_count": 1},
        )
        assert response.status_code == 404
        assert not EvidenceBundle.objects.filter(
            content_type=ContentType.objects.get_for_model(package), object_id=package.pk,
        ).exists()
        assert AuditEvent.objects.filter(
            action=AuditEvent.Action.PRIVILEGED_ACCESS_DENIED, actor=buyer,
        ).exists()

    def test_authorization_is_derived_from_bundle_target_and_denials_are_durable(
        self, package, package_b, internal_actor, manuel,
    ):
        bundle = audit_services.create_evidence_bundle(
            package, EvidenceBundle.BundleType.QUALITY_CONTROL, internal_actor,
        )
        document_type = DocumentType.objects.create(organization=package.organization, code="evidence-remediation", name="Evidence")
        document = Document.objects.create(
            organization=package.organization, document_type=document_type, title="EVIDENCE_TARGET_SENTINEL", package=package,
        )
        with pytest.raises(audit_services.EvidenceBundleError):
            audit_services.add_evidence_item(bundle, manuel, document=document)
        assert bundle.items.count() == 0
        assert AuditEvent.objects.filter(action=AuditEvent.Action.PRIVILEGED_ACCESS_DENIED, object_id=bundle.pk).exists()

        uploader = User.objects.create_user(username="evidence_scoped_uploader", password="testpass123")
        UserProfile.objects.create(user=uploader, organization=package.organization)
        assign_user(package, uploader, "installer")
        item = audit_services.add_evidence_item(bundle, uploader, document=document)

        verifier = User.objects.create_user(username="wrong_package_verifier", password="testpass123")
        UserProfile.objects.create(user=verifier, organization=package.organization)
        assign_user(package_b, verifier, "inspector")
        with pytest.raises(audit_services.EvidenceBundleError):
            audit_services.verify_evidence_item(item, verifier, expected_package_id=package_b.pk)
        item.refresh_from_db()
        assert item.review_status == "pending"
        assert AuditEvent.objects.filter(
            action=AuditEvent.Action.PRIVILEGED_ACCESS_DENIED, actor=verifier,
        ).exists()

    def test_direct_cross_package_http_identifier_is_non_disclosing(
        self, client, package, package_b, internal_actor,
    ):
        bundle = audit_services.create_evidence_bundle(package, EvidenceBundle.BundleType.QUALITY_CONTROL, internal_actor)
        verifier = User.objects.create_user(username="http_wrong_package_verifier", password="testpass123")
        UserProfile.objects.create(user=verifier, organization=package.organization)
        assign_user(package_b, verifier, "inspector")
        client.force_login(verifier)
        response = client.post(reverse("procurement:evidence-item-add", args=[bundle.pk]))
        assert response.status_code == 404
        assert "FOUNDATION_SENTINEL_PACKAGE_A" not in response.content.decode()


class TestChangeRequestsAndRisk:
    def test_decisions_require_field_capability_and_hold_tracks_all_pending(self, client, package, buyer, internal_actor):
        package.is_frozen = True
        package.frozen_snapshot = {"roles": {"seller_of_record": "old", "exporter_of_record": "old"}}
        package.save()
        first = governance_services.request_change(package, "seller_of_record", "new-1", "reason", buyer)
        second = governance_services.request_change(package, "exporter_of_record", "new-2", "reason", buyer)

        with pytest.raises(governance_services.AuthorizationDenied):
            governance_services.reject_change_request(first, buyer)
        first.refresh_from_db()
        assert first.status == ChangeRequest.Status.PENDING
        assert AuditEvent.objects.filter(
            action=AuditEvent.Action.PRIVILEGED_ACCESS_DENIED, object_id=first.pk,
        ).exists()

        assignment = package.role_assignments.get(role_code="china_procurement_operator")
        governance_services.grant_capability(
            "APPROVE_ROLE_CHANGE", user=internal_actor, role_assignment=assignment, package=package,
        )
        governance_services.approve_change_request(first, internal_actor)
        package.refresh_from_db()
        assert package.is_on_hold
        governance_services.reject_change_request(second, internal_actor)
        package.refresh_from_db()
        assert not package.is_on_hold

        client.force_login(internal_actor)
        response = client.post(reverse("procurement:change-request-decide", args=[second.pk, "unsupported"]))
        assert response.status_code == 404
        second.refresh_from_db()
        assert second.status == ChangeRequest.Status.REJECTED

    def test_risk_flags_require_scoped_authority_and_guard_state(self, package, package_b, buyer, internal_actor):
        with pytest.raises(governance_services.AuthorizationDenied):
            governance_services.raise_risk_flag(package, RiskFlag.Level.HIGH_RISK, ["SENTINEL_RISK"], user=buyer)
        assert not package.risk_flags.exists()
        assert AuditEvent.objects.filter(action=AuditEvent.Action.PRIVILEGED_ACCESS_DENIED, actor=buyer).exists()

        _, other_assignment = assign_user(package_b, buyer, "technical_authority")
        governance_services.grant_capability(
            "MANAGE_RISK_FLAGS", user=buyer, role_assignment=other_assignment, package=package_b,
        )
        with pytest.raises(governance_services.AuthorizationDenied):
            governance_services.raise_risk_flag(package, RiskFlag.Level.HIGH_RISK, ["CROSS_PACKAGE"], user=buyer)

        assignment = package.role_assignments.get(role_code="china_procurement_operator")
        governance_services.grant_capability(
            "MANAGE_RISK_FLAGS", user=internal_actor, role_assignment=assignment, package=package,
        )
        flag = governance_services.raise_risk_flag(
            package, RiskFlag.Level.HIGH_RISK, ["AUTHORIZED_RISK"], user=internal_actor,
        )
        package.refresh_from_db()
        assert package.is_on_hold
        with pytest.raises(governance_services.AuthorizationDenied):
            governance_services.raise_risk_flag(
                package, RiskFlag.Level.HIGH_RISK, ["AUTHORIZED_RISK"], user=internal_actor,
            )
        governance_services.resolve_risk_flag(flag, internal_actor)
        package.refresh_from_db()
        assert not package.is_on_hold
        with pytest.raises(governance_services.AuthorizationDenied):
            governance_services.resolve_risk_flag(flag, internal_actor)


class TestSeparationOfDuties:
    def test_preparer_cannot_self_approve_even_with_both_capabilities(self, package, internal_actor):
        quote = procurement_services.create_client_quote(
            package, internal_actor, visible_seller_party=None, product_description="SOD_SENTINEL", quantity=1, sell_price=2,
        )
        assignment = package.role_assignments.get(role_code="china_procurement_operator")
        governance_services.grant_capability(
            "APPROVE_CLIENT_QUOTE", user=internal_actor, role_assignment=assignment, package=package,
        )
        with pytest.raises(procurement_services.PackageError):
            procurement_services.approve_client_quote(quote, internal_actor)
        quote.refresh_from_db()
        assert quote.status == ClientQuote.Status.DRAFT
        assert AuditEvent.objects.filter(
            action=AuditEvent.Action.PRIVILEGED_ACCESS_DENIED, object_id=quote.pk,
        ).exists()

        approver = User.objects.create_user(username="independent_quote_approver", password="testpass123")
        UserProfile.objects.create(user=approver, organization=package.organization)
        _, approver_assignment = assign_user(package, approver, "buyer_approver")
        governance_services.grant_capability(
            "APPROVE_CLIENT_QUOTE", user=approver, role_assignment=approver_assignment, package=package,
        )
        approved = procurement_services.approve_client_quote(quote, approver)
        assert approved.status == ClientQuote.Status.APPROVED


class TestDocumentIsolation:
    def test_classified_document_metadata_and_bytes_are_package_scoped(
        self, client, tmp_path, monkeypatch, package, internal_actor, manuel, other_org_user,
    ):
        monkeypatch.setattr(document_storage, "root", tmp_path)
        doc_type = DocumentType.objects.create(organization=package.organization, code="private-remediation", name="Private")
        stored = document_storage.save(SimpleUploadedFile("PRIVATE_FILENAME_SENTINEL.pdf", b"PRIVATE_BYTES_SENTINEL_774A"), "PRIVATE_FILENAME_SENTINEL.pdf")
        document = Document.objects.create(
            organization=package.organization, document_type=doc_type, title="PRIVATE_METADATA_SENTINEL_774A",
            package=package, classification=Classification.SOURCE_PRIVATE,
        )
        version = DocumentVersion.objects.create(
            document=document, version_number=1, stored_name=stored["stored_name"],
            original_filename=stored["original_filename"], sha256=stored["sha256"],
            size_bytes=stored["size_bytes"], mime_type="application/pdf",
        )
        with mock.patch.object(document_storage, "open", wraps=document_storage.open) as storage_open:
            for unauthorized in (manuel, other_org_user):
                client.force_login(unauthorized)
                detail = client.get(reverse("documents:detail", args=[document.pk]))
                download = client.get(reverse("documents:download", args=[document.pk, version.pk]))
                listing = client.get(reverse("documents:list"))
                assert detail.status_code == 404
                assert download.status_code == 404
                assert "PRIVATE_METADATA_SENTINEL_774A" not in listing.content.decode()
                assert "PRIVATE_FILENAME_SENTINEL.pdf" not in download.content.decode()
            assert storage_open.call_count == 0

            client.force_login(internal_actor)
            assert client.get(reverse("documents:detail", args=[document.pk])).status_code == 200
            response = client.get(reverse("documents:download", args=[document.pk, version.pk]))
            assert response.status_code == 200
            assert b"".join(response.streaming_content) == b"PRIVATE_BYTES_SENTINEL_774A"
            assert storage_open.call_count == 1


class TestCommercialAndManifestAPIs:
    @pytest.fixture
    def protected_records(self, package, internal_actor, pc_uom):
        supplier = Supplier.objects.create(
            organization=package.organization, name="FACTORY_IDENTITY_SENTINEL_5D0C",
            address="FACTORY_ADDRESS_SENTINEL_5D0C",
        )
        po = PurchaseOrder.objects.create(
            organization=package.organization, supplier=supplier, po_number="UPSTREAM_PO_SENTINEL_5D0C",
            package=package, po_kind=PurchaseOrder.POKind.UPSTREAM_FACTORY,
            classification=Classification.SOURCE_PRIVATE, total_amount=987654,
        )
        po_line = PurchaseOrderLine.objects.create(
            purchase_order=po, line_no=1, description="INTERNAL_COST_SENTINEL_5D0C",
            quantity_ordered=1, unit_of_measure=pc_uom, unit_price=4321, line_total=4321,
        )
        shipment = Shipment.objects.create(organization=package.organization, reference="SHIPMENT_SENTINEL_5D0C")
        manifest = ShipmentManifest.objects.create(shipment=shipment, purpose=ManifestPurpose.INTERNAL_OPERATIONAL_MANIFEST)
        version = ShipmentManifestVersion.objects.create(manifest=manifest, version_number=1)
        line = ManifestLine.objects.create(
            manifest_version=version, line_no=1, description="MANIFEST_SENTINEL_5D0C",
            reference_on_document="REF_SENTINEL_5D0C", quantity=1, unit_of_measure=pc_uom,
        )
        ManifestLineSource.objects.create(manifest_line=line, purchase_order_line=po_line)
        return po, line

    def test_unrelated_users_cannot_discover_commercial_or_manifest_records(
        self, client, protected_records, manuel, other_org_user,
    ):
        po, line = protected_records
        sentinels = ["UPSTREAM_PO_SENTINEL_5D0C", "MANIFEST_SENTINEL_5D0C", "REF_SENTINEL_5D0C", "4321"]
        for unauthorized in (manuel, other_org_user):
            client.force_login(unauthorized)
            po_list = client.get("/api/v1/purchase-orders/")
            po_filtered = client.get("/api/v1/purchase-orders/?po_number=UPSTREAM_PO_SENTINEL_5D0C")
            po_detail = client.get(f"/api/v1/purchase-orders/{po.pk}/")
            line_list = client.get("/api/v1/manifest-lines/")
            line_filtered = client.get("/api/v1/manifest-lines/?description=MANIFEST_SENTINEL_5D0C")
            line_detail = client.get(f"/api/v1/manifest-lines/{line.pk}/")
            assert po_list.json()["count"] == 0
            assert po_filtered.json()["count"] == 0
            assert line_list.json()["count"] == 0
            assert line_filtered.json()["count"] == 0
            assert po_detail.status_code == 404
            assert line_detail.status_code == 404
            combined = b"".join([
                po_list.content, po_filtered.content, po_detail.content,
                line_list.content, line_filtered.content, line_detail.content,
            ]).decode()
            for sentinel in sentinels:
                assert sentinel not in combined

    def test_restricted_buyer_is_denied_and_internal_actor_retains_access(
        self, client, protected_records, buyer, internal_actor,
    ):
        po, line = protected_records
        client.force_login(buyer)
        assert client.get("/api/v1/purchase-orders/").json()["count"] == 0
        assert client.get("/api/v1/manifest-lines/").json()["count"] == 0

        client.force_login(internal_actor)
        po_response = client.get("/api/v1/purchase-orders/")
        line_response = client.get("/api/v1/manifest-lines/")
        assert "UPSTREAM_PO_SENTINEL_5D0C" in po_response.content.decode()
        assert "MANIFEST_SENTINEL_5D0C" in line_response.content.decode()
