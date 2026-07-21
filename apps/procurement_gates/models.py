"""Procurement gate policy and permanent package-assignment foundation.

This module deliberately contains no A1-A6 execution model.  The write
guards below protect the Increment 1 historical/configuration records from
instance, queryset, bulk, delete, and cascade bypasses.
"""

from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import post_delete, pre_delete
from django.dispatch import receiver

from apps.core.models import BaseModel

GATE_CODES = ("A1", "A2", "A3", "A4", "A5", "A6")


class ImmutableGateHistoryError(ValidationError):
    """A protected procurement-gates record was mutated outside its service."""


_controlled_policy_write = ContextVar("controlled_policy_write", default=False)
_controlled_version_write = ContextVar("controlled_version_write", default=False)
_controlled_assignment_write = ContextVar("controlled_assignment_write", default=False)
_related_object_deletion_depth = ContextVar("related_object_deletion_depth", default=0)


@contextmanager
def _allow_controlled_policy_write():
    token = _controlled_policy_write.set(True)
    try:
        yield
    finally:
        _controlled_policy_write.reset(token)


@contextmanager
def _allow_controlled_version_write():
    token = _controlled_version_write.set(True)
    try:
        yield
    finally:
        _controlled_version_write.reset(token)


@contextmanager
def _allow_controlled_assignment_write():
    token = _controlled_assignment_write.set(True)
    try:
        yield
    finally:
        _controlled_assignment_write.reset(token)


class GatePolicyQuerySet(models.QuerySet):
    _CONTROLLED_FIELDS = {"organization", "organization_id", "is_canonical_default"}

    def update(self, **kwargs):
        deletion_null = (
            _related_object_deletion_depth.get()
            and set(kwargs).issubset({"organization", "organization_id"})
            and all(value is None for value in kwargs.values())
        )
        if self._CONTROLLED_FIELDS.intersection(kwargs) and not deletion_null:
            raise ImmutableGateHistoryError("Canonical policy identity is service-controlled.")
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        if self._CONTROLLED_FIELDS.intersection(fields):
            raise ImmutableGateHistoryError("Canonical policy identity is service-controlled.")
        return super().bulk_update(objs, fields, batch_size=batch_size)


class GatePolicyVersionQuerySet(models.QuerySet):
    _PROTECTED_FIELDS = {
        "policy", "policy_id", "version_number", "status", "gate_schema",
        "published_at", "published_by", "published_by_id", "supersedes",
        "supersedes_id", "withdrawal_reason", "created_by", "created_by_id",
    }

    def update(self, **kwargs):
        deletion_null = (
            _related_object_deletion_depth.get()
            and set(kwargs).issubset({"published_by", "published_by_id", "created_by", "created_by_id"})
            and all(value is None for value in kwargs.values())
        )
        if self._PROTECTED_FIELDS.intersection(kwargs) and not deletion_null:
            raise ImmutableGateHistoryError("GatePolicyVersion protected fields are service-controlled.")
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        if self._PROTECTED_FIELDS.intersection(fields):
            raise ImmutableGateHistoryError("GatePolicyVersion protected fields are service-controlled.")
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def delete(self):
        if self.exclude(status=GatePolicyVersion.Status.DRAFT).exists():
            raise ImmutableGateHistoryError("Published or withdrawn policy versions cannot be deleted.")
        return super().delete()


class PackagePolicyAssignmentQuerySet(models.QuerySet):
    def update(self, **kwargs):
        deletion_null = (
            _related_object_deletion_depth.get()
            and set(kwargs).issubset({
                "pinned_by", "pinned_by_id", "exemption_granted_by",
                "exemption_granted_by_id", "created_by", "created_by_id",
            })
            and all(value is None for value in kwargs.values())
        )
        if deletion_null:
            return super().update(**kwargs)
        raise ImmutableGateHistoryError("PackagePolicyAssignment is immutable after creation.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ImmutableGateHistoryError("PackagePolicyAssignment is immutable after creation.")

    def delete(self):
        raise ImmutableGateHistoryError("PackagePolicyAssignment cannot be deleted.")


class GatePolicy(BaseModel):
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

    objects = GatePolicyQuerySet.as_manager()

    class Meta:
        base_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], name="unique_gate_policy_code_per_organization"),
            models.UniqueConstraint(
                fields=["is_canonical_default"], condition=models.Q(is_canonical_default=True),
                name="unique_canonical_gate_policy",
            ),
            models.CheckConstraint(
                check=models.Q(is_canonical_default=False) | models.Q(organization__isnull=True),
                name="canonical_gate_policy_must_be_platform_scoped",
            ),
        ]
        ordering = ["-created_at"]
        verbose_name_plural = "gate policies"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.is_canonical_default and self.organization_id is not None:
            raise ValidationError("An organization-owned GatePolicy can never be canonical.")
        if self._state.adding and self.is_canonical_default and not _controlled_policy_write.get():
            raise ImmutableGateHistoryError("Canonical policy identity is service-controlled.")
        if not self._state.adding and not _controlled_policy_write.get():
            original = type(self).objects.filter(pk=self.pk).values(
                "organization_id", "is_canonical_default"
            ).first()
            if original and (
                original["organization_id"] != self.organization_id
                or original["is_canonical_default"] != self.is_canonical_default
            ):
                raise ImmutableGateHistoryError("Canonical policy identity is service-controlled.")
        super().save(*args, **kwargs)


