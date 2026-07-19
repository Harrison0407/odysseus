from django.conf import settings
from django.db import models

from apps.core.models import BaseModel

# ---------------------------------------------------------------------------
# Drawing and floor-plan register (one-shot release, section 2).
#
# A Drawing wraps an existing apps.documents.Document (SHA-256 hashed,
# duplicate-detected — the same provenance system every other upload in
# this system already uses) with drawing-specific metadata. A new
# revision is always a NEW Drawing row (never an edit of an existing
# one) linked via `supersedes` — this is what guarantees "a later
# drawing revision must never silently replace the historical drawing
# linked to an earlier order or installation": any FK elsewhere in the
# system that points at a specific Drawing keeps pointing at that exact
# revision forever, even after a newer one is registered.
# ---------------------------------------------------------------------------


class Drawing(BaseModel):
    class DrawingType(models.TextChoices):
        SITE_PLAN = "site_plan", "Plano de conjunto"
        BUILDING_PLAN = "building_plan", "Plano de edificio"
        FLOOR_PLAN = "floor_plan", "Plano de piso"
        UNIT_TYPOLOGY = "unit_typology", "Tipología de unidad/apartamento"
        ROOM_DETAIL = "room_detail", "Plano de detalle/ambiente"

    class Discipline(models.TextChoices):
        ARCHITECTURAL = "architectural", "Arquitectónico"
        STRUCTURAL = "structural", "Estructural"
        ELECTRICAL = "electrical", "Eléctrico"
        SANITARY = "sanitary", "Sanitario"
        OTHER = "other", "Otro"

    class Status(models.TextChoices):
        REFERENCE = "reference", "Referencia"
        DRAFT = "draft", "Borrador"
        UNDER_REVIEW = "under_review", "En revisión"
        APPROVED = "approved", "Aprobado"
        SIGNED_STAMPED = "signed_stamped", "Firmado/Sellado"
        ISSUED_FOR_ORDER = "issued_for_order", "Emitido para orden"
        ISSUED_FOR_CONSTRUCTION = "issued_for_construction", "Emitido para construcción"
        EXECUTED_AS_BUILT = "executed_as_built", "Ejecutado / Tal como construido"
        SUPERSEDED = "superseded", "Reemplazado"

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="drawings")
    building_family = models.ForeignKey(
        "projects.BuildingFamily", on_delete=models.SET_NULL, null=True, blank=True, related_name="drawings"
    )
    building = models.ForeignKey(
        "projects.Building", on_delete=models.SET_NULL, null=True, blank=True, related_name="drawings"
    )
    floor = models.ForeignKey("projects.Floor", on_delete=models.SET_NULL, null=True, blank=True, related_name="drawings")
    unit = models.ForeignKey("projects.Unit", on_delete=models.SET_NULL, null=True, blank=True, related_name="drawings")

    drawing_type = models.CharField(max_length=30, choices=DrawingType.choices)
    discipline = models.CharField(max_length=30, choices=Discipline.choices, default=Discipline.OTHER)
    title = models.CharField(max_length=255)
    drawing_number = models.CharField(max_length=100, blank=True)
    source_document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")

    revision = models.PositiveIntegerField(default=1)
    issue_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.DRAFT)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    approval_date = models.DateField(null=True, blank=True)

    supersedes = models.OneToOneField(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="superseded_by",
        help_text="The exact prior revision this one replaces — that row is never edited, only marked superseded.",
    )
    is_current = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-revision", "title"]

    def __str__(self):
        return f"{self.title} rev.{self.revision} ({self.get_status_display()})"
