from django.urls import path

from . import views

app_name = "tools"

urlpatterns = [
    path("", views.tool_list, name="list"),
    path("<uuid:pk>/", views.tool_detail, name="detail"),
    path("<uuid:pk>/entregar/", views.tool_checkout, name="checkout"),
    path("<uuid:pk>/entrega/<uuid:checkout_pk>/devolver/", views.tool_return, name="return"),
    path("<uuid:pk>/reparacion/", views.tool_repair, name="repair"),
]
