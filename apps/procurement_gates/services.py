"""Authorized procurement-gate policy, assignment, and A1 services."""

from __future__ import annotations

import hashlib
import json

from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.governance.models import (
    ALL_CAPABILITY_CODES,
    CapabilityGrant,
    ChangeRequest,
    RiskFlag,
    RoleAssignment,
    VisibilityMode,
)
from apps.governance.services import AuthorizationDenied, has_capability, log_denied_attempt

from .models import (
    GATE_CODES,
    GateAttempt,
    GateDecision,
    GateEvaluation,
    GatePolicy,
    GatePolicyVersion,
    GateState,
    PackagePolicyAssignment,
    PackageGateState,
    _allow_controlled_assignment_write,
    _allow_controlled_execution_write,
    _allow_controlled_policy_write,
    _allow_controlled_projection_write,
    _allow_controlled_version_write,
)

PUBLISH_GATE_POLICY = "PUBLISH_GATE_POLICY"
CREATE_PROCUREMENT_GATE_ATTEMPT = "CREATE_PROCUREMENT_GATE_ATTEMPT"
ASSIGN_GATE_POLICY = "ASSIGN_GATE_POLICY"
EXEMPT_PACKAGE_FROM_PROCUREMENT_GATES = "EXEMPT_PACKAGE_FROM_PROCUREMENT_GATES"
VIEW_PROCUREMENT_GATE_STATE = "VIEW_PROCUREMENT_GATE_STATE"
EVALUATE_PROCUREMENT_GATE = "EVALUATE_PROCUREMENT_GATE"
REQUEST_PROCUREMENT_GATE_REVIEW = "REQUEST_PROCUREMENT_GATE_REVIEW"

_GATE_SCHEMA_ENTRY_FIELDS = {
    "evidence_requirement_codes",
    "decision_capability",
    "attempt_creation_capability",
    "overridable",
    "non_overridable_requirements",
    "override_satisfies_successor_predecessor",
}


class GatePolicyValidationError(Exception):
    pass


class GatePolicyStateError(Exception):
    pass


class PolicyResolutionAmbiguity(GatePolicyStateError):
    pass


class GateExecutionError(GatePolicyStateError):
    """Fail-closed A1 execution precondition or lifecycle error."""


class GateIdempotencyConflict(GateExecutionError):
    pass


class _AuthorizationLost(Exception):
    def __init__(self, capability_code):
        self.capability_code = capability_code


def _actor_holds_platform_policy_capability(actor) -> bool:
    if actor is None or not getattr(actor, "is_authenticated", False):
        return False
    today = timezone.now().date()
    grants = CapabilityGrant.objects.filter(
        user=actor,
        capability_code=PUBLISH_GATE_POLICY,
        is_active=True,
        organization__isnull=True,
        package__isnull=True,
        role_assignment__isnull=True,
    )
    return any(grant.is_currently_active(on_date=today) for grant in grants)


def _require_policy_administration_capability(policy: GatePolicy, actor, *, resource=None):
    if policy.organization_id is not None:
        authorized = has_capability(actor, PUBLISH_GATE_POLICY, organization=policy.organization)
    else:
        authorized = _actor_holds_platform_policy_capability(actor)
    if not authorized:
        log_denied_attempt(
            actor,
            PUBLISH_GATE_POLICY,
            organization=policy.organization,
            resource=resource if resource is not None else policy,
            required_capability=PUBLISH_GATE_POLICY,
            policy_id=policy.pk,
            policy_version_id=getattr(resource, "pk", None),
        )
        raise AuthorizationDenied("Not authorized to administer this gate policy.")


def validate_gate_schema(gate_schema) -> None:
    if not isinstance(gate_schema, dict):
        raise GatePolicyValidationError("gate_schema must be a JSON object.")
    provided_codes = set(gate_schema)
    missing = set(GATE_CODES) - provided_codes
    extra = provided_codes - set(GATE_CODES)
    if missing:
        raise GatePolicyValidationError(f"gate_schema is missing required gate keys: {sorted(missing)}")
    if extra:
        raise GatePolicyValidationError(f"gate_schema contains unrecognized gate keys: {sorted(extra)}")

    for gate_code in GATE_CODES:
        entry = gate_schema[gate_code]
        if not isinstance(entry, dict):
            raise GatePolicyValidationError(f"gate_schema[{gate_code}] must be an object.")
        entry_keys = set(entry)
        missing_fields = _GATE_SCHEMA_ENTRY_FIELDS - entry_keys
        extra_fields = entry_keys - _GATE_SCHEMA_ENTRY_FIELDS
        if missing_fields:
            raise GatePolicyValidationError(
                f"gate_schema[{gate_code}] is missing required fields: {sorted(missing_fields)}"
            )
        if extra_fields:
            raise GatePolicyValidationError(
                f"gate_schema[{gate_code}] contains unrecognized fields: {sorted(extra_fields)}"
            )

        evidence_codes = entry["evidence_requirement_codes"]
        if not isinstance(evidence_codes, list) or not all(
            isinstance(code, str) and code for code in evidence_codes
        ):
            raise GatePolicyValidationError(
                f"gate_schema[{gate_code}].evidence_requirement_codes must be a list of non-empty strings."
            )

        for field_name in ("decision_capability", "attempt_creation_capability"):
            capability = entry[field_name]
            if not isinstance(capability, str) or not capability or capability not in ALL_CAPABILITY_CODES:
                raise GatePolicyValidationError(
                    f"gate_schema[{gate_code}].{field_name} must be a registered stable capability code."
                )

        overridable = entry["overridable"]
        if not isinstance(overridable, bool):
            raise GatePolicyValidationError(f"gate_schema[{gate_code}].overridable must be a boolean.")
        if gate_code == "A2" and overridable:
            raise GatePolicyValidationError("gate_schema[A2].overridable must always be False.")

        non_overridable = entry["non_overridable_requirements"]
        if not isinstance(non_overridable, list) or not all(
            isinstance(code, str) and code for code in non_overridable
        ):
            raise GatePolicyValidationError(
                f"gate_schema[{gate_code}].non_overridable_requirements must be a list of non-empty strings."
            )
        if not set(non_overridable).issubset(set(evidence_codes)):
            raise GatePolicyValidationError(
                f"gate_schema[{gate_code}].non_overridable_requirements must be a subset of evidence requirements."
            )
        if not isinstance(entry["override_satisfies_successor_predecessor"], bool):
            raise GatePolicyValidationError(
                f"gate_schema[{gate_code}].override_satisfies_successor_predecessor must be a boolean."
            )


def canonical_gate_schema() -> dict:
    return {
        code: {
            "evidence_requirement_codes": [],
            "decision_capability": "APPROVE_GATE",
            "attempt_creation_capability": CREATE_PROCUREMENT_GATE_ATTEMPT,
            "overridable": False,
            "non_overridable_requirements": [],
            "override_satisfies_successor_predecessor": False,
        }
        for code in GATE_CODES
    }


