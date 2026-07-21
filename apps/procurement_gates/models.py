"""Procurement Gate Policy and Package Assignment Foundation (Milestone 1,
Increment 1). Implements only the configuration/pinning layer defined by
docs/MILESTONE_1_PROCUREMENT_GATES_CHARTER.md Sections 1-4: GatePolicy,
GatePolicyVersion, PackagePolicyAssignment. Gate execution (GateAttempt,
GateEvaluation, GateDecision, overrides, freeze/change-control) is a
separate, not-yet-authorized increment -- nothing here evaluates or
records A1-A6 progression.

This app never imports or references apps.workflow -- the existing,
completed eight-gate Handoff engine is a distinct, untouched system
(Charter Section 0/Section 1).
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import BaseModel

GATE_CODES = ("A1", "A2", "A3", "A4", "A5", "A6")


class GatePolicy(BaseModel):
    """Stable identity for a named policy family (Charter Section 3.1).
    The actual, versioned gate_schema lives on GatePolicyVersion -- a
    GatePolicy row itself carries no schema."""

    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="gate_policies",
        help_text="NULL means platform-scoped. NULL is a necessary precondition for "
        "is_canonical_default but never itself the marker of canonical status.",
    )
    code = models.SlugField(max_length=80)
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(
        default=True,
        help_text="Soft-disables this policy for new pinnings only; never affects packages already pinned.",
    )
    is_canonical_default = models.BooleanField(
        default=False,
        help_text="Sole determinant of the canonical default policy family (Charter Section 3.3). "
        "Settable only where organization IS NULL.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], name="unique_gate_policy_code_per_organization"),
            models.UniqueConstraint(
                fields=["is_canonical_default"],
                condition=models.Q(is_canonical_default=True),
                name="unique_canonical_gate_policy",
            ),
        ]
        ordering = ["-created_at"]
        verbose_name_plural = "gate policies"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Defense in depth (Charter Section 3.3 item 2): an organization-owned
        # policy can never become canonical by any path, including direct
        # model manipulation. services.set_canonical_default is the primary
        # enforcement point; this guard is the backstop.
        if self.is_canonical_default and self.organization_id is not None:
            raise ValidationError("An organization-owned GatePolicy can never be canonical.")
        super().save(*args, **kwargs)


class GatePolicyVersion(BaseModel):
    """The actual, immutable-once-published, versioned gate_schema
    (Charter Section 3.1/3.2)."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        WITHDRAWN = "withdrawn", "Withdrawn"

    policy = models.ForeignKey(GatePolicy, on_delete=models.PROTECT, related_name="versions")
    version_number = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    gate_schema = models.JSONField(
        default=dict, blank=True,
        help_text="Exactly six top-level keys, A1-A6 (Charter Section 3.1/3.2). Immutable once published.",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    supersedes = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="superseded_by_versions",
    )
    withdrawal_reason = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["policy", "version_number"], name="unique_gate_policy_version_number"),
        ]
        ordering = ["policy", "-version_number"]

    def __str__(self):
        return f"{self.policy_id} v{self.version_number} ({self.status})"

    def save(self, *args, **kwargs):
        # Published/withdrawn rows are immutable on gate_schema/version_number/
        # policy (Charter Section 3.2). services.publish_policy_version and
        # services.withdraw_policy_version are the primary enforcement
        # points; this guard is the required backstop against any other
        # write path, including a direct model save().
        if self.pk is not None:
            try:
                original = type(self).objects.get(pk=self.pk)
            except type(self).DoesNotExist:
                original = None
            if original is not None and original.status != self.Status.DRAFT:
                for field_name in ("gate_schema", "version_number", "policy_id"):
                    if getattr(original, field_name) != getattr(self, field_name):
                        raise ValidationError(
                            f"GatePolicyVersion.{field_name} cannot be changed once status is {original.status!r}."
                        )
        super().save(*args, **kwargs)


class PackagePolicyAssignment(BaseModel):
    """Permanent pin of one ProcurementPackage to one published
    GatePolicyVersion for the life of its gate progression (Charter
    Section 3.4). No re-pinning, supersession, or reassignment mechanism
    exists in Milestone 1 -- there is deliberately no superseded_by
    field."""

    package = models.ForeignKey(
        "procurement.ProcurementPackage", on_delete=models.PROTECT, related_name="policy_assignments",
    )
    policy_version = models.ForeignKey(
        GatePolicyVersion, on_delete=models.PROTECT, related_name="package_assignments",
    )
    pinned_at = models.DateTimeField()
    pinned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="NULL for system-assigned pins (e.g. the existing-package migration) -- "
        "never disguised as a real approver.",
    )
    is_active = models.BooleanField(default=True)

    # Administrative exemption (Charter Section 4.4) -- not a historical
    # pass. Set only for closed/historical packages that are not expected
    # to progress through A1-A6.
    gate_progression_exempt = models.BooleanField(default=False)
    exemption_reason = models.TextField(blank=True, default="")
    exemption_granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    exemption_granted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["package"], condition=models.Q(is_active=True), name="unique_active_package_policy_assignment",
            ),
        ]
        ordering = ["-pinned_at"]

    def __str__(self):
        return f"{self.package_id} -> {self.policy_version_id}"
