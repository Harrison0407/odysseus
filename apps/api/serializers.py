from rest_framework import serializers

from apps.matching.models import Discrepancy
from apps.procurement.models import PurchaseOrder, PurchaseOrderLine
from apps.shipments.models import BillOfLading, ManifestLine, ManifestVariance, Shipment


class PurchaseOrderLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseOrderLine
        fields = ["id", "line_no", "description", "quantity_ordered", "unit_price", "line_total"]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    lines = PurchaseOrderLineSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "id", "po_number", "supplier", "project", "building", "approval_status",
            "order_date", "currency", "total_amount", "usage_note_on_document",
            "document_marked_received_in_full", "lines",
        ]


class BillOfLadingSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillOfLading
        fields = [
            "id", "bl_number", "shipper_name", "consignee_name", "port_of_loading", "port_of_discharge",
            "official_cargo_description", "declared_packages", "declared_gross_weight_kg", "declared_cbm",
        ]


class ManifestLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManifestLine
        fields = [
            "id", "line_no", "description", "reference_on_document", "destination_scope",
            "quantity", "packages", "gross_weight_kg", "cbm", "declaration_status",
        ]


class ManifestVarianceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManifestVariance
        fields = [
            "id", "official_category_text", "is_explained", "severity",
            "customs_review_required",
        ]


class ShipmentSerializer(serializers.ModelSerializer):
    bills_of_lading = BillOfLadingSerializer(many=True, read_only=True)

    class Meta:
        model = Shipment
        fields = ["id", "reference", "status", "etd", "eta", "bills_of_lading"]


class DiscrepancySerializer(serializers.ModelSerializer):
    class Meta:
        model = Discrepancy
        fields = ["id", "discrepancy_type", "severity", "description", "is_resolved", "blocks_gate"]
