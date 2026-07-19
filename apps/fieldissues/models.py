from django.conf import settings
from django.db import models

from apps.core.models import BaseModel

# ---------------------------------------------------------------------------
# Field issue reporting and corrective-action tracking (one-shot release,
# section 4). Mobile-first: only the physical building is required at
# creation time — floor/unit/exact location may be refined later without
# losing the original report's provenance (created_at/created_by never
# change).
# ---------------------------------------------------------------------------


class IssueCategory(BaseModel):
    """Configurable taxonomy (same pattern as `apps.documents.DocumentType`)
    — never a hard-coded choices list baked into business logic."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="issue_categories")
    name = models.CharField(max_length=150)
    code = models.SlugField(max_length=60)

    class Meta:
        unique_together = [("organization", "code")]
        verbose_name_plural = "issue categories"

    def __str__(self):
        return self.name


class FieldIssue(BaseModel):
    class Priority(models.TextChoices):
        LOW = "low", "Baja"
        MEDIUM = "medium", "Media"
        HIGH = "high", "Alta"
        URGENT = "urgent", "Urgente"

    class Status(models.TextChoices):
        REPORTED = "reported", "Reportado"
        ASSIGNED = "assigned", "Asignado"
        IN_PROGRESS = "in_progress", "En progreso"
        CORRECTION_COMPLETED = "correction_completed", "Corrección completada"
        READY_FOR_VERIFICATION = "ready_for_verification", "Listo para verificación"
        VERIFIED_CLOSED = "verified_closed", "Verificado y cerrado"
        RETURNED_FOR_CORRECTION = "returned_for_correction", "Devuelto para corrección"
        RESUBMITTED = "resubmitted", "Reenviado"
        REINSPECTION = "reinspection", "Reinspección"

    # Location — building is required; everything below it is optional and
    # refinable later without losing the original report.
    building = models.ForeignKey("projects.Building", on_delete=models.PROTECT, related_name="field_issues")
    floor = models.ForeignKey("projects.Floor", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    unit = models.ForeignKey("projects.Unit", on_delete=models.SET_NULL, null=True, blank=True, related_name="field_issues")
    room_or_location = models.CharField(max_length=255, blank=True)

    category = models.ForeignKey(IssueCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    item = models.ForeignKey("items.Item", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    component_description = models.CharField(max_length=255, blank=True)

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.MEDIUM)

    responsible_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    responsible_department = models.ForeignKey("accounts.Department", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    watchers = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="+")
    due_date = models.DateField(null=True, blank=True)

    # Traceability
    supplier = models.ForeignKey("procurement.Supplier", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    purchase_order_line = models.ForeignKey("procurement.PurchaseOrderLine", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    shipment = models.ForeignKey("shipments.Shipment", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    container = models.ForeignKey("shipments.Container", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    inventory_lot = models.ForeignKey("inventory.InventoryLot", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    delivery = models.ForeignKey("requests.Delivery", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    installation = models.ForeignKey("requests.InstallationRecord", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    inspection = models.ForeignKey("requests.InspectionRecord", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    drawing = models.ForeignKey("drawings.Drawing", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    training_session = models.ForeignKey(
        "training.TrainingSession", on_delete=models.SET_NULL, null=True, blank=True, related_name="field_issues",
        help_text="Set when this issue was created directly from a training session — preserves the relationship.",
    )
    walkthrough_item = models.ForeignKey(
        "walkthroughs.WalkthroughItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Set when this issue was created directly from a walkthrough inspection item.",
    )

    status = models.CharField(max_length=30, choices=Status.choices, default=Status.REPORTED)

    correction_description = models.TextField(blank=True)
    correction_performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    correction_completed_at = models.DateTimeField(null=True, blank=True)
    before_evidence_waived_reason = models.TextField(
        blank=True, help_text="Recorded by an authorized user when before-evidence genuinely could not be captured.",
    )

    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.building})"


class FieldIssueEvidence(BaseModel):
    """Reuses `apps.documents.Document` for the actual file (SHA-256,
    duplicate detection); `stage` is the one piece of domain-specific
    metadata the generic `Attachment` model doesn't carry, needed to
    distinguish before/during/after for the closure-evidence rule."""

    class Stage(models.TextChoices):
        BEFORE = "before", "Antes"
        DURING = "during", "Durante"
        AFTER = "after", "Después"

    field_issue = models.ForeignKey(FieldIssue, on_delete=models.CASCADE, related_name="evidence")
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")
    stage = models.CharField(max_length=20, choices=Stage.choices)
    caption = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.get_stage_display()} — {self.field_issue}"
