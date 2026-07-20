"""Pure MarketMatch authority and commercial-visibility contracts.

This module deliberately performs no authentication, persistence, logging,
network access, localization, or model invocation.  Callers must resolve an
authenticated principal before evaluation and must project fields before any
downstream localization, search, AI, export, or sharing operation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
import re
from types import MappingProxyType
from typing import Any, Mapping


_CODE_RE = re.compile(r"[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*\Z", re.ASCII)
_FIELD_RE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*\Z", re.ASCII)
_ERROR_RE = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*\Z", re.ASCII)
_MAX_CODE_BYTES = 256
_DECISION_MARKER = object()
_CONTRACT_ERROR_CODES = frozenset(
    {
        "INVALID_AUDIT_TIMESTAMP",
        "INVALID_AUTHORIZATION_DECISION",
        "INVALID_AUTHORIZATION_REQUEST",
        "INVALID_PROJECTION_INPUT",
        "UNSAFE_PROJECTION_BEHAVIOR",
    }
)
_SENSITIVE_REFERENCE_TERMS = frozenset(
    {
        "address",
        "avenue",
        "cookie",
        "cost",
        "factory",
        "hash",
        "margin",
        "markup",
        "password",
        "secret",
        "session",
        "street",
        "supplier",
        "token",
    }
)


class PartyKind(str, Enum):
    PERSON = "person"
    ORGANIZATION = "organization"
    SERVICE = "service"


class ResourceClassification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class VisibilityMode(str, Enum):
    TRANSPARENT = "transparent"
    CONTROLLED_CONFIDENTIALITY = "controlled_confidentiality"


class ProjectionBehavior(str, Enum):
    OMIT = "omit"


class DerivedArtifactKind(str, Enum):
    TRANSLATION = "translation"
    SUMMARY = "summary"
    ANALYSIS = "analysis"
    EXPORT = "export"
    PREVIEW = "preview"
    SEARCH_PROJECTION = "search_projection"
    EMBEDDING = "embedding"
    REPORT = "report"


class AuthorityReason(str, Enum):
    ALLOWED = "ALLOWED"
    MISSING_PRINCIPAL = "MISSING_PRINCIPAL"
    INVALID_PRINCIPAL = "INVALID_PRINCIPAL"
    MISSING_RESOURCE_SCOPE = "MISSING_RESOURCE_SCOPE"
    AMBIGUOUS_SCOPE = "AMBIGUOUS_SCOPE"
    INVALID_RESOURCE = "INVALID_RESOURCE"
    INVALID_CLASSIFICATION = "INVALID_CLASSIFICATION"
    UNKNOWN_CAPABILITY = "UNKNOWN_CAPABILITY"
    MISSING_CAPABILITY = "MISSING_CAPABILITY"
    INACTIVE_ASSIGNMENT = "INACTIVE_ASSIGNMENT"
    EXPLICIT_DENY = "EXPLICIT_DENY"
    AMBIGUOUS_GRANT = "AMBIGUOUS_GRANT"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    CLASSIFICATION_DENIED = "CLASSIFICATION_DENIED"
    INVALID_POLICY = "INVALID_POLICY"
    SOURCE_NOT_AUTHORIZED = "SOURCE_NOT_AUTHORIZED"
    SOURCE_SCOPE_CONFLICT = "SOURCE_SCOPE_CONFLICT"
    SOURCE_POLICY_CONFLICT = "SOURCE_POLICY_CONFLICT"
    INVALID_DERIVATION = "INVALID_DERIVATION"
    EVALUATION_ERROR = "EVALUATION_ERROR"


class AuthorityContractError(ValueError):
    """Fixed-code contract error that never includes rejected values."""

    def __init__(self, code: str):
        self.code = (
            code
            if type(code) is str
            and _ERROR_RE.fullmatch(code) is not None
            and code in _CONTRACT_ERROR_CODES
            else "INVALID_AUTHORITY_CONTRACT"
        )
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class PartyReference:
    """Stable language-neutral actor reference containing no role authority."""

    party_id: str
    kind: PartyKind


@dataclass(frozen=True, slots=True)
class AuthorityScope:
    """Explicit dimensions over which an assignment or resource applies."""

    organization_id: str | None = None
    product_id: str | None = None
    workspace_id: str | None = None
    project_id: str | None = None
    resource_id: str | None = None
    owner_party_id: str | None = None
    global_scope: bool = False


@dataclass(frozen=True, slots=True)
class ScopedRoleAssignment:
    assignment_id: str
    party_id: str
    role_code: str
    scope: AuthorityScope
    active: bool = True


@dataclass(frozen=True, slots=True)
class ScopedCapabilityGrant:
    capability_code: str
    scope: AuthorityScope
    source_ref: str
    assignment_id: str | None = None
    active: bool = True
    allow: bool = True


@dataclass(frozen=True, slots=True)
class PrincipalContext:
    """Minimum effectively immutable input derived from authoritative auth."""

    principal_ref: str
    party: PartyReference
    role_assignments: tuple[ScopedRoleAssignment, ...] = ()
    capability_grants: tuple[ScopedCapabilityGrant, ...] = ()
    authenticated: bool = True
    administrator: bool = False


@dataclass(frozen=True, slots=True)
class ResourceContext:
    resource_type: str
    resource_id: str
    scope: AuthorityScope
    classification: ResourceClassification
    available_fields: frozenset[str]


@dataclass(frozen=True, slots=True)
class VisibilityPolicy:
    policy_id: str
    version: str
    mode: VisibilityMode
    capabilities: frozenset[str]
    visible_fields: frozenset[str]
    allowed_classifications: frozenset[ResourceClassification]
    global_capabilities: frozenset[str] = frozenset()
    projection_behavior: ProjectionBehavior = ProjectionBehavior.OMIT


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    principal: PrincipalContext | None
    capability_code: str
    resource: ResourceContext
    requested_fields: frozenset[str] = frozenset()
    projection_purpose: str = "access"
    presentation_locale: str | None = None
    display_timezone: str | None = None


@dataclass(frozen=True, slots=True, init=False)
class AuthorizationDecision:
    allowed: bool
    reason_code: AuthorityReason
    effective_scope: AuthorityScope | None
    visible_fields: frozenset[str]
    omitted_fields: frozenset[str]
    policy_id: str
    policy_version: str
    principal_ref: str | None
    action_code: str
    resource_type: str
    resource_id: str
    _kernel_marker: object = field(repr=False, compare=False)
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls):
        del cls
        raise AuthorityContractError("INVALID_AUTHORIZATION_DECISION")


@dataclass(frozen=True, slots=True)
class SourceVisibility:
    source_resource_id: str
    classification: ResourceClassification
    visible_fields: frozenset[str]
    required_scopes: tuple[AuthorityScope, ...]
    authorized: bool
    policy_id: str
    policy_version: str


@dataclass(frozen=True, slots=True)
class DerivedVisibility:
    allowed: bool
    reason_code: AuthorityReason
    artifact_kind: DerivedArtifactKind
    classification: ResourceClassification
    visible_fields: frozenset[str]
    required_scopes: tuple[AuthorityScope, ...]
    source_resource_ids: tuple[str, ...]
    source_policies: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class DecisionAuditSummary:
    request_id: str | None
    principal_ref: str | None
    action_code: str
    resource_type: str
    resource_id: str
    allowed: bool
    reason_code: str
    policy_id: str
    policy_version: str
    timestamp: str | None


def _is_code(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        return len(value.encode("ascii")) <= _MAX_CODE_BYTES and _CODE_RE.fullmatch(value) is not None
    except (UnicodeError, ValueError):
        return False


def _is_field(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        return len(value.encode("ascii")) <= _MAX_CODE_BYTES and _FIELD_RE.fullmatch(value) is not None
    except (UnicodeError, ValueError):
        return False


def _is_safe_resource_reference(value: object) -> bool:
    if not _is_code(value):
        return False
    normalized = value.replace(".", "-").replace("_", "-").replace(":", "-")
    parts = frozenset(part for part in normalized.split("-") if part)
    return not (parts & _SENSITIVE_REFERENCE_TERMS)


def _scope_reason(scope: object, *, required: bool) -> AuthorityReason | None:
    if type(scope) is not AuthorityScope:
        return AuthorityReason.AMBIGUOUS_SCOPE
    dimensions = (
        scope.organization_id,
        scope.product_id,
        scope.workspace_id,
        scope.project_id,
        scope.resource_id,
        scope.owner_party_id,
    )
    if type(scope.global_scope) is not bool:
        return AuthorityReason.AMBIGUOUS_SCOPE
    if any(value is not None and not _is_code(value) for value in dimensions):
        return AuthorityReason.AMBIGUOUS_SCOPE
    if scope.resource_id is not None and not _is_safe_resource_reference(scope.resource_id):
        return AuthorityReason.AMBIGUOUS_SCOPE
    if scope.global_scope and any(value is not None for value in dimensions):
        return AuthorityReason.AMBIGUOUS_SCOPE
    if required and not scope.global_scope and all(value is None for value in dimensions):
        return AuthorityReason.MISSING_RESOURCE_SCOPE
    return None


def _scope_covers(grant_scope: AuthorityScope, resource_scope: AuthorityScope) -> bool:
    if grant_scope.global_scope:
        return True
    # V1 has no authoritative hierarchy registry capable of proving that an
    # organization-level grant owns every project, workspace, or resource below
    # it.  Exact dimensional equality is therefore the only safe non-global
    # match; omitted dimensions never become implicit wildcards.
    return grant_scope == resource_scope


def _safe_decision_value(value: object) -> str:
    return value if _is_code(value) else "invalid"


def _safe_audit_identifier(value: object) -> str | None:
    if not _is_code(value):
        return None
    try:
        if len(value.encode("ascii")) > 128:
            return None
    except (UnicodeError, ValueError):
        return None
    normalized = value.replace(".", "-").replace("_", "-").replace(":", "-")
    parts = frozenset(part for part in normalized.split("-") if part)
    if parts & _SENSITIVE_REFERENCE_TERMS:
        return None
    return value


def _new_authorization_decision(**values: object) -> AuthorizationDecision:
    decision = object.__new__(AuthorizationDecision)
    for field_name in (
        "allowed",
        "reason_code",
        "effective_scope",
        "visible_fields",
        "omitted_fields",
        "policy_id",
        "policy_version",
        "principal_ref",
        "action_code",
        "resource_type",
        "resource_id",
    ):
        object.__setattr__(decision, field_name, values[field_name])
    object.__setattr__(decision, "_kernel_marker", _DECISION_MARKER)
    object.__setattr__(decision, "_integrity", _decision_integrity(decision))
    return decision


def _decision_integrity(decision: AuthorizationDecision) -> tuple[object, ...]:
    scope = decision.effective_scope
    scope_integrity = None
    if type(scope) is AuthorityScope:
        scope_integrity = (
            scope.organization_id,
            scope.product_id,
            scope.workspace_id,
            scope.project_id,
            scope.resource_id,
            scope.owner_party_id,
            scope.global_scope,
        )
    return (
        decision.allowed,
        decision.reason_code,
        scope_integrity,
        decision.visible_fields,
        decision.omitted_fields,
        decision.policy_id,
        decision.policy_version,
        decision.principal_ref,
        decision.action_code,
        decision.resource_type,
        decision.resource_id,
    )


def _decision(
    request: AuthorizationRequest,
    policy: object,
    *,
    allowed: bool,
    reason: AuthorityReason,
    effective_scope: AuthorityScope | None = None,
    visible_fields: frozenset[str] = frozenset(),
    omitted_fields: frozenset[str] | None = None,
) -> AuthorizationDecision:
    resource = request.resource if type(request.resource) is ResourceContext else None
    principal = request.principal if type(request.principal) is PrincipalContext else None
    valid_policy = policy if type(policy) is VisibilityPolicy else None
    requested = request.requested_fields if type(request.requested_fields) is frozenset else frozenset()
    return _new_authorization_decision(
        allowed=allowed,
        reason_code=reason,
        effective_scope=effective_scope,
        visible_fields=visible_fields,
        omitted_fields=requested if omitted_fields is None else omitted_fields,
        policy_id=_safe_decision_value(valid_policy.policy_id) if valid_policy else "invalid",
        policy_version=_safe_decision_value(valid_policy.version) if valid_policy else "invalid",
        principal_ref=principal.principal_ref if principal and _is_code(principal.principal_ref) else None,
        action_code=_safe_decision_value(request.capability_code),
        resource_type=_safe_decision_value(resource.resource_type) if resource else "invalid",
        resource_id=(
            resource.resource_id
            if resource and _is_safe_resource_reference(resource.resource_id)
            else "invalid"
        ),
    )


def _valid_party(party: object) -> bool:
    return (
        type(party) is PartyReference
        and _is_code(party.party_id)
        and type(party.kind) is PartyKind
    )


def _valid_policy(policy: object) -> bool:
    if type(policy) is not VisibilityPolicy:
        return False
    if (
        not _is_code(policy.policy_id)
        or not _is_code(policy.version)
        or type(policy.mode) is not VisibilityMode
        or type(policy.projection_behavior) is not ProjectionBehavior
        or policy.projection_behavior is not ProjectionBehavior.OMIT
        or type(policy.capabilities) is not frozenset
        or type(policy.global_capabilities) is not frozenset
        or type(policy.visible_fields) is not frozenset
        or type(policy.allowed_classifications) is not frozenset
    ):
        return False
    return (
        bool(policy.capabilities)
        and all(_is_code(value) for value in policy.capabilities)
        and all(_is_code(value) for value in policy.global_capabilities)
        and policy.global_capabilities <= policy.capabilities
        and all(_is_field(value) for value in policy.visible_fields)
        and bool(policy.allowed_classifications)
        and all(type(value) is ResourceClassification for value in policy.allowed_classifications)
    )


def _valid_role(assignment: object, party_id: str) -> bool:
    return (
        type(assignment) is ScopedRoleAssignment
        and _is_code(assignment.assignment_id)
        and assignment.party_id == party_id
        and _is_code(assignment.role_code)
        and type(assignment.active) is bool
        and _scope_reason(assignment.scope, required=True) is None
    )


def _valid_grant(grant: object) -> bool:
    return (
        type(grant) is ScopedCapabilityGrant
        and _is_code(grant.capability_code)
        and _is_code(grant.source_ref)
        and (grant.assignment_id is None or _is_code(grant.assignment_id))
        and type(grant.active) is bool
        and type(grant.allow) is bool
        and _scope_reason(grant.scope, required=True) is None
    )


def _valid_decision(decision: object) -> bool:
    if type(decision) is not AuthorizationDecision:
        return False
    try:
        if (
            decision._kernel_marker is not _DECISION_MARKER
            or decision._integrity != _decision_integrity(decision)
        ):
            return False
        fields_valid = (
            type(decision.visible_fields) is frozenset
            and type(decision.omitted_fields) is frozenset
            and all(_is_field(value) for value in decision.visible_fields)
            and all(_is_field(value) for value in decision.omitted_fields)
            and not (decision.visible_fields & decision.omitted_fields)
        )
        identifiers_valid = (
            _is_code(decision.policy_id)
            and _is_code(decision.policy_version)
            and (decision.principal_ref is None or _is_code(decision.principal_ref))
            and _is_code(decision.action_code)
            and _is_code(decision.resource_type)
            and _is_code(decision.resource_id)
        )
        state_valid = type(decision.allowed) is bool and type(decision.reason_code) is AuthorityReason
        if decision.allowed:
            state_valid = (
                state_valid
                and decision.reason_code is AuthorityReason.ALLOWED
                and _scope_reason(decision.effective_scope, required=True) is None
            )
        else:
            state_valid = (
                state_valid
                and decision.reason_code is not AuthorityReason.ALLOWED
                and decision.effective_scope is None
                and not decision.visible_fields
            )
        return fields_valid and identifiers_valid and state_valid
    except Exception:
        return False


def _flat_projection_value(value: object) -> bool:
    if value is None or type(value) in (str, bool, int):
        return True
    return type(value) is float and math.isfinite(value)


def evaluate_authorization(
    request: AuthorizationRequest,
    policy: VisibilityPolicy,
) -> AuthorizationDecision:
    """Evaluate one request deterministically and deny on every uncertainty."""

    try:
        if type(request) is not AuthorizationRequest:
            raise AuthorityContractError("INVALID_AUTHORIZATION_REQUEST")
        if not _valid_policy(policy):
            return _decision(request, policy, allowed=False, reason=AuthorityReason.INVALID_POLICY)
        principal = request.principal
        if principal is None:
            return _decision(request, policy, allowed=False, reason=AuthorityReason.MISSING_PRINCIPAL)
        if (
            type(principal) is not PrincipalContext
            or principal.authenticated is not True
            or type(principal.administrator) is not bool
            or not _is_code(principal.principal_ref)
            or not _valid_party(principal.party)
            or type(principal.role_assignments) is not tuple
            or type(principal.capability_grants) is not tuple
        ):
            return _decision(request, policy, allowed=False, reason=AuthorityReason.INVALID_PRINCIPAL)
        if (
            any(not _valid_role(item, principal.party.party_id) for item in principal.role_assignments)
            or any(not _valid_grant(item) for item in principal.capability_grants)
            or len({item.assignment_id for item in principal.role_assignments})
            != len(principal.role_assignments)
        ):
            return _decision(request, policy, allowed=False, reason=AuthorityReason.INVALID_PRINCIPAL)

        resource = request.resource
        if (
            type(resource) is not ResourceContext
            or not _is_code(resource.resource_type)
            or not _is_safe_resource_reference(resource.resource_id)
            or type(resource.available_fields) is not frozenset
            or not all(_is_field(value) for value in resource.available_fields)
        ):
            return _decision(request, policy, allowed=False, reason=AuthorityReason.INVALID_RESOURCE)
        scope_reason = _scope_reason(resource.scope, required=True)
        if scope_reason is not None:
            return _decision(request, policy, allowed=False, reason=scope_reason)
        if type(resource.classification) is not ResourceClassification:
            return _decision(request, policy, allowed=False, reason=AuthorityReason.INVALID_CLASSIFICATION)
        if resource.classification not in policy.allowed_classifications:
            return _decision(request, policy, allowed=False, reason=AuthorityReason.CLASSIFICATION_DENIED)

        if not _is_code(request.capability_code) or request.capability_code not in policy.capabilities:
            return _decision(request, policy, allowed=False, reason=AuthorityReason.UNKNOWN_CAPABILITY)
        if (
            type(request.requested_fields) is not frozenset
            or not all(_is_field(value) for value in request.requested_fields)
            or not _is_code(request.projection_purpose)
        ):
            return _decision(request, policy, allowed=False, reason=AuthorityReason.INVALID_RESOURCE)

        matching = [grant for grant in principal.capability_grants if grant.capability_code == request.capability_code]
        if not matching:
            return _decision(request, policy, allowed=False, reason=AuthorityReason.MISSING_CAPABILITY)
        scoped = [grant for grant in matching if _scope_covers(grant.scope, resource.scope)]
        if not scoped:
            return _decision(request, policy, allowed=False, reason=AuthorityReason.SCOPE_MISMATCH)
        if len(scoped) != 1:
            return _decision(request, policy, allowed=False, reason=AuthorityReason.AMBIGUOUS_GRANT)

        grant = scoped[0]
        if grant.scope.global_scope and request.capability_code not in policy.global_capabilities:
            return _decision(request, policy, allowed=False, reason=AuthorityReason.SCOPE_MISMATCH)
        if grant.active is not True:
            return _decision(request, policy, allowed=False, reason=AuthorityReason.INACTIVE_ASSIGNMENT)
        if grant.allow is not True:
            return _decision(request, policy, allowed=False, reason=AuthorityReason.EXPLICIT_DENY)
        if grant.assignment_id is not None:
            assignments = [
                assignment
                for assignment in principal.role_assignments
                if assignment.assignment_id == grant.assignment_id
            ]
            if len(assignments) != 1 or assignments[0].active is not True:
                return _decision(request, policy, allowed=False, reason=AuthorityReason.INACTIVE_ASSIGNMENT)
            if not _scope_covers(assignments[0].scope, resource.scope):
                return _decision(request, policy, allowed=False, reason=AuthorityReason.SCOPE_MISMATCH)

        visible = frozenset(
            request.requested_fields
            & resource.available_fields
            & policy.visible_fields
        )
        omitted = frozenset(request.requested_fields - visible)
        return _decision(
            request,
            policy,
            allowed=True,
            reason=AuthorityReason.ALLOWED,
            effective_scope=resource.scope,
            visible_fields=visible,
            omitted_fields=omitted,
        )
    except Exception:
        if type(request) is AuthorizationRequest:
            return _decision(request, policy, allowed=False, reason=AuthorityReason.EVALUATION_ERROR)
        return _new_authorization_decision(
            allowed=False,
            reason_code=AuthorityReason.EVALUATION_ERROR,
            effective_scope=None,
            visible_fields=frozenset(),
            omitted_fields=frozenset(),
            policy_id="invalid",
            policy_version="invalid",
            principal_ref=None,
            action_code="invalid",
            resource_type="invalid",
            resource_id="invalid",
        )


def project_authorized_fields(
    record: Mapping[str, Any],
    decision: AuthorizationDecision,
    *,
    behavior: ProjectionBehavior = ProjectionBehavior.OMIT,
) -> dict[str, Any]:
    """Return a new mapping containing only fields explicitly authorized."""

    if behavior is not ProjectionBehavior.OMIT:
        raise AuthorityContractError("UNSAFE_PROJECTION_BEHAVIOR")
    if not isinstance(record, Mapping) or not _valid_decision(decision):
        raise AuthorityContractError("INVALID_PROJECTION_INPUT")
    if decision.allowed is not True:
        return {}
    try:
        # V1 is intentionally flat-only.  Nested or otherwise mutable values
        # are omitted even when their top-level field is visible; passing them
        # through would permit hidden nested fields and mutable aliasing.
        return {
            key: value
            for key, value in record.items()
            if type(key) is str
            and key in decision.visible_fields
            and _flat_projection_value(value)
        }
    except Exception:
        raise AuthorityContractError("INVALID_PROJECTION_INPUT") from None


_CLASSIFICATION_RANK = MappingProxyType(
    {
        ResourceClassification.PUBLIC: 0,
        ResourceClassification.INTERNAL: 1,
        ResourceClassification.CONFIDENTIAL: 2,
        ResourceClassification.RESTRICTED: 3,
    }
)


def _derived_denial(
    *,
    reason: AuthorityReason,
    artifact_kind: object,
) -> DerivedVisibility:
    return DerivedVisibility(
        allowed=False,
        reason_code=reason,
        artifact_kind=(
            artifact_kind
            if type(artifact_kind) is DerivedArtifactKind
            else DerivedArtifactKind.ANALYSIS
        ),
        classification=ResourceClassification.RESTRICTED,
        visible_fields=frozenset(),
        required_scopes=(),
        source_resource_ids=(),
        source_policies=(),
    )


def _source_scopes_conflict(scopes: tuple[AuthorityScope, ...]) -> bool:
    # Different source records are expected, so resource_id is preserved but
    # is not itself a conflict.  All tenancy/ownership dimensions must agree.
    for field_name in (
        "organization_id",
        "product_id",
        "workspace_id",
        "project_id",
        "owner_party_id",
    ):
        values = {
            getattr(scope, field_name)
            for scope in scopes
            if not scope.global_scope and getattr(scope, field_name) is not None
        }
        if len(values) > 1:
            return True
    return False


def inherit_derived_visibility(
    sources: tuple[SourceVisibility, ...],
    *,
    artifact_kind: DerivedArtifactKind,
) -> DerivedVisibility:
    """Preserve all source scopes and intersect fields for derived artifacts."""

    if type(artifact_kind) is not DerivedArtifactKind or type(sources) is not tuple or not sources:
        return _derived_denial(
            reason=AuthorityReason.INVALID_DERIVATION,
            artifact_kind=artifact_kind,
        )

    validated: list[SourceVisibility] = []
    for source in sources:
        if (
            type(source) is not SourceVisibility
            or not _is_safe_resource_reference(source.source_resource_id)
            or type(source.classification) is not ResourceClassification
            or type(source.visible_fields) is not frozenset
            or not all(_is_field(value) for value in source.visible_fields)
            or type(source.required_scopes) is not tuple
            or not source.required_scopes
            or any(_scope_reason(scope, required=True) is not None for scope in source.required_scopes)
            or source.authorized is not True
            or not _is_code(source.policy_id)
            or not _is_code(source.policy_version)
        ):
            return _derived_denial(
                reason=AuthorityReason.SOURCE_NOT_AUTHORIZED,
                artifact_kind=artifact_kind,
            )
        validated.append(source)

    policies = {(source.policy_id, source.policy_version) for source in validated}
    if len(policies) != 1:
        return _derived_denial(
            reason=AuthorityReason.SOURCE_POLICY_CONFLICT,
            artifact_kind=artifact_kind,
        )

    visible = set(validated[0].visible_fields)
    scopes: list[AuthorityScope] = []
    source_ids: list[str] = []
    classification = ResourceClassification.PUBLIC
    for source in validated:
        visible.intersection_update(source.visible_fields)
        if _CLASSIFICATION_RANK[source.classification] > _CLASSIFICATION_RANK[classification]:
            classification = source.classification
        source_ids.append(source.source_resource_id)
        for scope in source.required_scopes:
            if scope not in scopes:
                scopes.append(scope)

    if _source_scopes_conflict(tuple(scopes)):
        return _derived_denial(
            reason=AuthorityReason.SOURCE_SCOPE_CONFLICT,
            artifact_kind=artifact_kind,
        )

    return DerivedVisibility(
        allowed=True,
        reason_code=AuthorityReason.ALLOWED,
        artifact_kind=artifact_kind,
        classification=classification,
        visible_fields=frozenset(visible),
        required_scopes=tuple(scopes),
        source_resource_ids=tuple(source_ids),
        source_policies=tuple(sorted(policies)),
    )


def build_safe_audit_summary(
    decision: AuthorizationDecision,
    *,
    request_id: str | None = None,
    timestamp: datetime | None = None,
) -> DecisionAuditSummary:
    """Build a value-free summary safe for structured decision logging."""

    if not _valid_decision(decision):
        raise AuthorityContractError("INVALID_AUTHORIZATION_DECISION")
    safe_request_id = None if request_id is None else _safe_audit_identifier(request_id)
    safe_timestamp = None
    if timestamp is not None:
        if type(timestamp) is not datetime or timestamp.tzinfo is None:
            raise AuthorityContractError("INVALID_AUDIT_TIMESTAMP")
        safe_timestamp = timestamp.astimezone(timezone.utc).isoformat()
    return DecisionAuditSummary(
        request_id=safe_request_id,
        principal_ref=_safe_audit_identifier(decision.principal_ref),
        action_code=_safe_audit_identifier(decision.action_code) or "invalid",
        resource_type=_safe_audit_identifier(decision.resource_type) or "invalid",
        resource_id=_safe_audit_identifier(decision.resource_id) or "invalid",
        allowed=decision.allowed,
        reason_code=decision.reason_code.value,
        policy_id=_safe_audit_identifier(decision.policy_id) or "invalid",
        policy_version=_safe_audit_identifier(decision.policy_version) or "invalid",
        timestamp=safe_timestamp,
    )


__all__ = (
    "AuthorityContractError",
    "AuthorityReason",
    "AuthorityScope",
    "AuthorizationDecision",
    "AuthorizationRequest",
    "DecisionAuditSummary",
    "DerivedArtifactKind",
    "DerivedVisibility",
    "PartyKind",
    "PartyReference",
    "PrincipalContext",
    "ProjectionBehavior",
    "ResourceClassification",
    "ResourceContext",
    "ScopedCapabilityGrant",
    "ScopedRoleAssignment",
    "SourceVisibility",
    "VisibilityMode",
    "VisibilityPolicy",
    "build_safe_audit_summary",
    "evaluate_authorization",
    "inherit_derived_visibility",
    "project_authorized_fields",
)
