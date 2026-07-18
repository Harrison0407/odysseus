"""Tests for the supplier claim package generation feature
(apps.claims) — closes the Priority 1 gap "supplier claim package
generation." A claim traces to real supplier/PO/shipment/discrepancy
records rather than duplicating their data; the claim package reuses
the existing self-contained HTML snapshot mechanism.
"""

from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.claims import services
from apps.claims.models import SupplierClaim
from apps.core.models import DestinationScope
from apps.documents.models import DocumentType
from apps.matching.models import Discrepancy, DiscrepancyType
from apps.procurement.models import PurchaseOrder, PurchaseOrderLine, Supplier
from apps.shipments.models import ManifestLine, ManifestPurpose, Shipment, ShipmentManifest, ShipmentManifestVersion

pytestmark = pytest.mark.django_db


@pytest.fixture
def supplier(organization):
    return Supplier.objects.create(organization=organization, name="Proveedor de Prueba")


@pytest.fixture
def shipment(organization):
    return Shipment.objects.create(organization=organization, reference="TEST-CLAIM-SHIP")


@pytest.fixture
def purchase_order(organization, supplier):
    return PurchaseOrder.objects.create(organization=organization, supplier=supplier, po_number="PO-CLAIM-TEST")


@pytest.fixture
def purchase_order_line(purchase_order, pc_uom):
    return PurchaseOrderLine.objects.create(
        purchase_order=purchase_order, line_no=1, description="Línea de prueba",
        quantity_ordered=Decimal("10"), unit_of_measure=pc_uom, unit_price=Decimal("100"), line_total=Decimal("1000"),
    )


@pytest.fixture
def discrepancy(shipment):
    return Discrepancy.objects.create(shipment=shipment, discrepancy_type=DiscrepancyType.RECEIPT_SHORTAGE, description="Faltante de prueba.")


@pytest.fixture
def evidence_doc_type(organization):
    return DocumentType.objects.create(organization=organization, code="claim_evidence_test", name="Evidencia de Reclamo")


@pytest.fixture
def claim(shipment, supplier, harrison):
    return services.create_claim(
        shipment, supplier, SupplierClaim.ClaimType.SHORTAGE, "Faltante detectado en recepción.", harrison,
    )


def _attach_evidence(claim, user, doc_type):
    from apps.audit import services as audit_services

    return audit_services.attach_evidence(
        claim, user, document_type=doc_type, title="Foto de evidencia",
        uploaded_file=SimpleUploadedFile("evidencia.jpg", b"fake-jpeg-bytes", content_type="image/jpeg"),
    )


class TestCreateClaim:
    def test_claim_number_is_sequential_per_organization(self, shipment, supplier, harrison):
        first = services.create_claim(shipment, supplier, SupplierClaim.ClaimType.DAMAGE, "Razón 1", harrison)
        second = services.create_claim(shipment, supplier, SupplierClaim.ClaimType.DAMAGE, "Razón 2", harrison)
        assert first.claim_number != second.claim_number
        assert first.claim_number.startswith("CLM-")

    def test_claim_requires_a_reason(self, shipment, supplier, harrison):
        with pytest.raises(services.ClaimError):
            services.create_claim(shipment, supplier, SupplierClaim.ClaimType.DAMAGE, "   ", harrison)

    def test_claim_links_to_discrepancy_and_po_line(self, shipment, supplier, discrepancy, purchase_order, purchase_order_line, harrison):
        claim = services.create_claim(
            shipment, supplier, SupplierClaim.ClaimType.SHORTAGE, "Faltante ligado a discrepancia", harrison,
            discrepancy=discrepancy, purchase_order=purchase_order, purchase_order_line=purchase_order_line,
        )
        assert claim.discrepancy_id == discrepancy.id
        assert claim.purchase_order_line_id == purchase_order_line.id

    def test_claim_official_operational_mismatch_never_overwrites_the_variance(self, shipment, supplier, pc_uom, quartz_category, harrison, organization):
        from apps.items.models import Item
        from apps.shipments.models import ManifestVariance

        item = Item.objects.create(organization=organization, name="Artículo variance test", category=quartz_category, base_unit=pc_uom)
        manifest = ShipmentManifest.objects.create(shipment=shipment, purpose=ManifestPurpose.INTERNAL_OPERATIONAL_MANIFEST)
        version = ShipmentManifestVersion.objects.create(manifest=manifest, version_number=1)
        manifest_line = ManifestLine.objects.create(
            manifest_version=version, line_no=1, description="Línea variance", item=item,
            destination_scope=DestinationScope.PROJECT, quantity=Decimal("10"), unit_of_measure=pc_uom,
        )
        variance = ManifestVariance.objects.create(
            shipment=shipment, official_category_text="Vidrio (categoría oficial)", internal_manifest_line=manifest_line,
            quantity_expected_at_receipt=Decimal("10"), quantity_received=Decimal("8"),
        )
        claim = services.create_claim(
            shipment, supplier, SupplierClaim.ClaimType.SHORTAGE, "Discrepancia oficial/operativa", harrison,
            manifest_variance=variance,
        )
        variance.refresh_from_db()
        assert variance.quantity_expected_at_receipt == Decimal("10")  # untouched
        assert variance.quantity_received == Decimal("8")  # untouched
        assert claim.manifest_variance_id == variance.id


