from django.conf import settings
from django.db import models

from apps.core.models import BaseModel, ConfidenceLevel, Severity


class MatchRun(BaseModel):
    """One execution of the deterministic matching engine over a shipment
    or purchase order set (spec section 13)."""

    shipment = models.ForeignKey(
        "shipments.Shipment", on_delete=models.CASCADE, null=True, blank=True, related_name="match_runs"
    )
    triggered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    ran_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"MatchRun {self.id} ({self.ran_at:%Y-%m-%d %H:%M})"


class MatchStatus(models.TextChoices):
    EXACT = "exact", "Exacto"
    STRONG_CANDIDATE = "strong_candidate", "Candidato fuerte"
    POSSIBLE_CANDIDATE = "possible_candidate", "Candidato posible"
    MANUALLY_CONFIRMED = "manually_confirmed", "Confirmado manualmente"
    UNMATCHED = "unmatched", "Sin coincidencia"
    CONFLICT = "conflict", "Conflicto"
    SUPERSEDED = "superseded", "Reemplazado"


class MatchCandidate(BaseModel):
    """A proposed relationship between two source lines (e.g. a local PO
    line and a China-side PI line, or two references to the same item such
    as the fixture's "W5057" vs "W5097"). Every suggestion must explain
    why it was suggested — spec section 13."""

    match_run = models.ForeignKey(MatchRun, on_delete=models.CASCADE, related_name="candidates")

    left_content_type = models.ForeignKey(
        "contenttypes.ContentType", on_delete=models.CASCADE, related_name="+"
    )
    left_object_id = models.UUIDField()
    right_content_type = models.ForeignKey(
        "contenttypes.ContentType", on_delete=models.CASCADE, related_name="+"
    )
    right_object_id = models.UUIDField()

    status = models.CharField(max_length=30, choices=MatchStatus.choices, default=MatchStatus.POSSIBLE_CANDIDATE)
    confidence = models.CharField(max_length=20, choices=ConfidenceLevel.choices, default=ConfidenceLevel.MEDIUM)

    agreeing_fields = models.JSONField(default=list, blank=True)
    conflicting_fields = models.JSONField(default=list, blank=True)
    quantity_relationship = models.CharField(max_length=255, blank=True)
    explanation = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"MatchCandidate {self.id} ({self.status})"


class ConfirmedMatch(BaseModel):
    candidate = models.OneToOneField(MatchCandidate, on_delete=models.CASCADE, related_name="confirmation")
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    confirmed_at = models.DateTimeField(auto_now_add=True)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    reversal_reason = models.TextField(blank=True)

    def __str__(self):
        return f"Confirmed: {self.candidate}"