def create_gate_policy(*, organization, code, name, actor, is_active=True) -> GatePolicy:
    authorized = (
        has_capability(actor, PUBLISH_GATE_POLICY, organization=organization)
        if organization is not None
        else _actor_holds_platform_policy_capability(actor)
    )
    if not authorized:
        log_denied_attempt(
            actor, PUBLISH_GATE_POLICY, organization=organization,
            required_capability=PUBLISH_GATE_POLICY,
        )
        raise AuthorizationDenied("Not authorized to create a gate policy.")
    with transaction.atomic():
        policy = GatePolicy.objects.create(
            organization=organization, code=code, name=name, is_active=is_active, created_by=actor,
        )
        audit.log(
            AuditEvent.Action.GATE_POLICY_CREATED, instance=policy, actor=actor,
            summary="Gate policy created", organization_id=str(organization.pk) if organization else None,
            policy_id=str(policy.pk), prior_state=None, resulting_state="CREATED",
        )
    return policy


def set_canonical_default(policy: GatePolicy, actor) -> GatePolicy:
    if not _actor_holds_platform_policy_capability(actor):
        log_denied_attempt(
            actor, PUBLISH_GATE_POLICY, resource=policy, required_capability=PUBLISH_GATE_POLICY,
            policy_id=policy.pk,
        )
        raise AuthorizationDenied("Not authorized to set a canonical default gate policy.")
    with transaction.atomic():
        locked_policies = list(GatePolicy.objects.select_for_update().order_by("pk"))
        locked = next((item for item in locked_policies if item.pk == policy.pk), None)
        if locked is None:
            raise GatePolicyValidationError("GatePolicy does not exist.")
        if locked.organization_id is not None:
            raise GatePolicyValidationError("An organization-owned GatePolicy can never be canonical.")
        if not locked.versions.filter(status=GatePolicyVersion.Status.PUBLISHED).exists():
            raise GatePolicyStateError("A canonical policy must already have a published version.")
        with _allow_controlled_policy_write():
            # Clear the old marker before setting the new marker because both
            # supported databases check this unique constraint immediately.
            for current in locked_policies:
                if current.pk != locked.pk and current.is_canonical_default:
                    current.is_canonical_default = False
                    current.save(update_fields=["is_canonical_default", "updated_at"])
            if not locked.is_canonical_default:
                locked.is_canonical_default = True
                locked.save(update_fields=["is_canonical_default", "updated_at"])
    return locked


def create_draft_policy_version(*, policy, actor, gate_schema, supersedes=None) -> GatePolicyVersion:
    persisted_policy = GatePolicy.objects.get(pk=policy.pk)
    _require_policy_administration_capability(persisted_policy, actor)
    with transaction.atomic():
        locked_policy = GatePolicy.objects.select_for_update().get(pk=persisted_policy.pk)
        if supersedes is not None and supersedes.policy_id != locked_policy.pk:
            raise GatePolicyValidationError("supersedes must belong to the same policy family.")
        last_number = (
            GatePolicyVersion.objects.filter(policy=locked_policy)
            .order_by("-version_number").values_list("version_number", flat=True).first()
        ) or 0
        return GatePolicyVersion.objects.create(
            policy=locked_policy, version_number=last_number + 1,
            status=GatePolicyVersion.Status.DRAFT, gate_schema=gate_schema,
            supersedes=supersedes, created_by=actor,
        )


def update_draft_policy_version(*, policy_version, actor, gate_schema) -> GatePolicyVersion:
    policy = GatePolicy.objects.get(pk=policy_version.policy_id)
    _require_policy_administration_capability(policy, actor, resource=policy_version)
    with transaction.atomic():
        GatePolicy.objects.select_for_update().get(pk=policy.pk)
        locked = GatePolicyVersion.objects.select_for_update().get(pk=policy_version.pk)
        if locked.status != GatePolicyVersion.Status.DRAFT:
            raise GatePolicyStateError("Only a DRAFT policy version may be edited.")
        locked.gate_schema = gate_schema
        with _allow_controlled_version_write():
            locked.save(update_fields=["gate_schema", "updated_at"])
    return locked


def _publish_locked_version(locked, actor):
    if locked.status != GatePolicyVersion.Status.DRAFT:
        raise GatePolicyStateError(f"Policy version is already {locked.status}; cannot publish.")
    validate_gate_schema(locked.gate_schema)
    locked.status = GatePolicyVersion.Status.PUBLISHED
    locked.published_at = timezone.now()
    locked.published_by = actor if getattr(actor, "pk", None) else None
    with _allow_controlled_version_write():
        locked.save(update_fields=["status", "published_at", "published_by", "updated_at"])
    audit.log(
        AuditEvent.Action.GATE_POLICY_VERSION_PUBLISHED, instance=locked, actor=actor,
        summary="Gate policy version published",
        organization_id=str(locked.policy.organization_id) if locked.policy.organization_id else None,
        policy_id=str(locked.policy_id), policy_version_id=str(locked.pk),
        prior_state="DRAFT", resulting_state="PUBLISHED",
    )
    return locked


def publish_policy_version(policy_version, actor) -> GatePolicyVersion:
    policy = GatePolicy.objects.get(pk=policy_version.policy_id)
    _require_policy_administration_capability(policy, actor, resource=policy_version)
    with transaction.atomic():
        locked_policy = GatePolicy.objects.select_for_update().get(pk=policy.pk)
        locked = GatePolicyVersion.objects.select_for_update().select_related("policy").get(
            pk=policy_version.pk, policy=locked_policy
        )
        return _publish_locked_version(locked, actor)


def _withdraw_locked_version(locked, actor, reason):
    if locked.status != GatePolicyVersion.Status.PUBLISHED:
        raise GatePolicyStateError("Only a PUBLISHED policy version may be withdrawn.")
    if locked.policy.is_canonical_default and not GatePolicyVersion.objects.filter(
        policy=locked.policy, status=GatePolicyVersion.Status.PUBLISHED,
    ).exclude(pk=locked.pk).exists():
        raise GatePolicyStateError("Cannot withdraw the final usable canonical published version.")
    locked.status = GatePolicyVersion.Status.WITHDRAWN
    locked.withdrawal_reason = reason
    with _allow_controlled_version_write():
        locked.save(update_fields=["status", "withdrawal_reason", "updated_at"])
    audit.log(
        AuditEvent.Action.GATE_POLICY_VERSION_WITHDRAWN, instance=locked, actor=actor,
        summary="Gate policy version withdrawn",
        organization_id=str(locked.policy.organization_id) if locked.policy.organization_id else None,
        policy_id=str(locked.policy_id), policy_version_id=str(locked.pk),
        prior_state="PUBLISHED", resulting_state="WITHDRAWN",
    )
    return locked


def withdraw_policy_version(policy_version, actor, reason: str) -> GatePolicyVersion:
    if not reason:
        raise GatePolicyValidationError("A written reason is required to withdraw a gate policy version.")
    policy = GatePolicy.objects.get(pk=policy_version.policy_id)
    _require_policy_administration_capability(policy, actor, resource=policy_version)
    with transaction.atomic():
        locked_policy = GatePolicy.objects.select_for_update().get(pk=policy.pk)
        locked = GatePolicyVersion.objects.select_for_update().select_related("policy").get(
            pk=policy_version.pk, policy=locked_policy
        )
        return _withdraw_locked_version(locked, actor, reason)


