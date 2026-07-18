"""Tests for the cycle-count workflow (apps.inventory.services/views) —
closes the Priority 1 gap "cycle-count screens." Data model
(`CycleCount`/`CycleCountLine`/`InventoryAdjustment`) existed since
Priority 0; no counting/adjustment logic existed anywhere before this
session.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.inventory import services
from apps.inventory.models import (
    InventoryLot,
    InventoryMovement,
    MovementType,
    StorageSite,
    WarehouseLocation,
    WarehouseZone,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def site(organization):
    return StorageSite.objects.create(organization=organization, name="Sitio de prueba", site_type=StorageSite.SiteType.CENTRAL_WAREHOUSE)


@pytest.fixture
def location(site):
    zone = WarehouseZone.objects.create(site=site, name="Zona A", code="zona-a")
    return WarehouseLocation.objects.create(zone=zone, code="A-1")


@pytest.fixture
def item(organization, pc_uom, quartz_category):
    from apps.items.models import Item
    return Item.objects.create(organization=organization, name="Artículo de conteo", category=quartz_category, base_unit=pc_uom)


@pytest.fixture
def lot_with_10_units(item, location, manuel):
    lot = InventoryLot.objects.create(item=item, lot_code="CC-LOT-1")
    InventoryMovement.objects.create(
        lot=lot, movement_type=MovementType.RECEIPT, quantity=Decimal("10"),
        unit_of_measure=item.base_unit, to_location=location, posted_by=manuel,
    )
    return lot


class TestStartCycleCount:
    def test_seeds_system_quantity_from_ledger(self, site, lot_with_10_units, manuel):
        cycle_count = services.start_cycle_count(site, manuel, lots=[lot_with_10_units])
        line = cycle_count.lines.get(lot=lot_with_10_units)
        assert line.system_quantity == Decimal("10")
        assert line.physical_quantity is None


class TestRecordPhysicalCount:
    def test_records_variance(self, site, lot_with_10_units, manuel):
        cycle_count = services.start_cycle_count(site, manuel, lots=[lot_with_10_units])
        line = cycle_count.lines.get(lot=lot_with_10_units)
        services.record_physical_count(line, Decimal("8"), manuel, explanation="Faltante detectado en la góndola.")
        line.refresh_from_db()
        assert line.physical_quantity == Decimal("8")
        assert line.variance == Decimal("-2")

    def test_recount_marks_recounted(self, site, lot_with_10_units, manuel):
        cycle_count = services.start_cycle_count(site, manuel, lots=[lot_with_10_units])
        line = cycle_count.lines.get(lot=lot_with_10_units)
        services.record_physical_count(line, Decimal("8"), manuel)
        services.record_physical_count(line, Decimal("9"), manuel)
        line.refresh_from_db()
        assert line.recounted is True
        assert line.variance == Decimal("-1")


class TestApproveAdjustment:
    def test_negative_variance_posts_movement_from_location(self, site, lot_with_10_units, location, manuel):
        cycle_count = services.start_cycle_count(site, manuel, lots=[lot_with_10_units])
        line = cycle_count.lines.get(lot=lot_with_10_units)
        services.record_physical_count(line, Decimal("8"), manuel)
        adjustment = services.approve_adjustment(line, manuel, reason="Faltante confirmado tras recuento.", location=location)

        assert adjustment.quantity_delta == Decimal("-2")
        movement = InventoryMovement.objects.get(lot=lot_with_10_units, movement_type=MovementType.ADJUSTMENT)
        assert movement.from_location == location
        assert movement.to_location is None
        assert movement.quantity == Decimal("2")
        # on-hand is now correctly reduced by the posted adjustment movement
        assert services.lot_on_hand_quantity(lot_with_10_units) == Decimal("8")

    def test_positive_variance_posts_movement_to_location(self, site, lot_with_10_units, location, manuel):
        cycle_count = services.start_cycle_count(site, manuel, lots=[lot_with_10_units])
        line = cycle_count.lines.get(lot=lot_with_10_units)
        services.record_physical_count(line, Decimal("12"), manuel)
        adjustment = services.approve_adjustment(line, manuel, reason="Sobrante encontrado.", location=location)
        assert adjustment.quantity_delta == Decimal("2")
        movement = InventoryMovement.objects.get(lot=lot_with_10_units, movement_type=MovementType.ADJUSTMENT)
        assert movement.to_location == location
        assert services.lot_on_hand_quantity(lot_with_10_units) == Decimal("12")

    def test_cannot_approve_without_a_physical_count(self, site, lot_with_10_units, location, manuel):
        cycle_count = services.start_cycle_count(site, manuel, lots=[lot_with_10_units])
        line = cycle_count.lines.get(lot=lot_with_10_units)
        with pytest.raises(services.CycleCountError):
            services.approve_adjustment(line, manuel, reason="x", location=location)

    def test_cannot_approve_twice(self, site, lot_with_10_units, location, manuel):
        cycle_count = services.start_cycle_count(site, manuel, lots=[lot_with_10_units])
        line = cycle_count.lines.get(lot=lot_with_10_units)
        services.record_physical_count(line, Decimal("8"), manuel)
        services.approve_adjustment(line, manuel, reason="x", location=location)
        with pytest.raises(services.CycleCountError):
            services.approve_adjustment(line, manuel, reason="y", location=location)

    def test_no_variance_cannot_be_adjusted(self, site, lot_with_10_units, location, manuel):
        cycle_count = services.start_cycle_count(site, manuel, lots=[lot_with_10_units])
        line = cycle_count.lines.get(lot=lot_with_10_units)
        services.record_physical_count(line, Decimal("10"), manuel)
        with pytest.raises(services.CycleCountError):
            services.approve_adjustment(line, manuel, reason="x", location=location)


class TestBlindCountHTTP:
    def test_blind_count_hides_system_quantity_until_counted(self, client, manuel, site, lot_with_10_units):
        cycle_count = services.start_cycle_count(site, manuel, is_blind_count=True, lots=[lot_with_10_units])
        client.force_login(manuel)
        response = client.get(reverse("inventory:cycle-count-detail", args=[cycle_count.pk]))
        content = response.content.decode()
        assert "oculta (conteo ciego)" in content
        assert "Cantidad de sistema: <strong>10" not in content

    def test_non_blind_count_shows_system_quantity(self, client, manuel, site, lot_with_10_units):
        cycle_count = services.start_cycle_count(site, manuel, is_blind_count=False, lots=[lot_with_10_units])
        client.force_login(manuel)
        response = client.get(reverse("inventory:cycle-count-detail", args=[cycle_count.pk]))
        assert "10" in response.content.decode()

    def test_record_and_approve_via_http(self, client, manuel, site, lot_with_10_units, location):
        cycle_count = services.start_cycle_count(site, manuel, lots=[lot_with_10_units])
        line = cycle_count.lines.get(lot=lot_with_10_units)
        client.force_login(manuel)

        record_response = client.post(
            reverse("inventory:cycle-count-record", args=[cycle_count.pk, line.pk]),
            {f"{line.pk}-physical_quantity": "7", f"{line.pk}-explanation": "Conteo inicial"},
        )
        assert record_response.status_code == 302
        line.refresh_from_db()
        assert line.variance == Decimal("-3")

        approve_response = client.post(
            reverse("inventory:cycle-count-approve-adjustment", args=[cycle_count.pk, line.pk]),
            {f"{line.pk}-location": str(location.pk), f"{line.pk}-reason": "Confirmado"},
        )
        assert approve_response.status_code == 302
        line.refresh_from_db()
        assert line.approved_adjustment is not None

    def test_cross_organization_access_denied(self, client, site, lot_with_10_units, manuel):
        from django.contrib.auth import get_user_model

        from apps.accounts.models import Organization, UserProfile

        cycle_count = services.start_cycle_count(site, manuel, lots=[lot_with_10_units])
        User = get_user_model()
        other_org = Organization.objects.create(name="Other Org Cycle Count Test")
        outsider = User.objects.create_user(username="outsider_cyclecount", password="testpass123")
        UserProfile.objects.create(user=outsider, organization=other_org)

        client.force_login(outsider)
        response = client.get(reverse("inventory:cycle-count-detail", args=[cycle_count.pk]))
        assert response.status_code == 404