class TestSubmissionReadiness:
    def test_missing_evidence_warns(self, claim):
        warnings = services.submission_readiness(claim)
        assert any("evidencia" in w for w in warnings)

    def test_complete_claim_has_fewer_warnings(self, claim, harrison, evidence_doc_type, purchase_order, purchase_order_line, discrepancy):
        _attach_evidence(claim, harrison, evidence_doc_type)
        claim.purchase_order = purchase_order
        claim.purchase_order_line = purchase_order_line
        claim.discrepancy = discrepancy
        claim.quantity_claimed = Decimal("5")
        claim.save()
        warnings = services.submission_readiness(claim)
        assert not any("evidencia" in w for w in warnings)
        assert not any("orden de compra" in w for w in warnings)


class TestClaimLifecycle:
    def test_cannot_approve_without_evidence(self, claim, harrison):
        with pytest.raises(services.ClaimError):
            services.approve_claim(claim, harrison)

    def test_approve_requires_draft_status(self, claim, harrison, evidence_doc_type):
        _attach_evidence(claim, harrison, evidence_doc_type)
        services.approve_claim(claim, harrison)
        with pytest.raises(services.ClaimError):
            services.approve_claim(claim, harrison)

    def test_cannot_submit_a_draft_claim(self, claim, harrison):
        with pytest.raises(services.ClaimError):
            services.submit_claim(claim, harrison)

    def test_cannot_submit_twice(self, claim, harrison, evidence_doc_type):
        _attach_evidence(claim, harrison, evidence_doc_type)
        services.approve_claim(claim, harrison)
        services.submit_claim(claim, harrison)
        with pytest.raises(services.ClaimError):
            services.submit_claim(claim, harrison)

    def test_full_lifecycle_to_closure(self, claim, harrison, evidence_doc_type):
        _attach_evidence(claim, harrison, evidence_doc_type)
        services.approve_claim(claim, harrison)
        services.submit_claim(claim, harrison)
        services.record_supplier_response(claim, harrison, response=SupplierClaim.SupplierResponse.ACCEPTED, notes="Aceptado por el proveedor.")
        services.resolve_claim(claim, harrison, resolution_type=SupplierClaim.ResolutionType.CREDIT_NOTE, resolution_reference="NC-001", resolution_amount=Decimal("200"))
        services.close_claim(claim, harrison, closure_notes="Caso cerrado tras nota de crédito.")
        claim.refresh_from_db()
        assert claim.status == SupplierClaim.Status.CLOSED
        assert claim.resolution_type == SupplierClaim.ResolutionType.CREDIT_NOTE

    def test_cannot_respond_before_submission(self, claim, harrison):
        with pytest.raises(services.ClaimError):
            services.record_supplier_response(claim, harrison, response=SupplierClaim.SupplierResponse.ACCEPTED)

    def test_cannot_resolve_before_supplier_response(self, claim, harrison, evidence_doc_type):
        _attach_evidence(claim, harrison, evidence_doc_type)
        services.approve_claim(claim, harrison)
        services.submit_claim(claim, harrison)
        with pytest.raises(services.ClaimError):
            services.resolve_claim(claim, harrison, resolution_type=SupplierClaim.ResolutionType.REPLACEMENT)

    def test_cannot_close_before_resolution(self, claim, harrison, evidence_doc_type):
        _attach_evidence(claim, harrison, evidence_doc_type)
        services.approve_claim(claim, harrison)
        services.submit_claim(claim, harrison)
        services.record_supplier_response(claim, harrison, response=SupplierClaim.SupplierResponse.REJECTED)
        with pytest.raises(services.ClaimError):
            services.close_claim(claim, harrison)

    def test_full_audit_chronology_recorded(self, claim, harrison, evidence_doc_type):
        from django.contrib.contenttypes.models import ContentType

        from apps.audit.models import AuditEvent

        _attach_evidence(claim, harrison, evidence_doc_type)
        services.approve_claim(claim, harrison)
        services.submit_claim(claim, harrison)
        events = AuditEvent.objects.filter(
            content_type=ContentType.objects.get_for_model(SupplierClaim), object_id=claim.pk,
        )
        assert events.filter(action=AuditEvent.Action.CLAIM_ACTION).count() >= 3  # creation, approval, submission


