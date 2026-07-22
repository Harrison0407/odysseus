from django.urls import path

from . import package_views, views

app_name = "procurement"

urlpatterns = [
    path("", views.po_list, name="po-list"),
    path("<uuid:pk>/", views.po_detail, name="po-detail"),
    path("lineas/<uuid:pk>/asignacion/", views.line_allocation_detail, name="line-allocation-detail"),
    path("lineas/<uuid:pk>/asignar/", views.line_allocate, name="line-allocate"),
    path("lineas/<uuid:pk>/confirmar-repuesto/", views.line_confirm_spare, name="line-confirm-spare"),
    path("asignaciones/<uuid:pk>/reasignar/", views.allocation_reassign, name="allocation-reassign"),
    path("repuestos/", views.purchased_spares_list, name="purchased-spares-list"),
    path("asignaciones/", views.allocations_by_destination, name="allocations-by-destination"),
    # ProcurementPackage / Controlled Transparency & Confidentiality
    path("paquetes/", package_views.package_list, name="package-list"),
    path("paquetes/<uuid:pk>/", package_views.package_detail, name="package-detail"),
    path("paquetes/<uuid:pk>/gates/inicializar/", package_views.package_gates_initialize, name="package-gates-initialize"),
    path("paquetes/<uuid:pk>/gates/a1/evaluar/", package_views.package_a1_evaluate, name="package-a1-evaluate"),
    path("paquetes/<uuid:pk>/gates/a1/solicitar-revision/", package_views.package_a1_request_review, name="package-a1-request-review"),
    path("paquetes/<uuid:pk>/gates/a1/decision/<str:decision>/", package_views.package_a1_decide, name="package-a1-decide"),
    path("paquetes/<uuid:pk>/gates/a1/nuevo-intento/", package_views.package_a1_open_attempt, name="package-a1-open-attempt"),
    path("paquetes/<uuid:pk>/cotizacion-fabrica/crear/", package_views.factory_quote_create, name="factory-quote-create"),
    path("paquetes/<uuid:pk>/hoja-comercial/crear/", package_views.commercial_sheet_create, name="commercial-sheet-create"),
    path("paquetes/<uuid:pk>/cotizacion-cliente/crear/", package_views.client_quote_create, name="client-quote-create"),
    path("cotizaciones-cliente/<uuid:pk>/aprobar/", package_views.client_quote_approve, name="client-quote-approve"),
    path("paquetes/<uuid:pk>/congelar/", package_views.package_freeze, name="package-freeze"),
    path("paquetes/<uuid:pk>/cambios/crear/", package_views.change_request_create, name="change-request-create"),
    path("cambios/<uuid:pk>/<str:decision>/", package_views.change_request_decide, name="change-request-decide"),
    path("paquetes/<uuid:pk>/divulgaciones/crear/", package_views.disclosure_grant_create, name="disclosure-grant-create"),
    path("divulgaciones/<uuid:pk>/revocar/", package_views.disclosure_grant_revoke, name="disclosure-grant-revoke"),
    path("paquetes/<uuid:pk>/evidencia/crear/", package_views.evidence_bundle_create, name="evidence-bundle-create"),
    path("evidencia/<uuid:pk>/agregar/", package_views.evidence_item_add, name="evidence-item-add"),
    path("evidencia-item/<uuid:pk>/verificar/", package_views.evidence_item_verify, name="evidence-item-verify"),
    path("paquetes/<uuid:pk>/aserciones/crear/", package_views.verification_assertion_create, name="verification-assertion-create"),
]
