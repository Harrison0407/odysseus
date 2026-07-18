import pytest
from django.urls import reverse

from apps.procurement.models import PurchaseOrder, Supplier


@pytest.mark.django_db
def test_purchase_order_list_is_scoped_to_users_organization(client, manuel, organization, other_org_user):
    supplier = Supplier.objects.create(organization=organization, name="Test Supplier")
    PurchaseOrder.objects.create(organization=organization, supplier=supplier, po_number="VISIBLE-PO")

    other_supplier = Supplier.objects.create(organization=other_org_user.profile.organization, name="Other Supplier")
    PurchaseOrder.objects.create(
        organization=other_org_user.profile.organization, supplier=other_supplier, po_number="HIDDEN-PO"
    )

    client.force_login(manuel)
    response = client.get(reverse("procurement:po-list"))
    content = response.content.decode()
    assert "VISIBLE-PO" in content
    assert "HIDDEN-PO" not in content


@pytest.mark.django_db
def test_purchase_order_detail_denies_cross_organization_access(client, manuel, other_org_user):
    supplier = Supplier.objects.create(organization=other_org_user.profile.organization, name="Other Supplier")
    other_po = PurchaseOrder.objects.create(
        organization=other_org_user.profile.organization, supplier=supplier, po_number="NOT-MINE"
    )
    client.force_login(manuel)
    response = client.get(reverse("procurement:po-detail", args=[other_po.pk]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_anonymous_user_redirected_to_login(client):
    response = client.get(reverse("dashboard-home"))
    assert response.status_code == 302
    assert reverse("login") in response.url
