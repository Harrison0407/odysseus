"""Service boundary for the procurement gate policy configuration and
package-pinning foundation (Milestone 1, Increment 1). Every mutation
Charter Section 3/4 defines goes through exactly one function here --
never a direct model save() from a view, admin, or migration.

Gate execution (opening/deciding a GateAttempt, overrides, freeze/change
control) is out of scope for this increment and is not implemented here.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.governance.models import ALL_CAPABILITY_CODES, CapabilityGrant
from apps.governance.services import AuthorizationDenied, has_capability, log_denied_attempt

from .models import GATE_CODES, GatePolicy, GatePolicyVersion, PackagePolicyAssignment

PUBLISH_GATE_POLICY = "PUBLISH_GATE_POLICY"
CREATE_PROCUREMENT_GATE_ATTEMPT = "CREATE_PROCUREMENT_GATE_ATTEMPT"

_GATE_SCHEMA_ENTRY_FIELDS = {
    "evidence_requirement_codes",
    "decision_capability",
    "attempt_creation_capability",
    "overridable",
    "non_overridable_requirements",
    "override_satisfies_successor_predecessor",
}


class GatePolicyValidationError(Exception):
    """Raised when a gate_schema, or an input to a policy operation, fails
    Charter-defined validation."""


class GatePolicyStateError(Exception):
    """Raised for an invalid state transition -- double-publish, edit
    after publish, withdraw a non-published version, re-pin a package,
    zero usable canonical default, etc."""


# ---------------------------------------------------------------------------
# Authorization helpers
# ---------------------------------------------------------------------------


def _actor_holds_platform_policy_capability(actor) -> bool:
    """PUBLISH_GATE_POLICY held as a pure platform-scoped grant
    (organization, package, and role_assignment all NULL).

    A canonical/platform-scoped GatePolicy (organization IS NULL) has no
    owning organization to scope a has_capability(..., organization=...)
    call to, and Charter Section 13 requires every apps.procurement_gates
    call to has_capability carry an explicit package= or organization=
    scope -- an unscoped call is a Milestone 1 authorization-architecture
    violation. This helper resolves the platform-scope case directly
    against CapabilityGrant instead of routing an unscoped call through
    has_capability.
    """
    if actor is None or not getattr(actor, "is_authenticated", False):
        return False
    today = timezone.now().date()
    grants = CapabilityGrant.objects.filter(
        user=actor, capability_code=PUBLISH_GATE_POLICY, is_active=True,
        organization__isnull=True, package__isnull=True, role_assignment__isnull=True,
    )
    return any(grant.is_currently_active(on_date=today) for grant in grants)


def _require_policy_administration_capability(policy: GatePolicy, actor, *, resource=None):
    if policy.organization_id is not None:
        authorized = has_capability(actor, PUBLISH_GATE_POLICY, organization=policy.organization)
    else:
        authorized = _actor_holds_platform_policy_capability(actor)
    if not authorized:
        log_denied_attempt(
            actor, PUBLISH_GATE_POLICY, resource=resource if resource is not None else policy,
            required_capability=PUBLISH_GATE_POLICY,
        )
        raise AuthorizationDenied("Not authorized to administer this gate policy.")


# ---------------------------------------------------------------------------
# gate_schema validation (Charter Section 3.2, binding)
# ---------------------------------------------------------------------------


def validate_gate_schema(gate_schema) -> None:
    """Enforces the Charter Section 3.2 gate-schema completeness rule.
    Called by publish_policy_version; exposed separately so draft authors
    and tests can validate before attempting to publish."""

    if not isinstance(gate_schema, dict):
        raise GatePolicyValidationError("gate_schema must be a JSON object.")

    provided_codes = set(gate_schema.keys())
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

        entry_keys = set(entry.keys())
        missing_fields = _GATE_SCHEMA_ENTRY_FIELDS - entry_keys
        if missing_fields:
            raise GatePolicyValidationError(
                f"gate_schema[{gate_code}] is missing required fields: {sorted(missing_fields)}"
            )
        extra_fields = entry_keys - _GATE_SCHEMA_ENTRY_FIELDS
        if extra_fields:
            raise GatePolicyValidationError(
                f"gate_schema[{gate_code}] contains unrecognized fields: {sorted(extra_fields)}"
            )

        codes = entry["evidence_requirement_codes"]
        if not isinstance(codes, list) or not all(isinstance(code, str) and code for code in codes):
            raise GatePolicyValidationError(
                f"gate_schema[{gate_code}].evidence_requirement_codes must be a list of non-empty strings "
                "(an empty list is valid -- a gate may require no evidence)."
            )

        decision_capability = entry["decision_capability"]
        if not isinstance(decision_capability, str) or not decision_capability:
            raise GatePolicyValidationError(f"gate_schema[{gate_code}].decision_capability must be a non-empty string.")

        attempt_capability = entry["attempt_creation_capability"]
        if not isinstance(attempt_capability, str) or not attempt_capability:
            raise GatePolicyValidationError(
                f"gate_schema[{gate_code}].attempt_creation_capability is required, non-null, and non-empty."
            )
        if attempt_capability not in ALL_CAPABILITY_CODES:
            raise GatePolicyValidationError(
                f"gate_schema[{gate_code}].attempt_creation_capability {attempt_capability!r} "
                "is not a registered capability code."
            )

        overridable = entry["overridable"]
        if not isinstance(overridable, bool):
            raise GatePolicyValidationError(f"gate_schema[{gate_code}].overridable must be a boolean.")
        if gate_code == "A2" and overridable:
            raise GatePolicyValidationError(
                "gate_schema[A2].overridable must always be False (Charter Section 12.1) -- "
                "A2 is never overridable, with no policy opt-in."
            )

        non_overridable = entry["non_overridable_requirements"]
        if not isinstance(non_overridable, list) or not all(isinstance(code, str) for code in non_overridable):
            raise GatePolicyValidationError(
                f"gate_schema[{gate_code}].non_overridable_requirements must be a list of strings."
            )
        if not set(non_overridable).issubset(set(codes)):
            raise GatePolicyValidationError(
                f"gate_schema[{gate_code}].non_overridable_requirements must be a subset of its own "
                "evidence_requirement_codes."
            )

        satisfies_successor = entry["override_satisfies_successor_predecessor"]
        if not isinstance(satisfies_successor, bool):
            raise GatePolicyValidationError(
                f"gate_schema[{gate_code}].override_satisfies_successor_predecessor must be a boolean."
            )


def canonical_gate_schema() -> dict:
    """The gate_schema shipped for the canonical Version 1 policy (Charter
    Section 4.2). No evidence requirements and no overrides -- evidence
    reuse (Section 5) and the override mechanism (Section 11) are later,
    not-yet-authorized increments; this schema is deliberately the
    simplest one that is Charter-valid today."""

    return {
        gate_code: {
            "evidence_requirement_codes": [],
            "decision_capability": "APPROVE_GATE",
            "attempt_creation_capability": CREATE_PROCUREMENT_GATE_ATTEMPT,
            "overridable": False,
            "non_overridable_requirements": [],
            "override_satisfies_successor_predecessor": False,
        }
        for gate_code in GATE_CODES
    }


# ---------------------------------------------------------------------------
# GatePolicy / GatePolicyVersion administration
# ---------------------------------------------------------------------------


def create_gate_policy(*, organization, code, name, actor, is_active=True) -> GatePolicy:
    if organization is not None:
        authorized = has_capability(actor, PUBLISH_GATE_POLICY, organization=organization)
    else:
        authorized = _actor_holds_platform_policy_capability(actor)
    if not authorized:
        log_denied_attempt(actor, PUBLISH_GATE_POLICY, required_capability=PUBLISH_GATE_POLICY)
        raise AuthorizationDenied("Not authorized to create a gate policy.")

    with transaction.atomic():
        policy = GatePolicy.objects.create(
            organization=organization, code=code, name=name, is_active=is_active, created_by=actor,
        )
        audit.log(
            AuditEvent.Action.GATE_POLICY_CREATED, instance=policy, actor=actor,
            summary=f"Gate policy created: {code}",
            organization_id=str(organization.pk) if organization is not None else None,
            prior_state=None, resulting_state="CREATED",
        )
    return policy


def set_canonical_default(policy: GatePolicy, actor) -> GatePolicy:
    """Flags a platform-scoped GatePolicy as the canonical default
    (Charter Section 3.3). Rejects any organization-owned policy outright
    -- enforced here (service layer) and again by GatePolicy.save() as a
    backstop; a second attempt while one already holds the flag is
    rejected by the database's conditional unique constraint."""

    if not _actor_holds_platform_policy_capability(actor):
        log_denied_attempt(actor, PUBLISH_GATE_POLICY, resource=policy, required_capability=PUBLISH_GATE_POLICY)
        raise AuthorizationDenied("Not authorized to set a canonical default gate policy.")

    with transaction.atomic():
        locked = GatePolicy.objects.select_for_update().get(pk=policy.pk)
        if locked.organization_id is not None:
            raise GatePolicyValidationError("An organization-owned GatePolicy can never be canonical.")
        locked.is_canonical_default = True
        locked.save(update_fields=["is_canonical_default", "updated_at"])
    return locked


