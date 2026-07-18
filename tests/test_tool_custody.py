"""Tests for tool custody (apps.tools.services/views) — closes the
Priority 1 gap "tool custody screens." Also regression-tests a real,
previously undetected bug found while building this: the Almacén
persona dashboard (apps.core.views.dashboard_home) crashed with a
FieldError for any almacen/recepcion-role user, because it filtered
ToolCheckout by a field (`actual_return_date`) that only exists on the
related ToolReturn model.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.models import UserProfile
from apps.tools import services
from apps.tools.models import Tool, ToolRepair, ToolReturn

pytestmark = pytest.mark.django_db


@pytest.fixture
def tool(organization):
    return Tool.objects.create(organization=organization, code="TOOL-TEST-1", description="Taladro de prueba")


class TestCheckoutAndReturn:
    def test_checkout_creates_assignment_and_checkout(self, tool, miguel, harrison):
        checkout = services.checkout_tool(tool, miguel, harrison, expected_return_date=date.today() + timedelta(days=7))
        assert checkout.assignment.tool == tool
        assert checkout.assignment.assigned_to == miguel
        assert checkout.assignment.is_active is True
        assert services.is_checked_out(tool) is True

    def test_cannot_check_out_a_tool_already_checked_out(self, tool, miguel, harrison):
        services.checkout_tool(tool, miguel, harrison)
        with pytest.raises(services.ToolCustodyError):
            services.checkout_tool(tool, harrison, harrison)

    def test_return_closes_assignment_and_allows_new_checkout(self, tool, miguel, harrison):
        checkout = services.checkout_tool(tool, miguel, harrison)
        services.return_tool(checkout, harrison)
        assert services.is_checked_out(tool) is False
        checkout.assignment.refresh_from_db()
        assert checkout.assignment.is_active is False
        # a fresh checkout is now allowed
        services.checkout_tool(tool, harrison, harrison)
        assert services.is_checked_out(tool) is True

    def test_return_with_damage_updates_tool_condition(self, tool, miguel, harrison):
        checkout = services.checkout_tool(tool, miguel, harrison)
        services.return_tool(checkout, harrison, damage_noted=True, damage_description="Cable dañado.")
        tool.refresh_from_db()
        assert "reparación" in tool.condition.lower() or "dañ" in tool.condition.lower()
        tool_return = ToolReturn.objects.get(checkout=checkout)
        assert tool_return.damage_noted is True

    def test_cannot_return_twice(self, tool, miguel, harrison):
        checkout = services.checkout_tool(tool, miguel, harrison)
        services.return_tool(checkout, harrison)
        with pytest.raises(services.ToolCustodyError):
            services.return_tool(checkout, harrison)


class TestRepair:
    def test_record_repair_with_write_off_updates_condition(self, tool, harrison):
        services.record_repair(tool, harrison, description="Motor quemado, irreparable.", cost=Decimal("50.00"), resulted_in_write_off=True)
        tool.refresh_from_db()
        assert "baja" in tool.condition.lower()
        assert ToolRepair.objects.filter(tool=tool, resulted_in_write_off=True).exists()


class TestToolCustodyHTTP:
    def test_tool_detail_shows_checkout_form_when_available(self, client, harrison, tool):
        client.force_login(harrison)
        response = client.get(reverse("tools:detail", args=[tool.pk]))
        assert response.status_code == 200
        assert "Entregar herramienta" in response.content.decode()

    def test_checkout_via_http_then_detail_shows_return_form(self, client, harrison, miguel, tool):
        client.force_login(harrison)
        response = client.post(reverse("tools:checkout", args=[tool.pk]), {"assigned_to": miguel.pk})
        assert response.status_code == 302
        detail = client.get(reverse("tools:detail", args=[tool.pk]))
        assert "Registrar devolución" in detail.content.decode()

    def test_cross_organization_access_denied(self, client, tool):
        from django.contrib.auth import get_user_model

        from apps.accounts.models import Organization

        User = get_user_model()
        other_org = Organization.objects.create(name="Other Org Tools Test")
        outsider = User.objects.create_user(username="outsider_tools", password="testpass123")
        UserProfile.objects.create(user=outsider, organization=other_org)

        client.force_login(outsider)
        response = client.get(reverse("tools:detail", args=[tool.pk]))
        assert response.status_code == 404


class TestAlmacenDashboardRegression:
    """Regression test for the FieldError bug found while building this
    feature: dashboard_home crashed for any almacen/recepcion-role user."""

    def test_almacen_dashboard_renders_without_error(self, client, manuel, tool, harrison):
        overdue_checkout = services.checkout_tool(tool, manuel, harrison, expected_return_date=date.today() - timedelta(days=3))
        client.force_login(manuel)
        response = client.get(reverse("dashboard-home"))
        assert response.status_code == 200
        assert "Herramientas no devueltas" in response.content.decode()
        assert str(overdue_checkout.assignment.tool) in response.content.decode()

    def test_almacen_dashboard_excludes_non_overdue_checkouts(self, client, manuel, tool, harrison):
        services.checkout_tool(tool, manuel, harrison, expected_return_date=date.today() + timedelta(days=30))
        client.force_login(manuel)
        response = client.get(reverse("dashboard-home"))
        assert response.status_code == 200
        assert "Sin herramientas vencidas." in response.content.decode()
