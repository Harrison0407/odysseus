"""Pure MarketMatch Human & AI Work Orchestration Kernel V1.

The contracts in this module do not execute work, persist state, schedule jobs,
call models, write files, emit notifications, or grant authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
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
from src.marketmatch_evidence import EvidenceRecord, evidence_metadata, validate_evidence_id
from src.marketmatch_operational_context import (
    OperationalContextRecord,
    context_metadata,
    validate_context_id,
)
from src.marketmatch_truth_accountability import (
    ActorKind,
    ActorReference,
    ApprovalRecord,
    ApprovalStatus,
    DecisionRecord,
    OperationalEvent,
    TargetType,
    validate_approval_id,
    validate_decision_id,
    validate_event_id,
)


WORK_CONTRACT_VERSION = "marketmatch-work-orchestration-v1"
WORK_POLICY_VERSION = "marketmatch-work-orchestration-policy-v1"
MAX_RECORDS_PER_KIND = 2048
MAX_REFERENCES = 256
MAX_DEPENDENCIES = 8192
MAX_ATTEMPTS = 64
MAX_CRITERIA = 32

_ID_RE = re.compile(r"[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*\Z", re.ASCII)
_SHA_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_MAX_ID = 160
_MAX_CODE = 128
_SENSITIVE = frozenset(
    {
        "address", "amount", "avenue", "bl", "container", "cookie", "cost",
        "credential", "database", "email", "factory", "margin", "markup",
        "password", "po", "price", "prompt", "row", "secret", "session",
        "stderr", "stdout", "street", "supplier", "token", "usd",
    }
)


class WorkErrorCode(str, Enum):
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    INVALID_EXECUTOR = "INVALID_EXECUTOR"
    INVALID_WORK_ITEM = "INVALID_WORK_ITEM"
    INVALID_ASSIGNMENT = "INVALID_ASSIGNMENT"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    INVALID_ATTEMPT = "INVALID_ATTEMPT"
    INVALID_RETRY_POLICY = "INVALID_RETRY_POLICY"
    INVALID_DEPENDENCY = "INVALID_DEPENDENCY"
    INVALID_HANDOFF = "INVALID_HANDOFF"
    INVALID_ESCALATION = "INVALID_ESCALATION"
    INVALID_COMPLETION_CLAIM = "INVALID_COMPLETION_CLAIM"
    INVALID_CRITERIA = "INVALID_CRITERIA"
    INVALID_AUTHORITY_DECISION = "INVALID_AUTHORITY_DECISION"
    INVALID_PROJECTION = "INVALID_PROJECTION"
    INVALID_AUDIT = "INVALID_AUDIT"
    INVALID_VERSION = "INVALID_VERSION"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    INVALID_SCOPE = "INVALID_SCOPE"
    MISSING_REFERENCE = "MISSING_REFERENCE"
    DUPLICATE_IDENTIFIER = "DUPLICATE_IDENTIFIER"
    SCOPE_CONFLICT = "SCOPE_CONFLICT"
    POLICY_CONFLICT = "POLICY_CONFLICT"
    VISIBILITY_CONFLICT = "VISIBILITY_CONFLICT"
    CHRONOLOGY_CONFLICT = "CHRONOLOGY_CONFLICT"
    INVALID_STATUS_TRANSITION = "INVALID_STATUS_TRANSITION"
    DEPENDENCY_CYCLE = "DEPENDENCY_CYCLE"
    HANDOFF_CYCLE = "HANDOFF_CYCLE"
    DUPLICATE_ACTIVE_ASSIGNMENT = "DUPLICATE_ACTIVE_ASSIGNMENT"
    DUPLICATE_ACTIVE_ATTEMPT = "DUPLICATE_ACTIVE_ATTEMPT"
    DUPLICATE_UNRESOLVED_ESCALATION = "DUPLICATE_UNRESOLVED_ESCALATION"
    DUPLICATE_COMPLETION_CLAIM = "DUPLICATE_COMPLETION_CLAIM"
    ASSIGNMENT_NOT_ACCEPTED = "ASSIGNMENT_NOT_ACCEPTED"
    EXECUTOR_MISMATCH = "EXECUTOR_MISMATCH"
    RETRY_NOT_ALLOWED = "RETRY_NOT_ALLOWED"
    CRITERIA_UNSATISFIED = "CRITERIA_UNSATISFIED"
    SELF_VERIFICATION_DENIED = "SELF_VERIFICATION_DENIED"
    SOURCE_NOT_AUTHORIZED = "SOURCE_NOT_AUTHORIZED"
    SOURCE_SCOPE_CONFLICT = "SOURCE_SCOPE_CONFLICT"
    SOURCE_POLICY_CONFLICT = "SOURCE_POLICY_CONFLICT"
    COLLECTION_LIMIT_EXCEEDED = "COLLECTION_LIMIT_EXCEEDED"
    UNSUPPORTED_ADAPTER = "UNSUPPORTED_ADAPTER"


class WorkContractError(ValueError):
    def __init__(self, code: WorkErrorCode):
        self.code = code if type(code) is WorkErrorCode else WorkErrorCode.INVALID_WORK_ITEM
        super().__init__(self.code.value)


class WorkType(str, Enum):
    REVIEW = "REVIEW"
    ANALYZE = "ANALYZE"
    TRANSCRIBE = "TRANSCRIBE"
    EXTRACT = "EXTRACT"
    CLASSIFY = "CLASSIFY"
    VERIFY = "VERIFY"
    RESEARCH = "RESEARCH"
    GENERATE = "GENERATE"
    GENERAL = "GENERAL"


class WorkPriority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class WorkStatus(str, Enum):
    DRAFT = "DRAFT"
    READY = "READY"
    ASSIGNED = "ASSIGNED"
    ACCEPTED = "ACCEPTED"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    WAITING = "WAITING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETION_CLAIMED = "COMPLETION_CLAIMED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


class ExecutorKind(str, Enum):
    HUMAN = "HUMAN"
    AGENT = "AGENT"
    WORKFLOW = "WORKFLOW"
    EXTERNAL_SERVICE = "EXTERNAL_SERVICE"


class AssignmentStatus(str, Enum):
    OFFERED = "OFFERED"
    ASSIGNED = "ASSIGNED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    SUPERSEDED = "SUPERSEDED"
    WITHDRAWN = "WITHDRAWN"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


class AttemptStatus(str, Enum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    INTERRUPTED = "INTERRUPTED"
    INVALIDATED = "INVALIDATED"


class DependencyType(str, Enum):
    REQUIRES = "REQUIRES"
    BLOCKED_BY = "BLOCKED_BY"
    FOLLOWS = "FOLLOWS"
    DUPLICATES = "DUPLICATES"
    SUPERSEDES = "SUPERSEDES"
    RELATED_TO = "RELATED_TO"


class HandoffStatus(str, Enum):
    OFFERED = "OFFERED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    SUPERSEDED = "SUPERSEDED"
    WITHDRAWN = "WITHDRAWN"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


class EscalationSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EscalationStatus(str, Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    WITHDRAWN = "WITHDRAWN"
    INVALIDATED = "INVALIDATED"


class CriterionType(str, Enum):
    RESULT_REFERENCE_REQUIRED = "RESULT_REFERENCE_REQUIRED"
    EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"
    EVENT_REQUIRED = "EVENT_REQUIRED"
    DECISION_REQUIRED = "DECISION_REQUIRED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    INDEPENDENT_VERIFIER_REQUIRED = "INDEPENDENT_VERIFIER_REQUIRED"
    ALL_DEPENDENCIES_COMPLETED = "ALL_DEPENDENCIES_COMPLETED"
    EXECUTOR_DECLARATION_REQUIRED = "EXECUTOR_DECLARATION_REQUIRED"


class ClaimOutcome(str, Enum):
    CLAIMED = "CLAIMED"
    PARTIAL = "PARTIAL"


class EvaluationStatus(str, Enum):
    SATISFIED = "SATISFIED"
    UNSATISFIED = "UNSATISFIED"
    INVALID = "INVALID"


class RetryReason(str, Enum):
    RETRY_ALLOWED = "RETRY_ALLOWED"
    ATTEMPT_LIMIT_REACHED = "ATTEMPT_LIMIT_REACHED"
    REASON_NOT_RETRYABLE = "REASON_NOT_RETRYABLE"
    PRIOR_ATTEMPT_SUCCEEDED = "PRIOR_ATTEMPT_SUCCEEDED"
    PRIOR_ATTEMPT_CANCELLED = "PRIOR_ATTEMPT_CANCELLED"
    PRIOR_ATTEMPT_ACTIVE = "PRIOR_ATTEMPT_ACTIVE"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    EXECUTOR_CHANGED = "EXECUTOR_CHANGED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"


class WorkloadViewKind(str, Enum):
    ASSIGNED_TO = "ASSIGNED_TO"
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"
    OVERDUE = "OVERDUE"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETION_CLAIMED = "COMPLETION_CLAIMED"
    FAILED = "FAILED"
    UNRESOLVED_ESCALATIONS = "UNRESOLVED_ESCALATIONS"


def _fail(code: WorkErrorCode) -> NoReturn:
    raise WorkContractError(code) from None


def _parts(value: str) -> frozenset[str]:
    return frozenset(part for part in re.split(r"[._:-]+", value.lower()) if part)


def _code(value: object, error: WorkErrorCode, *, maximum: int = _MAX_CODE, sensitive: bool = True) -> str:
    if type(value) is not str or not value or _ID_RE.fullmatch(value) is None:
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
        type(value) is not str
        or not value.startswith(prefix)
        or value.count(":") < 2
        or _ID_RE.fullmatch(value) is None
        or len(value.encode("ascii", errors="ignore")) > _MAX_ID
        or not any("a" <= char <= "z" for char in opaque)
        or _SHA_RE.fullmatch(opaque) is not None
        or _parts(value) & _SENSITIVE
    ):
        _fail(WorkErrorCode.INVALID_IDENTIFIER)
    return value


def validate_work_id(value: object) -> str: return _record_id(value, "wrk1:")
def validate_assignment_id(value: object) -> str: return _record_id(value, "asg1:")
def validate_attempt_id(value: object) -> str: return _record_id(value, "atm1:")
def validate_transition_id(value: object) -> str: return _record_id(value, "trn1:")
def validate_handoff_id(value: object) -> str: return _record_id(value, "hnd1:")
def validate_escalation_id(value: object) -> str: return _record_id(value, "esc1:")
def validate_claim_id(value: object) -> str: return _record_id(value, "clm1:")
def validate_work_audit_id(value: object) -> str: return _record_id(value, "waud1:")


def _utc(value: object, error: WorkErrorCode = WorkErrorCode.INVALID_TIMESTAMP) -> datetime:
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
        type(scope) is not AuthorityScope or scope.global_scope is not False
        or scope.organization_id is None or scope.product_id is None
        or scope.resource_id != resource_id
    ):
        return False
    try:
        result = inherit_derived_visibility(
            (SourceVisibility(resource_id, ResourceClassification.RESTRICTED, frozenset(), (scope,), True, "work.scope", "v1"),),
            artifact_kind=DerivedArtifactKind.ANALYSIS,
        )
        return result.allowed is True
    except Exception:
        return False


def _scope_compatible(left: AuthorityScope, right: AuthorityScope) -> bool:
    return all(
        getattr(left, name) == getattr(right, name)
        for name in ("organization_id", "product_id", "workspace_id", "project_id", "owner_party_id")
    )


_CLASS_RANK = {
    ResourceClassification.PUBLIC: 0,
    ResourceClassification.INTERNAL: 1,
    ResourceClassification.CONFIDENTIAL: 2,
    ResourceClassification.RESTRICTED: 3,
}


def _visibility_ok(derived: object, source: object) -> bool:
    try:
        return _CLASS_RANK[derived.classification] >= _CLASS_RANK[source.classification]
    except Exception:
        return False


def _valid_actor(value: object) -> bool:
    try:
        return (
            type(value) is ActorReference
            and value._integrity
            == (value.actor_ref, value.actor_kind, value.generator_identifier, value.generator_version)
        )
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class ExecutorReference:
    executor_id: str = field(repr=False)
    executor_kind: ExecutorKind
    provider_identifier: str | None = field(default=None, repr=False)
    provider_version: str | None = None
    _integrity: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _code(self.executor_id, WorkErrorCode.INVALID_EXECUTOR)
        if type(self.executor_kind) is not ExecutorKind:
            _fail(WorkErrorCode.INVALID_EXECUTOR)
        if (self.provider_identifier is None) != (self.provider_version is None):
            _fail(WorkErrorCode.INVALID_EXECUTOR)
        if self.executor_kind is ExecutorKind.HUMAN and self.provider_identifier is not None:
            _fail(WorkErrorCode.INVALID_EXECUTOR)
        if self.executor_kind is not ExecutorKind.HUMAN and self.provider_identifier is None:
            _fail(WorkErrorCode.INVALID_EXECUTOR)
        if self.provider_identifier is not None:
            _code(self.provider_identifier, WorkErrorCode.INVALID_EXECUTOR)
            _code(self.provider_version, WorkErrorCode.INVALID_EXECUTOR, sensitive=False)
        object.__setattr__(self, "_integrity", self._tuple())

    def _tuple(self) -> tuple[object, ...]:
        return (self.executor_id, self.executor_kind, self.provider_identifier, self.provider_version)


def _valid_executor(value: object) -> bool:
    try:
        return type(value) is ExecutorReference and value._integrity == value._tuple()
    except Exception:
        return False


def _authority(
    decision: object, *, resource_type: str, resource_id: str, scope: AuthorityScope,
    policy_id: str, policy_version: str, capability: str, actor_ref: str,
) -> AuthorizationDecision:
    try:
        project_authorized_fields({}, decision, behavior=ProjectionBehavior.OMIT)
    except Exception:
        _fail(WorkErrorCode.INVALID_AUTHORITY_DECISION)
    if (
        type(decision) is not AuthorizationDecision or decision.allowed is not True
        or decision.resource_type != resource_type or decision.resource_id != resource_id
        or decision.effective_scope != scope or decision.policy_id != policy_id
        or decision.policy_version != policy_version or decision.action_code != capability
        or decision.principal_ref != actor_ref
    ):
        _fail(WorkErrorCode.INVALID_AUTHORITY_DECISION)
    return decision


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
        for value in (
            self.principal_ref, self.capability_code, self.resource_type,
            self.resource_id, self.policy_id, self.policy_version,
        ):
            _code(value, WorkErrorCode.INVALID_AUTHORITY_DECISION, sensitive=False)
        object.__setattr__(self, "_integrity", self._tuple())

    def _tuple(self) -> tuple[object, ...]:
        return (
            self.principal_ref, self.capability_code, self.resource_type,
            self.resource_id, self.policy_id, self.policy_version,
        )


def _valid_provenance(value: object) -> bool:
    try:
        return type(value) is AuthorityProvenance and value._integrity == value._tuple()
    except Exception:
        return False


def _provenance(decision: AuthorizationDecision) -> AuthorityProvenance:
    return AuthorityProvenance(
        decision.principal_ref or "invalid", decision.action_code, decision.resource_type,
        decision.resource_id, decision.policy_id, decision.policy_version,
    )


def _refs(values: object, validator, error: WorkErrorCode) -> tuple[str, ...]:
    if type(values) is not tuple or len(values) > MAX_REFERENCES:
        _fail(error)
    result: list[str] = []
    for value in values:
        try:
            result.append(validator(value))
        except Exception:
            _fail(error)
    return tuple(sorted(set(result)))


@dataclass(frozen=True, slots=True)
class CompletionCriterion:
    criterion_type: CriterionType
    required_count: int = 1
    capability_code: str | None = None
    _integrity: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.criterion_type) is not CriterionType:
            _fail(WorkErrorCode.INVALID_CRITERIA)
        if type(self.required_count) is not int or not 1 <= self.required_count <= 16:
            _fail(WorkErrorCode.INVALID_CRITERIA)
        if self.capability_code is not None:
            _code(self.capability_code, WorkErrorCode.INVALID_CRITERIA)
        object.__setattr__(self, "_integrity", (self.criterion_type, self.required_count, self.capability_code))


def _valid_criterion(value: object) -> bool:
    try:
        return type(value) is CompletionCriterion and value._integrity == (
            value.criterion_type, value.required_count, value.capability_code,
        )
    except Exception:
        return False


@dataclass(frozen=True, slots=True, init=False)
class WorkItem:
    work_id: str
    work_type: WorkType
    scope: AuthorityScope
    context_ids: tuple[str, ...]
    requester: ActorReference
    created_at: datetime
    ready_at: datetime | None
    due_at: datetime | None
    priority: WorkPriority
    initial_status: WorkStatus
    classification: ResourceClassification
    visibility_policy_id: str
    visibility_policy_version: str
    required_capabilities: tuple[str, ...]
    input_evidence_ids: tuple[str, ...]
    source_event_ids: tuple[str, ...]
    source_decision_ids: tuple[str, ...]
    source_approval_ids: tuple[str, ...]
    source_work_ids: tuple[str, ...]
    completion_criteria: tuple[CompletionCriterion, ...]
    authority_provenance: AuthorityProvenance
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs
        _fail(WorkErrorCode.INVALID_WORK_ITEM)


def _work_tuple(value: WorkItem) -> tuple[object, ...]:
    return tuple(getattr(value, name) for name in (
        "work_id", "work_type", "scope", "context_ids", "requester", "created_at",
        "ready_at", "due_at", "priority", "initial_status", "classification",
        "visibility_policy_id", "visibility_policy_version", "required_capabilities",
        "input_evidence_ids", "source_event_ids", "source_decision_ids",
        "source_approval_ids", "source_work_ids", "completion_criteria",
        "authority_provenance", "contract_version",
    ))


def _valid_work(value: object) -> bool:
    try:
        return type(value) is WorkItem and _valid_actor(value.requester) and all(
            _valid_criterion(item) for item in value.completion_criteria
        ) and _valid_provenance(value.authority_provenance) and _scope_valid(
            value.scope, value.work_id
        ) and value._integrity == _work_tuple(value)
    except Exception:
        return False


def create_work_item(
    *, work_id: str, work_type: WorkType, scope: AuthorityScope,
    context_ids: tuple[str, ...], requester: ActorReference, created_at: datetime,
    priority: WorkPriority, initial_status: WorkStatus,
    classification: ResourceClassification, visibility_policy_id: str,
    visibility_policy_version: str, authority_decision: AuthorizationDecision,
    required_capabilities: tuple[str, ...], ready_at: datetime | None = None,
    due_at: datetime | None = None, input_evidence_ids: tuple[str, ...] = (),
    source_event_ids: tuple[str, ...] = (), source_decision_ids: tuple[str, ...] = (),
    source_approval_ids: tuple[str, ...] = (), source_work_ids: tuple[str, ...] = (),
    completion_criteria: tuple[CompletionCriterion, ...] = (),
    contract_version: str = WORK_CONTRACT_VERSION,
) -> WorkItem:
    validate_work_id(work_id)
    if type(work_type) is not WorkType or type(priority) is not WorkPriority:
        _fail(WorkErrorCode.INVALID_WORK_ITEM)
    if initial_status not in {WorkStatus.DRAFT, WorkStatus.READY} or not _valid_actor(requester):
        _fail(WorkErrorCode.INVALID_WORK_ITEM)
    if contract_version != WORK_CONTRACT_VERSION:
        _fail(WorkErrorCode.INVALID_VERSION)
    if not _scope_valid(scope, work_id) or type(classification) is not ResourceClassification:
        _fail(WorkErrorCode.INVALID_SCOPE)
    contexts = _refs(context_ids, validate_context_id, WorkErrorCode.INVALID_WORK_ITEM)
    if not contexts:
        _fail(WorkErrorCode.INVALID_WORK_ITEM)
    created = _utc(created_at)
    ready = None if ready_at is None else _utc(ready_at)
    due = None if due_at is None else _utc(due_at)
    if (ready is not None and ready < created) or (due is not None and due < (ready or created)):
        _fail(WorkErrorCode.CHRONOLOGY_CONFLICT)
    if type(required_capabilities) is not tuple or not required_capabilities:
        _fail(WorkErrorCode.INVALID_WORK_ITEM)
    capabilities = tuple(sorted(set(_code(item, WorkErrorCode.INVALID_WORK_ITEM) for item in required_capabilities)))
    if type(completion_criteria) is not tuple or len(completion_criteria) > MAX_CRITERIA or any(
        not _valid_criterion(item) for item in completion_criteria
    ):
        _fail(WorkErrorCode.INVALID_CRITERIA)
    if len({item.criterion_type for item in completion_criteria}) != len(completion_criteria):
        _fail(WorkErrorCode.INVALID_CRITERIA)
    _code(visibility_policy_id, WorkErrorCode.INVALID_WORK_ITEM)
    _code(visibility_policy_version, WorkErrorCode.INVALID_WORK_ITEM, sensitive=False)
    auth = _authority(
        authority_decision, resource_type="work_item", resource_id=work_id, scope=scope,
        policy_id=visibility_policy_id, policy_version=visibility_policy_version,
        capability="work.create", actor_ref=requester.actor_ref,
    )
    fields = {
        "work_id": work_id, "work_type": work_type, "scope": scope,
        "context_ids": contexts, "requester": requester, "created_at": created,
        "ready_at": ready, "due_at": due, "priority": priority,
        "initial_status": initial_status, "classification": classification,
        "visibility_policy_id": visibility_policy_id,
        "visibility_policy_version": visibility_policy_version,
        "required_capabilities": capabilities,
        "input_evidence_ids": _refs(input_evidence_ids, validate_evidence_id, WorkErrorCode.INVALID_WORK_ITEM),
        "source_event_ids": _refs(source_event_ids, validate_event_id, WorkErrorCode.INVALID_WORK_ITEM),
        "source_decision_ids": _refs(source_decision_ids, validate_decision_id, WorkErrorCode.INVALID_WORK_ITEM),
        "source_approval_ids": _refs(source_approval_ids, validate_approval_id, WorkErrorCode.INVALID_WORK_ITEM),
        "source_work_ids": _refs(source_work_ids, validate_work_id, WorkErrorCode.INVALID_WORK_ITEM),
        "completion_criteria": tuple(completion_criteria), "authority_provenance": _provenance(auth),
        "contract_version": contract_version,
    }
    value = object.__new__(WorkItem)
    for name, item in fields.items(): object.__setattr__(value, name, item)
    object.__setattr__(value, "_integrity", _work_tuple(value))
    return value


@dataclass(frozen=True, slots=True, init=False)
class Assignment:
    assignment_id: str
    work_id: str
    assignee: ExecutorReference
    assigner: ActorReference
    assigned_at: datetime
    responded_at: datetime | None
    acceptance_deadline: datetime | None
    due_at: datetime | None
    status: AssignmentStatus
    required_execution_capability: str
    scope: AuthorityScope
    classification: ResourceClassification
    visibility_policy_id: str
    visibility_policy_version: str
    reason_code: str | None
    supersedes_assignment_id: str | None
    authority_provenance: AuthorityProvenance
    response_authority_provenance: AuthorityProvenance | None
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs
        _fail(WorkErrorCode.INVALID_ASSIGNMENT)


def _assignment_tuple(value: Assignment) -> tuple[object, ...]:
    return tuple(getattr(value, name) for name in (
        "assignment_id", "work_id", "assignee", "assigner", "assigned_at",
        "responded_at", "acceptance_deadline", "due_at", "status", "required_execution_capability",
        "scope", "classification", "visibility_policy_id", "visibility_policy_version",
        "reason_code", "supersedes_assignment_id", "authority_provenance",
        "response_authority_provenance", "contract_version",
    ))


def _valid_assignment(value: object) -> bool:
    try:
        return type(value) is Assignment and _valid_executor(value.assignee) and _valid_actor(
            value.assigner
        ) and _valid_provenance(value.authority_provenance) and (
            value.response_authority_provenance is None
            or _valid_provenance(value.response_authority_provenance)
        ) and _scope_valid(value.scope, value.assignment_id) and value._integrity == _assignment_tuple(value)
    except Exception:
        return False


def create_assignment(
    *, assignment_id: str, work_id: str, assignee: ExecutorReference,
    assigner: ActorReference, assigned_at: datetime, status: AssignmentStatus,
    required_execution_capability: str, scope: AuthorityScope,
    classification: ResourceClassification, visibility_policy_id: str,
    visibility_policy_version: str, authority_decision: AuthorizationDecision,
    response_authority_decision: AuthorizationDecision | None = None,
    responded_at: datetime | None = None,
    acceptance_deadline: datetime | None = None, due_at: datetime | None = None,
    reason_code: str | None = None, supersedes_assignment_id: str | None = None,
    contract_version: str = WORK_CONTRACT_VERSION,
) -> Assignment:
    validate_assignment_id(assignment_id); validate_work_id(work_id)
    if not _valid_executor(assignee) or not _valid_actor(assigner) or type(status) is not AssignmentStatus:
        _fail(WorkErrorCode.INVALID_ASSIGNMENT)
    if contract_version != WORK_CONTRACT_VERSION: _fail(WorkErrorCode.INVALID_VERSION)
    if not _scope_valid(scope, assignment_id) or type(classification) is not ResourceClassification:
        _fail(WorkErrorCode.INVALID_SCOPE)
    assigned = _utc(assigned_at)
    responded = None if responded_at is None else _utc(responded_at)
    accept_by = None if acceptance_deadline is None else _utc(acceptance_deadline)
    due = None if due_at is None else _utc(due_at)
    if (accept_by is not None and accept_by <= assigned) or (due is not None and due <= assigned):
        _fail(WorkErrorCode.CHRONOLOGY_CONFLICT)
    if status in {AssignmentStatus.ACCEPTED, AssignmentStatus.DECLINED, AssignmentStatus.SUPERSEDED, AssignmentStatus.WITHDRAWN, AssignmentStatus.EXPIRED, AssignmentStatus.INVALIDATED}:
        if responded is None or responded < assigned: _fail(WorkErrorCode.INVALID_ASSIGNMENT)
    elif responded is not None:
        _fail(WorkErrorCode.INVALID_ASSIGNMENT)
    if accept_by is not None and responded is not None and responded > accept_by:
        _fail(WorkErrorCode.CHRONOLOGY_CONFLICT)
    if supersedes_assignment_id is not None:
        validate_assignment_id(supersedes_assignment_id)
        if supersedes_assignment_id == assignment_id: _fail(WorkErrorCode.INVALID_ASSIGNMENT)
    if status in {AssignmentStatus.SUPERSEDED, AssignmentStatus.WITHDRAWN, AssignmentStatus.INVALIDATED} and supersedes_assignment_id is None:
        _fail(WorkErrorCode.INVALID_ASSIGNMENT)
    capability = _code(required_execution_capability, WorkErrorCode.INVALID_ASSIGNMENT)
    if reason_code is not None: _code(reason_code, WorkErrorCode.INVALID_ASSIGNMENT, sensitive=False)
    _code(visibility_policy_id, WorkErrorCode.INVALID_ASSIGNMENT)
    _code(visibility_policy_version, WorkErrorCode.INVALID_ASSIGNMENT, sensitive=False)
    auth = _authority(
        authority_decision, resource_type="work_assignment", resource_id=assignment_id,
        scope=scope, policy_id=visibility_policy_id, policy_version=visibility_policy_version,
        capability="work.assign", actor_ref=assigner.actor_ref,
    )
    response_auth = None
    if status in {AssignmentStatus.ACCEPTED, AssignmentStatus.DECLINED}:
        if response_authority_decision is None:
            _fail(WorkErrorCode.INVALID_ASSIGNMENT)
        response_auth = _authority(
            response_authority_decision, resource_type="work_assignment",
            resource_id=assignment_id, scope=scope, policy_id=visibility_policy_id,
            policy_version=visibility_policy_version,
            capability=f"work.assignment.{status.value.lower()}",
            actor_ref=assignee.executor_id,
        )
    elif response_authority_decision is not None:
        _fail(WorkErrorCode.INVALID_ASSIGNMENT)
    values = locals() | {"assigned_at": assigned, "responded_at": responded, "acceptance_deadline": accept_by,
                         "due_at": due, "required_execution_capability": capability,
                         "authority_provenance": _provenance(auth),
                         "response_authority_provenance": None if response_auth is None else _provenance(response_auth)}
    value = object.__new__(Assignment)
    for name in Assignment.__dataclass_fields__:
        if name != "_integrity": object.__setattr__(value, name, values[name])
    object.__setattr__(value, "_integrity", _assignment_tuple(value))
    return value


_ALLOWED_TRANSITIONS = {
    WorkStatus.DRAFT: {WorkStatus.READY, WorkStatus.CANCELLED, WorkStatus.INVALIDATED},
    WorkStatus.READY: {WorkStatus.ASSIGNED, WorkStatus.CANCELLED, WorkStatus.EXPIRED, WorkStatus.INVALIDATED},
    WorkStatus.ASSIGNED: {WorkStatus.ACCEPTED, WorkStatus.READY, WorkStatus.CANCELLED, WorkStatus.EXPIRED, WorkStatus.INVALIDATED},
    WorkStatus.ACCEPTED: {WorkStatus.IN_PROGRESS, WorkStatus.CANCELLED, WorkStatus.EXPIRED, WorkStatus.INVALIDATED},
    WorkStatus.IN_PROGRESS: {WorkStatus.BLOCKED, WorkStatus.WAITING, WorkStatus.WAITING_APPROVAL, WorkStatus.COMPLETION_CLAIMED, WorkStatus.FAILED, WorkStatus.CANCELLED, WorkStatus.INVALIDATED},
    WorkStatus.BLOCKED: {WorkStatus.IN_PROGRESS, WorkStatus.CANCELLED, WorkStatus.EXPIRED, WorkStatus.INVALIDATED},
    WorkStatus.WAITING: {WorkStatus.IN_PROGRESS, WorkStatus.CANCELLED, WorkStatus.EXPIRED, WorkStatus.INVALIDATED},
    WorkStatus.WAITING_APPROVAL: {WorkStatus.IN_PROGRESS, WorkStatus.COMPLETION_CLAIMED, WorkStatus.CANCELLED, WorkStatus.EXPIRED, WorkStatus.INVALIDATED},
    WorkStatus.COMPLETION_CLAIMED: {WorkStatus.COMPLETED, WorkStatus.IN_PROGRESS, WorkStatus.INVALIDATED},
    WorkStatus.FAILED: {WorkStatus.READY, WorkStatus.CANCELLED, WorkStatus.INVALIDATED},
    WorkStatus.COMPLETED: {WorkStatus.INVALIDATED},
    WorkStatus.CANCELLED: {WorkStatus.INVALIDATED},
    WorkStatus.EXPIRED: {WorkStatus.INVALIDATED},
    WorkStatus.INVALIDATED: set(),
}


@dataclass(frozen=True, slots=True, init=False)
class WorkTransition:
    transition_id: str
    work_id: str
    from_status: WorkStatus
    to_status: WorkStatus
    actor: ActorReference
    occurred_at: datetime
    scope: AuthorityScope
    classification: ResourceClassification
    visibility_policy_id: str
    visibility_policy_version: str
    reason_code: str | None
    linked_event_id: str | None
    linked_decision_id: str | None
    linked_approval_id: str | None
    authority_provenance: AuthorityProvenance
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs
        _fail(WorkErrorCode.INVALID_TRANSITION)


def _transition_tuple(value: WorkTransition) -> tuple[object, ...]:
    return tuple(getattr(value, name) for name in WorkTransition.__dataclass_fields__ if name != "_integrity")


def _valid_transition(value: object) -> bool:
    try:
        return type(value) is WorkTransition and _valid_actor(value.actor) and _valid_provenance(
            value.authority_provenance
        ) and _scope_valid(value.scope, value.transition_id) and value._integrity == _transition_tuple(value)
    except Exception: return False


def create_work_transition(
    *, transition_id: str, work_id: str, from_status: WorkStatus, to_status: WorkStatus,
    actor: ActorReference, occurred_at: datetime, scope: AuthorityScope,
    classification: ResourceClassification, visibility_policy_id: str,
    visibility_policy_version: str, authority_decision: AuthorizationDecision,
    reason_code: str | None = None, linked_event_id: str | None = None,
    linked_decision_id: str | None = None, linked_approval_id: str | None = None,
    contract_version: str = WORK_CONTRACT_VERSION,
) -> WorkTransition:
    validate_transition_id(transition_id); validate_work_id(work_id)
    if type(from_status) is not WorkStatus or type(to_status) is not WorkStatus or not _valid_actor(actor):
        _fail(WorkErrorCode.INVALID_TRANSITION)
    if to_status not in _ALLOWED_TRANSITIONS[from_status]: _fail(WorkErrorCode.INVALID_STATUS_TRANSITION)
    if contract_version != WORK_CONTRACT_VERSION: _fail(WorkErrorCode.INVALID_VERSION)
    if not _scope_valid(scope, transition_id) or type(classification) is not ResourceClassification:
        _fail(WorkErrorCode.INVALID_SCOPE)
    when = _utc(occurred_at)
    for value, validator in ((linked_event_id, validate_event_id), (linked_decision_id, validate_decision_id), (linked_approval_id, validate_approval_id)):
        if value is not None: validator(value)
    if to_status is WorkStatus.CANCELLED and linked_decision_id is None:
        _fail(WorkErrorCode.INVALID_TRANSITION)
    if to_status is WorkStatus.COMPLETED and linked_approval_id is None and linked_decision_id is None:
        _fail(WorkErrorCode.CRITERIA_UNSATISFIED)
    if reason_code is not None: _code(reason_code, WorkErrorCode.INVALID_TRANSITION, sensitive=False)
    _code(visibility_policy_id, WorkErrorCode.INVALID_TRANSITION)
    _code(visibility_policy_version, WorkErrorCode.INVALID_TRANSITION, sensitive=False)
    capability = f"work.transition.{to_status.value.lower()}"
    auth = _authority(
        authority_decision, resource_type="work_transition", resource_id=transition_id,
        scope=scope, policy_id=visibility_policy_id, policy_version=visibility_policy_version,
        capability=capability, actor_ref=actor.actor_ref,
    )
    values = locals() | {"occurred_at": when, "authority_provenance": _provenance(auth)}
    value = object.__new__(WorkTransition)
    for name in WorkTransition.__dataclass_fields__:
        if name != "_integrity": object.__setattr__(value, name, values[name])
    object.__setattr__(value, "_integrity", _transition_tuple(value))
    return value


@dataclass(frozen=True, slots=True, init=False)
class ExecutionAttempt:
    attempt_id: str
    work_id: str
    assignment_id: str
    executor: ExecutorReference
    sequence: int
    started_at: datetime
    ended_at: datetime | None
    status: AttemptStatus
    input_evidence_ids: tuple[str, ...]
    input_context_ids: tuple[str, ...]
    result_evidence_ids: tuple[str, ...]
    result_event_ids: tuple[str, ...]
    result_decision_ids: tuple[str, ...]
    result_approval_ids: tuple[str, ...]
    scope: AuthorityScope
    classification: ResourceClassification
    visibility_policy_id: str
    visibility_policy_version: str
    outcome_code: str | None
    authority_provenance: AuthorityProvenance
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs
        _fail(WorkErrorCode.INVALID_ATTEMPT)


def _attempt_tuple(value: ExecutionAttempt) -> tuple[object, ...]:
    return tuple(getattr(value, name) for name in ExecutionAttempt.__dataclass_fields__ if name != "_integrity")


def _valid_attempt(value: object) -> bool:
    try:
        return type(value) is ExecutionAttempt and _valid_executor(value.executor) and _valid_provenance(
            value.authority_provenance
        ) and _scope_valid(value.scope, value.attempt_id) and value._integrity == _attempt_tuple(value)
    except Exception: return False


def create_execution_attempt(
    *, attempt_id: str, work_id: str, assignment_id: str, executor: ExecutorReference,
    sequence: int, started_at: datetime, status: AttemptStatus, scope: AuthorityScope,
    classification: ResourceClassification, visibility_policy_id: str,
    visibility_policy_version: str, authority_decision: AuthorizationDecision,
    ended_at: datetime | None = None, input_evidence_ids: tuple[str, ...] = (),
    input_context_ids: tuple[str, ...] = (), result_evidence_ids: tuple[str, ...] = (),
    result_event_ids: tuple[str, ...] = (), result_decision_ids: tuple[str, ...] = (),
    result_approval_ids: tuple[str, ...] = (), outcome_code: str | None = None,
    contract_version: str = WORK_CONTRACT_VERSION,
) -> ExecutionAttempt:
    validate_attempt_id(attempt_id); validate_work_id(work_id); validate_assignment_id(assignment_id)
    if not _valid_executor(executor) or type(sequence) is not int or not 1 <= sequence <= MAX_ATTEMPTS:
        _fail(WorkErrorCode.INVALID_ATTEMPT)
    if type(status) is not AttemptStatus: _fail(WorkErrorCode.INVALID_ATTEMPT)
    if contract_version != WORK_CONTRACT_VERSION: _fail(WorkErrorCode.INVALID_VERSION)
    if not _scope_valid(scope, attempt_id) or type(classification) is not ResourceClassification:
        _fail(WorkErrorCode.INVALID_SCOPE)
    start = _utc(started_at); end = None if ended_at is None else _utc(ended_at)
    if status is AttemptStatus.STARTED and end is not None: _fail(WorkErrorCode.INVALID_ATTEMPT)
    if status is not AttemptStatus.STARTED and end is None: _fail(WorkErrorCode.INVALID_ATTEMPT)
    if end is not None and end <= start: _fail(WorkErrorCode.CHRONOLOGY_CONFLICT)
    if outcome_code is not None: _code(outcome_code, WorkErrorCode.INVALID_ATTEMPT, sensitive=False)
    _code(visibility_policy_id, WorkErrorCode.INVALID_ATTEMPT)
    _code(visibility_policy_version, WorkErrorCode.INVALID_ATTEMPT, sensitive=False)
    auth = _authority(
        authority_decision, resource_type="work_attempt", resource_id=attempt_id,
        scope=scope, policy_id=visibility_policy_id, policy_version=visibility_policy_version,
        capability="work.execute", actor_ref=executor.executor_id,
    )
    values = locals() | {
        "started_at": start, "ended_at": end,
        "input_evidence_ids": _refs(input_evidence_ids, validate_evidence_id, WorkErrorCode.INVALID_ATTEMPT),
        "input_context_ids": _refs(input_context_ids, validate_context_id, WorkErrorCode.INVALID_ATTEMPT),
        "result_evidence_ids": _refs(result_evidence_ids, validate_evidence_id, WorkErrorCode.INVALID_ATTEMPT),
        "result_event_ids": _refs(result_event_ids, validate_event_id, WorkErrorCode.INVALID_ATTEMPT),
        "result_decision_ids": _refs(result_decision_ids, validate_decision_id, WorkErrorCode.INVALID_ATTEMPT),
        "result_approval_ids": _refs(result_approval_ids, validate_approval_id, WorkErrorCode.INVALID_ATTEMPT),
        "authority_provenance": _provenance(auth),
    }
    value = object.__new__(ExecutionAttempt)
    for name in ExecutionAttempt.__dataclass_fields__:
        if name != "_integrity": object.__setattr__(value, name, values[name])
    object.__setattr__(value, "_integrity", _attempt_tuple(value))
    return value


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    policy_code: str
    maximum_attempts: int
    retryable_reason_codes: frozenset[str]
    backoff_code: str | None = None
    allow_after_cancellation: bool = False
    approval_required: bool = False
    contract_version: str = WORK_CONTRACT_VERSION
    _integrity: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _code(self.policy_code, WorkErrorCode.INVALID_RETRY_POLICY)
        if type(self.maximum_attempts) is not int or not 1 <= self.maximum_attempts <= MAX_ATTEMPTS:
            _fail(WorkErrorCode.INVALID_RETRY_POLICY)
        if type(self.retryable_reason_codes) is not frozenset or not self.retryable_reason_codes:
            _fail(WorkErrorCode.INVALID_RETRY_POLICY)
        for item in self.retryable_reason_codes: _code(item, WorkErrorCode.INVALID_RETRY_POLICY, sensitive=False)
        if self.backoff_code is not None: _code(self.backoff_code, WorkErrorCode.INVALID_RETRY_POLICY, sensitive=False)
        if type(self.allow_after_cancellation) is not bool or type(self.approval_required) is not bool:
            _fail(WorkErrorCode.INVALID_RETRY_POLICY)
        if self.contract_version != WORK_CONTRACT_VERSION: _fail(WorkErrorCode.INVALID_VERSION)
        object.__setattr__(self, "_integrity", self._tuple())

    def _tuple(self) -> tuple[object, ...]:
        return (self.policy_code, self.maximum_attempts, tuple(sorted(self.retryable_reason_codes)), self.backoff_code, self.allow_after_cancellation, self.approval_required, self.contract_version)


@dataclass(frozen=True, slots=True)
class RetryEvaluation:
    allowed: bool
    reason_code: RetryReason
    next_sequence: int | None


def evaluate_retry(
    policy: RetryPolicy, attempts: Sequence[ExecutionAttempt], *, reason_code: str,
    now: datetime, due_at: datetime | None, executor: ExecutorReference,
    approval_present: bool = False,
) -> RetryEvaluation:
    if type(policy) is not RetryPolicy or policy._integrity != policy._tuple() or type(attempts) not in (list, tuple):
        _fail(WorkErrorCode.INVALID_RETRY_POLICY)
    when = _utc(now, WorkErrorCode.INVALID_RETRY_POLICY)
    if not _valid_executor(executor) or any(not _valid_attempt(item) for item in attempts):
        _fail(WorkErrorCode.INVALID_RETRY_POLICY)
    if not attempts: return RetryEvaluation(True, RetryReason.RETRY_ALLOWED, 1)
    ordered = sorted(attempts, key=lambda item: item.sequence)
    if [item.sequence for item in ordered] != list(range(1, len(ordered) + 1)):
        _fail(WorkErrorCode.INVALID_RETRY_POLICY)
    last = ordered[-1]
    if any(item.executor != executor for item in ordered): return RetryEvaluation(False, RetryReason.EXECUTOR_CHANGED, None)
    if last.status is AttemptStatus.SUCCEEDED: return RetryEvaluation(False, RetryReason.PRIOR_ATTEMPT_SUCCEEDED, None)
    if last.status is AttemptStatus.STARTED: return RetryEvaluation(False, RetryReason.PRIOR_ATTEMPT_ACTIVE, None)
    if last.status is AttemptStatus.CANCELLED and not policy.allow_after_cancellation:
        return RetryEvaluation(False, RetryReason.PRIOR_ATTEMPT_CANCELLED, None)
    if len(ordered) >= policy.maximum_attempts: return RetryEvaluation(False, RetryReason.ATTEMPT_LIMIT_REACHED, None)
    if reason_code not in policy.retryable_reason_codes: return RetryEvaluation(False, RetryReason.REASON_NOT_RETRYABLE, None)
    if due_at is not None and when >= _utc(due_at): return RetryEvaluation(False, RetryReason.DEADLINE_EXCEEDED, None)
    if policy.approval_required and approval_present is not True: return RetryEvaluation(False, RetryReason.APPROVAL_REQUIRED, None)
    return RetryEvaluation(True, RetryReason.RETRY_ALLOWED, len(ordered) + 1)


@dataclass(frozen=True, slots=True)
class WorkDependency:
    source_work_id: str
    target_work_id: str
    dependency_type: DependencyType
    created_at: datetime
    blocker_reason_code: str | None
    policy_id: str
    policy_version: str
    contract_version: str = WORK_CONTRACT_VERSION
    _integrity: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        validate_work_id(self.source_work_id); validate_work_id(self.target_work_id)
        if self.source_work_id == self.target_work_id or type(self.dependency_type) is not DependencyType:
            _fail(WorkErrorCode.INVALID_DEPENDENCY)
        object.__setattr__(self, "created_at", _utc(self.created_at))
        if self.blocker_reason_code is not None: _code(self.blocker_reason_code, WorkErrorCode.INVALID_DEPENDENCY, sensitive=False)
        _code(self.policy_id, WorkErrorCode.INVALID_DEPENDENCY)
        _code(self.policy_version, WorkErrorCode.INVALID_DEPENDENCY, sensitive=False)
        if self.contract_version != WORK_CONTRACT_VERSION: _fail(WorkErrorCode.INVALID_VERSION)
        object.__setattr__(self, "_integrity", self._tuple())

    def _tuple(self) -> tuple[object, ...]:
        return (self.source_work_id, self.target_work_id, self.dependency_type, self.created_at, self.blocker_reason_code, self.policy_id, self.policy_version, self.contract_version)


@dataclass(frozen=True, slots=True, init=False)
class Handoff:
    handoff_id: str
    work_id: str
    source_assignment_id: str
    target_assignment_id: str
    target_executor: ExecutorReference
    initiated_by: ActorReference
    initiated_at: datetime
    accepted_at: datetime | None
    status: HandoffStatus
    reason_code: str
    scope: AuthorityScope
    classification: ResourceClassification
    visibility_policy_id: str
    visibility_policy_version: str
    authority_provenance: AuthorityProvenance
    acceptance_authority_provenance: AuthorityProvenance | None
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs
        _fail(WorkErrorCode.INVALID_HANDOFF)


def _handoff_tuple(value: Handoff) -> tuple[object, ...]:
    return tuple(getattr(value, name) for name in Handoff.__dataclass_fields__ if name != "_integrity")


def _valid_handoff(value: object) -> bool:
    try:
        return type(value) is Handoff and _valid_actor(value.initiated_by) and _valid_executor(
            value.target_executor
        ) and _valid_provenance(value.authority_provenance) and (
            value.acceptance_authority_provenance is None
            or _valid_provenance(value.acceptance_authority_provenance)
        ) and _scope_valid(value.scope, value.handoff_id) and value._integrity == _handoff_tuple(value)
    except Exception: return False


def create_handoff(
    *, handoff_id: str, work_id: str, source_assignment_id: str,
    target_assignment_id: str, target_executor: ExecutorReference,
    initiated_by: ActorReference, initiated_at: datetime,
    status: HandoffStatus, reason_code: str, scope: AuthorityScope,
    classification: ResourceClassification, visibility_policy_id: str,
    visibility_policy_version: str, authority_decision: AuthorizationDecision,
    accepted_at: datetime | None = None,
    acceptance_authority_decision: AuthorizationDecision | None = None,
    contract_version: str = WORK_CONTRACT_VERSION,
) -> Handoff:
    validate_handoff_id(handoff_id); validate_work_id(work_id)
    validate_assignment_id(source_assignment_id); validate_assignment_id(target_assignment_id)
    if source_assignment_id == target_assignment_id or not _valid_executor(target_executor) or not _valid_actor(initiated_by) or type(status) is not HandoffStatus:
        _fail(WorkErrorCode.INVALID_HANDOFF)
    if contract_version != WORK_CONTRACT_VERSION: _fail(WorkErrorCode.INVALID_VERSION)
    if not _scope_valid(scope, handoff_id) or type(classification) is not ResourceClassification:
        _fail(WorkErrorCode.INVALID_SCOPE)
    initiated = _utc(initiated_at); accepted = None if accepted_at is None else _utc(accepted_at)
    if status is HandoffStatus.ACCEPTED and accepted is None: _fail(WorkErrorCode.INVALID_HANDOFF)
    if status is not HandoffStatus.ACCEPTED and accepted is not None: _fail(WorkErrorCode.INVALID_HANDOFF)
    if accepted is not None and accepted <= initiated: _fail(WorkErrorCode.CHRONOLOGY_CONFLICT)
    _code(reason_code, WorkErrorCode.INVALID_HANDOFF, sensitive=False)
    _code(visibility_policy_id, WorkErrorCode.INVALID_HANDOFF); _code(visibility_policy_version, WorkErrorCode.INVALID_HANDOFF, sensitive=False)
    auth = _authority(authority_decision, resource_type="work_handoff", resource_id=handoff_id, scope=scope, policy_id=visibility_policy_id, policy_version=visibility_policy_version, capability="work.handoff", actor_ref=initiated_by.actor_ref)
    acceptance_auth = None
    if status is HandoffStatus.ACCEPTED:
        if acceptance_authority_decision is None:
            _fail(WorkErrorCode.INVALID_HANDOFF)
        acceptance_auth = _authority(
            acceptance_authority_decision, resource_type="work_handoff",
            resource_id=handoff_id, scope=scope, policy_id=visibility_policy_id,
            policy_version=visibility_policy_version, capability="work.handoff.accept",
            actor_ref=target_executor.executor_id,
        )
    elif acceptance_authority_decision is not None:
        _fail(WorkErrorCode.INVALID_HANDOFF)
    values = locals() | {"initiated_at": initiated, "accepted_at": accepted,
                         "authority_provenance": _provenance(auth),
                         "acceptance_authority_provenance": None if acceptance_auth is None else _provenance(acceptance_auth)}
    value = object.__new__(Handoff)
    for name in Handoff.__dataclass_fields__:
        if name != "_integrity": object.__setattr__(value, name, values[name])
    object.__setattr__(value, "_integrity", _handoff_tuple(value)); return value


@dataclass(frozen=True, slots=True, init=False)
class Escalation:
    escalation_id: str
    work_id: str
    raised_by: ActorReference
    target_ref: str
    target_capability: str
    severity: EscalationSeverity
    reason_code: str
    raised_at: datetime
    response_due_at: datetime | None
    status: EscalationStatus
    scope: AuthorityScope
    classification: ResourceClassification
    visibility_policy_id: str
    visibility_policy_version: str
    authority_provenance: AuthorityProvenance
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs; _fail(WorkErrorCode.INVALID_ESCALATION)


def _escalation_tuple(value: Escalation) -> tuple[object, ...]:
    return tuple(getattr(value, name) for name in Escalation.__dataclass_fields__ if name != "_integrity")


def _valid_escalation(value: object) -> bool:
    try:
        return type(value) is Escalation and _valid_actor(value.raised_by) and _valid_provenance(
            value.authority_provenance
        ) and _scope_valid(value.scope, value.escalation_id) and value._integrity == _escalation_tuple(value)
    except Exception: return False


def create_escalation(
    *, escalation_id: str, work_id: str, raised_by: ActorReference, target_ref: str,
    target_capability: str, severity: EscalationSeverity, reason_code: str,
    raised_at: datetime, status: EscalationStatus, scope: AuthorityScope,
    classification: ResourceClassification, visibility_policy_id: str,
    visibility_policy_version: str, authority_decision: AuthorizationDecision,
    response_due_at: datetime | None = None, contract_version: str = WORK_CONTRACT_VERSION,
) -> Escalation:
    validate_escalation_id(escalation_id); validate_work_id(work_id)
    if not _valid_actor(raised_by) or type(severity) is not EscalationSeverity or type(status) is not EscalationStatus:
        _fail(WorkErrorCode.INVALID_ESCALATION)
    if contract_version != WORK_CONTRACT_VERSION: _fail(WorkErrorCode.INVALID_VERSION)
    if not _scope_valid(scope, escalation_id) or type(classification) is not ResourceClassification:
        _fail(WorkErrorCode.INVALID_SCOPE)
    _code(target_ref, WorkErrorCode.INVALID_ESCALATION); _code(target_capability, WorkErrorCode.INVALID_ESCALATION)
    _code(reason_code, WorkErrorCode.INVALID_ESCALATION, sensitive=False)
    raised = _utc(raised_at); due = None if response_due_at is None else _utc(response_due_at)
    if due is not None and due <= raised: _fail(WorkErrorCode.CHRONOLOGY_CONFLICT)
    _code(visibility_policy_id, WorkErrorCode.INVALID_ESCALATION); _code(visibility_policy_version, WorkErrorCode.INVALID_ESCALATION, sensitive=False)
    auth = _authority(authority_decision, resource_type="work_escalation", resource_id=escalation_id, scope=scope, policy_id=visibility_policy_id, policy_version=visibility_policy_version, capability="work.escalate", actor_ref=raised_by.actor_ref)
    values = locals() | {"raised_at": raised, "response_due_at": due, "authority_provenance": _provenance(auth)}
    value = object.__new__(Escalation)
    for name in Escalation.__dataclass_fields__:
        if name != "_integrity": object.__setattr__(value, name, values[name])
    object.__setattr__(value, "_integrity", _escalation_tuple(value)); return value


@dataclass(frozen=True, slots=True, init=False)
class CompletionClaim:
    claim_id: str
    work_id: str
    assignment_id: str
    attempt_id: str
    claimed_by: ExecutorReference
    claimed_at: datetime
    result_evidence_ids: tuple[str, ...]
    result_event_ids: tuple[str, ...]
    assertion_decision_ids: tuple[str, ...]
    assertion_approval_ids: tuple[str, ...]
    outcome: ClaimOutcome
    scope: AuthorityScope
    classification: ResourceClassification
    visibility_policy_id: str
    visibility_policy_version: str
    authority_provenance: AuthorityProvenance
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs; _fail(WorkErrorCode.INVALID_COMPLETION_CLAIM)


def _claim_tuple(value: CompletionClaim) -> tuple[object, ...]:
    return tuple(getattr(value, name) for name in CompletionClaim.__dataclass_fields__ if name != "_integrity")


def _valid_claim(value: object) -> bool:
    try:
        return type(value) is CompletionClaim and _valid_executor(value.claimed_by) and _valid_provenance(
            value.authority_provenance
        ) and _scope_valid(value.scope, value.claim_id) and value._integrity == _claim_tuple(value)
    except Exception: return False


def create_completion_claim(
    *, claim_id: str, work_id: str, assignment_id: str, attempt_id: str,
    claimed_by: ExecutorReference, claimed_at: datetime, outcome: ClaimOutcome,
    scope: AuthorityScope, classification: ResourceClassification,
    visibility_policy_id: str, visibility_policy_version: str,
    authority_decision: AuthorizationDecision, result_evidence_ids: tuple[str, ...] = (),
    result_event_ids: tuple[str, ...] = (), assertion_decision_ids: tuple[str, ...] = (),
    assertion_approval_ids: tuple[str, ...] = (),
    contract_version: str = WORK_CONTRACT_VERSION,
) -> CompletionClaim:
    validate_claim_id(claim_id); validate_work_id(work_id); validate_assignment_id(assignment_id); validate_attempt_id(attempt_id)
    if not _valid_executor(claimed_by) or type(outcome) is not ClaimOutcome:
        _fail(WorkErrorCode.INVALID_COMPLETION_CLAIM)
    if contract_version != WORK_CONTRACT_VERSION: _fail(WorkErrorCode.INVALID_VERSION)
    if not _scope_valid(scope, claim_id) or type(classification) is not ResourceClassification:
        _fail(WorkErrorCode.INVALID_SCOPE)
    _code(visibility_policy_id, WorkErrorCode.INVALID_COMPLETION_CLAIM); _code(visibility_policy_version, WorkErrorCode.INVALID_COMPLETION_CLAIM, sensitive=False)
    auth = _authority(authority_decision, resource_type="work_completion_claim", resource_id=claim_id, scope=scope, policy_id=visibility_policy_id, policy_version=visibility_policy_version, capability="work.completion.claim", actor_ref=claimed_by.executor_id)
    values = locals() | {
        "claimed_at": _utc(claimed_at),
        "result_evidence_ids": _refs(result_evidence_ids, validate_evidence_id, WorkErrorCode.INVALID_COMPLETION_CLAIM),
        "result_event_ids": _refs(result_event_ids, validate_event_id, WorkErrorCode.INVALID_COMPLETION_CLAIM),
        "assertion_decision_ids": _refs(assertion_decision_ids, validate_decision_id, WorkErrorCode.INVALID_COMPLETION_CLAIM),
        "assertion_approval_ids": _refs(assertion_approval_ids, validate_approval_id, WorkErrorCode.INVALID_COMPLETION_CLAIM),
        "authority_provenance": _provenance(auth),
    }
    value = object.__new__(CompletionClaim)
    for name in CompletionClaim.__dataclass_fields__:
        if name != "_integrity": object.__setattr__(value, name, values[name])
    object.__setattr__(value, "_integrity", _claim_tuple(value)); return value


@dataclass(frozen=True, slots=True)
class CompletionEvaluation:
    status: EvaluationStatus
    reason_codes: tuple[str, ...]
    satisfied_criteria: tuple[CriterionType, ...]
    missing_criteria: tuple[CriterionType, ...]


def _truth_authority_tuple(value: object) -> tuple[object, ...]:
    return (
        value.principal_ref, value.capability_code, value.resource_type,
        value.resource_id, value.policy_id, value.policy_version,
    )


def _valid_truth_approval(value: object) -> bool:
    try:
        return (
            type(value) is ApprovalRecord
            and _valid_actor(value.approver)
            and value._integrity
            == (
                value.approval_id, value.approval_type, value.target_type, value.target_id,
                _scope_tuple(value.scope), value.approver._integrity, value.status,
                value.recorded_at, value.effective_at, value.expires_at,
                value.supporting_event_ids, value.supporting_evidence_ids,
                value.classification, value.visibility_policy_id,
                value.visibility_policy_version, value.conditions_code, value.reason_code,
                _truth_authority_tuple(value.authority_provenance), value.contract_version,
            )
        )
    except Exception:
        return False


def _valid_truth_event(value: object) -> bool:
    try:
        return (
            type(value) is OperationalEvent and _valid_actor(value.actor)
            and value._integrity
            == (
                value.event_id, value.event_type, _scope_tuple(value.scope),
                value.context_ids, value.actor._integrity, value.occurred_at,
                value.recorded_at, value.received_at, value.evidence_ids,
                value.source_event_id, value.correlation_id, value.classification,
                value.visibility_policy_id, value.visibility_policy_version,
                value.reason_code, _truth_authority_tuple(value.authority_provenance),
                value.contract_version,
            )
        )
    except Exception:
        return False


def _valid_truth_decision(value: object) -> bool:
    try:
        return (
            type(value) is DecisionRecord and _valid_actor(value.decision_maker)
            and value._integrity
            == (
                value.decision_id, value.decision_type, value.target_type, value.target_id,
                _scope_tuple(value.scope), value.decision_maker._integrity,
                value.issued_at, value.effective_at, value.expires_at, value.outcome,
                value.status, value.supporting_event_ids, value.supporting_evidence_ids,
                value.supporting_context_ids, value.prior_decision_ids,
                value.approval_ids, value.classification, value.visibility_policy_id,
                value.visibility_policy_version, value.reason_code,
                _truth_authority_tuple(value.authority_provenance), value.contract_version,
            )
        )
    except Exception:
        return False


def evaluate_completion(
    work: WorkItem, claim: CompletionClaim, attempt: ExecutionAttempt,
    assignment: Assignment, *, approvals: Sequence[ApprovalRecord],
    decisions: Sequence[DecisionRecord], events: Sequence[OperationalEvent],
    completed_dependency_ids: frozenset[str], allow_self_verification: bool = False,
    evaluated_at: datetime | None = None,
) -> CompletionEvaluation:
    if not _valid_work(work) or not _valid_claim(claim) or not _valid_attempt(attempt) or not _valid_assignment(assignment):
        _fail(WorkErrorCode.INVALID_CRITERIA)
    if type(approvals) not in (list, tuple) or type(decisions) not in (list, tuple) or type(events) not in (list, tuple):
        _fail(WorkErrorCode.INVALID_CRITERIA)
    if any(not _valid_truth_approval(item) for item in approvals):
        _fail(WorkErrorCode.INVALID_CRITERIA)
    if any(not _valid_truth_decision(item) for item in decisions) or any(
        not _valid_truth_event(item) for item in events
    ):
        _fail(WorkErrorCode.INVALID_CRITERIA)
    if claim.work_id != work.work_id or claim.attempt_id != attempt.attempt_id or claim.assignment_id != assignment.assignment_id:
        _fail(WorkErrorCode.MISSING_REFERENCE)
    if attempt.status is not AttemptStatus.SUCCEEDED or attempt.executor != claim.claimed_by:
        _fail(WorkErrorCode.INVALID_COMPLETION_CLAIM)
    now = _utc(evaluated_at or claim.claimed_at, WorkErrorCode.INVALID_CRITERIA)
    approval_by_id = {item.approval_id: item for item in approvals if type(item) is ApprovalRecord}
    decision_by_id = {item.decision_id: item for item in decisions if type(item) is DecisionRecord}
    event_by_id = {item.event_id: item for item in events if type(item) is OperationalEvent}
    satisfied: list[CriterionType] = []
    missing: list[CriterionType] = []
    reasons: list[str] = []
    def relevant_truth_target(item: object) -> bool:
        return (
            item.target_type is TargetType.OPERATIONAL_CONTEXT
            and item.target_id in work.context_ids
        ) or (
            item.target_type is TargetType.DECISION
            and item.target_id in claim.assertion_decision_ids
        )
    for criterion in work.completion_criteria:
        count = 0
        if criterion.criterion_type is CriterionType.RESULT_REFERENCE_REQUIRED:
            count = len(claim.result_evidence_ids) + len(claim.result_event_ids)
        elif criterion.criterion_type is CriterionType.EVIDENCE_REQUIRED:
            count = len(claim.result_evidence_ids)
        elif criterion.criterion_type is CriterionType.EVENT_REQUIRED:
            count = sum(item in event_by_id for item in claim.result_event_ids)
        elif criterion.criterion_type is CriterionType.DECISION_REQUIRED:
            count = sum(
                item in decision_by_id and relevant_truth_target(decision_by_id[item])
                for item in claim.assertion_decision_ids
            )
        elif criterion.criterion_type in {CriterionType.APPROVAL_REQUIRED, CriterionType.INDEPENDENT_VERIFIER_REQUIRED}:
            valid = [
                approval_by_id[item] for item in claim.assertion_approval_ids
                if item in approval_by_id
                and approval_by_id[item].status is ApprovalStatus.APPROVED
                and relevant_truth_target(approval_by_id[item])
                and _scope_compatible(approval_by_id[item].scope, work.scope)
                and approval_by_id[item].visibility_policy_id == work.visibility_policy_id
                and approval_by_id[item].visibility_policy_version == work.visibility_policy_version
                and _visibility_ok(work, approval_by_id[item])
                and approval_by_id[item].effective_at <= now
                and (approval_by_id[item].expires_at is None or approval_by_id[item].expires_at > now)
            ]
            if criterion.capability_code is not None:
                valid = [item for item in valid if item.authority_provenance.capability_code == criterion.capability_code]
            if criterion.criterion_type is CriterionType.INDEPENDENT_VERIFIER_REQUIRED and not allow_self_verification:
                valid = [item for item in valid if item.approver.actor_ref != claim.claimed_by.executor_id]
            count = len({item.approver.actor_ref for item in valid})
        elif criterion.criterion_type is CriterionType.ALL_DEPENDENCIES_COMPLETED:
            count = len(completed_dependency_ids)
        elif criterion.criterion_type is CriterionType.EXECUTOR_DECLARATION_REQUIRED:
            count = 1 if claim.outcome is ClaimOutcome.CLAIMED else 0
        if count >= criterion.required_count:
            satisfied.append(criterion.criterion_type)
        else:
            missing.append(criterion.criterion_type)
            reasons.append(f"MISSING_{criterion.criterion_type.value}")
    if not work.completion_criteria:
        return CompletionEvaluation(EvaluationStatus.UNSATISFIED, ("NO_COMPLETION_CRITERIA",), (), ())
    return CompletionEvaluation(
        EvaluationStatus.SATISFIED if not missing else EvaluationStatus.UNSATISFIED,
        tuple(reasons) if reasons else ("COMPLETION_CRITERIA_SATISFIED",),
        tuple(satisfied), tuple(missing),
    )


def _record_scope_policy(record: object) -> tuple[AuthorityScope, str, str]:
    return record.scope, record.visibility_policy_id, record.visibility_policy_version


def _compatible(record: object, referenced: object) -> None:
    scope, policy, version = _record_scope_policy(record)
    if not _scope_compatible(scope, referenced.scope): _fail(WorkErrorCode.SCOPE_CONFLICT)
    if policy != referenced.visibility_policy_id or version != referenced.visibility_policy_version:
        _fail(WorkErrorCode.POLICY_CONFLICT)
    if not _visibility_ok(record, referenced): _fail(WorkErrorCode.VISIBILITY_CONFLICT)


def _acyclic(edges: Mapping[str, set[str]], error: WorkErrorCode) -> None:
    state: dict[str, int] = {}
    for start in sorted(edges):
        if state.get(start) == 2: continue
        stack: list[tuple[str, bool]] = [(start, False)]
        while stack:
            node, exiting = stack.pop()
            if exiting: state[node] = 2; continue
            if state.get(node) == 1: _fail(error)
            if state.get(node) == 2: continue
            state[node] = 1; stack.append((node, True))
            for target in sorted(edges.get(node, ()), reverse=True):
                if state.get(target) == 1: _fail(error)
                if state.get(target) != 2: stack.append((target, False))


@dataclass(frozen=True, slots=True)
class ValidatedWorkCollection:
    materialized_statuses: tuple[tuple[str, WorkStatus], ...]
    unresolved_blockers: tuple[str, ...]
    unterminated_attempt_ids: tuple[str, ...]


def validate_work_collection(
    *, work_items: Sequence[WorkItem], assignments: Sequence[Assignment],
    transitions: Sequence[WorkTransition], attempts: Sequence[ExecutionAttempt],
    dependencies: Sequence[WorkDependency], handoffs: Sequence[Handoff],
    escalations: Sequence[Escalation], claims: Sequence[CompletionClaim],
    contexts: Sequence[OperationalContextRecord], evidence_records: Sequence[EvidenceRecord],
    events: Sequence[OperationalEvent], decisions: Sequence[DecisionRecord],
    approvals: Sequence[ApprovalRecord], evaluated_at: datetime,
) -> ValidatedWorkCollection:
    all_collections = (work_items, assignments, transitions, attempts, dependencies, handoffs, escalations, claims, contexts, evidence_records, events, decisions, approvals)
    if any(type(items) not in (list, tuple) for items in all_collections): _fail(WorkErrorCode.INVALID_WORK_ITEM)
    if any(len(items) > MAX_RECORDS_PER_KIND for items in all_collections if items is not dependencies) or len(dependencies) > MAX_DEPENDENCIES:
        _fail(WorkErrorCode.COLLECTION_LIMIT_EXCEEDED)
    validators = (_valid_work, _valid_assignment, _valid_transition, _valid_attempt, lambda item: type(item) is WorkDependency and item._integrity == item._tuple(), _valid_handoff, _valid_escalation, _valid_claim)
    for items, validator in zip(all_collections[:8], validators, strict=True):
        if any(not validator(item) for item in items): _fail(WorkErrorCode.INVALID_WORK_ITEM)
    registries = (
        {item.work_id: item for item in work_items}, {item.assignment_id: item for item in assignments},
        {item.transition_id: item for item in transitions}, {item.attempt_id: item for item in attempts},
        {item.handoff_id: item for item in handoffs}, {item.escalation_id: item for item in escalations},
        {item.claim_id: item for item in claims},
    )
    for registry, items in zip(registries, (work_items, assignments, transitions, attempts, handoffs, escalations, claims), strict=True):
        if len(registry) != len(items): _fail(WorkErrorCode.DUPLICATE_IDENTIFIER)
    all_ids = [identifier for registry in registries for identifier in registry]
    if len(all_ids) != len(set(all_ids)): _fail(WorkErrorCode.DUPLICATE_IDENTIFIER)
    work_by, assignment_by, _, attempt_by, _, _, _ = registries
    context_by = {item.context_id: item for item in contexts}
    evidence_by = {item.evidence_id: item for item in evidence_records}
    event_by = {item.event_id: item for item in events}
    decision_by = {item.decision_id: item for item in decisions}
    approval_by = {item.approval_id: item for item in approvals}
    if any(len(registry) != len(items) for registry, items in ((context_by, contexts), (evidence_by, evidence_records), (event_by, events), (decision_by, decisions), (approval_by, approvals))):
        _fail(WorkErrorCode.DUPLICATE_IDENTIFIER)
    for context in contexts: context_metadata(context)
    for evidence in evidence_records: evidence_metadata(evidence)
    if any(not _valid_truth_event(item) for item in events): _fail(WorkErrorCode.INVALID_WORK_ITEM)
    if any(not _valid_truth_decision(item) for item in decisions): _fail(WorkErrorCode.INVALID_WORK_ITEM)
    if any(not _valid_truth_approval(item) for item in approvals): _fail(WorkErrorCode.INVALID_WORK_ITEM)
    for work in work_items:
        for registry, ids in ((context_by, work.context_ids), (evidence_by, work.input_evidence_ids), (event_by, work.source_event_ids), (decision_by, work.source_decision_ids), (approval_by, work.source_approval_ids), (work_by, work.source_work_ids)):
            for ref in ids:
                item = registry.get(ref)
                if item is None: _fail(WorkErrorCode.MISSING_REFERENCE)
                _compatible(work, item)
    source_work_edges: dict[str, set[str]] = {}
    for work in work_items:
        for source_id in work.source_work_ids:
            if source_id == work.work_id: _fail(WorkErrorCode.INVALID_WORK_ITEM)
            source_work_edges.setdefault(work.work_id, set()).add(source_id)
    _acyclic(source_work_edges, WorkErrorCode.DEPENDENCY_CYCLE)
    active_assignments: dict[str, list[Assignment]] = {}
    superseded_assignment_ids = {
        item.supersedes_assignment_id for item in assignments
        if item.supersedes_assignment_id is not None
    }
    for assignment in assignments:
        work = work_by.get(assignment.work_id)
        if work is None: _fail(WorkErrorCode.MISSING_REFERENCE)
        _compatible(assignment, work)
        if assignment.assigned_at < work.created_at: _fail(WorkErrorCode.CHRONOLOGY_CONFLICT)
        if assignment.supersedes_assignment_id is not None:
            prior = assignment_by.get(assignment.supersedes_assignment_id)
            if prior is None or prior.work_id != assignment.work_id or prior.assigned_at >= assignment.assigned_at:
                _fail(WorkErrorCode.INVALID_ASSIGNMENT)
        if assignment.status in {AssignmentStatus.ASSIGNED, AssignmentStatus.ACCEPTED}:
            active_assignments.setdefault(assignment.work_id, []).append(assignment)
    for work_id, active in active_assignments.items():
        superseded = {item.supersedes_assignment_id for item in active if item.supersedes_assignment_id}
        remaining = [item for item in active if item.assignment_id not in superseded]
        if len(remaining) > 1: _fail(WorkErrorCode.DUPLICATE_ACTIVE_ASSIGNMENT)
    assignment_edges = {
        item.assignment_id: {item.supersedes_assignment_id}
        for item in assignments if item.supersedes_assignment_id is not None
    }
    _acyclic(assignment_edges, WorkErrorCode.INVALID_ASSIGNMENT)
    transitions_by_work: dict[str, list[WorkTransition]] = {}
    for transition in transitions:
        work = work_by.get(transition.work_id)
        if work is None: _fail(WorkErrorCode.MISSING_REFERENCE)
        _compatible(transition, work)
        if transition.occurred_at < work.created_at: _fail(WorkErrorCode.CHRONOLOGY_CONFLICT)
        for registry, ref in ((event_by, transition.linked_event_id), (decision_by, transition.linked_decision_id), (approval_by, transition.linked_approval_id)):
            if ref is not None:
                item = registry.get(ref)
                if item is None: _fail(WorkErrorCode.MISSING_REFERENCE)
                _compatible(transition, item)
        transitions_by_work.setdefault(transition.work_id, []).append(transition)
    materialized: dict[str, WorkStatus] = {}
    terminal_times: dict[str, datetime] = {}
    for work in work_items:
        current = work.initial_status; last = work.created_at
        for transition in sorted(transitions_by_work.get(work.work_id, ()), key=lambda item: (item.occurred_at, item.transition_id)):
            if transition.occurred_at <= last or transition.from_status is not current:
                _fail(WorkErrorCode.INVALID_STATUS_TRANSITION)
            current = transition.to_status; last = transition.occurred_at
            if current in {WorkStatus.COMPLETED, WorkStatus.CANCELLED, WorkStatus.EXPIRED, WorkStatus.INVALIDATED}:
                terminal_times[work.work_id] = transition.occurred_at
        materialized[work.work_id] = current
    active_attempts: dict[str, list[ExecutionAttempt]] = {}
    attempts_by_assignment: dict[str, list[ExecutionAttempt]] = {}
    for attempt in attempts:
        work = work_by.get(attempt.work_id); assignment = assignment_by.get(attempt.assignment_id)
        if work is None or assignment is None or assignment.work_id != attempt.work_id:
            _fail(WorkErrorCode.MISSING_REFERENCE)
        _compatible(attempt, work); _compatible(attempt, assignment)
        if assignment.status is not AssignmentStatus.ACCEPTED: _fail(WorkErrorCode.ASSIGNMENT_NOT_ACCEPTED)
        if assignment.assignment_id in superseded_assignment_ids:
            _fail(WorkErrorCode.ASSIGNMENT_NOT_ACCEPTED)
        if assignment.assignee != attempt.executor: _fail(WorkErrorCode.EXECUTOR_MISMATCH)
        if attempt.started_at < (assignment.responded_at or assignment.assigned_at):
            _fail(WorkErrorCode.CHRONOLOGY_CONFLICT)
        if attempt.started_at >= terminal_times.get(work.work_id, datetime.max.replace(tzinfo=timezone.utc)):
            _fail(WorkErrorCode.INVALID_ATTEMPT)
        attempts_by_assignment.setdefault(assignment.assignment_id, []).append(attempt)
        if attempt.status is AttemptStatus.STARTED: active_attempts.setdefault(work.work_id, []).append(attempt)
        for registry, ids in ((context_by, attempt.input_context_ids), (evidence_by, attempt.input_evidence_ids), (evidence_by, attempt.result_evidence_ids), (event_by, attempt.result_event_ids), (decision_by, attempt.result_decision_ids), (approval_by, attempt.result_approval_ids)):
            for ref in ids:
                item = registry.get(ref)
                if item is None: _fail(WorkErrorCode.MISSING_REFERENCE)
                _compatible(attempt, item)
    if any(len(items) > 1 for items in active_attempts.values()): _fail(WorkErrorCode.DUPLICATE_ACTIVE_ATTEMPT)
    for items in attempts_by_assignment.values():
        ordered = sorted(items, key=lambda item: item.sequence)
        if [item.sequence for item in ordered] != list(range(1, len(ordered) + 1)):
            _fail(WorkErrorCode.INVALID_ATTEMPT)
        if any(ordered[index].started_at <= ordered[index - 1].started_at for index in range(1, len(ordered))):
            _fail(WorkErrorCode.CHRONOLOGY_CONFLICT)
        if any(
            ordered[index - 1].ended_at is None
            or ordered[index].started_at <= ordered[index - 1].ended_at
            for index in range(1, len(ordered))
        ):
            _fail(WorkErrorCode.CHRONOLOGY_CONFLICT)
    dep_edges: dict[str, set[str]] = {}; dep_keys: set[tuple[str, str, DependencyType]] = set(); blockers: set[str] = set()
    for dep in dependencies:
        key = (dep.source_work_id, dep.target_work_id, dep.dependency_type)
        if key in dep_keys: _fail(WorkErrorCode.INVALID_DEPENDENCY)
        dep_keys.add(key)
        source = work_by.get(dep.source_work_id); target = work_by.get(dep.target_work_id)
        if source is None or target is None: _fail(WorkErrorCode.MISSING_REFERENCE)
        _compatible(source, target)
        if dep.dependency_type in {DependencyType.REQUIRES, DependencyType.BLOCKED_BY, DependencyType.FOLLOWS}:
            dep_edges.setdefault(dep.source_work_id, set()).add(dep.target_work_id)
            if materialized[target.work_id] is not WorkStatus.COMPLETED: blockers.add(source.work_id)
    _acyclic(dep_edges, WorkErrorCode.DEPENDENCY_CYCLE)
    handoff_edges: dict[str, set[str]] = {}; active_handoffs: set[str] = set()
    for handoff in handoffs:
        source = assignment_by.get(handoff.source_assignment_id); target = assignment_by.get(handoff.target_assignment_id)
        work = work_by.get(handoff.work_id)
        if source is None or target is None or work is None or source.work_id != work.work_id or target.work_id != work.work_id:
            _fail(WorkErrorCode.MISSING_REFERENCE)
        _compatible(handoff, work); _compatible(handoff, source); _compatible(handoff, target)
        if handoff.target_executor != target.assignee:
            _fail(WorkErrorCode.EXECUTOR_MISMATCH)
        handoff_edges.setdefault(source.assignment_id, set()).add(target.assignment_id)
        if handoff.status in {HandoffStatus.OFFERED, HandoffStatus.ACCEPTED}:
            if work.work_id in active_handoffs: _fail(WorkErrorCode.INVALID_HANDOFF)
            active_handoffs.add(work.work_id)
    _acyclic(handoff_edges, WorkErrorCode.HANDOFF_CYCLE)
    open_escalations: set[tuple[str, str, str]] = set()
    for escalation in escalations:
        work = work_by.get(escalation.work_id)
        if work is None: _fail(WorkErrorCode.MISSING_REFERENCE)
        _compatible(escalation, work)
        key = (escalation.work_id, escalation.target_ref, escalation.reason_code)
        if escalation.status in {EscalationStatus.OPEN, EscalationStatus.ACKNOWLEDGED}:
            if key in open_escalations: _fail(WorkErrorCode.DUPLICATE_UNRESOLVED_ESCALATION)
            open_escalations.add(key)
    claim_keys: set[tuple[str, str]] = set()
    for claim in claims:
        work = work_by.get(claim.work_id); assignment = assignment_by.get(claim.assignment_id); attempt = attempt_by.get(claim.attempt_id)
        if work is None or assignment is None or attempt is None: _fail(WorkErrorCode.MISSING_REFERENCE)
        _compatible(claim, work); _compatible(claim, assignment); _compatible(claim, attempt)
        if attempt.status is not AttemptStatus.SUCCEEDED or attempt.executor != claim.claimed_by:
            _fail(WorkErrorCode.INVALID_COMPLETION_CLAIM)
        if not set(claim.result_evidence_ids) <= set(attempt.result_evidence_ids):
            _fail(WorkErrorCode.INVALID_COMPLETION_CLAIM)
        if not set(claim.result_event_ids) <= set(attempt.result_event_ids):
            _fail(WorkErrorCode.INVALID_COMPLETION_CLAIM)
        if claim.claimed_at <= (attempt.ended_at or attempt.started_at): _fail(WorkErrorCode.CHRONOLOGY_CONFLICT)
        key = (claim.work_id, claim.attempt_id)
        if key in claim_keys: _fail(WorkErrorCode.DUPLICATE_COMPLETION_CLAIM)
        claim_keys.add(key)
        for registry, ids in ((evidence_by, claim.result_evidence_ids), (event_by, claim.result_event_ids), (decision_by, claim.assertion_decision_ids), (approval_by, claim.assertion_approval_ids)):
            for ref in ids:
                item = registry.get(ref)
                if item is None: _fail(WorkErrorCode.MISSING_REFERENCE)
                _compatible(claim, item)
    evaluation_time = _utc(evaluated_at)
    for work in work_items:
        if materialized[work.work_id] is not WorkStatus.COMPLETED: continue
        work_claims = sorted((item for item in claims if item.work_id == work.work_id), key=lambda item: item.claimed_at)
        if not work_claims: _fail(WorkErrorCode.CRITERIA_UNSATISFIED)
        latest = work_claims[-1]
        evaluation = evaluate_completion(
            work, latest, attempt_by[latest.attempt_id], assignment_by[latest.assignment_id],
            approvals=approvals, decisions=decisions, events=events,
            completed_dependency_ids=frozenset(
                dep.target_work_id for dep in dependencies
                if dep.source_work_id == work.work_id
                and materialized.get(dep.target_work_id) is WorkStatus.COMPLETED
            ),
            evaluated_at=evaluation_time,
        )
        if evaluation.status is not EvaluationStatus.SATISFIED:
            _fail(WorkErrorCode.CRITERIA_UNSATISFIED)
    unterminated = tuple(sorted(
        item.attempt_id for item in attempts
        if item.status is AttemptStatus.STARTED
        and materialized[item.work_id] in {WorkStatus.CANCELLED, WorkStatus.EXPIRED, WorkStatus.INVALIDATED}
    ))
    return ValidatedWorkCollection(
        tuple(sorted(materialized.items())), tuple(sorted(blockers)), unterminated,
    )


def _scope_metadata(scope: AuthorityScope) -> dict[str, Any]:
    return {
        "scope_organization_id": scope.organization_id, "scope_product_id": scope.product_id,
        "scope_workspace_id": scope.workspace_id, "scope_project_id": scope.project_id,
        "scope_resource_id": scope.resource_id, "scope_owner_party_id": scope.owner_party_id,
        "scope_global": scope.global_scope,
    }


def _metadata(record: object) -> dict[str, Any]:
    common = {
        "contract_version": record.contract_version,
        "classification": record.classification.value,
        "visibility_policy_id": record.visibility_policy_id,
        "visibility_policy_version": record.visibility_policy_version,
        **_scope_metadata(record.scope),
    }
    if type(record) is WorkItem and _valid_work(record):
        return common | {
            "work_id": record.work_id, "work_type": record.work_type.value,
            "requester_ref": record.requester.actor_ref, "requester_kind": record.requester.actor_kind.value,
            "created_at": _iso(record.created_at), "ready_at": _iso(record.ready_at),
            "due_at": _iso(record.due_at), "priority": record.priority.value,
            "initial_status": record.initial_status.value, "context_count": len(record.context_ids),
            "input_count": len(record.input_evidence_ids), "criteria_count": len(record.completion_criteria),
        }
    if type(record) is Assignment and _valid_assignment(record):
        return common | {
            "assignment_id": record.assignment_id, "work_id": record.work_id,
            "executor_id": record.assignee.executor_id, "executor_kind": record.assignee.executor_kind.value,
            "assigner_ref": record.assigner.actor_ref, "assigned_at": _iso(record.assigned_at),
            "responded_at": _iso(record.responded_at),
            "acceptance_deadline": _iso(record.acceptance_deadline), "due_at": _iso(record.due_at),
            "status": record.status.value, "required_execution_capability": record.required_execution_capability,
            "reason_code": record.reason_code, "supersedes_assignment_id": record.supersedes_assignment_id,
        }
    if type(record) is ExecutionAttempt and _valid_attempt(record):
        return common | {
            "attempt_id": record.attempt_id, "work_id": record.work_id,
            "assignment_id": record.assignment_id, "executor_id": record.executor.executor_id,
            "executor_kind": record.executor.executor_kind.value, "sequence": record.sequence,
            "started_at": _iso(record.started_at), "ended_at": _iso(record.ended_at),
            "status": record.status.value, "outcome_code": record.outcome_code,
            "input_count": len(record.input_evidence_ids) + len(record.input_context_ids),
            "result_count": len(record.result_evidence_ids) + len(record.result_event_ids) + len(record.result_decision_ids) + len(record.result_approval_ids),
        }
    if type(record) is Handoff and _valid_handoff(record):
        return common | {
            "handoff_id": record.handoff_id, "work_id": record.work_id,
            "source_assignment_id": record.source_assignment_id,
            "target_assignment_id": record.target_assignment_id,
            "target_executor_id": record.target_executor.executor_id,
            "target_executor_kind": record.target_executor.executor_kind.value,
            "initiated_by": record.initiated_by.actor_ref, "initiated_at": _iso(record.initiated_at),
            "accepted_at": _iso(record.accepted_at), "status": record.status.value,
            "reason_code": record.reason_code,
        }
    if type(record) is Escalation and _valid_escalation(record):
        return common | {
            "escalation_id": record.escalation_id, "work_id": record.work_id,
            "raised_by": record.raised_by.actor_ref, "target_ref": record.target_ref,
            "target_capability": record.target_capability, "severity": record.severity.value,
            "reason_code": record.reason_code, "raised_at": _iso(record.raised_at),
            "response_due_at": _iso(record.response_due_at), "status": record.status.value,
        }
    if type(record) is CompletionClaim and _valid_claim(record):
        return common | {
            "claim_id": record.claim_id, "work_id": record.work_id,
            "assignment_id": record.assignment_id, "attempt_id": record.attempt_id,
            "executor_id": record.claimed_by.executor_id, "executor_kind": record.claimed_by.executor_kind.value,
            "claimed_at": _iso(record.claimed_at), "outcome": record.outcome.value,
            "result_count": len(record.result_evidence_ids) + len(record.result_event_ids),
            "assertion_count": len(record.assertion_decision_ids) + len(record.assertion_approval_ids),
        }
    _fail(WorkErrorCode.INVALID_PROJECTION)


_COMPUTED = frozenset({"context_count", "input_count", "criteria_count", "result_count", "assertion_count"})
_SPECS = {
    WorkItem: ("work_item", "work_id", validate_work_id, _valid_work, {"work_type": WorkType, "priority": WorkPriority, "initial_status": WorkStatus}),
    Assignment: ("work_assignment", "assignment_id", validate_assignment_id, _valid_assignment, {"status": AssignmentStatus, "executor_kind": ExecutorKind}),
    ExecutionAttempt: ("work_attempt", "attempt_id", validate_attempt_id, _valid_attempt, {"status": AttemptStatus, "executor_kind": ExecutorKind}),
    Handoff: ("work_handoff", "handoff_id", validate_handoff_id, _valid_handoff, {"status": HandoffStatus}),
    Escalation: ("work_escalation", "escalation_id", validate_escalation_id, _valid_escalation, {"status": EscalationStatus, "severity": EscalationSeverity}),
    CompletionClaim: ("work_completion_claim", "claim_id", validate_claim_id, _valid_claim, {"outcome": ClaimOutcome, "executor_kind": ExecutorKind}),
}


def _project(record: object, decision: AuthorizationDecision, record_type: type) -> dict[str, Any]:
    resource_type, id_field, validator, valid_record, enum_fields = _SPECS[record_type]
    try:
        if type(record) is record_type:
            if not valid_record(record): _fail(WorkErrorCode.INVALID_PROJECTION)
            source = _metadata(record); scope = record.scope
        elif isinstance(record, Mapping):
            _fail(WorkErrorCode.INVALID_PROJECTION)
        else: _fail(WorkErrorCode.INVALID_PROJECTION)
        project_authorized_fields({}, decision, behavior=ProjectionBehavior.OMIT)
        if (
            decision.resource_type != resource_type or decision.resource_id != source[id_field]
            or decision.policy_id != source["visibility_policy_id"]
            or decision.policy_version != source["visibility_policy_version"]
            or (decision.allowed and decision.effective_scope != scope)
        ): _fail(WorkErrorCode.INVALID_AUTHORITY_DECISION)
        return project_authorized_fields(source, decision, behavior=ProjectionBehavior.OMIT)
    except WorkContractError: raise
    except Exception: _fail(WorkErrorCode.INVALID_PROJECTION)


def project_work_item(record: WorkItem | Mapping[str, Any], decision: AuthorizationDecision) -> dict[str, Any]: return _project(record, decision, WorkItem)
def project_assignment(record: Assignment | Mapping[str, Any], decision: AuthorizationDecision) -> dict[str, Any]: return _project(record, decision, Assignment)
def project_execution_attempt(record: ExecutionAttempt | Mapping[str, Any], decision: AuthorizationDecision) -> dict[str, Any]: return _project(record, decision, ExecutionAttempt)
def project_handoff(record: Handoff | Mapping[str, Any], decision: AuthorizationDecision) -> dict[str, Any]: return _project(record, decision, Handoff)
def project_escalation(record: Escalation | Mapping[str, Any], decision: AuthorizationDecision) -> dict[str, Any]: return _project(record, decision, Escalation)
def project_completion_claim(record: CompletionClaim | Mapping[str, Any], decision: AuthorizationDecision) -> dict[str, Any]: return _project(record, decision, CompletionClaim)


@dataclass(frozen=True, slots=True, init=False)
class SafeWorkAudit:
    audit_id: str
    action_code: str
    record_type: str
    record_id: str
    executor_kind: str | None
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
        del cls, args, kwargs
        _fail(WorkErrorCode.INVALID_AUDIT)


def _new_work_audit(*values: object) -> SafeWorkAudit:
    names = tuple(name for name in SafeWorkAudit.__dataclass_fields__ if name != "_integrity")
    value = object.__new__(SafeWorkAudit)
    for name, item in zip(names, values, strict=True): object.__setattr__(value, name, item)
    object.__setattr__(value, "_integrity", tuple(values))
    return value


_AUDIT_TARGETS = {
    "work_item": validate_work_id, "work_assignment": validate_assignment_id,
    "work_attempt": validate_attempt_id, "work_transition": validate_transition_id,
    "work_handoff": validate_handoff_id, "work_escalation": validate_escalation_id,
    "work_completion_claim": validate_claim_id,
}


def build_safe_work_audit(
    *, audit_id: str, action_code: str, record_type: str, record_id: str,
    decision: AuthorizationDecision, timestamp: datetime,
    executor: ExecutorReference | None = None, correlation_id: str | None = None,
) -> SafeWorkAudit:
    validate_work_audit_id(audit_id); _code(action_code, WorkErrorCode.INVALID_AUDIT)
    validator = _AUDIT_TARGETS.get(record_type)
    if validator is None: _fail(WorkErrorCode.INVALID_AUDIT)
    try: validator(record_id); project_authorized_fields({}, decision, behavior=ProjectionBehavior.OMIT)
    except Exception: _fail(WorkErrorCode.INVALID_AUDIT)
    if decision.resource_type != record_type or decision.resource_id != record_id or decision.action_code != action_code:
        _fail(WorkErrorCode.INVALID_AUDIT)
    if executor is not None and not _valid_executor(executor): _fail(WorkErrorCode.INVALID_AUDIT)
    if correlation_id is not None: _code(correlation_id, WorkErrorCode.INVALID_AUDIT)
    include_executor = executor is not None and decision.allowed and "executor_kind" in decision.visible_fields and decision.principal_ref == executor.executor_id
    return _new_work_audit(
        audit_id, action_code, record_type, record_id,
        executor.executor_kind.value if include_executor else None,
        "ALLOWED" if decision.allowed else "DENIED", decision.reason_code.value,
        decision.policy_id, decision.policy_version, record_id,
        _iso(_utc(timestamp, WorkErrorCode.INVALID_AUDIT)) or "", correlation_id,
        WORK_CONTRACT_VERSION,
    )


def build_work_rejection_audit(
    *, audit_id: str, action_code: str, record_type: str, record_id: str,
    reason_code: WorkErrorCode, policy_id: str, policy_version: str,
    timestamp: datetime, correlation_id: str | None = None,
) -> SafeWorkAudit:
    validate_work_audit_id(audit_id); _code(action_code, WorkErrorCode.INVALID_AUDIT)
    validator = _AUDIT_TARGETS.get(record_type)
    if validator is None:
        if record_type != "malformed_record": _fail(WorkErrorCode.INVALID_AUDIT)
        _code(record_id, WorkErrorCode.INVALID_AUDIT, maximum=_MAX_ID)
    else:
        try: validator(record_id)
        except Exception: _fail(WorkErrorCode.INVALID_AUDIT)
    if type(reason_code) is not WorkErrorCode: _fail(WorkErrorCode.INVALID_AUDIT)
    _code(policy_id, WorkErrorCode.INVALID_AUDIT); _code(policy_version, WorkErrorCode.INVALID_AUDIT, sensitive=False)
    if correlation_id is not None: _code(correlation_id, WorkErrorCode.INVALID_AUDIT)
    return _new_work_audit(audit_id, action_code, record_type, record_id, None, "REJECTED", reason_code.value, policy_id, policy_version, record_id, _iso(_utc(timestamp, WorkErrorCode.INVALID_AUDIT)) or "", correlation_id, WORK_CONTRACT_VERSION)


@dataclass(frozen=True, slots=True)
class DerivedWorkloadView:
    view_kind: WorkloadViewKind
    work_ids: tuple[str, ...]
    classification: ResourceClassification
    visible_fields: frozenset[str]
    policy_id: str
    policy_version: str
    derived: bool = True


def create_derived_workload_view(
    sources: Sequence[tuple[WorkItem, AuthorizationDecision]], *,
    view_kind: WorkloadViewKind, materialized_statuses: Mapping[str, WorkStatus],
    evaluated_at: datetime,
    assignments: Sequence[tuple[Assignment, AuthorizationDecision]] = (),
    assigned_executor_id: str | None = None,
    escalations: Sequence[tuple[Escalation, AuthorizationDecision]] = (),
) -> DerivedWorkloadView:
    if type(sources) not in (list, tuple) or not sources or type(view_kind) is not WorkloadViewKind:
        _fail(WorkErrorCode.SOURCE_NOT_AUTHORIZED)
    when = _utc(evaluated_at)
    if type(assignments) not in (list, tuple) or any(
        type(pair) is not tuple or len(pair) != 2 or not _valid_assignment(pair[0])
        for pair in assignments
    ):
        _fail(WorkErrorCode.SOURCE_NOT_AUTHORIZED)
    if type(escalations) not in (list, tuple) or any(
        type(pair) is not tuple or len(pair) != 2 or not _valid_escalation(pair[0])
        for pair in escalations
    ):
        _fail(WorkErrorCode.SOURCE_NOT_AUTHORIZED)
    visibility: list[SourceVisibility] = []; works: list[WorkItem] = []; seen: set[str] = set()
    for pair in sources:
        if type(pair) is not tuple or len(pair) != 2 or not _valid_work(pair[0]): _fail(WorkErrorCode.SOURCE_NOT_AUTHORIZED)
        work, decision = pair
        if work.work_id in seen or type(materialized_statuses.get(work.work_id)) is not WorkStatus:
            _fail(WorkErrorCode.SOURCE_NOT_AUTHORIZED)
        seen.add(work.work_id)
        try: project_work_item(work, decision)
        except Exception: _fail(WorkErrorCode.SOURCE_NOT_AUTHORIZED)
        if not decision.allowed or "work_id" not in decision.visible_fields: _fail(WorkErrorCode.SOURCE_NOT_AUTHORIZED)
        works.append(work)
        visibility.append(SourceVisibility(work.work_id, work.classification, decision.visible_fields, (work.scope,), True, work.visibility_policy_id, work.visibility_policy_version))
    assignment_records: list[Assignment] = []
    for assignment, decision in assignments:
        try: project_assignment(assignment, decision)
        except Exception: _fail(WorkErrorCode.SOURCE_NOT_AUTHORIZED)
        required = {"assignment_id", "work_id", "status"}
        if view_kind is WorkloadViewKind.ASSIGNED_TO: required.add("executor_id")
        if not decision.allowed or not required <= decision.visible_fields:
            _fail(WorkErrorCode.SOURCE_NOT_AUTHORIZED)
        assignment_records.append(assignment)
        visibility.append(SourceVisibility(
            assignment.assignment_id, assignment.classification, decision.visible_fields,
            (assignment.scope,), True, assignment.visibility_policy_id,
            assignment.visibility_policy_version,
        ))
    escalation_records: list[Escalation] = []
    for escalation, decision in escalations:
        try: project_escalation(escalation, decision)
        except Exception: _fail(WorkErrorCode.SOURCE_NOT_AUTHORIZED)
        if not decision.allowed or not {"escalation_id", "work_id", "status"} <= decision.visible_fields:
            _fail(WorkErrorCode.SOURCE_NOT_AUTHORIZED)
        escalation_records.append(escalation)
        visibility.append(SourceVisibility(
            escalation.escalation_id, escalation.classification, decision.visible_fields,
            (escalation.scope,), True, escalation.visibility_policy_id,
            escalation.visibility_policy_version,
        ))
    inherited = inherit_derived_visibility(tuple(sorted(visibility, key=lambda item: item.source_resource_id)), artifact_kind=DerivedArtifactKind.REPORT)
    if inherited.reason_code is AuthorityReason.SOURCE_SCOPE_CONFLICT: _fail(WorkErrorCode.SOURCE_SCOPE_CONFLICT)
    if inherited.reason_code is AuthorityReason.SOURCE_POLICY_CONFLICT: _fail(WorkErrorCode.SOURCE_POLICY_CONFLICT)
    if not inherited.allowed: _fail(WorkErrorCode.SOURCE_NOT_AUTHORIZED)
    source_by_id = {item.work_id: item for item in works}
    for item in assignment_records:
        work = source_by_id.get(item.work_id)
        if work is None:
            _fail(WorkErrorCode.SOURCE_NOT_AUTHORIZED)
        _compatible(item, work)
    for item in escalation_records:
        work = source_by_id.get(item.work_id)
        if work is None:
            _fail(WorkErrorCode.SOURCE_NOT_AUTHORIZED)
        _compatible(item, work)
    active = {WorkStatus.READY, WorkStatus.ASSIGNED, WorkStatus.ACCEPTED, WorkStatus.IN_PROGRESS, WorkStatus.BLOCKED, WorkStatus.WAITING, WorkStatus.WAITING_APPROVAL, WorkStatus.COMPLETION_CLAIMED}
    assigned_work = {item.work_id for item in assignment_records if item.assignee.executor_id == assigned_executor_id and item.status in {AssignmentStatus.ASSIGNED, AssignmentStatus.ACCEPTED}}
    unresolved = {item.work_id for item in escalation_records if item.status in {EscalationStatus.OPEN, EscalationStatus.ACKNOWLEDGED}}
    def include(work: WorkItem) -> bool:
        status = materialized_statuses[work.work_id]
        return {
            WorkloadViewKind.ASSIGNED_TO: work.work_id in assigned_work,
            WorkloadViewKind.ACTIVE: status in active,
            WorkloadViewKind.BLOCKED: status is WorkStatus.BLOCKED,
            WorkloadViewKind.OVERDUE: work.due_at is not None and work.due_at < when and status not in {WorkStatus.COMPLETED, WorkStatus.CANCELLED, WorkStatus.EXPIRED, WorkStatus.INVALIDATED},
            WorkloadViewKind.WAITING_APPROVAL: status is WorkStatus.WAITING_APPROVAL,
            WorkloadViewKind.COMPLETION_CLAIMED: status is WorkStatus.COMPLETION_CLAIMED,
            WorkloadViewKind.FAILED: status is WorkStatus.FAILED,
            WorkloadViewKind.UNRESOLVED_ESCALATIONS: work.work_id in unresolved,
        }[view_kind]
    policy_id, policy_version = inherited.source_policies[0]
    return DerivedWorkloadView(view_kind, tuple(sorted(work.work_id for work in works if include(work))), inherited.classification, inherited.visible_fields, policy_id, policy_version)


def adapt_current_task(*args: object, **kwargs: object) -> NoReturn:
    del args, kwargs
    _fail(WorkErrorCode.UNSUPPORTED_ADAPTER)


__all__ = (
    "Assignment", "AssignmentStatus", "AttemptStatus", "ClaimOutcome",
    "CompletionClaim", "CompletionCriterion", "CompletionEvaluation", "CriterionType",
    "DependencyType", "DerivedWorkloadView", "Escalation", "EscalationSeverity",
    "EscalationStatus", "EvaluationStatus", "ExecutionAttempt", "ExecutorKind",
    "ExecutorReference", "Handoff", "HandoffStatus", "MAX_ATTEMPTS",
    "MAX_DEPENDENCIES", "MAX_RECORDS_PER_KIND", "RetryEvaluation", "RetryPolicy",
    "RetryReason", "SafeWorkAudit", "ValidatedWorkCollection", "WORK_CONTRACT_VERSION",
    "WORK_POLICY_VERSION", "WorkContractError", "WorkDependency", "WorkErrorCode",
    "WorkItem", "WorkPriority", "WorkStatus", "WorkTransition", "WorkType",
    "WorkloadViewKind", "adapt_current_task", "build_safe_work_audit",
    "build_work_rejection_audit", "create_assignment", "create_completion_claim",
    "create_derived_workload_view", "create_escalation", "create_execution_attempt",
    "create_handoff", "create_work_item", "create_work_transition",
    "evaluate_completion", "evaluate_retry", "project_assignment",
    "project_completion_claim", "project_escalation", "project_execution_attempt",
    "project_handoff", "project_work_item", "validate_assignment_id",
    "validate_attempt_id", "validate_claim_id", "validate_escalation_id",
    "validate_handoff_id", "validate_transition_id", "validate_work_audit_id",
    "validate_work_collection", "validate_work_id",
)
