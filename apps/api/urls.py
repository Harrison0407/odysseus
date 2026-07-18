from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("shipments", views.ShipmentViewSet, basename="shipment")
router.register("purchase-orders", views.PurchaseOrderViewSet, basename="purchase-order")
router.register("manifest-lines", views.ManifestLineViewSet, basename="manifest-line")
router.register("manifest-variances", views.ManifestVarianceViewSet, basename="manifest-variance")
router.register("discrepancies", views.DiscrepancyViewSet, basename="discrepancy")

urlpatterns = router.urls
