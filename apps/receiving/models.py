from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


# ---------------------------------------------------------------------------
# Storage and receiving readiness (spec section 9.4, 17)
# ---------------------------------------------------------------------------


class ReceivingPlan(BaseModel):
    """Before authorizing shipment (or before arrival if unavoidable), a
    receiving plan is required. A missing/unsuitable plan blocks
    "Authorized to Ship" unless an executive override records a reason."""

    shipment = models.OneToOneField("shipments.Shipment", on_delete=models.CASCADE, related_name="receiving_plan")
    project = models.ForeignKey("projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    proposed_unloading_location = models.ForeignKey(
        "inventory.WarehouseLocation", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    expected_packages = models.PositiveIntegerField(null=True, blank=True)
    expected_weight_kg = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    expected_cbm = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    has_sensitive_materials = models.BooleanField(default=False)
    responsible_receiving_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    expected_installation_date = models.DateField(null=True, blank=True)
    estimated_storage_duration_days = models.PositiveIntegerField(null=True, blank=True)
    alternative_plan = models.TextField(blank=True)

    class Status(models.TextChoices):
        MISSING = "missing", "Faltante"
        DRAFT = "draft", "Borrador"
        SUITABLE = "suitable", "Adecuado"
        UNSUITABLE = "unsuitable", "No adecuado"
        OVERRIDDEN = "overridden", "Anulado por autorización ejecutiva"

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.MISSING)
    override_reason = models.TextField(blank=True)
    overridden_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    def __str__(self):
        return f"Plan de recepción: {self.shipment}"


class ReceivingPlanLine(BaseModel):
    receiving_plan = models.ForeignKey(ReceivingPlan, on_delete=models.CASCADE, related_name="lines")
    manifest_line = models.ForeignKey("shipments.ManifestLine", on_delete=models.CASCADE, related_name="+")
    proposed_final_location = models.ForeignKey(
        "inventory.WarehouseLocation", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    is_direct_to_project = models.BooleanField(default=False)


class StorageCapacityReservation(BaseModel):
    receiving_plan = models.ForeignKey(ReceivingPlan, on_delete=models.CASCADE, related_name="capacity_reservations")
    location = models.ForeignKey("inventory.WarehouseLocation", on_delete=models.CASCADE, related_name="reservations")
    reserved_volume_cbm = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    reserved_area_sqm = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)


class UnloadingRoute(BaseModel):
    receiving_plan = models.ForeignKey(ReceivingPlan, on_delete=models.CASCADE, related_name="unloading_routes")
    description = models.TextField()


class EquipmentRequirement(BaseModel):
    receiving_plan = models.ForeignKey(ReceivingPlan, on_delete=models.CASCADE, related_name="equipment_requirements")
    equipment_type = models.CharField(max_length=100, help_text="Telehandler, forklift, truck, etc.")
    quantity = models.PositiveIntegerField(default=1)