def replace_canonical_policy_version(
    *, replacement_policy_version, actor, prior_policy_version=None, withdrawal_reason=""
) -> GatePolicyVersion:
    """Atomically publish a replacement and optionally withdraw its predecessor."""
    policy = GatePolicy.objects.get(pk=replacement_policy_version.policy_id)
    _require_policy_administration_capability(policy, actor, resource=replacement_policy_version)
    with transaction.atomic():
        locked_policy = GatePolicy.objects.select_for_update().get(pk=policy.pk)
        if not locked_policy.is_canonical_default:
            raise GatePolicyValidationError("Replacement service applies only to the canonical policy.")
        replacement = GatePolicyVersion.objects.select_for_update().select_related("policy").get(
            pk=replacement_policy_version.pk, policy=locked_policy
        )
        replacement = _publish_locked_version(replacement, actor)
        if prior_policy_version is not None:
            if not withdrawal_reason:
                raise GatePolicyValidationError("A withdrawal reason is required when replacing a prior version.")
            prior = GatePolicyVersion.objects.select_for_update().select_related("policy").get(
                pk=prior_policy_version.pk, policy=locked_policy
            )
            if prior.pk == replacement.pk:
                raise GatePolicyValidationError("Replacement and prior versions must differ.")
            _withdraw_locked_version(prior, actor, withdrawal_reason)
        if not GatePolicyVersion.objects.filter(
            policy=locked_policy, status=GatePolicyVersion.Status.PUBLISHED
        ).exists():
            raise GatePolicyStateError("Canonical replacement left no usable published version.")
        return replacement


def _resolve_policy_version_for_organization(*, organization, explicit_policy_version_id=None):
    if explicit_policy_version_id is not None:
        version = GatePolicyVersion.objects.select_related("policy").get(pk=explicit_policy_version_id)
        eligible_scope = version.policy.is_canonical_default or version.policy.organization_id == organization.pk
        if (
            version.status != GatePolicyVersion.Status.PUBLISHED
            or not version.policy.is_active
            or not eligible_scope
        ):
            raise GatePolicyValidationError("Explicit policy version is not eligible for this package.")
        return version

    policy_ids = list(
        GatePolicy.objects.filter(
            organization=organization, is_active=True,
            versions__status=GatePolicyVersion.Status.PUBLISHED,
        ).values_list("pk", flat=True).distinct()
    )
    if len(policy_ids) > 1:
        raise PolicyResolutionAmbiguity("Multiple eligible organization gate-policy families exist.")
    if len(policy_ids) == 1:
        return GatePolicyVersion.objects.filter(
            policy_id=policy_ids[0], status=GatePolicyVersion.Status.PUBLISHED,
        ).order_by("-version_number").first()

    canonical_policies = GatePolicy.objects.filter(is_canonical_default=True, is_active=True)
    if canonical_policies.count() != 1:
        raise GatePolicyStateError("Exactly one usable canonical policy is required.")
    version = GatePolicyVersion.objects.filter(
        policy=canonical_policies.get(), status=GatePolicyVersion.Status.PUBLISHED,
    ).order_by("-version_number").first()
    if version is None:
        raise GatePolicyStateError("No usable canonical published version exists.")
    return version


def _create_assignment_locked(*, locked_package, policy_version, pinned_by):
    existing = PackagePolicyAssignment.objects.filter(package=locked_package).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            with _allow_controlled_assignment_write():
                assignment = PackagePolicyAssignment.objects.create(
                    package=locked_package, policy_version=policy_version,
                    pinned_at=timezone.now(), pinned_by=pinned_by, created_by=pinned_by,
                )
    except IntegrityError:
        return PackagePolicyAssignment.objects.get(package=locked_package)
    audit.log(
        AuditEvent.Action.GATE_POLICY_PINNED, instance=assignment, actor=pinned_by,
        summary="Gate policy version pinned to package",
        organization_id=str(locked_package.organization_id), package_id=str(locked_package.pk),
        assignment_id=str(assignment.pk), policy_id=str(policy_version.policy_id),
        policy_version_id=str(policy_version.pk), prior_state=None, resulting_state="PINNED",
    )
    return assignment


def assign_policy_to_package(actor, package_id, *, explicit_policy_version_id=None):
    """The sole user-callable assignment boundary."""
    from apps.procurement.models import ProcurementPackage

    if hasattr(package_id, "pk") or hasattr(explicit_policy_version_id, "pk"):
        raise TypeError("Assignment accepts persisted UUID values, never model objects.")
    denied = False
    with transaction.atomic():
        locked_package = ProcurementPackage.objects.select_for_update().get(pk=package_id)
        if not has_capability(actor, ASSIGN_GATE_POLICY, package=locked_package):
            log_denied_attempt(
                actor, ASSIGN_GATE_POLICY, package=locked_package,
                required_capability=ASSIGN_GATE_POLICY,
                policy_version_id=explicit_policy_version_id,
            )
            denied = True
        else:
            existing = PackagePolicyAssignment.objects.filter(package=locked_package).first()
            if existing is not None:
                return existing
            version = _resolve_policy_version_for_organization(
                organization=locked_package.organization,
                explicit_policy_version_id=explicit_policy_version_id,
            )
            return _create_assignment_locked(
                locked_package=locked_package, policy_version=version, pinned_by=actor,
            )
    if denied:
        raise AuthorizationDenied("Not authorized to assign a gate policy to this package.")


def _assign_policy_to_package_system(package_id, *, policy_version_id):
    """Private bootstrap path: fixed IDs only, never accepts an actor or caller policy object."""
    from apps.procurement.models import ProcurementPackage

    with transaction.atomic():
        locked_package = ProcurementPackage.objects.select_for_update().get(pk=package_id)
        existing = PackagePolicyAssignment.objects.filter(package=locked_package).first()
        if existing is not None:
            return existing
        version = GatePolicyVersion.objects.select_related("policy").get(
            pk=policy_version_id,
            status=GatePolicyVersion.Status.PUBLISHED,
            policy__is_canonical_default=True,
            policy__is_active=True,
        )
        return _create_assignment_locked(
            locked_package=locked_package, policy_version=version, pinned_by=None,
        )


def grant_gate_progression_exemption(actor, package_id, reason: str):
    if not reason:
        raise GatePolicyValidationError("A written reason is required to grant a gate-progression exemption.")
    from apps.procurement.models import ProcurementPackage

    denied = False
    with transaction.atomic():
        locked_package = ProcurementPackage.objects.select_for_update().get(pk=package_id)
        if not has_capability(actor, EXEMPT_PACKAGE_FROM_PROCUREMENT_GATES, package=locked_package):
            log_denied_attempt(
                actor, EXEMPT_PACKAGE_FROM_PROCUREMENT_GATES, package=locked_package,
                required_capability=EXEMPT_PACKAGE_FROM_PROCUREMENT_GATES,
            )
            denied = True
        else:
            assignment = PackagePolicyAssignment.objects.select_for_update().get(package=locked_package)
            if assignment.gate_progression_exempt:
                return assignment
            assignment.gate_progression_exempt = True
            assignment.exemption_reason = reason
            assignment.exemption_granted_by = actor
            assignment.exemption_granted_at = timezone.now()
            with _allow_controlled_assignment_write():
                assignment.save(update_fields=[
                    "gate_progression_exempt", "exemption_reason", "exemption_granted_by",
                    "exemption_granted_at", "updated_at",
                ])
            audit.log(
                AuditEvent.Action.GATE_PROGRESSION_EXEMPTION_GRANTED,
                instance=assignment, actor=actor, summary="Gate progression exemption granted",
                organization_id=str(locked_package.organization_id),
                package_id=str(locked_package.pk), assignment_id=str(assignment.pk),
                policy_version_id=str(assignment.policy_version_id),
                capability_code=EXEMPT_PACKAGE_FROM_PROCUREMENT_GATES,
                exempt=True,
                prior_state="NOT_EXEMPT", resulting_state="EXEMPT",
            )
            return assignment
    if denied:
        raise AuthorizationDenied("Not authorized to exempt this package from procurement gates.")


