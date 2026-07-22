from django.urls import path
from . import views
app_name = "procurement_prototype"
urlpatterns = [path("", views.dashboard, name="dashboard"), path("a<int:gate>/", views.gate, name="gate"), path("comparison/", views.comparison, name="comparison"), path("action/", views.action, name="action"), path("export/<str:kind>/", views.export_csv, name="export")]