class PersonnelAssignment(BaseModel):
    receiving_plan = models.ForeignKey(ReceivingPlan, on_delete=models.CASCADE, related_name="personnel_assignments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    role_note = models.CharField(max_length=150, blank=True)


class SamplingRule(BaseModel):
    """Risk-based sampling (spec section 18): all packages are always
    counted; sampling only affects inspection depth."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="sampling_rules")
    risk_level = models.CharField(max_length=20, choices=[("low", "Bajo"), ("medium", "Medio"), ("high", "Alto")])
    sample_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    expand_on_failure = models.BooleanField(default=True)
    full_inspection_on_critical_discrepancy = models.BooleanField(default=True)


class InspectionPlan(BaseModel):
    receiving_plan = models.ForeignKey(ReceivingPlan, on_delete=models.CASCADE, related_name="inspection_plans")
    manifest_line = models.ForeignKey("shipments.ManifestLine", on_delete=models.CASCADE, related_name="+")
    sampling_rule = models.ForeignKey(SamplingRule, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    requires_full_inspection = models.BooleanField(default=False)


class StorageComparisonScenario(BaseModel):
    """A versioned, named external-storage comparison exercise for one
    receiving plan (spec section 17). Immutable once `FINALIZED`: a new
    comparison creates a new version rather than editing a decided one
    — mirrors `ReleasePacketVersion`/`LandedCostVersion`'s
    version_number + is_current pattern."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Borrador"
        FINALIZED = "finalized", "Finalizado"

    receiving_plan = models.ForeignKey(ReceivingPlan, on_delete=models.CASCADE, related_name="storage_comparison_scenarios")
    name = models.CharField(max_length=150, blank=True)
    version_number = models.PositiveIntegerField()
    is_current = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    chosen_option = models.ForeignKey(
        "AlternativeStorageOption", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    recommendation_rationale = models.TextField(blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("receiving_plan", "version_number")]
        ordering = ["-version_number"]

    def __str__(self):
        return f"Comparación de almacenaje v{self.version_number} — {self.receiving_plan}"


class AlternativeStorageOption(BaseModel):
    """One internal-baseline or external option inside a
    `StorageComparisonScenario` (spec section 17)."""

    class OptionType(models.TextChoices):
        INTERNAL_BASELINE = "internal_baseline", "Almacén propio (referencia)"
        EXTERNAL_WAREHOUSE = "external_warehouse", "Almacén externo"
        PORT_STORAGE = "port_storage", "Almacenaje en puerto"
        OTHER = "other", "Otro"

    scenario = models.ForeignKey(
        StorageComparisonScenario, on_delete=models.CASCADE, null=True, blank=True, related_name="options"
    )
    option_type = models.CharField(max_length=30, choices=OptionType.choices, default=OptionType.EXTERNAL_WAREHOUSE)
    option_name = models.CharField(max_length=150, help_text="Holding in China, port storage, external warehouse, immediate delivery")
    internal_location = models.ForeignKey(
        "inventory.WarehouseLocation", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Solo para option_type=internal_baseline — reutiliza la capacidad/idoneidad ya registrada de esta ubicación en vez de duplicarla.",
    )
    currency = models.ForeignKey("cost.Currency", on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    storage_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, help_text="Costo total estimado de almacenaje para la duración evaluada, no una tarifa diaria.")
    inbound_transport_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    outbound_transport_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    handling_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    insurance_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    minimum_commitment_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    free_days = models.PositiveIntegerField(null=True, blank=True)
    expected_duration_days = models.PositiveIntegerField(null=True, blank=True)
    operational_lead_time_days = models.PositiveIntegerField(null=True, blank=True, help_text="Tiempo operativo estimado para mover material hacia/desde esta opción.")
    contract_period_days = models.PositiveIntegerField(null=True, blank=True)
    estimated_movement_count = models.PositiveIntegerField(null=True, blank=True)

    demurrage_penalty_estimated_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    demurrage_penalty_notes = models.TextField(blank=True)

    capacity_volume_cbm = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    capacity_weight_kg = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    location_description = models.CharField(max_length=255, blank=True)
    covered = models.BooleanField(default=False)
    dry = models.BooleanField(default=False)
    climate_controlled = models.BooleanField(default=False)
    secure = models.BooleanField(default=False)
    access_controlled = models.BooleanField(default=False)
    access_restrictions_notes = models.TextField(blank=True)

    risk_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["option_type", "option_name"]

    def __str__(self):
        return f"{self.option_name} ({self.scenario})"


# ---------------------------------------------------------------------------
# Physical receiving (spec section 9.5, 9.6, 18)
# ---------------------------------------------------------------------------


class ReleasePacket(BaseModel):
    """Expected receiving quantities frozen in a versioned release packet
    (spec section 9.5) before release to physical Receiving."""

    shipment = models.OneToOneField("shipments.Shipment", on_delete=models.CASCADE, related_name="release_packet")
    verifier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Paquete de liberación: {self.shipment}"


class ReleasePacketVersion(BaseModel):
    release_packet = models.ForeignKey(ReleasePacket, on_delete=models.CASCADE, related_name="versions")
    version_number = models.PositiveIntegerField()
    manifest_version = models.ForeignKey(
        "shipments.ShipmentManifestVersion", on_delete=models.PROTECT, related_name="+"
    )
    is_current = models.BooleanField(default=True)
    change_reason = models.TextField(blank=True)

    class Meta:
        unique_together = [("release_packet", "version_number")]
        ordering = ["-version_number"]


class Receipt(BaseModel):
    """Physical receipt of a container/shipment. This — never a
    commercial document, never QuickBooks — is the only source of
    physical-receiving truth (core principle 4.1, 4.5)."""

    class Status(models.TextChoices):
        PROVISIONAL = "provisional", "Provisional"
        MATCHED = "matched", "Completo, sin excepciones"
        RECEIVED_WITH_EXCEPTIONS = "received_with_exceptions", "Recibido con excepciones"

    release_packet = models.ForeignKey(ReleasePacket, on_delete=models.PROTECT, related_name="receipts")
    container = models.ForeignKey("shipments.Container", on_delete=models.PROTECT, related_name="receipts")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PROVISIONAL)

    seal_confirmed_intact = models.BooleanField(null=True, blank=True)
    unloading_started_at = models.DateTimeField(null=True, blank=True)
    unloading_ended_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    provisional_deadline = models.DateTimeField(
        null=True, blank=True, help_text="Default 7 calendar days after unloading (configurable)."
    )
    escalated = models.BooleanField(default=False)

    closed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Recibo {self.id} — {self.container}"


class ReceiptLine(BaseModel):
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name="lines")
    manifest_line = models.ForeignKey("shipments.ManifestLine", on_delete=models.PROTECT, related_name="receipt_lines")

    quantity_expected = models.DecimalField(max_digits=14, decimal_places=3)
    quantity_received = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_damaged = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_missing = models.DecimalField(max_digits=14, decimal_places=3, default=0)

    class ExceptionType(models.TextChoices):
        NONE = "none", "Sin excepción"
        SHORTAGE = "shortage", "Faltante"
        OVERAGE = "overage", "Sobrante"
        DAMAGE = "damage", "Daño"
        WRONG_MODEL = "wrong_model", "Modelo incorrecto"
        WRONG_SIZE = "wrong_size", "Medida incorrecta"
        WRONG_COLOR = "wrong_color", "Color incorrecto"
        WRONG_ORIENTATION = "wrong_orientation", "Orientación incorrecta"
        MISSING_COMPONENT = "missing_component", "Componente faltante"
        PACKAGING_FAILURE = "packaging_failure", "Falla de empaque"

    exception_type = models.CharField(max_length=30, choices=ExceptionType.choices, default=ExceptionType.NONE)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.receipt}: {self.manifest_line}"


