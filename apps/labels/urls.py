from django.urls import path

from . import views

app_name = "labels"

urlpatterns = [
    path("<str:app_label>/<str:model>/<uuid:pk>/", views.label_print, name="print"),
    path("lote/imprimir/", views.label_batch_print, name="batch-print"),
    path("<uuid:label_pk>/invalidar/", views.label_invalidate, name="invalidate"),
]
