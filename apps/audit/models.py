from django.conf import settings
from django.db import models

from apps.core.models import BaseModel
from apps.governance.models import Classification


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
        ROLE_ASSIGNMENT = "role_assignment", "Asignación de rol"
        CAPABILITY_GRANT = "capability_grant", "Otorgamiento de capacidad"
        DISCLOSURE_GRANT = "disclosure_grant", "Otorgamiento de divulgación"
        DISCLOSURE_REVOKED = "disclosure_revoked", "Revocación de divulgación"
        VISIBILITY_MODE_CHANGE = "visibility_mode_change", "Cambio de modo de visibilidad"
        PACKAGE_FREEZE = "package_freeze", "Congelamiento de paquete"
        CHANGE_REQUEST = "change_request", "Solicitud de cambio"
        VERIFICATION_ASSERTION = "verification_assertion", "Aserción de verificación"
        EVIDENCE_VERIFICATION = "evidence_verification", "Verificación de evidencia"
        PRIVILEGED_ACCESS_GRANTED = "privileged_access_granted", "Acceso privilegiado concedido"
        PRIVILEGED_ACCESS_DENIED = "privileged_access_denied", "Acceso privilegiado denegado"
        RISK_FLAG = "risk_flag", "Señal de riesgo"
        DERIVED_ARTIFACT = "derived_artifact", "Artefacto derivado"
        # Milestone 1 Increment 1 (apps.procurement_gates policy foundation,
        # Charter Section 10). GATE_POLICY_VERSION_WITHDRAWN is an
        # implementer gap-fill: the Charter's Section 10 table enumerates
        # every other policy-lifecycle action but is silent on withdrawal;
        # this follows the same naming/meaning pattern as the sibling
        # GATE_POLICY_VERSION_PUBLISHED action.
        GATE_POLICY_CREATED = "gate_policy_created", "Política de gate creada"
        GATE_POLICY_VERSION_PUBLISHED = "gate_policy_version_published", "Versión de política de gate publicada"
        GATE_POLICY_VERSION_WITHDRAWN = "gate_policy_version_withdrawn", "Versión de política de gate retirada"
        GATE_POLICY_PINNED = "gate_policy_pinned", "Política de gate fijada al paquete"
        GATE_PROGRESSION_EXEMPTION_GRANTED = (
            "gate_progression_exemption_granted", "Exención de progresión de gates otorgada"
        )
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


class EvidenceBundle(BaseModel):
    """Groups EvidenceItems for a configurable workflow checkpoint
    (inspection, receiving, production verification, QC, packing,
    shipment, installation, walkthrough, approval, ...). Uploading a
    file is never automatically verification — `status` only reaches
    VERIFIED once an independently-authorized user (never the uploader
    of the item(s) being verified) records that check."""

    class BundleType(models.TextChoices):
        PRODUCTION_VERIFICATION = "production_verification", "Verificación de producción"
        QUALITY_CONTROL = "quality_control", "Control de calidad"
        PACKING = "packing", "Empaque"
        RECEIVING = "receiving", "Recepción"
        SHIPMENT = "shipment", "Embarque"
        INSTALLATION = "installation", "Instalación"
        WALKTHROUGH = "walkthrough", "Recorrido"
        APPROVAL = "approval", "Aprobación"
        OTHER = "other", "Otro (configurable)"

    class Status(models.TextChoices):
        INCOMPLETE = "incomplete", "Incompleto"
        COMPLETE = "complete", "Completo (sin verificar)"
        VERIFIED = "verified", "Verificado"

    content_type = models.ForeignKey("contenttypes.ContentType", on_delete=models.CASCADE, related_name="+")
    object_id = models.UUIDField()

    bundle_type = models.CharField(max_length=40, choices=BundleType.choices, default=BundleType.OTHER)
    required_evidence_types = models.JSONField(default=list, blank=True)
    minimum_count = models.PositiveIntegerField(default=1)
    required_verifier_capability = models.CharField(max_length=60, blank=True, default="VERIFY_EVIDENCE")
    minimum_review_state = models.CharField(max_length=20, default="verified")
    requires_geolocation = models.BooleanField(default=False)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INCOMPLETE)
    classification = models.CharField(max_length=40, choices=Classification.choices, default=Classification.OPERATIONAL_SHARED, blank=True)

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id"])]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_bundle_type_display()} bundle ({self.get_status_display()})"


class EvidenceItem(BaseModel):
    """A single piece of evidence within a bundle — reuses the existing
    immutable `Document`/`DocumentVersion` mechanism for the actual file;
    this row adds capture provenance, review state, and classification.
    No confidence score is assigned — this system has no real basis for
    computing one."""

    class ReviewStatus(models.TextChoices):
        PENDING = "pending", "Pendiente"
        REVIEWED = "reviewed", "Revisado"
        VERIFIED = "verified", "Verificado"
        REJECTED = "rejected", "Rechazado"

    bundle = models.ForeignKey(EvidenceBundle, on_delete=models.CASCADE, related_name="items")
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")
    evidence_type = models.CharField(max_length=100, blank=True)
    capture_method = models.CharField(max_length=50, blank=True, help_text="photo, scan, manual, ...")
    captured_at = models.DateTimeField(null=True, blank=True)
    device_metadata = models.JSONField(default=dict, blank=True)
    location = models.CharField(max_length=255, blank=True, help_text="Free-text location, only when lawfully captured.")

    classification = models.CharField(max_length=40, choices=Classification.choices, default=Classification.OPERATIONAL_SHARED, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    review_status = models.CharField(max_length=20, choices=ReviewStatus.choices, default=ReviewStatus.PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    verification_basis = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.evidence_type or 'Evidencia'} — {self.bundle}"


class Notification(BaseModel):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    message = models.CharField(max_length=255)
    link_path = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
