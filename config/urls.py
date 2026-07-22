from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from apps.accounts.views import RateLimitedLoginView
from apps.labels.views import qr_scan_landing

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", RateLimitedLoginView.as_view(template_name="registration/login.html"), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("qr/<str:token>/", qr_scan_landing, name="qr-scan"),
    path("", include("apps.core.urls")),
    path("documentos/", include("apps.documents.urls")),
    path("compras/", include("apps.procurement.urls")),
    path("embarques/", include("apps.shipments.urls")),
    path("recepcion/", include("apps.receiving.urls")),
    path("almacen/", include("apps.inventory.urls")),
    path("solicitudes/", include("apps.requests.urls")),
    path("costos/", include("apps.cost.urls")),
    path("aduanas/", include("apps.customs.urls")),
    path("herramientas/", include("apps.tools.urls")),
    path("reclamos/", include("apps.claims.urls")),
    path("etiquetas/", include("apps.labels.urls")),
    path("propiedades/", include("apps.projects.urls")),
    path("propiedades/", include("apps.unitplans.urls")),
    path("gobernanza/", include("apps.governance.urls")),
    path("planos/", include("apps.drawings.urls")),
    path("incidencias/", include("apps.fieldissues.urls")),
    path("capacitaciones/", include("apps.training.urls")),
    path("recorridos/", include("apps.walkthroughs.urls")),
    path("evidencias-sin-clasificar/", include("apps.evidenceinbox.urls")),
    path("flujo/", include("apps.workflow.urls")),
    path("reportes/", include("apps.reports.urls")),
    path("api/v1/", include("apps.api.urls")),
]

if settings.PROCUREMENT_PROTOTYPE_ENABLED:
    urlpatterns += [path("prototype/procurement/", include("apps.procurement_prototype.urls"))]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
