from django.db import models

from apps.core.models import BaseModel


class CustomsDeclaration(BaseModel):
    shipment = models.ForeignKey("shipments.Shipment", on_delete=models.CASCADE, related_name="customs_declarations")
    declaration_number = models.CharField(max_length=100, blank=True)
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")

    def __str__(self):
        return self.declaration_number or str(self.id)


class CustomsLiquidation(BaseModel):
    declaration = models.ForeignKey(CustomsDeclaration, on_delete=models.CASCADE, related_name="liquidations")
    liquidation_number = models.CharField(max_length=100, blank=True)
    liquidated_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")


class ConfoturList(BaseModel):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="confotur_lists")
    list_number = models.CharField(max_length=100)
    submitted_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.list_number


class ConfoturLine(BaseModel):
    confotur_list = models.ForeignKey(ConfoturList, on_delete=models.CASCADE, related_name="lines")
    manifest_line = models.ForeignKey(
        "shipments.ManifestLine", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    quotation = models.ForeignKey(
        "procurement.Quotation", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    requested_exemption = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    applied_exemption = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    imported_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    is_duplicate_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="duplicates",
        help_text="Set when the matching engine detects the same exemption being used twice (spec section 25).",
    )


class ConfoturApproval(BaseModel):
    confotur_list = models.ForeignKey(ConfoturList, on_delete=models.CASCADE, related_name="approvals")
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")
    approved_date = models.DateField(null=True, blank=True)


class ExemptionAllocation(BaseModel):
    confotur_line = models.ForeignKey(ConfoturLine, on_delete=models.CASCADE, related_name="exemption_allocations")
    liquidation = models.ForeignKey(CustomsLiquidation, on_delete=models.CASCADE, related_name="exemption_allocations")
    amount = models.DecimalField(max_digits=14, decimal_places=2)


class ImportEntry(BaseModel):
    shipment = models.ForeignKey("shipments.Shipment", on_delete=models.CASCADE, related_name="import_entries")
    confotur_line = models.ForeignKey(
        ConfoturLine, on_delete=models.SET_NULL, null=True, blank=True, related_name="import_entries"
    )
    liquidation = models.ForeignKey(
        CustomsLiquidation, on_delete=models.SET_NULL, null=True, blank=True, related_name="import_entries"
    )


class CustomsDocumentLink(BaseModel):
    """Generic linkage so a CONFOTUR/import line can point at any of:
    quotation, PO, invoice, packing list, BL, container, declaration,
    liquidation (spec section 25)."""

    confotur_line = models.ForeignKey(ConfoturLine, on_delete=models.CASCADE, related_name="document_links")
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")
    role = models.CharField(max_length=100, blank=True, help_text="e.g. quotation, PO, invoice, packing_list, BL")
