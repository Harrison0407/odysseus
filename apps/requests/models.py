from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


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
    dispatch = models.OneToOneField(Dispatch, on_delete=models.CASCADE, related_name="delivery")
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    accepted = models.BooleanField(null=True, blank=True)
    rejected_quantity_note = models.TextField(blank=True)

    def __str__(self):
        return f"Entrega — {self.dispatch}"


class DeliveryLine(BaseModel):
    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name="lines")
    dispatch_line = models.ForeignKey(DispatchLine, on_delete=models.PROTECT, related_name="+")
    quantity_accepted = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_rejected = models.DecimalField(max_digits=14, decimal_places=3, default=0)


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
    site_damage_reported = models.BooleanField(default=False)
    missing_items_reported = models.BooleanField(default=False)
    wrong_material_reported = models.BooleanField(default=False)
    notes = models.TextField(blank=True)


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
    project_receipt = models.ForeignKey(ProjectReceipt, on_delete=models.CASCADE, related_name="installation_records")
    quantity_installed = models.DecimalField(max_digits=14, decimal_places=3)
    installed_at = models.DateTimeField(null=True, blank=True)
    installed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    photo_document = models.ForeignKey(
        "documents.Document", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )


class InspectionRecord(BaseModel):
    installation = models.ForeignKey(InstallationRecord, on_delete=models.CASCADE, related_name="inspections")
    inspector = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    passed = models.BooleanField(null=True, blank=True)
    notes = models.TextField(blank=True)


class AcceptanceRecord(BaseModel):
    installation = models.OneToOneField(InstallationRecord, on_delete=models.CASCADE, related_name="acceptance")
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    accepted_at = models.DateTimeField(auto_now_add=True)
