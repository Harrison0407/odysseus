from django.conf import settings
from django.db import models

from apps.core.models import BaseModel

# ---------------------------------------------------------------------------
# Lawson training and reference-installation sessions (one-shot release,
# section 5). Lawson is a configured user like any other trainer — never
# hard-coded into business logic; the immediate use case (Arena T1
# Building 11/12, two local installers) is seed/demo data, not a schema
# constraint. The workflow is building-agnostic by construction (every
# location field mirrors apps.fieldissues.FieldIssue's pattern: only
# `building` is required).
# ---------------------------------------------------------------------------


class TrainingCategory(BaseModel):
    """Configurable taxonomy (same pattern as `DocumentType`/
    `IssueCategory`) — aluminum windows, sliding doors, protective-film
    removal, kitchens, vanities, interior/entry doors, and any future
    system, all as configuration, never hard-coded choices."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="training_categories")
    name = models.CharField(max_length=150)
    code = models.SlugField(max_length=60)

    class Meta:
        unique_together = [("organization", "code")]
        verbose_name_plural = "training categories"

    def __str__(self):
        return self.name


class TrainingSession(BaseModel):
    building = models.ForeignKey("projects.Building", on_delete=models.PROTECT, related_name="training_sessions")
    floor = models.ForeignKey("projects.Floor", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    unit = models.ForeignKey("projects.Unit", on_delete=models.SET_NULL, null=True, blank=True, related_name="training_sessions")
    room_or_area = models.CharField(max_length=255, blank=True)

    category = models.ForeignKey(TrainingCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    trainer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    participants = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="training_sessions_attended")

    scheduled_at = models.DateTimeField(null=True, blank=True)
    actual_start = models.DateTimeField(null=True, blank=True)
    actual_finish = models.DateTimeField(null=True, blank=True)

    procedure_drawing = models.ForeignKey("drawings.Drawing", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    tools_instruments = models.TextField(blank=True)
    used_digital_level = models.BooleanField(default=False)
    digital_level_reading = models.CharField(max_length=100, blank=True)

    defects_discovered = models.TextField(blank=True)
    adjustment_demonstrated = models.TextField(blank=True)
    remaining_actions = models.TextField(blank=True)

    supervisor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    supervisor_signed_off_at = models.DateTimeField(null=True, blank=True)
    supervisor_notes = models.TextField(blank=True)

    is_approved_reference_installation = models.BooleanField(default=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Capacitación: {self.building} — {self.category or ''}"


class TrainingChecklistItem(BaseModel):
    class Result(models.TextChoices):
        PASS = "pass", "Aprobado"
        FAIL = "fail", "Rechazado"
        NOT_APPLICABLE = "not_applicable", "No aplica"

    session = models.ForeignKey(TrainingSession, on_delete=models.CASCADE, related_name="checklist_items")
    description = models.CharField(max_length=255)
    result = models.CharField(max_length=20, choices=Result.choices)
    notes = models.TextField(blank=True)


class TrainingParticipantAcknowledgement(BaseModel):
    session = models.ForeignKey(TrainingSession, on_delete=models.CASCADE, related_name="acknowledgements")
    participant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    acknowledged_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [("session", "participant")]


class TrainingEvidence(BaseModel):
    """Same stage-tagged wrapper around `apps.documents.Document` as
    `apps.fieldissues.FieldIssueEvidence` — reused shape, not a shared
    abstraction, matching this codebase's existing per-domain evidence
    wrapper convention (`ReceiptEvidence`, etc.)."""

    class Stage(models.TextChoices):
        BEFORE = "before", "Antes"
        DURING = "during", "Durante"
        AFTER = "after", "Después"

    session = models.ForeignKey(TrainingSession, on_delete=models.CASCADE, related_name="evidence")
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")
    stage = models.CharField(max_length=20, choices=Stage.choices)
    caption = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.get_stage_display()} — {self.session}"
