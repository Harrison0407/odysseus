from rest_framework import viewsets

from apps.matching.models import Discrepancy
from apps.procurement.models import PurchaseOrder
from apps.shipments.models import ManifestLine, ManifestVariance, Shipment

from . import serializers


class OrgScopedViewSet(viewsets.ReadOnlyModelViewSet):
    """Base for endpoints scoped to the requesting user's organization
    (spec section 31: organization and project scoping)."""

    organization_field = "organization"

    def get_queryset(self):
        qs = super().get_queryset()
        org = getattr(self.request.user, "profile", None) and self.request.user.profile.organization
        if org and self.organization_field:
            qs = qs.filter(**{self.organization_field: org})
        return qs


class ShipmentViewSet(OrgScopedViewSet):
    queryset = Shipment.objects.all().prefetch_related("bills_of_lading")
    serializer_class = serializers.ShipmentSerializer


class PurchaseOrderViewSet(OrgScopedViewSet):
    queryset = PurchaseOrder.objects.all().prefetch_related("lines")
    serializer_class = serializers.PurchaseOrderSerializer


class ManifestLineViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ManifestLine.objects.all()
    serializer_class = serializers.ManifestLineSerializer
    organization_field = None


class ManifestVarianceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ManifestVariance.objects.select_related("shipment")
    serializer_class = serializers.ManifestVarianceSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        org = getattr(self.request.user, "profile", None) and self.request.user.profile.organization
        if org:
            qs = qs.filter(shipment__organization=org)
        return qs


class DiscrepancyViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Discrepancy.objects.select_related("shipment")
    serializer_class = serializers.DiscrepancySerializer

    def get_queryset(self):
        qs = super().get_queryset()
        org = getattr(self.request.user, "profile", None) and self.request.user.profile.organization
        if org:
            qs = qs.filter(shipment__organization=org)
        return qs
