from django.urls import path

from . import views

app_name = "receiving"

urlpatterns = [
    path("", views.receipt_list, name="list"),
    path("<uuid:pk>/", views.receipt_detail, name="detail"),
    path("<uuid:pk>/linea/<uuid:line_pk>/registrar/", views.receipt_line_update, name="line-update"),
]
