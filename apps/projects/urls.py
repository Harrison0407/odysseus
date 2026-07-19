from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    path("", views.building_family_list, name="family-list"),
    path("familias/<uuid:pk>/", views.building_family_detail, name="family-detail"),
    path("edificios/<uuid:pk>/", views.building_detail, name="building-detail"),
    path("unidades/", views.unit_search, name="unit-search"),
    path("unidades/<uuid:pk>/", views.unit_detail, name="unit-detail"),
]
