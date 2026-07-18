from django.urls import path

from . import views

app_name = "procurement"

urlpatterns = [
    path("", views.po_list, name="po-list"),
    path("<uuid:pk>/", views.po_detail, name="po-detail"),
]
