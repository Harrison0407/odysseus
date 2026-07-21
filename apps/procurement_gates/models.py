"""Procurement gate policy, permanent assignment, and A1 execution core.

Increment 2 adds the immutable execution history required for A1 only.  It
deliberately contains no freeze, evidence-mapping, override, hold-cause, or
invalidation model and implements no A2-A6 operational behavior.
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


class GateState(models.TextChoices):
    NOT_STARTED = "NOT_STARTED", "Not started"
    BLOCKED = "BLOCKED", "Blocked"
    READY = "READY", "Ready"
    IN_REVIEW = "IN_REVIEW", "In review"
    PASSED = "PASSED", "Passed"
    FAILED = "FAILED", "Failed"
    INVALIDATED = "INVALIDATED", "Invalidated"
    EXPIRED = "EXPIRED", "Expired"
    OVERRIDDEN = "OVERRIDDEN", "Overridden"
    SUPERSEDED = "SUPERSEDED", "Superseded"


class ImmutableGateHistoryError(ValidationError):
    """A protected procurement-gates record was mutated outside its service."""


_controlled_policy_write = ContextVar("controlled_policy_write", default=False)
_controlled_version_write = ContextVar("controlled_version_write", default=False)
_controlled_assignment_write = ContextVar("controlled_assignment_write", default=False)
_controlled_execution_write = ContextVar("controlled_execution_write", default=False)
_controlled_projection_write = ContextVar("controlled_projection_write", default=False)
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


@contextmanager
def _allow_controlled_execution_write():
    token = _controlled_execution_write.set(True)
    try:
        yield
    finally:
        _controlled_execution_write.reset(token)


@contextmanager
def _allow_controlled_projection_write():
    token = _controlled_projection_write.set(True)
    try:
        yield
    finally:
        _controlled_projection_write.reset(token)


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


class GateAttemptQuerySet(models.QuerySet):
    _ACTOR_FIELDS = {
        "opened_by", "opened_by_id", "closed_by", "closed_by_id",
        "review_requested_by", "review_requested_by_id", "created_by", "created_by_id",
    }

    def update(self, **kwargs):
        deletion_null = (
            _related_object_deletion_depth.get()
            and set(kwargs).issubset(self._ACTOR_FIELDS)
            and all(value is None for value in kwargs.values())
        )
        if not _controlled_execution_write.get() and not deletion_null:
            raise ImmutableGateHistoryError("GateAttempt is service-controlled and immutable.")
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        if not _controlled_execution_write.get():
            raise ImmutableGateHistoryError("GateAttempt is service-controlled and immutable.")
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def delete(self):
        raise ImmutableGateHistoryError("GateAttempt cannot be deleted.")

    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False, update_conflicts=False,
                    update_fields=None, unique_fields=None):
        if not _controlled_execution_write.get():
            raise ImmutableGateHistoryError("GateAttempt may be created only by its lifecycle service.")
        return super().bulk_create(
            objs, batch_size=batch_size, ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts, update_fields=update_fields, unique_fields=unique_fields,
        )


class ImmutableExecutionQuerySet(models.QuerySet):
    _ACTOR_FIELDS = {
        "evaluated_by", "evaluated_by_id", "decided_by", "decided_by_id",
        "created_by", "created_by_id",
    }

    def update(self, **kwargs):
        deletion_null = (
            _related_object_deletion_depth.get()
            and set(kwargs).issubset(self._ACTOR_FIELDS)
            and all(value is None for value in kwargs.values())
        )
        if not _controlled_execution_write.get() and not deletion_null:
            raise ImmutableGateHistoryError("Gate execution history is immutable.")
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        if not _controlled_execution_write.get():
            raise ImmutableGateHistoryError("Gate execution history is immutable.")
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def delete(self):
        raise ImmutableGateHistoryError("Gate execution history cannot be deleted.")

    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False, update_conflicts=False,
                    update_fields=None, unique_fields=None):
        if not _controlled_execution_write.get():
            raise ImmutableGateHistoryError("Gate execution history may be created only by its service.")
        return super().bulk_create(
            objs, batch_size=batch_size, ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts, update_fields=update_fields, unique_fields=unique_fields,
        )


class PackageGateStateQuerySet(models.QuerySet):
    def update(self, **kwargs):
        deletion_null = (
            _related_object_deletion_depth.get()
            and set(kwargs).issubset({"created_by", "created_by_id"})
            and all(value is None for value in kwargs.values())
        )
        if not _controlled_projection_write.get() and not deletion_null:
            raise ImmutableGateHistoryError("PackageGateState is a service-controlled projection.")
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        if not _controlled_projection_write.get():
            raise ImmutableGateHistoryError("PackageGateState is a service-controlled projection.")
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False, update_conflicts=False,
                    update_fields=None, unique_fields=None):
        if not _controlled_projection_write.get():
            raise ImmutableGateHistoryError("PackageGateState is a service-controlled projection.")
        return super().bulk_create(
            objs, batch_size=batch_size, ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts, update_fields=update_fields, unique_fields=unique_fields,
        )

    def delete(self):
        if not _controlled_projection_write.get():
            raise ImmutableGateHistoryError("PackageGateState is a service-controlled projection.")
        return super().delete()


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


class GateAttempt(BaseModel):
    package = models.ForeignKey(
        "procurement.ProcurementPackage", on_delete=models.PROTECT, related_name="gate_attempts",
    )
    assignment = models.ForeignKey(
        PackagePolicyAssignment, on_delete=models.PROTECT, related_name="gate_attempts",
    )
    policy_version = models.ForeignKey(
        GatePolicyVersion, on_delete=models.PROTECT, related_name="gate_attempts",
    )
    gate_code = models.CharField(max_length=2, choices=[(code, code) for code in GATE_CODES])
    attempt_number = models.PositiveIntegerField()
    attempt_creation_capability = models.CharField(max_length=60)
    decision_capability = models.CharField(max_length=60)
    opened_at = models.DateTimeField()
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    closure_code = models.CharField(max_length=20, blank=True, default="")
    idempotency_key = models.CharField(max_length=128, unique=True)
    request_fingerprint = models.CharField(max_length=64)
    review_requested_at = models.DateTimeField(null=True, blank=True)
    review_requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    review_idempotency_key = models.CharField(max_length=128, blank=True, default="")
    review_evaluation = models.ForeignKey(
        "GateEvaluation", on_delete=models.PROTECT, null=True, blank=True, related_name="review_requests",
    )

    objects = GateAttemptQuerySet.as_manager()

    class Meta:
        base_manager_name = "objects"
        ordering = ["package", "gate_code", "-attempt_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["package", "gate_code", "attempt_number"],
                name="unique_gate_attempt_number",
            ),
            models.UniqueConstraint(
                fields=["package", "gate_code"], condition=models.Q(closed_at__isnull=True),
                name="unique_open_gate_attempt",
            ),
            models.CheckConstraint(
                check=models.Q(closed_at__isnull=True, closure_code="")
                | models.Q(closed_at__isnull=False, closure_code__in=["PASSED", "FAILED"]),
                name="gate_attempt_closure_consistent",
            ),
            models.UniqueConstraint(
                fields=["review_idempotency_key"],
                condition=~models.Q(review_idempotency_key=""),
                name="unique_gate_review_idempotency_key",
            ),
        ]
        indexes = [models.Index(fields=["package", "gate_code", "closed_at"])]

    def __str__(self):
        return f"{self.package_id}:{self.gate_code}:{self.attempt_number}"

    def save(self, *args, **kwargs):
        if not _controlled_execution_write.get():
            raise ImmutableGateHistoryError("GateAttempt may be written only by its lifecycle service.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableGateHistoryError("GateAttempt cannot be deleted.")


class GateEvaluation(BaseModel):
    class Result(models.TextChoices):
        SATISFIED = "SATISFIED", "Satisfied"
        BLOCKED = "BLOCKED", "Blocked"

    attempt = models.ForeignKey(GateAttempt, on_delete=models.PROTECT, related_name="evaluations")
    policy_version = models.ForeignKey(
        GatePolicyVersion, on_delete=models.PROTECT, related_name="gate_evaluations",
    )
    evaluated_at = models.DateTimeField()
    evaluated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    requirement_results = models.JSONField(default=dict)
    blocker_codes = models.JSONField(default=list, blank=True)
    predecessor_valid = models.BooleanField()
    package_on_hold_at_evaluation = models.BooleanField()
    overall_ready = models.BooleanField()
    result_code = models.CharField(max_length=20, choices=Result.choices)
    safe_summary = models.JSONField(default=dict, blank=True)
    idempotency_key = models.CharField(max_length=128, unique=True)
    request_fingerprint = models.CharField(max_length=64)

    objects = ImmutableExecutionQuerySet.as_manager()

    class Meta:
        base_manager_name = "objects"
        ordering = ["attempt", "-evaluated_at", "-created_at"]
        indexes = [models.Index(fields=["attempt", "-evaluated_at"])]

    def __str__(self):
        return f"{self.attempt_id}:{self.result_code}"

    def save(self, *args, **kwargs):
        if not _controlled_execution_write.get():
            raise ImmutableGateHistoryError("GateEvaluation may be created only by its evaluation service.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableGateHistoryError("GateEvaluation cannot be deleted.")


class GateDecision(BaseModel):
    class Outcome(models.TextChoices):
        PASSED = "PASSED", "Passed"
        FAILED = "FAILED", "Failed"

    attempt = models.OneToOneField(GateAttempt, on_delete=models.PROTECT, related_name="decision")
    evaluation = models.ForeignKey(GateEvaluation, on_delete=models.PROTECT, related_name="decisions")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    decided_at = models.DateTimeField()
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    comment = models.TextField(blank=True, default="")
    prior_state = models.CharField(max_length=20, choices=GateState.choices)
    resulting_state = models.CharField(max_length=20, choices=GateState.choices)
    capability_code = models.CharField(max_length=60)
    idempotency_key = models.CharField(max_length=128, unique=True)
    request_fingerprint = models.CharField(max_length=64)

    objects = ImmutableExecutionQuerySet.as_manager()

    class Meta:
        base_manager_name = "objects"
        ordering = ["-decided_at"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(outcome="PASSED", resulting_state=GateState.PASSED)
                | models.Q(outcome="FAILED", resulting_state=GateState.FAILED),
                name="gate_decision_result_consistent",
            ),
            models.CheckConstraint(
                check=models.Q(outcome="PASSED") | ~models.Q(comment=""),
                name="failed_gate_decision_requires_comment",
            ),
        ]

    def __str__(self):
        return f"{self.attempt_id}:{self.outcome}"

    def save(self, *args, **kwargs):
        if not _controlled_execution_write.get():
            raise ImmutableGateHistoryError("GateDecision may be created only by its decision service.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableGateHistoryError("GateDecision cannot be deleted.")


class PackageGateState(BaseModel):
    package = models.ForeignKey(
        "procurement.ProcurementPackage", on_delete=models.CASCADE, related_name="gate_state_cache",
    )
    gate_code = models.CharField(max_length=2, choices=[(code, code) for code in GATE_CODES])
    state = models.CharField(max_length=20, choices=GateState.choices)
    is_exempt = models.BooleanField(default=False)
    last_recomputed_at = models.DateTimeField()

    objects = PackageGateStateQuerySet.as_manager()

    class Meta:
        base_manager_name = "objects"
        ordering = ["package", "gate_code"]
        constraints = [
            models.UniqueConstraint(fields=["package", "gate_code"], name="unique_package_gate_state"),
        ]

    def __str__(self):
        return f"{self.package_id}:{self.gate_code}:{self.state}"

    def save(self, *args, **kwargs):
        if not _controlled_projection_write.get():
            raise ImmutableGateHistoryError("PackageGateState is a service-controlled projection.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not _controlled_projection_write.get():
            raise ImmutableGateHistoryError("PackageGateState is a service-controlled projection.")
        return super().delete(*args, **kwargs)


@receiver(pre_delete, sender=GatePolicyVersion)
def _protect_terminal_policy_version_delete(sender, instance, **kwargs):
    if instance.status != GatePolicyVersion.Status.DRAFT:
        raise ImmutableGateHistoryError("Published or withdrawn policy versions cannot be deleted.")


@receiver(pre_delete, sender=PackagePolicyAssignment)
def _protect_assignment_delete(sender, instance, **kwargs):
    raise ImmutableGateHistoryError("PackagePolicyAssignment cannot be deleted.")


@receiver(pre_delete, sender=GateAttempt)
@receiver(pre_delete, sender=GateEvaluation)
@receiver(pre_delete, sender=GateDecision)
def _protect_execution_history_delete(sender, instance, **kwargs):
    raise ImmutableGateHistoryError(f"{sender.__name__} cannot be deleted.")


@receiver(pre_delete, sender=settings.AUTH_USER_MODEL)
@receiver(pre_delete, sender="accounts.Organization")
def _begin_related_object_deletion(sender, instance, **kwargs):
    _related_object_deletion_depth.set(_related_object_deletion_depth.get() + 1)


@receiver(post_delete, sender=settings.AUTH_USER_MODEL)
@receiver(post_delete, sender="accounts.Organization")
def _end_related_object_deletion(sender, instance, **kwargs):
    _related_object_deletion_depth.set(max(0, _related_object_deletion_depth.get() - 1))