class GatePolicyVersion(BaseModel):
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

    objects = GatePolicyVersionQuerySet.as_manager()

    _IMMUTABLE_FIELDS = (
        "policy_id", "version_number", "status", "gate_schema", "published_at",
        "published_by_id", "supersedes_id", "withdrawal_reason", "created_by_id",
    )

    class Meta:
        base_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(fields=["policy", "version_number"], name="unique_gate_policy_version_number"),
        ]
        ordering = ["policy", "-version_number"]

    def __str__(self):
        return f"{self.policy_id} v{self.version_number} ({self.status})"

    def save(self, *args, **kwargs):
        if (
            self._state.adding
            and self.status != self.Status.DRAFT
            and not _controlled_version_write.get()
        ):
            raise ImmutableGateHistoryError(
                "A GatePolicyVersion must enter the lifecycle as DRAFT through its service."
            )
        if not self._state.adding and not _controlled_version_write.get():
            original = type(self).objects.filter(pk=self.pk).values(*self._IMMUTABLE_FIELDS).first()
            if original and any(original[field] != getattr(self, field) for field in self._IMMUTABLE_FIELDS):
                raise ImmutableGateHistoryError(
                    "GatePolicyVersion may be changed only through its authorized lifecycle service."
                )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != self.Status.DRAFT:
            raise ImmutableGateHistoryError("Published or withdrawn policy versions cannot be deleted.")
        return super().delete(*args, **kwargs)


class PackagePolicyAssignment(BaseModel):
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
    # Retained for migration compatibility only.  A false value never permits
    # a replacement assignment and this field is immutable after creation.
    is_active = models.BooleanField(default=True)
    gate_progression_exempt = models.BooleanField(default=False)
    exemption_reason = models.TextField(blank=True, default="")
    exemption_granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    exemption_granted_at = models.DateTimeField(null=True, blank=True)

    objects = PackagePolicyAssignmentQuerySet.as_manager()

    class Meta:
        base_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(fields=["package"], name="unique_package_policy_assignment"),
        ]
        ordering = ["-pinned_at"]

    def __str__(self):
        return f"{self.package_id} -> {self.policy_version_id}"

    def save(self, *args, **kwargs):
        if self._state.adding and not _controlled_assignment_write.get():
            raise ImmutableGateHistoryError(
                "PackagePolicyAssignment may be created only through its assignment service."
            )
        if not self._state.adding and not _controlled_assignment_write.get():
            raise ImmutableGateHistoryError("PackagePolicyAssignment is immutable after creation.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableGateHistoryError("PackagePolicyAssignment cannot be deleted.")


@receiver(pre_delete, sender=GatePolicyVersion)
def _protect_terminal_policy_version_delete(sender, instance, **kwargs):
    if instance.status != GatePolicyVersion.Status.DRAFT:
        raise ImmutableGateHistoryError("Published or withdrawn policy versions cannot be deleted.")


@receiver(pre_delete, sender=PackagePolicyAssignment)
def _protect_assignment_delete(sender, instance, **kwargs):
    raise ImmutableGateHistoryError("PackagePolicyAssignment cannot be deleted.")


@receiver(pre_delete, sender=settings.AUTH_USER_MODEL)
@receiver(pre_delete, sender="accounts.Organization")
def _begin_related_object_deletion(sender, instance, **kwargs):
    _related_object_deletion_depth.set(_related_object_deletion_depth.get() + 1)


@receiver(post_delete, sender=settings.AUTH_USER_MODEL)
@receiver(post_delete, sender="accounts.Organization")
def _end_related_object_deletion(sender, instance, **kwargs):
    _related_object_deletion_depth.set(max(0, _related_object_deletion_depth.get() - 1))
