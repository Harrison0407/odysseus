from django.db import models

from apps.core.models import BaseModel

# ---------------------------------------------------------------------------
# Unclassified Evidence Inbox (one-shot release, section "Unclassified
# Evidence Inbox"). Needed for Lawson's historical photographs and any
# future field upload whose exact apartment/issue is not yet known.
#
# Provenance (uploader, timestamp, original filename, checksum) is
# never touched again after upload — it lives entirely on the wrapped
# `apps.documents.Document`/`DocumentVersion` (the same immutable,
# SHA-256-hashed, duplicate-detected mechanism every other upload in
# this system already uses). Classification is a separate, generic,
# reassignable pointer (`EvidenceClassification`) layered on top —
# never a modification of the original upload record.
# ---------------------------------------------------------------------------


class UnclassifiedEvidence(BaseModel):
    class ClassificationStatus(models.TextChoices):
        UNCLASSIFIED = "unclassified", "Sin clasificar"
        PARTIALLY_CLASSIFIED = "partially_classified", "Parcialmente clasificado"
        CLASSIFIED = "classified", "Clasificado"

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="unclassified_evidence")
    project = models.ForeignKey("projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    building = models.ForeignKey("projects.Building", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")
    date_taken = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    classification_status = models.CharField(max_length=30, choices=ClassificationStatus.choices, default=ClassificationStatus.UNCLASSIFIED)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "unclassified evidence"

    def __str__(self):
        return f"Evidencia {self.document.title} ({self.get_classification_status_display()})"


class EvidenceClassification(BaseModel):
    """Generic, reassignable classification pointer (same
    content_type/object_id shape as `Attachment`/`AuditEvent`, ADR-004)
    — evidence may be classified to a Building/Floor/Unit/Walkthrough/
    WalkthroughItem/TrainingSession/FieldIssue/Item/Supplier/
    PurchaseOrderLine/Shipment/Container/InstallationRecord/
    InspectionRecord. Reassignment never deletes the prior
    classification — it is marked inactive and linked via
    `superseded_by`, exactly like `OrderLineAllocation` reassignment."""

    evidence = models.ForeignKey(UnclassifiedEvidence, on_delete=models.CASCADE, related_name="classifications")
    content_type = models.ForeignKey("contenttypes.ContentType", on_delete=models.CASCADE, related_name="+")
    object_id = models.UUIDField()
    is_active = models.BooleanField(default=True)
    reason = models.TextField(blank=True)
    superseded_by = models.OneToOneField(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="supersedes",
    )

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id"])]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.evidence} -> {self.content_type.model} {self.object_id}"

    @property
    def target(self):
        try:
            return self.content_type.get_object_for_this_type(pk=self.object_id)
        except self.content_type.model_class().DoesNotExist:
            return None