def create_draft_policy_version(*, policy: GatePolicy, actor, gate_schema, supersedes=None) -> GatePolicyVersion:
    _require_policy_administration_capability(policy, actor)

    with transaction.atomic():
        locked_policy = GatePolicy.objects.select_for_update().get(pk=policy.pk)
        last_version_number = (
            GatePolicyVersion.objects.filter(policy=locked_policy)
            .order_by("-version_number")
            .values_list("version_number", flat=True)
            .first()
        ) or 0
        version = GatePolicyVersion.objects.create(
            policy=locked_policy, version_number=last_version_number + 1,
            status=GatePolicyVersion.Status.DRAFT, gate_schema=gate_schema, supersedes=supersedes,
            created_by=actor,
        )
    return version


def update_draft_policy_version(*, policy_version: GatePolicyVersion, actor, gate_schema) -> GatePolicyVersion:
    _require_policy_administration_capability(policy_version.policy, actor, resource=policy_version)

    with transaction.atomic():
        locked = GatePolicyVersion.objects.select_for_update().get(pk=policy_version.pk)
        if locked.status != GatePolicyVersion.Status.DRAFT:
            raise GatePolicyStateError("Only a DRAFT policy version may be edited.")
        locked.gate_schema = gate_schema
        locked.save(update_fields=["gate_schema", "updated_at"])
    return locked


