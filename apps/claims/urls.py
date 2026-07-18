from django.urls import path

from . import views

app_name = "claims"

urlpatterns = [
    path("", views.claim_list, name="list"),
    path("nuevo/", views.claim_create, name="create"),
    path("<uuid:pk>/", views.claim_detail, name="detail"),
    path("<uuid:pk>/evidencia/", views.claim_evidence_upload, name="evidence-upload"),
    path("<uuid:pk>/aprobar/", views.claim_approve, name="approve"),
    path("<uuid:pk>/enviar/", views.claim_submit, name="submit"),
    path("<uuid:pk>/respuesta/", views.claim_record_response, name="record-response"),
    path("<uuid:pk>/resolver/", views.claim_resolve, name="resolve"),
    path("<uuid:pk>/cerrar/", views.claim_close, name="close"),
    path("<uuid:pk>/paquete/", views.claim_generate_package, name="package"),
]
