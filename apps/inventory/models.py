from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class StorageSite(BaseModel):
    class SiteType(models.TextChoices):
        CENTRAL_WAREHOUSE = "central_warehouse", "Almacén central"
        TEMPORARY_WAREHOUSE = "temporary_warehouse", "Almacén temporal"
        PROJECT_BUILDING = "project_building", "Edificio de proyecto"
        OUTDOOR_YARD = "outdoor_yard", "Patio exterior"
        COVERED_EXTERIOR = "covered_exterior", "Exterior techado"
        PORT_STORAGE = "port_storage", "Almacenaje portuario"
        EXTERNAL_WAREHOUSE = "external_warehouse", "Almacén externo"
        CONTAINER = "container", "Contenedor"
        QUARANTINE_AREA = "quarantine_area", "Zona de cuarentena"
        DISPATCH_STAGING = "dispatch_staging", "Zona de despacho"

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="storage_sites")
    name = models.CharField(max_length=150)
    site_type = models.CharField(max_length=30, choices=SiteType.choices)
    project = models.ForeignKey("projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    building = models.ForeignKey("projects.Building", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    custodian = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class WarehouseZone(BaseModel):
    """E.g. Manuel's recommended zones: Recepción, Cuarentena, Seco y
    sensible, Pesado resistente, Consumibles, Herramientas, Despacho."""

    site = models.ForeignKey(StorageSite, on_delete=models.CASCADE, related_name="zones")
    name = models.CharField(max_length=150)
    code = models.SlugField(max_length=50)

    class Meta:
        unique_together = [("site", "code")]

    def __str__(self):
        return f"{self.site} / {self.name}"


class WarehouseLocation(BaseModel):
    zone = models.ForeignKey(WarehouseZone, on_delete=models.CASCADE, related_name="locations")
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=150, blank=True)
    allowed_categories = models.ManyToManyField("items.ProductCategory", blank=True, related_name="+")
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("zone", "code")]

    def __str__(self):
        return f"{self.zone.site.name} / {self.code}"


class LocationSuitability(BaseModel):
    location = models.OneToOneField(WarehouseLocation, on_delete=models.CASCADE, related_name="suitability")
    covered = models.BooleanField(default=False)
    dry = models.BooleanField(default=False)
    climate_controlled = models.BooleanField(default=False)
    secure = models.BooleanField(default=False)
    access_controlled = models.BooleanField(default=False)
    flood_risk = models.BooleanField(default=False)
    leak_risk = models.BooleanField(default=False)
    sun_exposure = models.BooleanField(default=False)
    rain_exposure = models.BooleanField(default=False)
    floor_load_kg_per_sqm = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    truck_access = models.BooleanField(default=False)
    telehandler_access = models.BooleanField(default=False)

    def __str__(self):
        return f"Suitability: {self.location}"


class LocationCapacity(BaseModel):
    location = models.OneToOneField(WarehouseLocation, on_delete=models.CASCADE, related_name="capacity")
    available_volume_cbm = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    available_area_sqm = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    weight_capacity_kg = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)


