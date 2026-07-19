"""Tests for the Interactive Apartment Plan / Room-Zone layer (one-shot
release addendum). Templates are reusable across every unit sharing
family/unit-type-letter/floor-variant — never one row per apartment.
Only PALMERA (A/B/C/D) has a real per-unit-type source drawing; every
other family gets an honest Missing Source slot rather than an invented
room layout.
"""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse

import pytest

from apps.drawings.models import Drawing
from apps.fieldissues.models import FieldIssue
from apps.projects.models import Building, BuildingFamily, Floor, Project, Unit
from apps.unitplans import services
from apps.unitplans.models import PlanZone, UnitPlanAssignment, UnitPlanTemplate
from apps.walkthroughs.models import WalkthroughItem

pytestmark = pytest.mark.django_db


@pytest.fixture
def project(organization):
    return Project.objects.create(organization=organization, name="Mare", code="mare-unitplans-test")


@pytest.fixture
def family(project):
    return BuildingFamily.objects.create(project=project, code="mare-b-unitplans-test", display_name="MARE B")


@pytest.fixture
def building(project, family):
    return Building.objects.create(project=project, code="b25-unitplans-test", name="Edificio 25", family=family, building_number="25")


@pytest.fixture
def floor1(building):
    return Floor.objects.create(building=building, name="Piso 1", level=1)


@pytest.fixture
def floor4(building):
    return Floor.objects.create(building=building, name="Piso 4", level=4)


@pytest.fixture
def unit_c_floor1(building, floor1):
    return Unit.objects.create(building=building, floor=floor1, name="C1", apartment_letter="C", apartment_number="C1")


@pytest.fixture
def unit_c_floor4(building, floor4):
    return Unit.objects.create(building=building, floor=floor4, name="C4", apartment_letter="C", apartment_number="C4")


@pytest.fixture
def unit_a_penthouse(building, floor4):
    return Unit.objects.create(
        building=building, floor=floor4, name="A (PH)", apartment_letter="A (PH)", apartment_number="A (PH)4-5",
        is_penthouse=True,
    )


@pytest.fixture
def palmera_project(organization):
    return Project.objects.create(organization=organization, name="Palmera", code="palmera-unitplans-test")


@pytest.fixture
def palmera_family(palmera_project):
    return BuildingFamily.objects.create(project=palmera_project, code="palmera", display_name="PALMERA")


@pytest.fixture
def palmera_building(palmera_project, palmera_family):
    return Building.objects.create(project=palmera_project, code="b1-unitplans-test", name="Edificio 1", family=palmera_family, building_number="1")


@pytest.fixture
def palmera_unit_a(palmera_building):
    return Unit.objects.create(building=palmera_building, name="201", apartment_letter="A", apartment_number="201")


class TestFloorVariantResolution:
    def test_first_floor_vs_upper_floor(self, building, unit_c_floor1, unit_c_floor4, harrison):
        template1 = services.create_template(
            building.project.organization, building.family, unit_type_letter="C",
            floor_variant=UnitPlanTemplate.FloorVariant.FIRST_FLOOR, user=harrison,
        )
        template4 = services.create_template(
            building.project.organization, building.family, unit_type_letter="C",
            floor_variant=UnitPlanTemplate.FloorVariant.UPPER_FLOOR, user=harrison,
        )
        services.assign_unit_to_template(unit_c_floor1, template1, harrison)
        services.assign_unit_to_template(unit_c_floor4, template4, harrison)
        assert services.effective_template_for_unit(unit_c_floor1) == template1
        assert services.effective_template_for_unit(unit_c_floor4) == template4
        assert template1.floor_variant != template4.floor_variant

    def test_template_code_deterministic_by_family_letter_variant(self, family):
        code = services.template_code_for(family.code, "D", UnitPlanTemplate.FloorVariant.UPPER_FLOOR)
        assert code == f"{family.code}-d-upper_floor"

    def test_penthouse_duplex_variant(self, building, unit_a_penthouse, harrison):
        template = services.create_template(
            building.project.organization, building.family, unit_type_letter="A (PH)",
            floor_variant=UnitPlanTemplate.FloorVariant.PENTHOUSE_DUPLEX, user=harrison,
        )
        services.assign_unit_to_template(unit_a_penthouse, template, harrison)
        assert services.effective_template_for_unit(unit_a_penthouse).floor_variant == UnitPlanTemplate.FloorVariant.PENTHOUSE_DUPLEX

    def test_mirrored_variant_recorded(self, building, harrison):
        template = services.create_template(
            building.project.organization, building.family, unit_type_letter="B",
            floor_variant=UnitPlanTemplate.FloorVariant.UPPER_FLOOR, is_mirrored=True, orientation="espejado este",
            user=harrison,
        )
        assert template.is_mirrored
        assert template.orientation == "espejado este"


