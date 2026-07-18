from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class AuditEvent(BaseModel):
    """Append-only audit trail for every material business action (spec
    section 32). Never edited or deleted by application code."""

    class Action(models.TextChoices):
        DOCUMENT_UPLOAD = "document_upload", "Carga de documento"
        DOCUMENT_REPLACEMENT = "document_replacement", "Reemplazo de documento"
        IMPORT_CONFIRMATION = "import_confirmation", "Confirmación de importación"
        MATCH_CONFIRMATION = "match_confirmation", "Confirmación de coincidencia"
        MATCH_REVERSAL = "match_reversal", "Reversión de coincidencia"
        WAIVER = "waiver", "Dispensa"
        HANDOFF = "handoff", "Entrega de responsabilidad"
        PAYMENT_APPROVAL = "payment_approval", "Aprobación de pago"
        ORIGIN_RELEASE = "origin_release", "Liberación de origen"
        RECEIVING_PLAN_APPROVAL = "receiving_plan_approval", "Aprobación de plan de recepción"
        OPERATIONAL_RELEASE = "operational_release", "Liberación operativa"
        RECEIPT_POSTING = "receipt_posting", "Registro de recepción"
        QUARANTINE = "quarantine", "Cuarentena"
        QUARANTINE_RELEASE = "quarantine_release", "Liberación de cuarentena"
        INVENTORY_MOVEMENT = "inventory_movement", "Movimiento de inventario"
        ADJUSTMENT = "adjustment", "Ajuste"
        DISPATCH = "dispatch", "Despacho"
        PROJECT_ACCEPTANCE = "project_acceptance", "Aceptación de proyecto"
        DESTINATION_REASSIGNMENT = "destination_reassignment", "Reasignación de destino"
        INSTALLATION = "installation", "Instalación"
        ACCEPTANCE = "acceptance", "Aceptación"
        COST_FINALIZATION = "cost_finalization", "Finalización de costo"
        CLAIM_ACTION = "claim_action", "Acción de reclamo"
        SHARE_LINK_CREATED = "share_link_created", "Enlace compartido creado"
        SHARE_LINK_REVOKED = "share_link_revoked", "Enlace compartido revocado"
        OTHER = "other", "Otro"

    action = models.CharField(max_length=40, choices=Action.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_events")
    content_type = models.ForeignKey(
        "contenttypes.ContentType", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    object_id = models.UUIDField(null=True, blank=True)
    summary = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.get_action_display()} by {self.actor} @ {self.occurred_at:%Y-%m-%d %H:%M}"


class Comment(BaseModel):
    content_type = models.ForeignKey("contenttypes.ContentType", on_delete=models.CASCADE)
    object_id = models.UUIDField()
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    body = models.TextField()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]


class Attachment(BaseModel):
    content_type = models.ForeignKey("contenttypes.ContentType", on_delete=models.CASCADE)
    object_id = models.UUIDField()
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id"])]


class Notification(BaseModel):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    message = models.CharField(max_length=255)
    link_path = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
