from django.urls import path

from . import views

app_name = "evidenceinbox"

urlpatterns = [
    path("", views.inbox_list, name="list"),
    path("subir/", views.inbox_upload, name="upload"),
    path("<uuid:pk>/", views.inbox_detail, name="detail"),
    path("<uuid:pk>/clasificar/", views.inbox_classify, name="classify"),
    path("clasificaciones/<uuid:classification_pk>/reasignar/", views.inbox_reclassify, name="reclassify"),
    path("clasificar-lote/", views.inbox_batch_classify, name="batch-classify"),
]
