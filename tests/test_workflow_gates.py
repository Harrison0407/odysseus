"""Tests for the reusable gate-evaluation engine (apps.workflow.gates).

These test the evaluator functions directly against constructed model
graphs — the "does the gate correctly read the target's real state"
layer, independent of the handoff lifecycle (covered in
test_workflow_handoffs.py).
"""

from decimal import Decimal

import pytest

from apps.core.models import DestinationScope, Severity
from apps.documents.models import Document, DocumentType
from apps.inventory.models import (
    InventoryLot,
    QuarantineRecord,
    StorageSite,
    WarehouseLocation,
    WarehouseZone,
)
from apps.items.models import Item
from apps.matching.models import Discrepancy, DiscrepancyType
from apps.procurement.models import PaymentMilestone, PurchaseOrder, Supplier
from apps.projects.models import Project
from apps.receiving.models import Receipt, ReceivingPlan, ReleasePacket
from apps.requests.models import MaterialRequest, MaterialRequestLine
from apps.shipments.models import (
    BillOfLading,
    Container,
    ManifestLine,
    ManifestPurpose,
    ManifestVariance,
    Shipment,
    ShipmentManifest,
    ShipmentManifestVersion,
)
from apps.workflow import gates


@pytest.fixture
def supplier(organization):
    return Supplier.objects.create(organization=organization, name="Test Supplier")


@pytest.fixture
def purchase_order(organization, supplier):
    return PurchaseOrder.objects.create(
        organization=organization, supplier=supplier, po_number="PO-TEST-1",
        approval_status=PurchaseOrder.ApprovalStatus.DRAFT,
    )


@pytest.fixture
def shipment(organization):
    return Shipment.objects.create(organization=organization, reference="TEST-GATE-SHIP")


@pytest.fixture
def bill_of_lading(organization, shipment):
    doc_type = DocumentType.objects.create(organization=organization, code="bill_of_lading", name="BL")
    document = Document.objects.create(organization=organization, document_type=doc_type, title="Test BL")
    return BillOfLading.objects.create(shipment=shipment, bl_number="TESTBL", source_document=document)


@pytest.fixture
def internal_manifest_line(shipment, pc_uom, quartz_category, organization):
    item = Item.objects.create(organization=organization, name="Item de prueba", category=quartz_category, base_unit=pc_uom)
    manifest = ShipmentManifest.objects.create(shipment=shipment, purpose=ManifestPurpose.INTERNAL_OPERATIONAL_MANIFEST)
    version = ShipmentManifestVersion.objects.create(manifest=manifest, version_number=1)
    return ManifestLine.objects.create(
        manifest_version=version, line_no=1, description="Línea de prueba", item=item,
        destination_scope=DestinationScope.PROJECT, quantity=Decimal("10"), unit_of_measure=pc_uom,
    )


@pytest.mark.django_db
class TestPurchasingToFinance:
    def test_blocked_when_not_approved(self, purchase_order):
        result = gates.evaluate_purchasing_to_finance(purchase_order)
        assert result.blocked
        assert "no está aprobada" in result.unmet_requirements[0]

    def test_ready_when_approved(self, purchase_order):
        purchase_order.approval_status = PurchaseOrder.ApprovalStatus.APPROVED
        purchase_order.save()
        result = gates.evaluate_purchasing_to_finance(purchase_order)
        assert result.ready
        assert result.severity == "ready"


@pytest.mark.django_db
class TestFinanceToLogistics:
    def test_blocked_by_pending_payment_milestone(self, purchase_order):
        PaymentMilestone.objects.create(
            purchase_order=purchase_order, name="Depósito 30%", status=PaymentMilestone.Status.PENDING_APPROVAL
        )
        result = gates.evaluate_finance_to_logistics(purchase_order)
        assert result.blocked
        assert "Depósito 30%" in result.missing_approvals

    def test_ready_when_no_pending_or_held_milestones(self, purchase_order):
        PaymentMilestone.objects.create(
            purchase_order=purchase_order, name="Depósito 30%", status=PaymentMilestone.Status.PAID
        )
        result = gates.evaluate_finance_to_logistics(purchase_order)
        assert result.ready


@pytest.mark.django_db
class TestLogisticsToReceiving:
    def test_blocked_without_bill_of_lading(self, shipment):
        result = gates.evaluate_logistics_to_receiving(shipment)
        assert result.blocked
        assert "Conocimiento de embarque (BL)" in result.missing_documents

    def test_blocked_by_unresolved_official_vs_operational_mismatch(self, shipment, bill_of_lading, internal_manifest_line):
        """The official-vs-operational mismatch scenario: a critical,
        unexplained ManifestVariance must block this gate — and must
        never be resolved by editing the BL or the manifest line."""
        ManifestVariance.objects.create(
            shipment=shipment, official_category_text="QUARTZ STONE COUNTERTOP",
            internal_manifest_line=internal_manifest_line, is_explained=False,
            severity=Severity.CRITICAL, customs_review_required=True,
        )
        ReceivingPlan.objects.create(shipment=shipment, status=ReceivingPlan.Status.SUITABLE)

        result = gates.evaluate_logistics_to_receiving(shipment)

        assert result.blocked
        assert len(result.unresolved_discrepancies) == 1
        assert len(result.customs_review_required) == 1
        bill_of_lading.refresh_from_db()
        assert bill_of_lading.official_cargo_description == ""  # untouched, never rewritten
        internal_manifest_line.refresh_from_db()
        assert internal_manifest_line.description == "Línea de prueba"  # untouched

    def test_blocked_without_suitable_receiving_plan(self, shipment, bill_of_lading):
        result = gates.evaluate_logistics_to_receiving(shipment)
        assert result.blocked
        assert any("recepción" in item for item in result.unmet_requirements)

    def test_ready_when_all_conditions_met(self, shipment, bill_of_lading):
        ReceivingPlan.objects.create(shipment=shipment, status=ReceivingPlan.Status.SUITABLE)
        result = gates.evaluate_logistics_to_receiving(shipment)
        assert result.ready
        assert result.evidence_requirements  # still lists what evidence a submitter should attach


