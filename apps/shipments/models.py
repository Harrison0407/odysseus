"""Shipment, container, and the dual-manifest engine.

This module is the single most important part of the whole system per the
governing spec (section 13A, marked Priority 0 and non-negotiable): Official
Logistics (the carrier/customs documentary record) and Operational Logistics
(everything the business actually knows is physically loaded or expected)
are modeled as two separate, explicitly linked views. Neither is ever
derived by mutating the other.
"""

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel, DestinationScope, Severity


# ---------------------------------------------------------------------------
# Supplier commercial/shipping documents (source data, not the manifest itself)
# ---------------------------------------------------------------------------


class SupplierInvoice(BaseModel):
    """Commercial Invoice / CI, e.g. CI 60044678-1, CI referencing BL
    MEDUWY575021 in the fixture."""

    supplier = models.ForeignKey("procurement.Supplier", on_delete=models.PROTECT, related_name="invoices")
    purchase_order = models.ForeignKey(
        "procurement.PurchaseOrder", on_delete=models.SET_NULL, null=True, blank=True, related_name="invoices"
    )
    invoice_number = models.CharField(max_length=100)
    invoice_date = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    total_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    source_document = models.ForeignKey(
        "documents.Document", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    def __str__(self):
        return self.invoice_number


class SupplierInvoiceLine(BaseModel):
    invoice = models.ForeignKey(SupplierInvoice, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField(default=1)
    description = models.TextField()
    item = models.ForeignKey("items.Item", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_of_measure = models.ForeignKey("items.UnitOfMeasure", on_delete=models.PROTECT, related_name="+")
    unit_price = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    line_total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["line_no"]


class PackingList(BaseModel):
    supplier = models.ForeignKey("procurement.Supplier", on_delete=models.PROTECT, related_name="packing_lists")
    invoice = models.ForeignKey(
        SupplierInvoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="packing_lists"
    )
    reference = models.CharField(max_length=100)
    total_packages = models.PositiveIntegerField(null=True, blank=True)
    total_net_weight_kg = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    total_gross_weight_kg = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    total_cbm = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    source_document = models.ForeignKey(
        "documents.Document", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    def __str__(self):
        return self.reference


class PackingListLine(BaseModel):
    packing_list = models.ForeignKey(PackingList, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField(default=1)
    container_number = models.CharField(max_length=50, blank=True)
    seal_number = models.CharField(max_length=50, blank=True)
    model_or_ref = models.CharField(max_length=150, blank=True, help_text='E.g. "W5084", "SOLE-26 APT-A".')
    description = models.TextField(blank=True)
    item = models.ForeignKey("items.Item", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_of_measure = models.ForeignKey("items.UnitOfMeasure", on_delete=models.PROTECT, related_name="+")
    packages = models.PositiveIntegerField(null=True, blank=True)
    net_weight_kg = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    gross_weight_kg = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    cbm = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)

    class Meta:
        ordering = ["line_no"]

    def __str__(self):
        return f"{self.packing_list}: {self.model_or_ref or self.description[:30]}"


class TechnicalPackingList(BaseModel):
    """The detailed, box-level technical packing list a factory should
    provide for complex products (spec section 15) — e.g. the
    "kitchen cabinet Packing List" / "Vanity Packing List" sheets found in
    the live fixture, box by box, with dimensions and weights."""

    packing_list = models.ForeignKey(PackingList, on_delete=models.CASCADE, related_name="technical_detail")
    source_document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")
    complies_with_standard = models.BooleanField(default=False)
    missing_fields_note = models.TextField(blank=True)


# ---------------------------------------------------------------------------
# Shipment / container / official BL
# ---------------------------------------------------------------------------


class Shipment(BaseModel):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="shipments")
    reference = models.CharField(max_length=100, help_text="Internal shipment reference.")
    booking_reference = models.CharField(max_length=100, blank=True)
    carrier = models.CharField(max_length=150, blank=True)
    origin_port = models.CharField(max_length=150, blank=True)
    destination_port = models.CharField(max_length=150, blank=True)
    etd = models.DateField(null=True, blank=True)
    eta = models.DateField(null=True, blank=True)

    class Status(models.TextChoices):
        REQUIREMENT = "requirement", "Requerimiento"
        PURCHASING_PREPARATION = "purchasing_preparation", "Preparación de compra"
        FINANCIAL_REVIEW = "financial_review", "Revisión financiera"
        CHINA_ORIGIN_PREPARATION = "china_origin_preparation", "Preparación en origen (China)"
        PRE_SHIPMENT_MATCHING = "pre_shipment_matching", "Conciliación previa al embarque"
        STORAGE_RECEIVING_READINESS = "storage_receiving_readiness", "Listo para recepción/almacenaje"
        AUTHORIZED_TO_SHIP = "authorized_to_ship", "Autorizado para embarcar"
        SHIPMENT_DOCUMENTATION = "shipment_documentation", "Documentación de embarque"
        OPERATIONALLY_VERIFIED = "operationally_verified", "Verificado operativamente"
        RELEASED_TO_RECEIVING = "released_to_receiving", "Liberado a recepción"
        RECEIVING_IN_PROGRESS = "receiving_in_progress", "Recepción en progreso"
        RECEIVED = "received", "Recibido"
        RECEIVED_WITH_EXCEPTIONS = "received_with_exceptions", "Recibido con excepciones"
        WAREHOUSE_CUSTODY = "warehouse_custody", "En custodia de almacén"
        RECONCILED_CLOSED = "reconciled_closed", "Conciliado y cerrado"

    status = models.CharField(max_length=40, choices=Status.choices, default=Status.REQUIREMENT)
    logistics_service_marketed_as_direct = models.BooleanField(
        default=False, help_text='Spec section 16: never use "direct" without recording what it actually means.'
    )
    transshipment = models.BooleanField(null=True, blank=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.reference


class ShipmentLeg(BaseModel):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="legs")
    sequence = models.PositiveIntegerField(default=1)
    from_location = models.CharField(max_length=150)
    to_location = models.CharField(max_length=150)
    mode = models.CharField(max_length=50, blank=True, help_text="ocean, truck, rail, etc.")

    class Meta:
        ordering = ["sequence"]


class Container(BaseModel):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="containers")
    container_number = models.CharField(max_length=20, help_text='E.g. "TCNU8926924".')
    seal_number = models.CharField(max_length=50, blank=True)
    container_type = models.CharField(max_length=30, blank=True, help_text='E.g. "40HQ".')

    class ClosureStatus(models.TextChoices):
        OPEN = "open", "Abierto"
        CLOSED = "closed", "Cerrado (acta de cierre emitida)"

    closure_status = models.CharField(max_length=20, choices=ClosureStatus.choices, default=ClosureStatus.OPEN)

    def __str__(self):
        return self.container_number


class BillOfLading(BaseModel):
    """Official transport document. Preserved exactly as issued — core
    principle 4.9 and spec section 13A.1. Never edited to match the
    internal manifest; only compared against it."""

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="bills_of_lading")
    bl_number = models.CharField(max_length=100, help_text='E.g. "MEDUWY575021".')
    shipper_name = models.CharField(max_length=255, blank=True)
    consignee_name = models.CharField(max_length=255, blank=True)
    vessel_and_voyage = models.CharField(max_length=150, blank=True)
    port_of_loading = models.CharField(max_length=150, blank=True)
    port_of_discharge = models.CharField(max_length=150, blank=True)
    telex_release = models.BooleanField(default=False)
    shipped_on_board_date = models.DateField(null=True, blank=True)

    # Official cargo description exactly as printed — broad categories are
    # expected and must not be "corrected" (e.g. "QUARTZ STONE COUNTERTOP /
    # WOODEN DOORS / KITCHEN CABINET").
    official_cargo_description = models.TextField(blank=True)
    declared_packages = models.PositiveIntegerField(null=True, blank=True)
    declared_gross_weight_kg = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    declared_cbm = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)

    source_document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")

    def __str__(self):
        return self.bl_number


class ShipmentLineAllocation(BaseModel):
    """Allocates a purchase-order/quotation line to a specific shipment,
    supporting one PO across many shipments and one shipment covering many
    POs (spec section 13)."""

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="line_allocations")
    purchase_order_line = models.ForeignKey(
        "procurement.PurchaseOrderLine", on_delete=models.CASCADE, related_name="shipment_allocations"
    )
    quantity_allocated = models.DecimalField(max_digits=14, decimal_places=3)

    def __str__(self):
        return f"{self.shipment} <- {self.purchase_order_line} ({self.quantity_allocated})"