CANONICAL_POLICY_CODE = "canonical-a1-a6"


def seed_canonical_policy() -> GatePolicy:
    with transaction.atomic():
        policy = GatePolicy.objects.filter(is_canonical_default=True).first()
        if policy is None:
            with _allow_controlled_policy_write():
                policy = GatePolicy.objects.create(
                    organization=None, code=CANONICAL_POLICY_CODE,
                    name="Canonical A1-A6 Procurement Gate Policy",
                    is_active=True, is_canonical_default=True,
                )
            audit.log(
                AuditEvent.Action.GATE_POLICY_CREATED, instance=policy, actor=None,
                summary="Canonical default gate policy seeded", policy_id=str(policy.pk),
                prior_state=None, resulting_state="CREATED",
            )
        version = GatePolicyVersion.objects.filter(policy=policy, version_number=1).first()
        if version is None:
            version = GatePolicyVersion.objects.create(
                policy=policy, version_number=1, status=GatePolicyVersion.Status.DRAFT,
                gate_schema=canonical_gate_schema(),
            )
        if version.status == GatePolicyVersion.Status.DRAFT:
            validate_gate_schema(version.gate_schema)
            version.status = GatePolicyVersion.Status.PUBLISHED
            version.published_at = timezone.now()
            with _allow_controlled_version_write():
                version.save(update_fields=["status", "published_at", "published_by", "updated_at"])
            audit.log(
                AuditEvent.Action.GATE_POLICY_VERSION_PUBLISHED, instance=version, actor=None,
                summary="Canonical gate policy version 1 published",
                policy_id=str(policy.pk), policy_version_id=str(version.pk),
                prior_state="DRAFT", resulting_state="PUBLISHED",
            )
        return policy


def assign_canonical_policy_to_existing_packages() -> int:
    from apps.procurement.models import ProcurementPackage

    policy = seed_canonical_policy()
    version = policy.versions.filter(status=GatePolicyVersion.Status.PUBLISHED).order_by("version_number").first()
    assigned = 0
    for package_id in ProcurementPackage.objects.exclude(policy_assignments__isnull=False).values_list("pk", flat=True):
        before = PackagePolicyAssignment.objects.filter(package_id=package_id).exists()
        _assign_policy_to_package_system(package_id, policy_version_id=version.pk)
        assigned += int(not before)
    return assigned


# ---------------------------------------------------------------------------
# Milestone 1 Increment 2 — gate execution core and A1 Deal Established.
# ---------------------------------------------------------------------------

A1_ROLE_REQUIREMENTS = {
    "buyer_party": ("buyer",),
    "seller_or_exporter_party": ("seller_of_record", "exporter_of_record"),
    "procurement_operator_party": ("china_procurement_operator",),
    "factory_or_site_party": ("production_factory", "production_site"),
    "logistics_authority": ("logistics_operator", "consolidation_warehouse"),
    "quality_authority": ("quality_operator", "inspector", "laboratory"),
    "approval_authority": ("buyer_approver", "technical_authority", "non_financial_approver"),
}

A1_ROLE_BLOCKERS = {
    "buyer_party": "A1_BUYER_ROLE_MISSING",
    "seller_or_exporter_party": "A1_SELLER_OR_EXPORTER_ROLE_MISSING",
    "procurement_operator_party": "A1_PROCUREMENT_OPERATOR_ROLE_MISSING",
    "factory_or_site_party": "A1_FACTORY_OR_SITE_ROLE_MISSING",
    "logistics_authority": "A1_LOGISTICS_AUTHORITY_MISSING",
    "quality_authority": "A1_QUALITY_AUTHORITY_MISSING",
    "approval_authority": "A1_APPROVAL_AUTHORITY_MISSING",
}


def _fingerprint(payload) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_idempotency_key(idempotency_key):
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 128:
        raise GateExecutionError("A non-empty idempotency key of at most 128 characters is required.")
    return idempotency_key.strip()


def _validate_identifier(value, label):
    if hasattr(value, "pk"):
        raise TypeError(f"{label} must be a persisted identifier, never a model object.")
    return value


def _audit_gate(action, instance, actor, *, package, attempt=None, evaluation=None, **metadata):
    assignment = PackagePolicyAssignment.objects.filter(package_id=package.pk).only("policy_version_id").first()
    return audit.log(
        action,
        instance=instance,
        actor=actor,
        summary={
            AuditEvent.Action.PACKAGE_GATES_INITIALIZED: "Package gates initialized",
            AuditEvent.Action.GATE_ATTEMPT_OPENED: "Procurement gate attempt opened",
            AuditEvent.Action.GATE_EVALUATED: "Procurement gate evaluated",
            AuditEvent.Action.GATE_REVIEW_REQUESTED: "Procurement gate review requested",
            AuditEvent.Action.GATE_PASSED: "Procurement gate passed",
            AuditEvent.Action.GATE_FAILED: "Procurement gate returned",
            AuditEvent.Action.GATE_ADVANCEMENT_BLOCKED: "Procurement gate advancement blocked",
        }[action],
        organization_id=str(package.organization_id),
        package_id=str(package.pk),
        policy_version_id=str(assignment.policy_version_id) if assignment else None,
        gate_code=getattr(attempt, "gate_code", "A1"),
        attempt_id=str(attempt.pk) if attempt else None,
        evaluation_id=str(evaluation.pk) if evaluation else None,
        **metadata,
    )


def _safe_attempt_scope(actor, *, package_id, attempt_id):
    from apps.procurement.models import ProcurementPackage

    _validate_identifier(package_id, "package_id")
    _validate_identifier(attempt_id, "attempt_id")
    safe_attempt = GateAttempt.objects.filter(pk=attempt_id).values(
        "package_id", "gate_code", "decision_capability"
    ).first()
    if safe_attempt is None:
        raise GateExecutionError("Gate attempt does not exist.")
    package = ProcurementPackage.objects.only("id", "organization_id").get(pk=safe_attempt["package_id"])
    if str(package.pk) != str(package_id):
        log_denied_attempt(
            actor, "PROCUREMENT_GATE_TARGET_MISMATCH", package=package,
            denial_reason_code="TARGET_IDENTIFIER_MISMATCH",
        )
        raise AuthorizationDenied("Gate target identifiers do not match.")
    return package, safe_attempt


