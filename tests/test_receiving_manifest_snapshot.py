"""Tests for the detailed internal receiving manifest snapshot (spec
13A.8) — apps.reports.views.receiving_manifest_snapshot. Closes the
Priority 0 KNOWN_LIMITATIONS gap "Detailed internal receiving manifest,
exact spec 13A.8 layout."
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.core.models import DestinationScope
from apps.items.models import Item
from apps.matching.models import Discrepancy, DiscrepancyType
from apps.receiving.models import Inspection, Receipt, ReceiptLine, ReleasePacket
from apps.shipments.models import Container, ManifestLine, ManifestPurpose, Shipment, ShipmentManifest, ShipmentManifestVersion

pytestmark = pytest.mark.django_db


@pytest.fixture
def shipment(organization):
    return Shipment.objects.create(organization=organization, reference="TEST-MANIFEST-SNAPSHOT")


@pytest.fixture
def internal_manifest_line(shipment, pc_uom, quartz_category, organization):
    item = Item.objects.create(organization=organization, name="Artículo de prueba", category=quartz_category, base_unit=pc_uom)
    manifest = ShipmentManifest.objects.create(shipment=shipment, purpose=ManifestPurpose.INTERNAL_OPERATIONAL_MANIFEST)
    version = ShipmentManifestVersion.objects.create(manifest=manifest, version_number=1)
    return ManifestLine.objects.create(
        manifest_version=version, line_no=1, description="Línea de prueba", item=item,
        destination_scope=DestinationScope.PROJECT, quantity=Decimal("10"), unit_of_measure=pc_uom,
    )


@pytest.fixture
def receipt(shipment):
    release_packet = ReleasePacket.objects.create(shipment=shipment)
    container = Container.objects.create(shipment=shipment, container_number="TESTCONT-SNAPSHOT")
    return Receipt.objects.create(release_packet=release_packet, container=container, status=Receipt.Status.RECEIVED_WITH_EXCEPTIONS)


@pytest.fixture
def receipt_line(receipt, internal_manifest_line):
    return ReceiptLine.objects.create(
        receipt=receipt, manifest_line=internal_manifest_line, quantity_expected=Decimal("10"),
        quantity_received=Decimal("8"), quantity_damaged=Decimal("2"),
        exception_type=ReceiptLine.ExceptionType.DAMAGE, notes="Daño detectado al abrir el contenedor.",
    )


def test_receiving_manifest_snapshot_renders_all_ten_sections(client, harrison, receipt, receipt_line):
    client.force_login(harrison)
    response = client.get(reverse("reports:receiving-manifest-snapshot", args=[receipt.pk]))
    assert response.status_code == 200
    content = response.content.decode()
    for marker in [
        "1. Encabezado", "2. Resumen Oficial", "3. Manifiesto Operativo Interno completo",
        "4. Recepción física", "5. Detalle de recepción por línea", "6. Inspecciones de esta recepción",
        "7. Cuarentena y daños", "8. Discrepancias", "9. Plan de recepción",
        "10. Variaciones oficial vs. operativo que requieren atención",
    ]:
        assert marker in content
    assert "TESTCONT-SNAPSHOT" in content
    assert "Daño detectado al abrir el contenedor." in content


def test_receiving_manifest_snapshot_shows_inspection_and_discrepancy_detail(client, harrison, shipment, receipt, receipt_line):
    Inspection.objects.create(
        receipt=receipt, receipt_line=receipt_line, inspector=harrison,
        sample_size=5, population_size=20, passed=False, findings="2 unidades con grietas visibles.",
    )
    Discrepancy.objects.create(
        shipment=shipment, discrepancy_type=DiscrepancyType.RECEIPT_SHORTAGE,
        description="Faltante detectado en la línea de prueba.",
    )
    client.force_login(harrison)
    response = client.get(reverse("reports:receiving-manifest-snapshot", args=[receipt.pk]))
    content = response.content.decode()
    assert "2 unidades con grietas visibles." in content
    assert "Faltante detectado en la línea de prueba." in content


def test_receiving_manifest_snapshot_denies_cross_organization_access(client, receipt):
    from django.contrib.auth import get_user_model

    from apps.accounts.models import Organization, UserProfile

    User = get_user_model()
    other_org = Organization.objects.create(name="Other Org Snapshot Test")
    outsider = User.objects.create_user(username="outsider_snapshot", password="testpass123")
    UserProfile.objects.create(user=outsider, organization=other_org)

    client.force_login(outsider)
    response = client.get(reverse("reports:receiving-manifest-snapshot", args=[receipt.pk]))
    assert response.status_code == 404


def test_receiving_manifest_snapshot_creates_report_version_and_document(client, harrison, receipt, receipt_line):
    from apps.reports.models import ReportVersion

    client.force_login(harrison)
    client.get(reverse("reports:receiving-manifest-snapshot", args=[receipt.pk]))
    report = ReportVersion.objects.filter(report_type=ReportVersion.ReportType.ACTUAL_RECEIVING_REPORT).first()
    assert report is not None
    assert report.rendered_html_document is not None