# ---------------------------------------------------------------------------
# Dual manifest engine (spec section 13A)
# ---------------------------------------------------------------------------


class ManifestPurpose(models.TextChoices):
    OFFICIAL_CARRIER_SUMMARY = "official_carrier_summary", "Resumen oficial del transportista"
    OFFICIAL_CUSTOMS_CARGO_SET = "official_customs_cargo_set", "Expediente oficial de aduanas"
    INTERNAL_OPERATIONAL_MANIFEST = "internal_operational_manifest", "Manifiesto operativo interno"
    DETAILED_RECEIVING_MANIFEST = "detailed_receiving_manifest", "Manifiesto detallado de recepción"
    WAREHOUSE_PUTAWAY_MANIFEST = "warehouse_putaway_manifest", "Manifiesto de ubicación en almacén"
    HISTORICAL_RECONSTRUCTION = "historical_reconstruction", "Reconstrucción histórica"


class CargoDeclarationStatus(models.TextChoices):
    CLEARLY_REPRESENTED = "clearly_represented", "Claramente representado en documentos oficiales"
    BROADER_DESCRIPTION = "broader_description", "Representado bajo una descripción oficial más amplia"
    PENDING_CUSTOMS_CONFIRMATION = "pending_customs_confirmation", "Detalle interno pendiente de confirmación aduanal"
    NOT_REPRESENTED_REVIEW_REQUIRED = "not_represented_review_required", "No representado claramente — requiere revisión aduanal"
    REPLACEMENT_OR_WARRANTY = "replacement_or_warranty", "Carga de reemplazo o garantía"
    NO_CHARGE_CARGO = "no_charge_cargo", "Carga sin cargo"
    PROJECT_WIDE_STOCK = "project_wide_stock", "Stock de proyecto (no asignado a edificio)"
    CARRYOVER_FROM_OPEN_COMMITMENT = "carryover_from_open_commitment", "Saldo de un compromiso abierto"
    PRIOR_SHIPMENT_FULFILLMENT_EVIDENCE = "prior_shipment_fulfillment_evidence", "Evidencia de cumplimiento previo"
    CANCELLED_OR_NOT_LOADED = "cancelled_or_not_loaded", "Cancelado o no cargado"
    UNKNOWN_HISTORICAL = "unknown_historical", "Desconocido (histórico)"


