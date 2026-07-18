import pytest
from django.contrib.auth import get_user_model

from apps.accounts.models import Department, Organization, Role, UserProfile, UserRole
from apps.items.models import ProductCategory, UnitOfMeasure

User = get_user_model()


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="DT Beach Test", default_currency="USD")


@pytest.fixture
def department_almacen(organization):
    return Department.objects.create(organization=organization, name="Almacén", code="almacen")


@pytest.fixture
def role_almacen(organization):
    return Role.objects.create(organization=organization, name="Almacén", code="almacen")


@pytest.fixture
def role_obra(organization):
    return Role.objects.create(organization=organization, name="Obra", code="obra")


@pytest.fixture
def manuel(organization, department_almacen, role_almacen):
    user = User.objects.create_user(username="manuel", password="testpass123")
    UserProfile.objects.create(user=user, organization=organization, primary_department=department_almacen)
    UserRole.objects.create(user=user, role=role_almacen, department=department_almacen)
    return user


@pytest.fixture
def miguel(organization, role_obra):
    user = User.objects.create_user(username="miguel", password="testpass123")
    UserProfile.objects.create(user=user, organization=organization)
    UserRole.objects.create(user=user, role=role_obra)
    return user


@pytest.fixture
def other_org_user(db):
    other_org = Organization.objects.create(name="Other Org")
    user = User.objects.create_user(username="outsider", password="testpass123")
    UserProfile.objects.create(user=user, organization=other_org)
    return user


@pytest.fixture
def set_uom(organization):
    return UnitOfMeasure.objects.create(organization=organization, code="SET", name="Set")


@pytest.fixture
def pc_uom(organization):
    return UnitOfMeasure.objects.create(organization=organization, code="PC", name="Pieza")


@pytest.fixture
def quartz_category(organization):
    return ProductCategory.objects.create(organization=organization, name="Cuarzo y piedra")
