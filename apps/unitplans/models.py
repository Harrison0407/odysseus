from django.conf import settings
from django.db import models

from apps.core.models import BaseModel, ConfidenceLevel

# ---------------------------------------------------------------------------
# Interactive Apartment Plan / Room-Zone layer (one-shot release addendum).
#
# Two distinct layers, per the release's explicit instruction:
#   1. Source Drawing (apps.drawings.Drawing) — the original uploaded PDF,
#      immutable file provenance, drawing number/revision/status. Never
#      modified here.
#   2. Operational Interactive Plan (this app) — a derived image/SVG
#      linked to its exact source drawing and page, with room/zone
#      overlays, mapping status, and a verifier. Never presented as the
#      original architect drawing.
#
# A `UnitPlanTemplate` is reusable across every physical unit that shares
# the same family/unit-type-letter/floor-variant — never one row per
# apartment. `UnitPlanAssignment` links a `Unit` to its *current* effective
# template; reassigning (a later plan revision, a remap) never edits or
# deletes the old assignment, it creates a new one and marks the old
# `is_current=False` (the same versioned-immutable-row pattern used
# throughout this release for Drawing/OrderLineAllocation/
# EvidenceClassification/Walkthrough). Anything created from a plan click
# (FieldIssue, WalkthroughItem, ...) stores its own direct FK to the
# template/zone that was effective *at that moment* — so a later
# superseded template never alters that historical record.
# ---------------------------------------------------------------------------


class UnitPlanTemplate(BaseModel):
    class FloorVariant(models.TextChoices):
        FIRST_FLOOR = "first_floor", "Planta baja / primer nivel"
        UPPER_FLOOR = "upper_floor", "Niveles superiores"
        ALL_FLOORS = "all_floors", "Todos los niveles (sin variante)"
        PENTHOUSE_DUPLEX = "penthouse_duplex", "Penthouse dúplex"

    class Status(models.TextChoices):
        DRAFT = "draft", "Borrador"
        NEEDS_REVIEW = "needs_review", "Requiere revisión"
        APPROVED = "approved", "Aprobado para uso operativo"
        MISSING_SOURCE = "missing_source", "Fuente faltante"
        SUPERSEDED = "superseded", "Reemplazado"

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="unit_plan_templates")
    family = models.ForeignKey("projects.BuildingFamily", on_delete=models.CASCADE, related_name="unit_plan_templates")
    code = models.SlugField(max_length=80, help_text='Stable slug, e.g. "palmera-a-all_floors".')
    unit_type_letter = models.CharField(max_length=20, help_text='Apartment letter/type as in the source, e.g. "A", "D (PH)".')
    floor_variant = models.CharField(max_length=30, choices=FloorVariant.choices)
    is_mirrored = models.BooleanField(default=False)
    orientation = models.CharField(max_length=50, blank=True, help_text='Free-text orientation note when relevant, e.g. "esquina norte".')

    source_drawing = models.ForeignKey(
        "drawings.Drawing", on_delete=models.SET_NULL, null=True, blank=True, related_name="unit_plan_templates",
        help_text="Immutable source drawing this template was derived from — never modified by this app.",
    )
    source_page = models.PositiveIntegerField(null=True, blank=True)
    derived_plan_document = models.ForeignKey(
        "documents.Document", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="The derived crop/image (own Document/DocumentVersion, own checksum) — never the original architect file.",
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    uncertainty_notes = models.TextField(blank=True, help_text="Documents why a slot is Missing Source / Needs Review, or any known ambiguity.")

    verifier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)

    supersedes = models.OneToOneField(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="superseded_by",
        help_text="The exact prior template revision this one replaces — never edited, only marked superseded.",
    )
    is_current = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], condition=models.Q(is_current=True),
                name="unique_current_template_code_per_org",
            ),
        ]
        ordering = ["family__display_name", "unit_type_letter", "floor_variant"]

    def __str__(self):
        return f"{self.family.display_name} — Tipo {self.unit_type_letter} ({self.get_floor_variant_display()})"


