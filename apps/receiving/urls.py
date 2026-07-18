from django.urls import path

from . import views

app_name = "receiving"

urlpatterns = [
    path("", views.receipt_list, name="list"),
    path("<uuid:pk>/", views.receipt_detail, name="detail"),
    path("<uuid:pk>/linea/<uuid:line_pk>/registrar/", views.receipt_line_update, name="line-update"),
    path("planes/<uuid:pk>/", views.receiving_plan_detail, name="plan-detail"),
    path("planes/<uuid:plan_pk>/comparaciones/nueva/", views.comparison_scenario_create, name="comparison-create"),
    path("comparaciones/<uuid:pk>/", views.comparison_scenario_detail, name="comparison-detail"),
    path("comparaciones/<uuid:pk>/opciones/nueva/", views.comparison_option_create, name="comparison-option-create"),
    path("comparaciones/<uuid:pk>/finalizar/", views.comparison_finalize, name="comparison-finalize"),
    path("comparaciones/<uuid:pk>/exportar/", views.comparison_export, name="comparison-export"),
]
