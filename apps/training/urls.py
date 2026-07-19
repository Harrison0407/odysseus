from django.urls import path

from . import views

app_name = "training"

urlpatterns = [
    path("", views.session_list, name="list"),
    path("nueva/", views.session_create, name="create"),
    path("<uuid:pk>/", views.session_detail, name="detail"),
    path("<uuid:pk>/iniciar/", views.session_start, name="start"),
    path("<uuid:pk>/finalizar/", views.session_finish, name="finish"),
    path("<uuid:pk>/checklist/", views.session_add_checklist_item, name="add-checklist-item"),
    path("<uuid:pk>/evidencia/", views.session_add_evidence, name="add-evidence"),
    path("<uuid:pk>/reconocer/", views.session_acknowledge, name="acknowledge"),
    path("<uuid:pk>/firma-supervisor/", views.session_supervisor_sign_off, name="supervisor-sign-off"),
    path("<uuid:pk>/aprobar-referencia/", views.session_approve_reference, name="approve-reference"),
    path("<uuid:pk>/crear-incidencia/", views.session_create_issue, name="create-issue"),
]
