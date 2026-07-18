from django.urls import path

from . import views

app_name = "requests"

urlpatterns = [
    path("", views.request_list, name="list"),
    path("nueva/", views.request_create, name="create"),
    path("<uuid:pk>/", views.request_detail, name="detail"),
    path("<uuid:pk>/aprobar/", views.request_approve, name="approve"),
    path("<uuid:pk>/linea/<uuid:line_pk>/reservar/", views.request_reserve_line, name="reserve-line"),
    path("<uuid:pk>/despachar/", views.request_dispatch, name="dispatch"),

    path("entregas/", views.delivery_list, name="delivery-list"),
    path("entregas/<uuid:pk>/", views.delivery_detail, name="delivery-detail"),
    path("entregas/<uuid:pk>/linea/<uuid:line_pk>/registrar/", views.delivery_record_line, name="delivery-record-line"),
    path("entregas/<uuid:pk>/completar/", views.delivery_complete, name="delivery-complete"),
    path("entregas/<uuid:pk>/recepcion/", views.delivery_create_receipt, name="delivery-create-receipt"),
    path("entregas/<uuid:pk>/evidencia/", views.delivery_add_evidence, name="delivery-add-evidence"),

    path("instalaciones/", views.installation_list, name="installation-list"),
    path("recepciones/<uuid:receipt_pk>/instalacion/nueva/", views.installation_create, name="installation-create"),
    path("instalaciones/<uuid:pk>/", views.installation_detail, name="installation-detail"),
    path("instalaciones/<uuid:pk>/progreso/", views.installation_progress, name="installation-progress"),
    path("instalaciones/<uuid:pk>/reconocer/", views.installation_acknowledge, name="installation-acknowledge"),
    path(
        "instalaciones/<uuid:pk>/confirmar-supervisor/",
        views.installation_supervisor_confirm,
        name="installation-supervisor-confirm",
    ),
    path("instalaciones/<uuid:pk>/inspeccionar/", views.installation_create_inspection, name="installation-create-inspection"),
    path("instalaciones/<uuid:pk>/aceptar-final/", views.installation_final_accept, name="installation-final-accept"),
    path("instalaciones/<uuid:pk>/evidencia/", views.installation_add_evidence, name="installation-add-evidence"),

    path("inspecciones/", views.inspection_list, name="inspection-list"),
    path("inspecciones/<uuid:pk>/", views.inspection_detail, name="inspection-detail"),
    path("inspecciones/<uuid:pk>/items/<uuid:item_pk>/cerrar/", views.inspection_close_item, name="inspection-close-item"),
    path("inspecciones/<uuid:pk>/firma-tecnica/", views.inspection_sign_off, name="inspection-sign-off"),
    path("inspecciones/<uuid:pk>/evidencia/", views.inspection_add_evidence, name="inspection-add-evidence"),

    path("aceptaciones/", views.acceptance_list, name="acceptance-list"),
]
