from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("apps.core.urls")),
    path("documentos/", include("apps.documents.urls")),
    path("compras/", include("apps.procurement.urls")),
    path("embarques/", include("apps.shipments.urls")),
    path("recepcion/", include("apps.receiving.urls")),
    path("almacen/", include("apps.inventory.urls")),
    path("solicitudes/", include("apps.requests.urls")),
    path("costos/", include("apps.cost.urls")),
    path("reportes/", include("apps.reports.urls")),
    path("api/v1/", include("apps.api.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
