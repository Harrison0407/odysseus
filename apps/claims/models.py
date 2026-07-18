from django.conf import settings
from django.db import models

from apps.core.models import BaseModel

# ---------------------------------------------------------------------------
# Supplier claims (spec section 17/26) — a claim traces to every real record
# that justifies it, and never duplicates the Official/Operational manifest
# split: it links to the existing, already-immutable `ManifestVariance`
# rather than copying quantities that could then drift apart from it.
# ---------------------------------------------------------------------------


class SupplierClaim(BaseModel):
    class ClaimType(models.TextChoices):
        SHORTAGE = "shortage", "Faltante"
        OVERAGE = "overage", "Sobrante no solicitado"
        DAMAGE = "damage", "Daño"
        WRONG_ITEM = "wrong_item", "Artículo incorrecto"
        QUALITY_DEFECT = "quality_defect", "Defecto de calidad"
        MISSING_COMPONENT = "missing_component", "Componente faltante"
        OTHER = "other", "Otro"

    class Status(models.TextChoices):
        DRAFT = "draft", "Borrador"
        APPROVED = "approved", "Aprobado para envío"
        SUBMITTED = "submitted", "Enviado al proveedor"
        SUPPLIER_RESPONDED = "supplier_responded", "Proveedor respondió"
        RESOLVED = "resolved", "Resuelto"
        CLOSED = "closed", "Cerrado"

    class SupplierResponse(models.TextChoices):
        PENDING = "pending", "Pendiente"
        ACCEPTED = "accepted", "Aceptado"
        PARTIALLY_ACCEPTED = "partially_accepted", "Parcialmente aceptado"
        REJECTED = "rejected", "Rechazado"

    class ResolutionType(models.TextChoices):
        NONE = "none", "Ninguna"
        REPLACEMENT = "replacement", "Reemplazo"
        CREDIT_NOTE = "credit_note", "Nota de crédito"
        SETTLEMENT = "settlement", "Pago/Liquidación"

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="supplier_claims")
    claim_number = models.CharField(max_length=30, unique=True, editable=False)
    claim_type = models.CharField(max_length=30, choices=ClaimType.choices)
    reason = models.TextField()

    # Traceability chain — every FK optional except organization/shipment,
    # since not every claim originates from every kind of record, but each
    # one present must point at the genuine record, never a re-entered copy.
    supplier = models.ForeignKey("procurement.Supplier", on_delete=models.PROTECT, related_name="claims")
    purchase_order = models.ForeignKey(
        "procurement.PurchaseOrder", on_delete=models.SET_NULL, null=True, blank=True, related_name="claims"
    )
    purchase_order_line = models.ForeignKey(
        "procurement.PurchaseOrderLine", on_delete=models.SET_NULL, null=True, blank=True, related_name="claims"
    )
    item = models.ForeignKey("items.Item", on_delete=models.SET_NULL, null=True, blank=True, related_name="claims")
    shipment = models.ForeignKey("shipments.Shipment", on_delete=models.PROTECT, related_name="claims")
    container = models.ForeignKey(
        "shipments.Container", on_delete=models.SET_NULL, null=True, blank=True, related_name="claims"
    )
    manifest_variance = models.ForeignKey(
        "shipments.ManifestVariance", on_delete=models.SET_NULL, null=True, blank=True, related_name="claims",
        help_text="Links to the existing Official-vs-Operational variance record — never overwritten or duplicated here.",
    )
    receipt = models.ForeignKey(
        "receiving.Receipt", on_delete=models.SET_NULL, null=True, blank=True, related_name="claims"
    )
    receipt_line = models.ForeignKey(
        "receiving.ReceiptLine", on_delete=models.SET_NULL, null=True, blank=True, related_name="claims"
    )
    discrepancy = models.ForeignKey(
        "matching.Discrepancy", on_delete=models.SET_NULL, null=True, blank=True, related_name="claims"
    )
    quarantine_record = models.ForeignKey(
        "inventory.QuarantineRecord", on_delete=models.SET_NULL, null=True, blank=True, related_name="claims"
    )
    inspection = models.ForeignKey(
        "receiving.Inspection", on_delete=models.SET_NULL, null=True, blank=True, related_name="claims"
    )
    replacement_case = models.ForeignKey(
        "shipments.ReplacementCase", on_delete=models.SET_NULL, null=True, blank=True, related_name="claims"
    )

    quantity_claimed = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quantity_damaged = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quantity_missing = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    amount_claimed = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.ForeignKey("cost.Currency", on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    responsible_internal_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    responsible_supplier_contact = models.ForeignKey(
        "procurement.SupplierContact", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    status = models.CharField(max_length=30, choices=Status.choices, default=Status.DRAFT)

    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)

    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)

    supplier_response = models.CharField(max_length=30, choices=SupplierResponse.choices, default=SupplierResponse.PENDING)
    supplier_response_date = models.DateField(null=True, blank=True)
    supplier_response_notes = models.TextField(blank=True)

    resolution_type = models.CharField(max_length=30, choices=ResolutionType.choices, default=ResolutionType.NONE)
    resolution_reference = models.CharField(max_length=150, blank=True)
    resolution_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)

    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    closed_at = models.DateTimeField(null=True, blank=True)
    closure_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.claim_number} — {self.supplier}"