def _deny_gate(actor, capability_code, package, *, attempt_id=None, denial_reason_code="CAPABILITY_REQUIRED"):
    log_denied_attempt(
        actor,
        capability_code,
        package=package,
        required_capability=capability_code,
        denial_reason_code=denial_reason_code,
    )
    raise AuthorizationDenied("Not authorized for this procurement-gate action.")


def _create_attempt_locked(
    *, package, assignment, gate_code, actor, idempotency_key, operation,
    authorization_capability=None,
):
    if gate_code != "A1":
        raise GateExecutionError("Increment 2 permits opening A1 attempts only.")
    fingerprint = _fingerprint({
        "operation": operation,
        "package_id": str(package.pk),
        "gate_code": gate_code,
        "policy_version_id": str(assignment.policy_version_id),
    })
    existing_key = GateAttempt.objects.filter(idempotency_key=idempotency_key).first()
    if existing_key is not None:
        if (
            existing_key.package_id == package.pk
            and existing_key.gate_code == gate_code
            and existing_key.request_fingerprint == fingerprint
        ):
            return existing_key, False
        raise GateIdempotencyConflict("Idempotency key was already used for a different gate request.")

    if GateAttempt.objects.filter(package=package, gate_code=gate_code, closed_at__isnull=True).exists():
        raise GateExecutionError("An open A1 attempt already exists.")
    if package.is_on_hold:
        raise GateExecutionError("A held package cannot open a gate attempt.")

    gate_entry = assignment.policy_version.gate_schema.get(gate_code)
    if not isinstance(gate_entry, dict):
        raise GateExecutionError("Pinned policy does not contain the requested gate.")
    decision_capability = gate_entry.get("decision_capability")
    if decision_capability not in ALL_CAPABILITY_CODES:
        raise GateExecutionError("Pinned policy has no registered decision capability for A1.")
    attempt_capability = gate_entry.get("attempt_creation_capability")
    if attempt_capability not in ALL_CAPABILITY_CODES:
        raise GateExecutionError("Pinned policy has no registered attempt capability for A1.")

    attempt_number = (
        GateAttempt.objects.filter(package=package, gate_code=gate_code)
        .aggregate(max_number=Max("attempt_number"))["max_number"]
        or 0
    ) + 1
    with _allow_controlled_execution_write():
        attempt = GateAttempt.objects.create(
            package=package,
            assignment=assignment,
            policy_version=assignment.policy_version,
            gate_code=gate_code,
            attempt_number=attempt_number,
            attempt_creation_capability=attempt_capability,
            decision_capability=decision_capability,
            opened_at=timezone.now(),
            opened_by=actor,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            created_by=actor,
        )
    _audit_gate(
        AuditEvent.Action.GATE_ATTEMPT_OPENED,
        attempt,
        actor,
        package=package,
        attempt=attempt,
        prior_state=GateState.NOT_STARTED if attempt_number == 1 else GateState.FAILED,
        resulting_state=GateState.IN_REVIEW,
        capability_code=authorization_capability or attempt_capability,
        attempt_number=attempt_number,
    )
    return attempt, True


def compute_gate_state(package, gate_code):
    """Pure, side-effect-free state derivation from immutable history."""
    if gate_code not in GATE_CODES:
        raise GateExecutionError("Unknown procurement gate code.")
    assignment = PackagePolicyAssignment.objects.filter(package_id=package.pk).only(
        "gate_progression_exempt"
    ).first()
    if assignment is not None and assignment.gate_progression_exempt:
        return GateState.NOT_STARTED
    attempt = GateAttempt.objects.filter(package_id=package.pk, gate_code=gate_code).order_by(
        "-attempt_number"
    ).first()
    if attempt is None:
        return GateState.NOT_STARTED
    decision = GateDecision.objects.filter(attempt=attempt).only("outcome").first()
    if decision is not None:
        return GateState.PASSED if decision.outcome == GateDecision.Outcome.PASSED else GateState.FAILED
    evaluation = attempt.evaluations.order_by("-evaluated_at", "-created_at").first()
    if evaluation is None:
        return GateState.IN_REVIEW
    if not evaluation.predecessor_valid or evaluation.package_on_hold_at_evaluation:
        return GateState.BLOCKED
    if evaluation.overall_ready:
        if attempt.review_requested_at is not None and attempt.review_evaluation_id == evaluation.pk:
            return GateState.IN_REVIEW
        return GateState.READY
    return GateState.BLOCKED if evaluation.blocker_codes else GateState.IN_REVIEW


def derive_current_gate(package):
    """Return the first canonical gate without a pass; exempt packages have none."""
    assignment = PackagePolicyAssignment.objects.filter(package_id=package.pk).only(
        "gate_progression_exempt"
    ).first()
    if assignment is not None and assignment.gate_progression_exempt:
        return None
    if compute_gate_state(package, "A1") != GateState.PASSED:
        return "A1"
    return "A2"


def _refresh_gate_state_locked(package, gate_code):
    state = compute_gate_state(package, gate_code)
    assignment = PackagePolicyAssignment.objects.filter(package_id=package.pk).only(
        "gate_progression_exempt"
    ).first()
    with _allow_controlled_projection_write():
        row, _ = PackageGateState.objects.update_or_create(
            package=package,
            gate_code=gate_code,
            defaults={
                "state": state,
                "is_exempt": bool(assignment and assignment.gate_progression_exempt),
                "last_recomputed_at": timezone.now(),
            },
        )
    return row


def _refresh_all_gate_states_locked(package):
    return [_refresh_gate_state_locked(package, gate_code) for gate_code in GATE_CODES]


def rebuild_gate_state(package_id):
    """Rebuild the non-authoritative cache from history under the package lock."""
    from apps.procurement.models import ProcurementPackage

    _validate_identifier(package_id, "package_id")
    with transaction.atomic():
        package = ProcurementPackage.objects.select_for_update().get(pk=package_id)
        return _refresh_all_gate_states_locked(package)


def initialize_package_gates(actor, package_id, *, idempotency_key):
    """Authorize and open exactly the first A1 attempt for a pinned package."""
    from apps.procurement.models import ProcurementPackage

    _validate_identifier(package_id, "package_id")
    idempotency_key = _validate_idempotency_key(idempotency_key)
    denied_package = None
    with transaction.atomic():
        package = ProcurementPackage.objects.select_for_update().only("id", "organization_id").get(
            pk=package_id
        )
        if not has_capability(
            actor, CREATE_PROCUREMENT_GATE_ATTEMPT, organization=package.organization
        ):
            denied_package = package
        else:
            package = ProcurementPackage.objects.get(pk=package.pk)
            assignment = PackagePolicyAssignment.objects.select_related("policy_version").get(package=package)
            if assignment.gate_progression_exempt:
                raise GateExecutionError("Gate-exempt packages are not initialized into progression.")
            attempts = GateAttempt.objects.filter(package=package, gate_code="A1")
            existing = attempts.first()
            if existing is not None:
                fingerprint = _fingerprint({
                    "operation": "initialize",
                    "package_id": str(package.pk),
                    "gate_code": "A1",
                    "policy_version_id": str(assignment.policy_version_id),
                })
                if existing.idempotency_key == idempotency_key and existing.request_fingerprint == fingerprint:
                    return existing
                raise GateIdempotencyConflict("Package gates were already initialized with another request.")
            attempt, _ = _create_attempt_locked(
                package=package,
                assignment=assignment,
                gate_code="A1",
                actor=actor,
                idempotency_key=idempotency_key,
                operation="initialize",
                authorization_capability=CREATE_PROCUREMENT_GATE_ATTEMPT,
            )
            _refresh_all_gate_states_locked(package)
            _audit_gate(
                AuditEvent.Action.PACKAGE_GATES_INITIALIZED,
                attempt,
                actor,
                package=package,
                attempt=attempt,
                prior_state=GateState.NOT_STARTED,
                resulting_state=GateState.IN_REVIEW,
                capability_code=CREATE_PROCUREMENT_GATE_ATTEMPT,
            )
            return attempt
    if denied_package is not None:
        _deny_gate(actor, CREATE_PROCUREMENT_GATE_ATTEMPT, denied_package)


