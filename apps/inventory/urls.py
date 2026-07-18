from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("ubicaciones/", views.location_list, name="locations"),
    path("lotes/<uuid:pk>/", views.lot_detail, name="lot-detail"),
]
