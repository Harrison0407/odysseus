"""Pure MarketMatch Operations & Reliability Kernel V1.

The kernel is deliberately inert: it performs no persistence, environment or
secret reads, scheduling, sleeping, network access, notification delivery,
health probing, backup/restore, rollback, logging, or model invocation.  It
validates immutable records and derives deterministic decisions from values
supplied by an authorized caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import re
from typing import Any, Mapping, NoReturn, Sequence

from src.marketmatch_authority import (
    AuthorityScope,
    AuthorizationDecision,
    ProjectionBehavior,
    ResourceClassification,
    project_authorized_fields,
)
from src.marketmatch_tenancy import TenantContext, validate_tenant_id
from src.marketmatch_evidence import validate_evidence_id
from src.marketmatch_truth_accountability import (
    ApprovalRecord, ApprovalStatus, TargetType,
    _valid_approval as _valid_truth_approval,
)
from src.marketmatch_work_orchestration import validate_work_id
from src.marketmatch_billing import validate_usage_id


OPERATIONS_CONTRACT_VERSION = "marketmatch-operations-reliability-v1"
OPERATIONS_POLICY_VERSION = "marketmatch-operations-policy-v1"
MAX_RECORDS = 2048
MAX_ATTEMPTS = 64
MAX_IDENTIFIER_BYTES = 160
MAX_CODE_BYTES = 128
MAX_STRING_CHARS = 512
MAX_MEASUREMENTS = 32
MAX_METRIC_ABS = Decimal("1000000000000000000")

# Closed vocabulary only; ADR 0001 remains the sole grant/evaluation kernel.
OPERATIONS_CAPABILITIES = frozenset({
    "operations.configuration.view", "operations.configuration.admin",
    "operations.feature.view", "operations.feature.admin",
    "operations.secret_reference.view", "operations.secret_reference.admin",
    "operations.job.request", "operations.job.cancel", "operations.job_status.view",
    "operations.notification.request", "operations.notification_status.view",
    "operations.health.view", "operations.metrics.view",
    "operations.backup_policy.admin", "operations.backup_metadata.view",
    "operations.backup.verify", "operations.restore.request", "approval.restore",
    "operations.restore.view", "operations.rollback.propose", "approval.rollback",
    "operations.rollback.view",
})

_CODE_RE = re.compile(r"[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*\Z", re.ASCII)
_SHA_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_HOST_RE = re.compile(r"(?:[a-z0-9-]+\.)+[a-z]{2,}\Z", re.ASCII | re.IGNORECASE)
_SECRET_TEXT_RE = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:api[_-]?key|authorization|cookie|password|passwd|"
    r"private[_-]?key|secret|session|token)\s*[:=]\s*\S+)"
)
_CREDENTIAL_VALUE_RE = re.compile(
    r"(?i)(?:sk-[a-z0-9_-]{8,}|gh[opusr]_[a-z0-9]{8,}|xox[baprs]-[a-z0-9-]{8,}|"
    r"AKIA[A-Z0-9]{12,}|eyJ[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,})"
)
_SENSITIVE_PARTS = frozenset({
    "address", "apikey", "authorization", "bearer", "certificate",
    "connection", "cookie", "credential", "email", "header",
    "hostname", "key", "margin", "password", "path", "phone", "price",
    "prompt", "secret", "session", "token", "url", "username",
})
_PREFIXES = {
    "configuration_definition": "cfgdef1:", "configuration_snapshot": "cfgsnap1:",
    "feature_flag": "flag1:", "feature_evaluation": "feval1:",
    "secret_reference": "secref1:", "job": "job1:", "job_request": "jreq1:",
    "job_run": "jrun1:", "job_attempt": "jatt1:",
    "notification_intent": "nint1:", "delivery_attempt": "datt1:",
    "health_check": "hchk1:", "operational_signal": "osig1:",
    "backup_policy": "bpol1:", "backup_record": "brec1:",
    "backup_verification": "bver1:", "restore_plan": "rplan1:",
    "restore_attempt": "ratt1:", "rollback_point": "rbp1:",
    "operations_audit": "oaudit1:",
}


class OperationsErrorCode(str, Enum):
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    INVALID_VERSION = "INVALID_VERSION"
    INVALID_RECORD = "INVALID_RECORD"
    INVALID_SCOPE = "INVALID_SCOPE"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    INVALID_AUTHORITY = "INVALID_AUTHORITY"
    INVALID_APPROVAL = "INVALID_APPROVAL"
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
    CONFIGURATION_CONFLICT = "CONFIGURATION_CONFLICT"
    SECRET_VALUE_REJECTED = "SECRET_VALUE_REJECTED"
    FEATURE_CONFLICT = "FEATURE_CONFLICT"
    INVALID_FEATURE = "INVALID_FEATURE"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    INVALID_RETRY_POLICY = "INVALID_RETRY_POLICY"
    RETRY_NOT_ALLOWED = "RETRY_NOT_ALLOWED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    DUPLICATE_IDENTIFIER = "DUPLICATE_IDENTIFIER"
    INVALID_NOTIFICATION = "INVALID_NOTIFICATION"
    INVALID_HEALTH = "INVALID_HEALTH"
    INVALID_SIGNAL = "INVALID_SIGNAL"
    INVALID_BACKUP = "INVALID_BACKUP"
    INVALID_VERIFICATION = "INVALID_VERIFICATION"
    BACKUP_NOT_VERIFIED = "BACKUP_NOT_VERIFIED"
    INVALID_RESTORE = "INVALID_RESTORE"
    INVALID_ROLLBACK = "INVALID_ROLLBACK"
    TENANT_CONFLICT = "TENANT_CONFLICT"
    POLICY_CONFLICT = "POLICY_CONFLICT"
    SOURCE_NOT_AUTHORIZED = "SOURCE_NOT_AUTHORIZED"
    INVALID_PROJECTION = "INVALID_PROJECTION"
    INVALID_AUDIT = "INVALID_AUDIT"
    COLLECTION_LIMIT_EXCEEDED = "COLLECTION_LIMIT_EXCEEDED"
    UNSUPPORTED_ADAPTER = "UNSUPPORTED_ADAPTER"


class OperationsContractError(ValueError):
    """A stable-code exception which never includes rejected input."""

    def __init__(self, code: OperationsErrorCode):
        self.code = code if type(code) is OperationsErrorCode else OperationsErrorCode.INVALID_RECORD
        super().__init__(self.code.value)


def _fail(code: OperationsErrorCode) -> NoReturn:
    raise OperationsContractError(code) from None


def _parts(value: str) -> frozenset[str]:
    return frozenset(part for part in re.split(r"[._:-]+", value.lower()) if part)


def _code(value: object, error: OperationsErrorCode = OperationsErrorCode.INVALID_RECORD,
          *, sensitive: bool = True, maximum: int = MAX_CODE_BYTES) -> str:
    if type(value) is not str or not value or _CODE_RE.fullmatch(value) is None:
        _fail(error)
    try:
        if len(value.encode("ascii")) > maximum:
            _fail(error)
    except UnicodeError:
        _fail(error)
    if sensitive and (_parts(value) & _SENSITIVE_PARTS):
        _fail(error)
    if _CREDENTIAL_VALUE_RE.search(value):
        _fail(error)
    return value


def validate_operations_id(kind: str, value: object) -> str:
    prefix = _PREFIXES.get(kind)
    if prefix is None:
        _fail(OperationsErrorCode.INVALID_IDENTIFIER)
    _code(value, OperationsErrorCode.INVALID_IDENTIFIER, maximum=MAX_IDENTIFIER_BYTES)
    opaque = value[len(prefix):] if type(value) is str and value.startswith(prefix) else ""
    if (not opaque or value.count(":") < 2 or not any("a" <= c <= "z" for c in opaque)
            or _SHA_RE.fullmatch(opaque) is not None or _HOST_RE.fullmatch(opaque) is not None):
        _fail(OperationsErrorCode.INVALID_IDENTIFIER)
    return value


def _validator(kind: str):
    return lambda value: validate_operations_id(kind, value)


validate_configuration_definition_id = _validator("configuration_definition")
validate_configuration_snapshot_id = _validator("configuration_snapshot")
validate_feature_flag_id = _validator("feature_flag")
validate_feature_evaluation_id = _validator("feature_evaluation")
validate_secret_reference_id = _validator("secret_reference")
validate_job_id = _validator("job")
validate_job_request_id = _validator("job_request")
validate_job_run_id = _validator("job_run")
validate_job_attempt_id = _validator("job_attempt")
validate_notification_intent_id = _validator("notification_intent")
validate_delivery_attempt_id = _validator("delivery_attempt")
validate_health_check_id = _validator("health_check")
validate_operational_signal_id = _validator("operational_signal")
validate_backup_policy_id = _validator("backup_policy")
validate_backup_record_id = _validator("backup_record")
validate_backup_verification_id = _validator("backup_verification")
validate_restore_plan_id = _validator("restore_plan")
validate_restore_attempt_id = _validator("restore_attempt")
validate_rollback_point_id = _validator("rollback_point")
validate_operations_audit_id = _validator("operations_audit")


def _utc(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(OperationsErrorCode.INVALID_TIMESTAMP)
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        _fail(OperationsErrorCode.INVALID_TIMESTAMP)


def _duration(value: object, *, allow_zero: bool = False,
              maximum: timedelta = timedelta(days=3650)) -> timedelta:
    if type(value) is not timedelta or value < timedelta(0) or (not allow_zero and value == timedelta(0)):
        _fail(OperationsErrorCode.INVALID_RECORD)
    if value > maximum:
        _fail(OperationsErrorCode.INVALID_RECORD)
    return value


def _version(value: object) -> str:
    if value != OPERATIONS_CONTRACT_VERSION:
        _fail(OperationsErrorCode.INVALID_VERSION)
    return value


def _safe_text(value: object, *, maximum: int = MAX_STRING_CHARS) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > maximum:
        _fail(OperationsErrorCode.INVALID_RECORD)
    if (any(ord(c) < 32 or ord(c) == 127 for c in value)
            or _SECRET_TEXT_RE.search(value) or _CREDENTIAL_VALUE_RE.search(value)):
        _fail(OperationsErrorCode.SECRET_VALUE_REJECTED)
    return value


def _valid_tenant_context(value: object) -> bool:
    try:
        return (type(value) is TenantContext and value._integrity == value._tuple()
                and value.scope.organization_id == value.organization_id
                and None not in (value.scope.product_id, value.scope.workspace_id,
                                 value.scope.project_id, value.scope.owner_party_id)
                and value.scope.global_scope is False)
    except Exception:
        return False


def _context_tuple(value: TenantContext) -> tuple[object, ...]:
    if not _valid_tenant_context(value):
        _fail(OperationsErrorCode.INVALID_SCOPE)
    return value._tuple()


@dataclass(frozen=True, slots=True)
class OperationsScope:
    tenant_context: TenantContext | None
    environment_code: str
    service_code: str
    global_scope: bool = False
    _integrity: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _code(self.environment_code)
        _code(self.service_code)
        if type(self.global_scope) is not bool:
            _fail(OperationsErrorCode.INVALID_SCOPE)
        if self.global_scope:
            if self.tenant_context is not None:
                _fail(OperationsErrorCode.INVALID_SCOPE)
            context = None
        else:
            context = _context_tuple(self.tenant_context)
        object.__setattr__(self, "_integrity", (context, self.environment_code,
                                               self.service_code, self.global_scope))


def _scope_tuple(value: OperationsScope) -> tuple[object, ...]:
    try:
        expected = (None if value.global_scope else _context_tuple(value.tenant_context),
                    value.environment_code, value.service_code, value.global_scope)
        if type(value) is not OperationsScope or value._integrity != expected:
            _fail(OperationsErrorCode.INVALID_SCOPE)
        return expected
    except OperationsContractError:
        raise
    except Exception:
        _fail(OperationsErrorCode.INVALID_SCOPE)


def _scope_material(scope: OperationsScope) -> str:
    """Language-neutral canonical bytes input for deterministic rollout."""
    _scope_tuple(scope)
    if scope.global_scope:
        parts = ("global", scope.environment_code, scope.service_code)
    else:
        context = scope.tenant_context
        parts = ("tenant", context.tenant_id, context.organization_id,
                 context.product_context_id, context.workspace_context_id,
                 context.project_context_id, context.scope.owner_party_id,
                 scope.environment_code, scope.service_code,
                 context.policy_id, context.policy_version)
    return "\x1e".join(parts)


def _authority_scope(scope: OperationsScope, resource_id: str) -> AuthorityScope:
    _scope_tuple(scope)
    if scope.global_scope:
        return AuthorityScope(resource_id=resource_id, global_scope=True)
    context = scope.tenant_context
    return AuthorityScope(context.organization_id, context.scope.product_id,
                          context.scope.workspace_id, context.scope.project_id,
                          resource_id, context.scope.owner_party_id, False)


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
        values = (self.principal_ref, self.capability_code, self.resource_type,
                  self.resource_id, self.policy_id, self.policy_version)
        for value in values:
            _code(value, OperationsErrorCode.INVALID_AUTHORITY, sensitive=False)
        object.__setattr__(self, "_integrity", values)


def _authority(decision: object, *, resource_type: str, resource_id: str,
               scope: OperationsScope, capability: str, actor_ref: str,
               policy_id: str, policy_version: str) -> AuthorityProvenance:
    try:
        project_authorized_fields({}, decision, behavior=ProjectionBehavior.OMIT)
    except Exception:
        _fail(OperationsErrorCode.INVALID_AUTHORITY)
    if (type(decision) is not AuthorizationDecision or decision.allowed is not True
            or decision.resource_type != resource_type or decision.resource_id != resource_id
            or decision.effective_scope != _authority_scope(scope, resource_id)
            or decision.action_code != capability or decision.principal_ref != actor_ref
            or decision.policy_id != policy_id or decision.policy_version != policy_version):
        _fail(OperationsErrorCode.INVALID_AUTHORITY)
    return AuthorityProvenance(actor_ref, capability, resource_type, resource_id,
                               policy_id, policy_version)


class _FactoryRecord:
    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs
        _fail(OperationsErrorCode.INVALID_RECORD)


def _integrity_value(item: object) -> object:
    if type(item) is OperationsScope:
        return _scope_tuple(item)
    if type(item) is AuthorityProvenance:
        return item._integrity
    if type(item) is RetryPolicy:
        if not _valid_retry_policy(item): _fail(OperationsErrorCode.INVALID_RECORD)
        return item._integrity
    if type(item) is SafeMeasurement:
        return (item.metric_code, item.value, item.unit_code)
    if type(item) is SecretReference:
        if not _valid(item, SecretReference): _fail(OperationsErrorCode.INVALID_RECORD)
        return item._integrity
    if type(item) is tuple:
        return tuple(_integrity_value(value) for value in item)
    return item


def _record_tuple(value: object, cls: type) -> tuple[object, ...]:
    result: list[object] = []
    for name in cls.__dataclass_fields__:
        if name == "_integrity":
            continue
        item = getattr(value, name)
        result.append(_integrity_value(item))
    return tuple(result)


def _new(cls: type, values: Mapping[str, object]):
    item = object.__new__(cls)
    for name in cls.__dataclass_fields__:
        if name != "_integrity":
            object.__setattr__(item, name, values[name])
    object.__setattr__(item, "_integrity", _record_tuple(item, cls))
    return item


def _valid(value: object, cls: type) -> bool:
    try:
        return type(value) is cls and value._integrity == _record_tuple(value, cls)
    except Exception:
        return False


class ConfigurationValueType(str, Enum):
    BOOLEAN = "BOOLEAN"
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"
    STRING = "STRING"
    ENUM = "ENUM"
    DURATION = "DURATION"
    BYTE_SIZE = "BYTE_SIZE"
    SECRET_REFERENCE = "SECRET_REFERENCE"


class ConfigurationScopeType(str, Enum):
    TENANT = "TENANT"
    SERVICE = "SERVICE"
    GLOBAL = "GLOBAL"


class SecretStatus(str, Enum):
    ACTIVE = "ACTIVE"
    ROTATING = "ROTATING"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True, init=False)
class ConfigurationDefinition(_FactoryRecord):
    definition_id: str
    key: str
    value_type: ConfigurationValueType
    scope_type: ConfigurationScopeType
    required: bool
    has_default: bool
    secret_bearing: bool
    validation_code: str
    allowed_values: tuple[str, ...]
    effective_at: datetime
    expires_at: datetime | None
    policy_id: str
    policy_version: str
    classification: ResourceClassification
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_configuration_definition(*, definition_id: str, key: str,
                                    value_type: ConfigurationValueType,
                                    scope_type: ConfigurationScopeType, required: bool,
                                    has_default: bool, validation_code: str,
                                    effective_at: datetime, policy_id: str,
                                    policy_version: str,
                                    classification: ResourceClassification,
                                    allowed_values: tuple[str, ...] = (),
                                    expires_at: datetime | None = None,
                                    contract_version: str = OPERATIONS_CONTRACT_VERSION) -> ConfigurationDefinition:
    validate_configuration_definition_id(definition_id); _code(key); _code(validation_code, sensitive=False)
    if type(value_type) is not ConfigurationValueType or type(scope_type) is not ConfigurationScopeType:
        _fail(OperationsErrorCode.INVALID_CONFIGURATION)
    if type(required) is not bool or type(has_default) is not bool:
        _fail(OperationsErrorCode.INVALID_CONFIGURATION)
    if type(allowed_values) is not tuple or any(type(v) is not str or _code(v) != v for v in allowed_values):
        _fail(OperationsErrorCode.INVALID_CONFIGURATION)
    if value_type is ConfigurationValueType.ENUM and not allowed_values:
        _fail(OperationsErrorCode.INVALID_CONFIGURATION)
    if value_type is not ConfigurationValueType.ENUM and allowed_values:
        _fail(OperationsErrorCode.INVALID_CONFIGURATION)
    start = _utc(effective_at); end = None if expires_at is None else _utc(expires_at)
    if end is not None and end <= start:
        _fail(OperationsErrorCode.INVALID_CONFIGURATION)
    if type(classification) is not ResourceClassification:
        _fail(OperationsErrorCode.INVALID_CONFIGURATION)
    _code(policy_id); _code(policy_version, sensitive=False); _version(contract_version)
    return _new(ConfigurationDefinition, locals() | {
        "secret_bearing": value_type is ConfigurationValueType.SECRET_REFERENCE,
        "effective_at": start, "expires_at": end,
    })


@dataclass(frozen=True, slots=True, init=False)
class SecretReference(_FactoryRecord):
    secret_reference_id: str
    provider_code: str
    purpose_code: str
    scope: OperationsScope
    rotation_reference: str
    status: SecretStatus
    created_at: datetime
    expires_at: datetime | None
    policy_id: str
    policy_version: str
    classification: ResourceClassification
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_secret_reference(*, secret_reference_id: str, provider_code: str,
                            purpose_code: str, scope: OperationsScope,
                            rotation_reference: str, status: SecretStatus,
                            created_at: datetime, policy_id: str, policy_version: str,
                            classification: ResourceClassification,
                            expires_at: datetime | None = None,
                            contract_version: str = OPERATIONS_CONTRACT_VERSION) -> SecretReference:
    validate_secret_reference_id(secret_reference_id)
    for value in (provider_code, purpose_code, rotation_reference): _code(value)
    _scope_tuple(scope)
    if type(status) is not SecretStatus or type(classification) is not ResourceClassification:
        _fail(OperationsErrorCode.INVALID_RECORD)
    created = _utc(created_at); expiry = None if expires_at is None else _utc(expires_at)
    if expiry is not None and expiry <= created:
        _fail(OperationsErrorCode.INVALID_RECORD)
    _code(policy_id); _code(policy_version, sensitive=False); _version(contract_version)
    return _new(SecretReference, locals() | {"created_at": created, "expires_at": expiry})


def secret_reference_is_active(reference: SecretReference, *, as_of: datetime,
                               scope: OperationsScope) -> bool:
    if not _valid(reference, SecretReference) or _scope_tuple(reference.scope) != _scope_tuple(scope):
        return False
    now = _utc(as_of)
    return (reference.status in {SecretStatus.ACTIVE, SecretStatus.ROTATING}
            and reference.created_at <= now
            and (reference.expires_at is None or reference.expires_at > now))


@dataclass(frozen=True, slots=True, init=False)
class ConfigurationSnapshot(_FactoryRecord):
    snapshot_id: str
    definition_id: str
    key: str
    scope: OperationsScope
    value_type: ConfigurationValueType
    value: bool | int | Decimal | str | timedelta | SecretReference
    effective_at: datetime
    expires_at: datetime | None
    source_code: str
    policy_id: str
    policy_version: str
    classification: ResourceClassification
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def _configuration_value(definition: ConfigurationDefinition, value: object,
                         scope: OperationsScope, as_of: datetime) -> object:
    kind = definition.value_type
    if kind is ConfigurationValueType.BOOLEAN:
        valid = type(value) is bool
    elif kind is ConfigurationValueType.INTEGER:
        valid = type(value) is int and -(2 ** 63) <= value < 2 ** 63
    elif kind is ConfigurationValueType.DECIMAL:
        valid = type(value) is Decimal and value.is_finite() and abs(value) <= MAX_METRIC_ABS
    elif kind is ConfigurationValueType.STRING:
        valid = type(value) is str
        if valid: _safe_text(value)
    elif kind is ConfigurationValueType.ENUM:
        valid = type(value) is str and value in definition.allowed_values
    elif kind is ConfigurationValueType.DURATION:
        valid = type(value) is timedelta
        if valid: _duration(value, allow_zero=True)
    elif kind is ConfigurationValueType.BYTE_SIZE:
        valid = type(value) is int and not isinstance(value, bool) and 0 <= value <= 2 ** 63 - 1
    elif kind is ConfigurationValueType.SECRET_REFERENCE:
        valid = type(value) is SecretReference and _valid(value, SecretReference)
        if valid:
            valid = secret_reference_is_active(value, as_of=as_of, scope=scope)
    else:
        valid = False
    if not valid:
        _fail(OperationsErrorCode.SECRET_VALUE_REJECTED if definition.secret_bearing
              else OperationsErrorCode.INVALID_CONFIGURATION)
    return value


def create_configuration_snapshot(*, snapshot_id: str,
                                  definition: ConfigurationDefinition,
                                  scope: OperationsScope, value: object,
                                  effective_at: datetime, source_code: str,
                                  expires_at: datetime | None = None,
                                  contract_version: str = OPERATIONS_CONTRACT_VERSION) -> ConfigurationSnapshot:
    validate_configuration_snapshot_id(snapshot_id)
    if not _valid(definition, ConfigurationDefinition):
        _fail(OperationsErrorCode.INVALID_CONFIGURATION)
    _scope_tuple(scope)
    expected_global = definition.scope_type is ConfigurationScopeType.GLOBAL
    if scope.global_scope is not expected_global:
        _fail(OperationsErrorCode.INVALID_SCOPE)
    start = _utc(effective_at); end = None if expires_at is None else _utc(expires_at)
    if end is not None and end <= start:
        _fail(OperationsErrorCode.INVALID_CONFIGURATION)
    if (start < definition.effective_at
            or (definition.expires_at is not None
                and (start >= definition.expires_at
                     or (end is not None and end > definition.expires_at)))):
        _fail(OperationsErrorCode.INVALID_CONFIGURATION)
    typed = _configuration_value(definition, value, scope, start)
    _code(source_code); _version(contract_version)
    return _new(ConfigurationSnapshot, {
        "snapshot_id": snapshot_id, "definition_id": definition.definition_id,
        "key": definition.key, "scope": scope, "value_type": definition.value_type,
        "value": typed, "effective_at": start, "expires_at": end,
        "source_code": source_code, "policy_id": definition.policy_id,
        "policy_version": definition.policy_version,
        "classification": definition.classification, "contract_version": contract_version,
    })


def evaluate_configuration(definition: ConfigurationDefinition,
                           snapshots: Sequence[ConfigurationSnapshot], *,
                           scope: OperationsScope, as_of: datetime) -> ConfigurationSnapshot | None:
    if not _valid(definition, ConfigurationDefinition) or type(snapshots) not in (tuple, list):
        _fail(OperationsErrorCode.INVALID_CONFIGURATION)
    if len(snapshots) > MAX_RECORDS: _fail(OperationsErrorCode.COLLECTION_LIMIT_EXCEEDED)
    now = _utc(as_of); expected = _scope_tuple(scope); active = []
    if now < definition.effective_at or (definition.expires_at is not None
                                         and definition.expires_at <= now):
        return None
    for item in snapshots:
        if not _valid(item, ConfigurationSnapshot): _fail(OperationsErrorCode.INVALID_CONFIGURATION)
        if item.definition_id != definition.definition_id or item.key != definition.key:
            continue
        if (_scope_tuple(item.scope) == expected and item.policy_id == definition.policy_id
                and item.policy_version == definition.policy_version
                and item.effective_at <= now and (item.expires_at is None or item.expires_at > now)):
            active.append(item)
    if len(active) > 1: _fail(OperationsErrorCode.CONFIGURATION_CONFLICT)
    return active[0] if active else None


class FeatureStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"
    INVALIDATED = "INVALIDATED"


class FeatureEvaluationMode(str, Enum):
    OFF = "OFF"
    ON = "ON"
    EXACT_SCOPE = "EXACT_SCOPE"
    PERCENTAGE = "PERCENTAGE"
    ALLOWLIST = "ALLOWLIST"


@dataclass(frozen=True, slots=True, init=False)
class FeatureFlagDefinition(_FactoryRecord):
    feature_flag_id: str
    flag_key: str
    scope: OperationsScope
    status: FeatureStatus
    mode: FeatureEvaluationMode
    default_enabled: bool
    percentage_basis_points: int
    allowlist: tuple[str, ...]
    effective_at: datetime
    expires_at: datetime | None
    supersedes_flag_id: str | None
    authority_provenance: AuthorityProvenance
    policy_id: str
    policy_version: str
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_feature_flag(*, feature_flag_id: str, flag_key: str, scope: OperationsScope,
                        status: FeatureStatus, mode: FeatureEvaluationMode,
                        default_enabled: bool, effective_at: datetime,
                        actor_ref: str, authority_decision: AuthorizationDecision,
                        policy_id: str, policy_version: str,
                        percentage_basis_points: int = 0,
                        allowlist: tuple[str, ...] = (), expires_at: datetime | None = None,
                        supersedes_flag_id: str | None = None,
                        contract_version: str = OPERATIONS_CONTRACT_VERSION) -> FeatureFlagDefinition:
    validate_feature_flag_id(feature_flag_id); _code(flag_key); _scope_tuple(scope)
    if type(status) is not FeatureStatus or type(mode) is not FeatureEvaluationMode or type(default_enabled) is not bool:
        _fail(OperationsErrorCode.INVALID_FEATURE)
    if type(percentage_basis_points) is not int or isinstance(percentage_basis_points, bool) or not 0 <= percentage_basis_points <= 10000:
        _fail(OperationsErrorCode.INVALID_FEATURE)
    if type(allowlist) is not tuple or any(_code(v) != v for v in allowlist) or len(set(allowlist)) != len(allowlist):
        _fail(OperationsErrorCode.INVALID_FEATURE)
    if mode is FeatureEvaluationMode.PERCENTAGE and not 0 <= percentage_basis_points <= 10000:
        _fail(OperationsErrorCode.INVALID_FEATURE)
    if mode is not FeatureEvaluationMode.PERCENTAGE and percentage_basis_points != 0:
        _fail(OperationsErrorCode.INVALID_FEATURE)
    if mode is FeatureEvaluationMode.ALLOWLIST and not allowlist:
        _fail(OperationsErrorCode.INVALID_FEATURE)
    if mode is not FeatureEvaluationMode.ALLOWLIST and allowlist:
        _fail(OperationsErrorCode.INVALID_FEATURE)
    start = _utc(effective_at); end = None if expires_at is None else _utc(expires_at)
    if end is not None and end <= start: _fail(OperationsErrorCode.INVALID_FEATURE)
    if supersedes_flag_id is not None:
        validate_feature_flag_id(supersedes_flag_id)
        if supersedes_flag_id == feature_flag_id: _fail(OperationsErrorCode.INVALID_FEATURE)
    _code(policy_id); _code(policy_version, sensitive=False); _version(contract_version)
    auth = _authority(authority_decision, resource_type="operations_feature_flag",
                      resource_id=feature_flag_id, scope=scope,
                      capability="operations.feature.admin", actor_ref=actor_ref,
                      policy_id=policy_id, policy_version=policy_version)
    return _new(FeatureFlagDefinition, locals() | {"effective_at": start,
                "expires_at": end, "authority_provenance": auth})


@dataclass(frozen=True, slots=True, init=False)
class FeatureEvaluation(_FactoryRecord):
    evaluation_id: str
    feature_flag_id: str | None
    flag_key: str
    scope: OperationsScope
    tenant_id: str | None
    enabled: bool
    reason_code: str
    evaluated_at: datetime
    authority_granted: bool
    entitlement_granted: bool
    policy_id: str
    policy_version: str
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def evaluate_feature_flag(*, evaluation_id: str, flag_key: str,
                          flags: Sequence[FeatureFlagDefinition], scope: OperationsScope,
                          as_of: datetime, subject_ref: str | None = None) -> FeatureEvaluation:
    validate_feature_evaluation_id(evaluation_id); _code(flag_key); expected = _scope_tuple(scope)
    if type(flags) not in (list, tuple) or len(flags) > MAX_RECORDS:
        _fail(OperationsErrorCode.INVALID_FEATURE)
    now = _utc(as_of); candidates = []
    for item in flags:
        if not _valid(item, FeatureFlagDefinition): _fail(OperationsErrorCode.INVALID_FEATURE)
        if (item.flag_key == flag_key and _scope_tuple(item.scope) == expected
                and item.status is FeatureStatus.ACTIVE and item.effective_at <= now
                and (item.expires_at is None or item.expires_at > now)):
            candidates.append(item)
    if len(candidates) > 1: _fail(OperationsErrorCode.FEATURE_CONFLICT)
    tenant_id = None if scope.global_scope else scope.tenant_context.tenant_id
    if not candidates:
        values = (evaluation_id, None, flag_key, scope, tenant_id, False, "no_active_flag", now,
                  False, False, OPERATIONS_POLICY_VERSION, "v1", OPERATIONS_CONTRACT_VERSION)
    else:
        flag = candidates[0]
        enabled = flag.default_enabled; reason = "default"
        if flag.mode is FeatureEvaluationMode.OFF: enabled, reason = False, "mode_off"
        elif flag.mode is FeatureEvaluationMode.ON: enabled, reason = True, "mode_on"
        elif flag.mode is FeatureEvaluationMode.EXACT_SCOPE: enabled, reason = True, "exact_scope"
        elif flag.mode is FeatureEvaluationMode.ALLOWLIST:
            if subject_ref is None: enabled, reason = False, "missing_subject"
            else:
                _code(subject_ref); enabled = subject_ref in flag.allowlist
                reason = "allowlist_match" if enabled else "allowlist_miss"
        elif flag.mode is FeatureEvaluationMode.PERCENTAGE:
            if subject_ref is None: enabled, reason = False, "missing_subject"
            else:
                _code(subject_ref)
                material = "\x1f".join((flag.flag_key, _scope_material(scope),
                                         subject_ref, flag.policy_version))
                bucket = int.from_bytes(hashlib.sha256(material.encode("ascii")).digest()[:8], "big") % 10000
                enabled = bucket < flag.percentage_basis_points
                reason = "percentage_match" if enabled else "percentage_miss"
        values = (evaluation_id, flag.feature_flag_id, flag_key, scope, tenant_id, enabled, reason, now,
                  False, False, flag.policy_id, flag.policy_version, OPERATIONS_CONTRACT_VERSION)
    return _new(FeatureEvaluation, dict(zip(
        ("evaluation_id", "feature_flag_id", "flag_key", "scope", "tenant_id", "enabled",
         "reason_code", "evaluated_at", "authority_granted", "entitlement_granted",
         "policy_id", "policy_version", "contract_version"), values, strict=True)))


class BackoffMethod(str, Enum):
    FIXED = "FIXED"
    EXPONENTIAL = "EXPONENTIAL"


class JitterPolicy(str, Enum):
    NONE = "NONE"


class JobDefinitionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


class JobLifecycleStatus(str, Enum):
    REQUESTED = "REQUESTED"
    ACCEPTED = "ACCEPTED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    ABANDONED = "ABANDONED"
    DEAD_LETTERED = "DEAD_LETTERED"
    INVALIDATED = "INVALIDATED"


class JobAttemptOutcome(str, Enum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


class CancellationPolicy(str, Enum):
    COOPERATIVE = "COOPERATIVE"
    NOT_SUPPORTED = "NOT_SUPPORTED"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    maximum_attempts: int
    initial_delay: timedelta
    maximum_delay: timedelta
    backoff_method: BackoffMethod
    retryable_reason_codes: frozenset[str]
    maximum_elapsed: timedelta | None = None
    jitter_policy: JitterPolicy = JitterPolicy.NONE
    _integrity: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (type(self.maximum_attempts) is not int or isinstance(self.maximum_attempts, bool)
                or not 1 <= self.maximum_attempts <= MAX_ATTEMPTS):
            _fail(OperationsErrorCode.INVALID_RETRY_POLICY)
        _duration(self.initial_delay, allow_zero=True)
        _duration(self.maximum_delay, allow_zero=True)
        if self.initial_delay > self.maximum_delay:
            _fail(OperationsErrorCode.INVALID_RETRY_POLICY)
        if type(self.backoff_method) is not BackoffMethod or type(self.jitter_policy) is not JitterPolicy:
            _fail(OperationsErrorCode.INVALID_RETRY_POLICY)
        if (type(self.retryable_reason_codes) is not frozenset
                or any(_code(v, sensitive=False) != v for v in self.retryable_reason_codes)):
            _fail(OperationsErrorCode.INVALID_RETRY_POLICY)
        if self.maximum_attempts > 1 and not self.retryable_reason_codes:
            _fail(OperationsErrorCode.INVALID_RETRY_POLICY)
        if self.maximum_elapsed is not None: _duration(self.maximum_elapsed)
        object.__setattr__(self, "_integrity", (
            self.maximum_attempts, self.initial_delay, self.maximum_delay,
            self.backoff_method, self.retryable_reason_codes, self.maximum_elapsed,
            self.jitter_policy,
        ))


def _valid_retry_policy(value: object) -> bool:
    try:
        return type(value) is RetryPolicy and value._integrity == (
            value.maximum_attempts, value.initial_delay, value.maximum_delay,
            value.backoff_method, value.retryable_reason_codes, value.maximum_elapsed,
            value.jitter_policy,
        )
    except Exception:
        return False


@dataclass(frozen=True, slots=True, init=False)
class JobDefinition(_FactoryRecord):
    job_id: str
    job_type: str
    version: str
    required_scope_type: ConfigurationScopeType
    timeout: timedelta
    retry_policy: RetryPolicy
    idempotency_principal_bound: bool
    cancellation_policy: CancellationPolicy
    status: JobDefinitionStatus
    policy_id: str
    policy_version: str
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_job_definition(*, job_id: str, job_type: str, version: str,
                          required_scope_type: ConfigurationScopeType,
                          timeout: timedelta, retry_policy: RetryPolicy,
                          idempotency_principal_bound: bool,
                          cancellation_policy: CancellationPolicy,
                          status: JobDefinitionStatus, policy_id: str,
                          policy_version: str,
                          contract_version: str = OPERATIONS_CONTRACT_VERSION) -> JobDefinition:
    validate_job_id(job_id); _code(job_type); _code(version, sensitive=False)
    if type(required_scope_type) is not ConfigurationScopeType:
        _fail(OperationsErrorCode.INVALID_RECORD)
    _duration(timeout)
    if not _valid_retry_policy(retry_policy) or type(idempotency_principal_bound) is not bool:
        _fail(OperationsErrorCode.INVALID_RETRY_POLICY)
    if type(cancellation_policy) is not CancellationPolicy or type(status) is not JobDefinitionStatus:
        _fail(OperationsErrorCode.INVALID_RECORD)
    _code(policy_id); _code(policy_version, sensitive=False); _version(contract_version)
    return _new(JobDefinition, locals())


@dataclass(frozen=True, slots=True, init=False)
class JobRequest(_FactoryRecord):
    request_id: str
    job_id: str
    scope: OperationsScope
    requesting_principal_ref: str
    work_item_id: str | None
    safe_input_ref: str
    requested_at: datetime
    idempotency_key: str
    authority_provenance: AuthorityProvenance
    policy_id: str
    policy_version: str
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_job_request(*, request_id: str, definition: JobDefinition,
                       scope: OperationsScope, requesting_principal_ref: str,
                       safe_input_ref: str, requested_at: datetime,
                       idempotency_key: str, authority_decision: AuthorizationDecision,
                       work_item_id: str | None = None,
                       contract_version: str = OPERATIONS_CONTRACT_VERSION) -> JobRequest:
    validate_job_request_id(request_id)
    if not _valid(definition, JobDefinition) or definition.status is not JobDefinitionStatus.ACTIVE:
        _fail(OperationsErrorCode.INVALID_RECORD)
    actual = _scope_tuple(scope)
    if scope.global_scope is not (definition.required_scope_type is ConfigurationScopeType.GLOBAL):
        _fail(OperationsErrorCode.INVALID_SCOPE)
    _code(requesting_principal_ref); _code(safe_input_ref); _code(idempotency_key)
    if work_item_id is not None:
        try: validate_work_id(work_item_id)
        except Exception: _fail(OperationsErrorCode.INVALID_RECORD)
    when = _utc(requested_at); _version(contract_version)
    auth = _authority(authority_decision, resource_type="operations_job_request",
                      resource_id=request_id, scope=scope, capability="operations.job.request",
                      actor_ref=requesting_principal_ref, policy_id=definition.policy_id,
                      policy_version=definition.policy_version)
    del actual
    return _new(JobRequest, {
        "request_id": request_id, "job_id": definition.job_id, "scope": scope,
        "requesting_principal_ref": requesting_principal_ref,
        "work_item_id": work_item_id, "safe_input_ref": safe_input_ref,
        "requested_at": when, "idempotency_key": idempotency_key,
        "authority_provenance": auth, "policy_id": definition.policy_id,
        "policy_version": definition.policy_version, "contract_version": contract_version,
    })


@dataclass(frozen=True, slots=True, init=False)
class IdempotencyEvaluation(_FactoryRecord):
    request_id: str
    job_id: str
    idempotency_key: str
    accepted: bool
    duplicate: bool
    conflicting: bool
    existing_request_id: str | None
    reason_code: str
    authority_granted: bool
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def evaluate_idempotency(request: JobRequest, history: Sequence[JobRequest],
                         definition: JobDefinition) -> IdempotencyEvaluation:
    if (not _valid(request, JobRequest) or not _valid(definition, JobDefinition)
            or request.job_id != definition.job_id or type(history) not in (list, tuple)):
        _fail(OperationsErrorCode.IDEMPOTENCY_CONFLICT)
    if len(history) > MAX_RECORDS: _fail(OperationsErrorCode.COLLECTION_LIMIT_EXCEEDED)
    matches = []
    for prior in history:
        if not _valid(prior, JobRequest): _fail(OperationsErrorCode.IDEMPOTENCY_CONFLICT)
        same_scope = _scope_tuple(prior.scope) == _scope_tuple(request.scope)
        same_principal = (not definition.idempotency_principal_bound
                          or prior.requesting_principal_ref == request.requesting_principal_ref)
        if same_scope and same_principal and prior.idempotency_key == request.idempotency_key:
            matches.append(prior)
    if not matches:
        values = (request.request_id, request.job_id, request.idempotency_key,
                  True, False, False, None, "accepted", False, OPERATIONS_CONTRACT_VERSION)
    elif len(matches) != 1:
        values = (request.request_id, request.job_id, request.idempotency_key,
                  False, False, True, None, "conflicting_history", False, OPERATIONS_CONTRACT_VERSION)
    else:
        prior = matches[0]
        compatible = (prior.job_id == request.job_id and prior.safe_input_ref == request.safe_input_ref
                      and prior.work_item_id == request.work_item_id
                      and prior.policy_id == request.policy_id
                      and prior.policy_version == request.policy_version)
        values = ((request.request_id, request.job_id, request.idempotency_key,
                   False, True, False, prior.request_id, "duplicate", False,
                   OPERATIONS_CONTRACT_VERSION) if compatible else
                  (request.request_id, request.job_id, request.idempotency_key,
                   False, False, True, prior.request_id, "collision", False,
                   OPERATIONS_CONTRACT_VERSION))
    return _new(IdempotencyEvaluation, dict(zip(
        ("request_id", "job_id", "idempotency_key", "accepted", "duplicate",
         "conflicting", "existing_request_id", "reason_code",
         "authority_granted", "contract_version"), values, strict=True)))


@dataclass(frozen=True, slots=True, init=False)
class JobRun(_FactoryRecord):
    run_id: str
    request_id: str
    scope: OperationsScope
    status: JobLifecycleStatus
    accepted_at: datetime
    started_at: datetime | None
    terminal_at: datetime | None
    current_attempt_id: str | None
    cancellation_reference: str | None
    policy_id: str
    policy_version: str
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


_TERMINAL_JOB_STATUSES = frozenset({
    JobLifecycleStatus.SUCCEEDED, JobLifecycleStatus.FAILED,
    JobLifecycleStatus.TIMED_OUT, JobLifecycleStatus.CANCELLED,
    JobLifecycleStatus.ABANDONED, JobLifecycleStatus.DEAD_LETTERED,
    JobLifecycleStatus.INVALIDATED,
})
_JOB_TRANSITIONS = {
    JobLifecycleStatus.REQUESTED: frozenset({JobLifecycleStatus.ACCEPTED, JobLifecycleStatus.CANCELLED, JobLifecycleStatus.INVALIDATED}),
    JobLifecycleStatus.ACCEPTED: frozenset({JobLifecycleStatus.RUNNING, JobLifecycleStatus.CANCELLED, JobLifecycleStatus.ABANDONED}),
    JobLifecycleStatus.RUNNING: frozenset({JobLifecycleStatus.SUCCEEDED, JobLifecycleStatus.FAILED,
                                          JobLifecycleStatus.TIMED_OUT, JobLifecycleStatus.CANCELLED,
                                          JobLifecycleStatus.ABANDONED}),
    JobLifecycleStatus.FAILED: frozenset({JobLifecycleStatus.ACCEPTED, JobLifecycleStatus.DEAD_LETTERED}),
    JobLifecycleStatus.TIMED_OUT: frozenset({JobLifecycleStatus.ACCEPTED, JobLifecycleStatus.DEAD_LETTERED}),
    JobLifecycleStatus.ABANDONED: frozenset({JobLifecycleStatus.ACCEPTED, JobLifecycleStatus.DEAD_LETTERED}),
}


def create_job_run(*, run_id: str, request: JobRequest,
                   idempotency_evaluation: IdempotencyEvaluation,
                   existing_runs: Sequence[JobRun] = (),
                   status: JobLifecycleStatus = JobLifecycleStatus.REQUESTED,
                   accepted_at: datetime | None = None,
                   contract_version: str = OPERATIONS_CONTRACT_VERSION) -> JobRun:
    validate_job_run_id(run_id)
    if (not _valid(request, JobRequest)
            or not _valid(idempotency_evaluation, IdempotencyEvaluation)
            or idempotency_evaluation.request_id != request.request_id
            or idempotency_evaluation.job_id != request.job_id
            or idempotency_evaluation.idempotency_key != request.idempotency_key
            or idempotency_evaluation.accepted is not True
            or idempotency_evaluation.duplicate is not False
            or idempotency_evaluation.conflicting is not False
            or status not in {JobLifecycleStatus.REQUESTED,
                              JobLifecycleStatus.ACCEPTED}):
        _fail(OperationsErrorCode.INVALID_TRANSITION)
    if type(existing_runs) not in (list, tuple) or len(existing_runs) > MAX_RECORDS:
        _fail(OperationsErrorCode.INVALID_TRANSITION)
    for prior in existing_runs:
        if not _valid(prior, JobRun): _fail(OperationsErrorCode.INVALID_TRANSITION)
        if prior.request_id == request.request_id:
            _fail(OperationsErrorCode.IDEMPOTENCY_CONFLICT)
    accepted = request.requested_at if accepted_at is None else _utc(accepted_at)
    if accepted < request.requested_at: _fail(OperationsErrorCode.INVALID_TRANSITION)
    _version(contract_version)
    return _new(JobRun, {"run_id": run_id, "request_id": request.request_id,
        "scope": request.scope, "status": status, "accepted_at": accepted,
        "started_at": None, "terminal_at": None, "current_attempt_id": None,
        "cancellation_reference": None, "policy_id": request.policy_id,
        "policy_version": request.policy_version, "contract_version": contract_version})


def transition_job_run(run: JobRun, *, to_status: JobLifecycleStatus, at: datetime,
                       current_attempt_id: str | None = None,
                       cancellation_reference: str | None = None,
                       retry_evaluation: RetryEvaluation | None = None) -> JobRun:
    if not _valid(run, JobRun) or type(to_status) is not JobLifecycleStatus:
        _fail(OperationsErrorCode.INVALID_TRANSITION)
    if to_status not in _JOB_TRANSITIONS.get(run.status, frozenset()):
        _fail(OperationsErrorCode.INVALID_TRANSITION)
    retry_transition = (run.status in {JobLifecycleStatus.FAILED,
                                      JobLifecycleStatus.TIMED_OUT,
                                      JobLifecycleStatus.ABANDONED}
                        and to_status is JobLifecycleStatus.ACCEPTED)
    if retry_transition:
        if not _valid(retry_evaluation, RetryEvaluation) or retry_evaluation.allowed is not True:
            _fail(OperationsErrorCode.RETRY_NOT_ALLOWED)
    elif retry_evaluation is not None:
        _fail(OperationsErrorCode.INVALID_TRANSITION)
    when = _utc(at)
    if retry_transition and (retry_evaluation.retry_at is None or when < retry_evaluation.retry_at):
        _fail(OperationsErrorCode.RETRY_NOT_ALLOWED)
    if when < run.accepted_at or (run.started_at is not None and when < run.started_at):
        _fail(OperationsErrorCode.INVALID_TRANSITION)
    attempt = current_attempt_id if current_attempt_id is not None else run.current_attempt_id
    if attempt is not None: validate_job_attempt_id(attempt)
    cancellation = cancellation_reference if cancellation_reference is not None else run.cancellation_reference
    if to_status is JobLifecycleStatus.CANCELLED:
        if cancellation_reference is None: _fail(OperationsErrorCode.INVALID_TRANSITION)
        _code(cancellation_reference)
    elif cancellation_reference is not None:
        _fail(OperationsErrorCode.INVALID_TRANSITION)
    started = when if to_status is JobLifecycleStatus.RUNNING and run.started_at is None else run.started_at
    terminal = when if to_status in _TERMINAL_JOB_STATUSES else None
    return _new(JobRun, {"run_id": run.run_id, "request_id": run.request_id,
        "scope": run.scope, "status": to_status, "accepted_at": run.accepted_at,
        "started_at": started, "terminal_at": terminal, "current_attempt_id": attempt,
        "cancellation_reference": cancellation, "policy_id": run.policy_id,
        "policy_version": run.policy_version, "contract_version": run.contract_version})


@dataclass(frozen=True, slots=True, init=False)
class RetryEvaluation(_FactoryRecord):
    allowed: bool
    next_attempt_ordinal: int | None
    delay: timedelta | None
    retry_at: datetime | None
    reason_code: str
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def evaluate_retry(policy: RetryPolicy, *, attempt_ordinal: int, outcome: JobAttemptOutcome,
                   reason_code: str, finished_at: datetime, first_started_at: datetime,
                   run_status: JobLifecycleStatus,
                   deadline: datetime | None = None) -> RetryEvaluation:
    if not _valid_retry_policy(policy) or type(attempt_ordinal) is not int or isinstance(attempt_ordinal, bool):
        _fail(OperationsErrorCode.INVALID_RETRY_POLICY)
    if (not 1 <= attempt_ordinal <= MAX_ATTEMPTS or type(outcome) is not JobAttemptOutcome
            or type(run_status) is not JobLifecycleStatus):
        _fail(OperationsErrorCode.INVALID_RETRY_POLICY)
    _code(reason_code, sensitive=False); finished = _utc(finished_at); started = _utc(first_started_at)
    if finished < started: _fail(OperationsErrorCode.INVALID_RETRY_POLICY)
    absolute_deadline = None if deadline is None else _utc(deadline)
    if policy.maximum_elapsed is not None:
        try:
            elapsed_deadline = started + policy.maximum_elapsed
        except OverflowError:
            elapsed_deadline = datetime.max.replace(tzinfo=timezone.utc)
        absolute_deadline = elapsed_deadline if absolute_deadline is None else min(absolute_deadline, elapsed_deadline)
    allowed = True; denial = "retry_allowed"
    if run_status is JobLifecycleStatus.CANCELLED or outcome is JobAttemptOutcome.CANCELLED:
        allowed, denial = False, "cancelled"
    elif run_status in {JobLifecycleStatus.SUCCEEDED, JobLifecycleStatus.INVALIDATED,
                        JobLifecycleStatus.DEAD_LETTERED}:
        allowed, denial = False, "terminal_run"
    elif outcome not in {JobAttemptOutcome.FAILED, JobAttemptOutcome.TIMED_OUT}:
        allowed, denial = False, "terminal_outcome"
    elif reason_code not in policy.retryable_reason_codes:
        allowed, denial = False, "reason_not_retryable"
    elif attempt_ordinal >= policy.maximum_attempts:
        allowed, denial = False, "attempt_limit"
    factor = 1
    if policy.backoff_method is BackoffMethod.EXPONENTIAL:
        for _ in range(max(0, attempt_ordinal - 1)):
            factor = min(factor * 2, 2 ** 63 - 1)
    delay_seconds = min(policy.maximum_delay.total_seconds(),
                        policy.initial_delay.total_seconds() * factor)
    delay = timedelta(seconds=delay_seconds)
    try:
        retry_at = finished + delay
    except OverflowError:
        allowed, denial = False, "deadline_overflow"
        retry_at = None
    if allowed and absolute_deadline is not None and retry_at is not None and retry_at > absolute_deadline:
        allowed, denial = False, "deadline_exceeded"
    values = (allowed, attempt_ordinal + 1 if allowed else None, delay if allowed else None,
              retry_at if allowed else None, denial, OPERATIONS_CONTRACT_VERSION)
    return _new(RetryEvaluation, dict(zip(
        ("allowed", "next_attempt_ordinal", "delay", "retry_at", "reason_code",
         "contract_version"), values, strict=True)))


@dataclass(frozen=True, slots=True, init=False)
class JobAttempt(_FactoryRecord):
    attempt_id: str
    run_id: str
    attempt_ordinal: int
    started_at: datetime
    finished_at: datetime | None
    outcome: JobAttemptOutcome
    error_code: str | None
    retry_allowed: bool
    usage_event_id: str | None
    policy_id: str
    policy_version: str
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_job_attempt(*, attempt_id: str, run: JobRun, attempt_ordinal: int,
                       started_at: datetime, outcome: JobAttemptOutcome,
                       finished_at: datetime | None = None, error_code: str | None = None,
                       retry_evaluation: RetryEvaluation | None = None,
                       usage_event_id: str | None = None,
                       contract_version: str = OPERATIONS_CONTRACT_VERSION) -> JobAttempt:
    validate_job_attempt_id(attempt_id)
    if not _valid(run, JobRun) or type(attempt_ordinal) is not int or isinstance(attempt_ordinal, bool) or not 1 <= attempt_ordinal <= MAX_ATTEMPTS:
        _fail(OperationsErrorCode.INVALID_RECORD)
    if type(outcome) is not JobAttemptOutcome: _fail(OperationsErrorCode.INVALID_RECORD)
    start = _utc(started_at); finish = None if finished_at is None else _utc(finished_at)
    if outcome is JobAttemptOutcome.RUNNING:
        if finish is not None or error_code is not None or retry_evaluation is not None:
            _fail(OperationsErrorCode.INVALID_RECORD)
    else:
        if finish is None or finish < start: _fail(OperationsErrorCode.INVALID_RECORD)
        if outcome in {JobAttemptOutcome.FAILED, JobAttemptOutcome.TIMED_OUT}:
            if error_code is None: _fail(OperationsErrorCode.INVALID_RECORD)
            _code(error_code, sensitive=False)
        elif error_code is not None:
            _fail(OperationsErrorCode.INVALID_RECORD)
    retry_allowed = False
    if retry_evaluation is not None:
        if (not _valid(retry_evaluation, RetryEvaluation) or finish is None
                or retry_evaluation.next_attempt_ordinal != attempt_ordinal + 1):
            _fail(OperationsErrorCode.INVALID_RETRY_POLICY)
        retry_allowed = retry_evaluation.allowed
    if usage_event_id is not None:
        try: validate_usage_id(usage_event_id)
        except Exception: _fail(OperationsErrorCode.INVALID_RECORD)
    _version(contract_version)
    return _new(JobAttempt, {"attempt_id": attempt_id, "run_id": run.run_id,
        "attempt_ordinal": attempt_ordinal, "started_at": start, "finished_at": finish,
        "outcome": outcome, "error_code": error_code, "retry_allowed": retry_allowed,
        "usage_event_id": usage_event_id, "policy_id": run.policy_id,
        "policy_version": run.policy_version, "contract_version": contract_version})


def validate_job_attempts(run: JobRun, attempts: Sequence[JobAttempt]) -> tuple[JobAttempt, ...]:
    if not _valid(run, JobRun) or type(attempts) not in (tuple, list) or len(attempts) > MAX_ATTEMPTS:
        _fail(OperationsErrorCode.INVALID_RECORD)
    seen_ids = set(); seen_ordinals = set(); ordered = sorted(attempts, key=lambda x: getattr(x, "attempt_ordinal", 0))
    for attempt in ordered:
        if not _valid(attempt, JobAttempt) or attempt.run_id != run.run_id:
            _fail(OperationsErrorCode.INVALID_RECORD)
        if attempt.attempt_id in seen_ids or attempt.attempt_ordinal in seen_ordinals:
            _fail(OperationsErrorCode.DUPLICATE_IDENTIFIER)
        seen_ids.add(attempt.attempt_id); seen_ordinals.add(attempt.attempt_ordinal)
    if [a.attempt_ordinal for a in ordered] != list(range(1, len(ordered) + 1)):
        _fail(OperationsErrorCode.INVALID_RECORD)
    usage_ids = [a.usage_event_id for a in ordered if a.usage_event_id is not None]
    if len(usage_ids) != len(set(usage_ids)):
        _fail(OperationsErrorCode.DUPLICATE_IDENTIFIER)
    return tuple(ordered)


@dataclass(frozen=True, slots=True)
class AbandonmentEvaluation:
    abandoned: bool
    reason_code: str
    evaluated_at: datetime
    authority_granted: bool = False


def evaluate_abandonment(run: JobRun, *, last_heartbeat_at: datetime | None,
                         lease_expires_at: datetime | None, as_of: datetime) -> AbandonmentEvaluation:
    if not _valid(run, JobRun): _fail(OperationsErrorCode.INVALID_RECORD)
    now = _utc(as_of)
    if run.status not in {JobLifecycleStatus.ACCEPTED, JobLifecycleStatus.RUNNING}:
        return AbandonmentEvaluation(False, "not_active", now)
    if last_heartbeat_at is None or lease_expires_at is None:
        return AbandonmentEvaluation(False, "lease_unknown", now)
    heartbeat = _utc(last_heartbeat_at); expiry = _utc(lease_expires_at)
    if expiry < heartbeat: _fail(OperationsErrorCode.INVALID_RECORD)
    return AbandonmentEvaluation(now > expiry, "lease_expired" if now > expiry else "lease_active", now)


@dataclass(frozen=True, slots=True)
class DeadLetterEvaluation:
    dead_letter: bool
    reason_code: str
    authority_granted: bool = False


def evaluate_dead_letter(run: JobRun, attempts: Sequence[JobAttempt], policy: RetryPolicy) -> DeadLetterEvaluation:
    ordered = validate_job_attempts(run, attempts)
    if not _valid_retry_policy(policy): _fail(OperationsErrorCode.INVALID_RETRY_POLICY)
    if run.status not in {JobLifecycleStatus.FAILED, JobLifecycleStatus.TIMED_OUT, JobLifecycleStatus.ABANDONED}:
        return DeadLetterEvaluation(False, "run_not_failed")
    if not ordered: return DeadLetterEvaluation(True, "missing_attempt_history")
    last = ordered[-1]
    exhausted = len(ordered) >= policy.maximum_attempts or not last.retry_allowed
    return DeadLetterEvaluation(exhausted, "retry_exhausted" if exhausted else "retry_available")


class NotificationChannel(str, Enum):
    IN_APP = "IN_APP"
    EMAIL = "EMAIL"
    WEBHOOK = "WEBHOOK"


class NotificationPriority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DeliveryOutcome(str, Enum):
    ATTEMPTED = "ATTEMPTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True, init=False)
class NotificationIntent(_FactoryRecord):
    intent_id: str
    scope: OperationsScope
    notification_type: str
    recipient_reference: str
    template_reference: str
    source_record_type: str
    source_record_id: str
    requested_at: datetime
    priority: NotificationPriority
    authority_provenance: AuthorityProvenance
    policy_id: str
    policy_version: str
    classification: ResourceClassification
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_notification_intent(*, intent_id: str, scope: OperationsScope,
                               notification_type: str, recipient_reference: str,
                               template_reference: str, source_record_type: str,
                               source_record_id: str, requested_at: datetime,
                               priority: NotificationPriority, actor_ref: str,
                               authority_decision: AuthorizationDecision,
                               policy_id: str, policy_version: str,
                               classification: ResourceClassification,
                               contract_version: str = OPERATIONS_CONTRACT_VERSION) -> NotificationIntent:
    validate_notification_intent_id(intent_id); _scope_tuple(scope)
    for value in (notification_type, recipient_reference, template_reference,
                  source_record_type, source_record_id, actor_ref): _code(value)
    if type(priority) is not NotificationPriority or type(classification) is not ResourceClassification:
        _fail(OperationsErrorCode.INVALID_NOTIFICATION)
    when = _utc(requested_at); _code(policy_id); _code(policy_version, sensitive=False); _version(contract_version)
    auth = _authority(authority_decision, resource_type="operations_notification_intent",
                      resource_id=intent_id, scope=scope,
                      capability="operations.notification.request", actor_ref=actor_ref,
                      policy_id=policy_id, policy_version=policy_version)
    return _new(NotificationIntent, locals() | {"requested_at": when, "authority_provenance": auth})


@dataclass(frozen=True, slots=True, init=False)
class DeliveryAttempt(_FactoryRecord):
    delivery_attempt_id: str
    intent_id: str
    scope: OperationsScope
    channel: NotificationChannel
    attempt_ordinal: int
    attempted_at: datetime
    outcome: DeliveryOutcome
    acknowledgement_code: str | None
    provider_reference: str | None
    error_code: str | None
    policy_id: str
    policy_version: str
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_delivery_attempt(*, delivery_attempt_id: str, intent: NotificationIntent,
                            channel: NotificationChannel, attempt_ordinal: int,
                            attempted_at: datetime, outcome: DeliveryOutcome,
                            acknowledgement_code: str | None = None,
                            provider_reference: str | None = None,
                            error_code: str | None = None,
                            contract_version: str = OPERATIONS_CONTRACT_VERSION) -> DeliveryAttempt:
    validate_delivery_attempt_id(delivery_attempt_id)
    if not _valid(intent, NotificationIntent) or type(channel) is not NotificationChannel:
        _fail(OperationsErrorCode.INVALID_NOTIFICATION)
    if type(attempt_ordinal) is not int or isinstance(attempt_ordinal, bool) or not 1 <= attempt_ordinal <= MAX_ATTEMPTS:
        _fail(OperationsErrorCode.INVALID_NOTIFICATION)
    if type(outcome) is not DeliveryOutcome: _fail(OperationsErrorCode.INVALID_NOTIFICATION)
    when = _utc(attempted_at)
    if when < intent.requested_at: _fail(OperationsErrorCode.INVALID_NOTIFICATION)
    if outcome is DeliveryOutcome.ACKNOWLEDGED:
        if acknowledgement_code is None: _fail(OperationsErrorCode.INVALID_NOTIFICATION)
        _code(acknowledgement_code)
    elif acknowledgement_code is not None: _fail(OperationsErrorCode.INVALID_NOTIFICATION)
    if outcome is DeliveryOutcome.FAILED:
        if error_code is None: _fail(OperationsErrorCode.INVALID_NOTIFICATION)
        _code(error_code, sensitive=False)
    elif error_code is not None: _fail(OperationsErrorCode.INVALID_NOTIFICATION)
    if provider_reference is not None: _code(provider_reference)
    _version(contract_version)
    return _new(DeliveryAttempt, {"delivery_attempt_id": delivery_attempt_id,
        "intent_id": intent.intent_id, "scope": intent.scope, "channel": channel,
        "attempt_ordinal": attempt_ordinal, "attempted_at": when, "outcome": outcome,
        "acknowledgement_code": acknowledgement_code, "provider_reference": provider_reference,
        "error_code": error_code, "policy_id": intent.policy_id,
        "policy_version": intent.policy_version, "contract_version": contract_version})


def validate_delivery_attempts(intent: NotificationIntent,
                               attempts: Sequence[DeliveryAttempt]) -> tuple[DeliveryAttempt, ...]:
    if not _valid(intent, NotificationIntent) or type(attempts) not in (list, tuple):
        _fail(OperationsErrorCode.INVALID_NOTIFICATION)
    ordered = sorted(attempts, key=lambda item: getattr(item, "attempt_ordinal", 0))
    if len(ordered) > MAX_ATTEMPTS: _fail(OperationsErrorCode.COLLECTION_LIMIT_EXCEEDED)
    for index, item in enumerate(ordered, 1):
        if (not _valid(item, DeliveryAttempt) or item.intent_id != intent.intent_id
                or _scope_tuple(item.scope) != _scope_tuple(intent.scope)
                or item.attempt_ordinal != index):
            _fail(OperationsErrorCode.INVALID_NOTIFICATION)
    if len({item.delivery_attempt_id for item in ordered}) != len(ordered):
        _fail(OperationsErrorCode.DUPLICATE_IDENTIFIER)
    return tuple(ordered)


class HealthCheckType(str, Enum):
    LIVENESS = "LIVENESS"
    READINESS = "READINESS"
    DEPENDENCY = "DEPENDENCY"


class Criticality(str, Enum):
    CRITICAL = "CRITICAL"
    NONCRITICAL = "NONCRITICAL"


class HealthObservationOutcome(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class ServiceHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True, init=False)
class HealthCheckDefinition(_FactoryRecord):
    check_id: str
    scope: OperationsScope
    dependency_code: str
    check_type: HealthCheckType
    expected_interval: timedelta
    stale_after: timedelta
    criticality: Criticality
    policy_id: str
    policy_version: str
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_health_check_definition(*, check_id: str, scope: OperationsScope,
                                   dependency_code: str, check_type: HealthCheckType,
                                   expected_interval: timedelta, stale_after: timedelta,
                                   criticality: Criticality, policy_id: str,
                                   policy_version: str,
                                   contract_version: str = OPERATIONS_CONTRACT_VERSION) -> HealthCheckDefinition:
    validate_health_check_id(check_id); _scope_tuple(scope); _code(dependency_code)
    if type(check_type) is not HealthCheckType or type(criticality) is not Criticality:
        _fail(OperationsErrorCode.INVALID_HEALTH)
    _duration(expected_interval); _duration(stale_after)
    if stale_after < expected_interval: _fail(OperationsErrorCode.INVALID_HEALTH)
    _code(policy_id); _code(policy_version, sensitive=False); _version(contract_version)
    return _new(HealthCheckDefinition, locals())


@dataclass(frozen=True, slots=True)
class SafeMeasurement:
    metric_code: str
    value: int | Decimal
    unit_code: str

    def __post_init__(self) -> None:
        _code(self.metric_code); _code(self.unit_code)
        value = self.value
        if type(value) is int and not isinstance(value, bool): value = Decimal(value)
        if type(value) is not Decimal or not value.is_finite() or abs(value) > MAX_METRIC_ABS:
            _fail(OperationsErrorCode.INVALID_SIGNAL)


@dataclass(frozen=True, slots=True, init=False)
class HealthObservation(_FactoryRecord):
    signal_id: str
    check_id: str
    scope: OperationsScope
    observed_at: datetime
    latency_ms: Decimal | None
    outcome: HealthObservationOutcome
    reason_code: str
    measurements: tuple[SafeMeasurement, ...]
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_health_observation(*, signal_id: str, definition: HealthCheckDefinition,
                              observed_at: datetime, outcome: HealthObservationOutcome,
                              reason_code: str, latency_ms: Decimal | None = None,
                              measurements: tuple[SafeMeasurement, ...] = (),
                              contract_version: str = OPERATIONS_CONTRACT_VERSION) -> HealthObservation:
    validate_operational_signal_id(signal_id)
    if not _valid(definition, HealthCheckDefinition) or type(outcome) is not HealthObservationOutcome:
        _fail(OperationsErrorCode.INVALID_HEALTH)
    _code(reason_code, sensitive=False); when = _utc(observed_at)
    if latency_ms is not None and (type(latency_ms) is not Decimal or not latency_ms.is_finite()
                                   or latency_ms < 0 or latency_ms > MAX_METRIC_ABS):
        _fail(OperationsErrorCode.INVALID_HEALTH)
    if (type(measurements) is not tuple or len(measurements) > MAX_MEASUREMENTS
            or any(type(v) is not SafeMeasurement for v in measurements)):
        _fail(OperationsErrorCode.INVALID_HEALTH)
    _version(contract_version)
    return _new(HealthObservation, {"signal_id": signal_id, "check_id": definition.check_id,
        "scope": definition.scope, "observed_at": when, "latency_ms": latency_ms,
        "outcome": outcome, "reason_code": reason_code, "measurements": measurements,
        "contract_version": contract_version})


@dataclass(frozen=True, slots=True, init=False)
class ServiceHealth(_FactoryRecord):
    scope: OperationsScope
    service_code: str
    status: ServiceHealthStatus
    evaluated_at: datetime
    fresh_until: datetime
    contributing_signal_ids: tuple[str, ...]
    reason_code: str
    authority_granted: bool
    policy_id: str
    policy_version: str
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def derive_service_health(definitions: Sequence[HealthCheckDefinition],
                          observations: Sequence[HealthObservation], *,
                          scope: OperationsScope, service_code: str,
                          as_of: datetime) -> ServiceHealth:
    if type(definitions) not in (tuple, list) or type(observations) not in (tuple, list):
        _fail(OperationsErrorCode.INVALID_HEALTH)
    if not definitions or len(definitions) > MAX_RECORDS or len(observations) > MAX_RECORDS:
        _fail(OperationsErrorCode.INVALID_HEALTH)
    expected = _scope_tuple(scope); _code(service_code); now = _utc(as_of)
    checks = {}
    policies = set()
    for definition in definitions:
        if (not _valid(definition, HealthCheckDefinition)
                or _scope_tuple(definition.scope) != expected
                or definition.scope.service_code != service_code):
            _fail(OperationsErrorCode.TENANT_CONFLICT)
        if definition.check_id in checks: _fail(OperationsErrorCode.DUPLICATE_IDENTIFIER)
        checks[definition.check_id] = definition
        policies.add((definition.policy_id, definition.policy_version))
    if len(policies) != 1: _fail(OperationsErrorCode.POLICY_CONFLICT)
    latest: dict[str, HealthObservation] = {}
    for observation in observations:
        if not _valid(observation, HealthObservation) or observation.check_id not in checks:
            _fail(OperationsErrorCode.INVALID_HEALTH)
        if _scope_tuple(observation.scope) != expected: _fail(OperationsErrorCode.TENANT_CONFLICT)
        if observation.observed_at > now:
            continue
        prior = latest.get(observation.check_id)
        if prior is None or observation.observed_at > prior.observed_at:
            latest[observation.check_id] = observation
    states = []; signal_ids = []; freshness = []
    for check_id, definition in checks.items():
        observation = latest.get(check_id)
        if observation is None:
            states.append((ServiceHealthStatus.UNAVAILABLE if definition.criticality is Criticality.CRITICAL
                           else ServiceHealthStatus.UNKNOWN, definition.criticality, "missing_observation"))
            continue
        signal_ids.append(observation.signal_id)
        freshness.append(observation.observed_at + definition.stale_after)
        if observation.observed_at + definition.stale_after < now:
            states.append((ServiceHealthStatus.UNKNOWN, definition.criticality, "stale_observation"))
        else:
            states.append((ServiceHealthStatus[observation.outcome.name], definition.criticality,
                           observation.reason_code))
    critical = [state for state, criticality, _ in states if criticality is Criticality.CRITICAL]
    all_states = [state for state, _, _ in states]
    if ServiceHealthStatus.UNAVAILABLE in critical: status, reason = ServiceHealthStatus.UNAVAILABLE, "critical_unavailable"
    elif ServiceHealthStatus.UNKNOWN in critical: status, reason = ServiceHealthStatus.UNKNOWN, "critical_unknown"
    elif ServiceHealthStatus.DEGRADED in all_states or ServiceHealthStatus.UNAVAILABLE in all_states:
        status, reason = ServiceHealthStatus.DEGRADED, "dependency_degraded"
    elif ServiceHealthStatus.UNKNOWN in all_states: status, reason = ServiceHealthStatus.UNKNOWN, "dependency_unknown"
    else: status, reason = ServiceHealthStatus.HEALTHY, "all_checks_healthy"
    policy_id, policy_version = next(iter(policies))
    return _new(ServiceHealth, {"scope": scope, "service_code": service_code,
        "status": status, "evaluated_at": now,
        "fresh_until": min(freshness) if freshness else now,
        "contributing_signal_ids": tuple(sorted(signal_ids)), "reason_code": reason,
        "authority_granted": False, "policy_id": policy_id,
        "policy_version": policy_version, "contract_version": OPERATIONS_CONTRACT_VERSION})


class OperationalSignalType(str, Enum):
    JOB_ACCEPTED = "JOB_ACCEPTED"
    JOB_COMPLETED = "JOB_COMPLETED"
    JOB_FAILED = "JOB_FAILED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    NOTIFICATION_ATTEMPTED = "NOTIFICATION_ATTEMPTED"
    DEPENDENCY_DEGRADED = "DEPENDENCY_DEGRADED"
    BACKUP_RECORDED = "BACKUP_RECORDED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    RESTORE_ATTEMPTED = "RESTORE_ATTEMPTED"
    ROLLBACK_PROPOSED = "ROLLBACK_PROPOSED"


@dataclass(frozen=True, slots=True, init=False)
class OperationalSignal(_FactoryRecord):
    signal_id: str
    scope: OperationsScope
    signal_type: OperationalSignalType
    source_record_type: str
    source_record_id: str
    occurred_at: datetime
    reason_code: str
    measurements: tuple[SafeMeasurement, ...]
    correlation_id: str | None
    official_event: bool
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_operational_signal(*, signal_id: str, scope: OperationsScope,
                              signal_type: OperationalSignalType,
                              source_record_type: str, source_record_id: str,
                              occurred_at: datetime, reason_code: str,
                              measurements: tuple[SafeMeasurement, ...] = (),
                              correlation_id: str | None = None,
                              contract_version: str = OPERATIONS_CONTRACT_VERSION) -> OperationalSignal:
    validate_operational_signal_id(signal_id); _scope_tuple(scope)
    if type(signal_type) is not OperationalSignalType: _fail(OperationsErrorCode.INVALID_SIGNAL)
    for value in (source_record_type, source_record_id): _code(value)
    _code(reason_code, sensitive=False)
    if correlation_id is not None: _code(correlation_id)
    if (type(measurements) is not tuple or len(measurements) > MAX_MEASUREMENTS
            or any(type(v) is not SafeMeasurement for v in measurements)):
        _fail(OperationsErrorCode.INVALID_SIGNAL)
    when = _utc(occurred_at); _version(contract_version)
    return _new(OperationalSignal, locals() | {"occurred_at": when, "official_event": False})


class BackupLifecycleStatus(str, Enum):
    REQUESTED = "REQUESTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class EncryptionStatus(str, Enum):
    ENCRYPTED = "ENCRYPTED"
    NOT_ENCRYPTED = "NOT_ENCRYPTED"
    UNKNOWN = "UNKNOWN"


class VerificationOutcome(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class RestoreLifecycleStatus(str, Enum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RestoreValidationOutcome(str, Enum):
    NOT_PERFORMED = "NOT_PERFORMED"
    PASSED = "PASSED"
    FAILED = "FAILED"


class IntegrityStatus(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class CompatibilityStatus(str, Enum):
    COMPATIBLE = "COMPATIBLE"
    INCOMPATIBLE = "INCOMPATIBLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True, init=False)
class BackupPolicy(_FactoryRecord):
    backup_policy_id: str
    scope: OperationsScope
    resource_type: str
    trigger_code: str
    retention: timedelta
    encryption_required: bool
    verification_required: bool
    recovery_point_objective: timedelta | None
    recovery_time_objective: timedelta | None
    effective_at: datetime
    expires_at: datetime | None
    authority_provenance: AuthorityProvenance
    policy_id: str
    policy_version: str
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_backup_policy(*, backup_policy_id: str, scope: OperationsScope,
                         resource_type: str, trigger_code: str, retention: timedelta,
                         encryption_required: bool, verification_required: bool,
                         effective_at: datetime, actor_ref: str,
                         authority_decision: AuthorizationDecision,
                         policy_id: str, policy_version: str,
                         recovery_point_objective: timedelta | None = None,
                         recovery_time_objective: timedelta | None = None,
                         expires_at: datetime | None = None,
                         contract_version: str = OPERATIONS_CONTRACT_VERSION) -> BackupPolicy:
    validate_backup_policy_id(backup_policy_id); _scope_tuple(scope)
    _code(resource_type); _code(trigger_code); _duration(retention)
    if type(encryption_required) is not bool or type(verification_required) is not bool:
        _fail(OperationsErrorCode.INVALID_BACKUP)
    if recovery_point_objective is not None: _duration(recovery_point_objective)
    if recovery_time_objective is not None: _duration(recovery_time_objective)
    start = _utc(effective_at); end = None if expires_at is None else _utc(expires_at)
    if end is not None and end <= start: _fail(OperationsErrorCode.INVALID_BACKUP)
    _code(policy_id); _code(policy_version, sensitive=False); _version(contract_version)
    auth = _authority(authority_decision, resource_type="operations_backup_policy",
                      resource_id=backup_policy_id, scope=scope,
                      capability="operations.backup_policy.admin", actor_ref=actor_ref,
                      policy_id=policy_id, policy_version=policy_version)
    return _new(BackupPolicy, locals() | {"effective_at": start,
                "expires_at": end, "authority_provenance": auth})


@dataclass(frozen=True, slots=True, init=False)
class BackupRecord(_FactoryRecord):
    backup_record_id: str
    backup_policy_id: str
    scope: OperationsScope
    resource_type: str
    resource_reference: str
    created_at: datetime
    completed_at: datetime | None
    artifact_reference: str | None
    content_digest: str | None
    size_bytes: int | None
    encryption_status: EncryptionStatus
    status: BackupLifecycleStatus
    producer_ref: str
    verified: bool
    policy_id: str
    policy_version: str
    classification: ResourceClassification
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_backup_record(*, backup_record_id: str, policy: BackupPolicy,
                         resource_reference: str, created_at: datetime,
                         encryption_status: EncryptionStatus,
                         status: BackupLifecycleStatus, producer_ref: str,
                         classification: ResourceClassification,
                         completed_at: datetime | None = None,
                         artifact_reference: str | None = None,
                         content_digest: str | None = None,
                         size_bytes: int | None = None,
                         contract_version: str = OPERATIONS_CONTRACT_VERSION) -> BackupRecord:
    validate_backup_record_id(backup_record_id)
    if not _valid(policy, BackupPolicy): _fail(OperationsErrorCode.INVALID_BACKUP)
    _code(resource_reference); _code(producer_ref)
    if type(encryption_status) is not EncryptionStatus or type(status) is not BackupLifecycleStatus:
        _fail(OperationsErrorCode.INVALID_BACKUP)
    if type(classification) is not ResourceClassification:
        _fail(OperationsErrorCode.INVALID_BACKUP)
    created = _utc(created_at); completed = None if completed_at is None else _utc(completed_at)
    if created < policy.effective_at or (policy.expires_at is not None and created >= policy.expires_at):
        _fail(OperationsErrorCode.INVALID_BACKUP)
    terminal_with_artifact = status is BackupLifecycleStatus.COMPLETED
    if terminal_with_artifact:
        if completed is None or completed < created or artifact_reference is None:
            _fail(OperationsErrorCode.INVALID_BACKUP)
        _code(artifact_reference)
        if policy.encryption_required and encryption_status is not EncryptionStatus.ENCRYPTED:
            _fail(OperationsErrorCode.INVALID_BACKUP)
    else:
        if status in {BackupLifecycleStatus.FAILED, BackupLifecycleStatus.INVALIDATED,
                      BackupLifecycleStatus.EXPIRED}:
            if completed is None or completed < created: _fail(OperationsErrorCode.INVALID_BACKUP)
        elif completed is not None: _fail(OperationsErrorCode.INVALID_BACKUP)
        if artifact_reference is not None or content_digest is not None or size_bytes is not None:
            _fail(OperationsErrorCode.INVALID_BACKUP)
    if content_digest is not None:
        if type(content_digest) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", content_digest) is None:
            _fail(OperationsErrorCode.INVALID_BACKUP)
    if size_bytes is not None and (type(size_bytes) is not int or isinstance(size_bytes, bool)
                                   or not 0 <= size_bytes <= 2 ** 63 - 1):
        _fail(OperationsErrorCode.INVALID_BACKUP)
    _version(contract_version)
    return _new(BackupRecord, {"backup_record_id": backup_record_id,
        "backup_policy_id": policy.backup_policy_id, "scope": policy.scope,
        "resource_type": policy.resource_type, "resource_reference": resource_reference,
        "created_at": created, "completed_at": completed,
        "artifact_reference": artifact_reference, "content_digest": content_digest,
        "size_bytes": size_bytes, "encryption_status": encryption_status, "status": status,
        "producer_ref": producer_ref, "verified": False, "policy_id": policy.policy_id,
        "policy_version": policy.policy_version, "classification": classification,
        "contract_version": contract_version})


@dataclass(frozen=True, slots=True, init=False)
class BackupVerification(_FactoryRecord):
    verification_id: str
    backup_record_id: str
    scope: OperationsScope
    verifier_ref: str
    verified_at: datetime
    verification_method_code: str
    integrity_outcome: VerificationOutcome
    compatibility_outcome: VerificationOutcome
    restorable_outcome: VerificationOutcome
    verified: bool
    reason_code: str
    evidence_reference: str | None
    authority_provenance: AuthorityProvenance
    policy_id: str
    policy_version: str
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_backup_verification(*, verification_id: str, backup: BackupRecord,
                               verifier_ref: str, verified_at: datetime,
                               verification_method_code: str,
                               integrity_outcome: VerificationOutcome,
                               compatibility_outcome: VerificationOutcome,
                               restorable_outcome: VerificationOutcome,
                               reason_code: str, authority_decision: AuthorizationDecision,
                               evidence_reference: str | None = None,
                               contract_version: str = OPERATIONS_CONTRACT_VERSION) -> BackupVerification:
    validate_backup_verification_id(verification_id)
    if (not _valid(backup, BackupRecord) or backup.status is not BackupLifecycleStatus.COMPLETED
            or backup.completed_at is None):
        _fail(OperationsErrorCode.INVALID_VERIFICATION)
    _code(verifier_ref); _code(verification_method_code); _code(reason_code, sensitive=False)
    outcomes = (integrity_outcome, compatibility_outcome, restorable_outcome)
    if any(type(item) is not VerificationOutcome for item in outcomes):
        _fail(OperationsErrorCode.INVALID_VERIFICATION)
    when = _utc(verified_at)
    if when < backup.completed_at: _fail(OperationsErrorCode.INVALID_VERIFICATION)
    if evidence_reference is not None:
        try: validate_evidence_id(evidence_reference)
        except Exception: _fail(OperationsErrorCode.INVALID_VERIFICATION)
    _version(contract_version)
    auth = _authority(authority_decision, resource_type="operations_backup_verification",
                      resource_id=verification_id, scope=backup.scope,
                      capability="operations.backup.verify", actor_ref=verifier_ref,
                      policy_id=backup.policy_id, policy_version=backup.policy_version)
    passed = all(item is VerificationOutcome.PASSED for item in outcomes)
    return _new(BackupVerification, {"verification_id": verification_id,
        "backup_record_id": backup.backup_record_id, "scope": backup.scope,
        "verifier_ref": verifier_ref, "verified_at": when,
        "verification_method_code": verification_method_code,
        "integrity_outcome": integrity_outcome,
        "compatibility_outcome": compatibility_outcome,
        "restorable_outcome": restorable_outcome, "verified": passed,
        "reason_code": reason_code, "evidence_reference": evidence_reference,
        "authority_provenance": auth, "policy_id": backup.policy_id,
        "policy_version": backup.policy_version, "contract_version": contract_version})


def _valid_approval(approval: object, *, protected_record_id: str,
                    required_capability: str, scope: OperationsScope,
                    as_of: datetime) -> bool:
    try:
        if scope.global_scope:
            return False
        return (_valid_truth_approval(approval)
                and approval.status is ApprovalStatus.APPROVED
                and approval.target_type is TargetType.OPERATIONAL_CONTEXT
                and approval.target_id == scope.tenant_context.project_context_id
                and approval.scope == _authority_scope(scope, approval.approval_id)
                and approval.conditions_code == protected_record_id
                and approval.authority_provenance.capability_code == required_capability
                and approval.visibility_policy_id == scope.tenant_context.policy_id
                and approval.visibility_policy_version == scope.tenant_context.policy_version
                and approval.effective_at <= as_of
                and (approval.expires_at is None or approval.expires_at > as_of))
    except Exception:
        return False


@dataclass(frozen=True, slots=True, init=False)
class RestorePlan(_FactoryRecord):
    restore_plan_id: str
    backup_record_id: str
    verification_id: str
    target_scope: OperationsScope
    compatibility_policy_code: str
    expected_rollback_point_id: str
    requested_actor_ref: str
    approval_id: str | None
    created_at: datetime
    expires_at: datetime
    authority_provenance: AuthorityProvenance
    policy_id: str
    policy_version: str
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_restore_plan(*, restore_plan_id: str, backup: BackupRecord,
                        verification: BackupVerification, target_scope: OperationsScope,
                        compatibility_policy_code: str,
                        expected_rollback_point_id: str,
                        requested_actor_ref: str, created_at: datetime,
                        expires_at: datetime, authority_decision: AuthorizationDecision,
                        approval_required: bool, approval: ApprovalRecord | None = None,
                        contract_version: str = OPERATIONS_CONTRACT_VERSION) -> RestorePlan:
    validate_restore_plan_id(restore_plan_id)
    if (not _valid(backup, BackupRecord) or not _valid(verification, BackupVerification)
            or verification.backup_record_id != backup.backup_record_id or not verification.verified):
        _fail(OperationsErrorCode.BACKUP_NOT_VERIFIED)
    if _scope_tuple(backup.scope) != _scope_tuple(target_scope):
        _fail(OperationsErrorCode.TENANT_CONFLICT)
    _code(compatibility_policy_code); validate_rollback_point_id(expected_rollback_point_id)
    _code(requested_actor_ref); start = _utc(created_at); end = _utc(expires_at)
    if (end <= start or start < verification.verified_at
            or type(approval_required) is not bool):
        _fail(OperationsErrorCode.INVALID_RESTORE)
    approval_id = None
    if approval_required:
        if not _valid_approval(approval, protected_record_id=restore_plan_id,
                               required_capability="approval.restore",
                               scope=target_scope, as_of=start):
            _fail(OperationsErrorCode.INVALID_APPROVAL)
        approval_id = approval.approval_id
    elif approval is not None:
        _fail(OperationsErrorCode.INVALID_APPROVAL)
    _version(contract_version)
    auth = _authority(authority_decision, resource_type="operations_restore_plan",
                      resource_id=restore_plan_id, scope=target_scope,
                      capability="operations.restore.request", actor_ref=requested_actor_ref,
                      policy_id=backup.policy_id, policy_version=backup.policy_version)
    return _new(RestorePlan, {"restore_plan_id": restore_plan_id,
        "backup_record_id": backup.backup_record_id,
        "verification_id": verification.verification_id, "target_scope": target_scope,
        "compatibility_policy_code": compatibility_policy_code,
        "expected_rollback_point_id": expected_rollback_point_id,
        "requested_actor_ref": requested_actor_ref, "approval_id": approval_id,
        "created_at": start, "expires_at": end, "authority_provenance": auth,
        "policy_id": backup.policy_id, "policy_version": backup.policy_version,
        "contract_version": contract_version})


@dataclass(frozen=True, slots=True, init=False)
class RestoreAttempt(_FactoryRecord):
    restore_attempt_id: str
    restore_plan_id: str
    scope: OperationsScope
    started_at: datetime
    finished_at: datetime | None
    lifecycle_status: RestoreLifecycleStatus
    validation_outcome: RestoreValidationOutcome
    reason_code: str
    resulting_rollback_point_id: str | None
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_restore_attempt(*, restore_attempt_id: str, plan: RestorePlan,
                           started_at: datetime, lifecycle_status: RestoreLifecycleStatus,
                           validation_outcome: RestoreValidationOutcome,
                           reason_code: str, finished_at: datetime | None = None,
                           resulting_rollback_point_id: str | None = None,
                           contract_version: str = OPERATIONS_CONTRACT_VERSION) -> RestoreAttempt:
    validate_restore_attempt_id(restore_attempt_id)
    if not _valid(plan, RestorePlan) or type(lifecycle_status) is not RestoreLifecycleStatus:
        _fail(OperationsErrorCode.INVALID_RESTORE)
    if type(validation_outcome) is not RestoreValidationOutcome:
        _fail(OperationsErrorCode.INVALID_RESTORE)
    start = _utc(started_at); finish = None if finished_at is None else _utc(finished_at)
    if start < plan.created_at or start >= plan.expires_at: _fail(OperationsErrorCode.INVALID_RESTORE)
    if lifecycle_status is RestoreLifecycleStatus.STARTED:
        if finish is not None or validation_outcome is not RestoreValidationOutcome.NOT_PERFORMED:
            _fail(OperationsErrorCode.INVALID_RESTORE)
    elif finish is None or finish < start:
        _fail(OperationsErrorCode.INVALID_RESTORE)
    if lifecycle_status is RestoreLifecycleStatus.COMPLETED and validation_outcome is RestoreValidationOutcome.NOT_PERFORMED:
        pass  # completion is deliberately not represented as verification
    if resulting_rollback_point_id is not None: validate_rollback_point_id(resulting_rollback_point_id)
    _code(reason_code, sensitive=False); _version(contract_version)
    return _new(RestoreAttempt, {"restore_attempt_id": restore_attempt_id,
        "restore_plan_id": plan.restore_plan_id, "scope": plan.target_scope,
        "started_at": start, "finished_at": finish, "lifecycle_status": lifecycle_status,
        "validation_outcome": validation_outcome, "reason_code": reason_code,
        "resulting_rollback_point_id": resulting_rollback_point_id,
        "contract_version": contract_version})


@dataclass(frozen=True, slots=True, init=False)
class RollbackPoint(_FactoryRecord):
    rollback_point_id: str
    scope: OperationsScope
    resource_type: str
    resource_reference: str
    source_backup_record_id: str | None
    deployment_reference: str | None
    created_at: datetime
    integrity_status: IntegrityStatus
    compatibility_status: CompatibilityStatus
    authority_provenance: AuthorityProvenance
    policy_id: str
    policy_version: str
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_rollback_point(*, rollback_point_id: str, scope: OperationsScope,
                          resource_type: str, resource_reference: str,
                          created_at: datetime, integrity_status: IntegrityStatus,
                          compatibility_status: CompatibilityStatus,
                          actor_ref: str, authority_decision: AuthorizationDecision,
                          policy_id: str, policy_version: str,
                          source_backup: BackupRecord | None = None,
                          backup_verification: BackupVerification | None = None,
                          deployment_reference: str | None = None,
                          contract_version: str = OPERATIONS_CONTRACT_VERSION) -> RollbackPoint:
    validate_rollback_point_id(rollback_point_id); _scope_tuple(scope)
    _code(resource_type); _code(resource_reference); _code(actor_ref)
    if (source_backup is None) == (deployment_reference is None):
        _fail(OperationsErrorCode.INVALID_ROLLBACK)
    source_backup_record_id = None
    if source_backup is not None:
        if (not _valid(source_backup, BackupRecord)
                or source_backup.status is not BackupLifecycleStatus.COMPLETED
                or not _valid(backup_verification, BackupVerification)
                or backup_verification.backup_record_id != source_backup.backup_record_id
                or backup_verification.verified is not True
                or _scope_tuple(source_backup.scope) != _scope_tuple(scope)
                or source_backup.resource_type != resource_type
                or source_backup.resource_reference != resource_reference):
            _fail(OperationsErrorCode.INVALID_ROLLBACK)
        source_backup_record_id = source_backup.backup_record_id
    if deployment_reference is not None:
        if backup_verification is not None: _fail(OperationsErrorCode.INVALID_ROLLBACK)
        _code(deployment_reference)
    if type(integrity_status) is not IntegrityStatus or type(compatibility_status) is not CompatibilityStatus:
        _fail(OperationsErrorCode.INVALID_ROLLBACK)
    when = _utc(created_at); _code(policy_id); _code(policy_version, sensitive=False); _version(contract_version)
    auth = _authority(authority_decision, resource_type="operations_rollback_point",
                      resource_id=rollback_point_id, scope=scope,
                      capability="operations.rollback.propose", actor_ref=actor_ref,
                      policy_id=policy_id, policy_version=policy_version)
    return _new(RollbackPoint, locals() | {"created_at": when, "authority_provenance": auth})


@dataclass(frozen=True, slots=True, init=False)
class RollbackEvaluation(_FactoryRecord):
    rollback_point_id: str
    may_propose: bool
    reason_code: str
    approval_id: str | None
    evaluated_at: datetime
    executed: bool
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def evaluate_rollback(point: RollbackPoint, *, as_of: datetime,
                      approval_required: bool, approval: ApprovalRecord | None = None) -> RollbackEvaluation:
    if not _valid(point, RollbackPoint) or type(approval_required) is not bool:
        _fail(OperationsErrorCode.INVALID_ROLLBACK)
    now = _utc(as_of); approval_id = None
    allowed = (point.integrity_status is IntegrityStatus.VALID
               and point.compatibility_status is CompatibilityStatus.COMPATIBLE)
    reason = "rollback_eligible" if allowed else "integrity_or_compatibility_unknown"
    if approval_required:
        if not _valid_approval(approval, protected_record_id=point.rollback_point_id,
                               required_capability="approval.rollback",
                               scope=point.scope, as_of=now):
            allowed, reason = False, "approval_required"
        else: approval_id = approval.approval_id
    elif approval is not None: _fail(OperationsErrorCode.INVALID_APPROVAL)
    return _new(RollbackEvaluation, {"rollback_point_id": point.rollback_point_id,
        "may_propose": allowed, "reason_code": reason, "approval_id": approval_id,
        "evaluated_at": now, "executed": False,
        "contract_version": OPERATIONS_CONTRACT_VERSION})


_PROJECT_SPEC = {
    ConfigurationDefinition: ("operations_configuration", "definition_id", "operations.configuration.view"),
    ConfigurationSnapshot: ("operations_configuration", "snapshot_id", "operations.configuration.view"),
    FeatureFlagDefinition: ("operations_feature_flag", "feature_flag_id", "operations.feature.view"),
    FeatureEvaluation: ("operations_feature_evaluation", "evaluation_id", "operations.feature.view"),
    SecretReference: ("operations_secret_reference", "secret_reference_id", "operations.secret_reference.view"),
    JobDefinition: ("operations_job", "job_id", "operations.job_status.view"),
    JobRun: ("operations_job_run", "run_id", "operations.job_status.view"),
    JobAttempt: ("operations_job_attempt", "attempt_id", "operations.job_status.view"),
    NotificationIntent: ("operations_notification_intent", "intent_id", "operations.notification_status.view"),
    DeliveryAttempt: ("operations_delivery_attempt", "delivery_attempt_id", "operations.notification_status.view"),
    ServiceHealth: ("operations_health", "service_code", "operations.health.view"),
    BackupRecord: ("operations_backup_record", "backup_record_id", "operations.backup_metadata.view"),
    BackupVerification: ("operations_backup_verification", "verification_id", "operations.backup_metadata.view"),
    RestorePlan: ("operations_restore_plan", "restore_plan_id", "operations.restore.view"),
    RollbackPoint: ("operations_rollback_point", "rollback_point_id", "operations.rollback.view"),
}

_PROJECT_FIELDS = {
    ConfigurationDefinition: frozenset({"definition_id", "key", "value_type", "scope_type", "required",
        "has_default", "secret_bearing", "validation_code", "effective_at", "expires_at",
        "policy_id", "policy_version", "classification", "contract_version"}),
    ConfigurationSnapshot: frozenset({"snapshot_id", "definition_id", "key", "value_type", "value",
        "effective_at", "expires_at", "source_code", "policy_id", "policy_version",
        "classification", "contract_version"}),
    FeatureFlagDefinition: frozenset({"feature_flag_id", "flag_key", "status", "mode", "default_enabled",
        "percentage_basis_points", "effective_at", "expires_at", "supersedes_flag_id",
        "policy_id", "policy_version", "contract_version"}),
    FeatureEvaluation: frozenset({"evaluation_id", "feature_flag_id", "flag_key", "tenant_id", "enabled",
        "reason_code", "evaluated_at", "authority_granted", "entitlement_granted",
        "policy_id", "policy_version", "contract_version"}),
    SecretReference: frozenset({"secret_reference_id", "provider_code", "purpose_code", "rotation_reference",
        "status", "created_at", "expires_at", "policy_id", "policy_version",
        "classification", "contract_version"}),
    JobDefinition: frozenset({"job_id", "job_type", "version", "required_scope_type", "timeout",
        "idempotency_principal_bound", "cancellation_policy", "status", "policy_id",
        "policy_version", "contract_version"}),
    JobRun: frozenset({"run_id", "request_id", "status", "accepted_at", "started_at", "terminal_at",
        "current_attempt_id", "cancellation_reference", "policy_id", "policy_version", "contract_version"}),
    JobAttempt: frozenset({"attempt_id", "run_id", "attempt_ordinal", "started_at", "finished_at",
        "outcome", "error_code", "retry_allowed", "policy_id",
        "policy_version", "contract_version"}),
    NotificationIntent: frozenset({"intent_id", "notification_type", "recipient_reference", "template_reference",
        "requested_at", "priority", "policy_id",
        "policy_version", "classification", "contract_version"}),
    DeliveryAttempt: frozenset({"delivery_attempt_id", "intent_id", "channel", "attempt_ordinal",
        "attempted_at", "outcome", "acknowledgement_code", "provider_reference", "error_code",
        "policy_id", "policy_version", "contract_version"}),
    ServiceHealth: frozenset({"service_code", "status", "evaluated_at", "fresh_until",
        "reason_code", "authority_granted",
        "policy_id", "policy_version", "contract_version"}),
    BackupRecord: frozenset({"backup_record_id", "backup_policy_id", "resource_type", "resource_reference",
        "created_at", "completed_at", "size_bytes", "encryption_status", "status", "producer_ref",
        "verified", "policy_id", "policy_version", "classification", "contract_version"}),
    BackupVerification: frozenset({"verification_id", "backup_record_id", "verifier_ref", "verified_at",
        "verification_method_code", "integrity_outcome", "compatibility_outcome", "restorable_outcome",
        "verified", "reason_code", "policy_id", "policy_version", "contract_version"}),
    RestorePlan: frozenset({"restore_plan_id", "backup_record_id", "verification_id",
        "compatibility_policy_code", "expected_rollback_point_id", "requested_actor_ref",
        "created_at", "expires_at", "policy_id", "policy_version", "contract_version"}),
    RollbackPoint: frozenset({"rollback_point_id", "resource_type", "resource_reference",
        "source_backup_record_id", "deployment_reference", "created_at", "integrity_status",
        "compatibility_status", "policy_id", "policy_version", "contract_version"}),
}


def _record_scope(record: object, supplied_scope: OperationsScope | None) -> OperationsScope:
    value = getattr(record, "scope", getattr(record, "target_scope", None))
    if value is None: value = supplied_scope
    if type(value) is not OperationsScope: _fail(OperationsErrorCode.INVALID_PROJECTION)
    _scope_tuple(value)
    return value


def _scalar(value: object) -> object:
    if value is None or type(value) in (str, bool, int): return value
    if isinstance(value, Enum): return value.value
    if type(value) is datetime: return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if type(value) is timedelta: return format(Decimal(str(value.total_seconds())), "f")
    if type(value) is Decimal:
        if not value.is_finite(): _fail(OperationsErrorCode.INVALID_PROJECTION)
        return format(value, "f")
    if type(value) is SecretReference:
        if not _valid(value, SecretReference): _fail(OperationsErrorCode.INVALID_PROJECTION)
        return value.secret_reference_id
    _fail(OperationsErrorCode.INVALID_PROJECTION)


def operations_metadata(record: object) -> dict[str, Any]:
    cls = type(record)
    if cls not in _PROJECT_SPEC or not _valid(record, cls):
        _fail(OperationsErrorCode.INVALID_PROJECTION)
    result = {}
    for name in _PROJECT_FIELDS[cls]:
        result[name] = _scalar(getattr(record, name))
    if (cls is ConfigurationSnapshot
            and record.value_type is ConfigurationValueType.SECRET_REFERENCE):
        result.pop("value", None)
    scope = getattr(record, "scope", getattr(record, "target_scope", None))
    if type(scope) is OperationsScope:
        result.update({"environment_code": scope.environment_code,
                       "service_code": scope.service_code,
                       "global_scope": scope.global_scope})
        if not scope.global_scope:
            context = scope.tenant_context
            result.update({"tenant_id": context.tenant_id,
                           "organization_id": context.organization_id,
                           "product_context_id": context.product_context_id,
                           "workspace_context_id": context.workspace_context_id,
                           "project_context_id": context.project_context_id})
    return result


def project_operations_record(record: object, decision: AuthorizationDecision,
                              *, scope: OperationsScope | None = None) -> dict[str, Any]:
    try:
        cls = type(record); spec = _PROJECT_SPEC.get(cls)
        if spec is None or not _valid(record, cls): _fail(OperationsErrorCode.INVALID_PROJECTION)
        resource_type, id_field, capability = spec
        record_id = getattr(record, id_field); actual_scope = _record_scope(record, scope)
        source = operations_metadata(record)
        project_authorized_fields({}, decision, behavior=ProjectionBehavior.OMIT)
        policy_id = getattr(record, "policy_id", None)
        policy_version = getattr(record, "policy_version", None)
        if policy_id is None:
            policy_id, policy_version = actual_scope.tenant_context.policy_id, actual_scope.tenant_context.policy_version
        if (decision.resource_type != resource_type or decision.resource_id != record_id
                or decision.action_code != capability or decision.policy_id != policy_id
                or decision.policy_version != policy_version
                or (decision.allowed and decision.effective_scope != _authority_scope(actual_scope, record_id))):
            _fail(OperationsErrorCode.INVALID_AUTHORITY)
        return project_authorized_fields(source, decision, behavior=ProjectionBehavior.OMIT)
    except OperationsContractError:
        raise
    except Exception:
        _fail(OperationsErrorCode.INVALID_PROJECTION)


project_configuration = project_operations_record
project_feature_flag = project_operations_record
project_feature_evaluation = project_operations_record
project_secret_reference = project_operations_record
project_job_definition = project_operations_record
project_job_run = project_operations_record
project_job_attempt = project_operations_record
project_notification_intent = project_operations_record
project_delivery_attempt = project_operations_record
project_health = project_operations_record
project_backup_record = project_operations_record
project_backup_verification = project_operations_record
project_restore_plan = project_operations_record
project_rollback_point = project_operations_record


@dataclass(frozen=True, slots=True, init=False)
class SafeOperationsAudit(_FactoryRecord):
    audit_id: str
    action_code: str
    record_type: str
    record_id: str
    tenant_id: str | None
    service_code: str
    outcome_code: str
    reason_code: str
    capability_code: str | None
    policy_id: str
    policy_version: str
    timestamp: str
    correlation_id: str | None
    contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


_AUDIT_ACTIONS = frozenset({
    "configuration.evaluated", "feature.evaluated", "feature.admin.attempted",
    "secret_reference.accessed", "job.requested", "job.accepted", "job.started",
    "job.retried", "job.completed", "job.failed", "job.timed_out", "job.cancelled",
    "notification.requested", "notification.attempted", "notification.acknowledged",
    "notification.failed", "health.changed", "backup.requested", "backup.recorded",
    "backup.failed", "verification.attempted", "verification.passed", "verification.failed",
    "restore.proposed", "restore.attempted", "restore.validated", "restore.failed",
    "rollback.proposed", "rollback.authorized", "rollback.denied",
    "cross_tenant.denied", "malformed_record.rejected",
})
_AUDIT_OUTCOMES = frozenset({"allowed", "denied", "recorded", "accepted", "started",
                             "retried", "completed", "failed", "timed_out", "cancelled",
                             "attempted", "acknowledged", "passed", "validated", "rejected"})
_AUDIT_RECORD_VALIDATORS = {
    "configuration_definition": validate_configuration_definition_id,
    "configuration_snapshot": validate_configuration_snapshot_id,
    "feature_flag": validate_feature_flag_id, "feature_evaluation": validate_feature_evaluation_id,
    "secret_reference": validate_secret_reference_id, "job": validate_job_id,
    "job_request": validate_job_request_id, "job_run": validate_job_run_id,
    "job_attempt": validate_job_attempt_id, "notification_intent": validate_notification_intent_id,
    "delivery_attempt": validate_delivery_attempt_id, "health_check": validate_health_check_id,
    "operational_signal": validate_operational_signal_id, "backup_policy": validate_backup_policy_id,
    "backup_record": validate_backup_record_id, "backup_verification": validate_backup_verification_id,
    "restore_plan": validate_restore_plan_id, "restore_attempt": validate_restore_attempt_id,
    "rollback_point": validate_rollback_point_id,
}


def build_safe_operations_audit(*, audit_id: str, action_code: str, record_type: str,
                                record_id: str, scope: OperationsScope,
                                outcome_code: str, reason_code: str,
                                policy_id: str, policy_version: str,
                                timestamp: datetime, capability_code: str | None = None,
                                correlation_id: str | None = None,
                                contract_version: str = OPERATIONS_CONTRACT_VERSION) -> SafeOperationsAudit:
    try:
        validate_operations_audit_id(audit_id); _scope_tuple(scope); _code(record_type)
        validator = _AUDIT_RECORD_VALIDATORS.get(record_type)
        if validator is None: _fail(OperationsErrorCode.INVALID_AUDIT)
        validator(record_id)
    except Exception:
        _fail(OperationsErrorCode.INVALID_AUDIT)
    for value in (action_code, outcome_code, reason_code, policy_id, policy_version):
        _code(value, OperationsErrorCode.INVALID_AUDIT, sensitive=False)
    if action_code not in _AUDIT_ACTIONS or outcome_code not in _AUDIT_OUTCOMES:
        _fail(OperationsErrorCode.INVALID_AUDIT)
    if capability_code is not None: _code(capability_code)
    if correlation_id is not None: _code(correlation_id)
    when = _utc(timestamp).isoformat().replace("+00:00", "Z"); _version(contract_version)
    return _new(SafeOperationsAudit, {"audit_id": audit_id, "action_code": action_code,
        "record_type": record_type, "record_id": record_id,
        "tenant_id": None if scope.global_scope else scope.tenant_context.tenant_id,
        "service_code": scope.service_code, "outcome_code": outcome_code,
        "reason_code": reason_code, "capability_code": capability_code,
        "policy_id": policy_id, "policy_version": policy_version, "timestamp": when,
        "correlation_id": correlation_id, "contract_version": contract_version})


class OperationsViewKind(str, Enum):
    EFFECTIVE_FEATURE_FLAGS = "EFFECTIVE_FEATURE_FLAGS"
    ACTIVE_CONFIGURATION = "ACTIVE_CONFIGURATION"
    ACTIVE_JOBS = "ACTIVE_JOBS"
    FAILED_JOBS = "FAILED_JOBS"
    RETRYABLE_JOBS = "RETRYABLE_JOBS"
    ABANDONED_JOBS = "ABANDONED_JOBS"
    DEAD_LETTER_CANDIDATES = "DEAD_LETTER_CANDIDATES"
    PENDING_NOTIFICATIONS = "PENDING_NOTIFICATIONS"
    FAILED_NOTIFICATIONS = "FAILED_NOTIFICATIONS"
    SERVICE_HEALTH = "SERVICE_HEALTH"
    LATEST_VERIFIED_BACKUPS = "LATEST_VERIFIED_BACKUPS"
    BACKUPS_REQUIRING_VERIFICATION = "BACKUPS_REQUIRING_VERIFICATION"
    PENDING_RESTORE_PLANS = "PENDING_RESTORE_PLANS"
    ROLLBACK_CANDIDATES = "ROLLBACK_CANDIDATES"


@dataclass(frozen=True, slots=True)
class DerivedOperationsView:
    view_kind: OperationsViewKind
    tenant_id: str | None
    record_ids: tuple[str, ...]
    derived: bool = True
    official_event: bool = False
    executes_operation: bool = False


def _view_source_allowed(kind: OperationsViewKind, record: object,
                         now: datetime | None) -> bool:
    if kind is OperationsViewKind.EFFECTIVE_FEATURE_FLAGS:
        return type(record) is FeatureEvaluation and record.enabled is True
    if kind is OperationsViewKind.ACTIVE_CONFIGURATION:
        return type(record) is ConfigurationSnapshot
    if kind is OperationsViewKind.ACTIVE_JOBS:
        return type(record) is JobRun and record.status in {
            JobLifecycleStatus.REQUESTED, JobLifecycleStatus.ACCEPTED, JobLifecycleStatus.RUNNING}
    if kind is OperationsViewKind.FAILED_JOBS:
        return type(record) is JobRun and record.status in {
            JobLifecycleStatus.FAILED, JobLifecycleStatus.TIMED_OUT, JobLifecycleStatus.ABANDONED}
    if kind is OperationsViewKind.RETRYABLE_JOBS:
        return type(record) is JobAttempt and record.retry_allowed is True
    if kind is OperationsViewKind.ABANDONED_JOBS:
        return type(record) is JobRun and record.status is JobLifecycleStatus.ABANDONED
    if kind is OperationsViewKind.DEAD_LETTER_CANDIDATES:
        return type(record) is JobRun and record.status is JobLifecycleStatus.DEAD_LETTERED
    if kind is OperationsViewKind.PENDING_NOTIFICATIONS:
        return type(record) is NotificationIntent
    if kind is OperationsViewKind.FAILED_NOTIFICATIONS:
        return type(record) is DeliveryAttempt and record.outcome is DeliveryOutcome.FAILED
    if kind is OperationsViewKind.SERVICE_HEALTH:
        return (type(record) is ServiceHealth and now is not None
                and record.evaluated_at <= now <= record.fresh_until)
    if kind is OperationsViewKind.LATEST_VERIFIED_BACKUPS:
        return type(record) is BackupVerification and record.verified is True
    if kind is OperationsViewKind.BACKUPS_REQUIRING_VERIFICATION:
        return (type(record) is BackupRecord
                and record.status is BackupLifecycleStatus.COMPLETED
                and record.verified is False)
    if kind is OperationsViewKind.PENDING_RESTORE_PLANS:
        return type(record) is RestorePlan and now is not None and record.created_at <= now < record.expires_at
    if kind is OperationsViewKind.ROLLBACK_CANDIDATES:
        return (type(record) is RollbackPoint
                and record.integrity_status is IntegrityStatus.VALID
                and record.compatibility_status is CompatibilityStatus.COMPATIBLE)
    return False


def create_derived_operations_view(sources: Sequence[tuple[object, AuthorizationDecision]], *,
                                   view_kind: OperationsViewKind,
                                   scope: OperationsScope,
                                   as_of: datetime | None = None) -> DerivedOperationsView:
    if type(view_kind) is not OperationsViewKind or type(sources) not in (list, tuple) or not sources:
        _fail(OperationsErrorCode.SOURCE_NOT_AUTHORIZED)
    expected = _scope_tuple(scope); ids = []; policies = set(); now = None if as_of is None else _utc(as_of)
    for pair in sources:
        if type(pair) is not tuple or len(pair) != 2: _fail(OperationsErrorCode.SOURCE_NOT_AUTHORIZED)
        record, decision = pair
        try: projected = project_operations_record(record, decision, scope=scope)
        except Exception: _fail(OperationsErrorCode.SOURCE_NOT_AUTHORIZED)
        if not decision.allowed or not projected: _fail(OperationsErrorCode.SOURCE_NOT_AUTHORIZED)
        actual_scope = _record_scope(record, scope)
        if _scope_tuple(actual_scope) != expected: _fail(OperationsErrorCode.TENANT_CONFLICT)
        policies.add((decision.policy_id, decision.policy_version))
        _, id_field, _ = _PROJECT_SPEC[type(record)]; record_id = getattr(record, id_field)
        if not _view_source_allowed(view_kind, record, now):
            _fail(OperationsErrorCode.SOURCE_NOT_AUTHORIZED)
        ids.append(record_id)
    if len(policies) != 1: _fail(OperationsErrorCode.POLICY_CONFLICT)
    if len(ids) != len(set(ids)): _fail(OperationsErrorCode.DUPLICATE_IDENTIFIER)
    return DerivedOperationsView(view_kind, None if scope.global_scope else scope.tenant_context.tenant_id,
                                 tuple(sorted(ids)))


_TASK_RUN_STATUS = {"queued": JobLifecycleStatus.ACCEPTED, "running": JobLifecycleStatus.RUNNING,
                    "success": JobLifecycleStatus.SUCCEEDED, "error": JobLifecycleStatus.FAILED,
                    "aborted": JobLifecycleStatus.CANCELLED, "skipped": JobLifecycleStatus.INVALIDATED}


def adapt_current_task_run(source: Mapping[str, object], *, run_id: str,
                           request: JobRequest,
                           idempotency_evaluation: IdempotencyEvaluation,
                           status_as_of: datetime) -> JobRun:
    """Read-only compatibility seam; canonical Tenant and IDs are mandatory.

    Current ``TaskRun`` rows have owner strings, numeric-free UUIDs, raw result
    and error bodies, and no Tenant Context or idempotency contract.  Therefore
    only its literal status/timing may be mapped after the caller supplies an
    already-authorized canonical Job Request and a new safe operations Run ID.
    """
    validate_job_run_id(run_id)
    if type(source) is not dict or not _valid(request, JobRequest):
        _fail(OperationsErrorCode.UNSUPPORTED_ADAPTER)
    if set(source) - {"status", "started_at", "finished_at"}:
        _fail(OperationsErrorCode.UNSUPPORTED_ADAPTER)
    status = _TASK_RUN_STATUS.get(source.get("status"))
    if status is None: _fail(OperationsErrorCode.UNSUPPORTED_ADAPTER)
    started = source.get("started_at")
    if type(started) is not datetime: _fail(OperationsErrorCode.UNSUPPORTED_ADAPTER)
    accepted = _utc(started)
    if accepted < request.requested_at: _fail(OperationsErrorCode.UNSUPPORTED_ADAPTER)
    base = create_job_run(run_id=run_id, request=request,
                          idempotency_evaluation=idempotency_evaluation,
                          status=JobLifecycleStatus.ACCEPTED, accepted_at=accepted)
    if status is JobLifecycleStatus.ACCEPTED: return base
    running = transition_job_run(base, to_status=JobLifecycleStatus.RUNNING, at=accepted)
    if status is JobLifecycleStatus.RUNNING: return running
    finished = source.get("finished_at")
    if type(finished) is not datetime: _fail(OperationsErrorCode.UNSUPPORTED_ADAPTER)
    terminal = _utc(finished)
    if terminal > _utc(status_as_of): _fail(OperationsErrorCode.UNSUPPORTED_ADAPTER)
    if status is JobLifecycleStatus.CANCELLED:
        return transition_job_run(running, to_status=status, at=terminal,
                                  cancellation_reference="legacy:task_run:aborted")
    return transition_job_run(running, to_status=status, at=terminal)


def adapt_current_global_feature(*, flag_key: str, enabled: object,
                                 feature_flag_id: str, effective_at: datetime,
                                 authority_decision: AuthorizationDecision,
                                 actor_ref: str, environment_code: str,
                                 service_code: str, policy_id: str,
                                 policy_version: str) -> FeatureFlagDefinition:
    """Adapt only a literal boolean from current ``features.json`` as global.

    No Tenant is inferred from process, file, owner, hostname, or environment.
    """
    if type(enabled) is not bool: _fail(OperationsErrorCode.UNSUPPORTED_ADAPTER)
    scope = OperationsScope(None, environment_code, service_code, True)
    return create_feature_flag(feature_flag_id=feature_flag_id, flag_key=flag_key,
        scope=scope, status=FeatureStatus.ACTIVE,
        mode=FeatureEvaluationMode.ON if enabled else FeatureEvaluationMode.OFF,
        default_enabled=False, effective_at=effective_at, actor_ref=actor_ref,
        authority_decision=authority_decision, policy_id=policy_id,
        policy_version=policy_version)
