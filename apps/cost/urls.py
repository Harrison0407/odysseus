from django.urls import path

from . import views

app_name = "cost"

urlpatterns = [
    path("", views.landed_cost_list, name="list"),
    path("<uuid:pk>/", views.landed_cost_detail, name="detail"),
]