def publish_policy_version(policy_version: GatePolicyVersion, actor) -> GatePolicyVersion:
    """One-way DRAFT -> PUBLISHED transition (Charter Section 3.2).
    Publication-time gate_schema completeness validation is the sole gate
    -- there is no partial/incremental publication."""

    _require_policy_administration_capability(policy_version.policy, actor, resource=policy_version)

    with transaction.atomic():
        locked = GatePolicyVersion.objects.select_for_update().get(pk=policy_version.pk)
        if locked.status != GatePolicyVersion.Status.DRAFT:
            raise GatePolicyStateError(f"Policy version is already {locked.status}; cannot publish.")

        validate_gate_schema(locked.gate_schema)

        locked.status = GatePolicyVersion.Status.PUBLISHED
        locked.published_at = timezone.now()
        locked.published_by = actor if getattr(actor, "pk", None) else None
        locked.save(update_fields=["status", "published_at", "published_by", "updated_at"])

        audit.log(
            AuditEvent.Action.GATE_POLICY_VERSION_PUBLISHED, instance=locked, actor=actor,
            summary=f"Gate policy version {locked.version_number} published for {locked.policy.code}",
            organization_id=str(locked.policy.organization_id) if locked.policy.organization_id else None,
            policy_version_id=str(locked.pk), prior_state="DRAFT", resulting_state="PUBLISHED",
        )
    return locked


def withdraw_policy_version(policy_version: GatePolicyVersion, actor, reason: str) -> GatePolicyVersion:
    """PUBLISHED -> WITHDRAWN (Charter Section 3.2). Never mutates
    gate_schema and never affects any existing PackagePolicyAssignment.
    Rejected outright if this is the canonical default's only published
    version with no replacement (the availability invariant, Section 3.3)."""

    if not reason:
        raise GatePolicyValidationError("A written reason is required to withdraw a gate policy version.")

    _require_policy_administration_capability(policy_version.policy, actor, resource=policy_version)

    with transaction.atomic():
        locked = GatePolicyVersion.objects.select_for_update().get(pk=policy_version.pk)
        if locked.status != GatePolicyVersion.Status.PUBLISHED:
            raise GatePolicyStateError("Only a PUBLISHED policy version may be withdrawn.")

        if locked.policy.is_canonical_default:
            remaining = GatePolicyVersion.objects.filter(
                policy__is_canonical_default=True, status=GatePolicyVersion.Status.PUBLISHED,
            ).exclude(pk=locked.pk)
            if not remaining.exists():
                raise GatePolicyStateError(
                    "Cannot withdraw the canonical default's only published version -- no replacement exists "
                    "(availability invariant, Charter Section 3.3)."
                )

        locked.status = GatePolicyVersion.Status.WITHDRAWN
        locked.withdrawal_reason = reason
        locked.save(update_fields=["status", "withdrawal_reason", "updated_at"])

        audit.log(
            AuditEvent.Action.GATE_POLICY_VERSION_WITHDRAWN, instance=locked, actor=actor,
            summary=f"Gate policy version {locked.version_number} withdrawn for {locked.policy.code}", reason=reason,
            organization_id=str(locked.policy.organization_id) if locked.policy.organization_id else None,
            policy_version_id=str(locked.pk), prior_state="PUBLISHED", resulting_state="WITHDRAWN",
        )
    return locked


