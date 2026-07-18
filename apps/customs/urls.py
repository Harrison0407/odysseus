from django.urls import path

from . import views

app_name = "customs"

urlpatterns = [
    path("confotur/", views.confotur_list_list, name="confotur-list"),
    path("confotur/<uuid:pk>/", views.confotur_list_detail, name="confotur-detail"),
    path("confotur/reconciliacion/", views.confotur_reconciliation, name="reconciliation"),
    path("confotur/lineas/<uuid:pk>/confirmar-duplicado/", views.confotur_confirm_duplicate, name="confirm-duplicate"),
]
