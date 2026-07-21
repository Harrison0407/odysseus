"""Authorized services for the Increment 1 policy/pinning foundation only."""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.governance.models import ALL_CAPABILITY_CODES, CapabilityGrant
from apps.governance.services import AuthorizationDenied, has_capability, log_denied_attempt

from .models import (
    GATE_CODES,
    GatePolicy,
    GatePolicyVersion,
    PackagePolicyAssignment,
    _allow_controlled_assignment_write,
    _allow_controlled_policy_write,
    _allow_controlled_version_write,
)

PUBLISH_GATE_POLICY = "PUBLISH_GATE_POLICY"
CREATE_PROCUREMENT_GATE_ATTEMPT = "CREATE_PROCUREMENT_GATE_ATTEMPT"
ASSIGN_GATE_POLICY = "ASSIGN_GATE_POLICY"
EXEMPT_PACKAGE_FROM_PROCUREMENT_GATES = "EXEMPT_PACKAGE_FROM_PROCUREMENT_GATES"

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