# ---------------------------------------------------------------------------
# Canonical / organization-specific policy resolution (Charter Section 3.3)
# ---------------------------------------------------------------------------


def resolve_policy_version_for_package(*, organization, requested_policy_version=None) -> GatePolicyVersion:
    """Resolution order (Charter Section 3.3, fixed, not configurable):
    (1) an explicit policy version chosen by an authorized admin;
    (2) else the requesting organization's own active GatePolicy's latest
        PUBLISHED version, if one exists;
    (3) else the one policy with is_canonical_default=True's latest
        PUBLISHED version.
    """

    if requested_policy_version is not None:
        if requested_policy_version.status != GatePolicyVersion.Status.PUBLISHED:
            raise GatePolicyValidationError("Only a PUBLISHED policy version may be assigned to a package.")
        return requested_policy_version

    org_version = (
        GatePolicyVersion.objects.filter(
            policy__organization=organization, policy__is_active=True, status=GatePolicyVersion.Status.PUBLISHED,
        )
        .order_by("-policy__created_at", "-version_number")
        .first()
    )
    if org_version is not None:
        return org_version

    canonical_version = (
        GatePolicyVersion.objects.filter(
            policy__is_canonical_default=True, status=GatePolicyVersion.Status.PUBLISHED,
        )
        .order_by("-version_number")
        .first()
    )
    if canonical_version is None:
        raise GatePolicyStateError("No usable canonical default gate policy version exists.")
    return canonical_version


# ---------------------------------------------------------------------------
# Package pinning (Charter Section 3.4, 14.1a Pattern B)
# ---------------------------------------------------------------------------


def assign_policy_to_package(*, package, policy_version=None, pinned_by=None) -> PackagePolicyAssignment:
    """Permanent, one-time PackagePolicyAssignment creation. Locks the
    ProcurementPackage row first (Pattern B, Charter Section 14.1a) --
    there is no child row to lock yet. Idempotent: a second call for an
    already-pinned package returns the existing assignment rather than
    creating a duplicate or re-pinning (Section 3.4, 14.4)."""

    from apps.procurement.models import ProcurementPackage

    with transaction.atomic():
        locked_package = ProcurementPackage.objects.select_for_update().get(pk=package.pk)

        existing = PackagePolicyAssignment.objects.filter(package=locked_package, is_active=True).first()
        if existing is not None:
            return existing

        resolved_version = resolve_policy_version_for_package(
            organization=locked_package.organization, requested_policy_version=policy_version,
        )
        if resolved_version.status != GatePolicyVersion.Status.PUBLISHED:
            raise GatePolicyValidationError("A PackagePolicyAssignment must pin a PUBLISHED policy version.")

        assignment = PackagePolicyAssignment.objects.create(
            package=locked_package, policy_version=resolved_version, pinned_at=timezone.now(),
            pinned_by=pinned_by, is_active=True, created_by=pinned_by,
        )

        audit.log(
            AuditEvent.Action.GATE_POLICY_PINNED, instance=assignment, actor=pinned_by,
            summary=f"Gate policy version pinned to package {locked_package.code}",
            organization_id=str(locked_package.organization_id), package_id=str(locked_package.pk),
            policy_version_id=str(resolved_version.pk), prior_state=None, resulting_state="PINNED",
        )
    return assignment


