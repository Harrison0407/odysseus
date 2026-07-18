from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class WorkflowStage(BaseModel):
    """One stage of the end-to-end chain in spec section 9 (Requirement ->
    ... -> Reconciled and Closed). Configurable per organization/process,
    not hard-coded."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="workflow_stages")
    name = models.CharField(max_length=150)
    code = models.SlugField(max_length=60)
    sequence = models.PositiveIntegerField(default=1)
    department = models.ForeignKey("accounts.Department", on_delete=models.SET_NULL, null=True, blank=True, related_name="stages")

    class Meta:
        unique_together = [("organization", "code")]
        ordering = ["sequence"]

    def __str__(self):
        return self.name


class ServiceLevelTarget(BaseModel):
    stage = models.ForeignKey(WorkflowStage, on_delete=models.CASCADE, related_name="sla_targets")
    target_hours = models.PositiveIntegerField()
    escalation_department = models.ForeignKey(
        "accounts.Department", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )


class StageAssignment(BaseModel):
    """Who owns a given record at a given stage, with every role the spec
    requires (section 8): primary, backup, preparer, reviewer, approver,
    observer, escalation."""

    content_type = models.ForeignKey("contenttypes.ContentType", on_delete=models.CASCADE)
    object_id = models.UUIDField()
    stage = models.ForeignKey(WorkflowStage, on_delete=models.PROTECT, related_name="assignments")

    primary_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    backup_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    preparer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    observers = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="+")
    escalation_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    due_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.stage} for {self.content_type} {self.object_id}"


class HandoffStatus(models.TextChoices):
    NOT_READY = "not_ready", "No listo"
    READY_FOR_SUBMISSION = "ready_for_submission", "Listo para enviar"
    SUBMITTED = "submitted", "Enviado"
    ACCEPTED = "accepted", "Aceptado"
    REJECTED = "rejected", "Rechazado"
    RETURNED_FOR_CORRECTION = "returned_for_correction", "Devuelto para corrección"
    SUPERSEDED = "superseded", "Reemplazado"
    CANCELLED = "cancelled", "Cancelado"


class Handoff(BaseModel):
    """Responsibility transfers only when: (1) the outgoing owner submits,
    (2) mandatory controls pass, (3) the incoming owner explicitly
    accepts — never merely by changing a status (spec section 8)."""

    content_type = models.ForeignKey("contenttypes.ContentType", on_delete=models.CASCADE)
    object_id = models.UUIDField()

    from_department = models.ForeignKey(
        "accounts.Department", on_delete=models.SET_NULL, null=True, blank=True, related_name="handoffs_out"
    )
    from_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="handoffs_sent"
    )
    to_department = models.ForeignKey(
        "accounts.Department", on_delete=models.SET_NULL, null=True, blank=True, related_name="handoffs_in"
    )
    to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="handoffs_received"
    )

    status = models.CharField(max_length=30, choices=HandoffStatus.choices, default=HandoffStatus.NOT_READY)
    version = models.PositiveIntegerField(default=1)

    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    rejection_or_correction_reason = models.TextField(blank=True)
    covered_lines_note = models.TextField(blank=True, help_text="Free-text summary of which lines/quantities this handoff covers.")

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id"])]
        ordering = ["-created_at"]

    def __str__(self):
        return f"Handoff {self.from_department}->{self.to_department} ({self.status})"


class HandoffChecklist(BaseModel):
    handoff = models.ForeignKey(Handoff, on_delete=models.CASCADE, related_name="checklist_items")
    question = models.CharField(max_length=255)
    answer = models.CharField(max_length=20, choices=[("yes", "Sí"), ("no", "No"), ("na", "N/A")], blank=True)
    is_warning = models.BooleanField(default=False)
    is_critical = models.BooleanField(default=False)


class HandoffEvidence(BaseModel):
    handoff = models.ForeignKey(Handoff, on_delete=models.CASCADE, related_name="evidence")
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")


class HandoffDecision(BaseModel):
    handoff = models.ForeignKey(Handoff, on_delete=models.CASCADE, related_name="decisions")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    decision = models.CharField(max_length=30, choices=[
        ("accepted", "Aceptado"), ("rejected", "Rechazado"), ("returned", "Devuelto para corrección"),
    ])
    comment = models.TextField(blank=True)
    decided_at = models.DateTimeField(auto_now_add=True)
