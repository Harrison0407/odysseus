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


class GateDefinition(BaseModel):
    """A named, reusable transition rule between two stages (Gate Controls
    milestone). Readiness is never computed inline in a view or template —
    `apps.workflow.gates.evaluate_gate(gate_definition, target)` is the
    single place gate logic lives, keyed off `code` against a registry of
    evaluator functions.

    Required minimum transitions (spec): Purchasing→Finance,
    Finance→Logistics, Logistics→Receiving, Receiving→Warehouse,
    Warehouse→Project, Project Delivery→Installation,
    Installation→Inspection, Inspection→Acceptance.
    """

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="gate_definitions")
    code = models.SlugField(
        max_length=60, help_text='Matched against the apps.workflow.gates registry, e.g. "logistics_to_receiving".'
    )
    name = models.CharField(max_length=150)
    sequence = models.PositiveIntegerField(default=1)

    from_stage = models.ForeignKey(WorkflowStage, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    to_stage = models.ForeignKey(WorkflowStage, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    from_department = models.ForeignKey(
        "accounts.Department", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    to_department = models.ForeignKey(
        "accounts.Department", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    target_content_type = models.ForeignKey(
        "contenttypes.ContentType", on_delete=models.CASCADE, related_name="+",
        help_text="The model type this gate evaluates readiness for (e.g. Shipment, MaterialRequest, InstallationRecord).",
    )
    required_role_to_accept = models.ForeignKey(
        "accounts.Role", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="If set, only a user holding this role may accept a handoff created against this gate.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("organization", "code")]
        ordering = ["sequence"]

    def __str__(self):
        return self.name


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

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="handoffs", null=True, blank=True,
        help_text="Denormalized at creation time from the target object, same rationale as `project` below — "
        "every other view in this codebase scopes queries by organization and this keeps the inbox consistent. "
        "Always populated by apps.workflow.services.create_handoff(); nullable only to match this codebase's "
        "existing convention for denormalized cross-reference fields.",
    )
    gate_definition = models.ForeignKey(
        GateDefinition, on_delete=models.SET_NULL, null=True, blank=True, related_name="handoffs"
    )
    project = models.ForeignKey(
        "projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Denormalized at creation time from the target object (when it has a resolvable project) "
        "so the inbox can filter and enforce cross-project isolation without walking a different relation "
        "chain per target model type.",
    )

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
    supersedes = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="superseded_by",
        help_text="Set when this handoff is a corrected resubmission of a rejected/returned one — the prior "
        "row is marked SUPERSEDED and kept forever, never edited or deleted (core principle 4.4).",
    )

    readiness_ready = models.BooleanField(
        default=False, help_text="Result of the last gate evaluation at submission time."
    )
    readiness_snapshot = models.JSONField(
        default=dict, blank=True,
        help_text="Full structured GateResult (unmet requirements, discrepancies, missing docs, etc.) frozen "
        "at the moment of submission, so the reviewing party sees exactly what was true then.",
    )

    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    rejection_or_correction_reason = models.TextField(blank=True)
    covered_lines_note = models.TextField(blank=True, help_text="Free-text summary of which lines/quantities this handoff covers.")

    class Meta:
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
            models.Index(fields=["project"]),
            models.Index(fields=["to_department", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["content_type", "object_id", "gate_definition"],
                condition=models.Q(status__in=["not_ready", "ready_for_submission", "submitted"]),
                name="unique_active_handoff_per_target_gate",
            )
        ]
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


class GateOverride(BaseModel):
    """An authorized override of a blocked gate (spec: "any authorized
    override must require a permission, a written reason, actor identity,
    timestamp, before/after state, immutable audit record"). Never allows
    silently proceeding — creating this record is itself the audit trail,
    and it is never deleted or edited after creation."""

    gate_definition = models.ForeignKey(GateDefinition, on_delete=models.PROTECT, related_name="overrides")
    content_type = models.ForeignKey("contenttypes.ContentType", on_delete=models.CASCADE, related_name="+")
    object_id = models.UUIDField()
    handoff = models.ForeignKey(
        Handoff, on_delete=models.SET_NULL, null=True, blank=True, related_name="overrides_used"
    )

    overridden_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    reason = models.TextField()
    before_state = models.JSONField(default=dict, help_text="The blocking GateResult at the moment of override.")
    after_state = models.JSONField(default=dict, help_text="Handoff status/fields immediately after the override was applied.")

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id"])]
        ordering = ["-created_at"]

    def __str__(self):
        return f"Override de {self.gate_definition} por {self.overridden_by}"
