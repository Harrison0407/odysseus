from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path("", views.document_list, name="list"),
    path("subir/", views.document_upload, name="upload"),
    path("<uuid:pk>/", views.document_detail, name="detail"),
    path("<uuid:pk>/descargar/<uuid:version_pk>/", views.document_download, name="download"),
]
