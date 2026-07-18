from django.urls import path

from . import views

app_name = "cost"

urlpatterns = [
    path("", views.landed_cost_list, name="list"),
    path("<uuid:pk>/", views.landed_cost_detail, name="detail"),
    path("<uuid:pk>/finalizar/", views.landed_cost_finalize, name="finalize"),
    path("embarque/<uuid:shipment_pk>/", views.shipment_cost_dashboard, name="shipment-dashboard"),
    path("embarque/<uuid:shipment_pk>/asignar/", views.shipment_run_allocation, name="shipment-run-allocation"),
    path("embarque/<uuid:shipment_pk>/calcular/", views.shipment_calculate_version, name="shipment-calculate-version"),
]