def open_a1_attempt(actor, package_id, *, idempotency_key):
    """Open a subsequent A1 attempt after a historical FAILED decision."""
    from apps.procurement.models import ProcurementPackage

    _validate_identifier(package_id, "package_id")
    idempotency_key = _validate_idempotency_key(idempotency_key)
    safe_prior = GateAttempt.objects.filter(package_id=package_id, gate_code="A1").order_by(
        "-attempt_number"
    ).values("attempt_creation_capability").first()
    if safe_prior is None:
        raise GateExecutionError("Use package initialization for the first A1 attempt.")
    attempt_capability = safe_prior["attempt_creation_capability"]
    package_scope = ProcurementPackage.objects.only("id", "organization_id").get(pk=package_id)
    if not has_capability(actor, attempt_capability, package=package_scope):
        _deny_gate(actor, attempt_capability, package_scope)
    denied_package = None
    with transaction.atomic():
        package = ProcurementPackage.objects.select_for_update().get(pk=package_id)
        if not has_capability(actor, attempt_capability, package=package):
            denied_package = package
        else:
            assignment = PackagePolicyAssignment.objects.select_related("policy_version").get(package=package)
            if assignment.gate_progression_exempt:
                raise GateExecutionError("Gate-exempt packages cannot open attempts.")
            prior = GateAttempt.objects.filter(package=package, gate_code="A1").order_by(
                "-attempt_number"
            ).first()
            gate_entry = assignment.policy_version.gate_schema.get("A1", {})
            persisted_attempt_capability = gate_entry.get("attempt_creation_capability")
            if persisted_attempt_capability not in ALL_CAPABILITY_CODES:
                raise GateExecutionError("Pinned policy has no registered attempt capability for A1.")
            if persisted_attempt_capability != attempt_capability:
                raise GateExecutionError("Pinned attempt capability does not match immutable attempt history.")
            if denied_package is None:
                existing_key = GateAttempt.objects.filter(idempotency_key=idempotency_key).first()
                expected_fingerprint = _fingerprint({
                    "operation": "reattempt",
                    "package_id": str(package.pk),
                    "gate_code": "A1",
                    "policy_version_id": str(assignment.policy_version_id),
                })
                if existing_key is not None:
                    if (
                        existing_key.package_id == package.pk
                        and existing_key.request_fingerprint == expected_fingerprint
                    ):
                        return existing_key
                    raise GateIdempotencyConflict("Idempotency key was already used for another request.")
                if compute_gate_state(package, "A1") != GateState.FAILED:
                    raise GateExecutionError("A new A1 attempt requires a prior FAILED decision.")
                attempt, _ = _create_attempt_locked(
                    package=package,
                    assignment=assignment,
                    gate_code="A1",
                    actor=actor,
                    idempotency_key=idempotency_key,
                    operation="reattempt",
                    authorization_capability=attempt_capability,
                )
                _refresh_all_gate_states_locked(package)
                return attempt
    if denied_package is not None:
        _deny_gate(actor, attempt_capability, denied_package)


def _active_a1_assignments(package):
    today = timezone.now().date()
    return RoleAssignment.objects.filter(
        package=package,
        organization_context_id=package.organization_id,
        status=RoleAssignment.Status.ACTIVE,
        effective_from__lte=today,
        party__is_active=True,
        party__hosting_organization_id=package.organization_id,
        superseded_by__isnull=True,
    ).exclude(effective_until__lt=today).select_related("party")


def _decision_authority_exists(package, capability_code, assignments):
    today = timezone.now().date()
    grants = CapabilityGrant.objects.filter(capability_code=capability_code, is_active=True).filter(
        Q(package=package, user__isnull=False)
        | Q(role_assignment__in=assignments, role_assignment__party__memberships__is_active=True)
    ).distinct()
    return any(grant.is_currently_active(on_date=today) for grant in grants)