class QuantityReconciliation(BaseModel):
    """Rolls up the quantity layers for one item/line across the whole
    chain (required/quoted/ordered/paid/produced/packed/shipped/expected/
    received/accepted/... — core principle 4.2). Never copies one layer
    into another as proof of another."""

    item = models.ForeignKey("items.Item", on_delete=models.CASCADE, related_name="reconciliations")
    project = models.ForeignKey("projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    quantity_required = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quantity_quoted = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quantity_ordered = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quantity_paid_for = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quantity_produced = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quantity_packed = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quantity_shipped = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quantity_expected_at_receipt = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quantity_received = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quantity_accepted = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quantity_damaged = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quantity_missing = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quantity_available = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quantity_installed = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)

    computed_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Reconciliación: {self.item}"


class DiscrepancyType(models.TextChoices):
    REQUIRED_NOT_ORDERED = "required_not_ordered", "Requerido pero no ordenado"
    ORDERED_NOT_INVOICED = "ordered_not_invoiced", "Ordenado pero no facturado"
    INVOICED_NOT_PACKED = "invoiced_not_packed", "Facturado pero no empacado"
    PACKED_NOT_INVOICED = "packed_not_invoiced", "Empacado pero no facturado"
    PACKED_QUANTITY_MISMATCH = "packed_quantity_mismatch", "Empacado por debajo/encima de lo ordenado"
    DUPLICATE_LINE_OR_PACKAGE = "duplicate_line_or_package", "Línea o paquete duplicado"
    UNIT_CONFLICT = "unit_conflict", "Conflicto de unidad"
    DIMENSION_CONFLICT = "dimension_conflict", "Conflicto de dimensión"
    COLOR_CONFLICT = "color_conflict", "Conflicto de color"
    ORIENTATION_CONFLICT = "orientation_conflict", "Conflicto izquierda/derecha"
    PRICE_CURRENCY_CONFLICT = "price_currency_conflict", "Conflicto de precio/moneda"
    PROJECT_BUILDING_CONFLICT = "project_building_conflict", "Conflicto de proyecto/edificio"
    MISSING_PACKAGE_ID = "missing_package_id", "Falta identificador de paquete"
    MISSING_CONTAINER_ASSIGNMENT = "missing_container_assignment", "Falta asignación de contenedor"
    BL_CONTAINER_MISMATCH = "bl_container_mismatch", "Discrepancia BL/contenedor"
    OFFICIAL_VS_OPERATIONAL_MISMATCH = "official_vs_operational_mismatch", "Discrepancia manifiesto oficial vs. operativo"
    INTERNAL_CARGO_NOT_REPRESENTED = "internal_cargo_not_represented", "Carga interna no representada oficialmente"
    OPEN_COMMITMENT_CLOSED_INCORRECTLY = "open_commitment_closed_incorrectly", "Compromiso abierto cerrado incorrectamente"
    PRIOR_FULFILLMENT_UNVERIFIED = "prior_fulfillment_unverified", "Cumplimiento previo sin evidencia verificada"
    REPLACEMENT_COUNTED_AS_NEW = "replacement_counted_as_new", "Reemplazo contado como compra nueva"
    ADDED_AT_LOADING_WITHOUT_REVIEW = "added_at_loading_without_review", "Agregado en carga sin revisión aduanal"
    WEIGHT_CBM_MISMATCH = "weight_cbm_mismatch", "Discrepancia de peso o CBM"
    MISSING_DOCUMENT = "missing_document", "Documento faltante"
    REVISION_CONFLICT = "revision_conflict", "Conflicto de revisión"
    RECEIPT_SHORTAGE = "receipt_shortage", "Faltante en recepción"
    RECEIPT_OVERAGE = "receipt_overage", "Sobrante en recepción"
    DAMAGE = "damage", "Daño"
    WRONG_DESTINATION = "wrong_destination", "Destino incorrecto"
    INVENTORY_ISSUED_WRONG_BUILDING = "inventory_issued_wrong_building", "Inventario emitido a edificio incorrecto"
    INCOMPLETE_KIT = "incomplete_kit", "Ensamble/kit incompleto"
    CONFOTUR_DUPLICATE_OR_OMISSION = "confotur_duplicate_or_omission", "Duplicado u omisión CONFOTUR"
    IDENTITY_CONFLICT = "identity_conflict", "Conflicto de identidad de producto (p.ej. plancha vs. tope)"


class Discrepancy(BaseModel):
    match_candidate = models.ForeignKey(
        MatchCandidate, on_delete=models.SET_NULL, null=True, blank=True, related_name="discrepancies"
    )
    manifest_variance = models.ForeignKey(
        "shipments.ManifestVariance", on_delete=models.SET_NULL, null=True, blank=True, related_name="discrepancies"
    )
    shipment = models.ForeignKey(
        "shipments.Shipment", on_delete=models.CASCADE, null=True, blank=True, related_name="discrepancies"
    )
    discrepancy_type = models.CharField(max_length=50, choices=DiscrepancyType.choices)
    severity = models.CharField(max_length=20, choices=Severity.choices, default=Severity.WARNING)
    description = models.TextField(blank=True)
    is_resolved = models.BooleanField(default=False)
    blocks_gate = models.CharField(
        max_length=100, blank=True, help_text="Gate name this discrepancy blocks while unresolved, if any."
    )

    class Meta:
        verbose_name_plural = "discrepancies"
        ordering = ["-severity", "-created_at"]

    def __str__(self):
        return f"{self.get_discrepancy_type_display()} ({self.severity})"


class DiscrepancyResolution(BaseModel):
    discrepancy = models.OneToOneField(Discrepancy, on_delete=models.CASCADE, related_name="resolution")
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    resolved_at = models.DateTimeField(auto_now_add=True)
    resolution_note = models.TextField()


class Waiver(BaseModel):
    """A critical discrepancy blocks its gate unless formally waived by an
    authorized user (spec section 13, 9.4) — the waiver itself remains
    permanently visible even after the gate proceeds (core principle 4.4)."""

    discrepancy = models.ForeignKey(Discrepancy, on_delete=models.CASCADE, related_name="waivers")
    waived_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    waived_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField()

    def __str__(self):
        return f"Waiver: {self.discrepancy}"