def grant_gate_progression_exemption(assignment: PackagePolicyAssignment, actor, reason: str) -> PackagePolicyAssignment:
    """Administrative exemption (Charter Section 4.4) -- explicitly not a
    historical pass. Requires a written reason and is itself an audited
    action."""

    if not reason:
        raise GatePolicyValidationError("A written reason is required to grant a gate-progression exemption.")

    if not has_capability(actor, "APPROVE_GATE", package=assignment.package):
        log_denied_attempt(
            actor, "APPROVE_GATE", package=assignment.package, resource=assignment,
            required_capability="APPROVE_GATE",
        )
        raise AuthorizationDenied("Not authorized to grant a gate-progression exemption for this package.")

    with transaction.atomic():
        locked = PackagePolicyAssignment.objects.select_for_update().get(pk=assignment.pk)
        locked.gate_progression_exempt = True
        locked.exemption_reason = reason
        locked.exemption_granted_by = actor
        locked.exemption_granted_at = timezone.now()
        locked.save(
            update_fields=[
                "gate_progression_exempt", "exemption_reason", "exemption_granted_by", "exemption_granted_at",
                "updated_at",
            ]
        )
        audit.log(
            AuditEvent.Action.GATE_PROGRESSION_EXEMPTION_GRANTED, instance=locked, actor=actor,
            summary="Gate progression exemption granted", reason=reason,
            package_id=str(locked.package_id), prior_state="NOT_EXEMPT", resulting_state="EXEMPT",
        )
    return locked


# ---------------------------------------------------------------------------
# Deterministic canonical seeding + existing-package migration (Charter
# Section 4.2). System-initiated -- no actor, mirrors the pinned_by=NULL
# system-assignment convention. Safe to call repeatedly (idempotent).
# ---------------------------------------------------------------------------

CANONICAL_POLICY_CODE = "canonical-a1-a6"


def seed_canonical_policy() -> GatePolicy:
    """Idempotent. Creates the canonical default GatePolicy + initial
    PUBLISHED GatePolicyVersion if not already present. Works on an empty
    database and is safe to re-run."""

    policy = GatePolicy.objects.filter(is_canonical_default=True).first()
    if policy is None:
        policy = GatePolicy.objects.create(
            organization=None, code=CANONICAL_POLICY_CODE, name="Canonical A1-A6 Procurement Gate Policy",
            is_active=True, is_canonical_default=True,
        )
        audit.log(
            AuditEvent.Action.GATE_POLICY_CREATED, instance=policy, actor=None,
            summary="Canonical default gate policy seeded", organization_id=None,
            prior_state=None, resulting_state="CREATED",
        )

    version = GatePolicyVersion.objects.filter(policy=policy, version_number=1).first()
    if version is None:
        version = GatePolicyVersion.objects.create(
            policy=policy, version_number=1, status=GatePolicyVersion.Status.DRAFT, gate_schema=canonical_gate_schema(),
        )

    if version.status == GatePolicyVersion.Status.DRAFT:
        validate_gate_schema(version.gate_schema)
        version.status = GatePolicyVersion.Status.PUBLISHED
        version.published_at = timezone.now()
        version.published_by = None
        version.save(update_fields=["status", "published_at", "published_by", "updated_at"])
        audit.log(
            AuditEvent.Action.GATE_POLICY_VERSION_PUBLISHED, instance=version, actor=None,
            summary="Canonical gate policy version 1 published (system seed)",
            policy_version_id=str(version.pk), prior_state="DRAFT", resulting_state="PUBLISHED",
        )

    return policy


def assign_canonical_policy_to_existing_packages() -> int:
    """Existing-package migration step (Charter Section 4.2/4.5). Pins
    every ProcurementPackage that has no active PackagePolicyAssignment
    yet to the canonical default's published version, pinned_by=None.
    Idempotent -- safe to re-run; creates no duplicate assignments.
    Creates no GateAttempt/GateEvaluation/GateDecision and fabricates no
    PASSED result -- A1-A6 progression is represented only by the
    absence of any attempt.

    Read-only with respect to ProcurementPackage.Status, is_frozen,
    frozen_at, frozen_by, frozen_snapshot, and is_on_hold."""

    from apps.procurement.models import ProcurementPackage

    seed_canonical_policy()

    assigned = 0
    package_ids = ProcurementPackage.objects.exclude(
        policy_assignments__is_active=True,
    ).values_list("pk", flat=True)
    for package_id in list(package_ids):
        package = ProcurementPackage.objects.get(pk=package_id)
        assign_policy_to_package(package=package, pinned_by=None)
        assigned += 1
    return assigned