class TestDuplexZones:
    def test_zone_records_which_duplex_floor(self, building, unit_a_penthouse, harrison):
        template = services.create_template(
            building.project.organization, building.family, unit_type_letter="A (PH)",
            floor_variant=UnitPlanTemplate.FloorVariant.PENTHOUSE_DUPLEX, user=harrison,
        )
        lower = services.add_zone(
            template, zone_code="living-lower", name_es="Sala (nivel 4)", zone_type=PlanZone.ZoneType.LIVING_ROOM,
            coordinates={"x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.5}, duplex_floor="4", user=harrison,
        )
        upper = services.add_zone(
            template, zone_code="bedroom-upper", name_es="Habitación (nivel 5)", zone_type=PlanZone.ZoneType.PRIMARY_BEDROOM,
            coordinates={"x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.5}, duplex_floor="5", user=harrison,
        )
        assert lower.duplex_floor == "4"
        assert upper.duplex_floor == "5"


class TestProvenanceAndPersistence:
    def test_source_page_and_drawing_provenance(self, building, harrison, organization):
        drawing_doc = _make_document(organization, harrison)
        project_for_drawing = building.project
        drawing = Drawing.objects.create(
            project=project_for_drawing, drawing_type=Drawing.DrawingType.BUILDING_PLAN, title="Plano fuente test",
            source_document=drawing_doc, created_by=harrison,
        )
        template = services.create_template(
            organization, building.family, unit_type_letter="A", floor_variant=UnitPlanTemplate.FloorVariant.ALL_FLOORS,
            source_drawing=drawing, source_page=5, user=harrison,
        )
        assert template.source_drawing == drawing
        assert template.source_page == 5

    def test_room_polygon_coordinates_persist(self, building, harrison):
        template = services.create_template(
            building.project.organization, building.family, unit_type_letter="A",
            floor_variant=UnitPlanTemplate.FloorVariant.ALL_FLOORS, user=harrison,
        )
        polygon = [{"x": 0.1, "y": 0.1}, {"x": 0.4, "y": 0.1}, {"x": 0.4, "y": 0.4}, {"x": 0.1, "y": 0.4}]
        zone = services.add_zone(
            template, zone_code="kitchen", name_es="Cocina", zone_type=PlanZone.ZoneType.KITCHEN,
            coordinates=polygon, shape_type=PlanZone.ShapeType.POLYGON, user=harrison,
        )
        zone.refresh_from_db()
        assert zone.coordinates == polygon
        assert zone.shape_type == PlanZone.ShapeType.POLYGON


def _make_document(organization, user):
    from apps.core.storage import document_storage
    from apps.documents.models import Document, DocumentType, DocumentVersion

    doc_type, _ = DocumentType.objects.get_or_create(organization=organization, code="test-doc-type-unitplans", defaults={"name": "Test"})
    stored = document_storage.save(SimpleUploadedFile("source.pdf", b"fake-pdf-bytes"), "source.pdf")
    document = Document.objects.create(organization=organization, document_type=doc_type, title="Fuente", created_by=user)
    DocumentVersion.objects.create(
        document=document, version_number=1, stored_name=stored["stored_name"], original_filename=stored["original_filename"],
        sha256=stored["sha256"], size_bytes=stored["size_bytes"], mime_type="application/pdf", uploaded_by=user, created_by=user,
    )
    return document


class TestZoneClickPrefillsIssueAndWalkthroughItem:
    def test_issue_created_from_zone_carries_full_traceability(self, building, unit_c_floor1, harrison):
        template = services.create_template(
            building.project.organization, building.family, unit_type_letter="C",
            floor_variant=UnitPlanTemplate.FloorVariant.FIRST_FLOOR, user=harrison,
        )
        zone = services.add_zone(
            template, zone_code="bathroom", name_es="Baño", zone_type=PlanZone.ZoneType.BATHROOM,
            coordinates={"x0": 0, "y0": 0, "x1": 1, "y1": 1}, user=harrison,
        )
        from apps.fieldissues import services as issue_services

        issue = issue_services.report_issue(
            unit_c_floor1.building, harrison, title="Fuga en baño", floor=unit_c_floor1.floor, unit=unit_c_floor1,
            room_or_location=zone.name_es, plan_template=template, plan_zone=zone,
        )
        assert issue.plan_template_id == template.id
        assert issue.plan_zone_id == zone.id
        assert issue.unit_id == unit_c_floor1.id
        assert issue.building_id == unit_c_floor1.building_id

    def test_walkthrough_item_created_from_zone_carries_traceability(self, building, unit_c_floor1, harrison):
        template = services.create_template(
            building.project.organization, building.family, unit_type_letter="C",
            floor_variant=UnitPlanTemplate.FloorVariant.FIRST_FLOOR, user=harrison,
        )
        zone = services.add_zone(
            template, zone_code="kitchen", name_es="Cocina", zone_type=PlanZone.ZoneType.KITCHEN,
            coordinates={"x0": 0, "y0": 0, "x1": 1, "y1": 1}, user=harrison,
        )
        from apps.walkthroughs import services as walkthrough_services
        from apps.walkthroughs.models import Walkthrough

        walkthrough = walkthrough_services.create_walkthrough(
            unit_c_floor1.building, harrison, purpose=Walkthrough.Purpose.QUALITY_CONTROL, unit=unit_c_floor1,
        )
        item = walkthrough_services.add_item(
            walkthrough, harrison, room_or_location=zone.name_es, unit=unit_c_floor1,
            plan_template=template, plan_zone=zone,
        )
        assert item.plan_template_id == template.id
        assert item.plan_zone_id == zone.id


class TestSupersedeHistory:
    def test_supersede_template_preserves_old_row_and_copies_zones_as_needs_review(self, building, harrison):
        old_template = services.create_template(
            building.project.organization, building.family, unit_type_letter="A",
            floor_variant=UnitPlanTemplate.FloorVariant.ALL_FLOORS, status=UnitPlanTemplate.Status.APPROVED, user=harrison,
        )
        old_zone = services.add_zone(
            old_template, zone_code="kitchen", name_es="Cocina", zone_type=PlanZone.ZoneType.KITCHEN,
            coordinates={"x0": 0, "y0": 0, "x1": 0.5, "y1": 0.5}, validation_state=PlanZone.ValidationState.VALIDATED,
            user=harrison,
        )
        new_template = services.supersede_template(old_template, harrison, uncertainty_notes="Plano as-built cargado")

        old_template.refresh_from_db()
        assert old_template.status == UnitPlanTemplate.Status.SUPERSEDED
        assert old_template.is_current is False
        assert new_template.supersedes_id == old_template.id
        assert new_template.status == UnitPlanTemplate.Status.NEEDS_REVIEW

        old_zone.refresh_from_db()
        assert old_zone.is_active is False
        new_zone = old_zone.superseded_by
        assert new_zone.template_id == new_template.id
        assert new_zone.validation_state == PlanZone.ValidationState.NEEDS_REVIEW

    def test_supersede_moves_currently_assigned_units_to_new_revision(self, building, unit_c_floor1, harrison):
        old_template = services.create_template(
            building.project.organization, building.family, unit_type_letter="C",
            floor_variant=UnitPlanTemplate.FloorVariant.FIRST_FLOOR, status=UnitPlanTemplate.Status.APPROVED, user=harrison,
        )
        services.assign_unit_to_template(unit_c_floor1, old_template, harrison)
        new_template = services.supersede_template(old_template, harrison)
        assert services.effective_template_for_unit(unit_c_floor1) == new_template

    def test_existing_issue_keeps_pointing_at_superseded_template(self, building, unit_c_floor1, harrison):
        old_template = services.create_template(
            building.project.organization, building.family, unit_type_letter="C",
            floor_variant=UnitPlanTemplate.FloorVariant.FIRST_FLOOR, status=UnitPlanTemplate.Status.APPROVED, user=harrison,
        )
        zone = services.add_zone(
            old_template, zone_code="bathroom", name_es="Baño", zone_type=PlanZone.ZoneType.BATHROOM,
            coordinates={"x0": 0, "y0": 0, "x1": 1, "y1": 1}, user=harrison,
        )
        from apps.fieldissues import services as issue_services

        issue = issue_services.report_issue(
            unit_c_floor1.building, harrison, title="Grieta pared", unit=unit_c_floor1,
            plan_template=old_template, plan_zone=zone,
        )
        services.supersede_template(old_template, harrison)
        issue.refresh_from_db()
        assert issue.plan_template_id == old_template.id
        assert issue.plan_template.status == UnitPlanTemplate.Status.SUPERSEDED

    def test_supersede_zone_preserves_old_shape(self, building, harrison):
        template = services.create_template(
            building.project.organization, building.family, unit_type_letter="A",
            floor_variant=UnitPlanTemplate.FloorVariant.ALL_FLOORS, user=harrison,
        )
        zone = services.add_zone(
            template, zone_code="kitchen", name_es="Cocina", zone_type=PlanZone.ZoneType.KITCHEN,
            coordinates={"x0": 0, "y0": 0, "x1": 0.3, "y1": 0.3}, user=harrison,
        )
        new_zone = services.supersede_zone(zone, harrison, coordinates={"x0": 0.05, "y0": 0.05, "x1": 0.35, "y1": 0.35})
        zone.refresh_from_db()
        assert zone.is_active is False
        assert zone.coordinates == {"x0": 0, "y0": 0, "x1": 0.3, "y1": 0.3}
        assert new_zone.coordinates == {"x0": 0.05, "y0": 0.05, "x1": 0.35, "y1": 0.35}
        assert new_zone.supersedes_id == zone.id


class TestPermissionEnforcement:
    def test_unauthorized_user_cannot_approve_template(self, building, manuel):
        template = services.create_template(
            building.project.organization, building.family, unit_type_letter="A",
            floor_variant=UnitPlanTemplate.FloorVariant.ALL_FLOORS, status=UnitPlanTemplate.Status.NEEDS_REVIEW, user=manuel,
        )
        with pytest.raises(services.UnitPlanError):
            services.approve_template(template, manuel)

    def test_unauthorized_user_cannot_validate_zone(self, building, manuel):
        template = services.create_template(
            building.project.organization, building.family, unit_type_letter="A",
            floor_variant=UnitPlanTemplate.FloorVariant.ALL_FLOORS, user=manuel,
        )
        zone = PlanZone.objects.create(
            template=template, zone_code="kitchen", name_es="Cocina", zone_type=PlanZone.ZoneType.KITCHEN,
            coordinates={"x0": 0, "y0": 0, "x1": 1, "y1": 1}, created_by=manuel,
        )
        with pytest.raises(services.UnitPlanError):
            services.validate_zone(zone, manuel)

    def test_unauthorized_user_cannot_supersede_template(self, building, manuel):
        template = services.create_template(
            building.project.organization, building.family, unit_type_letter="A",
            floor_variant=UnitPlanTemplate.FloorVariant.ALL_FLOORS, status=UnitPlanTemplate.Status.APPROVED, user=manuel,
        )
        with pytest.raises(services.UnitPlanError):
            services.supersede_template(template, manuel)

    def test_authorized_user_can_approve(self, building, harrison):
        template = services.create_template(
            building.project.organization, building.family, unit_type_letter="A",
            floor_variant=UnitPlanTemplate.FloorVariant.ALL_FLOORS, status=UnitPlanTemplate.Status.NEEDS_REVIEW, user=harrison,
        )
        approved = services.approve_template(template, harrison)
        assert approved.status == UnitPlanTemplate.Status.APPROVED
        assert approved.verifier == harrison


class TestCrossOrganizationIsolation:
    def test_cannot_view_other_organizations_unit_plan(self, client, building, unit_c_floor1, harrison, other_org_user):
        template = services.create_template(
            building.project.organization, building.family, unit_type_letter="C",
            floor_variant=UnitPlanTemplate.FloorVariant.FIRST_FLOOR, user=harrison,
        )
        services.assign_unit_to_template(unit_c_floor1, template, harrison)
        client.force_login(other_org_user)
        response = client.get(reverse("unitplans:unit-plan", args=[unit_c_floor1.pk]))
        assert response.status_code == 404

    def test_cannot_administer_other_organizations_template(self, client, building, harrison, other_org_user):
        template = services.create_template(
            building.project.organization, building.family, unit_type_letter="C",
            floor_variant=UnitPlanTemplate.FloorVariant.FIRST_FLOOR, user=harrison,
        )
        client.force_login(other_org_user)
        response = client.get(reverse("unitplans:template-admin-detail", args=[template.pk]))
        assert response.status_code == 404


class TestHTTPWorkflow:
    def test_unit_plan_page_renders_zones(self, client, building, unit_c_floor1, harrison):
        template = services.create_template(
            building.project.organization, building.family, unit_type_letter="C",
            floor_variant=UnitPlanTemplate.FloorVariant.FIRST_FLOOR, status=UnitPlanTemplate.Status.APPROVED, user=harrison,
        )
        services.add_zone(
            template, zone_code="kitchen", name_es="Cocina", zone_type=PlanZone.ZoneType.KITCHEN,
            coordinates={"x0": 0.1, "y0": 0.1, "x1": 0.4, "y1": 0.4}, user=harrison,
        )
        services.assign_unit_to_template(unit_c_floor1, template, harrison)
        client.force_login(harrison)
        response = client.get(reverse("unitplans:unit-plan", args=[unit_c_floor1.pk]))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Cocina" in content
        assert "plan-zone" in content
        assert "plan-viewer-wrap" in content  # responsive/scrollable container class present

    def test_missing_source_unit_shows_configured_not_invented(self, client, building, unit_c_floor1, harrison):
        template = services.create_template(
            building.project.organization, building.family, unit_type_letter="C",
            floor_variant=UnitPlanTemplate.FloorVariant.FIRST_FLOOR, status=UnitPlanTemplate.Status.MISSING_SOURCE, user=harrison,
        )
        services.assign_unit_to_template(unit_c_floor1, template, harrison)
        client.force_login(harrison)
        response = client.get(reverse("unitplans:unit-plan", args=[unit_c_floor1.pk]))
        assert response.status_code == 200
        assert "Fuente faltante" in response.content.decode()

    def test_full_http_create_issue_from_zone(self, client, building, unit_c_floor1, harrison):
        template = services.create_template(
            building.project.organization, building.family, unit_type_letter="C",
            floor_variant=UnitPlanTemplate.FloorVariant.FIRST_FLOOR, status=UnitPlanTemplate.Status.APPROVED, user=harrison,
        )
        zone = services.add_zone(
            template, zone_code="kitchen", name_es="Cocina", zone_type=PlanZone.ZoneType.KITCHEN,
            coordinates={"x0": 0.1, "y0": 0.1, "x1": 0.4, "y1": 0.4}, user=harrison,
        )
        services.assign_unit_to_template(unit_c_floor1, template, harrison)
        client.force_login(harrison)
        response = client.post(
            reverse("unitplans:zone-create-issue", args=[zone.pk]) + f"?unit_id={unit_c_floor1.pk}",
            {"title": "Grifo dañado", "priority": "medium", "description": "Detectado en recorrido"},
        )
        assert response.status_code == 302
        issue = FieldIssue.objects.get(title="Grifo dañado")
        assert issue.plan_zone_id == zone.id
        assert issue.plan_template_id == template.id
        assert issue.unit_id == unit_c_floor1.id


class TestNoDuplicateOnReimport:
    def test_seed_command_is_idempotent(self, organization, building, unit_c_floor1, palmera_building, palmera_unit_a, harrison):
        call_command("seed_unit_plan_templates", organization=organization.name)
        template_count_1 = UnitPlanTemplate.objects.filter(organization=organization).count()
        assignment_count_1 = UnitPlanAssignment.objects.filter(unit__building__project__organization=organization).count()
        zone_count_1 = PlanZone.objects.filter(template__organization=organization).count()

        call_command("seed_unit_plan_templates", organization=organization.name)
        template_count_2 = UnitPlanTemplate.objects.filter(organization=organization).count()
        assignment_count_2 = UnitPlanAssignment.objects.filter(unit__building__project__organization=organization).count()
        zone_count_2 = PlanZone.objects.filter(template__organization=organization).count()

        assert template_count_1 == template_count_2
        assert assignment_count_1 == assignment_count_2
        assert zone_count_1 == zone_count_2
        assert template_count_1 > 0

    def test_palmera_units_get_real_zones_others_get_missing_source(self, organization, building, unit_c_floor1, palmera_building, palmera_unit_a, harrison):
        call_command("seed_unit_plan_templates", organization=organization.name)
        palmera_template = services.effective_template_for_unit(palmera_unit_a)
        mareb_template = services.effective_template_for_unit(unit_c_floor1)
        assert palmera_template.status == UnitPlanTemplate.Status.DRAFT
        assert palmera_template.zones.count() > 0
        assert mareb_template.status == UnitPlanTemplate.Status.MISSING_SOURCE
        assert mareb_template.zones.count() == 0
