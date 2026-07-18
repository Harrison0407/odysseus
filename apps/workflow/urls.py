from django.urls import path

from . import views

app_name = "workflow"

urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("<uuid:pk>/", views.handoff_detail, name="detail"),
    path("<uuid:pk>/enviar/", views.handoff_submit, name="submit"),
    path("<uuid:pk>/anular-enviar/", views.handoff_override_submit, name="override-submit"),
    path("<uuid:pk>/aceptar/", views.handoff_accept, name="accept"),
    path("<uuid:pk>/rechazar/", views.handoff_reject, name="reject"),
    path("<uuid:pk>/devolver/", views.handoff_return, name="return"),
    path("<uuid:pk>/reenviar/", views.handoff_resubmit, name="resubmit"),
    path("<uuid:pk>/comentario/", views.handoff_comment, name="comment"),
    path("crear/<int:content_type_id>/<uuid:object_id>/<slug:gate_code>/", views.handoff_create, name="create"),
]
