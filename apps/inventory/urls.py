from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("ubicaciones/", views.location_list, name="locations"),
    path("lotes/<uuid:pk>/", views.lot_detail, name="lot-detail"),
    path("conteos/", views.cycle_count_list, name="cycle-count-list"),
    path("conteos/nuevo/", views.cycle_count_create, name="cycle-count-create"),
    path("conteos/<uuid:pk>/", views.cycle_count_detail, name="cycle-count-detail"),
    path("conteos/<uuid:pk>/linea/<uuid:line_pk>/registrar/", views.cycle_count_record, name="cycle-count-record"),
    path(
        "conteos/<uuid:pk>/linea/<uuid:line_pk>/aprobar-ajuste/",
        views.cycle_count_approve_adjustment,
        name="cycle-count-approve-adjustment",
    ),
]