@pytest.mark.django_db
class TestReceivingToWarehouse:
    @pytest.fixture
    def receipt(self, shipment):
        release_packet = ReleasePacket.objects.create(shipment=shipment)
        container = Container.objects.create(shipment=shipment, container_number="TESTCONT1")
        return Receipt.objects.create(release_packet=release_packet, container=container)

    def test_blocked_without_any_receipt(self, shipment):
        result = gates.evaluate_receiving_to_warehouse(shipment)
        assert result.blocked
        assert result.incomplete_receiving

    def test_blocked_while_receipt_still_provisional(self, receipt, shipment):
        assert receipt.status == Receipt.Status.PROVISIONAL
        result = gates.evaluate_receiving_to_warehouse(shipment)
        assert result.blocked
        assert result.incomplete_receiving

    def test_unrelated_quarantine_does_not_block(self, receipt, shipment, organization, pc_uom, quartz_category):
        """A quarantine record for a lot from a *different* shipment's
        receipt must never leak into this gate's evaluation."""
        receipt.status = Receipt.Status.RECEIVED_WITH_EXCEPTIONS
        receipt.save()
        item = Item.objects.create(organization=organization, name="Otro artículo", category=quartz_category, base_unit=pc_uom)
        site = StorageSite.objects.create(organization=organization, name="Almacén", site_type=StorageSite.SiteType.CENTRAL_WAREHOUSE)
        zone = WarehouseZone.objects.create(site=site, name="Cuarentena", code="cuarentena")
        location = WarehouseLocation.objects.create(zone=zone, code="QTN-1")
        unrelated_lot = InventoryLot.objects.create(item=item)  # no source_receipt_line at all
        QuarantineRecord.objects.create(lot=unrelated_lot, location=location, quantity=Decimal("3"), reason="Daño en otro embarque")

        result = gates.evaluate_receiving_to_warehouse(shipment)
        assert not result.quarantined_inventory

    def test_blocked_by_quarantine_linked_to_this_shipments_receipt(
        self, receipt, shipment, internal_manifest_line, organization, pc_uom
    ):
        """Quarantined inventory (spec: damaged stock) traced back to this
        shipment's own receipt must block the gate until released — the
        live-fixture damage/quarantine pattern, exercised in isolation."""
        from apps.receiving.models import ReceiptLine

        receipt.status = Receipt.Status.RECEIVED_WITH_EXCEPTIONS
        receipt.save()
        receipt_line = ReceiptLine.objects.create(
            receipt=receipt, manifest_line=internal_manifest_line, quantity_expected=Decimal("10"),
            quantity_received=Decimal("10"), quantity_damaged=Decimal("3"),
        )
        site = StorageSite.objects.create(organization=organization, name="Almacén", site_type=StorageSite.SiteType.CENTRAL_WAREHOUSE)
        zone = WarehouseZone.objects.create(site=site, name="Cuarentena", code="cuarentena")
        location = WarehouseLocation.objects.create(zone=zone, code="QTN-2")
        lot = InventoryLot.objects.create(item=internal_manifest_line.item, source_receipt_line=receipt_line)
        QuarantineRecord.objects.create(lot=lot, location=location, quantity=Decimal("3"), reason="Daño detectado en recepción")

        result = gates.evaluate_receiving_to_warehouse(shipment)
        assert result.blocked
        assert len(result.quarantined_inventory) == 1

    def test_blocked_by_unresolved_critical_discrepancy(self, receipt, shipment):
        receipt.status = Receipt.Status.RECEIVED_WITH_EXCEPTIONS
        receipt.save()
        Discrepancy.objects.create(
            shipment=shipment, discrepancy_type=DiscrepancyType.RECEIPT_SHORTAGE, severity=Severity.CRITICAL
        )
        result = gates.evaluate_receiving_to_warehouse(shipment)
        assert result.blocked
        assert result.unresolved_discrepancies

    def test_ready_when_receipt_complete_and_no_open_issues(self, receipt, shipment):
        receipt.status = Receipt.Status.MATCHED
        receipt.save()
        result = gates.evaluate_receiving_to_warehouse(shipment)
        assert result.ready


@pytest.mark.django_db
class TestWarehouseToProject:
    @pytest.fixture
    def material_request(self, organization, pc_uom, quartz_category):
        project = Project.objects.create(organization=organization, name="Proyecto Test", code="test-proj")
        item = Item.objects.create(organization=organization, name="Artículo", category=quartz_category, base_unit=pc_uom)
        mr = MaterialRequest.objects.create(project=project)
        MaterialRequestLine.objects.create(
            request=mr, item=item, quantity_requested=Decimal("5"), quantity_approved=Decimal("5"), quantity_reserved=Decimal("2")
        )
        return mr

    def test_blocked_when_reserved_less_than_approved(self, material_request):
        result = gates.evaluate_warehouse_to_project(material_request)
        assert result.blocked

    def test_ready_when_fully_reserved(self, material_request):
        line = material_request.lines.first()
        line.quantity_reserved = line.quantity_approved
        line.save()
        result = gates.evaluate_warehouse_to_project(material_request)
        assert result.ready
