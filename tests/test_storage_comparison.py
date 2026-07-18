"""Tests for the external storage comparison calculator
(apps.receiving.services.create_comparison_scenario/add_storage_option/
compare_scenario_options/finalize_comparison_scenario) — closes the
Priority 1 gap "external storage comparison." Reuses
apps.cost.models.Currency/ExchangeRate (never fabricates a conversion
rate — same discipline as landed cost, ADR-026).
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.cost.models import Currency, ExchangeRate
from apps.inventory.models import LocationSuitability, StorageSite, WarehouseLocation, WarehouseZone
from apps.receiving import services
from apps.receiving.models import AlternativeStorageOption, ReceivingPlan, StorageComparisonScenario
from apps.shipments.models import Shipment

pytestmark = pytest.mark.django_db


@pytest.fixture
def usd(organization):
    return Currency.objects.get_or_create(code="USD", defaults={"name": "Dólar estadounidense"})[0]


@pytest.fixture
def cny(organization):
    return Currency.objects.get_or_create(code="CNY", defaults={"name": "Yuan chino"})[0]


@pytest.fixture
def shipment(organization):
    return Shipment.objects.create(organization=organization, reference="TEST-STORAGE-COMPARISON-SHIP")


@pytest.fixture
def receiving_plan(shipment):
    return ReceivingPlan.objects.create(shipment=shipment, has_sensitive_materials=True)


@pytest.fixture
def internal_location(organization):
    site = StorageSite.objects.create(organization=organization, name="Sitio Comparación", site_type=StorageSite.SiteType.CENTRAL_WAREHOUSE)
    zone = WarehouseZone.objects.create(site=site, name="Zona Comparación", code="zona-comparacion")
    location = WarehouseLocation.objects.create(zone=zone, code="COMPARE-1")
    LocationSuitability.objects.create(location=location, covered=True, dry=True, secure=True)
    return location


@pytest.fixture
def scenario(receiving_plan, harrison):
    return services.create_comparison_scenario(receiving_plan, harrison)


class TestCreateComparisonScenario:
    def test_first_scenario_is_version_1_and_current(self, scenario):
        assert scenario.version_number == 1
        assert scenario.is_current

    def test_new_version_supersedes_previous_without_deleting_it(self, receiving_plan, harrison, scenario):
        second = services.create_comparison_scenario(receiving_plan, harrison, name="Segunda ronda")
        scenario.refresh_from_db()
        assert second.version_number == 2
        assert second.is_current
        assert not scenario.is_current
        assert StorageComparisonScenario.objects.filter(pk=scenario.pk).exists()  # never deleted


class TestAddStorageOption:
    def test_add_option_succeeds(self, scenario, usd, harrison):
        option = services.add_storage_option(
            scenario, created_by=harrison, option_type=AlternativeStorageOption.OptionType.EXTERNAL_WAREHOUSE,
            option_name="Almacén externo Puerto Caucedo", currency=usd,
            storage_cost=Decimal("500"), handling_cost=Decimal("100"),
            inbound_transport_cost=Decimal("50"), outbound_transport_cost=Decimal("50"),
            insurance_cost=Decimal("20"),
        )
        assert option.scenario_id == scenario.id

    def test_cannot_add_option_to_finalized_scenario(self, scenario, usd, harrison):
        option = services.add_storage_option(
            scenario, created_by=harrison, option_name="Opción única", currency=usd, storage_cost=Decimal("100"),
        )
        services.finalize_comparison_scenario(scenario, option, harrison, rationale="Única opción viable.")
        with pytest.raises(services.StorageComparisonError):
            services.add_storage_option(scenario, created_by=harrison, option_name="Otra opción", currency=usd)


class TestCompareScenarioOptions:
    def test_complete_cost_calculation(self, scenario, usd, harrison):
        services.add_storage_option(
            scenario, created_by=harrison, option_name="Opción completa", currency=usd,
            storage_cost=Decimal("500"), handling_cost=Decimal("100"),
            inbound_transport_cost=Decimal("50"), outbound_transport_cost=Decimal("50"), insurance_cost=Decimal("20"),
        )
        results = services.compare_scenario_options(scenario)
        assert len(results) == 1
        assert results[0].guaranteed_total == Decimal("720")
        assert results[0].converted_total == Decimal("720")
        assert results[0].rank == 1

    def test_incomplete_inputs_report_unknown_not_zero(self, scenario, usd, harrison):
        services.add_storage_option(scenario, created_by=harrison, option_name="Sin costos", currency=usd)
        results = services.compare_scenario_options(scenario)
        assert results[0].guaranteed_total is None
        assert results[0].converted_total is None
        assert results[0].rank is None

    def test_minimum_commitment_floors_the_total(self, scenario, usd, harrison):
        services.add_storage_option(
            scenario, created_by=harrison, option_name="Con mínimo", currency=usd,
            storage_cost=Decimal("100"), minimum_commitment_amount=Decimal("400"),
        )
        results = services.compare_scenario_options(scenario)
        assert results[0].guaranteed_total == Decimal("400")
        assert results[0].used_minimum_commitment

    def test_demurrage_exposure_excluded_from_guaranteed_total(self, scenario, usd, harrison):
        services.add_storage_option(
            scenario, created_by=harrison, option_name="Con exposición", currency=usd,
            storage_cost=Decimal("100"), demurrage_penalty_estimated_cost=Decimal("9000"),
        )
        results = services.compare_scenario_options(scenario)
        assert results[0].guaranteed_total == Decimal("100")
        assert results[0].demurrage_exposure == Decimal("9000")

    def test_different_currency_without_rate_is_not_comparable(self, scenario, cny, harrison):
        services.add_storage_option(
            scenario, created_by=harrison, option_name="China holding", currency=cny, storage_cost=Decimal("3000"),
        )
        results = services.compare_scenario_options(scenario)
        assert results[0].converted_total is None
        assert "tasa de cambio" in results[0].conversion_note

    def test_provenance_recorded_conversion_succeeds(self, scenario, cny, harrison, organization):
        usd = Currency.objects.get_or_create(code="USD", defaults={"name": "Dólar"})[0]
        ExchangeRate.objects.create(
            from_currency=cny, to_currency=usd, rate=Decimal("0.14"), rate_date="2026-01-01",
            source="Banco Central, tasa de referencia 2026-01-01",
        )
        services.add_storage_option(
            scenario, created_by=harrison, option_name="China holding", currency=cny, storage_cost=Decimal("1000"),
        )
        results = services.compare_scenario_options(scenario)
        assert results[0].converted_total == Decimal("140.0000")

    def test_multi_option_ranking_lowest_cost_first(self, scenario, usd, harrison):
        services.add_storage_option(scenario, created_by=harrison, option_name="Caro", currency=usd, storage_cost=Decimal("900"))
        services.add_storage_option(scenario, created_by=harrison, option_name="Barato", currency=usd, storage_cost=Decimal("100"))
        results = services.compare_scenario_options(scenario)
        by_name = {r.option.option_name: r.rank for r in results}
        assert by_name["Barato"] == 1
        assert by_name["Caro"] == 2

    def test_suitability_scoring_warns_for_sensitive_material_without_conditions(self, scenario, usd, harrison):
        services.add_storage_option(
            scenario, created_by=harrison, option_name="Sin condiciones", currency=usd, storage_cost=Decimal("100"),
            covered=False, dry=False, secure=False,
        )
        results = services.compare_scenario_options(scenario)
        assert results[0].suitability_warnings

    def test_internal_baseline_reuses_location_suitability(self, scenario, usd, harrison, internal_location):
        services.add_storage_option(
            scenario, created_by=harrison, option_name="Almacén propio", currency=usd,
            option_type=AlternativeStorageOption.OptionType.INTERNAL_BASELINE,
            internal_location=internal_location, storage_cost=Decimal("0"),
        )
        results = services.compare_scenario_options(scenario)
        assert results[0].suitability_warnings == []  # location is covered/dry/secure


class TestFinalizeComparisonScenario:
    def test_finalize_requires_rationale(self, scenario, usd, harrison):
        option = services.add_storage_option(scenario, created_by=harrison, option_name="X", currency=usd, storage_cost=Decimal("1"))
        with pytest.raises(services.StorageComparisonError):
            services.finalize_comparison_scenario(scenario, option, harrison, rationale="")

    def test_finalize_rejects_option_from_another_scenario(self, scenario, receiving_plan, usd, harrison):
        other_scenario = services.create_comparison_scenario(receiving_plan, harrison, name="Otra")
        foreign_option = services.add_storage_option(other_scenario, created_by=harrison, option_name="Foránea", currency=usd, storage_cost=Decimal("1"))
        with pytest.raises(services.StorageComparisonError):
            services.finalize_comparison_scenario(scenario, foreign_option, harrison, rationale="Intento inválido")

    def test_finalize_records_immutable_decision(self, scenario, usd, harrison):
        option = services.add_storage_option(scenario, created_by=harrison, option_name="Elegida", currency=usd, storage_cost=Decimal("1"))
        services.finalize_comparison_scenario(scenario, option, harrison, rationale="Mejor costo total.")
        scenario.refresh_from_db()
        assert scenario.status == StorageComparisonScenario.Status.FINALIZED
        assert scenario.chosen_option_id == option.id
        assert scenario.decided_by == harrison

        from django.contrib.contenttypes.models import ContentType

        from apps.audit.models import AuditEvent

        assert AuditEvent.objects.filter(
            content_type=ContentType.objects.get_for_model(StorageComparisonScenario), object_id=scenario.pk,
        ).exists()

    def test_cannot_finalize_twice(self, scenario, usd, harrison):
        option = services.add_storage_option(scenario, created_by=harrison, option_name="X", currency=usd, storage_cost=Decimal("1"))
        services.finalize_comparison_scenario(scenario, option, harrison, rationale="Primera decisión.")
        with pytest.raises(services.StorageComparisonError):
            services.finalize_comparison_scenario(scenario, option, harrison, rationale="Segundo intento.")


class TestCrossOrganizationIsolation:
    def test_cannot_view_scenario_of_another_organization(self, client, scenario):
        from django.contrib.auth import get_user_model

        from apps.accounts.models import Organization, UserProfile

        User = get_user_model()
        other_org = Organization.objects.create(name="Other Org Storage Comparison Test")
        outsider = User.objects.create_user(username="outsider_comparison", password="testpass123")
        UserProfile.objects.create(user=outsider, organization=other_org)

        client.force_login(outsider)
        response = client.get(reverse("receiving:comparison-detail", args=[scenario.pk]))
        assert response.status_code == 404

    def test_cannot_view_receiving_plan_of_another_organization(self, client, receiving_plan):
        from django.contrib.auth import get_user_model

        from apps.accounts.models import Organization, UserProfile

        User = get_user_model()
        other_org = Organization.objects.create(name="Other Org Plan Test")
        outsider = User.objects.create_user(username="outsider_plan", password="testpass123")
        UserProfile.objects.create(user=outsider, organization=other_org)

        client.force_login(outsider)
        response = client.get(reverse("receiving:plan-detail", args=[receiving_plan.pk]))
        assert response.status_code == 404


class TestHTTPWorkflow:
    def test_full_scenario_lifecycle_via_http(self, client, receiving_plan, harrison, usd):
        client.force_login(harrison)
        create_response = client.post(reverse("receiving:comparison-create", args=[receiving_plan.pk]), {"name": "Primera"})
        assert create_response.status_code == 302
        scenario = StorageComparisonScenario.objects.get(receiving_plan=receiving_plan, version_number=1)

        option_response = client.post(reverse("receiving:comparison-option-create", args=[scenario.pk]), {
            "option_type": AlternativeStorageOption.OptionType.EXTERNAL_WAREHOUSE,
            "option_name": "Almacén de prueba HTTP",
            "currency": usd.pk,
            "storage_cost": "250",
        })
        assert option_response.status_code == 302
        option = AlternativeStorageOption.objects.get(scenario=scenario, option_name="Almacén de prueba HTTP")

        detail_response = client.get(reverse("receiving:comparison-detail", args=[scenario.pk]))
        assert detail_response.status_code == 200
        assert "Almacén de prueba HTTP" in detail_response.content.decode()

        finalize_response = client.post(reverse("receiving:comparison-finalize", args=[scenario.pk]), {
            "chosen_option": option.pk, "rationale": "Mejor opción disponible en la prueba HTTP.",
        })
        assert finalize_response.status_code == 302
        scenario.refresh_from_db()
        assert scenario.status == StorageComparisonScenario.Status.FINALIZED

    def test_export_downloads_html_snapshot(self, client, scenario, harrison, usd):
        services.add_storage_option(scenario, created_by=harrison, option_name="Exportable", currency=usd, storage_cost=Decimal("10"))
        client.force_login(harrison)
        response = client.get(reverse("receiving:comparison-export", args=[scenario.pk]))
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/html")
        assert "Exportable" in response.content.decode()
