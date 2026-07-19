from django.urls import path

from . import views

app_name = "governance"

urlpatterns = [
    path("partes/", views.party_list, name="party-list"),
    path("partes/nueva/", views.party_create, name="party-create"),
    path("partes/<uuid:pk>/", views.party_detail, name="party-detail"),
    path("auditoria-privilegiada/", views.privileged_audit, name="privileged-audit"),
]