def _evaluate_a1_requirements(package, attempt):
    assignments = _active_a1_assignments(package)
    counts = {
        requirement: assignments.filter(role_code__in=role_codes).count()
        for requirement, role_codes in A1_ROLE_REQUIREMENTS.items()
    }
    requirement_results = {
        "administering_organization": {
            "satisfied": package.organization_id is not None,
            "qualifying_count": int(package.organization_id is not None),
            "check_code": "A1_ADMINISTERING_ORGANIZATION",
        },
    }
    blocker_codes = []
    if package.organization_id is None:
        blocker_codes.append("A1_ADMINISTERING_ORGANIZATION_MISSING")

    for requirement, count in counts.items():
        requirement_results[requirement] = {
            "satisfied": count > 0,
            "qualifying_count": count,
            "check_code": f"A1_{requirement.upper()}",
        }
        if count == 0:
            blocker_codes.append(A1_ROLE_BLOCKERS[requirement])

    parties_by_role = {}
    critical_role_codes = {
        role_code for role_codes in A1_ROLE_REQUIREMENTS.values() for role_code in role_codes
    }
    for role_code, party_id in assignments.values_list("role_code", "party_id"):
        if role_code in critical_role_codes:
            parties_by_role.setdefault(role_code, set()).add(party_id)
    conflicting_role_codes = sorted(
        role_code for role_code, party_ids in parties_by_role.items() if len(party_ids) > 1
    )
    conflicts_clear = not conflicting_role_codes
    requirement_results["critical_role_conflicts_clear"] = {
        "satisfied": conflicts_clear,
        "qualifying_count": len(conflicting_role_codes),
        "check_code": "A1_CRITICAL_ROLE_CONFLICTS_CLEAR",
    }
    if not conflicts_clear:
        blocker_codes.append("A1_CRITICAL_ROLE_CONFLICT")

    visibility_valid = package.visibility_mode in VisibilityMode.values
    requirement_results["visibility_mode_selected"] = {
        "satisfied": visibility_valid,
        "qualifying_count": int(visibility_valid),
        "check_code": "A1_VISIBILITY_MODE_SELECTED",
    }
    if not visibility_valid:
        blocker_codes.append("A1_VISIBILITY_MODE_INVALID")

    decision_authority = _decision_authority_exists(
        package, attempt.decision_capability, assignments
    )
    requirement_results["decision_authority_registered"] = {
        "satisfied": decision_authority,
        "qualifying_count": int(decision_authority),
        "check_code": "A1_DECISION_AUTHORITY_REGISTERED",
    }
    if not decision_authority:
        blocker_codes.append("A1_DECISION_AUTHORITY_MISSING")

    unresolved_risks = RiskFlag.objects.filter(
        package=package, resolved_at__isnull=True
    ).exclude(level=RiskFlag.Level.STANDARD).count()
    unresolved_changes = ChangeRequest.objects.filter(
        package=package, status=ChangeRequest.Status.PENDING
    ).count()
    risk_clear = unresolved_risks == 0
    change_clear = unresolved_changes == 0
    hold_clear = not package.is_on_hold
    requirement_results["blocking_risks_clear"] = {
        "satisfied": risk_clear,
        "qualifying_count": unresolved_risks,
        "check_code": "A1_BLOCKING_RISKS_CLEAR",
    }
    requirement_results["blocking_changes_clear"] = {
        "satisfied": change_clear,
        "qualifying_count": unresolved_changes,
        "check_code": "A1_BLOCKING_CHANGES_CLEAR",
    }
    requirement_results["package_hold_clear"] = {
        "satisfied": hold_clear,
        "qualifying_count": int(not hold_clear),
        "check_code": "A1_PACKAGE_HOLD_CLEAR",
    }
    if not risk_clear:
        blocker_codes.append("A1_UNRESOLVED_BLOCKING_RISK")
    if not change_clear:
        blocker_codes.append("A1_UNRESOLVED_BLOCKING_CHANGE")
    if not hold_clear:
        blocker_codes.append("A1_PACKAGE_ON_HOLD")

    gate_entry = attempt.policy_version.gate_schema.get("A1", {})
    for code in gate_entry.get("evidence_requirement_codes", []):
        requirement_results[f"policy_requirement:{code}"] = {
            "satisfied": False,
            "qualifying_count": 0,
            "check_code": code,
        }
        blocker_codes.append(f"A1_POLICY_REQUIREMENT_UNAVAILABLE:{code}")

    blocker_codes = sorted(set(blocker_codes))
    overall_ready = not blocker_codes and all(
        item["satisfied"] for item in requirement_results.values()
    )
    return {
        "requirement_results": requirement_results,
        "blocker_codes": blocker_codes,
        "predecessor_valid": True,
        "package_on_hold_at_evaluation": package.is_on_hold,
        "overall_ready": overall_ready,
        "result_code": (
            GateEvaluation.Result.SATISFIED if overall_ready else GateEvaluation.Result.BLOCKED
        ),
        "safe_summary": {
            "gate_code": "A1",
            "result_code": (
                GateEvaluation.Result.SATISFIED if overall_ready else GateEvaluation.Result.BLOCKED
            ),
            "satisfied_requirement_count": sum(
                1 for item in requirement_results.values() if item["satisfied"]
            ),
            "requirement_count": len(requirement_results),
            "blocker_codes": blocker_codes,
        },
    }


def evaluate_a1(actor, package_id, attempt_id, *, idempotency_key):
    idempotency_key = _validate_idempotency_key(idempotency_key)
    package, _ = _safe_attempt_scope(actor, package_id=package_id, attempt_id=attempt_id)
    if not has_capability(actor, EVALUATE_PROCUREMENT_GATE, package=package):
        _deny_gate(actor, EVALUATE_PROCUREMENT_GATE, package)
    fingerprint = _fingerprint({
        "operation": "evaluate_a1", "package_id": str(package.pk), "attempt_id": str(attempt_id)
    })
    try:
        with transaction.atomic():
            attempt = GateAttempt.objects.select_for_update().select_related("policy_version").get(pk=attempt_id)
            from apps.procurement.models import ProcurementPackage

            locked_package = ProcurementPackage.objects.select_for_update().get(pk=attempt.package_id)
            if not has_capability(actor, EVALUATE_PROCUREMENT_GATE, package=locked_package):
                raise _AuthorizationLost(EVALUATE_PROCUREMENT_GATE)
            existing = GateEvaluation.objects.filter(idempotency_key=idempotency_key).first()
            if existing is not None:
                if existing.attempt_id == attempt.pk and existing.request_fingerprint == fingerprint:
                    return existing
                raise GateIdempotencyConflict("Idempotency key was already used for another evaluation.")
            if attempt.gate_code != "A1" or attempt.closed_at is not None:
                raise GateExecutionError("Only the current open A1 attempt may be evaluated.")
            if attempt.review_requested_at is not None:
                raise GateExecutionError("A1 cannot be reevaluated after review is requested.")
            if GateAttempt.objects.filter(
                package=locked_package, gate_code="A1", closed_at__isnull=True
            ).exclude(pk=attempt.pk).exists():
                raise GateExecutionError("A1 attempt is stale.")
            result = _evaluate_a1_requirements(locked_package, attempt)
            with _allow_controlled_execution_write():
                evaluation = GateEvaluation.objects.create(
                    attempt=attempt,
                    policy_version=attempt.policy_version,
                    evaluated_at=timezone.now(),
                    evaluated_by=actor,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                    created_by=actor,
                    **result,
                )
            _refresh_gate_state_locked(locked_package, "A1")
            _audit_gate(
                AuditEvent.Action.GATE_EVALUATED,
                evaluation,
                actor,
                package=locked_package,
                attempt=attempt,
                evaluation=evaluation,
                prior_state=GateState.IN_REVIEW,
                resulting_state=GateState.READY if evaluation.overall_ready else GateState.BLOCKED,
                capability_code=EVALUATE_PROCUREMENT_GATE,
                result_code=evaluation.result_code,
                blocker_codes=evaluation.blocker_codes,
            )
            return evaluation
    except _AuthorizationLost as exc:
        _deny_gate(actor, exc.capability_code, package)


