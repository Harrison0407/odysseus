"""Tests for CONFOTUR duplicate-exemption reconciliation
(apps.customs.services/views) — closes the Priority 1 gap "CONFOTUR
reconciliation UI." Data model (`ConfoturLine.is_duplicate_of`) existed
since Priority 0; no detection/confirmation logic existed anywhere
before this session.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.customs import services
from apps.customs.models import ConfoturLine, ConfoturList
from apps.procurement.models import Quotation, Supplier

pytestmark = pytest.mark.django_db


@pytest.fixture
def confotur_list(organization):
    return ConfoturList.objects.create(organization=organization, list_number="CFT-TEST-1")


@pytest.fixture
def other_confotur_list(organization):
    return ConfoturList.objects.create(organization=organization, list_number="CFT-TEST-2")


@pytest.fixture
def quotation(organization):
    supplier = Supplier.objects.create(organization=organization, name="Proveedor CONFOTUR Test")
    return Quotation.objects.create(organization=organization, supplier=supplier, reference="W-TEST-CONFOTUR")


class TestDetectDuplicateCandidates:
    def test_no_candidates_when_each_quotation_claimed_once(self, organization, confotur_list, quotation):
        ConfoturLine.objects.create(confotur_list=confotur_list, quotation=quotation, requested_exemption=Decimal("100"))
        assert services.detect_duplicate_candidates(organization) == []

    def test_flags_same_quotation_claimed_in_two_lists(self, organization, confotur_list, other_confotur_list, quotation):
        line1 = ConfoturLine.objects.create(confotur_list=confotur_list, quotation=quotation, requested_exemption=Decimal("100"))
        line2 = ConfoturLine.objects.create(confotur_list=other_confotur_list, quotation=quotation, requested_exemption=Decimal("100"))
        candidates = services.detect_duplicate_candidates(organization)
        assert len(candidates) == 1
        assert candidates[0]["basis"] == "quotation"
        assert {l.pk for l in candidates[0]["lines"]} == {line1.pk, line2.pk}

    def test_resolved_pair_no_longer_a_candidate(self, organization, confotur_list, other_confotur_list, quotation, harrison):
        line1 = ConfoturLine.objects.create(confotur_list=confotur_list, quotation=quotation, requested_exemption=Decimal("100"))
        line2 = ConfoturLine.objects.create(confotur_list=other_confotur_list, quotation=quotation, requested_exemption=Decimal("100"))
        services.confirm_duplicate(line2, line1, harrison)
        assert services.detect_duplicate_candidates(organization) == []

    def test_flags_same_manifest_line_claimed_twice(self, organization, confotur_list, other_confotur_list, pc_uom, quartz_category):
        from apps.items.models import Item
        from apps.shipments.models import ManifestLine, ManifestPurpose, Shipment, ShipmentManifest, ShipmentManifestVersion
        from apps.core.models import DestinationScope

        shipment = Shipment.objects.create(organization=organization, reference="CONFOTUR-SHIP-TEST")
        item = Item.objects.create(organization=organization, name="Artículo CONFOTUR", category=quartz_category, base_unit=pc_uom)
        manifest = ShipmentManifest.objects.create(shipment=shipment, purpose=ManifestPurpose.INTERNAL_OPERATIONAL_MANIFEST)
        version = ShipmentManifestVersion.objects.create(manifest=manifest, version_number=1)
        manifest_line = ManifestLine.objects.create(
            manifest_version=version, line_no=1, description="Línea CONFOTUR", item=item,
            destination_scope=DestinationScope.PROJECT, quantity=Decimal("5"), unit_of_measure=pc_uom,
        )
        ConfoturLine.objects.create(confotur_list=confotur_list, manifest_line=manifest_line)
        ConfoturLine.objects.create(confotur_list=other_confotur_list, manifest_line=manifest_line)
        candidates = services.detect_duplicate_candidates(organization)
        assert len(candidates) == 1
        assert candidates[0]["basis"] == "manifest_line"


class TestConfirmDuplicate:
    def test_confirm_sets_is_duplicate_of_and_logs_audit(self, confotur_list, other_confotur_list, quotation, harrison):
        from django.contrib.contenttypes.models import ContentType

        from apps.audit.models import AuditEvent

        line1 = ConfoturLine.objects.create(confotur_list=confotur_list, quotation=quotation)
        line2 = ConfoturLine.objects.create(confotur_list=other_confotur_list, quotation=quotation)
        services.confirm_duplicate(line2, line1, harrison)
        line2.refresh_from_db()
        assert line2.is_duplicate_of_id == line1.pk
        assert AuditEvent.objects.filter(
            content_type=ContentType.objects.get_for_model(ConfoturLine), object_id=line2.pk
        ).exists()

    def test_cannot_mark_line_as_duplicate_of_itself(self, confotur_list, quotation, harrison):
        line = ConfoturLine.objects.create(confotur_list=confotur_list, quotation=quotation)
        with pytest.raises(ValueError):
            services.confirm_duplicate(line, line, harrison)

    def test_cannot_reconfirm_already_resolved_line(self, confotur_list, other_confotur_list, quotation, harrison):
        line1 = ConfoturLine.objects.create(confotur_list=confotur_list, quotation=quotation)
        line2 = ConfoturLine.objects.create(confotur_list=other_confotur_list, quotation=quotation)
        services.confirm_duplicate(line2, line1, harrison)
        with pytest.raises(ValueError):
            services.confirm_duplicate(line2, line1, harrison)


class TestReconciliationHTTP:
    def test_reconciliation_page_shows_candidate_and_form(self, client, harrison, confotur_list, other_confotur_list, quotation):
        ConfoturLine.objects.create(confotur_list=confotur_list, quotation=quotation)
        ConfoturLine.objects.create(confotur_list=other_confotur_list, quotation=quotation)
        client.force_login(harrison)
        response = client.get(reverse("customs:reconciliation"))
        assert response.status_code == 200
        assert "CFT-TEST-1" in response.content.decode()
        assert "CFT-TEST-2" in response.content.decode()

    def test_confirm_duplicate_via_http_resolves_candidate(self, client, harrison, confotur_list, other_confotur_list, quotation):
        line1 = ConfoturLine.objects.create(confotur_list=confotur_list, quotation=quotation)
        line2 = ConfoturLine.objects.create(confotur_list=other_confotur_list, quotation=quotation)
        client.force_login(harrison)
        response = client.post(
            reverse("customs:confirm-duplicate", args=[line2.pk]),
            {"duplicate_line": str(line1.pk)},
        )
        assert response.status_code == 302
        line2.refresh_from_db()
        assert line2.is_duplicate_of_id == line1.pk

        follow_up = client.get(reverse("customs:reconciliation"))
        assert "Sin candidatos a exención duplicada pendientes" in follow_up.content.decode()

    def test_cross_organization_access_denied(self, client, confotur_list):
        from django.contrib.auth import get_user_model

        from apps.accounts.models import Organization, UserProfile

        User = get_user_model()
        other_org = Organization.objects.create(name="Other Org Confotur Test")
        outsider = User.objects.create_user(username="outsider_confotur", password="testpass123")
        UserProfile.objects.create(user=outsider, organization=other_org)

        client.force_login(outsider)
        response = client.get(reverse("customs:confotur-detail", args=[confotur_list.pk]))
        assert response.status_code == 404