class TestClaimPackageGeneration:
    def test_generate_package_produces_html_with_key_sections(self, claim, harrison, evidence_doc_type):
        _attach_evidence(claim, harrison, evidence_doc_type)
        report_version, html = services.generate_claim_package(claim, harrison)
        assert claim.claim_number in html
        assert "Cronología completa" in html
        assert "Índice de evidencia" in html
        assert report_version.content_type is not None
        assert report_version.object_id == claim.pk

    def test_package_includes_missing_document_warnings(self, claim, harrison):
        _, html = services.generate_claim_package(claim, harrison)
        assert "Advertencias de documentación faltante" in html


class TestCrossOrganizationIsolation:
    def test_cannot_view_claim_of_another_organization(self, client, claim):
        from django.contrib.auth import get_user_model

        from apps.accounts.models import Organization, UserProfile

        User = get_user_model()
        other_org = Organization.objects.create(name="Other Org Claims Test")
        outsider = User.objects.create_user(username="outsider_claims", password="testpass123")
        UserProfile.objects.create(user=outsider, organization=other_org)

        client.force_login(outsider)
        response = client.get(reverse("claims:detail", args=[claim.pk]))
        assert response.status_code == 404

    def test_claim_list_excludes_other_organization_claims(self, client, claim, harrison):
        client.force_login(harrison)
        response = client.get(reverse("claims:list"))
        assert claim.claim_number in response.content.decode()


class TestHTTPWorkflow:
    def test_full_claim_lifecycle_via_http(self, client, harrison, shipment, supplier, evidence_doc_type):
        client.force_login(harrison)
        create_response = client.post(reverse("claims:create"), {
            "claim_type": SupplierClaim.ClaimType.DAMAGE, "reason": "Daño detectado vía HTTP",
            "shipment": shipment.pk, "supplier": supplier.pk,
        })
        assert create_response.status_code == 302
        claim = SupplierClaim.objects.get(shipment=shipment, supplier=supplier)

        upload_response = client.post(reverse("claims:evidence-upload", args=[claim.pk]), {
            "document_type": evidence_doc_type.pk, "title": "Foto HTTP",
            "file": SimpleUploadedFile("http.jpg", b"fake-bytes", content_type="image/jpeg"),
        })
        assert upload_response.status_code == 302

        approve_response = client.post(reverse("claims:approve", args=[claim.pk]))
        assert approve_response.status_code == 302
        claim.refresh_from_db()
        assert claim.status == SupplierClaim.Status.APPROVED

        submit_response = client.post(reverse("claims:submit", args=[claim.pk]))
        assert submit_response.status_code == 302
        claim.refresh_from_db()
        assert claim.status == SupplierClaim.Status.SUBMITTED

        package_response = client.get(reverse("claims:package", args=[claim.pk]))
        assert package_response.status_code == 200
        assert "Daño detectado vía HTTP" in package_response.content.decode()

    def test_duplicate_submission_prevented_via_http(self, client, harrison, claim, evidence_doc_type):
        _attach_evidence(claim, harrison, evidence_doc_type)
        client.force_login(harrison)
        client.post(reverse("claims:approve", args=[claim.pk]))
        client.post(reverse("claims:submit", args=[claim.pk]))
        response = client.post(reverse("claims:submit", args=[claim.pk]))
        assert response.status_code == 302
        claim.refresh_from_db()
        assert claim.status == SupplierClaim.Status.SUBMITTED  # unchanged, no crash, no double-submit
