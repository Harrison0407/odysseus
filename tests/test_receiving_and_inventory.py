from decimal import Decimal

import pytest

from apps.core.models import DestinationScope
from apps.documents.models import Document, DocumentType
from apps.inventory.models import InventoryMovement, MovementType, StorageSite, WarehouseLocation, WarehouseZone
from apps.items.models import Item
from apps.receiving import services
from apps.receiving.models import Receipt, ReceiptLine, ReleasePacket
from apps.shipments.models import (
    BillOfLading,
    Container,
    ManifestLine,
    ManifestPurpose,
    Shipment,
    ShipmentManifest,
    ShipmentManifestVersion,
)


@pytest.fixture
def receiving_location(organization):
    site = StorageSite.objects.create(organization=organization, name="Almacén Central", site_type=StorageSite.SiteType.CENTRAL_WAREHOUSE)
    zone = WarehouseZone.objects.create(site=site, name="Recepción", code="recepcion")
    return WarehouseLocation.objects.create(zone=zone, code="REC-01")


@pytest.fixture
def quarantine_location(organization):
    site = StorageSite.objects.create(organization=organization, name="Almacén Central 2", site_type=StorageSite.SiteType.CENTRAL_WAREHOUSE)
    zone = WarehouseZone.objects.create(site=site, name="Cuarentena", code="cuarentena")
    return WarehouseLocation.objects.create(zone=zone, code="QTN-01")


@pytest.fixture
def receipt_line(organization, pc_uom, quartz_category):
    item = Item.objects.create(organization=organization, name="Tope de prueba", category=quartz_category, base_unit=pc_uom)
    shipment = Shipment.objects.create(organization=organization, reference="TEST-SHIP-1")
    container = Container.objects.create(shipment=shipment, container_number="TEST1234567")
    doc_type = DocumentType.objects.create(organization=organization, code="bill_of_lading", name="BL")
    document = Document.objects.create(organization=organization, document_type=doc_type, title="Test BL")
    BillOfLading.objects.create(shipment=shipment, bl_number="TESTBL1", source_document=document)
    manifest = ShipmentManifest.objects.create(shipment=shipment, purpose=ManifestPurpose.INTERNAL_OPERATIONAL_MANIFEST)
    version = ShipmentManifestVersion.objects.create(manifest=manifest, version_number=1)
    manifest_line = ManifestLine.objects.create(
        manifest_version=version, line_no=1, description="Tope de prueba", item=item,
        destination_scope=DestinationScope.PROJECT, quantity=Decimal("10"), unit_of_measure=pc_uom,
    )
    release_packet = ReleasePacket.objects.create(shipment=shipment)
    receipt = Receipt.objects.create(release_packet=release_packet, container=container)
    return ReceiptLine.objects.create(receipt=receipt, manifest_line=manifest_line, quantity_expected=Decimal("10"))


@pytest.mark.django_db
def test_posting_receipt_line_creates_inventory_movement(receipt_line, manuel, receiving_location):
    lot = services.post_receipt_line(
        receipt_line,
        quantity_received=Decimal("10"),
        quantity_damaged=Decimal("0"),
        quantity_missing=Decimal("0"),
        exception_type=ReceiptLine.ExceptionType.NONE,
        notes="",
        user=manuel,
        receiving_location=receiving_location,
    )
    assert lot is not None
    movement = InventoryMovement.objects.get(lot=lot)
    assert movement.movement_type == MovementType.RECEIPT
    assert movement.quantity == Decimal("10")
    assert movement.to_location == receiving_location


@pytest.mark.django_db
def test_damaged_quantity_is_quarantined_not_added_to_available_stock(
    receipt_line, manuel, receiving_location, quarantine_location
):
    services.post_receipt_line(
        receipt_line,
        quantity_received=Decimal("10"),
        quantity_damaged=Decimal("3"),
        quantity_missing=Decimal("0"),
        exception_type=ReceiptLine.ExceptionType.DAMAGE,
        notes="3 unidades llegaron rotas",
        user=manuel,
        receiving_location=receiving_location,
        quarantine_location=quarantine_location,
    )
    good_movement = InventoryMovement.objects.get(movement_type=MovementType.RECEIPT)
    assert good_movement.quantity == Decimal("7"), "Only the undamaged quantity should post as available receipt stock."

    quarantine_movement = InventoryMovement.objects.get(movement_type=MovementType.QUARANTINE)
    assert quarantine_movement.quantity == Decimal("3")
    assert quarantine_movement.to_location == quarantine_location


@pytest.mark.django_db
def test_damage_exception_creates_critical_discrepancy(receipt_line, manuel, receiving_location, quarantine_location):
    from apps.matching.models import Discrepancy

    services.post_receipt_line(
        receipt_line,
        quantity_received=Decimal("10"),
        quantity_damaged=Decimal("3"),
        quantity_missing=Decimal("0"),
        exception_type=ReceiptLine.ExceptionType.DAMAGE,
        notes="Daño detectado",
        user=manuel,
        receiving_location=receiving_location,
        quarantine_location=quarantine_location,
    )
    assert Discrepancy.objects.filter(severity="critical").exists()


@pytest.mark.django_db
def test_onhand_quantity_is_derived_from_ledger_never_edited_directly(
    receipt_line, manuel, receiving_location
):
    """Core principle 4.5: on-hand must always be computed from posted
    movements. This test recomputes it the same way the inventory view
    does and checks it matches what was actually posted."""
    services.post_receipt_line(
        receipt_line,
        quantity_received=Decimal("10"),
        quantity_damaged=Decimal("0"),
        quantity_missing=Decimal("0"),
        exception_type=ReceiptLine.ExceptionType.NONE,
        notes="",
        user=manuel,
        receiving_location=receiving_location,
    )
    total_in = sum(
        m.quantity for m in InventoryMovement.objects.filter(to_location=receiving_location)
    )
    total_out = sum(
        m.quantity for m in InventoryMovement.objects.filter(from_location=receiving_location)
    )
    assert (total_in - total_out) == Decimal("10")
