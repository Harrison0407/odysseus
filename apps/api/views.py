from django.db.models import Q
from rest_framework import viewsets

from apps.governance import services as governance_services
from apps.matching.models import Discrepancy
from apps.procurement.models import ProcurementPackage, PurchaseOrder
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

    def get_queryset(self):
        org_id = getattr(getattr(self.request.user, "profile", None), "organization_id", None)
        authorization = Q(package__isnull=True, organization_id=org_id)
        for package in ProcurementPackage.objects.filter(
            pk__in=governance_services.authorized_package_ids(self.request.user)
        ):
            authorization |= Q(
                package=package,
                classification__in=governance_services.visible_classifications_for(self.request.user, package=package),
            )
        return self.queryset.filter(authorization).distinct()


class ManifestLineViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ManifestLine.objects.all().select_related("manifest_version__manifest__shipment")
    serializer_class = serializers.ManifestLineSerializer

    def get_queryset(self):
        org_id = getattr(getattr(self.request.user, "profile", None), "organization_id", None)
        package_source = "sources__purchase_order_line__purchase_order__package"
        authorization = (
            Q(manifest_version__manifest__shipment__organization_id=org_id)
            & ~Q(**{f"{package_source}__isnull": False})
        )
        for package in ProcurementPackage.objects.filter(
            pk__in=governance_services.authorized_package_ids(self.request.user)
        ):
            authorization |= Q(
                **{
                    package_source: package,
                    "sources__purchase_order_line__purchase_order__classification__in":
                        governance_services.visible_classifications_for(self.request.user, package=package),
                }
            )
        return self.queryset.filter(authorization).distinct()


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
