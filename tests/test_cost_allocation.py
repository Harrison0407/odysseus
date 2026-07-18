"""Tests for the landed-cost allocation/calculation engine
(apps.cost.services) — closes the Priority 0 KNOWN_LIMITATIONS gap
"Landed-cost allocation-run trigger UI... calculation must currently be
run via the ORM/a management command," which turned out to require
building the calculation engine itself (it did not exist anywhere
before this session, only the data model did).
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.core.models import DestinationScope
from apps.cost import services
from apps.cost.models import CostAllocationLine, CostAllocationRun, CostCharge, CostDocument, LandedCostVersion
from apps.documents.models import Document, DocumentType
from apps.items.models import Item
from apps.procurement.models import PurchaseOrder, PurchaseOrderLine, Supplier
from apps.shipments.models import ManifestLine, ManifestLineSource, ManifestPurpose, Shipment, ShipmentManifest, ShipmentManifestVersion

pytestmark = pytest.mark.django_db


@pytest.fixture
def shipment(organization):
    return Shipment.objects.create(organization=organization, reference="TEST-COST-SHIP")


@pytest.fixture
def internal_manifest_lines(shipment, pc_uom, quartz_category, organization):
    manifest = ShipmentManifest.objects.create(shipment=shipment, purpose=ManifestPurpose.INTERNAL_OPERATIONAL_MANIFEST)
    version = ShipmentManifestVersion.objects.create(manifest=manifest, version_number=1)
    item = Item.objects.create(organization=organization, name="Artículo de costo", category=quartz_category, base_unit=pc_uom)

    line_a = ManifestLine.objects.create(
        manifest_version=version, line_no=1, description="Línea A", item=item,
        destination_scope=DestinationScope.PROJECT, quantity=Decimal("10"), unit_of_measure=pc_uom,
        gross_weight_kg=Decimal("100"), cbm=Decimal("2"), commercial_value=Decimal("1000"), packages=5,
    )
    line_b = ManifestLine.objects.create(
        manifest_version=version, line_no=2, description="Línea B", item=item,
        destination_scope=DestinationScope.PROJECT, quantity=Decimal("20"), unit_of_measure=pc_uom,
        gross_weight_kg=Decimal("200"), cbm=Decimal("3"), commercial_value=Decimal("2000"), packages=10,
    )
    return [line_a, line_b]


@pytest.fixture
def cost_charge(shipment, organization):
    doc_type = DocumentType.objects.create(organization=organization, code="freight_invoice", name="Factura de Flete")
    document = Document.objects.create(organization=organization, document_type=doc_type, title="Flete de prueba")
    cost_document = CostDocument.objects.create(
        shipment=shipment, cost_type=CostDocument.CostType.FREIGHT, document=document,
        amount=Decimal("1000.00"), currency_id=_currency_id("USD"),
    )
    return CostCharge.objects.create(cost_document=cost_document, description="Flete internacional", amount=Decimal("1000.00"))


def _currency_id(code):
    from apps.cost.models import Currency
    currency, _ = Currency.objects.get_or_create(code=code, defaults={"name": code})
    return currency.id


class TestRunAllocation:
    def test_allocates_proportionally_by_quantity_and_sums_exactly(self, shipment, internal_manifest_lines, cost_charge, harrison):
        run = services.run_allocation(cost_charge, shipment, CostAllocationRun.AllocationMethod.QUANTITY, harrison)
        lines = CostAllocationLine.objects.filter(allocation_run=run).order_by("manifest_line__line_no")
        amounts = [l.allocated_amount for l in lines]
        assert sum(amounts) == cost_charge.amount  # never a rounding leftover, never invented
        # Line A: 10/30 of 1000 = 333.33..., Line B (last, absorbs remainder): 666.67
        assert amounts[0] == Decimal("333.33")
        assert amounts[1] == Decimal("666.67")

    def test_allocates_by_cbm(self, shipment, internal_manifest_lines, cost_charge, harrison):
        run = services.run_allocation(cost_charge, shipment, CostAllocationRun.AllocationMethod.CBM, harrison)
        lines = list(CostAllocationLine.objects.filter(allocation_run=run).order_by("manifest_line__line_no"))
        assert sum(l.allocated_amount for l in lines) == cost_charge.amount
        assert lines[0].basis_value == Decimal("2")
        assert lines[1].basis_value == Decimal("3")

    def test_container_method_splits_equally(self, shipment, internal_manifest_lines, cost_charge, harrison):
        run = services.run_allocation(cost_charge, shipment, CostAllocationRun.AllocationMethod.CONTAINER, harrison)
        lines = list(CostAllocationLine.objects.filter(allocation_run=run))
        assert sum(l.allocated_amount for l in lines) == cost_charge.amount
        assert lines[0].allocated_amount == Decimal("500.00")

    def test_manual_percentage_must_sum_to_100(self, shipment, internal_manifest_lines, cost_charge, harrison):
        line_a, line_b = internal_manifest_lines
        with pytest.raises(services.AllocationError):
            services.run_allocation(
                cost_charge, shipment, CostAllocationRun.AllocationMethod.MANUAL_PERCENTAGE, harrison,
                manual_values={str(line_a.id): "50", str(line_b.id): "40"},
            )

    def test_manual_percentage_allocates_as_specified(self, shipment, internal_manifest_lines, cost_charge, harrison):
        line_a, line_b = internal_manifest_lines
        run = services.run_allocation(
            cost_charge, shipment, CostAllocationRun.AllocationMethod.MANUAL_PERCENTAGE, harrison,
            manual_values={str(line_a.id): "30", str(line_b.id): "70"},
        )
        lines = {l.manifest_line_id: l.allocated_amount for l in CostAllocationLine.objects.filter(allocation_run=run)}
        assert lines[line_a.id] == Decimal("300.00")
        assert lines[line_b.id] == Decimal("700.00")

    def test_raises_without_any_internal_manifest_lines(self, organization, harrison):
        empty_shipment = Shipment.objects.create(organization=organization, reference="EMPTY-SHIP")
        doc_type = DocumentType.objects.create(organization=organization, code="freight_invoice2", name="Factura de Flete")
        document = Document.objects.create(organization=organization, document_type=doc_type, title="Flete vacío")
        cost_document = CostDocument.objects.create(
            shipment=empty_shipment, cost_type=CostDocument.CostType.FREIGHT, document=document,
            amount=Decimal("500"), currency_id=_currency_id("USD"),
        )
        charge = CostCharge.objects.create(cost_document=cost_document, description="Flete", amount=Decimal("500"))
        with pytest.raises(services.AllocationError):
            services.run_allocation(charge, empty_shipment, CostAllocationRun.AllocationMethod.QUANTITY, harrison)


class TestCalculateLandedCost:
    def test_aggregates_freight_into_per_unit_cost(self, shipment, internal_manifest_lines, cost_charge, harrison):
        services.run_allocation(cost_charge, shipment, CostAllocationRun.AllocationMethod.QUANTITY, harrison)
        version = services.calculate_landed_cost(shipment, harrison)
        assert version.version_number == 1
        line_a, line_b = internal_manifest_lines
        cost_line_a = version.lines.get(manifest_line=line_a)
        # 333.33 freight / 10 units = 33.333
        assert cost_line_a.freight_per_unit == Decimal("33.3330")

    def test_never_overwrites_a_previous_version(self, shipment, internal_manifest_lines, cost_charge, harrison):
        services.run_allocation(cost_charge, shipment, CostAllocationRun.AllocationMethod.QUANTITY, harrison)
        v1 = services.calculate_landed_cost(shipment, harrison)
        v2 = services.calculate_landed_cost(shipment, harrison)
        assert v2.version_number == v1.version_number + 1
        assert LandedCostVersion.objects.filter(shipment=shipment).count() == 2
        v1.refresh_from_db()
        assert v1.lines.exists()  # untouched, not deleted or cleared

    def test_original_unit_price_traced_from_purchase_order_line(self, shipment, internal_manifest_lines, cost_charge, harrison, organization):
        line_a, _ = internal_manifest_lines
        supplier = Supplier.objects.create(organization=organization, name="Proveedor Costo Test")
        po = PurchaseOrder.objects.create(organization=organization, supplier=supplier, po_number="PO-COST-TEST", currency="USD")
        po_line = PurchaseOrderLine.objects.create(
            purchase_order=po, line_no=1, description="Línea A", quantity_ordered=Decimal("10"),
            unit_of_measure=line_a.unit_of_measure, unit_price=Decimal("50.00"), line_total=Decimal("500.00"),
        )
        ManifestLineSource.objects.create(manifest_line=line_a, purchase_order_line=po_line)

        services.run_allocation(cost_charge, shipment, CostAllocationRun.AllocationMethod.QUANTITY, harrison)
        version = services.calculate_landed_cost(shipment, harrison)
        cost_line_a = version.lines.get(manifest_line=line_a)
        assert cost_line_a.original_unit_price == Decimal("50.00")
        assert cost_line_a.base_currency_unit_price == Decimal("50.00")  # USD == org default currency, no conversion needed
        assert cost_line_a.final_landed_cost_per_unit is not None
        assert cost_line_a.total_landed_value == (cost_line_a.final_landed_cost_per_unit * line_a.quantity).quantize(Decimal("0.01"))

    def test_finalize_marks_final_once(self, shipment, internal_manifest_lines, cost_charge, harrison):
        services.run_allocation(cost_charge, shipment, CostAllocationRun.AllocationMethod.QUANTITY, harrison)
        version = services.calculate_landed_cost(shipment, harrison)
        assert version.is_final is False
        services.finalize_landed_cost(version, harrison)
        version.refresh_from_db()
        assert version.is_final is True
        assert version.finalized_by == harrison
        with pytest.raises(services.AllocationError):
            services.finalize_landed_cost(version, harrison)


class TestShipmentCostDashboardHTTP:
    def test_dashboard_shows_charges_and_run_form(self, client, harrison, shipment, internal_manifest_lines, cost_charge):
        client.force_login(harrison)
        response = client.get(reverse("cost:shipment-dashboard", args=[shipment.pk]))
        assert response.status_code == 200
        assert "Flete internacional" in response.content.decode()

    def test_run_allocation_via_http_creates_allocation_run(self, client, harrison, shipment, internal_manifest_lines, cost_charge):
        client.force_login(harrison)
        response = client.post(
            reverse("cost:shipment-run-allocation", args=[shipment.pk]),
            {"cost_charge": str(cost_charge.pk), "method": CostAllocationRun.AllocationMethod.QUANTITY},
        )
        assert response.status_code == 302
        assert CostAllocationRun.objects.filter(shipment=shipment).count() == 1

    def test_calculate_version_via_http_then_finalize(self, client, harrison, shipment, internal_manifest_lines, cost_charge):
        client.force_login(harrison)
        client.post(
            reverse("cost:shipment-run-allocation", args=[shipment.pk]),
            {"cost_charge": str(cost_charge.pk), "method": CostAllocationRun.AllocationMethod.QUANTITY},
        )
        response = client.post(reverse("cost:shipment-calculate-version", args=[shipment.pk]))
        assert response.status_code == 302
        version = LandedCostVersion.objects.get(shipment=shipment)

        finalize_response = client.post(reverse("cost:finalize", args=[version.pk]))
        assert finalize_response.status_code == 302
        version.refresh_from_db()
        assert version.is_final is True

    def test_cross_organization_access_denied(self, client, shipment):
        from django.contrib.auth import get_user_model

        from apps.accounts.models import Organization, UserProfile

        User = get_user_model()
        other_org = Organization.objects.create(name="Other Org Cost Test")
        outsider = User.objects.create_user(username="outsider_cost", password="testpass123")
        UserProfile.objects.create(user=outsider, organization=other_org)

        client.force_login(outsider)
        response = client.get(reverse("cost:shipment-dashboard", args=[shipment.pk]))
        assert response.status_code == 404