class PlanZone(BaseModel):
    class ZoneType(models.TextChoices):
        ENTRANCE = "entrance", "Entrada"
        LOBBY_HALLWAY = "lobby_hallway", "Vestíbulo/pasillo"
        LIVING_ROOM = "living_room", "Sala"
        DINING_ROOM = "dining_room", "Comedor"
        KITCHEN = "kitchen", "Cocina"
        PRIMARY_BEDROOM = "primary_bedroom", "Habitación principal"
        SECONDARY_BEDROOM = "secondary_bedroom", "Habitación secundaria"
        ADDITIONAL_BEDROOM = "additional_bedroom", "Habitación adicional"
        BATHROOM = "bathroom", "Baño"
        HALF_BATHROOM = "half_bathroom", "Medio baño"
        SERVICE_ROOM = "service_room", "Cuarto de servicio"
        LAUNDRY = "laundry", "Lavandería"
        BALCONY = "balcony", "Balcón"
        TERRACE = "terrace", "Terraza"
        CLOSET = "closet", "Closet"
        MECHANICAL_SERVICE = "mechanical_service", "Área mecánica/servicio"
        HOTEL_ROOM = "hotel_room", "Habitación de hotel"
        COMMON_AREA = "common_area", "Área común"
        OTHER = "other", "Otro (configurable)"

    class ShapeType(models.TextChoices):
        RECT = "rect", "Rectángulo"
        POLYGON = "polygon", "Polígono"

    class ValidationState(models.TextChoices):
        DRAFT = "draft", "Borrador"
        NEEDS_REVIEW = "needs_review", "Requiere revisión"
        VALIDATED = "validated", "Validado"

    template = models.ForeignKey(UnitPlanTemplate, on_delete=models.CASCADE, related_name="zones")
    zone_code = models.CharField(max_length=50, help_text="Stable code within the template, e.g. \"kitchen\", \"bedroom-1\".")
    name_es = models.CharField(max_length=150)
    name_en = models.CharField(max_length=150, blank=True)
    zone_type = models.CharField(max_length=30, choices=ZoneType.choices)
    custom_type_label = models.CharField(max_length=100, blank=True, help_text='Used only when zone_type="other".')

    shape_type = models.CharField(max_length=10, choices=ShapeType.choices, default=ShapeType.RECT)
    coordinates = models.JSONField(
        help_text="Relative (0-1) coordinates: {x0,y0,x1,y1} for rect, or [{x,y}, ...] for polygon.",
    )
    duplex_floor = models.CharField(max_length=10, blank=True, help_text='Which physical level within a duplex, e.g. "4" or "5".')
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    validation_state = models.CharField(max_length=20, choices=ValidationState.choices, default=ValidationState.DRAFT)
    source_confidence = models.CharField(max_length=20, choices=ConfidenceLevel.choices, default=ConfidenceLevel.LOW)
    verifier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)

    supersedes = models.OneToOneField(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="superseded_by",
        help_text="The exact prior zone shape this one replaces — never edited, only marked superseded.",
    )

    class Meta:
        ordering = ["template", "display_order", "zone_code"]

    def __str__(self):
        return f"{self.template} / {self.name_es}"

    def display_type_label(self):
        if self.zone_type == self.ZoneType.OTHER and self.custom_type_label:
            return self.custom_type_label
        return self.get_zone_type_display()


class UnitPlanAssignment(BaseModel):
    """Links a physical `Unit` to its *current* effective
    `UnitPlanTemplate`. Reassigning never edits or deletes the prior
    assignment — it creates a new row and marks the old one
    `is_current=False`, linked via `supersedes` (same pattern as
    `Drawing.supersedes`)."""

    unit = models.ForeignKey("projects.Unit", on_delete=models.CASCADE, related_name="plan_assignments")
    template = models.ForeignKey(UnitPlanTemplate, on_delete=models.PROTECT, related_name="assignments")
    is_current = models.BooleanField(default=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    supersedes = models.OneToOneField(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="superseded_by",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["unit"], condition=models.Q(is_current=True), name="unique_current_assignment_per_unit"),
        ]

    def __str__(self):
        return f"{self.unit} -> {self.template}"
