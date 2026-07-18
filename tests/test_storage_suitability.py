"""Tests for storage capacity/suitability warnings
(apps.inventory.services.check_location_suitability/enforce_location_suitability)
— closes the Priority 1 gap "storage capacity and suitability warnings."
Also covers apps.requests.services.transfer_lot (wires up the
previously-unused Transfer model) and the receiving put-away
integration (apps.receiving.services.post_receipt_line).
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.core.models import DestinationScope
from apps.inventory import services as inv_services
from apps.inventory.models import (
    InventoryLot,
    InventoryMovement,
    LocationCapacity,
    LocationSuitability,
    MovementType,
    StorageSite,
    WarehouseLocation,
    WarehouseZone,
)
from apps.items.models import Item, ProductRiskProfile
from apps.shipments.models import ManifestLine, ManifestPurpose, Shipment, ShipmentManifest, ShipmentManifestVersion

pytestmark = pytest.mark.django_db


@pytest.fixture
def site(organization):
    return StorageSite.objects.create(organization=organization, name="Sitio Suitability Test", site_type=StorageSite.SiteType.CENTRAL_WAREHOUSE)


@pytest.fixture
def plain_location(site):
    zone = WarehouseZone.objects.create(site=site, name="Zona Plana", code="zona-plana")
    return WarehouseLocation.objects.create(zone=zone, code="PLAIN-1")


@pytest.fixture
def unsuitable_location(site):
    """Not covered, not dry, not secure, flood risk — bad for sensitive material."""
    zone = WarehouseZone.objects.create(site=site, name="Zona Exterior", code="zona-exterior")
    location = WarehouseLocation.objects.create(zone=zone, code="OUTDOOR-1")
    LocationSuitability.objects.create(location=location, covered=False, dry=False, secure=False, flood_risk=True)
    return location


@pytest.fixture
def suitable_location(site):
    zone = WarehouseZone.objects.create(site=site, name="Zona Segura", code="zona-segura")
    location = WarehouseLocation.objects.create(zone=zone, code="SAFE-1")
    LocationSuitability.objects.create(location=location, covered=True, dry=True, secure=True, flood_risk=False)
    return location


@pytest.fixture
def small_capacity_location(site):
    zone = WarehouseZone.objects.create(site=site, name="Zona Pequeña", code="zona-pequena")
    location = WarehouseLocation.objects.create(zone=zone, code="SMALL-1")
    LocationCapacity.objects.create(location=location, available_volume_cbm=Decimal("1.0"), weight_capacity_kg=Decimal("100"))
    return location


@pytest.fixture
def restricted_location(site, quartz_category):
    """Only allows a category that is NOT quartz."""
    from apps.items.models import ProductCategory
    zone = WarehouseZone.objects.create(site=site, name="Zona Restringida", code="zona-restringida")
    location = WarehouseLocation.objects.create(zone=zone, code="RESTRICTED-1")
    other_category = ProductCategory.objects.create(organization=quartz_category.organization, name="Herrajes Test")
    location.allowed_categories.add(other_category)
    return location


@pytest.fixture
def high_risk_category(organization):
    from apps.items.models import ProductCategory
    category = ProductCategory.objects.create(organization=organization, name="Vidrio de prueba (alto riesgo)")
    ProductRiskProfile.objects.create(category=category, risk_level=ProductRiskProfile.RiskLevel.HIGH)
    return category


@pytest.fixture
def high_risk_item(organization, high_risk_category, pc_uom):
    return Item.objects.create(organization=organization, name="Vidrio templado", category=high_risk_category, base_unit=pc_uom)


@pytest.fixture
def normal_item(organization, quartz_category, pc_uom):
    return Item.objects.create(organization=organization, name="Artículo normal", category=quartz_category, base_unit=pc_uom)


@pytest.fixture
def item_with_footprint(organization, quartz_category, pc_uom):
    """An item whose per-unit CBM/weight is derivable from a real
    ManifestLine (0.5 cbm/unit, 50 kg/unit for a quantity-10 line)."""
    item = Item.objects.create(organization=organization, name="Artículo con huella física", category=quartz_category, base_unit=pc_uom)
    shipment = Shipment.objects.create(organization=organization, reference="TEST-SUITABILITY-SHIP")
    manifest = ShipmentManifest.objects.create(shipment=shipment, purpose=ManifestPurpose.INTERNAL_OPERATIONAL_MANIFEST)
    version = ShipmentManifestVersion.objects.create(manifest=manifest, version_number=1)
    ManifestLine.objects.create(
        manifest_version=version, line_no=1, description="Línea con huella", item=item,
        destination_scope=DestinationScope.PROJECT, quantity=Decimal("10"), unit_of_measure=pc_uom,
        cbm=Decimal("5"), gross_weight_kg=Decimal("500"),
    )
    return item


class TestCheckLocationSuitability:
    def test_ready_with_no_configured_restrictions(self, plain_location, normal_item):
        result = inv_services.check_location_suitability(plain_location, normal_item)
        assert result.ready
        assert result.severity == "ready"

    def test_category_restriction_blocks(self, restricted_location, normal_item):
        result = inv_services.check_location_suitability(restricted_location, normal_item)
        assert result.blocked
        assert result.severity == "blocked"

    def test_high_risk_item_in_unsuitable_location_warns_not_blocks(self, unsuitable_location, high_risk_item):
        result = inv_services.check_location_suitability(unsuitable_location, high_risk_item)
        assert result.ready  # warning, not blocking
        assert result.severity == "warning"
        assert result.warnings

    def test_high_risk_item_in_suitable_location_is_clean(self, suitable_location, high_risk_item):
        result = inv_services.check_location_suitability(suitable_location, high_risk_item)
        assert result.ready
        assert not result.warnings

    def test_missing_suitability_row_warns_for_high_risk_item(self, plain_location, high_risk_item):
        result = inv_services.check_location_suitability(plain_location, high_risk_item)
        assert result.severity == "warning"

    def test_capacity_within_limits_is_ready(self, small_capacity_location, item_with_footprint):
        # 1 unit = 0.5 cbm, well within 1.0 cbm capacity
        result = inv_services.check_location_suitability(small_capacity_location, item_with_footprint, quantity=Decimal("1"))
        assert result.ready

    def test_projected_capacity_exceeded_blocks(self, small_capacity_location, item_with_footprint):
        # 5 units = 2.5 cbm > 1.0 cbm capacity
        result = inv_services.check_location_suitability(small_capacity_location, item_with_footprint, quantity=Decimal("5"))
        assert result.blocked
        assert any("volumen" in b for b in result.blockers)

    def test_weight_capacity_exceeded_blocks(self, small_capacity_location, item_with_footprint):
        # 3 units = 150 kg > 100 kg capacity (and only 1.5 cbm > 1.0 cbm too, but check weight message present)
        result = inv_services.check_location_suitability(small_capacity_location, item_with_footprint, quantity=Decimal("3"))
        assert result.blocked
        assert any("peso" in b or "volumen" in b for b in result.blockers)


class TestEnforceLocationSuitability:
    def test_blocked_without_override_raises(self, restricted_location, normal_item, harrison):
        with pytest.raises(inv_services.StorageSuitabilityError):
            inv_services.enforce_location_suitability(restricted_location, normal_item, harrison)

    def test_authorized_override_succeeds_and_is_audited(self, restricted_location, normal_item, harrison):
        from django.contrib.contenttypes.models import ContentType

        from apps.audit.models import AuditEvent

        result = inv_services.enforce_location_suitability(
            restricted_location, normal_item, harrison, override_reason="Autorizado por dirección ante urgencia."
        )
        assert result.blocked  # the underlying condition is still reported honestly
        assert AuditEvent.objects.filter(
            content_type=ContentType.objects.get_for_model(WarehouseLocation),
            object_id=restricted_location.pk, action=AuditEvent.Action.WAIVER,
        ).exists()

    def test_unauthorized_override_denied(self, restricted_location, normal_item, manuel):
        """manuel (Almacén role) does not hold can_override_gates."""
        with pytest.raises(inv_services.StoragePermissionError):
            inv_services.enforce_location_suitability(
                restricted_location, normal_item, manuel, override_reason="Lo autorizo yo mismo."
            )


class TestTransferLot:
    @pytest.fixture
    def lot_at_plain_location(self, plain_location, normal_item, manuel):
        lot = InventoryLot.objects.create(item=normal_item, lot_code="TRANSFER-TEST-LOT")
        InventoryMovement.objects.create(
            lot=lot, movement_type=MovementType.RECEIPT, quantity=Decimal("10"),
            unit_of_measure=normal_item.base_unit, to_location=plain_location, posted_by=manuel,
        )
        return lot

    def test_transfer_posts_movement_and_transfer_record(self, lot_at_plain_location, plain_location, suitable_location, manuel):
        from apps.requests.models import Transfer
        from apps.requests.services import transfer_lot

        transfer = transfer_lot(lot_at_plain_location, plain_location, suitable_location, Decimal("4"), manuel, reason="Reubicación de prueba.")
        assert Transfer.objects.filter(pk=transfer.pk).exists()
        movement = InventoryMovement.objects.get(lot=lot_at_plain_location, movement_type=MovementType.TRANSFER)
        assert movement.from_location == plain_location
        assert movement.to_location == suitable_location
        assert movement.quantity == Decimal("4")

    def test_transfer_more_than_available_raises(self, lot_at_plain_location, plain_location, suitable_location, manuel):
        from apps.requests.services import QuantityInvariantError, transfer_lot

        with pytest.raises(QuantityInvariantError):
            transfer_lot(lot_at_plain_location, plain_location, suitable_location, Decimal("999"), manuel)

    def test_transfer_to_restricted_location_blocked_without_override(
        self, lot_at_plain_location, plain_location, restricted_location, manuel
    ):
        from apps.requests.services import transfer_lot

        with pytest.raises(inv_services.StorageSuitabilityError):
            transfer_lot(lot_at_plain_location, plain_location, restricted_location, Decimal("1"), manuel)


class TestReceivingPutAwayIntegration:
    """apps.receiving.services.post_receipt_line now enforces storage
    suitability at the point of put-away, closing the pre-existing
    WarehouseLocation.objects.first() placeholder bug (no location was
    ever really chosen before this feature)."""

    @pytest.fixture
    def receipt_line(self, organization, pc_uom, quartz_category, manuel):
        from apps.receiving.models import Receipt, ReceiptLine, ReleasePacket
        from apps.shipments.models import Container

        shipment = Shipment.objects.create(organization=organization, reference="TEST-PUTAWAY-SHIP")
        item = Item.objects.create(organization=organization, name="Artículo Put-Away", category=quartz_category, base_unit=pc_uom)
        manifest = ShipmentManifest.objects.create(shipment=shipment, purpose=ManifestPurpose.INTERNAL_OPERATIONAL_MANIFEST)
        version = ShipmentManifestVersion.objects.create(manifest=manifest, version_number=1)
        manifest_line = ManifestLine.objects.create(
            manifest_version=version, line_no=1, description="Línea put-away", item=item,
            destination_scope=DestinationScope.PROJECT, quantity=Decimal("10"), unit_of_measure=pc_uom,
        )
        release_packet = ReleasePacket.objects.create(shipment=shipment)
        container = Container.objects.create(shipment=shipment, container_number="PUTAWAY-CONT-1")
        receipt = Receipt.objects.create(release_packet=release_packet, container=container)
        return ReceiptLine.objects.create(receipt=receipt, manifest_line=manifest_line, quantity_expected=Decimal("10"))

    def test_post_receipt_line_blocked_by_restricted_location(self, receipt_line, restricted_location, manuel):
        from apps.receiving import services as receiving_services

        with pytest.raises(inv_services.StorageSuitabilityError):
            receiving_services.post_receipt_line(
                receipt_line, quantity_received=Decimal("10"), quantity_damaged=Decimal("0"),
                quantity_missing=Decimal("0"), exception_type="none", notes="", user=manuel,
                receiving_location=restricted_location,
            )
        # Blocked — no inventory consequence at all.
        assert not InventoryMovement.objects.filter(movement_type=MovementType.RECEIPT).exists()

    def test_post_receipt_line_succeeds_with_authorized_override(self, receipt_line, restricted_location, harrison):
        from apps.receiving import services as receiving_services

        lot = receiving_services.post_receipt_line(
            receipt_line, quantity_received=Decimal("10"), quantity_damaged=Decimal("0"),
            quantity_missing=Decimal("0"), exception_type="none", notes="", user=harrison,
            receiving_location=restricted_location, storage_override_reason="Autorizado por dirección.",
        )
        assert lot is not None
        assert InventoryMovement.objects.filter(lot=lot, movement_type=MovementType.RECEIPT).exists()

    def test_post_receipt_line_unaffected_by_unconfigured_location(self, receipt_line, plain_location, manuel):
        """Regression guard: a location with no LocationSuitability/
        LocationCapacity/allowed_categories configured must never block —
        matches every pre-existing receiving test's fixtures."""
        from apps.receiving import services as receiving_services

        lot = receiving_services.post_receipt_line(
            receipt_line, quantity_received=Decimal("10"), quantity_damaged=Decimal("0"),
            quantity_missing=Decimal("0"), exception_type="none", notes="", user=manuel,
            receiving_location=plain_location,
        )
        assert lot is not None


