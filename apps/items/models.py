from django.db import models

from apps.core.models import BaseModel


class UnitOfMeasure(BaseModel):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="units_of_measure")
    code = models.CharField(max_length=20, help_text='E.g. "SET", "PC", "M2", "KG".')
    name = models.CharField(max_length=100)

    class Meta:
        unique_together = [("organization", "code")]

    def __str__(self):
        return self.code


class UnitConversion(BaseModel):
    from_unit = models.ForeignKey(UnitOfMeasure, on_delete=models.CASCADE, related_name="conversions_from")
    to_unit = models.ForeignKey(UnitOfMeasure, on_delete=models.CASCADE, related_name="conversions_to")
    factor = models.DecimalField(max_digits=18, decimal_places=8, help_text="Multiply a from_unit qty by this to get to_unit.")

    class Meta:
        unique_together = [("from_unit", "to_unit")]

    def __str__(self):
        return f"1 {self.from_unit} = {self.factor} {self.to_unit}"


class ProductCategory(BaseModel):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="product_categories")
    name = models.CharField(max_length=150)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")

    class Meta:
        verbose_name_plural = "product categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class ProductRiskProfile(BaseModel):
    """Drives risk-based sampling (spec section 18): glass, stone/quartz,
    fabricated countertops, disassembled doors, windows/profiles, special
    hardware, left/right variants, replacement parts are high risk."""

    class RiskLevel(models.TextChoices):
        LOW = "low", "Bajo"
        MEDIUM = "medium", "Medio"
        HIGH = "high", "Alto / crítico"

    category = models.OneToOneField(ProductCategory, on_delete=models.CASCADE, related_name="risk_profile")
    risk_level = models.CharField(max_length=20, choices=RiskLevel.choices, default=RiskLevel.MEDIUM)
    requires_full_inspection = models.BooleanField(default=False)
    default_sample_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=10)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.category} ({self.risk_level})"


class Item(BaseModel):
    """Canonical commercial/physical item identity. Spec section 14.1:
    "stone/slab/top/countertop" wording must never collapse two different
    physical identities (e.g. a raw quartz slab vs. a fabricated
    countertop piece) merely because the description looks similar."""

    class PhysicalForm(models.TextChoices):
        RAW_MATERIAL = "raw_material", "Materia prima (p. ej. plancha)"
        FABRICATED_PIECE = "fabricated_piece", "Pieza fabricada (p. ej. tope terminado)"
        COMPONENT = "component", "Componente de un ensamble"
        FINISHED_UNIT = "finished_unit", "Unidad terminada"
        KIT = "kit", "Kit / conjunto"

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="items")
    category = models.ForeignKey(ProductCategory, on_delete=models.PROTECT, related_name="items")
    name = models.CharField(max_length=255)
    physical_form = models.CharField(max_length=30, choices=PhysicalForm.choices, default=PhysicalForm.FINISHED_UNIT)
    base_unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")

    customer_item_code = models.CharField(max_length=100, blank=True)
    supplier_item_code = models.CharField(max_length=100, blank=True)
    drawing_code = models.CharField(max_length=100, blank=True)

    dimensions = models.CharField(max_length=100, blank=True, help_text='Free text, e.g. "3200x1600x20mm".')
    thickness_mm = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    color = models.CharField(max_length=100, blank=True)
    material = models.CharField(max_length=100, blank=True)
    orientation = models.CharField(
        max_length=20, blank=True, choices=[("left", "Izquierda"), ("right", "Derecha"), ("na", "N/A")]
    )
    model = models.CharField(max_length=100, blank=True)

    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ItemAlias(BaseModel):
    """Approved alternate reference for the same item — e.g. the fixture's
    "W5057" vs "W5097" ambiguity is recorded as two candidate aliases with
    a Possible Candidate match status (see apps.matching), never silently
    merged."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="aliases")
    alias_text = models.CharField(max_length=150)
    source = models.CharField(max_length=100, blank=True, help_text="e.g. supplier code, customer code, drawing ref")

    def __str__(self):
        return f"{self.alias_text} -> {self.item}"


class ItemVariant(BaseModel):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="variants")
    name = models.CharField(max_length=150)
    attributes = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.item} / {self.name}"


class AssemblyDefinition(BaseModel):
    """E.g. a door = leaf + frame + header + side pieces + trim + hinges +
    lock + screws + accessories (spec section 14.3)."""

    item = models.OneToOneField(Item, on_delete=models.CASCADE, related_name="assembly_definition")
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name


class AssemblyComponent(BaseModel):
    assembly = models.ForeignKey(AssemblyDefinition, on_delete=models.CASCADE, related_name="components")
    component_item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    quantity_per_assembly = models.DecimalField(max_digits=10, decimal_places=3, default=1)
    is_mandatory_for_completeness = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.assembly}: {self.quantity_per_assembly} x {self.component_item}"


class KitDefinition(BaseModel):
    """E.g. an apartment kitchen kit — expected box count, module numbers,
    hardware, panels (spec section 14.4). Completeness must be checked
    against required box identities, not merely a matching total count."""

    item = models.OneToOneField(Item, on_delete=models.CASCADE, related_name="kit_definition")
    name = models.CharField(max_length=200)
    expected_box_count = models.PositiveIntegerField(default=1)

    def __str__(self):
        return self.name


class KitInstance(BaseModel):
    """A concrete instance of a kit assigned to a destination (e.g. Kitchen
    A for APT-D)."""

    kit_definition = models.ForeignKey(KitDefinition, on_delete=models.PROTECT, related_name="instances")
    destination_scope = models.CharField(max_length=30, blank=True)
    unit = models.ForeignKey("projects.Unit", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Status(models.TextChoices):
        INCOMPLETE = "incomplete", "Incompleto"
        COMPLETE = "complete", "Completo"
        UNKNOWN = "unknown", "Desconocido"

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNKNOWN)

    def __str__(self):
        return f"{self.kit_definition} @ {self.unit or 'sin asignar'}"


class Package(BaseModel):
    """A physical box/crate/bundle. Package identity is central to spec
    section 13/14/18 — box number + total boxes in kit is how completeness
    is checked, never total quantity alone."""

    package_number = models.CharField(max_length=100)
    total_packages_in_kit = models.PositiveIntegerField(null=True, blank=True)
    kit_instance = models.ForeignKey(
        KitInstance, on_delete=models.SET_NULL, null=True, blank=True, related_name="packages"
    )
    gross_weight_kg = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    net_weight_kg = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    cbm = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    def __str__(self):
        return self.package_number


class PackageComponent(BaseModel):
    package = models.ForeignKey(Package, on_delete=models.CASCADE, related_name="components")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)

    def __str__(self):
        return f"{self.package}: {self.quantity} x {self.item}"


class ItemPhoto(BaseModel):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="photos")
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")
    caption = models.CharField(max_length=255, blank=True)


class TechnicalDrawing(BaseModel):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="drawings")
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")
    drawing_code = models.CharField(max_length=100, blank=True)
