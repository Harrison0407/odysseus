from django.urls import path

from . import views

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
]