def request_a1_review(actor, package_id, attempt_id, *, idempotency_key):
    idempotency_key = _validate_idempotency_key(idempotency_key)
    package, _ = _safe_attempt_scope(actor, package_id=package_id, attempt_id=attempt_id)
    if not has_capability(actor, REQUEST_PROCUREMENT_GATE_REVIEW, package=package):
        _deny_gate(actor, REQUEST_PROCUREMENT_GATE_REVIEW, package)
    try:
        with transaction.atomic():
            attempt = GateAttempt.objects.select_for_update().get(pk=attempt_id)
            from apps.procurement.models import ProcurementPackage

            locked_package = ProcurementPackage.objects.select_for_update().get(pk=attempt.package_id)
            if not has_capability(actor, REQUEST_PROCUREMENT_GATE_REVIEW, package=locked_package):
                raise _AuthorizationLost(REQUEST_PROCUREMENT_GATE_REVIEW)
            latest = attempt.evaluations.order_by("-evaluated_at", "-created_at").first()
            if attempt.closed_at is not None or latest is None or not latest.overall_ready:
                raise GateExecutionError("A1 review requires the current satisfied evaluation.")
            if GateAttempt.objects.filter(
                review_idempotency_key=idempotency_key
            ).exclude(pk=attempt.pk).exists():
                raise GateIdempotencyConflict("Idempotency key was already used for another review.")
            current_result = _evaluate_a1_requirements(locked_package, attempt)
            if (
                current_result["requirement_results"] != latest.requirement_results
                or current_result["blocker_codes"] != latest.blocker_codes
                or current_result["overall_ready"] != latest.overall_ready
            ):
                raise GateExecutionError("A1 evaluation is stale.")
            if attempt.review_requested_at is not None:
                if (
                    attempt.review_idempotency_key == idempotency_key
                    and attempt.review_evaluation_id == latest.pk
                ):
                    return attempt
                raise GateIdempotencyConflict("A1 review was already requested with another request.")
            attempt.review_requested_at = timezone.now()
            attempt.review_requested_by = actor
            attempt.review_idempotency_key = idempotency_key
            attempt.review_evaluation = latest
            with _allow_controlled_execution_write():
                attempt.save(update_fields=[
                    "review_requested_at", "review_requested_by", "review_idempotency_key",
                    "review_evaluation", "updated_at",
                ])
            _refresh_gate_state_locked(locked_package, "A1")
            _audit_gate(
                AuditEvent.Action.GATE_REVIEW_REQUESTED,
                attempt,
                actor,
                package=locked_package,
                attempt=attempt,
                evaluation=latest,
                prior_state=GateState.READY,
                resulting_state=GateState.IN_REVIEW,
                capability_code=REQUEST_PROCUREMENT_GATE_REVIEW,
            )
            return attempt
    except _AuthorizationLost as exc:
        _deny_gate(actor, exc.capability_code, package)


def decide_a1_advancement(
    actor, package_id, attempt_id, evaluation_id, decision, *, idempotency_key, comment
):
    idempotency_key = _validate_idempotency_key(idempotency_key)
    _validate_identifier(evaluation_id, "evaluation_id")
    if decision not in GateDecision.Outcome.values:
        raise GateExecutionError("Decision must use the stable PASSED or FAILED code.")
    if not isinstance(comment, str):
        raise GateExecutionError("Decision comment must be text.")
    if decision == GateDecision.Outcome.FAILED and not comment.strip():
        raise GateExecutionError("A returned A1 decision requires a comment.")

    package, safe_attempt = _safe_attempt_scope(actor, package_id=package_id, attempt_id=attempt_id)
    capability_code = safe_attempt["decision_capability"]
    if not has_capability(actor, capability_code, package=package):
        _deny_gate(actor, capability_code, package)
    fingerprint = _fingerprint({
        "operation": "decide_a1",
        "package_id": str(package.pk),
        "attempt_id": str(attempt_id),
        "evaluation_id": str(evaluation_id),
        "decision": decision,
        "comment": comment,
    })
    attempt_for_audit = None
    evaluation_for_audit = None
    try:
        with transaction.atomic():
            attempt = GateAttempt.objects.select_for_update().select_related("policy_version").get(pk=attempt_id)
            attempt_for_audit = attempt
            from apps.procurement.models import ProcurementPackage

            locked_package = ProcurementPackage.objects.select_for_update().get(pk=attempt.package_id)
            if not has_capability(actor, attempt.decision_capability, package=locked_package):
                raise _AuthorizationLost(attempt.decision_capability)
            existing = GateDecision.objects.filter(idempotency_key=idempotency_key).first()
            if existing is not None:
                if existing.attempt_id == attempt.pk and existing.request_fingerprint == fingerprint:
                    return existing
                raise GateIdempotencyConflict("Idempotency key was already used for another decision.")
            if GateDecision.objects.filter(attempt=attempt).exists() or attempt.closed_at is not None:
                raise GateExecutionError("A1 attempt is already decided.")
            if attempt.gate_code != "A1":
                raise GateExecutionError("Increment 2 can decide A1 only.")
            evaluation = GateEvaluation.objects.filter(pk=evaluation_id, attempt=attempt).first()
            if evaluation is None:
                raise GateExecutionError("A1 evaluation does not belong to this attempt.")
            evaluation_for_audit = evaluation
            latest = attempt.evaluations.order_by("-evaluated_at", "-created_at").first()
            if latest is None or latest.pk != evaluation.pk:
                raise GateExecutionError("A1 evaluation is stale.")
            if attempt.review_requested_at is None or attempt.review_evaluation_id != evaluation.pk:
                raise GateExecutionError("A1 review has not been requested for this evaluation.")
            actor_id = getattr(actor, "pk", None)
            if decision == GateDecision.Outcome.PASSED and actor_id in {
                attempt.opened_by_id, attempt.review_requested_by_id, evaluation.evaluated_by_id
            }:
                raise GateExecutionError("A1 requester or preparer cannot approve the same attempt.")
            current_result = _evaluate_a1_requirements(locked_package, attempt)
            if (
                current_result["requirement_results"] != evaluation.requirement_results
                or current_result["blocker_codes"] != evaluation.blocker_codes
                or current_result["overall_ready"] != evaluation.overall_ready
            ):
                raise GateExecutionError("A1 evaluation is stale.")
            if decision == GateDecision.Outcome.PASSED and not evaluation.overall_ready:
                raise GateExecutionError("Only a satisfied A1 evaluation may be approved.")
            prior_state = compute_gate_state(locked_package, "A1")
            resulting_state = (
                GateState.PASSED if decision == GateDecision.Outcome.PASSED else GateState.FAILED
            )
            with _allow_controlled_execution_write():
                gate_decision = GateDecision.objects.create(
                    attempt=attempt,
                    evaluation=evaluation,
                    decided_by=actor,
                    decided_at=timezone.now(),
                    outcome=decision,
                    comment=comment,
                    prior_state=prior_state,
                    resulting_state=resulting_state,
                    capability_code=attempt.decision_capability,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                    created_by=actor,
                )
                attempt.closed_at = gate_decision.decided_at
                attempt.closed_by = actor
                attempt.closure_code = decision
                attempt.save(update_fields=["closed_at", "closed_by", "closure_code", "updated_at"])
            _refresh_all_gate_states_locked(locked_package)
            action = (
                AuditEvent.Action.GATE_PASSED
                if decision == GateDecision.Outcome.PASSED
                else AuditEvent.Action.GATE_FAILED
            )
            _audit_gate(
                action,
                gate_decision,
                actor,
                package=locked_package,
                attempt=attempt,
                evaluation=evaluation,
                prior_state=prior_state,
                resulting_state=resulting_state,
                capability_code=attempt.decision_capability,
                decision_code=decision,
                reason_code="A1_APPROVED" if decision == GateDecision.Outcome.PASSED else "A1_RETURNED",
            )
            return gate_decision
    except _AuthorizationLost as exc:
        _deny_gate(actor, exc.capability_code, package)
    except GateExecutionError as exc:
        _audit_gate(
            AuditEvent.Action.GATE_ADVANCEMENT_BLOCKED,
            attempt_for_audit or package,
            actor,
            package=package,
            attempt=attempt_for_audit,
            evaluation=evaluation_for_audit,
            prior_state=compute_gate_state(package, "A1"),
            resulting_state=compute_gate_state(package, "A1"),
            capability_code=capability_code,
            denial_reason_code=exc.__class__.__name__.upper(),
        )
        raise
