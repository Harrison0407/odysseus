import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel


def generate_share_token():
    return secrets.token_urlsafe(32)


class ReportVersion(BaseModel):
    """A generated report snapshot (dossier, matching matrix, discrepancy
    report, landed-cost report, etc. — spec section 29)."""

    class ReportType(models.TextChoices):
        SHIPMENT_DOSSIER = "shipment_dossier", "Expediente completo de embarque"
        MATCHING_MATRIX = "matching_matrix", "Matriz de coincidencias"
        DISCREPANCY_REPORT = "discrepancy_report", "Reporte de discrepancias"
        EXPECTED_RECEIVING_PACKET = "expected_receiving_packet", "Paquete esperado de recepción"
        ACTUAL_RECEIVING_REPORT = "actual_receiving_report", "Reporte real de recepción"
        CONTAINER_CLOSURE_ACT = "container_closure_act", "Acta de cierre de contenedor"
        LANDED_COST_REPORT = "landed_cost_report", "Reporte de costo de importación"
        INVENTORY_MOVEMENT_HISTORY = "inventory_movement_history", "Historial de movimientos de inventario"
        CURRENT_INVENTORY = "current_inventory", "Inventario actual"
        KIT_COMPLETENESS = "kit_completeness", "Completitud de kits"
        PROJECT_REQUEST_HISTORY = "project_request_history", "Historial de solicitudes de proyecto"
        DESTINATION_REASSIGNMENT_REPORT = "destination_reassignment_report", "Reporte de reasignación de destino"
        INSTALLATION_ACCEPTANCE_HISTORY = "installation_acceptance_history", "Historial de instalación/aceptación"
        CLAIM_PACKAGE = "claim_package", "Paquete de reclamo"
        CONFOTUR_RECONCILIATION = "confotur_reconciliation", "Conciliación CONFOTUR"
        RESPONSIBILITY_HANDOFF_TIMELINE = "responsibility_handoff_timeline", "Línea de tiempo de responsabilidad"
        FULL_TRACEABILITY_TIMELINE = "full_traceability_timeline", "Línea de tiempo de trazabilidad completa"
        HTML_SNAPSHOT = "html_snapshot", "Instantánea HTML autónoma"

    report_type = models.CharField(max_length=50, choices=ReportType.choices)
    content_type = models.ForeignKey(
        "contenttypes.ContentType", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    object_id = models.UUIDField(null=True, blank=True)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    generated_at = models.DateTimeField(auto_now_add=True)
    rendered_html_document = models.ForeignKey(
        "documents.Document", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    def __str__(self):
        return f"{self.get_report_type_display()} @ {self.generated_at:%Y-%m-%d}"


class SecureShareLink(BaseModel):
    """Revocable tokenized read-only URL (spec section 29): random
    non-sequential token, expiration, revocation, scoped, access-logged."""

    token = models.CharField(max_length=64, unique=True, default=generate_share_token, editable=False)
    report_version = models.ForeignKey(ReportVersion, on_delete=models.CASCADE, related_name="share_links")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_active(self):
        if self.revoked_at:
            return False
        if self.expires_at and self.expires_at < timezone.now():
            return False
        return True

    def __str__(self):
        return f"Share link {self.token[:8]}..."


class ShareSnapshot(BaseModel):
    share_link = models.ForeignKey(SecureShareLink, on_delete=models.CASCADE, related_name="access_log")
    accessed_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
