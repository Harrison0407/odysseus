from django.urls import path

from . import views

app_name = "unitplans"

urlpatterns = [
    path("unidades/<uuid:pk>/plano/", views.unit_plan, name="unit-plan"),
    path("plantillas/<uuid:pk>/imagen/", views.template_plan_image, name="template-plan-image"),
    path("zonas/<uuid:pk>/crear-incidencia/", views.zone_create_issue, name="zone-create-issue"),
    path("zonas/<uuid:pk>/agregar-foto/", views.zone_add_photo, name="zone-add-photo"),
    path("zonas/<uuid:pk>/crear-item-recorrido/", views.zone_create_walkthrough_item, name="zone-create-walkthrough-item"),
    path("admin-planos/", views.review_queue, name="review-queue"),
    path("admin-planos/plantillas/<uuid:pk>/", views.template_admin_detail, name="template-admin-detail"),
    path("admin-planos/plantillas/<uuid:pk>/aprobar/", views.template_approve, name="template-approve"),
    path("admin-planos/plantillas/<uuid:pk>/reemplazar/", views.template_supersede, name="template-supersede"),
    path("admin-planos/plantillas/<uuid:pk>/cargar-plano/", views.template_upload_plan, name="template-upload-plan"),
    path("admin-planos/plantillas/<uuid:pk>/agregar-zona/", views.zone_add, name="zone-add"),
    path("admin-planos/zonas/<uuid:pk>/editar/", views.zone_edit, name="zone-edit"),
    path("admin-planos/zonas/<uuid:pk>/validar/", views.zone_validate, name="zone-validate"),
]
