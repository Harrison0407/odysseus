from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("embarque/<uuid:pk>/instantanea/", views.shipment_snapshot, name="shipment-snapshot"),
    path("compartir/<str:token>/", views.shared_view, name="shared-view"),
]