class TestLocationDetailHTTP:
    def test_location_detail_shows_capacity_and_suitability(self, client, harrison, small_capacity_location):
        client.force_login(harrison)
        response = client.get(reverse("inventory:location-detail", args=[small_capacity_location.pk]))
        assert response.status_code == 200
        content = response.content.decode()
        assert "1,0000" in content or "1.0000" in content

    def test_check_suitability_query_shows_blocked_result(self, client, harrison, small_capacity_location, item_with_footprint):
        client.force_login(harrison)
        response = client.get(
            reverse("inventory:location-detail", args=[small_capacity_location.pk]),
            {"item": item_with_footprint.pk, "quantity": "5"},
        )
        assert response.status_code == 200
        assert "Bloqueado" in response.content.decode()

    def test_cross_organization_access_denied(self, client, small_capacity_location):
        from django.contrib.auth import get_user_model

        from apps.accounts.models import Organization, UserProfile

        User = get_user_model()
        other_org = Organization.objects.create(name="Other Org Storage Test")
        outsider = User.objects.create_user(username="outsider_storage", password="testpass123")
        UserProfile.objects.create(user=outsider, organization=other_org)

        client.force_login(outsider)
        response = client.get(reverse("inventory:location-detail", args=[small_capacity_location.pk]))
        assert response.status_code == 404