class ShipmentManifest(BaseModel):
    """A named manifest for a shipment (there can be several, one per
    ManifestPurpose). The Official Carrier Summary manifest's lines are
    always generated read-only from BillOfLading and never hand-edited."""

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="manifests")
    purpose = models.CharField(max_length=40, choices=ManifestPurpose.choices)

    def __str__(self):
        return f"{self.shipment} — {self.get_purpose_display()}"


class ShipmentManifestVersion(BaseModel):
    """Manifests are versioned and frozen at release (spec section 13A.9,
    9.5): the final internal manifest version is frozen after loading
    confirmation; later corrections create a new version, never an edit
    of the old one."""

    manifest = models.ForeignKey(ShipmentManifest, on_delete=models.CASCADE, related_name="versions")
    version_number = models.PositiveIntegerField()
    is_frozen = models.BooleanField(default=False)
    frozen_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    frozen_at = models.DateTimeField(null=True, blank=True)
    change_reason = models.TextField(blank=True)

    class Meta:
        unique_together = [("manifest", "version_number")]
        ordering = ["manifest", "-version_number"]

    def __str__(self):
        return f"{self.manifest} v{self.version_number}"


class ManifestLine(BaseModel):
    """One line of a manifest version. For the Official Carrier Summary
    manifest, lines are the broad BL categories verbatim. For the Internal
    Operational Manifest, lines are the fully decomposed physical cargo —
    e.g. "W5084 fabricated countertop APT-D/F/C/E qty 5", "W5086 Arena
    Countertop Replacement qty 5", "W5057/W5097 quartz slabs qty 20"."""

    manifest_version = models.ForeignKey(ShipmentManifestVersion, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField(default=1)
    description = models.TextField()
    item = models.ForeignKey("items.Item", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    reference_on_document = models.CharField(
        max_length=150, blank=True, help_text='Verbatim reference exactly as printed, e.g. "W5057".'
    )
    destination_scope = models.CharField(max_length=30, choices=DestinationScope.choices, default=DestinationScope.UNKNOWN_PENDING)
    building = models.ForeignKey("projects.Building", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    unit = models.ForeignKey("projects.Unit", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_of_measure = models.ForeignKey("items.UnitOfMeasure", on_delete=models.PROTECT, related_name="+")
    packages = models.PositiveIntegerField(null=True, blank=True)
    gross_weight_kg = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    cbm = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    commercial_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    value_is_zero_reason = models.CharField(max_length=150, blank=True)

    declaration_status = models.CharField(max_length=40, choices=CargoDeclarationStatus.choices, default=CargoDeclarationStatus.UNKNOWN_HISTORICAL)

    class Meta:
        ordering = ["line_no"]

    def __str__(self):
        return f"{self.manifest_version}: {self.description[:40]}"


class ManifestLineSource(BaseModel):
    """Links a manifest line back to every source document line it was
    built from (a PO line, an invoice line, a packing-list line) —
    preserves full traceability instead of collapsing provenance."""

    manifest_line = models.ForeignKey(ManifestLine, on_delete=models.CASCADE, related_name="sources")
    purchase_order_line = models.ForeignKey(
        "procurement.PurchaseOrderLine", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    invoice_line = models.ForeignKey(SupplierInvoiceLine, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    packing_list_line = models.ForeignKey(PackingListLine, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    document = models.ForeignKey("documents.Document", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    note = models.CharField(max_length=255, blank=True)


class ManifestLineAllocation(BaseModel):
    """Splits a manifest line's quantity across destinations/purposes when
    one physical line serves several (e.g. part project-wide stock, part
    a specific building)."""

    manifest_line = models.ForeignKey(ManifestLine, on_delete=models.CASCADE, related_name="allocations")
    destination_scope = models.CharField(max_length=30, choices=DestinationScope.choices)
    building = models.ForeignKey("projects.Building", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    unit = models.ForeignKey("projects.Unit", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)


class ManifestVariance(BaseModel):
    """The official-vs-operational variance matrix required by spec
    section 13A.3 — one row per comparison between an official BL category
    and an internal detailed line (or an internal line with no official
    counterpart at all)."""

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="manifest_variances")
    official_category_text = models.CharField(max_length=255, blank=True)
    internal_manifest_line = models.ForeignKey(ManifestLine, on_delete=models.CASCADE, related_name="variances")

    quantity_expected_at_receipt = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quantity_received = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    weight_variance_kg = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    cbm_variance = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)

    is_explained = models.BooleanField(default=False)
    explanation = models.TextField(blank=True)
    severity = models.CharField(max_length=20, choices=Severity.choices, default=Severity.INFO)

    customs_review_required = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.shipment}: {self.official_category_text or '(sin categoría oficial)'} vs {self.internal_manifest_line}"


class CustomsReviewDecision(BaseModel):
    """Spec section 13A.1: any internal cargo not clearly represented in
    the official documentary set must be visibly flagged and sent to the
    assigned Customs and Logistics reviewer before shipment authorization
    or operational release."""

    class Decision(models.TextChoices):
        PENDING = "pending", "Pendiente"
        APPROVED_AS_IS = "approved_as_is", "Aprobado tal cual"
        REQUIRES_AMENDMENT = "requires_amendment", "Requiere enmienda de documentos oficiales"
        ESCALATED = "escalated", "Escalado"

    manifest_variance = models.OneToOneField(ManifestVariance, on_delete=models.CASCADE, related_name="customs_review")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    decision = models.CharField(max_length=30, choices=Decision.choices, default=Decision.PENDING)
    decided_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)


class ContainerLoadEvent(BaseModel):
    """Origin loading confirmation (spec section 13A.9) — Harrison/Edison
    confirm actual loading at line/package level before origin handoff."""

    class LoadStatus(models.TextChoices):
        PLANNED = "planned", "Planeado para el contenedor"
        PRESENTED = "presented", "Presentado para carga"
        LOADED = "loaded", "Cargado"
        NOT_LOADED = "not_loaded", "No cargado"
        PARTIALLY_LOADED = "partially_loaded", "Cargado parcialmente"
        ADDED_AT_LOADING = "added_at_loading", "Agregado durante la carga"
        REMOVED_AT_LOADING = "removed_at_loading", "Retirado durante la carga"
        PACKAGE_SUBSTITUTED = "package_substituted", "Paquete sustituido"
        QUANTITY_CORRECTED = "quantity_corrected", "Cantidad corregida"
        AWAITING_EVIDENCE = "awaiting_evidence", "En espera de evidencia"

    manifest_line = models.ForeignKey(ManifestLine, on_delete=models.CASCADE, related_name="load_events")
    status = models.CharField(max_length=30, choices=LoadStatus.choices, default=LoadStatus.PLANNED)
    responsible_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    occurred_at = models.DateTimeField(null=True, blank=True)


class ContainerLoadEvidence(BaseModel):
    load_event = models.ForeignKey(ContainerLoadEvent, on_delete=models.CASCADE, related_name="evidence")
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")


class ContainerSpaceAddition(BaseModel):
    """Spec section 13A.9: cargo added because unused container space was
    available requires a source commitment, reason, and customs review —
    the exact fixture case of the Yekalon "REPOSICIÓN" doors and the
    "Arena Countertop Replacement" lines riding in this container."""

    container = models.ForeignKey(Container, on_delete=models.CASCADE, related_name="space_additions")
    manifest_line = models.ForeignKey(ManifestLine, on_delete=models.CASCADE, related_name="space_addition")
    source_commitment_note = models.CharField(max_length=255, blank=True)
    reason = models.TextField()
    responsible_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    customs_review = models.ForeignKey(
        CustomsReviewDecision, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )


# ---------------------------------------------------------------------------
# Open commitments, prior fulfillment, replacement/corrective cargo
# ---------------------------------------------------------------------------


class OpenCommitment(BaseModel):
    """A PO/PI/quotation/supplier order that may be fulfilled across many
    shipments (spec section 13A.4). E.g. PI W5084 for ARENA totals 40
    kitchen sets / 70 vanity sets; this container only carries 10 kitchen
    sets and 30 vanity sets — the remainder stays open here."""

    purchase_order = models.ForeignKey(
        "procurement.PurchaseOrder", on_delete=models.CASCADE, related_name="open_commitments"
    )

    class Status(models.TextChoices):
        OPEN = "open", "Abierto"
        PARTIALLY_FULFILLED = "partially_fulfilled", "Parcialmente cumplido"
        CLOSED = "closed", "Cerrado"

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)

    def __str__(self):
        return f"Compromiso abierto: {self.purchase_order}"


class OpenCommitmentLine(BaseModel):
    open_commitment = models.ForeignKey(OpenCommitment, on_delete=models.CASCADE, related_name="lines")
    purchase_order_line = models.ForeignKey("procurement.PurchaseOrderLine", on_delete=models.CASCADE, related_name="+")

    quantity_original = models.DecimalField(max_digits=14, decimal_places=3)
    quantity_approved = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quantity_produced = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quantity_previously_shipped = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_previously_received = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_previously_delivered = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_previously_installed = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_allocated_current_shipment = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_loaded_current_shipment = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_cancelled = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_replaced = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_credited = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_uncertain = models.DecimalField(max_digits=14, decimal_places=3, default=0)

    @property
    def quantity_open(self):
        accounted = (
            self.quantity_previously_received
            + self.quantity_allocated_current_shipment
            + self.quantity_cancelled
            + self.quantity_replaced
            + self.quantity_credited
        )
        base = self.quantity_approved if self.quantity_approved is not None else self.quantity_original
        return base - accounted

    def __str__(self):
        return f"{self.open_commitment}: {self.purchase_order_line}"


class FulfillmentEvent(BaseModel):
    """A terminal disposition event for part of an OpenCommitmentLine's
    quantity (spec section 13A.4): received+accepted, installed+accepted,
    cancelled, credited, replaced, or written off."""

    class Kind(models.TextChoices):
        RECEIVED_ACCEPTED = "received_accepted", "Recibido y aceptado"
        INSTALLED_ACCEPTED = "installed_accepted", "Instalado y aceptado"
        CANCELLED = "cancelled", "Cancelado con aprobación"
        CREDITED_REFUNDED = "credited_refunded", "Acreditado o reembolsado"
        REPLACED = "replaced", "Reemplazado mediante caso vinculado"
        WRITTEN_OFF = "written_off", "Dado de baja con autorización"

    open_commitment_line = models.ForeignKey(OpenCommitmentLine, on_delete=models.CASCADE, related_name="fulfillment_events")
    kind = models.CharField(max_length=30, choices=Kind.choices)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    occurred_at = models.DateTimeField(null=True, blank=True)
    reason = models.TextField(blank=True)
    authorized_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")


class PriorFulfillmentEvidence(BaseModel):
    """Spec section 13A.5: a claim that part of a quantity was already
    received/delivered/installed previously must be backed by linked
    evidence, never just a changed expected quantity."""

    class Status(models.TextChoices):
        CLAIMED_UNVERIFIED = "claimed_unverified", "Reclamado — sin verificar"
        EVIDENCE_ATTACHED_PENDING = "evidence_attached_pending", "Evidencia adjunta — pendiente de revisión"
        VERIFIED_RECEIVED = "verified_received", "Verificado: recibido previamente"
        VERIFIED_DELIVERED = "verified_delivered", "Verificado: entregado previamente"
        VERIFIED_INSTALLED = "verified_installed", "Verificado: instalado previamente"
        REJECTED = "rejected", "Reclamo rechazado"
        UNKNOWN_HISTORICAL = "unknown_historical", "Desconocido (histórico)"

    open_commitment_line = models.ForeignKey(OpenCommitmentLine, on_delete=models.CASCADE, related_name="prior_fulfillment_evidence")
    quantity_claimed = models.DecimalField(max_digits=14, decimal_places=3)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.CLAIMED_UNVERIFIED)
    evidence_document = models.ForeignKey(
        "documents.Document", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    evidence_description = models.TextField(
        blank=True, help_text="Earlier BL/container, packing list, receipt, delivery note, warehouse movement, "
        "project receipt, photos, installation/inspection record, or named historical attestation."
    )
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)


class ShipmentCarryoverAllocation(BaseModel):
    """Links leftover quantity from an open commitment into a *later*
    shipment without losing its original commercial identity (spec
    section 13A.4 / 00_READ_ME section 7)."""

    open_commitment_line = models.ForeignKey(OpenCommitmentLine, on_delete=models.CASCADE, related_name="carryovers")
    target_shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="carryover_allocations")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    notes = models.TextField(blank=True)


class ReplacementCase(BaseModel):
    """Spec section 13A.6: replacement cargo must retain its causal chain
    and must never be counted as a new normal purchase. Fixture instances:
    Yekalon doors for buildings 17–22 marked "ÚNICAMENTE PARA REPOSICIÓN",
    and the Boman "Arena Countertop Replacement C/F(K2-2) G(K1-2)
    B/E(K2-1)" lines."""

    class ResponsibilityParty(models.TextChoices):
        SUPPLIER = "supplier", "Proveedor"
        DT_BEACH = "dt_beach", "DT Beach"
        UNKNOWN = "unknown", "Desconocido"

    class ChargeType(models.TextChoices):
        PAID = "paid", "Pagado"
        FREE = "free", "Sin cargo"
        CREDITED = "credited", "Acreditado"
        WARRANTY = "warranty", "Garantía"

    original_purchase_order_line = models.ForeignKey(
        "procurement.PurchaseOrderLine", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    original_item = models.ForeignKey("items.Item", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    defect_or_drawing_error = models.TextField(blank=True)
    identified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    identified_date = models.DateField(null=True, blank=True)
    quantity_affected = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    supplier_responsibility = models.CharField(max_length=20, choices=ResponsibilityParty.choices, default=ResponsibilityParty.UNKNOWN)
    charge_type = models.CharField(max_length=20, choices=ChargeType.choices, default=ChargeType.FREE)
    replacement_manifest_line = models.ForeignKey(
        ManifestLine, on_delete=models.SET_NULL, null=True, blank=True, related_name="replacement_case"
    )
    destination_building = models.ForeignKey("projects.Building", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    claim_status = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"Reemplazo: {self.original_item or self.defect_or_drawing_error[:40]}"


class CorrectiveActionCase(BaseModel):
    """Broader corrective-action wrapper around one or more ReplacementCase
    records, for cases spanning several items/lines (e.g. the W5076
    glass-safety drawing-error case referenced in 00_READ_ME section 4)."""

    title = models.CharField(max_length=255)
    original_purchase_order = models.ForeignKey(
        "procurement.PurchaseOrder", on_delete=models.SET_NULL, null=True, blank=True, related_name="corrective_cases"
    )
    description = models.TextField(blank=True)
    replacement_cases = models.ManyToManyField(ReplacementCase, blank=True, related_name="corrective_action_cases")

    def __str__(self):
        return self.title


class WarrantyOrNoChargeLine(BaseModel):
    manifest_line = models.OneToOneField(ManifestLine, on_delete=models.CASCADE, related_name="warranty_detail")
    reason = models.CharField(max_length=255)
    replacement_case = models.ForeignKey(ReplacementCase, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")


class LoadingEvidence(BaseModel):
    container = models.ForeignKey(Container, on_delete=models.CASCADE, related_name="loading_evidence")
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")
    caption = models.CharField(max_length=255, blank=True)


# ---------------------------------------------------------------------------
# Logistics planning (spec section 16) — Priority 1, kept lightweight
# ---------------------------------------------------------------------------


class LogisticsQuotation(BaseModel):
    shipment = models.ForeignKey(
        Shipment, on_delete=models.CASCADE, related_name="logistics_quotations", null=True, blank=True
    )
    carrier = models.CharField(max_length=150)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.carrier} quotation"


class LogisticsOption(BaseModel):
    quotation = models.ForeignKey(LogisticsQuotation, on_delete=models.CASCADE, related_name="options")
    origin_port = models.CharField(max_length=150, blank=True)
    destination_port = models.CharField(max_length=150, blank=True)
    etd = models.DateField(null=True, blank=True)
    eta = models.DateField(null=True, blank=True)
    transit_days = models.PositiveIntegerField(null=True, blank=True)
    has_transshipment = models.BooleanField(default=False)
    stops = models.CharField(max_length=255, blank=True)
    space_confirmed = models.BooleanField(default=False)
    cutoff_date = models.DateField(null=True, blank=True)
    free_time_days = models.PositiveIntegerField(null=True, blank=True)
    estimated_total_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    rollover_risk_note = models.CharField(max_length=255, blank=True)
    marketed_as_direct = models.BooleanField(
        default=False,
        help_text='Spec section 16: track the marketing claim separately from `has_transshipment` fact.',
    )
    selected = models.BooleanField(default=False)
    decision_reason = models.TextField(blank=True)