class ReceiptPackage(BaseModel):
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name="packages")
    package = models.ForeignKey("items.Package", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    package_number_scanned = models.CharField(max_length=100, blank=True)
    is_damaged = models.BooleanField(default=False)
    project = models.ForeignKey("projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    is_direct_to_project = models.BooleanField(default=False)


class Inspection(BaseModel):
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name="inspections")
    receipt_line = models.ForeignKey(ReceiptLine, on_delete=models.CASCADE, related_name="inspections")
    inspector = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    sample_size = models.PositiveIntegerField(null=True, blank=True)
    population_size = models.PositiveIntegerField(null=True, blank=True)
    passed = models.BooleanField(null=True, blank=True)
    expanded_due_to_failure = models.BooleanField(default=False)
    findings = models.TextField(blank=True)

    def __str__(self):
        return f"Inspección: {self.receipt_line}"


class InspectionSample(BaseModel):
    inspection = models.ForeignKey(Inspection, on_delete=models.CASCADE, related_name="samples")
    package = models.ForeignKey("items.Package", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    result = models.CharField(max_length=20, choices=[("pass", "Aprobado"), ("fail", "Rechazado")])
    notes = models.TextField(blank=True)


class ReceiptEvidence(BaseModel):
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name="evidence")
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")
    caption = models.CharField(max_length=255, blank=True)


class DamageRecord(BaseModel):
    receipt_line = models.ForeignKey(ReceiptLine, on_delete=models.CASCADE, related_name="damage_records")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    description = models.TextField(blank=True)
    photo_document = models.ForeignKey(
        "documents.Document", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )


class UnidentifiedMaterial(BaseModel):
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name="unidentified_materials")
    description = models.TextField()
    possible_open_commitment = models.ForeignKey(
        "shipments.OpenCommitment", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    possible_replacement_case = models.ForeignKey(
        "shipments.ReplacementCase", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    resolved = models.BooleanField(default=False)


class ContainerClosureAct(BaseModel):
    receipt = models.OneToOneField(Receipt, on_delete=models.CASCADE, related_name="closure_act")
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    closed_at = models.DateTimeField(auto_now_add=True)
    summary = models.TextField(blank=True)
    has_open_exceptions = models.BooleanField(default=False)

    def __str__(self):
        return f"Acta de cierre: {self.receipt}"