class InventoryLot(BaseModel):
    """A traceable batch of an item at a source shipment/receipt — the
    unit that InventoryMovement always references (core principle 4.5:
    ledger-based inventory, never a directly edited stock number)."""

    item = models.ForeignKey("items.Item", on_delete=models.PROTECT, related_name="lots")
    source_receipt_line = models.ForeignKey(
        "receiving.ReceiptLine", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    lot_code = models.CharField(max_length=100, blank=True)
    bought_for_scope = models.CharField(max_length=30, blank=True, help_text="DestinationScope at creation time.")
    bought_for_building = models.ForeignKey(
        "projects.Building", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    def __str__(self):
        return f"Lote {self.lot_code or self.id} — {self.item}"


class MovementType(models.TextChoices):
    RECEIPT = "receipt", "Recepción"
    PROVISIONAL_RECEIPT = "provisional_receipt", "Recepción provisional"
    QUARANTINE = "quarantine", "Cuarentena"
    RELEASE_FROM_QUARANTINE = "release_from_quarantine", "Liberación de cuarentena"
    PUT_AWAY = "put_away", "Ubicación (put-away)"
    TRANSFER = "transfer", "Transferencia"
    RESERVATION = "reservation", "Reserva"
    PICK = "pick", "Picking"
    DISPATCH = "dispatch", "Despacho"
    PROJECT_DELIVERY = "project_delivery", "Entrega a proyecto"
    RETURN = "return", "Devolución"
    ADJUSTMENT = "adjustment", "Ajuste"
    DAMAGE = "damage", "Daño"
    WRITE_OFF = "write_off", "Baja"
    INSTALLATION_CONSUMPTION = "installation_consumption", "Consumo por instalación"
    RECLASSIFICATION = "reclassification", "Reclasificación"
    HISTORICAL_OPENING_BALANCE = "historical_opening_balance", "Saldo inicial histórico"


class InventoryMovement(BaseModel):
    """The append-only ledger. On-hand quantity is always *derived* from
    posted movements — never edited directly (core principle 4.5)."""

    lot = models.ForeignKey(InventoryLot, on_delete=models.PROTECT, related_name="movements")
    movement_type = models.CharField(max_length=30, choices=MovementType.choices)
    quantity = models.DecimalField(max_digits=14, decimal_places=3, help_text="Positive = increases on-hand at to_location.")
    unit_of_measure = models.ForeignKey("items.UnitOfMeasure", on_delete=models.PROTECT, related_name="+")

    from_location = models.ForeignKey(
        WarehouseLocation, on_delete=models.SET_NULL, null=True, blank=True, related_name="movements_out"
    )
    to_location = models.ForeignKey(
        WarehouseLocation, on_delete=models.SET_NULL, null=True, blank=True, related_name="movements_in"
    )

    intended_destination_scope = models.CharField(max_length=30, blank=True)
    actual_destination_building = models.ForeignKey(
        "projects.Building", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    posted_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=255, blank=True)
    requires_approval = models.BooleanField(default=False)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    idempotency_key = models.CharField(
        max_length=100, unique=True, null=True, blank=True,
        help_text="Prevents duplicate submission from double-posting the same movement (spec section 32).",
    )

    class Meta:
        ordering = ["-posted_at"]

    def __str__(self):
        return f"{self.movement_type}: {self.quantity} {self.unit_of_measure} ({self.lot.item})"


class InventoryReservation(BaseModel):
    lot = models.ForeignKey(InventoryLot, on_delete=models.CASCADE, related_name="reservations")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    material_request_line = models.ForeignKey(
        "requests.MaterialRequestLine", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    is_active = models.BooleanField(default=True)


class QuarantineRecord(BaseModel):
    lot = models.ForeignKey(InventoryLot, on_delete=models.CASCADE, related_name="quarantine_records")
    location = models.ForeignKey(WarehouseLocation, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    reason = models.CharField(max_length=255)
    placed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    placed_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    def __str__(self):
        return f"Cuarentena: {self.lot} ({self.quantity})"


class CycleCount(BaseModel):
    site = models.ForeignKey(StorageSite, on_delete=models.CASCADE, related_name="cycle_counts")
    planned_date = models.DateField(null=True, blank=True)
    is_blind_count = models.BooleanField(default=True)
    assigned_counter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    def __str__(self):
        return f"Conteo cíclico {self.id} — {self.site}"


class CycleCountLine(BaseModel):
    cycle_count = models.ForeignKey(CycleCount, on_delete=models.CASCADE, related_name="lines")
    lot = models.ForeignKey(InventoryLot, on_delete=models.CASCADE, related_name="+")
    system_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    physical_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    variance = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    recounted = models.BooleanField(default=False)
    explanation = models.TextField(blank=True)
    approved_adjustment = models.ForeignKey(
        "InventoryAdjustment", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )


class InventoryAdjustment(BaseModel):
    lot = models.ForeignKey(InventoryLot, on_delete=models.CASCADE, related_name="adjustments")
    quantity_delta = models.DecimalField(max_digits=14, decimal_places=3)
    reason = models.CharField(max_length=255)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    resulting_movement = models.OneToOneField(
        InventoryMovement, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    def __str__(self):
        return f"Ajuste {self.quantity_delta} — {self.lot}"
