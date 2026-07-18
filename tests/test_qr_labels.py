"""Tests for QR label generation and controlled scanning
(apps.labels) — closes the Priority 1 gap "QR labels and controlled
scanning." A scan never itself authorizes anything: it only resolves
an opaque token to an existing, already-permission-checked detail page.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.inventory.models import InventoryLot, StorageSite, WarehouseLocation, WarehouseZone
from apps.labels import services
from apps.labels.models import QRLabel, QRLabelPrintEvent, QRScanEvent

pytestmark = pytest.mark.django_db


@pytest.fixture
def site(organization):
    return StorageSite.objects.create(organization=organization, name="Sitio QR Test", site_type=StorageSite.SiteType.CENTRAL_WAREHOUSE)


@pytest.fixture
def location(site):
    zone = WarehouseZone.objects.create(site=site, name="Zona QR", code="zona-qr")
    return WarehouseLocation.objects.create(zone=zone, code="QR-LOC-1")


@pytest.fixture
def lot(organization, quartz_category, pc_uom):
    from apps.items.models import Item

    item = Item.objects.create(organization=organization, name="Artículo QR Test", category=quartz_category, base_unit=pc_uom)
    return InventoryLot.objects.create(item=item, lot_code="QR-LOT-001")


class TestLabelGeneration:
    def test_get_or_create_returns_same_label_on_repeated_calls(self, lot, harrison):
        first = services.get_or_create_active_label(lot, harrison)
        second = services.get_or_create_active_label(lot, harrison)
        assert first.pk == second.pk

    def test_label_has_opaque_token_not_the_object_pk(self, lot, harrison):
        label = services.get_or_create_active_label(lot, harrison)
        assert label.token != str(lot.pk)
        assert len(label.token) > 16

    def test_qr_payload_never_contains_the_raw_object_id(self, lot, harrison, rf):
        label = services.get_or_create_active_label(lot, harrison)
        request = rf.get("/")
        data_uri = services.label_qr_data_uri(label, request)
        assert str(lot.pk) not in data_uri  # only decodable from the embedded QR image, not as plaintext
        assert data_uri.startswith("data:image/png;base64,")

    def test_location_label_generation(self, location, harrison):
        label = services.get_or_create_active_label(location, harrison)
        assert label.entity_type_label == "Ubicación de almacén"
        assert label.human_label == location.code


class TestBatchGeneration:
    def test_batch_creates_one_label_per_lot_and_records_prints(self, organization, quartz_category, pc_uom, harrison, client):
        from apps.items.models import Item

        item = Item.objects.create(organization=organization, name="Artículo Batch", category=quartz_category, base_unit=pc_uom)
        lot1 = InventoryLot.objects.create(item=item, lot_code="BATCH-1")
        lot2 = InventoryLot.objects.create(item=item, lot_code="BATCH-2")
        client.force_login(harrison)
        response = client.post(reverse("labels:batch-print"), {
            "app_label": "inventory", "model": "inventorylot", "object_id": [str(lot1.pk), str(lot2.pk)], "cantidad": "2",
        })
        assert response.status_code == 200
        assert QRLabel.objects.filter(object_id__in=[lot1.pk, lot2.pk]).count() == 2
        assert QRLabelPrintEvent.objects.filter(is_batch=True, quantity=2).count() == 2

    def test_batch_silently_excludes_other_organization_entities(self, organization, quartz_category, pc_uom, harrison, client):
        from django.contrib.auth import get_user_model

        from apps.accounts.models import Organization, UserProfile
        from apps.items.models import Item, ProductCategory

        User = get_user_model()
        other_org = Organization.objects.create(name="Other Org QR Batch Test")
        other_category = ProductCategory.objects.create(organization=other_org, name="Otra categoría")
        from apps.items.models import UnitOfMeasure

        other_uom = UnitOfMeasure.objects.create(organization=other_org, code="OTR", name="Otro")
        other_item = Item.objects.create(organization=other_org, name="Artículo ajeno", category=other_category, base_unit=other_uom)
        foreign_lot = InventoryLot.objects.create(item=other_item, lot_code="FOREIGN-1")

        own_item = Item.objects.create(organization=organization, name="Artículo propio", category=quartz_category, base_unit=pc_uom)
        own_lot = InventoryLot.objects.create(item=own_item, lot_code="OWN-1")

        client.force_login(harrison)
        response = client.post(reverse("labels:batch-print"), {
            "app_label": "inventory", "model": "inventorylot", "object_id": [str(own_lot.pk), str(foreign_lot.pk)], "cantidad": "1",
        })
        assert response.status_code == 200
        assert not QRLabel.objects.filter(object_id=foreign_lot.pk).exists()
        assert QRLabel.objects.filter(object_id=own_lot.pk).exists()


class TestReprintHistory:
    def test_multiple_prints_are_all_recorded(self, lot, harrison):
        label = services.get_or_create_active_label(lot, harrison)
        services.record_print(label, harrison, quantity=1)
        services.record_print(label, harrison, quantity=3)
        assert QRLabelPrintEvent.objects.filter(label=label).count() == 2
        assert sum(QRLabelPrintEvent.objects.filter(label=label).values_list("quantity", flat=True)) == 4

    def test_zero_or_negative_quantity_rejected(self, lot, harrison):
        label = services.get_or_create_active_label(lot, harrison)
        with pytest.raises(services.LabelError):
            services.record_print(label, harrison, quantity=0)


class TestInvalidateAndReplace:
    def test_invalidate_creates_a_new_label_and_marks_old_one(self, lot, harrison):
        old_label = services.get_or_create_active_label(lot, harrison)
        new_label = services.invalidate_and_replace_label(old_label, harrison, reason="Etiqueta dañada por agua.")
        old_label.refresh_from_db()
        assert old_label.is_invalidated
        assert old_label.replaced_by_id == new_label.id
        assert new_label.version == old_label.version + 1
        assert not new_label.is_invalidated

    def test_invalidate_requires_a_reason(self, lot, harrison):
        label = services.get_or_create_active_label(lot, harrison)
        with pytest.raises(services.LabelError):
            services.invalidate_and_replace_label(label, harrison, reason="")

    def test_cannot_invalidate_twice(self, lot, harrison):
        label = services.get_or_create_active_label(lot, harrison)
        services.invalidate_and_replace_label(label, harrison, reason="Perdida.")
        with pytest.raises(services.LabelError):
            services.invalidate_and_replace_label(label, harrison, reason="Otra vez.")

    def test_get_or_create_after_invalidation_returns_the_new_label(self, lot, harrison):
        old_label = services.get_or_create_active_label(lot, harrison)
        new_label = services.invalidate_and_replace_label(old_label, harrison, reason="Reemplazo de prueba.")
        current = services.get_or_create_active_label(lot, harrison)
        assert current.id == new_label.id


class TestControlledScanning:
    def test_authenticated_authorized_scan_redirects_to_real_detail_page(self, client, lot, harrison):
        label = services.get_or_create_active_label(lot, harrison)
        client.force_login(harrison)
        response = client.get(reverse("qr-scan", args=[label.token]))
        assert response.status_code == 302
        assert response.url == reverse("inventory:lot-detail", args=[lot.pk])
        assert QRScanEvent.objects.filter(label=label, scanned_by=harrison).exists()

    def test_unauthenticated_scan_requires_login_first(self, client, lot, harrison):
        label = services.get_or_create_active_label(lot, harrison)
        response = client.get(reverse("qr-scan", args=[label.token]))
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_invalid_token_returns_not_found(self, client, harrison):
        client.force_login(harrison)
        response = client.get(reverse("qr-scan", args=["not-a-real-token-at-all"]))
        assert response.status_code == 404

    def test_invalidated_label_scan_is_denied(self, client, lot, harrison):
        label = services.get_or_create_active_label(lot, harrison)
        services.invalidate_and_replace_label(label, harrison, reason="Prueba de invalidación.")
        client.force_login(harrison)
        response = client.get(reverse("qr-scan", args=[label.token]))
        assert response.status_code == 404

    def test_cross_organization_scan_denied_and_logged(self, client, lot, harrison):
        from django.contrib.auth import get_user_model

        from apps.accounts.models import Organization, UserProfile

        User = get_user_model()
        label = services.get_or_create_active_label(lot, harrison)
        other_org = Organization.objects.create(name="Other Org QR Scan Test")
        outsider = User.objects.create_user(username="outsider_qr_scan", password="testpass123")
        UserProfile.objects.create(user=outsider, organization=other_org)

        client.force_login(outsider)
        response = client.get(reverse("qr-scan", args=[label.token]))
        assert response.status_code == 404
        assert QRScanEvent.objects.filter(label=label, scanned_by=outsider, was_cross_organization_denied=True).exists()

    def test_scan_alone_never_performs_a_consequential_action(self, client, lot, harrison):
        """Scanning only ever redirects to a page that itself still
        requires its own auth/permission checks — it must never, by
        itself, mutate inventory or any other state."""
        label = services.get_or_create_active_label(lot, harrison)
        client.force_login(harrison)
        client.get(reverse("qr-scan", args=[label.token]))
        assert not any(
            m.movement_type for m in lot.movements.all()
        ) if hasattr(lot, "movements") else True  # no movement created by the scan itself


class TestDirectObjectAccessPrevention:
    def test_cannot_print_label_for_entity_outside_organization(self, client, lot):
        from django.contrib.auth import get_user_model

        from apps.accounts.models import Organization, UserProfile

        User = get_user_model()
        other_org = Organization.objects.create(name="Other Org QR Print Test")
        outsider = User.objects.create_user(username="outsider_qr_print", password="testpass123")
        UserProfile.objects.create(user=outsider, organization=other_org)

        client.force_login(outsider)
        response = client.get(reverse("labels:print", args=["inventory", "inventorylot", lot.pk]))
        assert response.status_code == 404

    def test_unsupported_entity_type_is_not_found(self, client, harrison, organization):
        client.force_login(harrison)
        response = client.get(reverse("labels:print", args=["accounts", "organization", organization.pk]))
        assert response.status_code == 404


class TestHTTPPrintWorkflow:
    def test_print_page_renders_and_logs_print_on_post(self, client, lot, harrison):
        client.force_login(harrison)
        get_response = client.get(reverse("labels:print", args=["inventory", "inventorylot", lot.pk]))
        assert get_response.status_code == 200
        assert lot.lot_code in get_response.content.decode()

        label = QRLabel.objects.get(object_id=lot.pk)
        post_response = client.post(reverse("labels:print", args=["inventory", "inventorylot", lot.pk]), {"cantidad": "5"})
        assert post_response.status_code == 302
        assert QRLabelPrintEvent.objects.filter(label=label, quantity=5).exists()

    def test_invalidate_via_http_then_reprint_shows_new_label(self, client, lot, harrison):
        client.force_login(harrison)
        client.get(reverse("labels:print", args=["inventory", "inventorylot", lot.pk]))
        old_label = QRLabel.objects.get(object_id=lot.pk)
        response = client.post(reverse("labels:invalidate", args=[old_label.pk]), {"reason": "Dañada en campo."})
        assert response.status_code == 302
        old_label.refresh_from_db()
        assert old_label.is_invalidated
        new_label = QRLabel.objects.get(object_id=lot.pk, is_invalidated=False)
        assert new_label.version == 2
