from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class Currency(BaseModel):
    code = models.CharField(max_length=3, unique=True, help_text="ISO 4217, e.g. USD, CNY, DOP.")
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.code


class ExchangeRate(BaseModel):
    from_currency = models.ForeignKey(Currency, on_delete=models.CASCADE, related_name="rates_from")
    to_currency = models.ForeignKey(Currency, on_delete=models.CASCADE, related_name="rates_to")
    rate = models.DecimalField(max_digits=18, decimal_places=8)
    rate_date = models.DateField()
    source = models.CharField(max_length=150, blank=True, help_text="e.g. Yekalon PI stated rate, bank rate, etc.")

    class Meta:
        ordering = ["-rate_date"]

    def __str__(self):
        return f"1 {self.from_currency} = {self.rate} {self.to_currency} ({self.rate_date})"


class CostDocument(BaseModel):
    """A freight invoice, insurance certificate, brokerage invoice, etc. —
    e.g. the fixture's Guangzhou Eternalship ocean-freight invoice
    (USD 6,900.00) and its Banco Popular payment evidence."""

    class CostType(models.TextChoices):
        FREIGHT = "freight", "Flete internacional"
        INSURANCE = "insurance", "Seguro"
        CUSTOMS_DUTIES = "customs_duties", "Aduanas y aranceles"
        PORT_TERMINAL = "port_terminal", "Cargos portuarios/terminal"
        BROKERAGE = "brokerage", "Agenciamiento aduanal"
        LOCAL_TRANSPORT = "local_transport", "Transporte local"
        HANDLING = "handling", "Manejo"
        STORAGE = "storage", "Almacenaje"
        DEMURRAGE = "demurrage", "Demora"
        INSPECTION = "inspection", "Inspección"
        OTHER = "other", "Otro"

    shipment = models.ForeignKey("shipments.Shipment", on_delete=models.CASCADE, related_name="cost_documents")
    cost_type = models.CharField(max_length=30, choices=CostType.choices)
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    invoice_reference = models.CharField(max_length=150, blank=True)
    is_duplicate_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="duplicates",
        help_text="Set when the same cost invoice is detected submitted twice (spec section 26, acceptance case 27).",
    )

    def __str__(self):
        return f"{self.get_cost_type_display()}: {self.amount} {self.currency} ({self.shipment})"


class CostCharge(BaseModel):
    """A single chargeable amount extracted from a CostDocument, ready for
    allocation."""

    cost_document = models.ForeignKey(CostDocument, on_delete=models.CASCADE, related_name="charges")
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    def __str__(self):
        return f"{self.description}: {self.amount}"


class CostAllocationRun(BaseModel):
    class AllocationMethod(models.TextChoices):
        QUANTITY = "quantity", "Cantidad"
        PRODUCT_VALUE = "product_value", "Valor de producto"
        GROSS_WEIGHT = "gross_weight", "Peso bruto"
        NET_WEIGHT = "net_weight", "Peso neto"
        CBM = "cbm", "CBM"
        PACKAGE = "package", "Paquete"
        CONTAINER = "container", "Contenedor"
        MANUAL_PERCENTAGE = "manual_percentage", "Porcentaje manual"
        MANUAL_AMOUNT = "manual_amount", "Monto manual"

    shipment = models.ForeignKey("shipments.Shipment", on_delete=models.CASCADE, related_name="cost_allocation_runs")
    cost_charge = models.ForeignKey(CostCharge, on_delete=models.CASCADE, related_name="allocation_runs")
    method = models.CharField(max_length=30, choices=AllocationMethod.choices)
    run_at = models.DateTimeField(auto_now_add=True)
    run_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    total_allocated = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    def __str__(self):
        return f"Allocation {self.method} — {self.cost_charge}"


class CostAllocationLine(BaseModel):
    allocation_run = models.ForeignKey(CostAllocationRun, on_delete=models.CASCADE, related_name="lines")
    manifest_line = models.ForeignKey("shipments.ManifestLine", on_delete=models.CASCADE, related_name="cost_allocations")
    allocated_amount = models.DecimalField(max_digits=14, decimal_places=2)
    basis_value = models.DecimalField(
        max_digits=14, decimal_places=4, null=True, blank=True,
        help_text="The quantity/weight/CBM/value used as the allocation basis for this line.",
    )


class LandedCostVersion(BaseModel):
    """A frozen, versioned landed-cost calculation for a shipment. Clearly
    distinguishes provisional vs final, and Operationally Verified from
    Financially Finalized (spec section 9.6, 26)."""

    shipment = models.ForeignKey("shipments.Shipment", on_delete=models.CASCADE, related_name="landed_cost_versions")
    version_number = models.PositiveIntegerField()
    is_final = models.BooleanField(default=False)
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    finalized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("shipment", "version_number")]
        ordering = ["-version_number"]

    def __str__(self):
        return f"Landed cost v{self.version_number} — {self.shipment}"


class LandedCostLine(BaseModel):
    version = models.ForeignKey(LandedCostVersion, on_delete=models.CASCADE, related_name="lines")
    manifest_line = models.ForeignKey("shipments.ManifestLine", on_delete=models.CASCADE, related_name="landed_cost_lines")

    original_unit_price = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    base_currency_unit_price = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    freight_per_unit = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    local_cost_per_unit = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    other_cost_per_unit = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    final_landed_cost_per_unit = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    total_landed_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    def __str__(self):
        return f"{self.version}: {self.manifest_line}"
