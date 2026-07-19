from django.urls import path

from . import views

app_name = "drawings"

urlpatterns = [
    path("", views.drawing_list, name="list"),
    path("<uuid:pk>/", views.drawing_detail, name="detail"),
    path("<uuid:pk>/reemplazar/", views.drawing_supersede, name="supersede"),
    path("<uuid:pk>/aprobar/", views.drawing_approve, name="approve"),
]
