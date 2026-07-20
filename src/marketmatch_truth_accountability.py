"""Pure MarketMatch Truth & Accountability Kernel V1.

Events, Decisions, Approvals, and Audit Records remain separate immutable
contracts.  This module performs no authentication, persistence, logging,
workflow execution, localization, network access, or model invocation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Mapping, NoReturn, Sequence

from src.marketmatch_authority import (
    AuthorityContractError,
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
from src.marketmatch_evidence import (
    EvidenceContractError,
    EvidenceRecord,
    evidence_metadata,
    project_evidence_metadata,
    validate_evidence_id,
)
from src.marketmatch_operational_context import (
    OperationalContextError,
    OperationalContextRecord,
    context_metadata,
    project_context_metadata,
    validate_context_id,
)


TRUTH_CONTRACT_VERSION = "marketmatch-truth-accountability-v1"
TRUTH_POLICY_VERSION = "marketmatch-truth-accountability-policy-v1"
MAX_RECORDS_PER_KIND = 2048
MAX_REFERENCES_PER_RECORD = 256
MAX_LINEAGES = 8192
MAX_REQUIREMENT_SLOTS = 32

_ID_RE = re.compile(r"[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*\Z", re.ASCII)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_ERROR_RE = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*\Z", re.ASCII)
_MAX_ID_BYTES = 160
_MAX_CODE_BYTES = 128
_SENSITIVE_PARTS = frozenset(
    {
        "address", "amount", "avenue", "bl", "container", "cookie", "cost",
        "credential", "database", "email", "factory", "margin", "markup",
        "password", "po", "price", "row", "secret", "session", "street",
        "supplier", "token", "usd",
    }
)
_ACTOR_MARKER = object()
_EVENT_MARKER = object()
_DECISION_MARKER = object()
_APPROVAL_MARKER = object()
_LINEAGE_MARKER = object()
_AUDIT_MARKER = object()
_COLLECTION_MARKER = object()
_APPROVAL_SLOT_MARKER = object()
_APPROVAL_REQUIREMENT_MARKER = object()


class TruthErrorCode(str, Enum):
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    INVALID_ACTOR = "INVALID_ACTOR"
    INVALID_SCOPE = "INVALID_SCOPE"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    INVALID_EVENT = "INVALID_EVENT"
    INVALID_DECISION = "INVALID_DECISION"
    INVALID_APPROVAL = "INVALID_APPROVAL"
    INVALID_LINEAGE = "INVALID_LINEAGE"
    INVALID_AUTHORITY_DECISION = "INVALID_AUTHORITY_DECISION"
    INVALID_PROJECTION = "INVALID_PROJECTION"
    INVALID_AUDIT = "INVALID_AUDIT"
    INVALID_VERSION = "INVALID_VERSION"
    MISSING_REFERENCE = "MISSING_REFERENCE"
    DUPLICATE_IDENTIFIER = "DUPLICATE_IDENTIFIER"
    SCOPE_CONFLICT = "SCOPE_CONFLICT"
    POLICY_CONFLICT = "POLICY_CONFLICT"
    VISIBILITY_CONFLICT = "VISIBILITY_CONFLICT"
    CHRONOLOGY_CONFLICT = "CHRONOLOGY_CONFLICT"
    LINEAGE_CYCLE = "LINEAGE_CYCLE"
    CONFLICTING_DECISIONS = "CONFLICTING_DECISIONS"
    CONFLICTING_APPROVALS = "CONFLICTING_APPROVALS"
    DUPLICATE_APPROVAL = "DUPLICATE_APPROVAL"
    INVALID_REQUIREMENT = "INVALID_REQUIREMENT"
    MISSING_APPROVAL = "MISSING_APPROVAL"
    WRONG_AUTHORITY_SLOT = "WRONG_AUTHORITY_SLOT"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_INACTIVE = "APPROVAL_INACTIVE"
    SOURCE_NOT_AUTHORIZED = "SOURCE_NOT_AUTHORIZED"
    SOURCE_SCOPE_CONFLICT = "SOURCE_SCOPE_CONFLICT"
    SOURCE_POLICY_CONFLICT = "SOURCE_POLICY_CONFLICT"
    COLLECTION_LIMIT_EXCEEDED = "COLLECTION_LIMIT_EXCEEDED"


class TruthContractError(ValueError):
    """Fixed-code failure that never includes rejected values."""

    def __init__(self, code: TruthErrorCode):
        self.code = code if type(code) is TruthErrorCode else TruthErrorCode.INVALID_EVENT
        super().__init__(self.code.value)


class RecordKind(str, Enum):
    EVENT = "EVENT"
    DECISION = "DECISION"
    APPROVAL = "APPROVAL"
    AUDIT = "AUDIT"


class ActorKind(str, Enum):
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"
    EXTERNAL_SYSTEM = "EXTERNAL_SYSTEM"
    AGENT = "AGENT"


class EventType(str, Enum):
    OBSERVED = "OBSERVED"
    REPORTED = "REPORTED"
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    RECEIVED = "RECEIVED"
    CORRECTED = "CORRECTED"
    INVALIDATED = "INVALIDATED"
    SYSTEM_GENERATED = "SYSTEM_GENERATED"


class DecisionType(str, Enum):
    CONCLUSION = "CONCLUSION"
    COURSE_OF_ACTION = "COURSE_OF_ACTION"
    CLASSIFICATION = "CLASSIFICATION"
    EXCEPTION = "EXCEPTION"
    LIFECYCLE = "LIFECYCLE"


class DecisionOutcome(str, Enum):
    SELECTED = "SELECTED"
    DECLINED = "DECLINED"
    DEFERRED = "DEFERRED"
    INCONCLUSIVE = "INCONCLUSIVE"


class DecisionStatus(str, Enum):
    PROPOSED = "PROPOSED"
    ISSUED = "ISSUED"
    SUPERSEDED = "SUPERSEDED"
    WITHDRAWN = "WITHDRAWN"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class ApprovalType(str, Enum):
    AUTHORIZATION = "AUTHORIZATION"
    REVIEW = "REVIEW"
    EXCEPTION = "EXCEPTION"


class ApprovalStatus(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CONDITIONAL = "CONDITIONAL"
    WITHDRAWN = "WITHDRAWN"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


class TargetType(str, Enum):
    EVENT = "EVENT"
    DECISION = "DECISION"
    APPROVAL = "APPROVAL"
    EVIDENCE = "EVIDENCE"
    OPERATIONAL_CONTEXT = "OPERATIONAL_CONTEXT"


class LineageType(str, Enum):
    CORRECTS = "CORRECTS"
    SUPERSEDES = "SUPERSEDES"
    WITHDRAWS = "WITHDRAWS"
    INVALIDATES = "INVALIDATES"


class RequirementStatus(str, Enum):
    SATISFIED = "SATISFIED"
    UNRESOLVED = "UNRESOLVED"
    CONFLICT = "CONFLICT"
    INVALID = "INVALID"


class RequirementReason(str, Enum):
    NO_APPROVAL_REQUIRED = "NO_APPROVAL_REQUIRED"
    APPROVALS_SATISFIED = "APPROVALS_SATISFIED"
    MISSING_APPROVAL = "MISSING_APPROVAL"
    DUPLICATE_APPROVER = "DUPLICATE_APPROVER"
    WRONG_AUTHORITY_SLOT = "WRONG_AUTHORITY_SLOT"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_INACTIVE = "APPROVAL_INACTIVE"
    CONFLICTING_APPROVALS = "CONFLICTING_APPROVALS"
    INVALID_REQUIREMENT = "INVALID_REQUIREMENT"


class AuditOutcome(str, Enum):
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


def _fail(code: TruthErrorCode) -> NoReturn:
    raise TruthContractError(code) from None


def _parts(value: str) -> frozenset[str]:
    return frozenset(part for part in re.split(r"[._:-]+", value.lower()) if part)


def _valid_code(value: object, *, maximum: int = _MAX_CODE_BYTES, sensitive: bool = True) -> bool:
    if type(value) is not str or not value or _ID_RE.fullmatch(value) is None:
        return False
    try:
        if len(value.encode("ascii")) > maximum:
            return False
    except UnicodeError:
        return False
    return not sensitive or not (_parts(value) & _SENSITIVE_PARTS)


def _require_code(
    value: object,
    error: TruthErrorCode,
    *,
    maximum: int = _MAX_CODE_BYTES,
    sensitive: bool = True,
) -> str:
    if not _valid_code(value, maximum=maximum, sensitive=sensitive):
        _fail(error)
    return value


def _validate_record_id(value: object, prefix: str) -> str:
    opaque = value.split(":", 2)[2] if type(value) is str and value.count(":") >= 2 else ""
    if (
        not _valid_code(value, maximum=_MAX_ID_BYTES)
        or not value.startswith(prefix)
        or value.count(":") < 2
        or not any("a" <= char <= "z" for char in opaque)
        or _SHA256_RE.fullmatch(opaque) is not None
    ):
        _fail(TruthErrorCode.INVALID_IDENTIFIER)
    return value


def validate_event_id(value: object) -> str:
    return _validate_record_id(value, "evt1:")


def validate_decision_id(value: object) -> str:
    return _validate_record_id(value, "dec1:")


def validate_approval_id(value: object) -> str:
    return _validate_record_id(value, "apr1:")


def validate_audit_id(value: object) -> str:
    return _validate_record_id(value, "aud1:")


def _validate_id_for_kind(kind: RecordKind, value: object) -> str:
    return {
        RecordKind.EVENT: validate_event_id,
        RecordKind.DECISION: validate_decision_id,
        RecordKind.APPROVAL: validate_approval_id,
        RecordKind.AUDIT: validate_audit_id,
    }[kind](value)


def _utc(value: object, error: TruthErrorCode = TruthErrorCode.INVALID_TIMESTAMP) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(error)
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        _fail(error)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _scope_tuple(scope: AuthorityScope) -> tuple[object, ...]:
    return (
        scope.organization_id, scope.product_id, scope.workspace_id, scope.project_id,
        scope.resource_id, scope.owner_party_id, scope.global_scope,
    )


def _scope_valid(scope: object, resource_id: str) -> bool:
    if (
        type(scope) is not AuthorityScope
        or scope.global_scope is not False
        or scope.organization_id is None
        or scope.product_id is None
        or scope.resource_id != resource_id
    ):
        return False
    try:
        result = inherit_derived_visibility(
            (
                SourceVisibility(
                    source_resource_id=resource_id,
                    classification=ResourceClassification.RESTRICTED,
                    visible_fields=frozenset(),
                    required_scopes=(scope,),
                    authorized=True,
                    policy_id="truth.scope",
                    policy_version="v1",
                ),
            ),
            artifact_kind=DerivedArtifactKind.ANALYSIS,
        )
        return result.allowed is True
    except Exception:
        return False


def _scope_compatible(left: AuthorityScope, right: AuthorityScope) -> bool:
    return all(
        getattr(left, name) == getattr(right, name)
        for name in (
            "organization_id", "product_id", "workspace_id", "project_id", "owner_party_id"
        )
    )


_CLASSIFICATION_RANK = {
    ResourceClassification.PUBLIC: 0,
    ResourceClassification.INTERNAL: 1,
    ResourceClassification.CONFIDENTIAL: 2,
    ResourceClassification.RESTRICTED: 3,
}


def _visibility_compatible(derived: object, source: object) -> bool:
    try:
        return _CLASSIFICATION_RANK[derived.classification] >= _CLASSIFICATION_RANK[source.classification]
    except Exception:
        return False


def _canonical_refs(values: object, validator, error: TruthErrorCode) -> tuple[str, ...]:
    if type(values) is not tuple or len(values) > MAX_REFERENCES_PER_RECORD:
        _fail(error)
    checked: list[str] = []
    for value in values:
        try:
            checked.append(validator(value))
        except Exception:
            _fail(error)
    return tuple(sorted(set(checked)))


@dataclass(frozen=True, slots=True)
class ActorReference:
    actor_ref: str = field(repr=False)
    actor_kind: ActorKind
    generator_identifier: str | None = field(default=None, repr=False)
    generator_version: str | None = None
    _marker: object = field(init=False, repr=False, compare=False)
    _integrity: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_code(self.actor_ref, TruthErrorCode.INVALID_ACTOR)
        if type(self.actor_kind) is not ActorKind:
            _fail(TruthErrorCode.INVALID_ACTOR)
        if (self.generator_identifier is None) != (self.generator_version is None):
            _fail(TruthErrorCode.INVALID_ACTOR)
        if self.actor_kind is ActorKind.AGENT and self.generator_identifier is None:
            _fail(TruthErrorCode.INVALID_ACTOR)
        if self.actor_kind is not ActorKind.AGENT and self.generator_identifier is not None:
            _fail(TruthErrorCode.INVALID_ACTOR)
        if self.generator_identifier is not None:
            _require_code(self.generator_identifier, TruthErrorCode.INVALID_ACTOR)
            _require_code(self.generator_version, TruthErrorCode.INVALID_ACTOR, sensitive=False)
        object.__setattr__(self, "_marker", _ACTOR_MARKER)
        object.__setattr__(self, "_integrity", _actor_tuple(self))


def _actor_tuple(actor: ActorReference) -> tuple[object, ...]:
    return (actor.actor_ref, actor.actor_kind, actor.generator_identifier, actor.generator_version)


def _valid_actor(actor: object) -> bool:
    try:
        return (
            type(actor) is ActorReference
            and actor._marker is _ACTOR_MARKER
            and actor._integrity == _actor_tuple(actor)
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


def _authority_tuple(value: AuthorityProvenance) -> tuple[object, ...]:
    return (
        value.principal_ref, value.capability_code, value.resource_type,
        value.resource_id, value.policy_id, value.policy_version,
    )


def _validate_authority(
    decision: object,
    *,
    resource_type: str,
    resource_id: str,
    scope: AuthorityScope,
    policy_id: str,
    policy_version: str,
    actor_ref: str | None = None,
    capability_code: str | None = None,
    require_allowed: bool,
) -> AuthorizationDecision:
    try:
        project_authorized_fields({}, decision, behavior=ProjectionBehavior.OMIT)
    except Exception:
        _fail(TruthErrorCode.INVALID_AUTHORITY_DECISION)
    if (
        type(decision) is not AuthorizationDecision
        or decision.resource_type != resource_type
        or decision.resource_id != resource_id
        or decision.policy_id != policy_id
        or decision.policy_version != policy_version
        or (decision.allowed and decision.effective_scope != scope)
        or (require_allowed and decision.allowed is not True)
        or (actor_ref is not None and decision.principal_ref != actor_ref)
        or (capability_code is not None and decision.action_code != capability_code)
    ):
        _fail(TruthErrorCode.INVALID_AUTHORITY_DECISION)
    return decision


def _authority_provenance(decision: AuthorizationDecision) -> AuthorityProvenance:
    if decision.principal_ref is None:
        _fail(TruthErrorCode.INVALID_AUTHORITY_DECISION)
    return AuthorityProvenance(
        decision.principal_ref,
        decision.action_code,
        decision.resource_type,
        decision.resource_id,
        decision.policy_id,
        decision.policy_version,
    )


@dataclass(frozen=True, slots=True, init=False)
class OperationalEvent:
    event_id: str
    event_type: EventType
    scope: AuthorityScope
    context_ids: tuple[str, ...]
    actor: ActorReference
    occurred_at: datetime
    recorded_at: datetime
    received_at: datetime | None
    evidence_ids: tuple[str, ...]
    source_event_id: str | None
    correlation_id: str | None
    classification: ResourceClassification
    visibility_policy_id: str
    visibility_policy_version: str
    reason_code: str | None
    authority_provenance: AuthorityProvenance
    contract_version: str
    _marker: object = field(repr=False, compare=False)
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs
        _fail(TruthErrorCode.INVALID_EVENT)


def _event_tuple(value: OperationalEvent) -> tuple[object, ...]:
    return (
        value.event_id, value.event_type, _scope_tuple(value.scope), value.context_ids,
        value.actor._integrity, value.occurred_at, value.recorded_at, value.received_at,
        value.evidence_ids, value.source_event_id, value.correlation_id,
        value.classification, value.visibility_policy_id, value.visibility_policy_version,
        value.reason_code, _authority_tuple(value.authority_provenance), value.contract_version,
    )


def _valid_event(value: object) -> bool:
    try:
        return (
            type(value) is OperationalEvent
            and value._marker is _EVENT_MARKER
            and _valid_actor(value.actor)
            and value._integrity == _event_tuple(value)
        )
    except Exception:
        return False


def create_operational_event(
    *,
    event_id: str,
    event_type: EventType,
    scope: AuthorityScope,
    context_ids: tuple[str, ...],
    actor: ActorReference,
    occurred_at: datetime,
    recorded_at: datetime,
    authority_decision: AuthorizationDecision,
    classification: ResourceClassification,
    visibility_policy_id: str,
    visibility_policy_version: str,
    received_at: datetime | None = None,
    evidence_ids: tuple[str, ...] = (),
    source_event_id: str | None = None,
    correlation_id: str | None = None,
    reason_code: str | None = None,
    contract_version: str = TRUTH_CONTRACT_VERSION,
) -> OperationalEvent:
    validate_event_id(event_id)
    if type(event_type) is not EventType or not _valid_actor(actor):
        _fail(TruthErrorCode.INVALID_EVENT)
    if contract_version != TRUTH_CONTRACT_VERSION:
        _fail(TruthErrorCode.INVALID_VERSION)
    if not _scope_valid(scope, event_id):
        _fail(TruthErrorCode.INVALID_SCOPE)
    contexts = _canonical_refs(context_ids, validate_context_id, TruthErrorCode.INVALID_EVENT)
    if not contexts:
        _fail(TruthErrorCode.INVALID_EVENT)
    evidence = _canonical_refs(evidence_ids, validate_evidence_id, TruthErrorCode.INVALID_EVENT)
    if source_event_id is not None:
        validate_event_id(source_event_id)
        if source_event_id == event_id:
            _fail(TruthErrorCode.INVALID_EVENT)
    occurred = _utc(occurred_at)
    recorded = _utc(recorded_at)
    received = None if received_at is None else _utc(received_at)
    if recorded < occurred or (received is not None and not occurred <= received <= recorded):
        _fail(TruthErrorCode.CHRONOLOGY_CONFLICT)
    if type(classification) is not ResourceClassification:
        _fail(TruthErrorCode.INVALID_EVENT)
    _require_code(visibility_policy_id, TruthErrorCode.INVALID_EVENT)
    _require_code(visibility_policy_version, TruthErrorCode.INVALID_EVENT, sensitive=False)
    if correlation_id is not None:
        _require_code(correlation_id, TruthErrorCode.INVALID_EVENT)
    if reason_code is not None:
        _require_code(reason_code, TruthErrorCode.INVALID_EVENT, sensitive=False)
    authorized = _validate_authority(
        authority_decision,
        resource_type="truth_event",
        resource_id=event_id,
        scope=scope,
        policy_id=visibility_policy_id,
        policy_version=visibility_policy_version,
        actor_ref=actor.actor_ref,
        capability_code="event.create",
        require_allowed=True,
    )
    value = object.__new__(OperationalEvent)
    fields = {
        "event_id": event_id, "event_type": event_type, "scope": scope,
        "context_ids": contexts, "actor": actor, "occurred_at": occurred,
        "recorded_at": recorded, "received_at": received, "evidence_ids": evidence,
        "source_event_id": source_event_id, "correlation_id": correlation_id,
        "classification": classification, "visibility_policy_id": visibility_policy_id,
        "visibility_policy_version": visibility_policy_version, "reason_code": reason_code,
        "authority_provenance": _authority_provenance(authorized),
        "contract_version": contract_version,
    }
    for name, item in fields.items():
        object.__setattr__(value, name, item)
    object.__setattr__(value, "_marker", _EVENT_MARKER)
    object.__setattr__(value, "_integrity", _event_tuple(value))
    return value


@dataclass(frozen=True, slots=True, init=False)
class DecisionRecord:
    decision_id: str
    decision_type: DecisionType
    target_type: TargetType
    target_id: str
    scope: AuthorityScope
    decision_maker: ActorReference
    issued_at: datetime
    effective_at: datetime
    expires_at: datetime | None
    outcome: DecisionOutcome
    status: DecisionStatus
    supporting_event_ids: tuple[str, ...]
    supporting_evidence_ids: tuple[str, ...]
    supporting_context_ids: tuple[str, ...]
    prior_decision_ids: tuple[str, ...]
    approval_ids: tuple[str, ...]
    classification: ResourceClassification
    visibility_policy_id: str
    visibility_policy_version: str
    reason_code: str | None
    authority_provenance: AuthorityProvenance
    contract_version: str
    _marker: object = field(repr=False, compare=False)
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs
        _fail(TruthErrorCode.INVALID_DECISION)


def _decision_tuple(value: DecisionRecord) -> tuple[object, ...]:
    return (
        value.decision_id, value.decision_type, value.target_type, value.target_id,
        _scope_tuple(value.scope), value.decision_maker._integrity, value.issued_at,
        value.effective_at, value.expires_at, value.outcome, value.status,
        value.supporting_event_ids, value.supporting_evidence_ids,
        value.supporting_context_ids, value.prior_decision_ids, value.approval_ids,
        value.classification, value.visibility_policy_id, value.visibility_policy_version,
        value.reason_code, _authority_tuple(value.authority_provenance), value.contract_version,
    )


def _valid_decision(value: object) -> bool:
    try:
        return (
            type(value) is DecisionRecord
            and value._marker is _DECISION_MARKER
            and _valid_actor(value.decision_maker)
            and value._integrity == _decision_tuple(value)
        )
    except Exception:
        return False


def _validate_target_id(target_type: TargetType, target_id: object) -> str:
    if type(target_type) is not TargetType:
        _fail(TruthErrorCode.MISSING_REFERENCE)
    try:
        if target_type is TargetType.EVENT:
            return validate_event_id(target_id)
        if target_type is TargetType.DECISION:
            return validate_decision_id(target_id)
        if target_type is TargetType.APPROVAL:
            return validate_approval_id(target_id)
        if target_type is TargetType.EVIDENCE:
            return validate_evidence_id(target_id)
        return validate_context_id(target_id)
    except Exception:
        _fail(TruthErrorCode.MISSING_REFERENCE)


def create_decision_record(
    *,
    decision_id: str,
    decision_type: DecisionType,
    target_type: TargetType,
    target_id: str,
    scope: AuthorityScope,
    decision_maker: ActorReference,
    issued_at: datetime,
    outcome: DecisionOutcome,
    status: DecisionStatus,
    authority_decision: AuthorizationDecision,
    classification: ResourceClassification,
    visibility_policy_id: str,
    visibility_policy_version: str,
    effective_at: datetime | None = None,
    expires_at: datetime | None = None,
    supporting_event_ids: tuple[str, ...] = (),
    supporting_evidence_ids: tuple[str, ...] = (),
    supporting_context_ids: tuple[str, ...] = (),
    prior_decision_ids: tuple[str, ...] = (),
    approval_ids: tuple[str, ...] = (),
    reason_code: str | None = None,
    contract_version: str = TRUTH_CONTRACT_VERSION,
) -> DecisionRecord:
    validate_decision_id(decision_id)
    if (
        type(decision_type) is not DecisionType
        or type(outcome) is not DecisionOutcome
        or type(status) is not DecisionStatus
        or not _valid_actor(decision_maker)
    ):
        _fail(TruthErrorCode.INVALID_DECISION)
    target = _validate_target_id(target_type, target_id)
    if contract_version != TRUTH_CONTRACT_VERSION:
        _fail(TruthErrorCode.INVALID_VERSION)
    if not _scope_valid(scope, decision_id):
        _fail(TruthErrorCode.INVALID_SCOPE)
    issued = _utc(issued_at)
    effective = issued if effective_at is None else _utc(effective_at)
    expiry = None if expires_at is None else _utc(expires_at)
    if effective < issued or (expiry is not None and expiry <= effective):
        _fail(TruthErrorCode.CHRONOLOGY_CONFLICT)
    events = _canonical_refs(supporting_event_ids, validate_event_id, TruthErrorCode.INVALID_DECISION)
    evidence = _canonical_refs(
        supporting_evidence_ids, validate_evidence_id, TruthErrorCode.INVALID_DECISION
    )
    contexts = _canonical_refs(
        supporting_context_ids, validate_context_id, TruthErrorCode.INVALID_DECISION
    )
    prior = _canonical_refs(prior_decision_ids, validate_decision_id, TruthErrorCode.INVALID_DECISION)
    approvals = _canonical_refs(approval_ids, validate_approval_id, TruthErrorCode.INVALID_DECISION)
    basis_count = len(events) + len(evidence) + len(contexts) + len(prior) + len(approvals)
    if status is not DecisionStatus.PROPOSED and basis_count == 0:
        _fail(TruthErrorCode.INVALID_DECISION)
    if type(classification) is not ResourceClassification:
        _fail(TruthErrorCode.INVALID_DECISION)
    _require_code(visibility_policy_id, TruthErrorCode.INVALID_DECISION)
    _require_code(visibility_policy_version, TruthErrorCode.INVALID_DECISION, sensitive=False)
    if reason_code is not None:
        _require_code(reason_code, TruthErrorCode.INVALID_DECISION, sensitive=False)
    authorized = _validate_authority(
        authority_decision,
        resource_type="truth_decision",
        resource_id=decision_id,
        scope=scope,
        policy_id=visibility_policy_id,
        policy_version=visibility_policy_version,
        actor_ref=decision_maker.actor_ref,
        capability_code="decision.issue",
        require_allowed=True,
    )
    value = object.__new__(DecisionRecord)
    fields = {
        "decision_id": decision_id, "decision_type": decision_type,
        "target_type": target_type, "target_id": target, "scope": scope,
        "decision_maker": decision_maker, "issued_at": issued,
        "effective_at": effective, "expires_at": expiry, "outcome": outcome,
        "status": status, "supporting_event_ids": events,
        "supporting_evidence_ids": evidence, "supporting_context_ids": contexts,
        "prior_decision_ids": prior, "approval_ids": approvals,
        "classification": classification, "visibility_policy_id": visibility_policy_id,
        "visibility_policy_version": visibility_policy_version, "reason_code": reason_code,
        "authority_provenance": _authority_provenance(authorized),
        "contract_version": contract_version,
    }
    for name, item in fields.items():
        object.__setattr__(value, name, item)
    object.__setattr__(value, "_marker", _DECISION_MARKER)
    object.__setattr__(value, "_integrity", _decision_tuple(value))
    return value


@dataclass(frozen=True, slots=True, init=False)
class ApprovalRecord:
    approval_id: str
    approval_type: ApprovalType
    target_type: TargetType
    target_id: str
    scope: AuthorityScope
    approver: ActorReference
    status: ApprovalStatus
    recorded_at: datetime
    effective_at: datetime
    expires_at: datetime | None
    supporting_event_ids: tuple[str, ...]
    supporting_evidence_ids: tuple[str, ...]
    classification: ResourceClassification
    visibility_policy_id: str
    visibility_policy_version: str
    conditions_code: str | None
    reason_code: str | None
    authority_provenance: AuthorityProvenance
    contract_version: str
    _marker: object = field(repr=False, compare=False)
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs
        _fail(TruthErrorCode.INVALID_APPROVAL)


def _approval_tuple(value: ApprovalRecord) -> tuple[object, ...]:
    return (
        value.approval_id, value.approval_type, value.target_type, value.target_id,
        _scope_tuple(value.scope), value.approver._integrity, value.status,
        value.recorded_at, value.effective_at, value.expires_at,
        value.supporting_event_ids, value.supporting_evidence_ids, value.classification,
        value.visibility_policy_id, value.visibility_policy_version,
        value.conditions_code, value.reason_code, _authority_tuple(value.authority_provenance),
        value.contract_version,
    )


def _valid_approval(value: object) -> bool:
    try:
        return (
            type(value) is ApprovalRecord
            and value._marker is _APPROVAL_MARKER
            and _valid_actor(value.approver)
            and value._integrity == _approval_tuple(value)
        )
    except Exception:
        return False


def create_approval_record(
    *,
    approval_id: str,
    approval_type: ApprovalType,
    target_type: TargetType,
    target_id: str,
    scope: AuthorityScope,
    approver: ActorReference,
    status: ApprovalStatus,
    recorded_at: datetime,
    authority_decision: AuthorizationDecision,
    classification: ResourceClassification,
    visibility_policy_id: str,
    visibility_policy_version: str,
    effective_at: datetime | None = None,
    expires_at: datetime | None = None,
    supporting_event_ids: tuple[str, ...] = (),
    supporting_evidence_ids: tuple[str, ...] = (),
    conditions_code: str | None = None,
    reason_code: str | None = None,
    authority_capability: str = "approval.record",
    contract_version: str = TRUTH_CONTRACT_VERSION,
) -> ApprovalRecord:
    validate_approval_id(approval_id)
    if (
        type(approval_type) is not ApprovalType
        or type(status) is not ApprovalStatus
        or not _valid_actor(approver)
    ):
        _fail(TruthErrorCode.INVALID_APPROVAL)
    target = _validate_target_id(target_type, target_id)
    if contract_version != TRUTH_CONTRACT_VERSION:
        _fail(TruthErrorCode.INVALID_VERSION)
    if not _scope_valid(scope, approval_id):
        _fail(TruthErrorCode.INVALID_SCOPE)
    recorded = _utc(recorded_at)
    effective = recorded if effective_at is None else _utc(effective_at)
    expiry = None if expires_at is None else _utc(expires_at)
    if effective < recorded or (expiry is not None and expiry <= effective):
        _fail(TruthErrorCode.CHRONOLOGY_CONFLICT)
    events = _canonical_refs(supporting_event_ids, validate_event_id, TruthErrorCode.INVALID_APPROVAL)
    evidence = _canonical_refs(
        supporting_evidence_ids, validate_evidence_id, TruthErrorCode.INVALID_APPROVAL
    )
    if type(classification) is not ResourceClassification:
        _fail(TruthErrorCode.INVALID_APPROVAL)
    _require_code(visibility_policy_id, TruthErrorCode.INVALID_APPROVAL)
    _require_code(visibility_policy_version, TruthErrorCode.INVALID_APPROVAL, sensitive=False)
    if conditions_code is not None:
        _require_code(conditions_code, TruthErrorCode.INVALID_APPROVAL, sensitive=False)
    if reason_code is not None:
        _require_code(reason_code, TruthErrorCode.INVALID_APPROVAL, sensitive=False)
    _require_code(authority_capability, TruthErrorCode.INVALID_APPROVAL)
    if not authority_capability.startswith("approval."):
        _fail(TruthErrorCode.INVALID_APPROVAL)
    authorized = _validate_authority(
        authority_decision,
        resource_type="truth_approval",
        resource_id=approval_id,
        scope=scope,
        policy_id=visibility_policy_id,
        policy_version=visibility_policy_version,
        actor_ref=approver.actor_ref,
        capability_code=authority_capability,
        require_allowed=True,
    )
    value = object.__new__(ApprovalRecord)
    fields = {
        "approval_id": approval_id, "approval_type": approval_type,
        "target_type": target_type, "target_id": target, "scope": scope,
        "approver": approver, "status": status, "recorded_at": recorded,
        "effective_at": effective, "expires_at": expiry,
        "supporting_event_ids": events, "supporting_evidence_ids": evidence,
        "classification": classification, "visibility_policy_id": visibility_policy_id,
        "visibility_policy_version": visibility_policy_version,
        "conditions_code": conditions_code, "reason_code": reason_code,
        "authority_provenance": _authority_provenance(authorized),
        "contract_version": contract_version,
    }
    for name, item in fields.items():
        object.__setattr__(value, name, item)
    object.__setattr__(value, "_marker", _APPROVAL_MARKER)
    object.__setattr__(value, "_integrity", _approval_tuple(value))
    return value


@dataclass(frozen=True, slots=True)
class AccountabilityLineage:
    record_kind: RecordKind
    source_record_id: str
    target_record_id: str
    lineage_type: LineageType
    created_at: datetime
    policy_id: str
    policy_version: str
    contract_version: str = TRUTH_CONTRACT_VERSION
    _marker: object = field(init=False, repr=False, compare=False)
    _integrity: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.record_kind not in {RecordKind.EVENT, RecordKind.DECISION, RecordKind.APPROVAL}:
            _fail(TruthErrorCode.INVALID_LINEAGE)
        _validate_id_for_kind(self.record_kind, self.source_record_id)
        _validate_id_for_kind(self.record_kind, self.target_record_id)
        if self.source_record_id == self.target_record_id or type(self.lineage_type) is not LineageType:
            _fail(TruthErrorCode.INVALID_LINEAGE)
        allowed = {
            RecordKind.EVENT: {LineageType.CORRECTS, LineageType.SUPERSEDES, LineageType.INVALIDATES},
            RecordKind.DECISION: {LineageType.SUPERSEDES, LineageType.WITHDRAWS, LineageType.INVALIDATES},
            RecordKind.APPROVAL: {LineageType.SUPERSEDES, LineageType.WITHDRAWS, LineageType.INVALIDATES},
        }
        if self.lineage_type not in allowed[self.record_kind]:
            _fail(TruthErrorCode.INVALID_LINEAGE)
        object.__setattr__(self, "created_at", _utc(self.created_at))
        _require_code(self.policy_id, TruthErrorCode.INVALID_LINEAGE)
        _require_code(self.policy_version, TruthErrorCode.INVALID_LINEAGE, sensitive=False)
        if self.contract_version != TRUTH_CONTRACT_VERSION:
            _fail(TruthErrorCode.INVALID_VERSION)
        object.__setattr__(self, "_marker", _LINEAGE_MARKER)
        object.__setattr__(self, "_integrity", _lineage_tuple(self))


def _lineage_tuple(value: AccountabilityLineage) -> tuple[object, ...]:
    return (
        value.record_kind, value.source_record_id, value.target_record_id,
        value.lineage_type, value.created_at, value.policy_id,
        value.policy_version, value.contract_version,
    )


def _valid_lineage(value: object) -> bool:
    try:
        return (
            type(value) is AccountabilityLineage
            and value._marker is _LINEAGE_MARKER
            and value._integrity == _lineage_tuple(value)
        )
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class ApprovalSlot:
    capability_code: str
    required_count: int = 1
    _marker: object = field(init=False, repr=False, compare=False)
    _integrity: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_code(self.capability_code, TruthErrorCode.INVALID_REQUIREMENT)
        if type(self.required_count) is not int or not 1 <= self.required_count <= 16:
            _fail(TruthErrorCode.INVALID_REQUIREMENT)
        object.__setattr__(self, "_marker", _APPROVAL_SLOT_MARKER)
        object.__setattr__(self, "_integrity", (self.capability_code, self.required_count))


def _valid_approval_slot(value: object) -> bool:
    try:
        return (
            type(value) is ApprovalSlot
            and value._marker is _APPROVAL_SLOT_MARKER
            and value._integrity == (value.capability_code, value.required_count)
        )
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class ApprovalRequirement:
    requirement_id: str
    target_type: TargetType
    target_id: str
    scope: AuthorityScope
    slots: tuple[ApprovalSlot, ...]
    policy_id: str
    policy_version: str
    allow_same_approver_across_slots: bool = False
    contract_version: str = TRUTH_CONTRACT_VERSION
    _marker: object = field(init=False, repr=False, compare=False)
    _integrity: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_code(self.requirement_id, TruthErrorCode.INVALID_REQUIREMENT)
        _validate_target_id(self.target_type, self.target_id)
        if not _scope_valid(self.scope, self.target_id):
            _fail(TruthErrorCode.INVALID_REQUIREMENT)
        if type(self.slots) is not tuple or len(self.slots) > MAX_REQUIREMENT_SLOTS:
            _fail(TruthErrorCode.INVALID_REQUIREMENT)
        if any(not _valid_approval_slot(item) for item in self.slots):
            _fail(TruthErrorCode.INVALID_REQUIREMENT)
        if len({item.capability_code for item in self.slots}) != len(self.slots):
            _fail(TruthErrorCode.INVALID_REQUIREMENT)
        _require_code(self.policy_id, TruthErrorCode.INVALID_REQUIREMENT)
        _require_code(self.policy_version, TruthErrorCode.INVALID_REQUIREMENT, sensitive=False)
        if type(self.allow_same_approver_across_slots) is not bool:
            _fail(TruthErrorCode.INVALID_REQUIREMENT)
        if self.contract_version != TRUTH_CONTRACT_VERSION:
            _fail(TruthErrorCode.INVALID_VERSION)
        object.__setattr__(self, "_marker", _APPROVAL_REQUIREMENT_MARKER)
        object.__setattr__(
            self,
            "_integrity",
            (
                self.requirement_id, self.target_type, self.target_id, _scope_tuple(self.scope),
                tuple(item._integrity for item in self.slots), self.policy_id,
                self.policy_version, self.allow_same_approver_across_slots,
                self.contract_version,
            ),
        )


def _valid_approval_requirement(value: object) -> bool:
    try:
        return (
            type(value) is ApprovalRequirement
            and value._marker is _APPROVAL_REQUIREMENT_MARKER
            and all(_valid_approval_slot(item) for item in value.slots)
            and value._integrity
            == (
                value.requirement_id, value.target_type, value.target_id,
                _scope_tuple(value.scope), tuple(item._integrity for item in value.slots),
                value.policy_id, value.policy_version,
                value.allow_same_approver_across_slots, value.contract_version,
            )
        )
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class ApprovalRequirementResult:
    status: RequirementStatus
    reason_code: RequirementReason
    requirement_id: str
    satisfied_slots: tuple[str, ...]
    missing_slots: tuple[str, ...]


def evaluate_approval_requirement(
    requirement: ApprovalRequirement,
    approvals: Sequence[ApprovalRecord],
    *,
    evaluated_at: datetime,
) -> ApprovalRequirementResult:
    if not _valid_approval_requirement(requirement) or type(approvals) not in (list, tuple):
        _fail(TruthErrorCode.INVALID_REQUIREMENT)
    now = _utc(evaluated_at, TruthErrorCode.INVALID_REQUIREMENT)
    if not requirement.slots:
        return ApprovalRequirementResult(
            RequirementStatus.SATISFIED,
            RequirementReason.NO_APPROVAL_REQUIRED,
            requirement.requirement_id,
            (),
            (),
        )
    if any(not _valid_approval(item) for item in approvals):
        return ApprovalRequirementResult(
            RequirementStatus.INVALID, RequirementReason.INVALID_REQUIREMENT,
            requirement.requirement_id, (), tuple(item.capability_code for item in requirement.slots),
        )
    matching = [
        item for item in approvals
        if item.target_type is requirement.target_type and item.target_id == requirement.target_id
    ]
    if any(
        not _scope_compatible(item.scope, requirement.scope)
        or item.visibility_policy_id != requirement.policy_id
        or item.visibility_policy_version != requirement.policy_version
        for item in matching
    ):
        return ApprovalRequirementResult(
            RequirementStatus.INVALID, RequirementReason.INVALID_REQUIREMENT,
            requirement.requirement_id, (), tuple(item.capability_code for item in requirement.slots),
        )
    contradictory: dict[tuple[str, str], set[ApprovalStatus]] = {}
    for item in matching:
        contradictory.setdefault(
            (item.approver.actor_ref, item.authority_provenance.capability_code), set()
        ).add(item.status)
    if any(
        ApprovalStatus.APPROVED in statuses
        and statuses & {ApprovalStatus.REJECTED, ApprovalStatus.WITHDRAWN, ApprovalStatus.INVALIDATED}
        for statuses in contradictory.values()
    ):
        return ApprovalRequirementResult(
            RequirementStatus.CONFLICT, RequirementReason.CONFLICTING_APPROVALS,
            requirement.requirement_id, (), tuple(item.capability_code for item in requirement.slots),
        )

    used_approvers: set[str] = set()
    satisfied: list[str] = []
    missing: list[str] = []
    inactive_found = False
    expired_found = False
    for slot in requirement.slots:
        candidates = []
        seen_for_slot: set[str] = set()
        for item in matching:
            if item.authority_provenance.capability_code != slot.capability_code:
                continue
            if item.status is not ApprovalStatus.APPROVED:
                inactive_found = True
                continue
            if item.expires_at is not None and item.expires_at <= now:
                expired_found = True
                continue
            if item.effective_at > now or item.approver.actor_ref in seen_for_slot:
                continue
            if not requirement.allow_same_approver_across_slots and item.approver.actor_ref in used_approvers:
                continue
            seen_for_slot.add(item.approver.actor_ref)
            candidates.append(item)
        if len(candidates) >= slot.required_count:
            selected = candidates[: slot.required_count]
            satisfied.append(slot.capability_code)
            if not requirement.allow_same_approver_across_slots:
                used_approvers.update(item.approver.actor_ref for item in selected)
        else:
            missing.append(slot.capability_code)
    if not missing:
        return ApprovalRequirementResult(
            RequirementStatus.SATISFIED, RequirementReason.APPROVALS_SATISFIED,
            requirement.requirement_id, tuple(satisfied), (),
        )
    reason = (
        RequirementReason.APPROVAL_EXPIRED if expired_found
        else RequirementReason.APPROVAL_INACTIVE if inactive_found
        else RequirementReason.MISSING_APPROVAL
    )
    return ApprovalRequirementResult(
        RequirementStatus.UNRESOLVED, reason, requirement.requirement_id,
        tuple(satisfied), tuple(missing),
    )


def _event_metadata(record: OperationalEvent) -> dict[str, Any]:
    if not _valid_event(record):
        _fail(TruthErrorCode.INVALID_EVENT)
    return {
        "contract_version": record.contract_version, "event_id": record.event_id,
        "event_type": record.event_type.value, "actor_ref": record.actor.actor_ref,
        "actor_kind": record.actor.actor_kind.value, "occurred_at": _iso(record.occurred_at),
        "recorded_at": _iso(record.recorded_at), "received_at": _iso(record.received_at),
        "source_event_id": record.source_event_id, "correlation_id": record.correlation_id,
        "classification": record.classification.value,
        "visibility_policy_id": record.visibility_policy_id,
        "visibility_policy_version": record.visibility_policy_version,
        "reason_code": record.reason_code, "context_count": len(record.context_ids),
        "evidence_count": len(record.evidence_ids),
        "scope_organization_id": record.scope.organization_id,
        "scope_product_id": record.scope.product_id,
        "scope_workspace_id": record.scope.workspace_id,
        "scope_project_id": record.scope.project_id,
        "scope_resource_id": record.scope.resource_id,
        "scope_owner_party_id": record.scope.owner_party_id,
        "scope_global": record.scope.global_scope,
    }


def _decision_metadata(record: DecisionRecord) -> dict[str, Any]:
    if not _valid_decision(record):
        _fail(TruthErrorCode.INVALID_DECISION)
    return {
        "contract_version": record.contract_version, "decision_id": record.decision_id,
        "decision_type": record.decision_type.value, "target_type": record.target_type.value,
        "target_id": record.target_id, "actor_ref": record.decision_maker.actor_ref,
        "actor_kind": record.decision_maker.actor_kind.value,
        "issued_at": _iso(record.issued_at), "effective_at": _iso(record.effective_at),
        "expires_at": _iso(record.expires_at), "outcome": record.outcome.value,
        "status": record.status.value, "classification": record.classification.value,
        "visibility_policy_id": record.visibility_policy_id,
        "visibility_policy_version": record.visibility_policy_version,
        "reason_code": record.reason_code,
        "basis_count": sum((len(record.supporting_event_ids), len(record.supporting_evidence_ids),
                            len(record.supporting_context_ids), len(record.prior_decision_ids),
                            len(record.approval_ids))),
        "scope_organization_id": record.scope.organization_id,
        "scope_product_id": record.scope.product_id,
        "scope_workspace_id": record.scope.workspace_id,
        "scope_project_id": record.scope.project_id,
        "scope_resource_id": record.scope.resource_id,
        "scope_owner_party_id": record.scope.owner_party_id,
        "scope_global": record.scope.global_scope,
    }


def _approval_metadata(record: ApprovalRecord) -> dict[str, Any]:
    if not _valid_approval(record):
        _fail(TruthErrorCode.INVALID_APPROVAL)
    return {
        "contract_version": record.contract_version, "approval_id": record.approval_id,
        "approval_type": record.approval_type.value, "target_type": record.target_type.value,
        "target_id": record.target_id, "actor_ref": record.approver.actor_ref,
        "actor_kind": record.approver.actor_kind.value, "status": record.status.value,
        "recorded_at": _iso(record.recorded_at), "effective_at": _iso(record.effective_at),
        "expires_at": _iso(record.expires_at), "classification": record.classification.value,
        "visibility_policy_id": record.visibility_policy_id,
        "visibility_policy_version": record.visibility_policy_version,
        "conditions_code": record.conditions_code, "reason_code": record.reason_code,
        "support_count": len(record.supporting_event_ids) + len(record.supporting_evidence_ids),
        "scope_organization_id": record.scope.organization_id,
        "scope_product_id": record.scope.product_id,
        "scope_workspace_id": record.scope.workspace_id,
        "scope_project_id": record.scope.project_id,
        "scope_resource_id": record.scope.resource_id,
        "scope_owner_party_id": record.scope.owner_party_id,
        "scope_global": record.scope.global_scope,
    }


_COMMON_PROJECTION_FIELDS = frozenset(
    {
        "contract_version", "actor_ref", "actor_kind", "classification",
        "visibility_policy_id", "visibility_policy_version", "reason_code",
        "scope_organization_id", "scope_product_id", "scope_workspace_id",
        "scope_project_id", "scope_resource_id", "scope_owner_party_id", "scope_global",
    }
)
_EVENT_PROJECTION_FIELDS = _COMMON_PROJECTION_FIELDS | frozenset(
    {"event_id", "event_type", "occurred_at", "recorded_at", "received_at",
     "source_event_id", "correlation_id", "context_count", "evidence_count"}
)
_DECISION_PROJECTION_FIELDS = _COMMON_PROJECTION_FIELDS | frozenset(
    {"decision_id", "decision_type", "target_type", "target_id", "issued_at",
     "effective_at", "expires_at", "outcome", "status", "basis_count"}
)
_APPROVAL_PROJECTION_FIELDS = _COMMON_PROJECTION_FIELDS | frozenset(
    {"approval_id", "approval_type", "target_type", "target_id", "status",
     "recorded_at", "effective_at", "expires_at", "conditions_code", "support_count"}
)
_COMPUTED_PROJECTION_FIELDS = frozenset(
    {"context_count", "evidence_count", "basis_count", "support_count"}
)
_MAPPING_ENUM_FIELDS = {
    "event_id": {"event_type": EventType},
    "decision_id": {
        "decision_type": DecisionType,
        "target_type": TargetType,
        "outcome": DecisionOutcome,
        "status": DecisionStatus,
    },
    "approval_id": {
        "approval_type": ApprovalType,
        "target_type": TargetType,
        "status": ApprovalStatus,
    },
}


def _project_record(
    record: object,
    decision: AuthorizationDecision,
    *,
    record_type: type,
    validator,
    metadata_builder,
    resource_type: str,
    id_field: str,
    projection_fields: frozenset[str],
) -> dict[str, Any]:
    try:
        if type(record) is record_type:
            if not validator(record):
                _fail(TruthErrorCode.INVALID_PROJECTION)
            source = metadata_builder(record)
            resource_id = source[id_field]
            scope = record.scope
            policy = (record.visibility_policy_id, record.visibility_policy_version)
        elif isinstance(record, Mapping):
            source = {
                key: value for key, value in record.items()
                if type(key) is str
                and key in projection_fields
                and key not in _COMPUTED_PROJECTION_FIELDS
            }
            required = {
                id_field, "visibility_policy_id", "visibility_policy_version",
                "scope_organization_id", "scope_product_id", "scope_workspace_id",
                "scope_project_id", "scope_resource_id", "scope_owner_party_id", "scope_global",
            }
            if not required <= source.keys():
                _fail(TruthErrorCode.INVALID_PROJECTION)
            if source.get("contract_version") != TRUTH_CONTRACT_VERSION:
                _fail(TruthErrorCode.INVALID_PROJECTION)
            for name, enum_type in _MAPPING_ENUM_FIELDS[id_field].items():
                if name not in source or type(source[name]) is not str:
                    _fail(TruthErrorCode.INVALID_PROJECTION)
                try:
                    enum_type(source[name])
                except ValueError:
                    _fail(TruthErrorCode.INVALID_PROJECTION)
            resource_id = source[id_field]
            {
                "event_id": validate_event_id,
                "decision_id": validate_decision_id,
                "approval_id": validate_approval_id,
            }[id_field](resource_id)
            scope = AuthorityScope(
                organization_id=source["scope_organization_id"],
                product_id=source["scope_product_id"],
                workspace_id=source["scope_workspace_id"],
                project_id=source["scope_project_id"],
                resource_id=source["scope_resource_id"],
                owner_party_id=source["scope_owner_party_id"],
                global_scope=source["scope_global"],
            )
            if not _scope_valid(scope, resource_id):
                _fail(TruthErrorCode.INVALID_PROJECTION)
            policy = (source["visibility_policy_id"], source["visibility_policy_version"])
        else:
            _fail(TruthErrorCode.INVALID_PROJECTION)
        _validate_authority(
            decision, resource_type=resource_type, resource_id=resource_id, scope=scope,
            policy_id=policy[0], policy_version=policy[1], require_allowed=False,
        )
        return project_authorized_fields(source, decision, behavior=ProjectionBehavior.OMIT)
    except TruthContractError:
        raise
    except Exception:
        _fail(TruthErrorCode.INVALID_PROJECTION)


def project_event_record(record: OperationalEvent | Mapping[str, Any], decision: AuthorizationDecision) -> dict[str, Any]:
    return _project_record(
        record, decision, record_type=OperationalEvent, validator=_valid_event,
        metadata_builder=_event_metadata, resource_type="truth_event", id_field="event_id",
        projection_fields=_EVENT_PROJECTION_FIELDS,
    )


def project_decision_record(record: DecisionRecord | Mapping[str, Any], decision: AuthorizationDecision) -> dict[str, Any]:
    return _project_record(
        record, decision, record_type=DecisionRecord, validator=_valid_decision,
        metadata_builder=_decision_metadata, resource_type="truth_decision", id_field="decision_id",
        projection_fields=_DECISION_PROJECTION_FIELDS,
    )


def project_approval_record(record: ApprovalRecord | Mapping[str, Any], decision: AuthorizationDecision) -> dict[str, Any]:
    return _project_record(
        record, decision, record_type=ApprovalRecord, validator=_valid_approval,
        metadata_builder=_approval_metadata, resource_type="truth_approval", id_field="approval_id",
        projection_fields=_APPROVAL_PROJECTION_FIELDS,
    )


@dataclass(frozen=True, slots=True, init=False)
class SafeAuditRecord:
    audit_id: str
    action_code: str
    target_type: str
    target_id: str
    actor_kind: str | None
    actor_ref: str | None = field(repr=False)
    outcome_code: str
    reason_code: str
    policy_id: str
    policy_version: str
    scope_reference: str
    timestamp: str
    correlation_id: str | None
    contract_version: str
    _marker: object = field(repr=False, compare=False)
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs
        _fail(TruthErrorCode.INVALID_AUDIT)


def _audit_tuple(value: SafeAuditRecord) -> tuple[object, ...]:
    return (
        value.audit_id, value.action_code, value.target_type, value.target_id,
        value.actor_kind, value.actor_ref, value.outcome_code, value.reason_code,
        value.policy_id, value.policy_version, value.scope_reference, value.timestamp,
        value.correlation_id, value.contract_version,
    )


def _new_audit(**values: object) -> SafeAuditRecord:
    value = object.__new__(SafeAuditRecord)
    for name, item in values.items():
        object.__setattr__(value, name, item)
    object.__setattr__(value, "_marker", _AUDIT_MARKER)
    object.__setattr__(value, "_integrity", _audit_tuple(value))
    return value


def _validate_audit_target(target_type: str, target_id: str) -> None:
    validators = {
        "truth_event": validate_event_id,
        "truth_decision": validate_decision_id,
        "truth_approval": validate_approval_id,
        "evidence": validate_evidence_id,
        "operational_context": validate_context_id,
    }
    validator = validators.get(target_type)
    if validator is None:
        _fail(TruthErrorCode.INVALID_AUDIT)
    try:
        validator(target_id)
    except Exception:
        _fail(TruthErrorCode.INVALID_AUDIT)


def build_safe_audit_record(
    *,
    audit_id: str,
    action_code: str,
    target_type: str,
    target_id: str,
    decision: AuthorizationDecision,
    timestamp: datetime,
    actor: ActorReference | None = None,
    correlation_id: str | None = None,
) -> SafeAuditRecord:
    validate_audit_id(audit_id)
    _require_code(action_code, TruthErrorCode.INVALID_AUDIT)
    _require_code(target_type, TruthErrorCode.INVALID_AUDIT)
    _require_code(target_id, TruthErrorCode.INVALID_AUDIT, maximum=_MAX_ID_BYTES)
    _validate_audit_target(target_type, target_id)
    try:
        project_authorized_fields({}, decision, behavior=ProjectionBehavior.OMIT)
    except Exception:
        _fail(TruthErrorCode.INVALID_AUDIT)
    if (
        decision.resource_type != target_type
        or decision.resource_id != target_id
        or decision.action_code != action_code
    ):
        _fail(TruthErrorCode.INVALID_AUDIT)
    if correlation_id is not None:
        _require_code(correlation_id, TruthErrorCode.INVALID_AUDIT)
    if actor is not None and not _valid_actor(actor):
        _fail(TruthErrorCode.INVALID_AUDIT)
    include_actor = (
        actor is not None
        and decision.allowed
        and "actor_ref" in decision.visible_fields
        and decision.principal_ref == actor.actor_ref
    )
    return _new_audit(
        audit_id=audit_id,
        action_code=action_code,
        target_type=target_type,
        target_id=target_id,
        actor_kind=actor.actor_kind.value if include_actor else None,
        actor_ref=actor.actor_ref if include_actor else None,
        outcome_code=AuditOutcome.ALLOWED.value if decision.allowed else AuditOutcome.DENIED.value,
        reason_code=decision.reason_code.value,
        policy_id=decision.policy_id,
        policy_version=decision.policy_version,
        scope_reference=target_id,
        timestamp=_iso(_utc(timestamp, TruthErrorCode.INVALID_AUDIT)) or "",
        correlation_id=correlation_id,
        contract_version=TRUTH_CONTRACT_VERSION,
    )


def build_rejection_audit_record(
    *,
    audit_id: str,
    action_code: str,
    target_type: str,
    target_id: str,
    reason_code: TruthErrorCode,
    policy_id: str,
    policy_version: str,
    timestamp: datetime,
    correlation_id: str | None = None,
) -> SafeAuditRecord:
    validate_audit_id(audit_id)
    _require_code(action_code, TruthErrorCode.INVALID_AUDIT)
    _require_code(target_type, TruthErrorCode.INVALID_AUDIT)
    _require_code(target_id, TruthErrorCode.INVALID_AUDIT, maximum=_MAX_ID_BYTES)
    if type(reason_code) is not TruthErrorCode:
        _fail(TruthErrorCode.INVALID_AUDIT)
    _require_code(policy_id, TruthErrorCode.INVALID_AUDIT)
    _require_code(policy_version, TruthErrorCode.INVALID_AUDIT, sensitive=False)
    if correlation_id is not None:
        _require_code(correlation_id, TruthErrorCode.INVALID_AUDIT)
    return _new_audit(
        audit_id=audit_id, action_code=action_code, target_type=target_type,
        target_id=target_id, actor_kind=None, actor_ref=None,
        outcome_code=AuditOutcome.REJECTED.value, reason_code=reason_code.value,
        policy_id=policy_id, policy_version=policy_version, scope_reference=target_id,
        timestamp=_iso(_utc(timestamp, TruthErrorCode.INVALID_AUDIT)) or "",
        correlation_id=correlation_id, contract_version=TRUTH_CONTRACT_VERSION,
    )


def _record_time(record: object) -> datetime:
    if type(record) is OperationalEvent:
        return record.recorded_at
    if type(record) is DecisionRecord:
        return record.issued_at
    if type(record) is ApprovalRecord:
        return record.recorded_at
    _fail(TruthErrorCode.INVALID_LINEAGE)


def _record_scope_policy(record: object) -> tuple[AuthorityScope, str, str]:
    return record.scope, record.visibility_policy_id, record.visibility_policy_version


def _assert_acyclic(edges: Mapping[object, set[object]]) -> None:
    state: dict[object, int] = {}
    for start in sorted(edges):
        if state.get(start) == 2:
            continue
        stack: list[tuple[object, bool]] = [(start, False)]
        while stack:
            node, exiting = stack.pop()
            if exiting:
                state[node] = 2
                continue
            if state.get(node) == 1:
                _fail(TruthErrorCode.LINEAGE_CYCLE)
            if state.get(node) == 2:
                continue
            state[node] = 1
            stack.append((node, True))
            for target in sorted(edges.get(node, ()), reverse=True):
                if state.get(target) == 1:
                    _fail(TruthErrorCode.LINEAGE_CYCLE)
                if state.get(target) != 2:
                    stack.append((target, False))


@dataclass(frozen=True, slots=True, init=False)
class ValidatedAccountabilityCollection:
    events: tuple[OperationalEvent, ...]
    decisions: tuple[DecisionRecord, ...]
    approvals: tuple[ApprovalRecord, ...]
    lineages: tuple[AccountabilityLineage, ...]
    _marker: object = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs
        _fail(TruthErrorCode.INVALID_EVENT)


def validate_accountability_collection(
    *,
    events: Sequence[OperationalEvent],
    decisions: Sequence[DecisionRecord],
    approvals: Sequence[ApprovalRecord],
    lineages: Sequence[AccountabilityLineage],
    contexts: Sequence[OperationalContextRecord],
    evidence_records: Sequence[EvidenceRecord],
) -> ValidatedAccountabilityCollection:
    collections = (events, decisions, approvals, lineages, contexts, evidence_records)
    if any(type(items) not in (list, tuple) for items in collections):
        _fail(TruthErrorCode.INVALID_EVENT)
    if (
        any(len(items) > MAX_RECORDS_PER_KIND for items in (*collections[:3], *collections[4:]))
        or len(lineages) > MAX_LINEAGES
    ):
        _fail(TruthErrorCode.COLLECTION_LIMIT_EXCEEDED)
    if any(not _valid_event(item) for item in events):
        _fail(TruthErrorCode.INVALID_EVENT)
    if any(not _valid_decision(item) for item in decisions):
        _fail(TruthErrorCode.INVALID_DECISION)
    if any(not _valid_approval(item) for item in approvals):
        _fail(TruthErrorCode.INVALID_APPROVAL)
    if any(not _valid_lineage(item) for item in lineages):
        _fail(TruthErrorCode.INVALID_LINEAGE)

    by_kind = {
        RecordKind.EVENT: {item.event_id: item for item in events},
        RecordKind.DECISION: {item.decision_id: item for item in decisions},
        RecordKind.APPROVAL: {item.approval_id: item for item in approvals},
    }
    for kind, items in ((RecordKind.EVENT, events), (RecordKind.DECISION, decisions), (RecordKind.APPROVAL, approvals)):
        if len(by_kind[kind]) != len(items):
            _fail(TruthErrorCode.DUPLICATE_IDENTIFIER)
    all_ids = [*by_kind[RecordKind.EVENT], *by_kind[RecordKind.DECISION], *by_kind[RecordKind.APPROVAL]]
    if len(set(all_ids)) != len(all_ids):
        _fail(TruthErrorCode.DUPLICATE_IDENTIFIER)

    context_by_id: dict[str, OperationalContextRecord] = {}
    for context in contexts:
        try:
            context_metadata(context)
        except OperationalContextError:
            _fail(TruthErrorCode.MISSING_REFERENCE)
        context_by_id[context.context_id] = context
    if len(context_by_id) != len(contexts):
        _fail(TruthErrorCode.DUPLICATE_IDENTIFIER)
    evidence_by_id: dict[str, EvidenceRecord] = {}
    for evidence in evidence_records:
        try:
            evidence_metadata(evidence)
        except EvidenceContractError:
            _fail(TruthErrorCode.MISSING_REFERENCE)
        evidence_by_id[evidence.evidence_id] = evidence
    if len(evidence_by_id) != len(evidence_records):
        _fail(TruthErrorCode.DUPLICATE_IDENTIFIER)

    def compatible(record, referenced) -> None:
        scope, policy_id, policy_version = _record_scope_policy(record)
        if not _scope_compatible(scope, referenced.scope):
            _fail(TruthErrorCode.SCOPE_CONFLICT)
        if (
            policy_id != referenced.visibility_policy_id
            or policy_version != referenced.visibility_policy_version
        ):
            _fail(TruthErrorCode.POLICY_CONFLICT)
        if not _visibility_compatible(record, referenced):
            _fail(TruthErrorCode.VISIBILITY_CONFLICT)

    for event in events:
        for context_id in event.context_ids:
            context = context_by_id.get(context_id)
            if context is None:
                _fail(TruthErrorCode.MISSING_REFERENCE)
            compatible(event, context)
        for evidence_id in event.evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                _fail(TruthErrorCode.MISSING_REFERENCE)
            compatible(event, evidence)
        if event.source_event_id is not None:
            source = by_kind[RecordKind.EVENT].get(event.source_event_id)
            if source is None:
                _fail(TruthErrorCode.MISSING_REFERENCE)
            compatible(event, source)
            if event.occurred_at < source.recorded_at:
                _fail(TruthErrorCode.CHRONOLOGY_CONFLICT)

    def target_record(target_type: TargetType, target_id: str):
        if target_type is TargetType.EVENT:
            return by_kind[RecordKind.EVENT].get(target_id)
        if target_type is TargetType.DECISION:
            return by_kind[RecordKind.DECISION].get(target_id)
        if target_type is TargetType.APPROVAL:
            return by_kind[RecordKind.APPROVAL].get(target_id)
        if target_type is TargetType.EVIDENCE:
            return evidence_by_id.get(target_id)
        return context_by_id.get(target_id)

    for decision in decisions:
        target = target_record(decision.target_type, decision.target_id)
        if target is None:
            _fail(TruthErrorCode.MISSING_REFERENCE)
        compatible(decision, target)
        refs = (
            ((by_kind[RecordKind.EVENT], decision.supporting_event_ids)),
            ((evidence_by_id, decision.supporting_evidence_ids)),
            ((context_by_id, decision.supporting_context_ids)),
            ((by_kind[RecordKind.DECISION], decision.prior_decision_ids)),
            ((by_kind[RecordKind.APPROVAL], decision.approval_ids)),
        )
        for registry, ids in refs:
            for ref in ids:
                item = registry.get(ref)
                if item is None:
                    _fail(TruthErrorCode.MISSING_REFERENCE)
                compatible(decision, item)

    for approval in approvals:
        target = target_record(approval.target_type, approval.target_id)
        if target is None:
            _fail(TruthErrorCode.MISSING_REFERENCE)
        compatible(approval, target)
        for registry, ids in (
            (by_kind[RecordKind.EVENT], approval.supporting_event_ids),
            (evidence_by_id, approval.supporting_evidence_ids),
        ):
            for ref in ids:
                item = registry.get(ref)
                if item is None:
                    _fail(TruthErrorCode.MISSING_REFERENCE)
                compatible(approval, item)

    edges: dict[tuple[RecordKind, str], set[tuple[RecordKind, str]]] = {}
    lineage_keys: set[tuple[RecordKind, str, str]] = set()
    superseded_targets: set[str] = set()
    for lineage in lineages:
        lineage_key = (lineage.record_kind, lineage.source_record_id, lineage.target_record_id)
        if lineage_key in lineage_keys:
            _fail(TruthErrorCode.INVALID_LINEAGE)
        lineage_keys.add(lineage_key)
        source = by_kind[lineage.record_kind].get(lineage.source_record_id)
        target = by_kind[lineage.record_kind].get(lineage.target_record_id)
        if source is None or target is None:
            _fail(TruthErrorCode.MISSING_REFERENCE)
        if not _scope_compatible(source.scope, target.scope):
            _fail(TruthErrorCode.SCOPE_CONFLICT)
        if (
            lineage.policy_id != source.visibility_policy_id
            or lineage.policy_id != target.visibility_policy_id
            or lineage.policy_version != source.visibility_policy_version
            or lineage.policy_version != target.visibility_policy_version
        ):
            _fail(TruthErrorCode.POLICY_CONFLICT)
        if not _visibility_compatible(source, target):
            _fail(TruthErrorCode.VISIBILITY_CONFLICT)
        if lineage.created_at < _record_time(source) or lineage.created_at < _record_time(target):
            _fail(TruthErrorCode.CHRONOLOGY_CONFLICT)
        edges.setdefault(
            (lineage.record_kind, lineage.source_record_id), set()
        ).add((lineage.record_kind, lineage.target_record_id))
        if lineage.lineage_type is LineageType.SUPERSEDES:
            superseded_targets.add(lineage.target_record_id)
        if lineage.record_kind is RecordKind.EVENT:
            expected = {
                LineageType.CORRECTS: EventType.CORRECTED,
                LineageType.INVALIDATES: EventType.INVALIDATED,
            }.get(lineage.lineage_type)
            if expected is not None and source.event_type is not expected:
                _fail(TruthErrorCode.INVALID_LINEAGE)
        elif lineage.record_kind is RecordKind.DECISION:
            expected = {
                LineageType.SUPERSEDES: DecisionStatus.SUPERSEDED,
                LineageType.WITHDRAWS: DecisionStatus.WITHDRAWN,
                LineageType.INVALIDATES: DecisionStatus.INVALIDATED,
            }[lineage.lineage_type]
            if source.status is not expected:
                _fail(TruthErrorCode.INVALID_LINEAGE)
        else:
            expected = {
                LineageType.SUPERSEDES: ApprovalStatus.APPROVED,
                LineageType.WITHDRAWS: ApprovalStatus.WITHDRAWN,
                LineageType.INVALIDATES: ApprovalStatus.INVALIDATED,
            }[lineage.lineage_type]
            if source.status is not expected:
                _fail(TruthErrorCode.INVALID_LINEAGE)
    _assert_acyclic(edges)

    active_decisions: dict[tuple[TargetType, str, DecisionType], list[str]] = {}
    for item in decisions:
        if item.status is DecisionStatus.ISSUED and item.decision_id not in superseded_targets:
            active_decisions.setdefault((item.target_type, item.target_id, item.decision_type), []).append(
                item.decision_id
            )
    if any(len(ids) > 1 for ids in active_decisions.values()):
        _fail(TruthErrorCode.CONFLICTING_DECISIONS)

    approval_keys: set[tuple[object, ...]] = set()
    approval_states: dict[tuple[object, ...], set[ApprovalStatus]] = {}
    for item in approvals:
        key = (
            item.target_type, item.target_id, item.approval_type,
            item.approver.actor_ref, item.authority_provenance.capability_code,
        )
        exact = key + (item.status,)
        if exact in approval_keys:
            _fail(TruthErrorCode.DUPLICATE_APPROVAL)
        approval_keys.add(exact)
        approval_states.setdefault(key, set()).add(item.status)
    if any(
        ApprovalStatus.APPROVED in states
        and states & {ApprovalStatus.REJECTED, ApprovalStatus.WITHDRAWN, ApprovalStatus.INVALIDATED}
        for states in approval_states.values()
    ):
        _fail(TruthErrorCode.CONFLICTING_APPROVALS)

    value = object.__new__(ValidatedAccountabilityCollection)
    object.__setattr__(value, "events", tuple(sorted(events, key=lambda item: item.event_id)))
    object.__setattr__(value, "decisions", tuple(sorted(decisions, key=lambda item: item.decision_id)))
    object.__setattr__(value, "approvals", tuple(sorted(approvals, key=lambda item: item.approval_id)))
    object.__setattr__(value, "lineages", tuple(sorted(
        lineages, key=lambda item: (item.record_kind.value, item.source_record_id, item.target_record_id)
    )))
    object.__setattr__(value, "_marker", _COLLECTION_MARKER)
    return value


@dataclass(frozen=True, slots=True)
class DerivedAccountabilityView:
    artifact_kind: DerivedArtifactKind
    classification: ResourceClassification
    visible_fields: frozenset[str]
    required_scopes: tuple[AuthorityScope, ...]
    source_resource_ids: tuple[str, ...]
    policy_id: str
    policy_version: str
    generator_kind: ActorKind
    generator_identifier: str | None
    generator_version: str | None
    derived: bool = True


def create_derived_accountability_view(
    sources: Sequence[tuple[object, AuthorizationDecision]],
    *,
    artifact_kind: DerivedArtifactKind,
    generator: ActorReference,
) -> DerivedAccountabilityView:
    if (
        type(sources) not in (list, tuple)
        or not sources
        or type(artifact_kind) is not DerivedArtifactKind
        or not _valid_actor(generator)
    ):
        _fail(TruthErrorCode.SOURCE_NOT_AUTHORIZED)
    visibility_sources: list[SourceVisibility] = []
    baseline_scope: AuthorityScope | None = None
    seen_source_ids: set[str] = set()
    for pair in sources:
        if type(pair) is not tuple or len(pair) != 2:
            _fail(TruthErrorCode.SOURCE_NOT_AUTHORIZED)
        record, decision = pair
        if type(record) is OperationalEvent and _valid_event(record):
            resource_type, resource_id = "truth_event", record.event_id
        elif type(record) is DecisionRecord and _valid_decision(record):
            resource_type, resource_id = "truth_decision", record.decision_id
        elif type(record) is ApprovalRecord and _valid_approval(record):
            resource_type, resource_id = "truth_approval", record.approval_id
        elif type(record) is EvidenceRecord:
            try:
                project_evidence_metadata(record, decision)
            except EvidenceContractError:
                _fail(TruthErrorCode.SOURCE_NOT_AUTHORIZED)
            resource_type, resource_id = "evidence", record.evidence_id
        elif type(record) is OperationalContextRecord:
            try:
                project_context_metadata(record, decision)
            except OperationalContextError:
                _fail(TruthErrorCode.SOURCE_NOT_AUTHORIZED)
            resource_type, resource_id = "operational_context", record.context_id
        else:
            _fail(TruthErrorCode.SOURCE_NOT_AUTHORIZED)
        if resource_id in seen_source_ids:
            _fail(TruthErrorCode.SOURCE_NOT_AUTHORIZED)
        seen_source_ids.add(resource_id)
        if resource_type.startswith("truth_"):
            _validate_authority(
                decision, resource_type=resource_type, resource_id=resource_id,
                scope=record.scope, policy_id=record.visibility_policy_id,
                policy_version=record.visibility_policy_version, require_allowed=True,
            )
        elif decision.allowed is not True:
            _fail(TruthErrorCode.SOURCE_NOT_AUTHORIZED)
        if baseline_scope is None:
            baseline_scope = record.scope
        elif not _scope_compatible(baseline_scope, record.scope):
            _fail(TruthErrorCode.SOURCE_SCOPE_CONFLICT)
        visibility_sources.append(
            SourceVisibility(
                source_resource_id=resource_id,
                classification=record.classification,
                visible_fields=decision.visible_fields,
                required_scopes=(record.scope,),
                authorized=True,
                policy_id=record.visibility_policy_id,
                policy_version=record.visibility_policy_version,
            )
        )
    inherited = inherit_derived_visibility(
        tuple(sorted(visibility_sources, key=lambda item: item.source_resource_id)),
        artifact_kind=artifact_kind,
    )
    if inherited.reason_code is AuthorityReason.SOURCE_SCOPE_CONFLICT:
        _fail(TruthErrorCode.SOURCE_SCOPE_CONFLICT)
    if inherited.reason_code is AuthorityReason.SOURCE_POLICY_CONFLICT:
        _fail(TruthErrorCode.SOURCE_POLICY_CONFLICT)
    if not inherited.allowed:
        _fail(TruthErrorCode.SOURCE_NOT_AUTHORIZED)
    policy_id, policy_version = inherited.source_policies[0]
    return DerivedAccountabilityView(
        artifact_kind=artifact_kind,
        classification=inherited.classification,
        visible_fields=inherited.visible_fields,
        required_scopes=inherited.required_scopes,
        source_resource_ids=inherited.source_resource_ids,
        policy_id=policy_id,
        policy_version=policy_version,
        generator_kind=generator.actor_kind,
        generator_identifier=generator.generator_identifier,
        generator_version=generator.generator_version,
    )


__all__ = (
    "AccountabilityLineage", "ActorKind", "ActorReference", "ApprovalRecord",
    "ApprovalRequirement", "ApprovalRequirementResult", "ApprovalSlot", "ApprovalStatus",
    "ApprovalType", "AuditOutcome", "DecisionOutcome", "DecisionRecord", "DecisionStatus",
    "DecisionType", "DerivedAccountabilityView", "EventType", "LineageType",
    "MAX_LINEAGES", "MAX_RECORDS_PER_KIND", "OperationalEvent", "RecordKind",
    "RequirementReason", "RequirementStatus", "SafeAuditRecord", "TRUTH_CONTRACT_VERSION",
    "TRUTH_POLICY_VERSION", "TargetType", "TruthContractError", "TruthErrorCode",
    "ValidatedAccountabilityCollection", "build_rejection_audit_record",
    "build_safe_audit_record", "create_approval_record", "create_decision_record",
    "create_derived_accountability_view", "create_operational_event",
    "evaluate_approval_requirement", "project_approval_record", "project_decision_record",
    "project_event_record", "validate_accountability_collection", "validate_approval_id",
    "validate_audit_id", "validate_decision_id", "validate_event_id",
)
