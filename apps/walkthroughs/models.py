from django.conf import settings
from django.db import models

from apps.core.models import BaseModel

# ---------------------------------------------------------------------------
# Apartment walkthroughs and corrective actions (one-shot release,
# section 6). Building-agnostic by construction: every walkthrough
# needs only a `building` — floor/unit/units/area are all optional, so
# the exact same model/service/view code works for every configured
# active building (Arena T1's 8 buildings, Palmera, Sole 26, Sole,
# Sole PH, Mare B) without any per-building branching. A corrective
# defect found during a walkthrough becomes a real, fully-lifecycled
# `apps.fieldissues.FieldIssue` (linked via `WalkthroughItem
# .field_issue` / `FieldIssue.walkthrough_item`) — never a duplicated,
# parallel correction workflow.
# ---------------------------------------------------------------------------


class WalkthroughCategory(BaseModel):
    """Configurable taxonomy (same pattern as `IssueCategory`/
    `TrainingCategory`) — windows, sliding doors, interior/entry doors,
    kitchens, vanities, complete apartment, and any future category."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="walkthrough_categories")
    name = models.CharField(max_length=150)
    code = models.SlugField(max_length=60)

    class Meta:
        unique_together = [("organization", "code")]
        verbose_name_plural = "walkthrough categories"

    def __str__(self):
        return self.name


class WalkthroughChecklistTemplateItem(BaseModel):
    """A configurable, reusable checklist item for a given category —
    e.g. the 16 window/sliding-door checks. Seeded as *initial* data
    (`seed_walkthrough_checklist_templates`), never hard-coded into a
    Python branch; an organization can add/edit/remove entries freely
    afterward."""

    category = models.ForeignKey(WalkthroughCategory, on_delete=models.CASCADE, related_name="checklist_template_items")
    description = models.CharField(max_length=255)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "description"]

    def __str__(self):
        return f"{self.category}: {self.description}"


class Walkthrough(BaseModel):
    class Purpose(models.TextChoices):
        CONSTRUCTION_PROGRESS = "construction_progress", "Recorrido de progreso de construcción"
        QUALITY_CONTROL = "quality_control", "Recorrido de control de calidad"
        TRAINING_REFERENCE = "training_reference", "Recorrido de capacitación/instalación de referencia"
        PRE_DELIVERY_FINAL = "pre_delivery_final", "Recorrido previo a la entrega final"
        FINAL_HANDOVER_DELIVERY = "final_handover_delivery", "Recorrido de entrega/traspaso final"
        REINSPECTION = "reinspection", "Recorrido de reinspección"

    class ProgressStatus(models.TextChoices):
        NOT_STARTED = "not_started", "No iniciado"
        IN_PROGRESS = "in_progress", "En progreso"
        COMPLETED = "completed", "Completado"

    class DeliveryDecision(models.TextChoices):
        PENDING = "pending", "Pendiente"
        READY = "ready", "Listo para entrega"
        NOT_READY = "not_ready", "No listo para entrega"

    building = models.ForeignKey("projects.Building", on_delete=models.PROTECT, related_name="walkthroughs")
    floor = models.ForeignKey("projects.Floor", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    unit = models.ForeignKey(
        "projects.Unit", on_delete=models.SET_NULL, null=True, blank=True, related_name="walkthroughs",
        help_text="Primary/single unit for a one-apartment walkthrough.",
    )
    units = models.ManyToManyField(
        "projects.Unit", blank=True, related_name="group_walkthroughs",
        help_text="Additional units when this walkthrough covers a selected group of apartments.",
    )
    area = models.ForeignKey("projects.Area", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    purpose = models.CharField(max_length=30, choices=Purpose.choices)
    category = models.ForeignKey(WalkthroughCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    inspector = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    participants = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="walkthroughs_attended")

    scheduled_date = models.DateField(null=True, blank=True)
    actual_date = models.DateField(null=True, blank=True)
    progress_status = models.CharField(max_length=20, choices=ProgressStatus.choices, default=ProgressStatus.NOT_STARTED)

    drawing = models.ForeignKey("drawings.Drawing", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    training_session = models.ForeignKey("training.TrainingSession", on_delete=models.SET_NULL, null=True, blank=True, related_name="walkthroughs")
    previous_walkthrough = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="follow_up_walkthroughs",
        help_text="Set for a reinspection walkthrough — the prior attempt is never edited, only referenced.",
    )
    summary = models.TextField(blank=True)

    delivery_decision = models.CharField(max_length=20, choices=DeliveryDecision.choices, default=DeliveryDecision.PENDING)
    delivery_decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    delivery_decided_at = models.DateTimeField(null=True, blank=True)
    delivery_override_reason = models.TextField(blank=True)
    delivery_overridden_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        location = self.unit or self.floor or self.building
        return f"{self.get_purpose_display()} — {location}"


class WalkthroughItem(BaseModel):
    class Result(models.TextChoices):
        PASS = "pass", "Aprobado"
        CONDITIONAL = "conditional", "Aprobado condicionado"
        FAIL = "fail", "Rechazado"
        NOT_APPLICABLE = "not_applicable", "No aplica"
        PENDING = "pending", "Pendiente"

    class ConditionCheck(models.TextChoices):
        OK = "ok", "Correcto"
        NOT_OK = "not_ok", "Incorrecto"
        NOT_APPLICABLE = "not_applicable", "No aplica"
        NOT_CHECKED = "not_checked", "No verificado"

    walkthrough = models.ForeignKey(Walkthrough, on_delete=models.CASCADE, related_name="items")
    unit = models.ForeignKey(
        "projects.Unit", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Which specific unit this item belongs to, when the parent walkthrough spans more than one.",
    )
    room_or_location = models.CharField(max_length=255, blank=True)
    item = models.ForeignKey("items.Item", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    component_description = models.CharField(max_length=255, blank=True)

    checklist_result = models.CharField(max_length=20, choices=Result.choices, default=Result.PENDING)
    measurement = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    measurement_unit = models.CharField(max_length=30, blank=True)
    digital_level_reading = models.CharField(max_length=100, blank=True)
    level_condition = models.CharField(max_length=20, choices=ConditionCheck.choices, default=ConditionCheck.NOT_CHECKED)
    plumb_condition = models.CharField(max_length=20, choices=ConditionCheck.choices, default=ConditionCheck.NOT_CHECKED)
    square_condition = models.CharField(max_length=20, choices=ConditionCheck.choices, default=ConditionCheck.NOT_CHECKED)
    operational_test = models.CharField(max_length=20, choices=ConditionCheck.choices, default=ConditionCheck.NOT_CHECKED)

    condition_found = models.TextField(blank=True)
    adjustment_performed = models.TextField(blank=True)
    condition_after_adjustment = models.TextField(
        blank=True, help_text="Never overwrites `condition_found` — both are preserved distinctly.",
    )
    remaining_defect = models.TextField(blank=True)
    is_blocking_defect = models.BooleanField(default=False)

    installer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    supplier = models.ForeignKey("procurement.Supplier", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    purchase_order_line = models.ForeignKey("procurement.PurchaseOrderLine", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    container = models.ForeignKey("shipments.Container", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    field_issue = models.ForeignKey(
        "fieldissues.FieldIssue", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="The corrective issue created from this item, if any — the correction/verification "
        "lifecycle itself lives entirely on FieldIssue, never duplicated here.",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.walkthrough} — {self.room_or_location or self.component_description or self.item}"


class WalkthroughItemEvidence(BaseModel):
    """Same stage-tagged wrapper as `FieldIssueEvidence`/
    `TrainingEvidence` — reused shape, per this codebase's established
    per-domain evidence-wrapper convention."""

    class Stage(models.TextChoices):
        BEFORE = "before", "Antes"
        DURING = "during", "Durante"
        AFTER = "after", "Después"

    item = models.ForeignKey(WalkthroughItem, on_delete=models.CASCADE, related_name="evidence")
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")
    stage = models.CharField(max_length=20, choices=Stage.choices)
    caption = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.get_stage_display()} — {self.item}"
