from django.conf import settings
from django.db import models

from apps.core.models import BaseModel, Severity


class MaterialRequest(BaseModel):
    """Spec section 21. Supports the emergency/offline-origin path (a PDF
    or WhatsApp-originated request) as an explicitly labeled exception that
    still goes through the normal approval/audit flow — never a parallel
    undocumented path."""

    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="material_requests")
    project = models.ForeignKey("projects.Project", on_delete=models.PROTECT, related_name="material_requests")
    building = models.ForeignKey("projects.Building", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    floor = models.ForeignKey("projects.Floor", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    unit = models.ForeignKey("projects.Unit", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    area = models.ForeignKey(
        "projects.Area", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Common-area destination, when the request is not for a specific unit.",
    )

    class Priority(models.TextChoices):
        NORMAL = "normal", "Normal"
        URGENT = "urgent", "Urgente"
        EMERGENCY = "emergency", "Emergencia"

    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.NORMAL)
    needed_by_date = models.DateField(null=True, blank=True)
    purpose = models.TextField(blank=True)

    is_emergency_exception = models.BooleanField(
        default=False, help_text="True if this regularizes a PDF/WhatsApp-originated emergency request."
    )
    origin_evidence = models.ForeignKey(
        "documents.Document", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Enviada"
        APPROVED = "approved", "Aprobada"
        REJECTED = "rejected", "Rechazada"
        RESERVED = "reserved", "Reservada"
        PICKED = "picked", "Recogida (picking)"
        DISPATCHED = "dispatched", "Despachada"
        PARTIALLY_DELIVERED = "partially_delivered", "Entregada parcialmente"
        DELIVERED = "delivered", "Entregada"
        CLOSED = "closed", "Cerrada"

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED)

    def __str__(self):
        return f"Solicitud {self.id} — {self.project}"


class MaterialRequestLine(BaseModel):
    request = models.ForeignKey(MaterialRequest, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("items.Item", on_delete=models.PROTECT, related_name="+")
    quantity_requested = models.DecimalField(max_digits=14, decimal_places=3)
    quantity_approved = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quantity_reserved = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_dispatched = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_delivered = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_returned = models.DecimalField(max_digits=14, decimal_places=3, default=0)

    def __str__(self):
        return f"{self.request}: {self.item} x{self.quantity_requested}"


class RequestApproval(BaseModel):
    request = models.ForeignKey(MaterialRequest, on_delete=models.CASCADE, related_name="approvals")
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    decision = models.CharField(max_length=20, choices=[("approved", "Aprobada"), ("rejected", "Rechazada")])
    decided_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(blank=True)


class PickList(BaseModel):
    request = models.ForeignKey(MaterialRequest, on_delete=models.CASCADE, related_name="pick_lists")
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    def __str__(self):
        return f"Pick list {self.id} — {self.request}"


class Dispatch(BaseModel):
    pick_list = models.ForeignKey(PickList, on_delete=models.CASCADE, related_name="dispatches")
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="dispatches_verified"
    )
    delivered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="dispatches_delivered"
    )
    dispatched_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Despacho {self.id}"


class DispatchLine(BaseModel):
    dispatch = models.ForeignKey(Dispatch, on_delete=models.CASCADE, related_name="lines")
    request_line = models.ForeignKey(MaterialRequestLine, on_delete=models.PROTECT, related_name="dispatch_lines")
    lot = models.ForeignKey("inventory.InventoryLot", on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    photo_document = models.ForeignKey(
        "documents.Document", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )


class Delivery(BaseModel):
    """Spec: delivery must remain linked to the ledger-based inventory
    source of truth, and support partial/multi-trip delivery without
    falsely marking the whole request delivered — see
    `apps.requests.services.record_delivery_line` for the quantity
    invariants this model's lines are subject to."""

    dispatch = models.OneToOneField(Dispatch, on_delete=models.CASCADE, related_name="delivery")
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    accepted = models.BooleanField(null=True, blank=True)
    rejected_quantity_note = models.TextField(blank=True)
    delivered_at = models.DateTimeField(
        null=True, blank=True, help_text="When the delivery was actually recorded as completed at site, "
        "distinct from `Dispatch.dispatched_at` (warehouse release) and from this row's own creation time."
    )
    delivery_location_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Entrega — {self.dispatch}"


class DeliveryLine(BaseModel):
    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name="lines")
    dispatch_line = models.ForeignKey(DispatchLine, on_delete=models.PROTECT, related_name="+")
    quantity_accepted = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_rejected = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_damaged = models.DecimalField(
        max_digits=14, decimal_places=3, default=0,
        help_text="Damaged-on-delivery quantity — a subset of quantity_rejected, tracked separately so "
        "damage (a claim/quarantine concern) is never conflated with a plain refusal.",
    )

    def __str__(self):
        return f"{self.dispatch_line.request_line.item} x{self.dispatch_line.quantity} — {self.delivery}"


class ProjectReceipt(BaseModel):
    """Miguel/project confirms actual destination, damage, missing items,
    wrong material (spec section 22). Delivered != installed != inspected
    != accepted — all tracked distinctly."""

    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name="project_receipts")
    confirmed_destination_building = models.ForeignKey(
        "projects.Building", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    confirmed_destination_unit = models.ForeignKey(
        "projects.Unit", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    confirmed_destination_area = models.ForeignKey(
        "projects.Area", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    site_damage_reported = models.BooleanField(default=False)
    missing_items_reported = models.BooleanField(default=False)
    wrong_material_reported = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]


class Return(BaseModel):
    delivery_line = models.ForeignKey(DeliveryLine, on_delete=models.CASCADE, related_name="returns")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    reason = models.CharField(max_length=255)
    returned_to_location = models.ForeignKey("inventory.WarehouseLocation", on_delete=models.PROTECT, related_name="+")


class Transfer(BaseModel):
    lot = models.ForeignKey("inventory.InventoryLot", on_delete=models.CASCADE, related_name="transfers")
    from_location = models.ForeignKey("inventory.WarehouseLocation", on_delete=models.PROTECT, related_name="transfers_out")
    to_location = models.ForeignKey("inventory.WarehouseLocation", on_delete=models.PROTECT, related_name="transfers_in")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    reason = models.CharField(max_length=255, blank=True)


class DestinationReassignment(BaseModel):
    """Spec section 21: material bought for one building used in another
    requires authorization and preserves both intended and actual
    destination (the fixture's Palmera/Arena example)."""

    lot = models.ForeignKey("inventory.InventoryLot", on_delete=models.CASCADE, related_name="reassignments")
    original_building = models.ForeignKey("projects.Building", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    requested_building = models.ForeignKey("projects.Building", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    reason = models.TextField()
    authorized_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    authorized_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Reasignación {self.original_building} -> {self.requested_building}"


class InstallationRecord(BaseModel):
    """Traceable to project/building/floor/unit-or-area (all via
    `project_receipt`, since `Unit.floor` already derives floor — no
    duplicate location fields needed here), product/delivered quantity/
    delivery record (via `project_receipt.delivery`), inventory movement
    (via `apps.requests.services.create_installation_record`, which posts
    an `INSTALLATION_CONSUMPTION` movement), installer, evidence, and
    responsible owner (`installed_by` / `supervisor_confirmed_by`)."""

    project_receipt = models.ForeignKey(ProjectReceipt, on_delete=models.CASCADE, related_name="installation_records")
    delivery_line = models.ForeignKey(
        DeliveryLine, on_delete=models.PROTECT, related_name="installation_records", null=True, blank=True,
        help_text="The specific delivered product/quantity this record is for — completes the "
        "project/building/floor/unit/product/delivered-quantity/delivery-record traceability chain.",
    )

    assigned_installer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Installer or installation-team lead assigned/scheduled for this work.",
    )
    scheduled_date = models.DateField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)

    quantity_installed = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_not_used = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_damaged = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    missing_components_note = models.TextField(blank=True)

    is_complete = models.BooleanField(
        default=False, help_text="False while work is still in progress or explicitly left incomplete."
    )
    requires_rework = models.BooleanField(default=False)
    observations = models.TextField(blank=True)

    installed_at = models.DateTimeField(null=True, blank=True, help_text="Completion timestamp.")
    installed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    installer_acknowledged_at = models.DateTimeField(null=True, blank=True)
    supervisor_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    supervisor_confirmed_at = models.DateTimeField(null=True, blank=True)

    photo_document = models.ForeignKey(
        "documents.Document", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Primary photo, kept for backward compatibility — use the generic apps.audit.Attachment "
        "(content_type/object_id) for any additional evidence.",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Instalación {self.id} — {self.project_receipt}"


class InspectionRecord(BaseModel):
    """Each inspection/correction cycle is its own immutable row, chained
    via `previous_inspection` — a reinspection never overwrites the
    failed history it followed (spec requirement)."""

    class Result(models.TextChoices):
        PASS = "pass", "Aprobado"
        CONDITIONAL_PASS = "conditional_pass", "Aprobado condicionado"
        FAIL = "fail", "Rechazado"

    installation = models.ForeignKey(InstallationRecord, on_delete=models.CASCADE, related_name="inspections")
    inspector = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    inspection_date = models.DateField(null=True, blank=True)
    inspected_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)

    result = models.CharField(max_length=20, choices=Result.choices, null=True, blank=True)
    passed = models.BooleanField(
        null=True, blank=True,
        help_text="Kept in sync with `result` (PASS/CONDITIONAL_PASS -> True, FAIL -> False) because "
        "apps.workflow.gates.evaluate_inspection_to_acceptance depends on this exact field — never "
        "duplicate that check, only keep this derived value correct.",
    )
    notes = models.TextField(blank=True)

    previous_inspection = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="reinspections"
    )
    technical_sign_off_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    technical_sign_off_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Inspección {self.id} — {self.installation} ({self.result})"


class PunchListItem(BaseModel):
    """A single defect from an inspection. Critical/blocking items must
    prevent final acceptance until closed or an authorized gate override
    is used (enforced in apps.workflow.gates.evaluate_inspection_to_acceptance,
    not duplicated here)."""

    class Status(models.TextChoices):
        OPEN = "open", "Abierto"
        CLOSED = "closed", "Cerrado"

    inspection = models.ForeignKey(InspectionRecord, on_delete=models.CASCADE, related_name="punch_list_items")
    description = models.TextField()
    severity = models.CharField(max_length=20, choices=Severity.choices, default=Severity.WARNING)
    is_blocking = models.BooleanField(
        default=True, help_text="Critical defects default to blocking; a non-blocking (informational/minor) "
        "item can be created for tracking without preventing acceptance."
    )
    responsible_department = models.ForeignKey(
        "accounts.Department", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    responsible_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    correction_deadline = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    resolution_notes = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-is_blocking", "correction_deadline"]

    def __str__(self):
        return f"{self.get_status_display()}: {self.description[:50]}"


class AcceptanceRecord(BaseModel):
    """Created only on a successful `inspection_to_acceptance` handoff
    acceptance (see apps.requests.services.record_final_acceptance) —
    rejection and return-for-correction at this stage are handled
    entirely by the existing generic Handoff reject/return flow, never
    duplicated here."""

    class Decision(models.TextChoices):
        ACCEPTED = "accepted", "Aceptado"
        CONDITIONAL = "conditional", "Aceptado condicionado"

    installation = models.OneToOneField(InstallationRecord, on_delete=models.CASCADE, related_name="acceptance")
    decision = models.CharField(max_length=20, choices=Decision.choices, default=Decision.ACCEPTED)
    conditions_note = models.TextField(blank=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    accepted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_decision_display()} — {self.installation}"
