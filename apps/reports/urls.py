from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("embarque/<uuid:pk>/instantanea/", views.shipment_snapshot, name="shipment-snapshot"),
    path("recepcion/<uuid:pk>/manifiesto/", views.receiving_manifest_snapshot, name="receiving-manifest-snapshot"),
    path("compartir/<str:token>/", views.shared_view, name="shared-view"),
]
