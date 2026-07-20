"""Pure MarketMatch Tenancy & Membership Kernel V1.

This module defines isolation and membership contracts.  It does not
authenticate users, grant capabilities, persist data, provision tenants, send
invitations, execute work, or perform I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import re
from typing import Any, Mapping, NoReturn, Sequence

from src.marketmatch_authority import (
    AuthorityReason,
    AuthorityScope,
    AuthorizationDecision,
    DerivedArtifactKind,
    ProjectionBehavior,
    ResourceClassification,
    SourceVisibility,
    inherit_derived_visibility,
    project_authorized_fields,
)
from src.marketmatch_evidence import EvidenceRecord, evidence_metadata
from src.marketmatch_operational_context import (
    ContextType,
    HierarchyPolicy,
    OperationalContextRecord,
    context_metadata,
    validate_context_graph,
    validate_context_id,
)
from src.marketmatch_truth_accountability import (
    ActorKind,
    ActorReference,
    ApprovalRecord,
    DecisionRecord,
    OperationalEvent,
    validate_event_id,
)
from src.marketmatch_work_orchestration import (
    Assignment,
    ExecutorKind,
    WorkItem,
)


TENANCY_CONTRACT_VERSION = "marketmatch-tenancy-membership-v1"
TENANCY_POLICY_VERSION = "marketmatch-tenancy-membership-policy-v1"
MAX_RECORDS_PER_KIND = 2048
MAX_TRANSITIONS = 8192
MAX_LABEL_CHARS = 160
MAX_ID_BYTES = 160
MAX_CODE_BYTES = 128

_CODE_RE = re.compile(r"[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*\Z", re.ASCII)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_HOST_RE = re.compile(r"(?:[a-z0-9-]+\.)+[a-z]{2,}\Z", re.ASCII | re.IGNORECASE)
_SENSITIVE = frozenset({
    "address", "amount", "api", "apikey", "auth", "avenue", "bank",
    "bearer", "cookie", "cost", "credential", "database", "email",
    "factory", "header", "hostname", "invitation", "key",
    "hash", "margin", "markup", "password", "price", "request", "row", "secret",
    "session", "street", "supplier", "tax", "token", "usd", "url",
})


class TenancyErrorCode(str, Enum):
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    INVALID_TENANT = "INVALID_TENANT"
    INVALID_ORGANIZATION = "INVALID_ORGANIZATION"
    INVALID_MEMBERSHIP = "INVALID_MEMBERSHIP"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    INVALID_BINDING = "INVALID_BINDING"
    INVALID_TENANT_CONTEXT = "INVALID_TENANT_CONTEXT"
    INVALID_AUTHORITY_DECISION = "INVALID_AUTHORITY_DECISION"
    INVALID_PROJECTION = "INVALID_PROJECTION"
    INVALID_AUDIT = "INVALID_AUDIT"
    INVALID_VERSION = "INVALID_VERSION"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    INVALID_SCOPE = "INVALID_SCOPE"
    MISSING_REFERENCE = "MISSING_REFERENCE"
    DUPLICATE_IDENTIFIER = "DUPLICATE_IDENTIFIER"
    DUPLICATE_ACTIVE_MEMBERSHIP = "DUPLICATE_ACTIVE_MEMBERSHIP"
    CONFLICTING_CONTEXT_BINDING = "CONFLICTING_CONTEXT_BINDING"
    TENANT_CONFLICT = "TENANT_CONFLICT"
    ORGANIZATION_CONFLICT = "ORGANIZATION_CONFLICT"
    POLICY_CONFLICT = "POLICY_CONFLICT"
    CLASSIFICATION_CONFLICT = "CLASSIFICATION_CONFLICT"
    OWNER_CONFLICT = "OWNER_CONFLICT"
    CHRONOLOGY_CONFLICT = "CHRONOLOGY_CONFLICT"
    INVALID_LIFECYCLE = "INVALID_LIFECYCLE"
    MEMBERSHIP_NOT_FOUND = "MEMBERSHIP_NOT_FOUND"
    MEMBERSHIP_INACTIVE = "MEMBERSHIP_INACTIVE"
    MEMBERSHIP_NOT_EFFECTIVE = "MEMBERSHIP_NOT_EFFECTIVE"
    MEMBERSHIP_EXPIRED = "MEMBERSHIP_EXPIRED"
    SOURCE_NOT_AUTHORIZED = "SOURCE_NOT_AUTHORIZED"
    SOURCE_SCOPE_CONFLICT = "SOURCE_SCOPE_CONFLICT"
    SOURCE_POLICY_CONFLICT = "SOURCE_POLICY_CONFLICT"
    WORK_TENANT_CONFLICT = "WORK_TENANT_CONFLICT"
    COLLECTION_LIMIT_EXCEEDED = "COLLECTION_LIMIT_EXCEEDED"
    UNSUPPORTED_ADAPTER = "UNSUPPORTED_ADAPTER"


class TenancyContractError(ValueError):
    def __init__(self, code: TenancyErrorCode):
        self.code = code if type(code) is TenancyErrorCode else TenancyErrorCode.INVALID_TENANT
        super().__init__(self.code.value)


class TenantStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    RESTRICTED = "RESTRICTED"
    DEACTIVATED = "DEACTIVATED"


class OrganizationStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DEACTIVATED = "DEACTIVATED"


class MembershipKind(str, Enum):
    USER = "USER"


class MembershipStatus(str, Enum):
    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    DECLINED = "DECLINED"
    INVALIDATED = "INVALIDATED"
    SUPERSEDED = "SUPERSEDED"


class BindingStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    INVALIDATED = "INVALIDATED"


class MembershipReason(str, Enum):
    EFFECTIVE = "EFFECTIVE"
    NO_MEMBERSHIP = "NO_MEMBERSHIP"
    WRONG_TENANT = "WRONG_TENANT"
    WRONG_ORGANIZATION = "WRONG_ORGANIZATION"
    WRONG_PRINCIPAL = "WRONG_PRINCIPAL"
    INACTIVE = "INACTIVE"
    NOT_YET_EFFECTIVE = "NOT_YET_EFFECTIVE"
    EXPIRED = "EXPIRED"
    TENANT_INACTIVE = "TENANT_INACTIVE"
    ORGANIZATION_INACTIVE = "ORGANIZATION_INACTIVE"
    POLICY_MISMATCH = "POLICY_MISMATCH"
    CONFLICT = "CONFLICT"
    MALFORMED = "MALFORMED"


class TenantViewKind(str, Enum):
    ACTIVE_MEMBERSHIPS = "ACTIVE_MEMBERSHIPS"
    SUSPENDED_MEMBERSHIPS = "SUSPENDED_MEMBERSHIPS"
    EXPIRING_MEMBERSHIPS = "EXPIRING_MEMBERSHIPS"
    TENANT_PRODUCTS = "TENANT_PRODUCTS"
    TENANT_WORKSPACES = "TENANT_WORKSPACES"
    TENANT_PROJECTS = "TENANT_PROJECTS"
    UNRESOLVED_MEMBERSHIP_CONFLICTS = "UNRESOLVED_MEMBERSHIP_CONFLICTS"


def _fail(code: TenancyErrorCode) -> NoReturn:
    raise TenancyContractError(code) from None


def _parts(value: str) -> frozenset[str]:
    return frozenset(part for part in re.split(r"[._:-]+", value.lower()) if part)


def _code(value: object, error: TenancyErrorCode, *, maximum: int = MAX_CODE_BYTES,
          sensitive: bool = True) -> str:
    if type(value) is not str or not value or _CODE_RE.fullmatch(value) is None:
        _fail(error)
    try:
        if len(value.encode("ascii")) > maximum:
            _fail(error)
    except UnicodeError:
        _fail(error)
    if sensitive and _parts(value) & _SENSITIVE:
        _fail(error)
    return value


def _record_id(value: object, prefix: str) -> str:
    opaque = value.split(":", 2)[2] if type(value) is str and value.count(":") >= 2 else ""
    if (
        type(value) is not str or not value.startswith(prefix) or value.count(":") < 2
        or _CODE_RE.fullmatch(value) is None or len(value.encode("ascii", errors="ignore")) > MAX_ID_BYTES
        or not any("a" <= char <= "z" for char in opaque)
        or _SHA256_RE.fullmatch(opaque) is not None or _parts(value) & _SENSITIVE
        or any(part.isdigit() for part in _parts(opaque))
        or any(_HOST_RE.fullmatch(part) is not None for part in opaque.split(":"))
    ):
        _fail(TenancyErrorCode.INVALID_IDENTIFIER)
    return value


def validate_tenant_id(value: object) -> str: return _record_id(value, "tenant1:")
def validate_organization_id(value: object) -> str: return _record_id(value, "org1:")
def validate_membership_id(value: object) -> str: return _record_id(value, "mem1:")
def validate_membership_transition_id(value: object) -> str: return _record_id(value, "memtr1:")
def validate_binding_id(value: object) -> str: return _record_id(value, "tbind1:")
def validate_tenancy_audit_id(value: object) -> str: return _record_id(value, "taud1:")


def _utc(value: object, error: TenancyErrorCode = TenancyErrorCode.INVALID_TIMESTAMP) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(error)
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        _fail(error)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_label(value: object, error: TenancyErrorCode) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > MAX_LABEL_CHARS:
        _fail(error)
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        _fail(error)
    lowered = value.lower()
    if any(term in lowered for term in ("password=", "secret=", "token=", "bearer ")):
        _fail(error)
    return value


def _scope_tuple(scope: AuthorityScope) -> tuple[object, ...]:
    return (scope.organization_id, scope.product_id, scope.workspace_id, scope.project_id,
            scope.resource_id, scope.owner_party_id, scope.global_scope)


def _scope_valid(scope: object, resource_id: str, *, organization_id: str | None) -> bool:
    if type(scope) is not AuthorityScope or scope.global_scope is not False:
        return False
    if scope.resource_id != resource_id or scope.organization_id != organization_id:
        return False
    if any(value is not None for value in (
        scope.product_id, scope.workspace_id, scope.project_id, scope.owner_party_id,
    )):
        return False
    values = (scope.organization_id, scope.product_id, scope.workspace_id, scope.project_id,
              scope.resource_id, scope.owner_party_id)
    try:
        return all(value is None or _CODE_RE.fullmatch(value) is not None for value in values)
    except Exception:
        return False


def _operational_scope_equal(left: AuthorityScope, right: AuthorityScope) -> bool:
    return all(getattr(left, name) == getattr(right, name) for name in (
        "organization_id", "product_id", "workspace_id", "project_id", "owner_party_id",
    ))


def _classification_allows(derived: ResourceClassification, source: ResourceClassification) -> bool:
    rank = {ResourceClassification.PUBLIC: 0, ResourceClassification.INTERNAL: 1,
            ResourceClassification.CONFIDENTIAL: 2, ResourceClassification.RESTRICTED: 3}
    return type(derived) is ResourceClassification and type(source) is ResourceClassification and rank[derived] >= rank[source]


def _valid_actor(actor: object) -> bool:
    try:
        probe = ActorReference(
            actor.actor_ref, actor.actor_kind,
            actor.generator_identifier, actor.generator_version,
        )
        return (
            type(actor) is ActorReference
            and actor._marker is probe._marker
            and actor._integrity == probe._integrity
        )
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class AuthorityProvenance:
    principal_ref: str
    capability_code: str
    resource_type: str
    resource_id: str
    policy_id: str
    policy_version: str
    _integrity: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        for value in (self.principal_ref, self.capability_code, self.resource_type,
                      self.resource_id, self.policy_id, self.policy_version):
            _code(value, TenancyErrorCode.INVALID_AUTHORITY_DECISION, sensitive=False)
        object.__setattr__(self, "_integrity", self._tuple())

    def _tuple(self) -> tuple[object, ...]:
        return (self.principal_ref, self.capability_code, self.resource_type,
                self.resource_id, self.policy_id, self.policy_version)


def _valid_provenance(value: object) -> bool:
    try: return type(value) is AuthorityProvenance and value._integrity == value._tuple()
    except Exception: return False


def _authority(decision: object, *, resource_type: str, resource_id: str,
               scope: AuthorityScope, policy_id: str, policy_version: str,
               capability: str, actor_ref: str) -> AuthorizationDecision:
    try: project_authorized_fields({}, decision, behavior=ProjectionBehavior.OMIT)
    except Exception: _fail(TenancyErrorCode.INVALID_AUTHORITY_DECISION)
    if (
        type(decision) is not AuthorizationDecision or decision.allowed is not True
        or decision.resource_type != resource_type or decision.resource_id != resource_id
        or decision.effective_scope != scope or decision.policy_id != policy_id
        or decision.policy_version != policy_version or decision.action_code != capability
        or decision.principal_ref != actor_ref
    ):
        _fail(TenancyErrorCode.INVALID_AUTHORITY_DECISION)
    return decision


def _provenance(decision: AuthorizationDecision) -> AuthorityProvenance:
    return AuthorityProvenance(decision.principal_ref or "invalid", decision.action_code,
                               decision.resource_type, decision.resource_id,
                               decision.policy_id, decision.policy_version)


@dataclass(frozen=True, slots=True, init=False)
class TenantRecord:
    tenant_id: str
    created_at: datetime
    status: TenantStatus
    scope: AuthorityScope
    classification: ResourceClassification
    visibility_policy_id: str
    visibility_policy_version: str
    display_label: str | None
    created_by: ActorReference
    authority_provenance: AuthorityProvenance
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs; _fail(TenancyErrorCode.INVALID_TENANT)


def _record_tuple(value: object, cls: type) -> tuple[object, ...]:
    return tuple(getattr(value, name) for name in cls.__dataclass_fields__ if name != "_integrity")


def _new_record(cls: type, values: Mapping[str, object]) -> object:
    value = object.__new__(cls)
    for name in cls.__dataclass_fields__:
        if name != "_integrity": object.__setattr__(value, name, values[name])
    object.__setattr__(value, "_integrity", _record_tuple(value, cls))
    return value


def _valid_tenant(value: object) -> bool:
    try:
        return (type(value) is TenantRecord and _valid_actor(value.created_by)
                and _valid_provenance(value.authority_provenance)
                and _scope_valid(value.scope, value.tenant_id, organization_id=None)
                and value._integrity == _record_tuple(value, TenantRecord))
    except Exception: return False


def create_tenant(*, tenant_id: str, created_at: datetime, status: TenantStatus,
                  scope: AuthorityScope, classification: ResourceClassification,
                  visibility_policy_id: str, visibility_policy_version: str,
                  created_by: ActorReference, authority_decision: AuthorizationDecision,
                  display_label: str | None = None,
                  contract_version: str = TENANCY_CONTRACT_VERSION) -> TenantRecord:
    validate_tenant_id(tenant_id)
    if type(status) is not TenantStatus or type(classification) is not ResourceClassification or not _valid_actor(created_by):
        _fail(TenancyErrorCode.INVALID_TENANT)
    if contract_version != TENANCY_CONTRACT_VERSION: _fail(TenancyErrorCode.INVALID_VERSION)
    if not _scope_valid(scope, tenant_id, organization_id=None): _fail(TenancyErrorCode.INVALID_SCOPE)
    created = _utc(created_at); _code(visibility_policy_id, TenancyErrorCode.INVALID_TENANT)
    _code(visibility_policy_version, TenancyErrorCode.INVALID_TENANT, sensitive=False)
    label = None if display_label is None else _safe_label(display_label, TenancyErrorCode.INVALID_TENANT)
    auth = _authority(authority_decision, resource_type="tenancy_tenant", resource_id=tenant_id,
                      scope=scope, policy_id=visibility_policy_id,
                      policy_version=visibility_policy_version, capability="tenant.create",
                      actor_ref=created_by.actor_ref)
    return _new_record(TenantRecord, locals() | {"created_at": created, "display_label": label,
                       "authority_provenance": _provenance(auth)})


@dataclass(frozen=True, slots=True, init=False)
class OrganizationRecord:
    organization_id: str
    tenant_id: str
    created_at: datetime
    status: OrganizationStatus
    scope: AuthorityScope
    classification: ResourceClassification
    visibility_policy_id: str
    visibility_policy_version: str
    display_name: str | None
    created_by: ActorReference
    authority_provenance: AuthorityProvenance
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs; _fail(TenancyErrorCode.INVALID_ORGANIZATION)


def _valid_organization(value: object) -> bool:
    try:
        return (type(value) is OrganizationRecord and _valid_actor(value.created_by)
                and _valid_provenance(value.authority_provenance)
                and _scope_valid(value.scope, value.organization_id,
                                 organization_id=value.organization_id)
                and value._integrity == _record_tuple(value, OrganizationRecord))
    except Exception: return False


def create_organization(*, organization_id: str, tenant_id: str, created_at: datetime,
                        status: OrganizationStatus, scope: AuthorityScope,
                        classification: ResourceClassification,
                        visibility_policy_id: str, visibility_policy_version: str,
                        created_by: ActorReference, authority_decision: AuthorizationDecision,
                        display_name: str | None = None,
                        contract_version: str = TENANCY_CONTRACT_VERSION) -> OrganizationRecord:
    validate_organization_id(organization_id); validate_tenant_id(tenant_id)
    if type(status) is not OrganizationStatus or type(classification) is not ResourceClassification or not _valid_actor(created_by):
        _fail(TenancyErrorCode.INVALID_ORGANIZATION)
    if contract_version != TENANCY_CONTRACT_VERSION: _fail(TenancyErrorCode.INVALID_VERSION)
    if not _scope_valid(scope, organization_id, organization_id=organization_id): _fail(TenancyErrorCode.INVALID_SCOPE)
    created = _utc(created_at); _code(visibility_policy_id, TenancyErrorCode.INVALID_ORGANIZATION)
    _code(visibility_policy_version, TenancyErrorCode.INVALID_ORGANIZATION, sensitive=False)
    name = None if display_name is None else _safe_label(display_name, TenancyErrorCode.INVALID_ORGANIZATION)
    auth = _authority(authority_decision, resource_type="tenancy_organization",
                      resource_id=organization_id, scope=scope,
                      policy_id=visibility_policy_id, policy_version=visibility_policy_version,
                      capability="organization.bind", actor_ref=created_by.actor_ref)
    return _new_record(OrganizationRecord, locals() | {"created_at": created,
                       "display_name": name, "authority_provenance": _provenance(auth)})


@dataclass(frozen=True, slots=True, init=False)
class MembershipRecord:
    membership_id: str
    tenant_id: str
    organization_id: str
    principal_ref: str
    membership_kind: MembershipKind
    initial_status: MembershipStatus
    created_at: datetime
    effective_at: datetime
    expires_at: datetime | None
    sponsor: ActorReference
    supersedes_membership_id: str | None
    scope: AuthorityScope
    classification: ResourceClassification
    visibility_policy_id: str
    visibility_policy_version: str
    authority_provenance: AuthorityProvenance
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs; _fail(TenancyErrorCode.INVALID_MEMBERSHIP)


def _valid_membership(value: object) -> bool:
    try:
        return (type(value) is MembershipRecord and _valid_actor(value.sponsor)
                and _valid_provenance(value.authority_provenance)
                and _scope_valid(value.scope, value.membership_id,
                                 organization_id=value.organization_id)
                and value._integrity == _record_tuple(value, MembershipRecord))
    except Exception: return False


def create_membership(*, membership_id: str, tenant_id: str, organization_id: str,
                      principal_ref: str, membership_kind: MembershipKind,
                      initial_status: MembershipStatus, created_at: datetime,
                      effective_at: datetime, expires_at: datetime | None,
                      sponsor: ActorReference, scope: AuthorityScope,
                      classification: ResourceClassification, visibility_policy_id: str,
                      visibility_policy_version: str, authority_decision: AuthorizationDecision,
                      supersedes_membership_id: str | None = None,
                      contract_version: str = TENANCY_CONTRACT_VERSION) -> MembershipRecord:
    validate_membership_id(membership_id); validate_tenant_id(tenant_id); validate_organization_id(organization_id)
    _code(principal_ref, TenancyErrorCode.INVALID_MEMBERSHIP)
    if not principal_ref.startswith("principal:"):
        _fail(TenancyErrorCode.INVALID_MEMBERSHIP)
    if type(membership_kind) is not MembershipKind or type(initial_status) is not MembershipStatus:
        _fail(TenancyErrorCode.INVALID_MEMBERSHIP)
    if initial_status not in {MembershipStatus.INVITED, MembershipStatus.ACTIVE} or not _valid_actor(sponsor):
        _fail(TenancyErrorCode.INVALID_MEMBERSHIP)
    if contract_version != TENANCY_CONTRACT_VERSION: _fail(TenancyErrorCode.INVALID_VERSION)
    if not _scope_valid(scope, membership_id, organization_id=organization_id): _fail(TenancyErrorCode.INVALID_SCOPE)
    created = _utc(created_at); effective = _utc(effective_at)
    expires = None if expires_at is None else _utc(expires_at)
    if effective < created or (expires is not None and expires <= effective):
        _fail(TenancyErrorCode.CHRONOLOGY_CONFLICT)
    if supersedes_membership_id is not None:
        validate_membership_id(supersedes_membership_id)
        if supersedes_membership_id == membership_id: _fail(TenancyErrorCode.INVALID_MEMBERSHIP)
    _code(visibility_policy_id, TenancyErrorCode.INVALID_MEMBERSHIP)
    _code(visibility_policy_version, TenancyErrorCode.INVALID_MEMBERSHIP, sensitive=False)
    capability = "membership.invite" if initial_status is MembershipStatus.INVITED else "membership.activate"
    auth = _authority(authority_decision, resource_type="tenancy_membership",
                      resource_id=membership_id, scope=scope,
                      policy_id=visibility_policy_id, policy_version=visibility_policy_version,
                      capability=capability, actor_ref=sponsor.actor_ref)
    return _new_record(MembershipRecord, locals() | {"created_at": created,
                       "effective_at": effective, "expires_at": expires,
                       "authority_provenance": _provenance(auth)})


_TRANSITIONS = {
    MembershipStatus.INVITED: {MembershipStatus.ACTIVE, MembershipStatus.DECLINED,
                               MembershipStatus.EXPIRED, MembershipStatus.INVALIDATED},
    MembershipStatus.ACTIVE: {MembershipStatus.SUSPENDED, MembershipStatus.REVOKED,
                              MembershipStatus.EXPIRED, MembershipStatus.INVALIDATED,
                              MembershipStatus.SUPERSEDED},
    MembershipStatus.SUSPENDED: {MembershipStatus.ACTIVE, MembershipStatus.REVOKED,
                                 MembershipStatus.EXPIRED, MembershipStatus.INVALIDATED,
                                 MembershipStatus.SUPERSEDED},
}


@dataclass(frozen=True, slots=True, init=False)
class MembershipTransition:
    transition_id: str
    membership_id: str
    tenant_id: str
    organization_id: str
    from_status: MembershipStatus
    to_status: MembershipStatus
    actor: ActorReference
    occurred_at: datetime
    scope: AuthorityScope
    classification: ResourceClassification
    visibility_policy_id: str
    visibility_policy_version: str
    reason_code: str
    linked_event_id: str | None
    authority_provenance: AuthorityProvenance
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs; _fail(TenancyErrorCode.INVALID_TRANSITION)


def _valid_transition(value: object) -> bool:
    try:
        return (type(value) is MembershipTransition and _valid_actor(value.actor)
                and _valid_provenance(value.authority_provenance)
                and _scope_valid(value.scope, value.transition_id,
                                 organization_id=value.organization_id)
                and value._integrity == _record_tuple(value, MembershipTransition))
    except Exception: return False


def create_membership_transition(*, transition_id: str, membership_id: str,
                                 tenant_id: str, organization_id: str,
                                 from_status: MembershipStatus, to_status: MembershipStatus,
                                 actor: ActorReference, occurred_at: datetime,
                                 scope: AuthorityScope, classification: ResourceClassification,
                                 visibility_policy_id: str, visibility_policy_version: str,
                                 reason_code: str, authority_decision: AuthorizationDecision,
                                 linked_event_id: str | None = None,
                                 contract_version: str = TENANCY_CONTRACT_VERSION) -> MembershipTransition:
    validate_membership_transition_id(transition_id); validate_membership_id(membership_id)
    validate_tenant_id(tenant_id); validate_organization_id(organization_id)
    if type(from_status) is not MembershipStatus or type(to_status) is not MembershipStatus:
        _fail(TenancyErrorCode.INVALID_TRANSITION)
    if to_status not in _TRANSITIONS.get(from_status, set()) or not _valid_actor(actor):
        _fail(TenancyErrorCode.INVALID_LIFECYCLE)
    if contract_version != TENANCY_CONTRACT_VERSION: _fail(TenancyErrorCode.INVALID_VERSION)
    if not _scope_valid(scope, transition_id, organization_id=organization_id): _fail(TenancyErrorCode.INVALID_SCOPE)
    if type(classification) is not ResourceClassification: _fail(TenancyErrorCode.INVALID_TRANSITION)
    when = _utc(occurred_at); _code(reason_code, TenancyErrorCode.INVALID_TRANSITION, sensitive=False)
    _code(visibility_policy_id, TenancyErrorCode.INVALID_TRANSITION)
    _code(visibility_policy_version, TenancyErrorCode.INVALID_TRANSITION, sensitive=False)
    if linked_event_id is not None: validate_event_id(linked_event_id)
    capability = f"membership.{to_status.value.lower()}"
    auth = _authority(authority_decision, resource_type="tenancy_membership_transition",
                      resource_id=transition_id, scope=scope,
                      policy_id=visibility_policy_id, policy_version=visibility_policy_version,
                      capability=capability, actor_ref=actor.actor_ref)
    return _new_record(MembershipTransition, locals() | {"occurred_at": when,
                       "authority_provenance": _provenance(auth)})


@dataclass(frozen=True, slots=True, init=False)
class ContextBinding:
    binding_id: str
    tenant_id: str
    organization_id: str
    context_id: str
    context_type: ContextType
    parent_context_id: str | None
    effective_at: datetime
    status: BindingStatus
    supersedes_binding_id: str | None
    scope: AuthorityScope
    classification: ResourceClassification
    visibility_policy_id: str
    visibility_policy_version: str
    bound_by: ActorReference
    authority_provenance: AuthorityProvenance
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs; _fail(TenancyErrorCode.INVALID_BINDING)


def _valid_binding(value: object) -> bool:
    try:
        return (type(value) is ContextBinding and _valid_actor(value.bound_by)
                and _valid_provenance(value.authority_provenance)
                and _scope_valid(value.scope, value.binding_id,
                                 organization_id=value.organization_id)
                and value._integrity == _record_tuple(value, ContextBinding))
    except Exception: return False


def create_context_binding(*, binding_id: str, tenant_id: str, organization_id: str,
                           context_id: str, context_type: ContextType,
                           parent_context_id: str | None, effective_at: datetime,
                           status: BindingStatus, scope: AuthorityScope,
                           classification: ResourceClassification,
                           visibility_policy_id: str, visibility_policy_version: str,
                           bound_by: ActorReference, authority_decision: AuthorizationDecision,
                           supersedes_binding_id: str | None = None,
                           contract_version: str = TENANCY_CONTRACT_VERSION) -> ContextBinding:
    validate_binding_id(binding_id); validate_tenant_id(tenant_id); validate_organization_id(organization_id)
    validate_context_id(context_id)
    if type(context_type) is not ContextType or type(status) is not BindingStatus or not _valid_actor(bound_by):
        _fail(TenancyErrorCode.INVALID_BINDING)
    if context_type is ContextType.PRODUCT and parent_context_id is not None: _fail(TenancyErrorCode.INVALID_BINDING)
    if context_type is not ContextType.PRODUCT and parent_context_id is None: _fail(TenancyErrorCode.INVALID_BINDING)
    if parent_context_id is not None: validate_context_id(parent_context_id)
    if supersedes_binding_id is not None:
        validate_binding_id(supersedes_binding_id)
        if supersedes_binding_id == binding_id: _fail(TenancyErrorCode.INVALID_BINDING)
    if status is not BindingStatus.ACTIVE and supersedes_binding_id is None:
        _fail(TenancyErrorCode.INVALID_BINDING)
    if contract_version != TENANCY_CONTRACT_VERSION: _fail(TenancyErrorCode.INVALID_VERSION)
    if not _scope_valid(scope, binding_id, organization_id=organization_id): _fail(TenancyErrorCode.INVALID_SCOPE)
    if type(classification) is not ResourceClassification: _fail(TenancyErrorCode.INVALID_BINDING)
    when = _utc(effective_at); _code(visibility_policy_id, TenancyErrorCode.INVALID_BINDING)
    _code(visibility_policy_version, TenancyErrorCode.INVALID_BINDING, sensitive=False)
    auth = _authority(authority_decision, resource_type="tenancy_context_binding",
                      resource_id=binding_id, scope=scope, policy_id=visibility_policy_id,
                      policy_version=visibility_policy_version, capability="context.bind.tenant",
                      actor_ref=bound_by.actor_ref)
    return _new_record(ContextBinding, locals() | {"effective_at": when,
                       "authority_provenance": _provenance(auth)})


@dataclass(frozen=True, slots=True, init=False)
class TenantContext:
    tenant_id: str
    organization_id: str
    product_context_id: str
    workspace_context_id: str
    project_context_id: str
    scope: AuthorityScope
    classification: ResourceClassification
    policy_id: str
    policy_version: str
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs; _fail(TenancyErrorCode.INVALID_TENANT_CONTEXT)

    def _tuple(self) -> tuple[object, ...]:
        return (self.tenant_id, self.organization_id, self.product_context_id,
                self.workspace_context_id, self.project_context_id, _scope_tuple(self.scope),
                self.classification, self.policy_id, self.policy_version, self.contract_version)


def _new_tenant_context(*, tenant_id: str, organization_id: str,
                        product_context_id: str, workspace_context_id: str,
                        project_context_id: str, scope: AuthorityScope,
                        classification: ResourceClassification, policy_id: str,
                        policy_version: str,
                        contract_version: str = TENANCY_CONTRACT_VERSION) -> TenantContext:
    validate_tenant_id(tenant_id); validate_organization_id(organization_id)
    for value in (product_context_id, workspace_context_id, project_context_id):
        validate_context_id(value)
    if type(scope) is not AuthorityScope or scope.organization_id != organization_id:
        _fail(TenancyErrorCode.INVALID_TENANT_CONTEXT)
    if None in (scope.product_id, scope.workspace_id, scope.project_id, scope.owner_party_id):
        _fail(TenancyErrorCode.INVALID_TENANT_CONTEXT)
    if scope.resource_id != project_context_id or scope.global_scope is not False:
        _fail(TenancyErrorCode.INVALID_TENANT_CONTEXT)
    if type(classification) is not ResourceClassification:
        _fail(TenancyErrorCode.INVALID_TENANT_CONTEXT)
    _code(policy_id, TenancyErrorCode.INVALID_TENANT_CONTEXT)
    _code(policy_version, TenancyErrorCode.INVALID_TENANT_CONTEXT, sensitive=False)
    if contract_version != TENANCY_CONTRACT_VERSION:
        _fail(TenancyErrorCode.INVALID_VERSION)
    values = locals()
    item = object.__new__(TenantContext)
    for name in TenantContext.__dataclass_fields__:
        if name != "_integrity": object.__setattr__(item, name, values[name])
    object.__setattr__(item, "_integrity", item._tuple())
    return item


def _valid_tenant_context(value: object) -> bool:
    try: return type(value) is TenantContext and value._integrity == value._tuple()
    except Exception: return False


@dataclass(frozen=True, slots=True, init=False)
class MembershipEvaluation:
    effective: bool
    reason_code: MembershipReason
    membership_id: str | None
    tenant_id: str
    organization_id: str
    capability_granted: bool
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs; _fail(TenancyErrorCode.INVALID_MEMBERSHIP)


def _evaluation(effective: bool, reason_code: MembershipReason,
                membership_id: str | None, tenant_id: str,
                organization_id: str) -> MembershipEvaluation:
    if type(effective) is not bool or type(reason_code) is not MembershipReason:
        _fail(TenancyErrorCode.INVALID_MEMBERSHIP)
    values = (effective, reason_code, membership_id, tenant_id, organization_id, False)
    item = object.__new__(MembershipEvaluation)
    for name, value in zip(
        ("effective", "reason_code", "membership_id", "tenant_id",
         "organization_id", "capability_granted"), values, strict=True,
    ):
        object.__setattr__(item, name, value)
    object.__setattr__(item, "_integrity", values)
    return item


def _valid_evaluation(value: object) -> bool:
    try:
        return type(value) is MembershipEvaluation and value._integrity == (
            value.effective, value.reason_code, value.membership_id,
            value.tenant_id, value.organization_id, value.capability_granted,
        ) and value.capability_granted is False
    except Exception:
        return False


@dataclass(frozen=True, slots=True, init=False)
class ValidatedTenancyCollection:
    membership_states: tuple[tuple[str, MembershipStatus], ...]
    active_binding_ids: tuple[str, ...]
    conflicting_membership_ids: tuple[str, ...]
    membership_integrities: tuple[tuple[str, tuple[object, ...]], ...]
    binding_integrities: tuple[tuple[str, tuple[object, ...]], ...]
    evaluated_at: datetime
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs; _fail(TenancyErrorCode.INVALID_TENANT_CONTEXT)


def _new_validated(states: Mapping[str, MembershipStatus], active: Sequence[str],
                   conflicts: Sequence[str], memberships: Sequence[MembershipRecord],
                   bindings: Sequence[ContextBinding],
                   evaluated_at: datetime) -> ValidatedTenancyCollection:
    values = (
        tuple(sorted(states.items())), tuple(sorted(active)), tuple(sorted(conflicts)),
        tuple(sorted((item.membership_id, item._integrity) for item in memberships)),
        tuple(sorted((item.binding_id, item._integrity) for item in bindings)),
        evaluated_at,
    )
    item = object.__new__(ValidatedTenancyCollection)
    for name, value in zip(
        ("membership_states", "active_binding_ids", "conflicting_membership_ids",
         "membership_integrities", "binding_integrities", "evaluated_at"),
        values, strict=True,
    ):
        object.__setattr__(item, name, value)
    object.__setattr__(item, "_integrity", values)
    return item


def _valid_validated(value: object) -> bool:
    try:
        return type(value) is ValidatedTenancyCollection and value._integrity == (
            value.membership_states, value.active_binding_ids, value.conflicting_membership_ids,
            value.membership_integrities, value.binding_integrities, value.evaluated_at)
    except Exception: return False


def validate_tenancy_collection(*, tenants: Sequence[TenantRecord],
                                organizations: Sequence[OrganizationRecord],
                                memberships: Sequence[MembershipRecord],
                                transitions: Sequence[MembershipTransition],
                                bindings: Sequence[ContextBinding],
                                contexts: Sequence[OperationalContextRecord],
                                events: Sequence[OperationalEvent] = (),
                                evaluated_at: datetime) -> ValidatedTenancyCollection:
    collections = (tenants, organizations, memberships, transitions, bindings, contexts, events)
    if any(type(items) not in (list, tuple) for items in collections): _fail(TenancyErrorCode.INVALID_TENANT_CONTEXT)
    if any(len(items) > MAX_RECORDS_PER_KIND for items in collections if items is not transitions) or len(transitions) > MAX_TRANSITIONS:
        _fail(TenancyErrorCode.COLLECTION_LIMIT_EXCEEDED)
    now = _utc(evaluated_at)
    validators = (_valid_tenant, _valid_organization, _valid_membership, _valid_transition, _valid_binding)
    for items, validator in zip(collections[:5], validators, strict=True):
        if any(not validator(item) for item in items): _fail(TenancyErrorCode.INVALID_TENANT_CONTEXT)
    if any(type(item) is not OperationalEvent or not _valid_truth_source(item) for item in events):
        _fail(TenancyErrorCode.INVALID_TENANT_CONTEXT)
    tenant_by = {item.tenant_id: item for item in tenants}; org_by = {item.organization_id: item for item in organizations}
    member_by = {item.membership_id: item for item in memberships}; transition_by = {item.transition_id: item for item in transitions}
    binding_by = {item.binding_id: item for item in bindings}
    context_by = {item.context_id: item for item in contexts}; event_by = {item.event_id: item for item in events}
    registries = ((tenant_by, tenants), (org_by, organizations), (member_by, memberships),
                  (transition_by, transitions), (binding_by, bindings),
                  (context_by, contexts), (event_by, events))
    if any(len(registry) != len(items) for registry, items in registries): _fail(TenancyErrorCode.DUPLICATE_IDENTIFIER)
    all_ids = [key for registry, _ in registries[:5] for key in registry]
    if len(all_ids) != len(set(all_ids)): _fail(TenancyErrorCode.DUPLICATE_IDENTIFIER)
    for context in contexts: context_metadata(context)
    for org in organizations:
        tenant = tenant_by.get(org.tenant_id)
        if tenant is None: _fail(TenancyErrorCode.MISSING_REFERENCE)
        if org.created_at < tenant.created_at: _fail(TenancyErrorCode.CHRONOLOGY_CONFLICT)
        if org.visibility_policy_id != tenant.visibility_policy_id or org.visibility_policy_version != tenant.visibility_policy_version:
            _fail(TenancyErrorCode.POLICY_CONFLICT)
        if not _classification_allows(org.classification, tenant.classification): _fail(TenancyErrorCode.CLASSIFICATION_CONFLICT)
    membership_successors: dict[str, list[MembershipRecord]] = {}
    for member in memberships:
        tenant = tenant_by.get(member.tenant_id); org = org_by.get(member.organization_id)
        if tenant is None or org is None: _fail(TenancyErrorCode.MISSING_REFERENCE)
        if org.tenant_id != member.tenant_id: _fail(TenancyErrorCode.TENANT_CONFLICT)
        if member.visibility_policy_id != org.visibility_policy_id or member.visibility_policy_version != org.visibility_policy_version:
            _fail(TenancyErrorCode.POLICY_CONFLICT)
        if not _classification_allows(member.classification, org.classification):
            _fail(TenancyErrorCode.CLASSIFICATION_CONFLICT)
        if member.created_at < org.created_at or member.created_at < tenant.created_at: _fail(TenancyErrorCode.CHRONOLOGY_CONFLICT)
        if member.supersedes_membership_id is not None:
            prior = member_by.get(member.supersedes_membership_id)
            if prior is None or prior.tenant_id != member.tenant_id or prior.organization_id != member.organization_id or prior.principal_ref != member.principal_ref or prior.created_at >= member.created_at:
                _fail(TenancyErrorCode.INVALID_MEMBERSHIP)
            membership_successors.setdefault(prior.membership_id, []).append(member)
    if any(len(items) != 1 for items in membership_successors.values()):
        _fail(TenancyErrorCode.DUPLICATE_ACTIVE_MEMBERSHIP)
    states = {item.membership_id: item.initial_status for item in memberships}; last_times = {item.membership_id: item.created_at for item in memberships}
    by_membership: dict[str, list[MembershipTransition]] = {}
    for transition in transitions:
        member = member_by.get(transition.membership_id)
        if member is None: _fail(TenancyErrorCode.MISSING_REFERENCE)
        if transition.tenant_id != member.tenant_id or transition.organization_id != member.organization_id:
            _fail(TenancyErrorCode.TENANT_CONFLICT)
        if transition.visibility_policy_id != member.visibility_policy_id or transition.visibility_policy_version != member.visibility_policy_version:
            _fail(TenancyErrorCode.POLICY_CONFLICT)
        if not _classification_allows(transition.classification, member.classification):
            _fail(TenancyErrorCode.CLASSIFICATION_CONFLICT)
        if transition.linked_event_id is not None:
            event = event_by.get(transition.linked_event_id)
            if event is None: _fail(TenancyErrorCode.MISSING_REFERENCE)
            if event.scope.organization_id != transition.organization_id or event.scope.global_scope is not False:
                _fail(TenancyErrorCode.TENANT_CONFLICT)
            if event.occurred_at != transition.occurred_at:
                _fail(TenancyErrorCode.CHRONOLOGY_CONFLICT)
            if (event.visibility_policy_id != transition.visibility_policy_id
                    or event.visibility_policy_version != transition.visibility_policy_version):
                _fail(TenancyErrorCode.POLICY_CONFLICT)
            if not _classification_allows(transition.classification, event.classification):
                _fail(TenancyErrorCode.CLASSIFICATION_CONFLICT)
        by_membership.setdefault(member.membership_id, []).append(transition)
    for member_id, items in by_membership.items():
        for transition in sorted(items, key=lambda value: (value.occurred_at, value.transition_id)):
            if transition.occurred_at <= last_times[member_id] or transition.from_status is not states[member_id]:
                _fail(TenancyErrorCode.INVALID_LIFECYCLE)
            successors = membership_successors.get(member_id, ())
            if successors and transition.occurred_at >= successors[0].effective_at:
                _fail(TenancyErrorCode.INVALID_LIFECYCLE)
            states[member_id] = transition.to_status; last_times[member_id] = transition.occurred_at
    if contexts:
        validate_context_graph(contexts, (), hierarchy_policy=HierarchyPolicy(
            "tenancy.context.hierarchy", "v1",
            frozenset({(ContextType.PRODUCT, ContextType.WORKSPACE),
                       (ContextType.WORKSPACE, ContextType.PROJECT)})))
    binding_successors: dict[str, list[ContextBinding]] = {}
    for binding in bindings:
        tenant = tenant_by.get(binding.tenant_id); org = org_by.get(binding.organization_id); context = context_by.get(binding.context_id)
        if tenant is None or org is None or context is None: _fail(TenancyErrorCode.MISSING_REFERENCE)
        if org.tenant_id != binding.tenant_id or context.context_type is not binding.context_type:
            _fail(TenancyErrorCode.TENANT_CONFLICT)
        if (binding.visibility_policy_id != tenant.visibility_policy_id
                or binding.visibility_policy_version != tenant.visibility_policy_version
                or binding.visibility_policy_id != org.visibility_policy_id
                or binding.visibility_policy_version != org.visibility_policy_version):
            _fail(TenancyErrorCode.POLICY_CONFLICT)
        if context.scope.organization_id != binding.organization_id or binding.parent_context_id != context.parent_context_id:
            _fail(TenancyErrorCode.INVALID_BINDING)
        if binding.visibility_policy_id != context.visibility_policy_id or binding.visibility_policy_version != context.visibility_policy_version:
            _fail(TenancyErrorCode.POLICY_CONFLICT)
        if binding.classification is not context.classification: _fail(TenancyErrorCode.CLASSIFICATION_CONFLICT)
        if binding.effective_at < context.created_at: _fail(TenancyErrorCode.CHRONOLOGY_CONFLICT)
        if binding.supersedes_binding_id is not None:
            prior = binding_by.get(binding.supersedes_binding_id)
            if (prior is None or prior.context_id != binding.context_id
                    or prior.tenant_id != binding.tenant_id
                    or prior.organization_id != binding.organization_id
                    or prior.effective_at >= binding.effective_at
                    or prior.visibility_policy_id != binding.visibility_policy_id
                    or prior.visibility_policy_version != binding.visibility_policy_version
                    or prior.classification is not binding.classification):
                _fail(TenancyErrorCode.INVALID_BINDING)
            binding_successors.setdefault(prior.binding_id, []).append(binding)
    if any(len(items) != 1 for items in binding_successors.values()):
        _fail(TenancyErrorCode.CONFLICTING_CONTEXT_BINDING)
    superseded_binding_ids = {
        prior_id for prior_id, successors in binding_successors.items()
        if successors[0].effective_at <= now
    }
    active_bindings: dict[str, list[ContextBinding]] = {}
    for binding in bindings:
        if (binding.effective_at <= now and binding.binding_id not in superseded_binding_ids
                and binding.status is BindingStatus.ACTIVE):
            active_bindings.setdefault(binding.context_id, []).append(binding)
    if any(len(items) != 1 for items in active_bindings.values()): _fail(TenancyErrorCode.CONFLICTING_CONTEXT_BINDING)
    for context_id, items in active_bindings.items():
        binding = items[0]
        if binding.parent_context_id is not None:
            parents = active_bindings.get(binding.parent_context_id, ())
            if (len(parents) != 1 or parents[0].tenant_id != binding.tenant_id
                    or parents[0].organization_id != binding.organization_id):
                _fail(TenancyErrorCode.CONFLICTING_CONTEXT_BINDING)
    states = {item.membership_id: item.initial_status for item in memberships}
    for member_id, items in by_membership.items():
        for transition in sorted(items, key=lambda value: (value.occurred_at, value.transition_id)):
            if transition.occurred_at <= now:
                states[member_id] = transition.to_status
    for prior_id, successors in membership_successors.items():
        if successors[0].effective_at <= now:
            states[prior_id] = MembershipStatus.SUPERSEDED
    active_groups: dict[tuple[str, str, str], list[str]] = {}
    for member in memberships:
        if (member.created_at <= now and states[member.membership_id] is MembershipStatus.ACTIVE
                and member.effective_at <= now
                and (member.expires_at is None or member.expires_at > now)):
            active_groups.setdefault((member.tenant_id, member.organization_id, member.principal_ref), []).append(member.membership_id)
    conflicts = tuple(sorted(mid for ids in active_groups.values() if len(ids) > 1 for mid in ids))
    return _new_validated(
        states, (items[0].binding_id for items in active_bindings.values()), conflicts,
        memberships, bindings, now,
    )


def build_tenant_context(*, tenant: TenantRecord, organization: OrganizationRecord,
                         product_binding: ContextBinding, workspace_binding: ContextBinding,
                         project_binding: ContextBinding,
                         product: OperationalContextRecord, workspace: OperationalContextRecord,
                         project: OperationalContextRecord) -> TenantContext:
    if not _valid_tenant(tenant) or not _valid_organization(organization) or any(not _valid_binding(item) for item in (product_binding, workspace_binding, project_binding)):
        _fail(TenancyErrorCode.INVALID_TENANT_CONTEXT)
    for item in (product, workspace, project): context_metadata(item)
    if tenant.status is not TenantStatus.ACTIVE or organization.status is not OrganizationStatus.ACTIVE:
        _fail(TenancyErrorCode.INVALID_TENANT_CONTEXT)
    if organization.tenant_id != tenant.tenant_id or any(item.tenant_id != tenant.tenant_id or item.organization_id != organization.organization_id for item in (product_binding, workspace_binding, project_binding)):
        _fail(TenancyErrorCode.TENANT_CONFLICT)
    if (organization.visibility_policy_id != tenant.visibility_policy_id
            or organization.visibility_policy_version != tenant.visibility_policy_version):
        _fail(TenancyErrorCode.POLICY_CONFLICT)
    if not _classification_allows(organization.classification, tenant.classification):
        _fail(TenancyErrorCode.CLASSIFICATION_CONFLICT)
    try:
        validate_context_graph((product, workspace, project), (), hierarchy_policy=HierarchyPolicy(
            "tenancy.context.hierarchy", "v1",
            frozenset({(ContextType.PRODUCT, ContextType.WORKSPACE),
                       (ContextType.WORKSPACE, ContextType.PROJECT)}),
        ))
    except Exception:
        _fail(TenancyErrorCode.INVALID_TENANT_CONTEXT)
    expected = ((product_binding, product, ContextType.PRODUCT, None),
                (workspace_binding, workspace, ContextType.WORKSPACE, product.context_id),
                (project_binding, project, ContextType.PROJECT, workspace.context_id))
    for binding, context, kind, parent in expected:
        if binding.status is not BindingStatus.ACTIVE or binding.context_id != context.context_id or binding.context_type is not kind or binding.parent_context_id != parent:
            _fail(TenancyErrorCode.INVALID_TENANT_CONTEXT)
        if context.parent_context_id != parent:
            _fail(TenancyErrorCode.INVALID_TENANT_CONTEXT)
        if context.scope.organization_id != organization.organization_id:
            _fail(TenancyErrorCode.TENANT_CONFLICT)
        if (binding.visibility_policy_id != context.visibility_policy_id
                or binding.visibility_policy_version != context.visibility_policy_version):
            _fail(TenancyErrorCode.POLICY_CONFLICT)
        if binding.classification is not context.classification:
            _fail(TenancyErrorCode.CLASSIFICATION_CONFLICT)
    if (project.scope.organization_id != organization.organization_id
            or project.scope.resource_id != project.context_id
            or None in (project.scope.product_id, project.scope.workspace_id,
                        project.scope.project_id, project.scope.owner_party_id)):
        _fail(TenancyErrorCode.INVALID_SCOPE)
    if not (product.visibility_policy_id == workspace.visibility_policy_id == project.visibility_policy_id == tenant.visibility_policy_id
            and product.visibility_policy_version == workspace.visibility_policy_version == project.visibility_policy_version == tenant.visibility_policy_version):
        _fail(TenancyErrorCode.POLICY_CONFLICT)
    classification = max((tenant.classification, organization.classification,
                          product.classification, workspace.classification,
                          project.classification),
                         key=lambda item: {ResourceClassification.PUBLIC: 0, ResourceClassification.INTERNAL: 1,
                                           ResourceClassification.CONFIDENTIAL: 2, ResourceClassification.RESTRICTED: 3}[item])
    return _new_tenant_context(
        tenant_id=tenant.tenant_id, organization_id=organization.organization_id,
        product_context_id=product.context_id,
        workspace_context_id=workspace.context_id,
        project_context_id=project.context_id, scope=project.scope,
        classification=classification, policy_id=project.visibility_policy_id,
        policy_version=project.visibility_policy_version,
    )


def evaluate_membership(*, principal_ref: str, tenant_context: TenantContext,
                        tenant: TenantRecord, organization: OrganizationRecord,
                        memberships: Sequence[MembershipRecord],
                        transitions: Sequence[MembershipTransition],
                        evaluated_at: datetime) -> MembershipEvaluation:
    try: _code(principal_ref, TenancyErrorCode.INVALID_MEMBERSHIP)
    except Exception: return _evaluation(False, MembershipReason.MALFORMED, None, "invalid", "invalid")
    if not _valid_tenant_context(tenant_context) or not _valid_tenant(tenant) or not _valid_organization(organization) or type(memberships) not in (list, tuple) or type(transitions) not in (list, tuple):
        return _evaluation(False, MembershipReason.MALFORMED, None, tenant_context.tenant_id if type(tenant_context) is TenantContext else "invalid", tenant_context.organization_id if type(tenant_context) is TenantContext else "invalid")
    base = (tenant_context.tenant_id, tenant_context.organization_id)
    if tenant.tenant_id != base[0]: return _evaluation(False, MembershipReason.WRONG_TENANT, None, *base)
    if organization.organization_id != base[1] or organization.tenant_id != base[0]: return _evaluation(False, MembershipReason.WRONG_ORGANIZATION, None, *base)
    if (tenant.visibility_policy_id != tenant_context.policy_id
            or tenant.visibility_policy_version != tenant_context.policy_version
            or organization.visibility_policy_id != tenant_context.policy_id
            or organization.visibility_policy_version != tenant_context.policy_version):
        return _evaluation(False, MembershipReason.POLICY_MISMATCH, None, *base)
    if (not _classification_allows(tenant_context.classification, tenant.classification)
            or not _classification_allows(tenant_context.classification, organization.classification)):
        return _evaluation(False, MembershipReason.MALFORMED, None, *base)
    if tenant.status is not TenantStatus.ACTIVE: return _evaluation(False, MembershipReason.TENANT_INACTIVE, None, *base)
    if organization.status is not OrganizationStatus.ACTIVE: return _evaluation(False, MembershipReason.ORGANIZATION_INACTIVE, None, *base)
    if any(not _valid_membership(item) for item in memberships) or any(not _valid_transition(item) for item in transitions):
        return _evaluation(False, MembershipReason.MALFORMED, None, *base)
    membership_ids = {item.membership_id for item in memberships}
    if any(item.membership_id not in membership_ids for item in transitions):
        return _evaluation(False, MembershipReason.MALFORMED, None, *base)
    membership_by = {item.membership_id: item for item in memberships}
    if len(membership_by) != len(memberships):
        return _evaluation(False, MembershipReason.CONFLICT, None, *base)
    successors: dict[str, list[MembershipRecord]] = {}
    for item in memberships:
        if item.supersedes_membership_id is None:
            continue
        prior = membership_by.get(item.supersedes_membership_id)
        if (prior is None or prior.tenant_id != item.tenant_id
                or prior.organization_id != item.organization_id
                or prior.principal_ref != item.principal_ref
                or prior.created_at >= item.created_at):
            return _evaluation(False, MembershipReason.MALFORMED, None, *base)
        successors.setdefault(prior.membership_id, []).append(item)
    if any(len(items) != 1 for items in successors.values()):
        return _evaluation(False, MembershipReason.CONFLICT, None, *base)
    exact = [item for item in memberships if item.tenant_id == base[0] and item.organization_id == base[1] and item.principal_ref == principal_ref]
    if not exact:
        if any(item.principal_ref == principal_ref and item.tenant_id != base[0] for item in memberships): return _evaluation(False, MembershipReason.WRONG_TENANT, None, *base)
        if any(item.principal_ref == principal_ref and item.organization_id != base[1] for item in memberships): return _evaluation(False, MembershipReason.WRONG_ORGANIZATION, None, *base)
        if memberships: return _evaluation(False, MembershipReason.WRONG_PRINCIPAL, None, *base)
        return _evaluation(False, MembershipReason.NO_MEMBERSHIP, None, *base)
    now = _utc(evaluated_at); effective: list[MembershipRecord] = []
    for member in exact:
        if member.visibility_policy_id != tenant_context.policy_id or member.visibility_policy_version != tenant_context.policy_version:
            return _evaluation(False, MembershipReason.POLICY_MISMATCH, None, *base)
        state = member.initial_status; effective_state = state; last = member.created_at
        for transition in sorted((item for item in transitions if item.membership_id == member.membership_id), key=lambda item: (item.occurred_at, item.transition_id)):
            if (transition.tenant_id != base[0] or transition.organization_id != base[1]
                    or transition.visibility_policy_id != member.visibility_policy_id
                    or transition.visibility_policy_version != member.visibility_policy_version
                    or not _classification_allows(transition.classification, member.classification)
                    or transition.from_status is not state or transition.occurred_at <= last):
                return _evaluation(False, MembershipReason.MALFORMED, None, *base)
            replacements = successors.get(member.membership_id, ())
            if replacements and transition.occurred_at >= replacements[0].effective_at:
                return _evaluation(False, MembershipReason.MALFORMED, None, *base)
            state = transition.to_status
            if transition.occurred_at <= now:
                effective_state = transition.to_status
            last = transition.occurred_at
        if member.created_at > now or member.effective_at > now: continue
        replacements = successors.get(member.membership_id, ())
        if replacements and replacements[0].effective_at <= now:
            continue
        if member.expires_at is not None and member.expires_at <= now: continue
        if effective_state is MembershipStatus.ACTIVE: effective.append(member)
    if len(effective) > 1: return _evaluation(False, MembershipReason.CONFLICT, None, *base)
    if len(effective) == 1: return _evaluation(True, MembershipReason.EFFECTIVE, effective[0].membership_id, *base)
    member = exact[0]
    if member.created_at > now or member.effective_at > now: reason = MembershipReason.NOT_YET_EFFECTIVE
    elif member.expires_at is not None and member.expires_at <= now: reason = MembershipReason.EXPIRED
    else: reason = MembershipReason.INACTIVE
    return _evaluation(False, reason, None, *base)


def _sealed_tuple(value: object) -> bool:
    try:
        if callable(getattr(value, "_tuple", None)):
            expected = value._tuple()
        else:
            expected = tuple(
                getattr(value, name)
                for name in type(value).__dataclass_fields__
                if name not in {"_integrity", "_marker"}
            )
        return value._integrity == expected
    except Exception:
        return False


def _valid_work_source(value: object) -> bool:
    try:
        fields = tuple(
            getattr(value, name)
            for name in type(value).__dataclass_fields__
            if name != "_integrity"
        )
        resource_id = value.work_id if type(value) is WorkItem else value.assignment_id
        nested_valid = (
            _valid_actor(value.requester)
            and all(_sealed_tuple(item) for item in value.completion_criteria)
            if type(value) is WorkItem
            else _valid_actor(value.assigner) and _sealed_tuple(value.assignee)
        )
        provenance_valid = _sealed_tuple(value.authority_provenance)
        if type(value) is Assignment and value.response_authority_provenance is not None:
            provenance_valid = provenance_valid and _sealed_tuple(value.response_authority_provenance)
        return (
            type(value) in (WorkItem, Assignment) and nested_valid and provenance_valid
            and value._integrity == fields
            and type(value.scope) is AuthorityScope
            and value.scope.resource_id == resource_id
            and value.scope.global_scope is False
        )
    except Exception:
        return False


def _truth_authority_tuple(value: object) -> tuple[object, ...]:
    return (
        value.principal_ref, value.capability_code, value.resource_type,
        value.resource_id, value.policy_id, value.policy_version,
    )


def _valid_truth_source(value: object) -> bool:
    try:
        if type(value) is OperationalEvent:
            expected = (
                value.event_id, value.event_type, _scope_tuple(value.scope),
                value.context_ids, value.actor._integrity, value.occurred_at,
                value.recorded_at, value.received_at, value.evidence_ids,
                value.source_event_id, value.correlation_id, value.classification,
                value.visibility_policy_id, value.visibility_policy_version,
                value.reason_code, _truth_authority_tuple(value.authority_provenance),
                value.contract_version,
            )
            resource_id = value.event_id; actor_value = value.actor
        elif type(value) is DecisionRecord:
            expected = (
                value.decision_id, value.decision_type, value.target_type,
                value.target_id, _scope_tuple(value.scope),
                value.decision_maker._integrity, value.issued_at,
                value.effective_at, value.expires_at, value.outcome, value.status,
                value.supporting_event_ids, value.supporting_evidence_ids,
                value.supporting_context_ids, value.prior_decision_ids,
                value.approval_ids, value.classification,
                value.visibility_policy_id, value.visibility_policy_version,
                value.reason_code, _truth_authority_tuple(value.authority_provenance),
                value.contract_version,
            )
            resource_id = value.decision_id; actor_value = value.decision_maker
        elif type(value) is ApprovalRecord:
            expected = (
                value.approval_id, value.approval_type, value.target_type,
                value.target_id, _scope_tuple(value.scope), value.approver._integrity,
                value.status, value.recorded_at, value.effective_at,
                value.expires_at, value.supporting_event_ids,
                value.supporting_evidence_ids, value.classification,
                value.visibility_policy_id, value.visibility_policy_version,
                value.conditions_code, value.reason_code,
                _truth_authority_tuple(value.authority_provenance),
                value.contract_version,
            )
            resource_id = value.approval_id; actor_value = value.approver
        else:
            return False
        return (
            _valid_actor(actor_value) and value._integrity == expected
            and type(value.scope) is AuthorityScope
            and value.scope.resource_id == resource_id
            and value.scope.global_scope is False
        )
    except Exception:
        return False


def validate_work_tenant_compatibility(*, work: WorkItem, assignment: Assignment,
                                       tenant_context: TenantContext,
                                       membership: MembershipRecord,
                                       evaluation: MembershipEvaluation) -> bool:
    try:
        if not _valid_work_source(work) or not _valid_work_source(assignment) or not _valid_tenant_context(tenant_context) or not _valid_membership(membership): _fail(TenancyErrorCode.WORK_TENANT_CONFLICT)
        if (not _valid_evaluation(evaluation) or evaluation != _evaluation(
                True, MembershipReason.EFFECTIVE, membership.membership_id,
                tenant_context.tenant_id, tenant_context.organization_id)):
            _fail(TenancyErrorCode.MEMBERSHIP_INACTIVE)
        if (membership.tenant_id != tenant_context.tenant_id
                or membership.organization_id != tenant_context.organization_id):
            _fail(TenancyErrorCode.WORK_TENANT_CONFLICT)
        if assignment.assignee.executor_kind is not ExecutorKind.HUMAN or assignment.assignee.executor_id != membership.principal_ref:
            _fail(TenancyErrorCode.WORK_TENANT_CONFLICT)
        if work.work_id != assignment.work_id or tenant_context.project_context_id not in work.context_ids:
            _fail(TenancyErrorCode.WORK_TENANT_CONFLICT)
        for scope in (work.scope, assignment.scope):
            if not _operational_scope_equal(scope, tenant_context.scope): _fail(TenancyErrorCode.WORK_TENANT_CONFLICT)
        if (work.visibility_policy_id != tenant_context.policy_id
                or work.visibility_policy_version != tenant_context.policy_version
                or assignment.visibility_policy_id != tenant_context.policy_id
                or assignment.visibility_policy_version != tenant_context.policy_version
                or membership.visibility_policy_id != tenant_context.policy_id
                or membership.visibility_policy_version != tenant_context.policy_version):
            _fail(TenancyErrorCode.POLICY_CONFLICT)
        if any(not _classification_allows(tenant_context.classification, item.classification)
               for item in (work, assignment, membership)):
            _fail(TenancyErrorCode.CLASSIFICATION_CONFLICT)
        return True
    except TenancyContractError: raise
    except Exception: _fail(TenancyErrorCode.WORK_TENANT_CONFLICT)


def validate_tenant_scoped_source(tenant_context: TenantContext, source: object) -> bool:
    if not _valid_tenant_context(tenant_context): _fail(TenancyErrorCode.INVALID_TENANT_CONTEXT)
    try:
        if type(source) is EvidenceRecord: evidence_metadata(source)
        elif type(source) is OperationalContextRecord: context_metadata(source)
        elif type(source) in (OperationalEvent, DecisionRecord, ApprovalRecord):
            if not _valid_truth_source(source): _fail(TenancyErrorCode.TENANT_CONFLICT)
        elif type(source) in (WorkItem, Assignment):
            if not _valid_work_source(source): _fail(TenancyErrorCode.TENANT_CONFLICT)
        else:
            _fail(TenancyErrorCode.TENANT_CONFLICT)
        if not _operational_scope_equal(source.scope, tenant_context.scope): _fail(TenancyErrorCode.TENANT_CONFLICT)
        if source.visibility_policy_id != tenant_context.policy_id or source.visibility_policy_version != tenant_context.policy_version:
            _fail(TenancyErrorCode.POLICY_CONFLICT)
        if not _classification_allows(tenant_context.classification, source.classification): _fail(TenancyErrorCode.CLASSIFICATION_CONFLICT)
        return True
    except TenancyContractError: raise
    except Exception: _fail(TenancyErrorCode.TENANT_CONFLICT)


def _metadata(record: object) -> dict[str, Any]:
    common = {"contract_version": record.contract_version, "classification": record.classification.value,
              "visibility_policy_id": record.visibility_policy_id,
              "visibility_policy_version": record.visibility_policy_version,
              "scope_organization_id": record.scope.organization_id,
              "scope_product_id": record.scope.product_id,
              "scope_workspace_id": record.scope.workspace_id,
              "scope_project_id": record.scope.project_id,
              "scope_resource_id": record.scope.resource_id,
              "scope_owner_party_id": record.scope.owner_party_id,
              "scope_global": record.scope.global_scope}
    if type(record) is TenantRecord and _valid_tenant(record):
        return common | {"tenant_id": record.tenant_id, "created_at": _iso(record.created_at),
                         "status": record.status.value, "display_label": record.display_label}
    if type(record) is OrganizationRecord and _valid_organization(record):
        return common | {"organization_id": record.organization_id, "tenant_id": record.tenant_id,
                         "created_at": _iso(record.created_at), "status": record.status.value,
                         "display_name": record.display_name}
    if type(record) is MembershipRecord and _valid_membership(record):
        return common | {"membership_id": record.membership_id, "tenant_id": record.tenant_id,
                         "organization_id": record.organization_id, "principal_ref": record.principal_ref,
                         "membership_kind": record.membership_kind.value,
                         "initial_status": record.initial_status.value,
                         "created_at": _iso(record.created_at), "effective_at": _iso(record.effective_at),
                         "expires_at": _iso(record.expires_at), "sponsor_ref": record.sponsor.actor_ref,
                         "supersedes_membership_id": record.supersedes_membership_id}
    if type(record) is ContextBinding and _valid_binding(record):
        return common | {"binding_id": record.binding_id, "tenant_id": record.tenant_id,
                         "organization_id": record.organization_id, "context_id": record.context_id,
                         "context_type": record.context_type.value,
                         "parent_context_id": record.parent_context_id,
                         "effective_at": _iso(record.effective_at), "status": record.status.value,
                         "supersedes_binding_id": record.supersedes_binding_id}
    _fail(TenancyErrorCode.INVALID_PROJECTION)


_PROJECTION_SPECS = {
    TenantRecord: ("tenancy_tenant", "tenant_id", _valid_tenant),
    OrganizationRecord: ("tenancy_organization", "organization_id", _valid_organization),
    MembershipRecord: ("tenancy_membership", "membership_id", _valid_membership),
    ContextBinding: ("tenancy_context_binding", "binding_id", _valid_binding),
}
_PROJECTION_CAPABILITIES = {
    TenantRecord: frozenset({"tenancy.view", "tenant.view"}),
    OrganizationRecord: frozenset({"tenancy.view", "organization.view"}),
    MembershipRecord: frozenset({"tenancy.view", "membership.view"}),
    ContextBinding: frozenset({"tenancy.view", "binding.view"}),
}


def _project(record: object, decision: AuthorizationDecision, cls: type) -> dict[str, Any]:
    resource_type, id_field, validator = _PROJECTION_SPECS[cls]
    try:
        if type(record) is not cls or not validator(record): _fail(TenancyErrorCode.INVALID_PROJECTION)
        source = _metadata(record)
        project_authorized_fields({}, decision, behavior=ProjectionBehavior.OMIT)
        if (decision.resource_type != resource_type
                or decision.resource_id != source[id_field]
                or decision.action_code not in _PROJECTION_CAPABILITIES[cls]
                or decision.policy_id != source["visibility_policy_id"]
                or decision.policy_version != source["visibility_policy_version"]
                or (decision.allowed and decision.effective_scope != record.scope)):
            _fail(TenancyErrorCode.INVALID_AUTHORITY_DECISION)
        return project_authorized_fields(source, decision, behavior=ProjectionBehavior.OMIT)
    except TenancyContractError: raise
    except Exception: _fail(TenancyErrorCode.INVALID_PROJECTION)


def project_tenant(record: TenantRecord | Mapping[str, Any], decision: AuthorizationDecision) -> dict[str, Any]: return _project(record, decision, TenantRecord)
def project_organization(record: OrganizationRecord | Mapping[str, Any], decision: AuthorizationDecision) -> dict[str, Any]: return _project(record, decision, OrganizationRecord)
def project_membership(record: MembershipRecord | Mapping[str, Any], decision: AuthorizationDecision) -> dict[str, Any]: return _project(record, decision, MembershipRecord)
def project_context_binding(record: ContextBinding | Mapping[str, Any], decision: AuthorizationDecision) -> dict[str, Any]: return _project(record, decision, ContextBinding)


@dataclass(frozen=True, slots=True, init=False)
class SafeTenancyAudit:
    audit_id: str
    action_code: str
    record_type: str
    record_id: str
    tenant_id: str
    outcome_code: str
    reason_code: str
    policy_id: str
    policy_version: str
    scope_reference: str
    timestamp: str
    correlation_id: str | None
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs; _fail(TenancyErrorCode.INVALID_AUDIT)


_AUDIT_TARGETS = {"tenancy_tenant": validate_tenant_id, "tenancy_organization": validate_organization_id,
                  "tenancy_membership": validate_membership_id,
                  "tenancy_membership_transition": validate_membership_transition_id,
                  "tenancy_context_binding": validate_binding_id}
_AUDIT_ACTIONS = frozenset({
    "tenant.create", "organization.bind", "membership.invite",
    "membership.activate", "membership.suspend", "membership.revoke",
    "membership.expire", "membership.evaluate", "context.bind.tenant",
    "record.reject", "tenant.cross_tenant.deny",
})
_AUDIT_OUTCOMES = frozenset({"allowed", "denied", "created", "recorded", "detected", "rejected"})
_AUDIT_REASONS = frozenset(
    {item.value.lower() for item in TenancyErrorCode}
    | {f"membership.{item.value.lower()}" for item in MembershipReason}
    | {"created", "recorded", "expired"}
)


def build_safe_tenancy_audit(*, audit_id: str, action_code: str, record_type: str,
                             record_id: str, tenant_id: str, outcome_code: str,
                             reason_code: str, policy_id: str, policy_version: str,
                             scope_reference: str, timestamp: datetime,
                             correlation_id: str | None = None) -> SafeTenancyAudit:
    validate_tenancy_audit_id(audit_id); validate_tenant_id(tenant_id)
    validator = _AUDIT_TARGETS.get(record_type)
    if validator is None: _fail(TenancyErrorCode.INVALID_AUDIT)
    try: validator(record_id)
    except Exception: _fail(TenancyErrorCode.INVALID_AUDIT)
    for value in (action_code, outcome_code, reason_code, policy_id, scope_reference):
        _code(value, TenancyErrorCode.INVALID_AUDIT)
    if action_code not in _AUDIT_ACTIONS or outcome_code not in _AUDIT_OUTCOMES or reason_code not in _AUDIT_REASONS:
        _fail(TenancyErrorCode.INVALID_AUDIT)
    _code(policy_version, TenancyErrorCode.INVALID_AUDIT, sensitive=False)
    if correlation_id is not None: _code(correlation_id, TenancyErrorCode.INVALID_AUDIT)
    values = (audit_id, action_code, record_type, record_id, tenant_id, outcome_code,
              reason_code, policy_id, policy_version, scope_reference,
              _iso(_utc(timestamp, TenancyErrorCode.INVALID_AUDIT)) or "",
              correlation_id, TENANCY_CONTRACT_VERSION)
    item = object.__new__(SafeTenancyAudit)
    for name, value in zip((name for name in SafeTenancyAudit.__dataclass_fields__ if name != "_integrity"), values, strict=True): object.__setattr__(item, name, value)
    object.__setattr__(item, "_integrity", values); return item


@dataclass(frozen=True, slots=True)
class DerivedTenantView:
    view_kind: TenantViewKind
    record_ids: tuple[str, ...]
    tenant_id: str
    classification: ResourceClassification
    visible_fields: frozenset[str]
    policy_id: str
    policy_version: str
    derived: bool = True


def create_derived_tenant_view(sources: Sequence[tuple[object, AuthorizationDecision]], *,
                               view_kind: TenantViewKind,
                               validated: ValidatedTenancyCollection,
                               evaluated_at: datetime,
                               expiring_within: timedelta = timedelta(days=30)) -> DerivedTenantView:
    if type(sources) not in (list, tuple) or not sources or type(view_kind) is not TenantViewKind or not _valid_validated(validated):
        _fail(TenancyErrorCode.SOURCE_NOT_AUTHORIZED)
    if (type(expiring_within) is not timedelta or expiring_within <= timedelta(0)
            or expiring_within > timedelta(days=366)):
        _fail(TenancyErrorCode.SOURCE_NOT_AUTHORIZED)
    now = _utc(evaluated_at)
    try:
        expiry_limit = now + expiring_within
    except (OverflowError, ValueError):
        _fail(TenancyErrorCode.SOURCE_NOT_AUTHORIZED)
    states = dict(validated.membership_states)
    if now != validated.evaluated_at:
        _fail(TenancyErrorCode.SOURCE_NOT_AUTHORIZED)
    sealed_memberships = dict(validated.membership_integrities)
    sealed_bindings = dict(validated.binding_integrities)
    membership_view = view_kind in {
        TenantViewKind.ACTIVE_MEMBERSHIPS, TenantViewKind.SUSPENDED_MEMBERSHIPS,
        TenantViewKind.EXPIRING_MEMBERSHIPS,
        TenantViewKind.UNRESOLVED_MEMBERSHIP_CONFLICTS,
    }
    visibility: list[SourceVisibility] = []; records: list[object] = []; tenants: set[str] = set()
    for pair in sources:
        if type(pair) is not tuple or len(pair) != 2 or type(pair[0]) not in _PROJECTION_SPECS:
            _fail(TenancyErrorCode.SOURCE_NOT_AUTHORIZED)
        record, decision = pair
        if membership_view is not (type(record) is MembershipRecord):
            _fail(TenancyErrorCode.SOURCE_NOT_AUTHORIZED)
        if type(record) is MembershipRecord:
            if sealed_memberships.get(record.membership_id) != record._integrity:
                _fail(TenancyErrorCode.SOURCE_NOT_AUTHORIZED)
        elif sealed_bindings.get(record.binding_id) != record._integrity:
            _fail(TenancyErrorCode.SOURCE_NOT_AUTHORIZED)
        try: projected = _project(record, decision, type(record))
        except Exception: _fail(TenancyErrorCode.SOURCE_NOT_AUTHORIZED)
        id_field = _PROJECTION_SPECS[type(record)][1]
        if not decision.allowed or id_field not in projected: _fail(TenancyErrorCode.SOURCE_NOT_AUTHORIZED)
        tenant_id = record.tenant_id
        tenants.add(tenant_id); records.append(record)
        visibility.append(SourceVisibility(record.__getattribute__(id_field), record.classification,
                          decision.visible_fields, (record.scope,), True,
                          record.visibility_policy_id, record.visibility_policy_version))
    if len(tenants) != 1: _fail(TenancyErrorCode.SOURCE_SCOPE_CONFLICT)
    inherited = inherit_derived_visibility(tuple(visibility), artifact_kind=DerivedArtifactKind.REPORT)
    if inherited.reason_code is AuthorityReason.SOURCE_SCOPE_CONFLICT: _fail(TenancyErrorCode.SOURCE_SCOPE_CONFLICT)
    if inherited.reason_code is AuthorityReason.SOURCE_POLICY_CONFLICT: _fail(TenancyErrorCode.SOURCE_POLICY_CONFLICT)
    if not inherited.allowed: _fail(TenancyErrorCode.SOURCE_NOT_AUTHORIZED)
    def include(record: object) -> bool:
        if type(record) is MembershipRecord:
            state = states.get(record.membership_id)
            if type(state) is not MembershipStatus: _fail(TenancyErrorCode.SOURCE_NOT_AUTHORIZED)
            return {TenantViewKind.ACTIVE_MEMBERSHIPS: state is MembershipStatus.ACTIVE and record.effective_at <= now and (record.expires_at is None or record.expires_at > now),
                    TenantViewKind.SUSPENDED_MEMBERSHIPS: state is MembershipStatus.SUSPENDED,
                    TenantViewKind.EXPIRING_MEMBERSHIPS: state is MembershipStatus.ACTIVE and record.expires_at is not None and now < record.expires_at <= expiry_limit,
                    TenantViewKind.UNRESOLVED_MEMBERSHIP_CONFLICTS: record.membership_id in validated.conflicting_membership_ids}.get(view_kind, False)
        if type(record) is ContextBinding and record.binding_id in validated.active_binding_ids:
            return {TenantViewKind.TENANT_PRODUCTS: ContextType.PRODUCT,
                    TenantViewKind.TENANT_WORKSPACES: ContextType.WORKSPACE,
                    TenantViewKind.TENANT_PROJECTS: ContextType.PROJECT}.get(view_kind) is record.context_type
        return False
    policy_id, policy_version = inherited.source_policies[0]
    ids = tuple(sorted(_metadata(item)[_PROJECTION_SPECS[type(item)][1]] for item in records if include(item)))
    return DerivedTenantView(view_kind, ids, next(iter(tenants)), inherited.classification,
                             inherited.visible_fields, policy_id, policy_version)


def adapt_authenticated_principal(*args: object, **kwargs: object) -> NoReturn:
    del args, kwargs; _fail(TenancyErrorCode.UNSUPPORTED_ADAPTER)


__all__ = (
    "BindingStatus", "ContextBinding", "DerivedTenantView", "MembershipEvaluation",
    "MembershipKind", "MembershipReason", "MembershipRecord", "MembershipStatus",
    "MembershipTransition", "OrganizationRecord", "OrganizationStatus",
    "SafeTenancyAudit", "TENANCY_CONTRACT_VERSION", "TENANCY_POLICY_VERSION",
    "TenantContext", "TenantRecord", "TenantStatus", "TenantViewKind",
    "TenancyContractError", "TenancyErrorCode", "ValidatedTenancyCollection",
    "adapt_authenticated_principal", "build_safe_tenancy_audit", "build_tenant_context",
    "create_context_binding", "create_derived_tenant_view", "create_membership",
    "create_membership_transition", "create_organization", "create_tenant",
    "evaluate_membership", "project_context_binding", "project_membership",
    "project_organization", "project_tenant", "validate_binding_id",
    "validate_membership_id", "validate_membership_transition_id",
    "validate_organization_id", "validate_tenancy_audit_id",
    "validate_tenancy_collection", "validate_tenant_id",
    "validate_tenant_scoped_source", "validate_work_tenant_compatibility",
)
