from django.urls import path

from . import views

app_name = "fieldissues"

urlpatterns = [
    path("", views.issue_list, name="list"),
    path("reportar/", views.issue_report, name="report"),
    path("<uuid:pk>/", views.issue_detail, name="detail"),
    path("<uuid:pk>/asignar/", views.issue_assign, name="assign"),
    path("<uuid:pk>/en-progreso/", views.issue_start_progress, name="start-progress"),
    path("<uuid:pk>/corregir/", views.issue_record_correction, name="record-correction"),
    path("<uuid:pk>/listo-para-verificar/", views.issue_mark_ready, name="mark-ready"),
    path("<uuid:pk>/verificar-cerrar/", views.issue_verify_close, name="verify-close"),
    path("<uuid:pk>/rechazar/", views.issue_reject, name="reject"),
    path("<uuid:pk>/reenviar/", views.issue_resubmit, name="resubmit"),
    path("<uuid:pk>/reinspeccion/", views.issue_mark_reinspection, name="mark-reinspection"),
    path("<uuid:pk>/evidencia/", views.issue_add_evidence, name="add-evidence"),
]
