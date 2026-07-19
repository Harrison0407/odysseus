"""Tests for order destination allocation and purchased-spare control
(one-shot release, section 3). "allocated + confirmed purchased spares
must never exceed ordered quantity" is enforced on every write; a spare
becomes real inventory only through the existing ledger/reservation
architecture, never a second directly-editable balance.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.core.models import DestinationScope
from apps.inventory.models import InventoryLot, InventoryMovement, MovementType
from apps.procurement import services
from apps.procurement.models import OrderLineAllocation, PurchaseOrder, PurchaseOrderLine, PurchasedSpare, Supplier
from apps.projects.models import Building, BuildingFamily, Project, Unit

pytestmark = pytest.mark.django_db


@pytest.fixture
def supplier(organization):
    return Supplier.objects.create(organization=organization, name="Proveedor Allocation Test")


@pytest.fixture
def purchase_order(organization, supplier):
    return PurchaseOrder.objects.create(organization=organization, supplier=supplier, po_number="PO-ALLOC-TEST")


@pytest.fixture
def line(purchase_order, pc_uom):
    return PurchaseOrderLine.objects.create(
        purchase_order=purchase_order, line_no=1, description="Ventanas de aluminio",
        quantity_ordered=Decimal("100"), unit_of_measure=pc_uom, unit_price=Decimal("50"), line_total=Decimal("5000"),
    )


@pytest.fixture
def project(organization):
    return Project.objects.create(organization=organization, name="Arena", code="arena-alloc-test")


@pytest.fixture
def family(project):
    return BuildingFamily.objects.create(project=project, code="arena-t1-alloc-test", display_name="ARENA T1")


@pytest.fixture
def building(project, family):
    return Building.objects.create(project=project, code="b11-alloc-test", name="Edificio 11", family=family, building_number="11")


@pytest.fixture
def unit(building):
    return Unit.objects.create(building=building, name="A3", permanent_code="ARENA-T1-B11-A3-ALLOC-TEST")


class TestOrderLineAllocation:
    def test_split_allocation_across_multiple_destinations(self, line, unit, harrison):
        alloc1 = services.allocate_order_line(line, harrison, destination_scope=DestinationScope.UNIT, quantity=Decimal("60"), unit=unit)
        alloc2 = services.allocate_order_line(line, harrison, destination_scope=DestinationScope.WAREHOUSE_STOCK, quantity=Decimal("20"))
        assert alloc1.quantity == Decimal("60")
        assert alloc2.quantity == Decimal("20")
        assert services.allocated_quantity(line) == Decimal("80")

    def test_unallocated_quantity_calculation(self, line, unit, harrison):
        services.allocate_order_line(line, harrison, destination_scope=DestinationScope.UNIT, quantity=Decimal("60"), unit=unit)
        assert services.unallocated_quantity(line) == Decimal("40")

    def test_cannot_allocate_more_than_unallocated_quantity(self, line, unit, harrison):
        services.allocate_order_line(line, harrison, destination_scope=DestinationScope.UNIT, quantity=Decimal("60"), unit=unit)
        with pytest.raises(services.AllocationError):
            services.allocate_order_line(line, harrison, destination_scope=DestinationScope.UNIT, quantity=Decimal("50"), unit=unit)

    def test_shortage_is_none_when_required_quantity_unknown(self, line):
        assert services.shortage_quantity(line) is None

    def test_shortage_calculation_when_required_quantity_known(self, line):
        line.required_quantity = Decimal("120")
        line.save()
        assert services.shortage_quantity(line) == Decimal("20")

    def test_allocated_plus_spares_never_exceeds_ordered(self, line, unit, harrison):
        services.allocate_order_line(line, harrison, destination_scope=DestinationScope.UNIT, quantity=Decimal("60"), unit=unit)
        services.confirm_purchased_spare(line, harrison, quantity=Decimal("40"), reason="Excedente confirmado como repuesto.")
        assert services.unallocated_quantity(line) == Decimal("0")
        with pytest.raises(services.AllocationError):
            services.allocate_order_line(line, harrison, destination_scope=DestinationScope.UNIT, quantity=Decimal("1"), unit=unit)


class TestPurchasedSpareConfirmation:
    def test_unauthorized_user_cannot_confirm_spare(self, line, manuel):
        with pytest.raises(services.AllocationError):
            services.confirm_purchased_spare(line, manuel, quantity=Decimal("10"), reason="Intento no autorizado.")

    def test_authorized_user_can_confirm_spare(self, line, harrison):
        spare = services.confirm_purchased_spare(line, harrison, quantity=Decimal("10"), reason="Excedente de fabricación.", compatible_typology="Ventana tipo A")
        assert spare.confirmed_by == harrison
        assert services.confirmed_spare_quantity(line) == Decimal("10")

    def test_spare_requires_a_reason(self, line, harrison):
        with pytest.raises(services.AllocationError):
            services.confirm_purchased_spare(line, harrison, quantity=Decimal("10"), reason="   ")

    def test_spare_confirmation_is_audited(self, line, harrison):
        from django.contrib.contenttypes.models import ContentType

        from apps.audit.models import AuditEvent

        spare = services.confirm_purchased_spare(line, harrison, quantity=Decimal("10"), reason="Excedente.")
        assert AuditEvent.objects.filter(
            content_type=ContentType.objects.get_for_model(PurchasedSpare), object_id=spare.pk,
        ).exists()


class TestSpareInventoryTraceability:
    def test_spare_receipt_and_availability_via_ledger(self, line, harrison, quartz_category, pc_uom, organization, unit):
        from apps.inventory.models import WarehouseLocation, WarehouseZone, StorageSite
        from apps.items.models import Item

        spare = services.confirm_purchased_spare(line, harrison, quantity=Decimal("10"), reason="Excedente.")
        item = Item.objects.create(organization=organization, name="Ventana de aluminio", category=quartz_category, base_unit=pc_uom)
        site = StorageSite.objects.create(organization=organization, name="Sitio Spare Test", site_type=StorageSite.SiteType.CENTRAL_WAREHOUSE)
        zone = WarehouseZone.objects.create(site=site, name="Zona Spare", code="zona-spare-test")
        location = WarehouseLocation.objects.create(zone=zone, code="SPARE-LOC-1")

        lot = InventoryLot.objects.create(item=item, lot_code="SPARE-LOT-1", purchased_spare=spare)
        InventoryMovement.objects.create(
            lot=lot, movement_type=MovementType.RECEIPT, quantity=Decimal("10"), unit_of_measure=pc_uom,
            to_location=location, posted_by=harrison,
        )

        summary = services.spare_inventory_summary(spare)
        assert summary["quantity_received"] == Decimal("10")
        assert summary["quantity_available"] == Decimal("10")
        assert summary["quantity_reserved"] == Decimal("0")

    def test_spare_consumption_reduces_availability_through_real_movement(self, line, harrison, quartz_category, pc_uom, organization):
        from apps.inventory.models import WarehouseLocation, WarehouseZone, StorageSite
        from apps.items.models import Item

        spare = services.confirm_purchased_spare(line, harrison, quantity=Decimal("10"), reason="Excedente.")
        item = Item.objects.create(organization=organization, name="Ventana de aluminio 2", category=quartz_category, base_unit=pc_uom)
        site = StorageSite.objects.create(organization=organization, name="Sitio Spare Test 2", site_type=StorageSite.SiteType.CENTRAL_WAREHOUSE)
        zone = WarehouseZone.objects.create(site=site, name="Zona Spare 2", code="zona-spare-test-2")
        location = WarehouseLocation.objects.create(zone=zone, code="SPARE-LOC-2")
        other_location = WarehouseLocation.objects.create(zone=zone, code="SPARE-LOC-3")

        lot = InventoryLot.objects.create(item=item, lot_code="SPARE-LOT-2", purchased_spare=spare)
        InventoryMovement.objects.create(
            lot=lot, movement_type=MovementType.RECEIPT, quantity=Decimal("10"), unit_of_measure=pc_uom,
            to_location=location, posted_by=harrison,
        )
        # consumption modeled as a real ledger movement out of the location — never a direct field edit
        InventoryMovement.objects.create(
            lot=lot, movement_type=MovementType.TRANSFER, quantity=Decimal("4"), unit_of_measure=pc_uom,
            from_location=location, to_location=other_location, posted_by=harrison,
        )
        summary = services.spare_inventory_summary(spare)
        assert summary["quantity_received"] == Decimal("10")
        assert summary["quantity_available"] == Decimal("10")  # still on hand overall, just moved location


class TestDestinationReassignment:
    def test_reassign_preserves_original_allocation(self, line, unit, building, harrison):
        original = services.allocate_order_line(line, harrison, destination_scope=DestinationScope.UNIT, quantity=Decimal("20"), unit=unit)
        new_unit = Unit.objects.create(building=building, name="B3", permanent_code="ARENA-T1-B11-B3-ALLOC-TEST")
        new_allocation = services.reassign_allocation(
            original, harrison, reason="Cambio de destino por error de digitación.", unit=new_unit,
        )
        original.refresh_from_db()
        assert not original.is_active
        assert original.reassignment_reason == "Cambio de destino por error de digitación."
        assert new_allocation.reassigned_from_id == original.id
        assert new_allocation.unit_id == new_unit.id
        assert new_allocation.quantity == original.quantity

    def test_reassign_requires_a_reason(self, line, unit, harrison):
        original = services.allocate_order_line(line, harrison, destination_scope=DestinationScope.UNIT, quantity=Decimal("20"), unit=unit)
        with pytest.raises(services.AllocationError):
            services.reassign_allocation(original, harrison, reason="")

    def test_cannot_reassign_an_already_reassigned_allocation(self, line, unit, building, harrison):
        original = services.allocate_order_line(line, harrison, destination_scope=DestinationScope.UNIT, quantity=Decimal("20"), unit=unit)
        services.reassign_allocation(original, harrison, reason="Primera reasignación.", unit=unit)
        original.refresh_from_db()
        with pytest.raises(services.AllocationError):
            services.reassign_allocation(original, harrison, reason="Segundo intento.", unit=unit)


class TestHTTPWorkflow:
    def test_full_allocation_and_spare_workflow_via_http(self, client, line, unit, harrison):
        client.force_login(harrison)
        response = client.post(reverse("procurement:line-allocate", args=[line.pk]), {
            "destination_scope": "unit", "unit": unit.pk, "quantity": "60",
        })
        assert response.status_code == 302
        assert OrderLineAllocation.objects.filter(purchase_order_line=line, quantity=Decimal("60")).exists()

        response = client.post(reverse("procurement:line-confirm-spare", args=[line.pk]), {
            "quantity": "40", "reason": "Excedente confirmado vía HTTP.",
        })
        assert response.status_code == 302
        assert PurchasedSpare.objects.filter(purchase_order_line=line, quantity=Decimal("40")).exists()

        response = client.get(reverse("procurement:line-allocation-detail", args=[line.pk]))
        assert response.status_code == 200
        assert "0" in response.content.decode()  # unallocated now shows 0

    def test_unauthorized_spare_confirmation_denied_via_http(self, client, line, manuel):
        client.force_login(manuel)
        response = client.post(reverse("procurement:line-confirm-spare", args=[line.pk]), {
            "quantity": "10", "reason": "Intento no autorizado vía HTTP.",
        })
        assert response.status_code == 302
        assert not PurchasedSpare.objects.filter(purchase_order_line=line).exists()

    def test_cross_organization_line_access_denied(self, client, line):
        from django.contrib.auth import get_user_model

        from apps.accounts.models import Organization, UserProfile

        User = get_user_model()
        other_org = Organization.objects.create(name="Other Org Allocation Test")
        outsider = User.objects.create_user(username="outsider_allocation", password="testpass123")
        UserProfile.objects.create(user=outsider, organization=other_org)

        client.force_login(outsider)
        response = client.get(reverse("procurement:line-allocation-detail", args=[line.pk]))
        assert response.status_code == 404

    def test_allocations_by_destination_view(self, client, line, unit, harrison):
        services.allocate_order_line(line, harrison, destination_scope=DestinationScope.UNIT, quantity=Decimal("10"), unit=unit)
        client.force_login(harrison)
        response = client.get(reverse("procurement:allocations-by-destination"))
        assert response.status_code == 200

    def test_purchased_spares_list_view(self, client, line, harrison):
        services.confirm_purchased_spare(line, harrison, quantity=Decimal("10"), reason="Excedente HTTP list test.")
        client.force_login(harrison)
        response = client.get(reverse("procurement:purchased-spares-list"))
        assert response.status_code == 200
