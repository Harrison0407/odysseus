"""Tests for the physical property master (buildings/floors/units) —
one-shot release, section 1. Covers the idempotent import command,
permanent-code generation, and building/floor/unit access isolation.
"""

import pytest
from django.urls import reverse

from apps.accounts.models import UserProjectAccess
from apps.projects import services
from apps.projects.models import Building, BuildingFamily, Project, Unit

pytestmark = pytest.mark.django_db


class TestBuildPermanentCode:
    def test_multi_building_family_format(self, organization):
        project = Project.objects.create(organization=organization, name="Arena", code="arena")
        family = BuildingFamily.objects.create(project=project, code="arena-t1", display_name="ARENA T1")
        building = Building.objects.create(project=project, code="arena-t1-b11", name="ARENA T1 — Edificio 11", family=family, building_number="11")
        assert services.build_permanent_code(family, building, "A3") == "ARENA-T1-B11-A3"

    def test_single_building_family_without_segment(self, organization):
        project = Project.objects.create(organization=organization, name="Palmera", code="palmera")
        family = BuildingFamily.objects.create(project=project, code="palmera", display_name="PALMERA", uses_building_segment=False)
        building = Building.objects.create(project=project, code="palmera-b01", name="PALMERA — Edificio 1", family=family, building_number="1")
        assert services.build_permanent_code(family, building, "417") == "PALMERA-417"


class TestImportPhysicalPropertyMaster:
    def test_import_creates_expected_counts(self, organization):
        result = services.import_physical_property_master(organization)
        assert result.families_created == 9  # 6 active + 3 inactive catalog-only
        assert result.buildings_created == 24  # 8+8+3+3+1+1
        assert result.units_created == 684

    def test_import_is_idempotent(self, organization):
        services.import_physical_property_master(organization)
        second = services.import_physical_property_master(organization)
        assert second.families_created == 0
        assert second.buildings_created == 0
        assert second.units_created == 0
        assert second.units_skipped_unchanged == 684

    def test_dry_run_commits_nothing(self, organization):
        result = services.import_physical_property_master(organization, dry_run=True)
        assert result.units_created == 684
        assert Unit.objects.count() == 0

    def test_known_examples_resolve_to_real_units(self, organization):
        services.import_physical_property_master(organization)
        for code in ["ARENA-T1-B11-A3", "ARENA-T1-B12-A3", "MARE-B-B12-C4", "PALMERA-417"]:
            assert Unit.objects.filter(permanent_code=code).exists(), code

    def test_permanent_codes_are_globally_unique(self, organization):
        services.import_physical_property_master(organization)
        total = Unit.objects.count()
        distinct = Unit.objects.values_list("permanent_code", flat=True).distinct().count()
        assert total == distinct

    def test_inactive_families_have_no_buildings(self, organization):
        services.import_physical_property_master(organization)
        for code in ["mare-a", "arena-t2", "arena-t3"]:
            family = BuildingFamily.objects.get(project__organization=organization, code=code)
            assert not family.is_active
            assert family.buildings.count() == 0

    def test_active_families_match_canonical_list(self, organization):
        services.import_physical_property_master(organization)
        names = set(
            BuildingFamily.objects.filter(project__organization=organization, is_active=True).values_list("display_name", flat=True)
        )
        assert names == {"MARE B", "SOLE", "SOLE PH", "SOLE 26", "PALMERA", "ARENA T1"}

    def test_arena_t1_has_exactly_the_named_eight_buildings(self, organization):
        services.import_physical_property_master(organization)
        family = BuildingFamily.objects.get(project__organization=organization, code="arena-t1")
        numbers = set(family.buildings.values_list("building_number", flat=True))
        assert numbers == {"1", "2", "3", "4", "9", "10", "11", "12"}

    def test_arena_t1_floor_1_has_no_unit_d(self, organization):
        services.import_physical_property_master(organization)
        building = Building.objects.filter(family__code="arena-t1", building_number="1").first()
        floor1 = building.floors.get(level=1)
        assert not floor1.units.filter(apartment_letter="D").exists()
        floor2 = building.floors.get(level=2)
        assert floor2.units.filter(apartment_letter="D").exists()


@pytest.fixture
def imported(organization):
    services.import_physical_property_master(organization)
    return BuildingFamily.objects.filter(project__organization=organization, code="arena-t1").first()


class TestPropertyMasterHTTP:
    def test_family_list_shows_active_families(self, client, harrison, imported):
        client.force_login(harrison)
        response = client.get(reverse("projects:family-list"))
        assert response.status_code == 200
        assert "ARENA T1" in response.content.decode()

    def test_family_detail_lists_buildings(self, client, harrison, imported):
        client.force_login(harrison)
        response = client.get(reverse("projects:family-detail", args=[imported.pk]))
        assert response.status_code == 200

    def test_building_detail_shows_units(self, client, harrison, imported):
        building = imported.buildings.filter(building_number="11").first()
        client.force_login(harrison)
        response = client.get(reverse("projects:building-detail", args=[building.pk]))
        assert response.status_code == 200
        assert "ARENA-T1-B11-A3" in response.content.decode()

    def test_unit_detail_renders(self, client, harrison, imported):
        unit = Unit.objects.get(permanent_code="ARENA-T1-B11-A3")
        client.force_login(harrison)
        response = client.get(reverse("projects:unit-detail", args=[unit.pk]))
        assert response.status_code == 200

    def test_unit_search_by_permanent_code(self, client, harrison, imported):
        client.force_login(harrison)
        response = client.get(reverse("projects:unit-search"), {"q": "ARENA-T1-B11-A3"})
        assert response.status_code == 200
        assert "ARENA-T1-B11-A3" in response.content.decode()


class TestPropertyMasterIsolation:
    def test_cross_organization_building_access_denied(self, client, imported):
        from django.contrib.auth import get_user_model

        from apps.accounts.models import Organization, UserProfile

        User = get_user_model()
        other_org = Organization.objects.create(name="Other Org Property Test")
        outsider = User.objects.create_user(username="outsider_property", password="testpass123")
        UserProfile.objects.create(user=outsider, organization=other_org)

        building = imported.buildings.first()
        client.force_login(outsider)
        response = client.get(reverse("projects:building-detail", args=[building.pk]))
        assert response.status_code == 404

    def test_user_without_project_access_denied(self, client, miguel, imported):
        building = imported.buildings.first()
        client.force_login(miguel)
        response = client.get(reverse("projects:building-detail", args=[building.pk]))
        assert response.status_code == 404

    def test_user_with_project_access_allowed(self, client, miguel, imported):
        UserProjectAccess.objects.create(user=miguel, project=imported.project, access_level=UserProjectAccess.AccessLevel.READ_ONLY)
        building = imported.buildings.first()
        client.force_login(miguel)
        response = client.get(reverse("projects:building-detail", args=[building.pk]))
        assert response.status_code == 200

    def test_management_role_bypasses_project_access(self, client, harrison, imported):
        building = imported.buildings.first()
        client.force_login(harrison)
        response = client.get(reverse("projects:building-detail", args=[building.pk]))
        assert response.status_code == 200
