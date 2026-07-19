from django.urls import path

from . import views

app_name = "walkthroughs"

urlpatterns = [
    path("", views.walkthrough_list, name="list"),
    path("nuevo/", views.walkthrough_create, name="create"),
    path("<uuid:pk>/", views.walkthrough_detail, name="detail"),
    path("<uuid:pk>/poblar-checklist/", views.walkthrough_populate_checklist, name="populate-checklist"),
    path("<uuid:pk>/agregar-item/", views.walkthrough_add_item, name="add-item"),
    path("<uuid:pk>/progreso/", views.walkthrough_set_progress, name="set-progress"),
    path("<uuid:pk>/decision-entrega/", views.walkthrough_delivery_decision, name="delivery-decision"),
    path("<uuid:pk>/reinspeccion/", views.walkthrough_create_reinspection, name="create-reinspection"),
    path("<uuid:pk>/siguiente-secuencial/", views.walkthrough_create_next_sequential, name="create-next-sequential"),
    path("items/<uuid:pk>/registrar-resultado/", views.item_record_result, name="item-record-result"),
    path("items/<uuid:pk>/evidencia/", views.item_add_evidence, name="item-add-evidence"),
    path("items/<uuid:pk>/crear-incidencia/", views.item_create_issue, name="item-create-issue"),
]
