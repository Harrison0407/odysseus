import secrets

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel

# ---------------------------------------------------------------------------
# QR labels and controlled scanning (spec section 12/17/18/21/22).
#
# The QR payload never encodes a raw object ID: it encodes an opaque,
# random `token` (same non-sequential/unguessable-token pattern as
# `apps.reports.models.SecureShareLink`) that only resolves to a real
# record, org, and URL server-side, after a scan — a scan itself never
# authorizes anything; every consequential action still goes through its
# own existing login/permission checks on the page the scan lands on.
# ---------------------------------------------------------------------------


def generate_label_token():
    return secrets.token_urlsafe(24)


class QRLabel(BaseModel):
    """One label for one entity (`content_type`/`object_id` — the same
    generic pointer pattern as `Attachment`/`AuditEvent`, ADR-004).
    `version` increments whenever a label is invalidated and replaced;
    the previous row is never deleted (core principle 4.4) — only
    marked `is_invalidated` and linked via `replaced_by`."""

    token = models.CharField(max_length=64, unique=True, default=generate_label_token, editable=False)
    content_type = models.ForeignKey("contenttypes.ContentType", on_delete=models.CASCADE, related_name="+")
    object_id = models.UUIDField()
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="qr_labels")
    human_label = models.CharField(max_length=255)
    entity_type_label = models.CharField(max_length=100)
    context_label = models.CharField(max_length=255, blank=True, help_text="Project/warehouse context shown on the label.")
    version = models.PositiveIntegerField(default=1)

    is_invalidated = models.BooleanField(default=False)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    invalidated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    invalidated_reason = models.TextField(blank=True)
    replaced_by = models.OneToOneField("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="replaces")

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"Etiqueta QR {self.human_label} (v{self.version})"


class QRLabelPrintEvent(BaseModel):
    """Reprint history — every time a label is printed (individually or
    as part of a batch), regardless of whether it's the first print or
    the tenth."""

    label = models.ForeignKey(QRLabel, on_delete=models.CASCADE, related_name="print_events")
    printed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    printed_at = models.DateTimeField(auto_now_add=True)
    quantity = models.PositiveIntegerField(default=1)
    is_batch = models.BooleanField(default=False)

    class Meta:
        ordering = ["-printed_at"]


class QRScanEvent(BaseModel):
    """Audit history for scans themselves — distinct from prints. Never
    the mechanism that authorizes anything; it only records that a scan
    happened, by whom (if authenticated), and what it resolved to."""

    label = models.ForeignKey(QRLabel, on_delete=models.CASCADE, related_name="scan_events")
    scanned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    scanned_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    was_cross_organization_denied = models.BooleanField(default=False)

    class Meta:
        ordering = ["-scanned_at"]
