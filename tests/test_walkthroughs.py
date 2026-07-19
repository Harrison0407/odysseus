"""Tests for apartment walkthroughs and corrective actions (one-shot
release, section 6). Building-agnostic by construction — a dedicated
test creates walkthroughs across several different, independently
created buildings/families to prove nothing branches on which one it
is. A defect becomes a real `FieldIssue`; the walkthrough's own
"delivery readiness" is always computed by reading that FieldIssue's
status, never a second correction tracker.
"""

from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.documents.models import DocumentType
from apps.fieldissues.models import FieldIssue
from apps.projects.models import Building, BuildingFamily, Project
from apps.walkthroughs import services
from apps.walkthroughs.models import (
    Walkthrough,
    WalkthroughCategory,
    WalkthroughChecklistTemplateItem,
    WalkthroughItem,
    WalkthroughItemEvidence,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def project(organization):
    return Project.objects.create(organization=organization, name="Arena", code="arena-walkthrough-test")


@pytest.fixture
def family(project):
    return BuildingFamily.objects.create(project=project, code="arena-t1-walkthrough-test", display_name="ARENA T1")


@pytest.fixture
def building(project, family):
    return Building.objects.create(project=project, code="b11-walkthrough-test", name="Edificio 11", family=family, building_number="11")


@pytest.fixture
def another_building(project, family):
    """A second, independently created building to prove the same
    code path works without any per-building branching."""
    return Building.objects.create(project=project, code="b9-walkthrough-test", name="Edificio 9", family=family, building_number="9")


@pytest.fixture
def category(organization):
    return WalkthroughCategory.objects.create(organization=organization, code="windows-test", name="Ventanas")


@pytest.fixture
def doc_type(organization):
    return DocumentType.objects.create(organization=organization, code="walkthrough-evidence-test", name="Evidencia de recorrido")


def _attach(item, user, doc_type, stage, filename="foto.jpg"):
    return services.add_item_evidence(
        item, user, document_type=doc_type, title=f"Evidencia {stage}",
        uploaded_file=SimpleUploadedFile(filename, b"fake-jpeg-bytes", content_type="image/jpeg"), stage=stage,
    )


class TestBuildingAgnosticCreation:
    def test_walkthrough_works_for_any_building(self, building, another_building, harrison):
        w1 = services.create_walkthrough(building, harrison, purpose=Walkthrough.Purpose.QUALITY_CONTROL)
        w2 = services.create_walkthrough(another_building, harrison, purpose=Walkthrough.Purpose.CONSTRUCTION_PROGRESS)
        assert w1.building == building
        assert w2.building == another_building

    def test_all_six_purposes_supported(self, building, harrison):
        for purpose, _ in Walkthrough.Purpose.choices:
            w = services.create_walkthrough(building, harrison, purpose=purpose)
            assert w.purpose == purpose

    def test_walkthrough_scope_options(self, building, harrison):
        """building-only, floor, unit, and a selected group of units
        are all valid scopes for the same model/service code."""
        from apps.projects.models import Floor, Unit

        floor = Floor.objects.create(building=building, name="Piso 3", level=3)
        unit_a = Unit.objects.create(building=building, floor=floor, name="A3", permanent_code="ARENA-T1-B11-A3-WALKTHROUGH-TEST")
        unit_b = Unit.objects.create(building=building, floor=floor, name="B3", permanent_code="ARENA-T1-B11-B3-WALKTHROUGH-TEST")

        building_only = services.create_walkthrough(building, harrison, purpose=Walkthrough.Purpose.CONSTRUCTION_PROGRESS)
        floor_scoped = services.create_walkthrough(building, harrison, purpose=Walkthrough.Purpose.QUALITY_CONTROL, floor=floor)
        unit_scoped = services.create_walkthrough(building, harrison, purpose=Walkthrough.Purpose.QUALITY_CONTROL, unit=unit_a)
        group_scoped = services.create_walkthrough(building, harrison, purpose=Walkthrough.Purpose.QUALITY_CONTROL, units=[unit_a, unit_b])

        assert building_only.floor is None and building_only.unit is None
        assert floor_scoped.floor == floor
        assert unit_scoped.unit == unit_a
        assert set(group_scoped.units.all()) == {unit_a, unit_b}


class TestChecklistTemplate:
    def test_populate_from_template_creates_all_items(self, building, category, harrison):
        for i, description in enumerate(["Nivel del marco", "Plomo del marco", "Escuadra del marco"]):
            WalkthroughChecklistTemplateItem.objects.create(category=category, description=description, sort_order=i)
        walkthrough = services.create_walkthrough(building, harrison, purpose=Walkthrough.Purpose.QUALITY_CONTROL, category=category)
        created = services.populate_checklist_from_template(walkthrough, harrison)
        assert len(created) == 3
        assert walkthrough.items.count() == 3

    def test_populate_requires_a_category(self, building, harrison):
        walkthrough = services.create_walkthrough(building, harrison, purpose=Walkthrough.Purpose.QUALITY_CONTROL)
        with pytest.raises(services.WalkthroughError):
            services.populate_checklist_from_template(walkthrough, harrison)


class TestItemResultRecording:
    def test_digital_level_measurement_recorded(self, building, harrison):
        walkthrough = services.create_walkthrough(building, harrison, purpose=Walkthrough.Purpose.QUALITY_CONTROL)
        item = services.add_item(walkthrough, harrison, room_or_location="Ventana sala")
        services.record_item_result(
            item, harrison, checklist_result=WalkthroughItem.Result.FAIL, digital_level_reading="2.5mm/m fuera de nivel",
            level_condition=WalkthroughItem.ConditionCheck.NOT_OK, condition_found="Marco desnivelado 2.5mm/m",
            is_blocking_defect=True,
        )
        item.refresh_from_db()
        assert item.digital_level_reading == "2.5mm/m fuera de nivel"
        assert item.is_blocking_defect

    def test_condition_found_and_after_adjustment_both_preserved(self, building, harrison):
        walkthrough = services.create_walkthrough(building, harrison, purpose=Walkthrough.Purpose.QUALITY_CONTROL)
        item = services.add_item(walkthrough, harrison, room_or_location="Ventana dormitorio")
        services.record_item_result(
            item, harrison, checklist_result=WalkthroughItem.Result.CONDITIONAL,
            condition_found="Marco fuera de nivel 3mm/m", adjustment_performed="Re-nivelación del marco",
            condition_after_adjustment="Nivel corregido a 0.2mm/m",
        )
        item.refresh_from_db()
        assert item.condition_found == "Marco fuera de nivel 3mm/m"
        assert item.condition_after_adjustment == "Nivel corregido a 0.2mm/m"
        assert item.condition_found != item.condition_after_adjustment  # never overwritten into one field

    def test_before_during_after_evidence(self, building, harrison, doc_type):
        walkthrough = services.create_walkthrough(building, harrison, purpose=Walkthrough.Purpose.QUALITY_CONTROL)
        item = services.add_item(walkthrough, harrison, room_or_location="Ventana cocina")
        _attach(item, harrison, doc_type, WalkthroughItemEvidence.Stage.BEFORE, "b.jpg")
        _attach(item, harrison, doc_type, WalkthroughItemEvidence.Stage.DURING, "d.jpg")
        _attach(item, harrison, doc_type, WalkthroughItemEvidence.Stage.AFTER, "a.jpg")
        assert item.evidence.count() == 3


class TestCorrectiveIssueLinkage:
    def test_create_issue_from_item_preserves_relationship(self, building, harrison):
        walkthrough = services.create_walkthrough(building, harrison, purpose=Walkthrough.Purpose.QUALITY_CONTROL)
        item = services.add_item(walkthrough, harrison, room_or_location="Ventana desalineada")
        issue = services.create_issue_from_item(item, harrison, title="Ventana desalineada — corrección requerida")
        item.refresh_from_db()
        assert item.field_issue_id == issue.id
        assert issue.walkthrough_item_id == item.id
        assert issue.building_id == building.id

    def test_full_reject_resubmit_reinspect_cycle_via_linked_issue(self, building, harrison, manuel, doc_type):
        from apps.fieldissues import services as issue_services
        from apps.fieldissues.models import FieldIssueEvidence

        walkthrough = services.create_walkthrough(building, harrison, purpose=Walkthrough.Purpose.QUALITY_CONTROL)
        item = services.add_item(walkthrough, harrison, room_or_location="Ventana con defecto")
        issue = services.create_issue_from_item(item, harrison, title="Defecto de ventana")

        issue_services.assign_issue(issue, harrison, responsible_user=manuel)
        issue_services.start_progress(issue, manuel)
        issue_services.record_correction(
            issue, manuel, description="Primer ajuste.",
            before_evidence_waived_reason="Sin foto antes por urgencia — autorizado.",
        )
        issue_services.add_evidence(issue, manuel, document_type=doc_type, title="Después 1",
                                     uploaded_file=SimpleUploadedFile("a1.jpg", b"x", content_type="image/jpeg"),
                                     stage=FieldIssueEvidence.Stage.AFTER)
        issue_services.mark_ready_for_verification(issue, manuel)
        issue_services.reject_correction(issue, harrison, reason="Ajuste insuficiente.")
        issue_services.resubmit_correction(issue, manuel, description="Segundo ajuste.")
        issue_services.mark_for_reinspection(issue, harrison)
        issue_services.verify_and_close_issue(issue, harrison, notes="Correcto ahora.")

        issue.refresh_from_db()
        assert issue.status == FieldIssue.Status.VERIFIED_CLOSED


class TestDeliveryReadiness:
    def test_blocked_when_blocking_defect_open(self, building, harrison):
        walkthrough = services.create_walkthrough(building, harrison, purpose=Walkthrough.Purpose.PRE_DELIVERY_FINAL)
        item = services.add_item(walkthrough, harrison, room_or_location="Ventana con defecto bloqueante")
        services.record_item_result(item, harrison, checklist_result=WalkthroughItem.Result.FAIL, is_blocking_defect=True)
        summary = services.delivery_readiness_summary(walkthrough)
        assert summary.is_blocked
        with pytest.raises(services.WalkthroughError):
            services.mark_delivery_decision(walkthrough, harrison, decision=Walkthrough.DeliveryDecision.READY)

    def test_authorized_override_allows_ready_decision(self, building, harrison):
        walkthrough = services.create_walkthrough(building, harrison, purpose=Walkthrough.Purpose.PRE_DELIVERY_FINAL)
        item = services.add_item(walkthrough, harrison, room_or_location="Ventana con defecto bloqueante 2")
        services.record_item_result(item, harrison, checklist_result=WalkthroughItem.Result.FAIL, is_blocking_defect=True)
        services.mark_delivery_decision(
            walkthrough, harrison, decision=Walkthrough.DeliveryDecision.READY,
            override_reason="Cliente acepta entrega con defecto menor documentado, corrección programada.",
        )
        walkthrough.refresh_from_db()
        assert walkthrough.delivery_decision == Walkthrough.DeliveryDecision.READY
        assert walkthrough.delivery_overridden_by == harrison

    def test_unauthorized_override_denied(self, building, manuel):
        walkthrough = services.create_walkthrough(building, manuel, purpose=Walkthrough.Purpose.PRE_DELIVERY_FINAL)
        item = services.add_item(walkthrough, manuel, room_or_location="Ventana con defecto bloqueante 3")
        services.record_item_result(item, manuel, checklist_result=WalkthroughItem.Result.FAIL, is_blocking_defect=True)
        with pytest.raises(services.WalkthroughError):
            services.mark_delivery_decision(
                walkthrough, manuel, decision=Walkthrough.DeliveryDecision.READY, override_reason="Lo autorizo yo mismo.",
            )

    def test_ready_decision_allowed_with_no_blocking_defects(self, building, harrison):
        walkthrough = services.create_walkthrough(building, harrison, purpose=Walkthrough.Purpose.FINAL_HANDOVER_DELIVERY)
        item = services.add_item(walkthrough, harrison, room_or_location="Ventana perfecta")
        services.record_item_result(item, harrison, checklist_result=WalkthroughItem.Result.PASS)
        services.mark_delivery_decision(walkthrough, harrison, decision=Walkthrough.DeliveryDecision.READY)
        walkthrough.refresh_from_db()
        assert walkthrough.delivery_decision == Walkthrough.DeliveryDecision.READY
        assert walkthrough.delivery_override_reason == ""


class TestReinspectionAndSequential:
    def test_reinspection_preserves_previous_walkthrough(self, building, harrison):
        original = services.create_walkthrough(building, harrison, purpose=Walkthrough.Purpose.QUALITY_CONTROL)
        services.add_item(original, harrison, room_or_location="Item original")
        reinspection = services.create_reinspection_walkthrough(original, harrison)
        assert reinspection.previous_walkthrough_id == original.id
        assert reinspection.purpose == Walkthrough.Purpose.REINSPECTION
        # original untouched
        assert Walkthrough.objects.get(pk=original.pk).items.count() == 1

    def test_sequential_walkthrough_for_next_unit(self, building, harrison):
        from apps.projects.models import Floor, Unit

        floor = Floor.objects.create(building=building, name="Piso 5", level=5)
        unit_a = Unit.objects.create(building=building, floor=floor, name="A5", permanent_code="ARENA-T1-B11-A5-WALKTHROUGH-TEST")
        unit_b = Unit.objects.create(building=building, floor=floor, name="B5", permanent_code="ARENA-T1-B11-B5-WALKTHROUGH-TEST")
        first = services.create_walkthrough(building, harrison, purpose=Walkthrough.Purpose.QUALITY_CONTROL, floor=floor, unit=unit_a)
        second = services.create_next_sequential_walkthrough(first, unit_b, harrison)
        assert second.unit == unit_b
        assert second.floor == floor
        assert second.purpose == first.purpose


class TestIsolationAndHTTP:
    def test_cross_organization_walkthrough_access_denied(self, client, building, harrison):
        from django.contrib.auth import get_user_model

        from apps.accounts.models import Organization, UserProfile

        walkthrough = services.create_walkthrough(building, harrison, purpose=Walkthrough.Purpose.QUALITY_CONTROL)
        User = get_user_model()
        other_org = Organization.objects.create(name="Other Org Walkthrough Test")
        outsider = User.objects.create_user(username="outsider_walkthrough", password="testpass123")
        UserProfile.objects.create(user=outsider, organization=other_org)

        client.force_login(outsider)
        response = client.get(reverse("walkthroughs:detail", args=[walkthrough.pk]))
        assert response.status_code == 404

    def test_full_http_workflow(self, client, building, harrison):
        client.force_login(harrison)
        response = client.post(reverse("walkthroughs:create"), {
            "building": building.pk, "purpose": Walkthrough.Purpose.QUALITY_CONTROL,
        })
        assert response.status_code == 302
        walkthrough = Walkthrough.objects.get(building=building)

        response = client.post(reverse("walkthroughs:add-item", args=[walkthrough.pk]), {"room_or_location": "Ventana HTTP"})
        assert response.status_code == 302
        item = WalkthroughItem.objects.get(walkthrough=walkthrough)

        response = client.post(reverse("walkthroughs:item-record-result", args=[item.pk]), {
            "checklist_result": "fail", "digital_level_reading": "3mm/m", "is_blocking_defect": "on",
        })
        assert response.status_code == 302
        item.refresh_from_db()
        assert item.is_blocking_defect

        response = client.get(reverse("walkthroughs:detail", args=[walkthrough.pk]))
        assert response.status_code == 200
        assert "Ventana HTTP" in response.content.decode()
