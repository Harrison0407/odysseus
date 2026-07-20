"""Pure MarketMatch Billing, Entitlements & Usage Attribution Kernel V1.

The module performs no persistence, metering, provider, payment, network, or
filesystem operation.  Callers provide every timestamp and every measured or
commercial value explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from enum import Enum
import re
from typing import Any, Mapping, NoReturn, Sequence

from src.marketmatch_authority import (
    AuthorityScope, AuthorizationDecision, ProjectionBehavior,
    ResourceClassification, project_authorized_fields,
)
from src.marketmatch_evidence import validate_evidence_id
from src.marketmatch_operational_context import validate_context_id
from src.marketmatch_tenancy import (
    MembershipEvaluation, MembershipRecord, TenantContext,
    validate_organization_id, validate_tenant_id,
)
from src.marketmatch_truth_accountability import (
    ApprovalRecord, ApprovalStatus, DecisionRecord, OperationalEvent, TargetType,
    validate_approval_id, validate_decision_id, validate_event_id,
)
from src.marketmatch_work_orchestration import (
    Assignment, ExecutionAttempt, WorkItem, validate_assignment_id,
    validate_attempt_id, validate_work_id,
)


BILLING_CONTRACT_VERSION = "marketmatch-billing-entitlements-usage-v1"
BILLING_POLICY_VERSION = "marketmatch-billing-policy-v1"
MAX_RECORDS_PER_KIND = 2048
MAX_REFERENCES = 256
MAX_IDENTIFIER_BYTES = 160
MAX_CODE_BYTES = 128
MAX_AMOUNT = Decimal("999999999999999999.999999")
MONEY_QUANTUM = Decimal("0.000001")
QUANTITY_QUANTUM = Decimal("0.000001")

_CODE_RE = re.compile(r"[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*\Z", re.ASCII)
_SHA_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_HOST_RE = re.compile(r"(?:[a-z0-9-]+\.)+[a-z]{2,}\Z", re.ASCII | re.IGNORECASE)
_SENSITIVE = frozenset({
    "address", "amount", "api", "apikey", "bank", "bearer", "card", "cookie",
    "cost", "credential", "currency", "dollar", "email", "eur", "header",
    "hostname", "invoice", "key", "margin", "password", "payment", "price",
    "request", "secret", "session", "tax", "token", "usd", "url",
})


class BillingErrorCode(str, Enum):
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    INVALID_MONEY = "INVALID_MONEY"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    INVALID_RECORD = "INVALID_RECORD"
    INVALID_VERSION = "INVALID_VERSION"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    INVALID_SCOPE = "INVALID_SCOPE"
    INVALID_AUTHORITY = "INVALID_AUTHORITY"
    INVALID_PROJECTION = "INVALID_PROJECTION"
    INVALID_AUDIT = "INVALID_AUDIT"
    MISSING_REFERENCE = "MISSING_REFERENCE"
    DUPLICATE_IDENTIFIER = "DUPLICATE_IDENTIFIER"
    TENANT_CONFLICT = "TENANT_CONFLICT"
    CONTEXT_CONFLICT = "CONTEXT_CONFLICT"
    POLICY_CONFLICT = "POLICY_CONFLICT"
    CLASSIFICATION_CONFLICT = "CLASSIFICATION_CONFLICT"
    CURRENCY_CONFLICT = "CURRENCY_CONFLICT"
    UNIT_CONFLICT = "UNIT_CONFLICT"
    CHRONOLOGY_CONFLICT = "CHRONOLOGY_CONFLICT"
    PLAN_CONFLICT = "PLAN_CONFLICT"
    ENTITLEMENT_CONFLICT = "ENTITLEMENT_CONFLICT"
    NOT_ENTITLED = "NOT_ENTITLED"
    ALLOWANCE_EXCEEDED = "ALLOWANCE_EXCEEDED"
    CREDIT_EXCEEDED = "CREDIT_EXCEEDED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_INVALID = "APPROVAL_INVALID"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    DUPLICATE_USAGE = "DUPLICATE_USAGE"
    DUPLICATE_REVERSAL = "DUPLICATE_REVERSAL"
    ADJUSTMENT_EXCEEDED = "ADJUSTMENT_EXCEEDED"
    SOURCE_NOT_AUTHORIZED = "SOURCE_NOT_AUTHORIZED"
    COLLECTION_LIMIT_EXCEEDED = "COLLECTION_LIMIT_EXCEEDED"
    UNSUPPORTED_ADAPTER = "UNSUPPORTED_ADAPTER"


class BillingContractError(ValueError):
    def __init__(self, code: BillingErrorCode):
        self.code = code if type(code) is BillingErrorCode else BillingErrorCode.INVALID_RECORD
        super().__init__(self.code.value)


class Currency(str, Enum):
    USD = "USD"
    DOP = "DOP"
    EUR = "EUR"


class UsageUnit(str, Enum):
    TOKEN = "TOKEN"
    MILLISECOND = "MILLISECOND"
    BYTE = "BYTE"
    EXECUTION = "EXECUTION"


class RecordStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"
    INVALIDATED = "INVALIDATED"


class PlanStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class BillingProfileStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DEACTIVATED = "DEACTIVATED"


class PeriodType(str, Enum):
    ONE_TIME = "ONE_TIME"
    MONTHLY = "MONTHLY"
    CONTRACT_PERIOD = "CONTRACT_PERIOD"


class RolloverPolicy(str, Enum):
    NONE = "NONE"


class CreditType(str, Enum):
    EXPLORATION = "EXPLORATION"
    PREPAID = "PREPAID"


class CreditMovementType(str, Enum):
    GRANT = "GRANT"
    PURCHASE = "PURCHASE"
    CONSUMPTION = "CONSUMPTION"
    REVERSAL = "REVERSAL"
    EXPIRATION = "EXPIRATION"
    ADJUSTMENT = "ADJUSTMENT"


class PricingMechanism(str, Enum):
    INCLUDED_ALLOWANCE = "INCLUDED_ALLOWANCE"
    EXPLORATION_CREDIT = "EXPLORATION_CREDIT"
    PREPAID_CREDIT = "PREPAID_CREDIT"
    PASS_THROUGH = "PASS_THROUGH"
    PASS_THROUGH_WITH_MARGIN = "PASS_THROUGH_WITH_MARGIN"
    ENTERPRISE_CHARGEBACK = "ENTERPRISE_CHARGEBACK"
    NO_CHARGE = "NO_CHARGE"


class MarginMethod(str, Enum):
    FIXED_AMOUNT = "FIXED_AMOUNT"
    PERCENT_OF_PROVIDER_COST = "PERCENT_OF_PROVIDER_COST"


class ThresholdBasis(str, Enum):
    ESTIMATED_PROVIDER_COST = "ESTIMATED_PROVIDER_COST"
    ESTIMATED_CUSTOMER_CHARGE = "ESTIMATED_CUSTOMER_CHARGE"
    ESTIMATED_USAGE = "ESTIMATED_USAGE"
    ACTUAL_PROVIDER_COST = "ACTUAL_PROVIDER_COST"
    ACTUAL_CUSTOMER_CHARGE = "ACTUAL_CUSTOMER_CHARGE"


class UsageStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CostSource(str, Enum):
    REPORTED_BY_PROVIDER = "REPORTED_BY_PROVIDER"
    CONTRACT_RATE = "CONTRACT_RATE"
    INTERNAL_RATE = "INTERNAL_RATE"
    MEASURED_LOCAL_COST = "MEASURED_LOCAL_COST"
    UNKNOWN = "UNKNOWN"


class ChargeStatus(str, Enum):
    CALCULATED = "CALCULATED"
    WAIVED = "WAIVED"
    REVERSED = "REVERSED"
    INVALIDATED = "INVALIDATED"


class AdjustmentType(str, Enum):
    USAGE_CORRECTION = "USAGE_CORRECTION"
    CREDIT_REVERSAL = "CREDIT_REVERSAL"
    COST_CORRECTION = "COST_CORRECTION"
    CHARGE_REVERSAL = "CHARGE_REVERSAL"
    CHARGE_WAIVER = "CHARGE_WAIVER"
    INVALIDATION = "INVALIDATION"


class AdjustmentTarget(str, Enum):
    USAGE = "USAGE"
    CREDIT_MOVEMENT = "CREDIT_MOVEMENT"
    PROVIDER_COST = "PROVIDER_COST"
    CUSTOMER_CHARGE = "CUSTOMER_CHARGE"


class BudgetBasis(str, Enum):
    PROVIDER_COST = "PROVIDER_COST"
    CUSTOMER_CHARGE = "CUSTOMER_CHARGE"


class BudgetEnforcement(str, Enum):
    HARD = "HARD"
    SOFT = "SOFT"


class EvaluationReason(str, Enum):
    ENTITLED = "ENTITLED"
    NOT_ENTITLED = "NOT_ENTITLED"
    INACTIVE = "INACTIVE"
    NOT_YET_EFFECTIVE = "NOT_YET_EFFECTIVE"
    EXPIRED = "EXPIRED"
    CONFLICT = "CONFLICT"
    WITHIN_ALLOWANCE = "WITHIN_ALLOWANCE"
    ALLOWANCE_EXCEEDED = "ALLOWANCE_EXCEEDED"
    APPROVAL_NOT_REQUIRED = "APPROVAL_NOT_REQUIRED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_SATISFIED = "APPROVAL_SATISFIED"
    WITHIN_BUDGET = "WITHIN_BUDGET"
    BUDGET_WARNING = "BUDGET_WARNING"
    BUDGET_DENIED = "BUDGET_DENIED"
    OVERRIDE_SATISFIED = "OVERRIDE_SATISFIED"


def _fail(code: BillingErrorCode) -> NoReturn:
    raise BillingContractError(code) from None


def _parts(value: str) -> frozenset[str]:
    return frozenset(item for item in re.split(r"[._:-]+", value.lower()) if item)


def _code(value: object, error: BillingErrorCode = BillingErrorCode.INVALID_RECORD,
          *, sensitive: bool = True, maximum: int = MAX_CODE_BYTES) -> str:
    if type(value) is not str or not value or _CODE_RE.fullmatch(value) is None:
        _fail(error)
    try:
        if len(value.encode("ascii")) > maximum:
            _fail(error)
    except UnicodeError:
        _fail(error)
    if sensitive and (_parts(value) & _SENSITIVE):
        _fail(error)
    return value


def _record_id(value: object, prefix: str) -> str:
    _code(value, BillingErrorCode.INVALID_IDENTIFIER, maximum=MAX_IDENTIFIER_BYTES)
    opaque = value[len(prefix):] if type(value) is str and value.startswith(prefix) else ""
    if (not opaque or value.count(":") < 2 or not any("a" <= c <= "z" for c in opaque)
            or _SHA_RE.fullmatch(opaque) is not None or _HOST_RE.fullmatch(opaque) is not None):
        _fail(BillingErrorCode.INVALID_IDENTIFIER)
    return value


def validate_billing_profile_id(v: object) -> str: return _record_id(v, "bprof1:")
def validate_plan_id(v: object) -> str: return _record_id(v, "plan1:")
def validate_plan_version_id(v: object) -> str: return _record_id(v, "planv1:")
def validate_entitlement_id(v: object) -> str: return _record_id(v, "ent1:")
def validate_allowance_id(v: object) -> str: return _record_id(v, "allow1:")
def validate_credit_account_id(v: object) -> str: return _record_id(v, "cracct1:")
def validate_credit_movement_id(v: object) -> str: return _record_id(v, "crmov1:")
def validate_estimate_id(v: object) -> str: return _record_id(v, "uest1:")
def validate_usage_id(v: object) -> str: return _record_id(v, "usage1:")
def validate_provider_cost_id(v: object) -> str: return _record_id(v, "pcost1:")
def validate_customer_charge_id(v: object) -> str: return _record_id(v, "charge1:")
def validate_adjustment_id(v: object) -> str: return _record_id(v, "adj1:")
def validate_threshold_id(v: object) -> str: return _record_id(v, "thresh1:")
def validate_budget_id(v: object) -> str: return _record_id(v, "budget1:")
def validate_billing_audit_id(v: object) -> str: return _record_id(v, "baudit1:")


def _utc(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(BillingErrorCode.INVALID_TIMESTAMP)
    try: return value.astimezone(timezone.utc)
    except (OverflowError, ValueError): _fail(BillingErrorCode.INVALID_TIMESTAMP)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _decimal(value: object, *, money: bool = False, allow_zero: bool = True) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        _fail(BillingErrorCode.INVALID_MONEY if money else BillingErrorCode.INVALID_QUANTITY)
    if value < 0 or (not allow_zero and value == 0) or abs(value) > MAX_AMOUNT:
        _fail(BillingErrorCode.INVALID_MONEY if money else BillingErrorCode.INVALID_QUANTITY)
    quantum = MONEY_QUANTUM if money else QUANTITY_QUANTUM
    if value.as_tuple().exponent < quantum.as_tuple().exponent:
        _fail(BillingErrorCode.INVALID_MONEY if money else BillingErrorCode.INVALID_QUANTITY)
    try: return value.quantize(quantum, rounding=ROUND_HALF_EVEN)
    except InvalidOperation: _fail(BillingErrorCode.INVALID_MONEY if money else BillingErrorCode.INVALID_QUANTITY)


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: Currency
    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", _decimal(self.amount, money=True))
        if type(self.currency) is not Currency: _fail(BillingErrorCode.INVALID_MONEY)
    def serialize(self) -> str: return f"{self.amount:.6f} {self.currency.value}"
    def add(self, other: "Money") -> "Money":
        if type(other) is not Money or other.currency is not self.currency:
            _fail(BillingErrorCode.CURRENCY_CONFLICT)
        return Money(self.amount + other.amount, self.currency)


@dataclass(frozen=True, slots=True)
class UsageQuantity:
    quantity: Decimal
    unit: UsageUnit
    def __post_init__(self) -> None:
        object.__setattr__(self, "quantity", _decimal(self.quantity))
        if type(self.unit) is not UsageUnit: _fail(BillingErrorCode.INVALID_QUANTITY)
    def add(self, other: "UsageQuantity") -> "UsageQuantity":
        if type(other) is not UsageQuantity or other.unit is not self.unit:
            _fail(BillingErrorCode.UNIT_CONFLICT)
        return UsageQuantity(self.quantity + other.quantity, self.unit)


def _valid_tenant_context(value: object) -> bool:
    try:
        return (type(value) is TenantContext and value._integrity == value._tuple()
                and value.scope.organization_id == value.organization_id
                and value.scope.product_id is not None and value.scope.workspace_id is not None
                and value.scope.project_id is not None and value.scope.owner_party_id is not None
                and value.scope.global_scope is False)
    except Exception: return False


def _scope_for(context: TenantContext, resource_id: str) -> AuthorityScope:
    return AuthorityScope(context.organization_id, context.scope.product_id,
                          context.scope.workspace_id, context.scope.project_id,
                          resource_id, context.scope.owner_party_id, False)


def _context_tuple(context: TenantContext) -> tuple[object, ...]:
    if not _valid_tenant_context(context): _fail(BillingErrorCode.INVALID_SCOPE)
    return context._tuple()


def _same_context(left: TenantContext, right: TenantContext) -> bool:
    return _valid_tenant_context(left) and _valid_tenant_context(right) and left._tuple() == right._tuple()


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
            _code(value, BillingErrorCode.INVALID_AUTHORITY, sensitive=False)
        object.__setattr__(self, "_integrity", self._tuple())
    def _tuple(self) -> tuple[object, ...]:
        return (self.principal_ref, self.capability_code, self.resource_type,
                self.resource_id, self.policy_id, self.policy_version)


def _authority(decision: object, *, resource_type: str, resource_id: str,
               context: TenantContext, capability: str, actor_ref: str) -> AuthorityProvenance:
    try: project_authorized_fields({}, decision, behavior=ProjectionBehavior.OMIT)
    except Exception: _fail(BillingErrorCode.INVALID_AUTHORITY)
    if (type(decision) is not AuthorizationDecision or decision.allowed is not True
            or decision.resource_type != resource_type or decision.resource_id != resource_id
            or decision.effective_scope != _scope_for(context, resource_id)
            or decision.policy_id != context.policy_id or decision.policy_version != context.policy_version
            or decision.action_code != capability or decision.principal_ref != actor_ref):
        _fail(BillingErrorCode.INVALID_AUTHORITY)
    return AuthorityProvenance(actor_ref, capability, resource_type, resource_id,
                               context.policy_id, context.policy_version)


def _record_tuple(value: object, cls: type) -> tuple[object, ...]:
    return tuple(getattr(value, name) for name in cls.__dataclass_fields__ if name != "_integrity")


def _new(cls: type, values: Mapping[str, object]) -> object:
    item = object.__new__(cls)
    for name in cls.__dataclass_fields__:
        if name != "_integrity": object.__setattr__(item, name, values[name])
    object.__setattr__(item, "_integrity", _record_tuple(item, cls))
    return item


def _valid(value: object, cls: type) -> bool:
    try: return type(value) is cls and value._integrity == _record_tuple(value, cls)
    except Exception: return False


def _version(version: object) -> str:
    if version != BILLING_CONTRACT_VERSION: _fail(BillingErrorCode.INVALID_VERSION)
    return version


def _policy(code: object) -> str:
    return _code(code, BillingErrorCode.INVALID_RECORD, sensitive=False)


def _classification(value: object) -> ResourceClassification:
    if type(value) is not ResourceClassification: _fail(BillingErrorCode.INVALID_RECORD)
    return value


def _refs(values: object, validator) -> tuple[str, ...]:
    if type(values) is not tuple or len(values) > MAX_REFERENCES: _fail(BillingErrorCode.INVALID_RECORD)
    out: list[str] = []
    for value in values:
        try: out.append(validator(value))
        except Exception: _fail(BillingErrorCode.INVALID_RECORD)
    if len(out) != len(set(out)): _fail(BillingErrorCode.DUPLICATE_IDENTIFIER)
    return tuple(sorted(out))


class _FactoryRecord:
    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs; _fail(BillingErrorCode.INVALID_RECORD)


@dataclass(frozen=True, slots=True, init=False)
class BillingProfile(_FactoryRecord):
    billing_profile_id: str; tenant_id: str; organization_id: str
    status: BillingProfileStatus; effective_at: datetime; expires_at: datetime | None
    default_currency: Currency; pricing_policy_id: str; plan_version_id: str
    tenant_context: TenantContext; classification: ResourceClassification
    authority_provenance: AuthorityProvenance; contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_billing_profile(*, billing_profile_id: str, tenant_context: TenantContext,
                           status: BillingProfileStatus, effective_at: datetime,
                           default_currency: Currency, pricing_policy_id: str,
                           plan_version_id: str, actor_ref: str,
                           authority_decision: AuthorizationDecision,
                           classification: ResourceClassification,
                           expires_at: datetime | None = None,
                           contract_version: str = BILLING_CONTRACT_VERSION) -> BillingProfile:
    validate_billing_profile_id(billing_profile_id); validate_plan_version_id(plan_version_id)
    _context_tuple(tenant_context); _version(contract_version); _classification(classification)
    if type(status) is not BillingProfileStatus or type(default_currency) is not Currency: _fail(BillingErrorCode.INVALID_RECORD)
    start = _utc(effective_at); end = None if expires_at is None else _utc(expires_at)
    if end is not None and end <= start: _fail(BillingErrorCode.CHRONOLOGY_CONFLICT)
    _policy(pricing_policy_id); _code(actor_ref, BillingErrorCode.INVALID_RECORD)
    auth = _authority(authority_decision, resource_type="billing_profile", resource_id=billing_profile_id,
                      context=tenant_context, capability="billing.profile.create", actor_ref=actor_ref)
    return _new(BillingProfile, {"billing_profile_id": billing_profile_id,
        "tenant_id": tenant_context.tenant_id, "organization_id": tenant_context.organization_id,
        "status": status, "effective_at": start, "expires_at": end,
        "default_currency": default_currency, "pricing_policy_id": pricing_policy_id,
        "plan_version_id": plan_version_id, "tenant_context": tenant_context,
        "classification": classification, "authority_provenance": auth,
        "contract_version": contract_version})


@dataclass(frozen=True, slots=True, init=False)
class Plan(_FactoryRecord):
    plan_id: str; tenant_id: str; display_label: str; status: PlanStatus
    created_at: datetime; policy_id: str; policy_version: str; contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_plan(*, plan_id: str, tenant_id: str, display_label: str, status: PlanStatus,
                created_at: datetime, policy_id: str, policy_version: str,
                contract_version: str = BILLING_CONTRACT_VERSION) -> Plan:
    validate_plan_id(plan_id); validate_tenant_id(tenant_id); _version(contract_version)
    if type(status) is not PlanStatus or type(display_label) is not str or not display_label.strip() or len(display_label) > 160 or any(ord(c) < 32 for c in display_label): _fail(BillingErrorCode.INVALID_RECORD)
    return _new(Plan, {"plan_id": plan_id, "tenant_id": tenant_id,
        "display_label": display_label, "status": status, "created_at": _utc(created_at),
        "policy_id": _policy(policy_id), "policy_version": _policy(policy_version),
        "contract_version": contract_version})


@dataclass(frozen=True, slots=True, init=False)
class PlanVersion(_FactoryRecord):
    plan_version_id: str; plan_id: str; tenant_id: str; version_number: int
    effective_at: datetime; expires_at: datetime | None; entitlement_ids: tuple[str, ...]
    allowance_ids: tuple[str, ...]; billing_policy_ids: tuple[str, ...]
    threshold_ids: tuple[str, ...]; currency: Currency; supersedes_id: str | None
    policy_id: str; policy_version: str; contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_plan_version(*, plan_version_id: str, plan_id: str, tenant_id: str,
                        version_number: int, effective_at: datetime, currency: Currency,
                        policy_id: str, policy_version: str,
                        entitlement_ids: tuple[str, ...] = (), allowance_ids: tuple[str, ...] = (),
                        billing_policy_ids: tuple[str, ...] = (), threshold_ids: tuple[str, ...] = (),
                        expires_at: datetime | None = None, supersedes_id: str | None = None,
                        contract_version: str = BILLING_CONTRACT_VERSION) -> PlanVersion:
    validate_plan_version_id(plan_version_id); validate_plan_id(plan_id); validate_tenant_id(tenant_id); _version(contract_version)
    if type(version_number) is not int or version_number < 1 or type(currency) is not Currency: _fail(BillingErrorCode.INVALID_RECORD)
    start = _utc(effective_at); end = None if expires_at is None else _utc(expires_at)
    if end is not None and end <= start: _fail(BillingErrorCode.CHRONOLOGY_CONFLICT)
    if supersedes_id is not None:
        validate_plan_version_id(supersedes_id)
        if supersedes_id == plan_version_id: _fail(BillingErrorCode.INVALID_RECORD)
    policies = tuple(sorted({_policy(item) for item in billing_policy_ids}))
    return _new(PlanVersion, {"plan_version_id": plan_version_id, "plan_id": plan_id,
        "tenant_id": tenant_id, "version_number": version_number, "effective_at": start,
        "expires_at": end, "entitlement_ids": _refs(entitlement_ids, validate_entitlement_id),
        "allowance_ids": _refs(allowance_ids, validate_allowance_id), "billing_policy_ids": policies,
        "threshold_ids": _refs(threshold_ids, validate_threshold_id), "currency": currency,
        "supersedes_id": supersedes_id, "policy_id": _policy(policy_id),
        "policy_version": _policy(policy_version), "contract_version": contract_version})


@dataclass(frozen=True, slots=True, init=False)
class Entitlement(_FactoryRecord):
    entitlement_id: str; tenant_context: TenantContext; capability_code: str; status: RecordStatus
    effective_at: datetime; expires_at: datetime | None; plan_version_id: str | None
    grant_source: str; classification: ResourceClassification
    authority_provenance: AuthorityProvenance; contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_entitlement(*, entitlement_id: str, tenant_context: TenantContext,
                       capability_code: str, status: RecordStatus, effective_at: datetime,
                       grant_source: str, actor_ref: str, authority_decision: AuthorizationDecision,
                       classification: ResourceClassification, plan_version_id: str | None = None,
                       expires_at: datetime | None = None,
                       contract_version: str = BILLING_CONTRACT_VERSION) -> Entitlement:
    validate_entitlement_id(entitlement_id); _context_tuple(tenant_context); _version(contract_version); _classification(classification)
    if type(status) is not RecordStatus: _fail(BillingErrorCode.INVALID_RECORD)
    if plan_version_id is not None: validate_plan_version_id(plan_version_id)
    start = _utc(effective_at); end = None if expires_at is None else _utc(expires_at)
    if end is not None and end <= start: _fail(BillingErrorCode.CHRONOLOGY_CONFLICT)
    _code(capability_code); _code(grant_source); _code(actor_ref)
    auth = _authority(authority_decision, resource_type="billing_entitlement", resource_id=entitlement_id,
                      context=tenant_context, capability="billing.entitlement.grant", actor_ref=actor_ref)
    return _new(Entitlement, {"entitlement_id": entitlement_id, "tenant_context": tenant_context,
        "capability_code": capability_code, "status": status, "effective_at": start,
        "expires_at": end, "plan_version_id": plan_version_id, "grant_source": grant_source,
        "classification": classification, "authority_provenance": auth, "contract_version": contract_version})


@dataclass(frozen=True, slots=True, init=False)
class EntitlementEvaluation(_FactoryRecord):
    entitled: bool; reason_code: EvaluationReason; entitlement_id: str | None
    tenant_id: str; capability_code: str; authority_granted: bool
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def _ent_eval(entitled: bool, reason: EvaluationReason, entitlement_id: str | None,
              tenant_id: str, capability: str) -> EntitlementEvaluation:
    values = {"entitled": entitled, "reason_code": reason, "entitlement_id": entitlement_id,
              "tenant_id": tenant_id, "capability_code": capability, "authority_granted": False}
    return _new(EntitlementEvaluation, values)


def evaluate_entitlement(entitlements: Sequence[Entitlement], *, tenant_context: TenantContext,
                         capability_code: str, as_of: datetime,
                         plan_version: PlanVersion | None = None) -> EntitlementEvaluation:
    _context_tuple(tenant_context); _code(capability_code); now = _utc(as_of)
    if type(entitlements) not in (list, tuple) or len(entitlements) > MAX_RECORDS_PER_KIND: _fail(BillingErrorCode.INVALID_RECORD)
    if plan_version is not None:
        if not _valid(plan_version, PlanVersion) or plan_version.tenant_id != tenant_context.tenant_id: _fail(BillingErrorCode.PLAN_CONFLICT)
        if now < plan_version.effective_at or (plan_version.expires_at is not None and now >= plan_version.expires_at):
            return _ent_eval(False, EvaluationReason.NOT_YET_EFFECTIVE if now < plan_version.effective_at else EvaluationReason.EXPIRED, None, tenant_context.tenant_id, capability_code)
    candidates: list[Entitlement] = []
    for item in entitlements:
        if not _valid(item, Entitlement): _fail(BillingErrorCode.INVALID_RECORD)
        if not _same_context(item.tenant_context, tenant_context): continue
        if item.capability_code != capability_code: continue
        if plan_version is not None and item.entitlement_id not in plan_version.entitlement_ids: continue
        if item.status is RecordStatus.ACTIVE and item.effective_at <= now and (item.expires_at is None or now < item.expires_at): candidates.append(item)
    if not candidates: return _ent_eval(False, EvaluationReason.NOT_ENTITLED, None, tenant_context.tenant_id, capability_code)
    if len(candidates) != 1: return _ent_eval(False, EvaluationReason.CONFLICT, None, tenant_context.tenant_id, capability_code)
    return _ent_eval(True, EvaluationReason.ENTITLED, candidates[0].entitlement_id, tenant_context.tenant_id, capability_code)


@dataclass(frozen=True, slots=True, init=False)
class Allowance(_FactoryRecord):
    allowance_id: str; plan_version_id: str; tenant_context: TenantContext
    capability_code: str; included: UsageQuantity; period_type: PeriodType
    period_start: datetime; period_end: datetime; rollover_policy: RolloverPolicy
    policy_id: str; policy_version: str; contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_allowance(*, allowance_id: str, plan_version_id: str, tenant_context: TenantContext,
                     capability_code: str, included: UsageQuantity, period_type: PeriodType,
                     period_start: datetime, period_end: datetime,
                     rollover_policy: RolloverPolicy = RolloverPolicy.NONE,
                     policy_id: str = "billing.allowance", policy_version: str = BILLING_POLICY_VERSION,
                     contract_version: str = BILLING_CONTRACT_VERSION) -> Allowance:
    validate_allowance_id(allowance_id); validate_plan_version_id(plan_version_id); _context_tuple(tenant_context); _version(contract_version)
    if type(included) is not UsageQuantity or type(period_type) is not PeriodType or type(rollover_policy) is not RolloverPolicy: _fail(BillingErrorCode.INVALID_RECORD)
    start, end = _utc(period_start), _utc(period_end)
    if end <= start: _fail(BillingErrorCode.CHRONOLOGY_CONFLICT)
    return _new(Allowance, {"allowance_id": allowance_id, "plan_version_id": plan_version_id,
        "tenant_context": tenant_context, "capability_code": _code(capability_code), "included": included,
        "period_type": period_type, "period_start": start, "period_end": end,
        "rollover_policy": rollover_policy, "policy_id": _policy(policy_id),
        "policy_version": _policy(policy_version), "contract_version": contract_version})


@dataclass(frozen=True, slots=True, init=False)
class AllowanceEvaluation(_FactoryRecord):
    allowed: bool; reason_code: EvaluationReason; consumed: UsageQuantity; remaining: UsageQuantity
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True, init=False)
class CreditAccount(_FactoryRecord):
    account_id: str; tenant_id: str; organization_id: str; credit_type: CreditType
    status: RecordStatus; unit: UsageUnit; policy_id: str; policy_version: str; contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_credit_account(*, account_id: str, tenant_id: str, organization_id: str,
                          credit_type: CreditType, status: RecordStatus, unit: UsageUnit,
                          policy_id: str, policy_version: str,
                          contract_version: str = BILLING_CONTRACT_VERSION) -> CreditAccount:
    validate_credit_account_id(account_id); validate_tenant_id(tenant_id); validate_organization_id(organization_id); _version(contract_version)
    if type(credit_type) is not CreditType or type(status) is not RecordStatus or type(unit) is not UsageUnit: _fail(BillingErrorCode.INVALID_RECORD)
    return _new(CreditAccount, {"account_id": account_id, "tenant_id": tenant_id,
        "organization_id": organization_id, "credit_type": credit_type, "status": status,
        "unit": unit, "policy_id": _policy(policy_id), "policy_version": _policy(policy_version),
        "contract_version": contract_version})


@dataclass(frozen=True, slots=True, init=False)
class CreditMovement(_FactoryRecord):
    movement_id: str; account_id: str; tenant_id: str; movement_type: CreditMovementType
    quantity: UsageQuantity; occurred_at: datetime; source_ref: str
    usage_event_id: str | None; adjustment_id: str | None; expires_at: datetime | None
    policy_id: str; policy_version: str; authority_provenance: AuthorityProvenance
    contract_version: str; _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_credit_movement(*, movement_id: str, account: CreditAccount, tenant_context: TenantContext,
                           movement_type: CreditMovementType, quantity: UsageQuantity,
                           occurred_at: datetime, source_ref: str, actor_ref: str,
                           authority_decision: AuthorizationDecision, usage_event_id: str | None = None,
                           adjustment_id: str | None = None, expires_at: datetime | None = None,
                           contract_version: str = BILLING_CONTRACT_VERSION) -> CreditMovement:
    validate_credit_movement_id(movement_id); _context_tuple(tenant_context); _version(contract_version)
    if not _valid(account, CreditAccount) or account.tenant_id != tenant_context.tenant_id or account.organization_id != tenant_context.organization_id: _fail(BillingErrorCode.TENANT_CONFLICT)
    if type(movement_type) is not CreditMovementType or type(quantity) is not UsageQuantity or quantity.unit is not account.unit: _fail(BillingErrorCode.INVALID_RECORD)
    when = _utc(occurred_at); expiry = None if expires_at is None else _utc(expires_at)
    if expiry is not None and expiry <= when: _fail(BillingErrorCode.CHRONOLOGY_CONFLICT)
    if usage_event_id is not None: validate_usage_id(usage_event_id)
    if adjustment_id is not None: validate_adjustment_id(adjustment_id)
    _code(source_ref); _code(actor_ref)
    auth = _authority(authority_decision, resource_type="billing_credit_movement", resource_id=movement_id,
                      context=tenant_context, capability="billing.credit.record", actor_ref=actor_ref)
    return _new(CreditMovement, {"movement_id": movement_id, "account_id": account.account_id,
        "tenant_id": account.tenant_id, "movement_type": movement_type, "quantity": quantity,
        "occurred_at": when, "source_ref": source_ref, "usage_event_id": usage_event_id,
        "adjustment_id": adjustment_id, "expires_at": expiry, "policy_id": account.policy_id,
        "policy_version": account.policy_version, "authority_provenance": auth,
        "contract_version": contract_version})


@dataclass(frozen=True, slots=True, init=False)
class CreditBalance(_FactoryRecord):
    account_id: str; tenant_id: str; quantity: UsageQuantity; as_of: datetime
    policy_id: str; policy_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def calculate_credit_balance(account: CreditAccount, movements: Sequence[CreditMovement], *, as_of: datetime) -> CreditBalance:
    if not _valid(account, CreditAccount) or account.status is not RecordStatus.ACTIVE: _fail(BillingErrorCode.INVALID_RECORD)
    now = _utc(as_of); total = Decimal("0")
    if type(movements) not in (list, tuple) or len(movements) > MAX_RECORDS_PER_KIND: _fail(BillingErrorCode.INVALID_RECORD)
    if any(not _valid(item, CreditMovement) for item in movements): _fail(BillingErrorCode.INVALID_RECORD)
    if len({item.movement_id for item in movements}) != len(movements): _fail(BillingErrorCode.DUPLICATE_IDENTIFIER)
    seen: set[str] = set()
    ordered = sorted(movements, key=lambda v: (v.occurred_at, v.movement_id))
    prior_consumption = Decimal("0")
    for item in ordered:
        if item.movement_id in seen: _fail(BillingErrorCode.DUPLICATE_IDENTIFIER)
        seen.add(item.movement_id)
        if item.account_id != account.account_id or item.tenant_id != account.tenant_id or item.quantity.unit is not account.unit or item.policy_id != account.policy_id or item.policy_version != account.policy_version: _fail(BillingErrorCode.TENANT_CONFLICT)
        if item.occurred_at > now: continue
        if item.movement_type is CreditMovementType.CONSUMPTION:
            available_then = sum((candidate.quantity.quantity for candidate in ordered
                if candidate.occurred_at <= item.occurred_at
                and candidate.movement_type in {CreditMovementType.GRANT, CreditMovementType.PURCHASE}
                and (candidate.expires_at is None or candidate.expires_at > item.occurred_at)), Decimal("0"))
            available_then += sum((candidate.quantity.quantity for candidate in ordered
                if candidate.occurred_at <= item.occurred_at
                and candidate.movement_type in {CreditMovementType.REVERSAL, CreditMovementType.ADJUSTMENT}), Decimal("0"))
            if prior_consumption + item.quantity.quantity > available_then:
                _fail(BillingErrorCode.CREDIT_EXCEEDED)
            prior_consumption += item.quantity.quantity
    for item in ordered:
        if item.occurred_at > now: continue
        expired = item.expires_at is not None and item.expires_at <= now
        if item.movement_type in {CreditMovementType.GRANT, CreditMovementType.PURCHASE} and not expired: total += item.quantity.quantity
        elif item.movement_type in {CreditMovementType.CONSUMPTION, CreditMovementType.EXPIRATION}: total -= item.quantity.quantity
        elif item.movement_type in {CreditMovementType.REVERSAL, CreditMovementType.ADJUSTMENT}: total += item.quantity.quantity
    total = max(Decimal("0"), total)
    return _new(CreditBalance, {"account_id": account.account_id, "tenant_id": account.tenant_id,
        "quantity": UsageQuantity(total, account.unit), "as_of": now,
        "policy_id": account.policy_id, "policy_version": account.policy_version})


@dataclass(frozen=True, slots=True)
class MarginPolicy:
    method: MarginMethod
    value: Decimal
    currency: Currency | None = None
    def __post_init__(self) -> None:
        if type(self.method) is not MarginMethod: _fail(BillingErrorCode.INVALID_RECORD)
        amount = _decimal(self.value, money=True)
        if self.method is MarginMethod.FIXED_AMOUNT and type(self.currency) is not Currency: _fail(BillingErrorCode.INVALID_RECORD)
        if self.method is MarginMethod.PERCENT_OF_PROVIDER_COST:
            if self.currency is not None or amount > Decimal("100.000000"): _fail(BillingErrorCode.INVALID_RECORD)
        object.__setattr__(self, "value", amount)


@dataclass(frozen=True, slots=True)
class BillingPolicy:
    policy_id: str
    policy_version: str
    mechanism: PricingMechanism
    margin: MarginPolicy | None = None
    def __post_init__(self) -> None:
        _policy(self.policy_id); _policy(self.policy_version)
        if type(self.mechanism) is not PricingMechanism: _fail(BillingErrorCode.INVALID_RECORD)
        if (self.mechanism is PricingMechanism.PASS_THROUGH_WITH_MARGIN) != (self.margin is not None): _fail(BillingErrorCode.INVALID_RECORD)


@dataclass(frozen=True, slots=True, init=False)
class ApprovalThreshold(_FactoryRecord):
    threshold_id: str; tenant_context: TenantContext; capability_code: str
    basis: ThresholdBasis; money_threshold: Money | None; usage_threshold: UsageQuantity | None
    approval_capability: str; effective_at: datetime; expires_at: datetime | None
    policy_id: str; policy_version: str; contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_approval_threshold(*, threshold_id: str, tenant_context: TenantContext,
                              capability_code: str, basis: ThresholdBasis,
                              approval_capability: str, effective_at: datetime,
                              policy_id: str, policy_version: str, money_threshold: Money | None = None,
                              usage_threshold: UsageQuantity | None = None, expires_at: datetime | None = None,
                              contract_version: str = BILLING_CONTRACT_VERSION) -> ApprovalThreshold:
    validate_threshold_id(threshold_id); _context_tuple(tenant_context); _version(contract_version)
    if type(basis) is not ThresholdBasis or (money_threshold is None) == (usage_threshold is None): _fail(BillingErrorCode.INVALID_RECORD)
    if basis is ThresholdBasis.ESTIMATED_USAGE and usage_threshold is None: _fail(BillingErrorCode.INVALID_RECORD)
    if basis is not ThresholdBasis.ESTIMATED_USAGE and money_threshold is None: _fail(BillingErrorCode.INVALID_RECORD)
    start = _utc(effective_at); end = None if expires_at is None else _utc(expires_at)
    if end is not None and end <= start: _fail(BillingErrorCode.CHRONOLOGY_CONFLICT)
    _code(approval_capability)
    if not approval_capability.startswith("approval."): _fail(BillingErrorCode.INVALID_RECORD)
    return _new(ApprovalThreshold, {"threshold_id": threshold_id, "tenant_context": tenant_context,
        "capability_code": _code(capability_code), "basis": basis, "money_threshold": money_threshold,
        "usage_threshold": usage_threshold, "approval_capability": _code(approval_capability),
        "effective_at": start, "expires_at": end, "policy_id": _policy(policy_id),
        "policy_version": _policy(policy_version), "contract_version": contract_version})


@dataclass(frozen=True, slots=True, init=False)
class ThresholdEvaluation(_FactoryRecord):
    approval_required: bool; satisfied: bool; reason_code: EvaluationReason
    threshold_id: str; approval_id: str | None
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def _approval_valid(approval: object, threshold: ApprovalThreshold, as_of: datetime) -> bool:
    try:
        authority = approval.authority_provenance
        expected = (approval.approval_id, approval.approval_type, approval.target_type,
                    approval.target_id, (approval.scope.organization_id, approval.scope.product_id,
                    approval.scope.workspace_id, approval.scope.project_id, approval.scope.resource_id,
                    approval.scope.owner_party_id, approval.scope.global_scope), approval.approver._integrity,
                    approval.status, approval.recorded_at, approval.effective_at, approval.expires_at,
                    approval.supporting_event_ids, approval.supporting_evidence_ids, approval.classification,
                    approval.visibility_policy_id, approval.visibility_policy_version,
                    approval.conditions_code, approval.reason_code,
                    (authority.principal_ref, authority.capability_code, authority.resource_type,
                     authority.resource_id, authority.policy_id, authority.policy_version),
                    approval.contract_version)
        return (type(approval) is ApprovalRecord and approval._integrity == expected
                and approval.status is ApprovalStatus.APPROVED
                and approval.target_type is TargetType.OPERATIONAL_CONTEXT
                and approval.target_id == threshold.tenant_context.project_context_id
                and approval.scope.organization_id == threshold.tenant_context.organization_id
                and approval.scope.product_id == threshold.tenant_context.scope.product_id
                and approval.scope.workspace_id == threshold.tenant_context.scope.workspace_id
                and approval.scope.project_id == threshold.tenant_context.scope.project_id
                and approval.visibility_policy_id == threshold.policy_id
                and approval.visibility_policy_version == threshold.policy_version
                and approval.effective_at <= as_of
                and (approval.expires_at is None or as_of < approval.expires_at)
                and approval.authority_provenance.capability_code == threshold.approval_capability)
    except Exception: return False


def evaluate_approval_threshold(threshold: ApprovalThreshold, *, as_of: datetime,
                                money: Money | None = None, quantity: UsageQuantity | None = None,
                                approvals: Sequence[ApprovalRecord] = ()) -> ThresholdEvaluation:
    if not _valid(threshold, ApprovalThreshold): _fail(BillingErrorCode.INVALID_RECORD)
    now = _utc(as_of)
    if now < threshold.effective_at or (threshold.expires_at is not None and now >= threshold.expires_at): _fail(BillingErrorCode.INVALID_RECORD)
    if threshold.money_threshold is not None:
        if type(money) is not Money or money.currency is not threshold.money_threshold.currency: _fail(BillingErrorCode.CURRENCY_CONFLICT)
        exceeded = money.amount > threshold.money_threshold.amount
    else:
        if type(quantity) is not UsageQuantity or quantity.unit is not threshold.usage_threshold.unit: _fail(BillingErrorCode.UNIT_CONFLICT)
        exceeded = quantity.quantity > threshold.usage_threshold.quantity
    if not exceeded: return _new(ThresholdEvaluation, {"approval_required": False, "satisfied": True,
        "reason_code": EvaluationReason.APPROVAL_NOT_REQUIRED, "threshold_id": threshold.threshold_id, "approval_id": None})
    valid = [item for item in approvals if _approval_valid(item, threshold, now)]
    if len(valid) != 1: return _new(ThresholdEvaluation, {"approval_required": True, "satisfied": False,
        "reason_code": EvaluationReason.APPROVAL_REQUIRED, "threshold_id": threshold.threshold_id, "approval_id": None})
    return _new(ThresholdEvaluation, {"approval_required": True, "satisfied": True,
        "reason_code": EvaluationReason.APPROVAL_SATISFIED, "threshold_id": threshold.threshold_id,
        "approval_id": valid[0].approval_id})


@dataclass(frozen=True, slots=True, init=False)
class UsageEstimate(_FactoryRecord):
    estimate_id: str; tenant_context: TenantContext; principal_ref: str; capability_code: str
    work_id: str | None; attempt_id: str | None; provider_id: str | None; engine_id: str | None
    quantities: tuple[UsageQuantity, ...]; duration: UsageQuantity | None
    estimated_provider_cost: Money | None; estimated_customer_charge: Money | None
    billing_policy_id: str; threshold_id: str | None; approval_id: str | None
    created_at: datetime; expires_at: datetime; classification: ResourceClassification
    contract_version: str; _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_usage_estimate(*, estimate_id: str, tenant_context: TenantContext, principal_ref: str,
                          capability_code: str, quantities: tuple[UsageQuantity, ...],
                          billing_policy_id: str, created_at: datetime, expires_at: datetime,
                          classification: ResourceClassification, work_id: str | None = None,
                          attempt_id: str | None = None, provider_id: str | None = None,
                          engine_id: str | None = None, duration: UsageQuantity | None = None,
                          estimated_provider_cost: Money | None = None,
                          estimated_customer_charge: Money | None = None,
                          threshold_id: str | None = None, approval_id: str | None = None,
                          contract_version: str = BILLING_CONTRACT_VERSION) -> UsageEstimate:
    validate_estimate_id(estimate_id); _context_tuple(tenant_context); _version(contract_version); _classification(classification)
    _code(principal_ref); _code(capability_code); _policy(billing_policy_id)
    if type(quantities) is not tuple or not quantities or len(quantities) > MAX_REFERENCES or any(type(q) is not UsageQuantity for q in quantities): _fail(BillingErrorCode.INVALID_RECORD)
    if duration is not None and (type(duration) is not UsageQuantity or duration.unit is not UsageUnit.MILLISECOND): _fail(BillingErrorCode.INVALID_RECORD)
    if (provider_id is None) != (engine_id is None): _fail(BillingErrorCode.INVALID_RECORD)
    if provider_id is not None: _code(provider_id); _code(engine_id)
    if work_id is not None: validate_work_id(work_id)
    if attempt_id is not None: validate_attempt_id(attempt_id)
    if threshold_id is not None: validate_threshold_id(threshold_id)
    if approval_id is not None: validate_approval_id(approval_id)
    start, end = _utc(created_at), _utc(expires_at)
    if end <= start: _fail(BillingErrorCode.CHRONOLOGY_CONFLICT)
    return _new(UsageEstimate, locals() | {"created_at": start, "expires_at": end})


@dataclass(frozen=True, slots=True, init=False)
class UsageEvent(_FactoryRecord):
    usage_event_id: str; tenant_context: TenantContext; principal_ref: str; capability_code: str
    work_id: str; assignment_id: str | None; attempt_id: str; provider_id: str | None
    engine_id: str | None; input_quantities: tuple[UsageQuantity, ...]
    output_quantities: tuple[UsageQuantity, ...]; duration: UsageQuantity | None
    status: UsageStatus; occurred_at: datetime; recorded_at: datetime
    source_event_id: str | None; evidence_ids: tuple[str, ...]; billing_policy_id: str
    approval_id: str | None; estimate_id: str | None; idempotency_ref: str
    classification: ResourceClassification; authority_provenance: AuthorityProvenance
    contract_version: str; _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_usage_event(*, usage_event_id: str, tenant_context: TenantContext, principal_ref: str,
                       capability_code: str, work_id: str, attempt_id: str,
                       input_quantities: tuple[UsageQuantity, ...], output_quantities: tuple[UsageQuantity, ...],
                       status: UsageStatus, occurred_at: datetime, recorded_at: datetime,
                       billing_policy_id: str, idempotency_ref: str,
                       actor_ref: str, authority_decision: AuthorizationDecision,
                       classification: ResourceClassification, assignment_id: str | None = None,
                       provider_id: str | None = None, engine_id: str | None = None,
                       duration: UsageQuantity | None = None, source_event_id: str | None = None,
                       evidence_ids: tuple[str, ...] = (), approval_id: str | None = None,
                       estimate_id: str | None = None,
                       contract_version: str = BILLING_CONTRACT_VERSION) -> UsageEvent:
    validate_usage_id(usage_event_id); _context_tuple(tenant_context); _version(contract_version); _classification(classification)
    validate_work_id(work_id); validate_attempt_id(attempt_id)
    if assignment_id is not None: validate_assignment_id(assignment_id)
    if source_event_id is not None: validate_event_id(source_event_id)
    if approval_id is not None: validate_approval_id(approval_id)
    if estimate_id is not None: validate_estimate_id(estimate_id)
    if type(status) is not UsageStatus: _fail(BillingErrorCode.INVALID_RECORD)
    for quantities in (input_quantities, output_quantities):
        if type(quantities) is not tuple or len(quantities) > MAX_REFERENCES or any(type(q) is not UsageQuantity for q in quantities): _fail(BillingErrorCode.INVALID_RECORD)
    if duration is not None and (type(duration) is not UsageQuantity or duration.unit is not UsageUnit.MILLISECOND): _fail(BillingErrorCode.INVALID_RECORD)
    if (provider_id is None) != (engine_id is None): _fail(BillingErrorCode.INVALID_RECORD)
    if provider_id is not None: _code(provider_id); _code(engine_id)
    occurred, recorded = _utc(occurred_at), _utc(recorded_at)
    if recorded < occurred: _fail(BillingErrorCode.CHRONOLOGY_CONFLICT)
    for item in (principal_ref, capability_code, billing_policy_id, idempotency_ref, actor_ref): _code(item)
    auth = _authority(authority_decision, resource_type="billing_usage", resource_id=usage_event_id,
                      context=tenant_context, capability="billing.usage.record", actor_ref=actor_ref)
    return _new(UsageEvent, {"usage_event_id": usage_event_id, "tenant_context": tenant_context,
        "principal_ref": principal_ref, "capability_code": capability_code, "work_id": work_id,
        "assignment_id": assignment_id, "attempt_id": attempt_id, "provider_id": provider_id,
        "engine_id": engine_id, "input_quantities": input_quantities, "output_quantities": output_quantities,
        "duration": duration, "status": status, "occurred_at": occurred, "recorded_at": recorded,
        "source_event_id": source_event_id, "evidence_ids": _refs(evidence_ids, validate_evidence_id),
        "billing_policy_id": billing_policy_id, "approval_id": approval_id, "estimate_id": estimate_id,
        "idempotency_ref": idempotency_ref, "classification": classification,
        "authority_provenance": auth, "contract_version": contract_version})


@dataclass(frozen=True, slots=True, init=False)
class ProviderCost(_FactoryRecord):
    provider_cost_id: str; usage_event_id: str; tenant_id: str; tenant_context: TenantContext
    amount: Money | None
    cost_source: CostSource; recorded_at: datetime; invoice_reference: str | None
    policy_id: str; policy_version: str; classification: ResourceClassification
    authority_provenance: AuthorityProvenance; contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_provider_cost(*, provider_cost_id: str, usage: UsageEvent, cost_source: CostSource,
                         amount: Money | None, recorded_at: datetime, actor_ref: str,
                         authority_decision: AuthorizationDecision, invoice_reference: str | None = None,
                         contract_version: str = BILLING_CONTRACT_VERSION) -> ProviderCost:
    validate_provider_cost_id(provider_cost_id); _version(contract_version)
    if not _valid(usage, UsageEvent) or type(cost_source) is not CostSource: _fail(BillingErrorCode.INVALID_RECORD)
    if (cost_source is CostSource.UNKNOWN) != (amount is None): _fail(BillingErrorCode.INVALID_RECORD)
    when = _utc(recorded_at)
    if when < usage.recorded_at: _fail(BillingErrorCode.CHRONOLOGY_CONFLICT)
    if invoice_reference is not None: _code(invoice_reference, BillingErrorCode.INVALID_RECORD)
    auth = _authority(authority_decision, resource_type="billing_provider_cost", resource_id=provider_cost_id,
                      context=usage.tenant_context, capability="billing.provider_cost.record", actor_ref=actor_ref)
    return _new(ProviderCost, {"provider_cost_id": provider_cost_id,
        "usage_event_id": usage.usage_event_id, "tenant_id": usage.tenant_context.tenant_id,
        "tenant_context": usage.tenant_context,
        "amount": amount, "cost_source": cost_source, "recorded_at": when,
        "invoice_reference": invoice_reference, "policy_id": usage.tenant_context.policy_id,
        "policy_version": usage.tenant_context.policy_version, "classification": usage.classification,
        "authority_provenance": auth, "contract_version": contract_version})


@dataclass(frozen=True, slots=True, init=False)
class CustomerCharge(_FactoryRecord):
    customer_charge_id: str; usage_event_id: str; tenant_id: str; billing_profile_id: str
    tenant_context: TenantContext; plan_version_id: str; billing_policy_id: str
    amount: Money; calculation_basis: str
    created_at: datetime; status: ChargeStatus; approval_id: str | None
    classification: ResourceClassification; authority_provenance: AuthorityProvenance
    contract_version: str; _integrity: tuple[object, ...] = field(repr=False, compare=False)


def calculate_customer_charge(*, customer_charge_id: str, usage: UsageEvent,
                              billing_profile: BillingProfile, plan_version: PlanVersion,
                              billing_policy: BillingPolicy, provider_cost: ProviderCost | None,
                              created_at: datetime, actor_ref: str,
                              authority_decision: AuthorizationDecision,
                              entitlement_evaluation: EntitlementEvaluation | None = None,
                              threshold_evaluation: ThresholdEvaluation | None = None,
                              status: ChargeStatus = ChargeStatus.CALCULATED,
                              approval_id: str | None = None,
                              contract_version: str = BILLING_CONTRACT_VERSION) -> CustomerCharge:
    validate_customer_charge_id(customer_charge_id); _version(contract_version)
    if not all((_valid(usage, UsageEvent), _valid(billing_profile, BillingProfile), _valid(plan_version, PlanVersion))): _fail(BillingErrorCode.INVALID_RECORD)
    if type(billing_policy) is not BillingPolicy or status is not ChargeStatus.CALCULATED: _fail(BillingErrorCode.INVALID_RECORD)
    tenant = usage.tenant_context.tenant_id
    if (billing_profile.tenant_id != tenant or plan_version.tenant_id != tenant
            or billing_profile.plan_version_id != plan_version.plan_version_id
            or not _same_context(billing_profile.tenant_context, usage.tenant_context)):
        _fail(BillingErrorCode.TENANT_CONFLICT)
    if entitlement_evaluation is not None and (not _valid(entitlement_evaluation, EntitlementEvaluation) or not entitlement_evaluation.entitled): _fail(BillingErrorCode.NOT_ENTITLED)
    if threshold_evaluation is not None and (not _valid(threshold_evaluation, ThresholdEvaluation) or not threshold_evaluation.satisfied): _fail(BillingErrorCode.APPROVAL_REQUIRED)
    if billing_policy.mechanism in {PricingMechanism.PASS_THROUGH, PricingMechanism.PASS_THROUGH_WITH_MARGIN, PricingMechanism.ENTERPRISE_CHARGEBACK}:
        if provider_cost is None or not _valid(provider_cost, ProviderCost) or provider_cost.amount is None or provider_cost.usage_event_id != usage.usage_event_id: _fail(BillingErrorCode.MISSING_REFERENCE)
        base = provider_cost.amount
    else: base = Money(Decimal("0"), billing_profile.default_currency)
    if base.currency is not billing_profile.default_currency: _fail(BillingErrorCode.CURRENCY_CONFLICT)
    amount = base
    if billing_policy.mechanism is PricingMechanism.PASS_THROUGH_WITH_MARGIN:
        margin = billing_policy.margin
        if margin.method is MarginMethod.FIXED_AMOUNT:
            if margin.currency is not base.currency: _fail(BillingErrorCode.CURRENCY_CONFLICT)
            amount = Money(base.amount + margin.value, base.currency)
        else:
            calculated = (base.amount * (Decimal("1") + margin.value / Decimal("100"))).quantize(
                MONEY_QUANTUM, rounding=ROUND_HALF_EVEN,
            )
            amount = Money(calculated, base.currency)
    if billing_policy.mechanism in {PricingMechanism.INCLUDED_ALLOWANCE, PricingMechanism.EXPLORATION_CREDIT, PricingMechanism.PREPAID_CREDIT, PricingMechanism.NO_CHARGE}: amount = Money(Decimal("0"), billing_profile.default_currency)
    if approval_id is not None: validate_approval_id(approval_id)
    when = _utc(created_at)
    if when < usage.recorded_at: _fail(BillingErrorCode.CHRONOLOGY_CONFLICT)
    auth = _authority(authority_decision, resource_type="billing_customer_charge", resource_id=customer_charge_id,
                      context=usage.tenant_context, capability="billing.charge.calculate", actor_ref=actor_ref)
    return _new(CustomerCharge, {"customer_charge_id": customer_charge_id,
        "usage_event_id": usage.usage_event_id, "tenant_id": tenant,
        "billing_profile_id": billing_profile.billing_profile_id,
        "tenant_context": usage.tenant_context,
        "plan_version_id": plan_version.plan_version_id, "billing_policy_id": billing_policy.policy_id,
        "amount": amount, "calculation_basis": billing_policy.mechanism.value.lower(),
        "created_at": when, "status": status, "approval_id": approval_id,
        "classification": usage.classification, "authority_provenance": auth,
        "contract_version": contract_version})


@dataclass(frozen=True, slots=True, init=False)
class Adjustment(_FactoryRecord):
    adjustment_id: str; target_type: AdjustmentTarget; target_id: str; tenant_id: str
    tenant_context: TenantContext; adjustment_type: AdjustmentType
    money: Money | None; quantity: UsageQuantity | None
    reason_code: str; occurred_at: datetime; actor_ref: str; approval_id: str | None
    decision_id: str | None; policy_id: str; policy_version: str
    authority_provenance: AuthorityProvenance; contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


_TARGET_VALIDATORS = {AdjustmentTarget.USAGE: validate_usage_id,
    AdjustmentTarget.CREDIT_MOVEMENT: validate_credit_movement_id,
    AdjustmentTarget.PROVIDER_COST: validate_provider_cost_id,
    AdjustmentTarget.CUSTOMER_CHARGE: validate_customer_charge_id}


def create_adjustment(*, adjustment_id: str, target_type: AdjustmentTarget, target_id: str,
                      tenant_context: TenantContext, adjustment_type: AdjustmentType,
                      reason_code: str, occurred_at: datetime, actor_ref: str,
                      authority_decision: AuthorizationDecision, money: Money | None = None,
                      quantity: UsageQuantity | None = None, approval_id: str | None = None,
                      decision_id: str | None = None,
                      contract_version: str = BILLING_CONTRACT_VERSION) -> Adjustment:
    validate_adjustment_id(adjustment_id); _context_tuple(tenant_context); _version(contract_version)
    if type(target_type) is not AdjustmentTarget or type(adjustment_type) is not AdjustmentType or (money is None) == (quantity is None): _fail(BillingErrorCode.INVALID_RECORD)
    _TARGET_VALIDATORS[target_type](target_id)
    if target_id == adjustment_id: _fail(BillingErrorCode.INVALID_RECORD)
    if approval_id is not None: validate_approval_id(approval_id)
    if decision_id is not None: validate_decision_id(decision_id)
    _code(reason_code, sensitive=False); _code(actor_ref)
    auth = _authority(authority_decision, resource_type="billing_adjustment", resource_id=adjustment_id,
                      context=tenant_context, capability="billing.adjust", actor_ref=actor_ref)
    return _new(Adjustment, {"adjustment_id": adjustment_id, "target_type": target_type,
        "target_id": target_id, "tenant_id": tenant_context.tenant_id,
        "tenant_context": tenant_context,
        "adjustment_type": adjustment_type, "money": money, "quantity": quantity,
        "reason_code": reason_code, "occurred_at": _utc(occurred_at), "actor_ref": actor_ref,
        "approval_id": approval_id, "decision_id": decision_id, "policy_id": tenant_context.policy_id,
        "policy_version": tenant_context.policy_version, "authority_provenance": auth,
        "contract_version": contract_version})


@dataclass(frozen=True, slots=True, init=False)
class BudgetPolicy(_FactoryRecord):
    budget_id: str; tenant_context: TenantContext; basis: BudgetBasis; capability_code: str
    period_start: datetime; period_end: datetime; limit: Money; warning_threshold: Money
    enforcement: BudgetEnforcement; override_capability: str; policy_id: str
    policy_version: str; contract_version: str; _integrity: tuple[object, ...] = field(repr=False, compare=False)


def create_budget_policy(*, budget_id: str, tenant_context: TenantContext, basis: BudgetBasis,
                         capability_code: str, period_start: datetime, period_end: datetime,
                         limit: Money, warning_threshold: Money, enforcement: BudgetEnforcement,
                         override_capability: str, policy_id: str, policy_version: str,
                         contract_version: str = BILLING_CONTRACT_VERSION) -> BudgetPolicy:
    validate_budget_id(budget_id); _context_tuple(tenant_context); _version(contract_version)
    if type(basis) is not BudgetBasis or type(enforcement) is not BudgetEnforcement or type(limit) is not Money or type(warning_threshold) is not Money: _fail(BillingErrorCode.INVALID_RECORD)
    if limit.currency is not warning_threshold.currency or warning_threshold.amount > limit.amount: _fail(BillingErrorCode.INVALID_MONEY)
    start, end = _utc(period_start), _utc(period_end)
    if end <= start: _fail(BillingErrorCode.CHRONOLOGY_CONFLICT)
    _code(override_capability)
    if not override_capability.startswith("approval."): _fail(BillingErrorCode.INVALID_RECORD)
    return _new(BudgetPolicy, {"budget_id": budget_id, "tenant_context": tenant_context,
        "basis": basis, "capability_code": _code(capability_code), "period_start": start,
        "period_end": end, "limit": limit, "warning_threshold": warning_threshold,
        "enforcement": enforcement, "override_capability": _code(override_capability),
        "policy_id": _policy(policy_id), "policy_version": _policy(policy_version),
        "contract_version": contract_version})


@dataclass(frozen=True, slots=True, init=False)
class BudgetEvaluation(_FactoryRecord):
    budget_id: str; total: Money; allowed: bool; warning: bool; reason_code: EvaluationReason
    approval_id: str | None; tenant_context: TenantContext
    policy_id: str; policy_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def evaluate_budget(budget: BudgetPolicy, amounts: Sequence[Money], *, as_of: datetime,
                    approval: ApprovalRecord | None = None) -> BudgetEvaluation:
    if not _valid(budget, BudgetPolicy): _fail(BillingErrorCode.INVALID_RECORD)
    now = _utc(as_of)
    if not budget.period_start <= now < budget.period_end: _fail(BillingErrorCode.INVALID_RECORD)
    total = Money(Decimal("0"), budget.limit.currency)
    for amount in amounts:
        total = total.add(amount)
    warning = total.amount > budget.warning_threshold.amount
    if total.amount <= budget.limit.amount:
        return _new(BudgetEvaluation, {"budget_id": budget.budget_id, "total": total, "allowed": True,
            "warning": warning, "reason_code": EvaluationReason.BUDGET_WARNING if warning else EvaluationReason.WITHIN_BUDGET,
            "approval_id": None, "tenant_context": budget.tenant_context,
            "policy_id": budget.policy_id, "policy_version": budget.policy_version})
    if approval is not None and _approval_valid(approval, create_approval_threshold(
            threshold_id="thresh1:budget:override", tenant_context=budget.tenant_context,
            capability_code=budget.capability_code, basis=ThresholdBasis.ACTUAL_CUSTOMER_CHARGE,
            approval_capability=budget.override_capability, effective_at=budget.period_start,
            expires_at=budget.period_end, policy_id=budget.policy_id, policy_version=budget.policy_version,
            money_threshold=budget.limit), now):
        return _new(BudgetEvaluation, {"budget_id": budget.budget_id, "total": total, "allowed": True,
            "warning": True, "reason_code": EvaluationReason.OVERRIDE_SATISFIED,
            "approval_id": approval.approval_id, "tenant_context": budget.tenant_context,
            "policy_id": budget.policy_id, "policy_version": budget.policy_version})
    allowed = budget.enforcement is BudgetEnforcement.SOFT
    return _new(BudgetEvaluation, {"budget_id": budget.budget_id, "total": total, "allowed": allowed,
        "warning": True, "reason_code": EvaluationReason.BUDGET_WARNING if allowed else EvaluationReason.BUDGET_DENIED,
        "approval_id": None, "tenant_context": budget.tenant_context,
        "policy_id": budget.policy_id, "policy_version": budget.policy_version})


def evaluate_allowance(allowance: Allowance, usage_events: Sequence[UsageEvent], *, as_of: datetime) -> AllowanceEvaluation:
    if not _valid(allowance, Allowance): _fail(BillingErrorCode.INVALID_RECORD)
    now = _utc(as_of)
    if not allowance.period_start <= now < allowance.period_end: _fail(BillingErrorCode.INVALID_RECORD)
    consumed = Decimal("0")
    for event in usage_events:
        if not _valid(event, UsageEvent): _fail(BillingErrorCode.INVALID_RECORD)
        if not _same_context(event.tenant_context, allowance.tenant_context): _fail(BillingErrorCode.TENANT_CONFLICT)
        if event.capability_code != allowance.capability_code or not allowance.period_start <= event.occurred_at < allowance.period_end: continue
        for quantity in event.input_quantities + event.output_quantities:
            if quantity.unit is allowance.included.unit: consumed += quantity.quantity
    remaining = max(Decimal("0"), allowance.included.quantity - consumed)
    allowed = consumed <= allowance.included.quantity
    return _new(AllowanceEvaluation, {"allowed": allowed,
        "reason_code": EvaluationReason.WITHIN_ALLOWANCE if allowed else EvaluationReason.ALLOWANCE_EXCEEDED,
        "consumed": UsageQuantity(consumed, allowance.included.unit),
        "remaining": UsageQuantity(remaining, allowance.included.unit)})


@dataclass(frozen=True, slots=True, init=False)
class ValidatedBillingCollection(_FactoryRecord):
    record_ids: tuple[str, ...]; tenant_ids: tuple[str, ...]; usage_integrities: tuple[tuple[str, tuple[object, ...]], ...]
    charge_integrities: tuple[tuple[str, tuple[object, ...]], ...]; evaluated_at: datetime
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


def validate_billing_collection(*, billing_profiles: Sequence[BillingProfile] = (), plans: Sequence[Plan] = (),
                                plan_versions: Sequence[PlanVersion] = (), entitlements: Sequence[Entitlement] = (),
                                allowances: Sequence[Allowance] = (), credit_accounts: Sequence[CreditAccount] = (),
                                credit_movements: Sequence[CreditMovement] = (), thresholds: Sequence[ApprovalThreshold] = (),
                                estimates: Sequence[UsageEstimate] = (), usage_events: Sequence[UsageEvent] = (),
                                provider_costs: Sequence[ProviderCost] = (), charges: Sequence[CustomerCharge] = (),
                                adjustments: Sequence[Adjustment] = (), budgets: Sequence[BudgetPolicy] = (),
                                evaluated_at: datetime) -> ValidatedBillingCollection:
    groups = (billing_profiles, plans, plan_versions, entitlements, allowances, credit_accounts,
              credit_movements, thresholds, estimates, usage_events, provider_costs, charges, adjustments, budgets)
    classes = (BillingProfile, Plan, PlanVersion, Entitlement, Allowance, CreditAccount,
               CreditMovement, ApprovalThreshold, UsageEstimate, UsageEvent, ProviderCost,
               CustomerCharge, Adjustment, BudgetPolicy)
    if any(type(g) not in (list, tuple) or len(g) > MAX_RECORDS_PER_KIND for g in groups): _fail(BillingErrorCode.COLLECTION_LIMIT_EXCEEDED)
    if any(not _valid(item, cls) for g, cls in zip(groups, classes, strict=True) for item in g): _fail(BillingErrorCode.INVALID_RECORD)
    id_fields = {BillingProfile: "billing_profile_id", Plan: "plan_id",
        PlanVersion: "plan_version_id", Entitlement: "entitlement_id",
        Allowance: "allowance_id", CreditAccount: "account_id",
        CreditMovement: "movement_id", ApprovalThreshold: "threshold_id",
        UsageEstimate: "estimate_id", UsageEvent: "usage_event_id",
        ProviderCost: "provider_cost_id", CustomerCharge: "customer_charge_id",
        Adjustment: "adjustment_id", BudgetPolicy: "budget_id"}
    def rid(item: object) -> str:
        try: return getattr(item, id_fields[type(item)])
        except Exception: _fail(BillingErrorCode.INVALID_RECORD)
    ids = [rid(item) for group in groups for item in group]
    if len(ids) != len(set(ids)): _fail(BillingErrorCode.DUPLICATE_IDENTIFIER)
    plan_by = {x.plan_id: x for x in plans}; pv_by = {x.plan_version_id: x for x in plan_versions}
    usage_by = {x.usage_event_id: x for x in usage_events}; profile_by = {x.billing_profile_id: x for x in billing_profiles}
    account_by = {x.account_id: x for x in credit_accounts}; cost_by = {x.provider_cost_id: x for x in provider_costs}; charge_by = {x.customer_charge_id: x for x in charges}
    for pv in plan_versions:
        plan = plan_by.get(pv.plan_id)
        if plan is None: _fail(BillingErrorCode.MISSING_REFERENCE)
        if plan.tenant_id != pv.tenant_id or pv.effective_at < plan.created_at: _fail(BillingErrorCode.PLAN_CONFLICT)
    for left in plan_versions:
        for right in plan_versions:
            if left.plan_version_id < right.plan_version_id and left.plan_id == right.plan_id:
                left_end = left.expires_at or datetime.max.replace(tzinfo=timezone.utc); right_end = right.expires_at or datetime.max.replace(tzinfo=timezone.utc)
                if left.effective_at < right_end and right.effective_at < left_end: _fail(BillingErrorCode.PLAN_CONFLICT)
    active_entitlements: dict[tuple[tuple[object, ...], str], list[Entitlement]] = {}
    now = _utc(evaluated_at)
    for item in entitlements:
        if item.plan_version_id is not None and item.plan_version_id not in pv_by:
            _fail(BillingErrorCode.MISSING_REFERENCE)
        if item.status is RecordStatus.ACTIVE and item.effective_at <= now and (item.expires_at is None or now < item.expires_at):
            active_entitlements.setdefault((_context_tuple(item.tenant_context), item.capability_code), []).append(item)
    if any(len(items) > 1 for items in active_entitlements.values()):
        _fail(BillingErrorCode.ENTITLEMENT_CONFLICT)
    for allowance in allowances:
        if allowance.plan_version_id not in pv_by: _fail(BillingErrorCode.MISSING_REFERENCE)
    for movement in credit_movements:
        account = account_by.get(movement.account_id)
        if account is None: _fail(BillingErrorCode.MISSING_REFERENCE)
        if account.tenant_id != movement.tenant_id: _fail(BillingErrorCode.TENANT_CONFLICT)
    estimate_by = {item.estimate_id: item for item in estimates}
    idem: dict[str, UsageEvent] = {}
    for usage in usage_events:
        prior = idem.get(usage.idempotency_ref)
        if prior is not None: _fail(BillingErrorCode.DUPLICATE_USAGE)
        idem[usage.idempotency_ref] = usage
        if usage.estimate_id is not None:
            estimate = estimate_by.get(usage.estimate_id)
            if estimate is None: _fail(BillingErrorCode.MISSING_REFERENCE)
            if (not _same_context(estimate.tenant_context, usage.tenant_context)
                    or estimate.capability_code != usage.capability_code
                    or estimate.billing_policy_id != usage.billing_policy_id
                    or estimate.created_at > usage.occurred_at):
                _fail(BillingErrorCode.POLICY_CONFLICT)
    for cost in provider_costs:
        usage = usage_by.get(cost.usage_event_id)
        if usage is None: _fail(BillingErrorCode.MISSING_REFERENCE)
        if cost.tenant_id != usage.tenant_context.tenant_id: _fail(BillingErrorCode.TENANT_CONFLICT)
    if len({item.usage_event_id for item in provider_costs}) != len(provider_costs):
        _fail(BillingErrorCode.DUPLICATE_IDENTIFIER)
    for charge in charges:
        usage = usage_by.get(charge.usage_event_id); profile = profile_by.get(charge.billing_profile_id); pv = pv_by.get(charge.plan_version_id)
        if None in (usage, profile, pv): _fail(BillingErrorCode.MISSING_REFERENCE)
        if len({charge.tenant_id, usage.tenant_context.tenant_id, profile.tenant_id, pv.tenant_id}) != 1: _fail(BillingErrorCode.TENANT_CONFLICT)
    if len({item.usage_event_id for item in charges}) != len(charges):
        _fail(BillingErrorCode.DUPLICATE_USAGE)
    adjusted: set[tuple[AdjustmentType, str]] = set()
    for adjustment in adjustments:
        targets = {AdjustmentTarget.USAGE: usage_by, AdjustmentTarget.CREDIT_MOVEMENT: {x.movement_id: x for x in credit_movements}, AdjustmentTarget.PROVIDER_COST: cost_by, AdjustmentTarget.CUSTOMER_CHARGE: charge_by}
        target = targets[adjustment.target_type].get(adjustment.target_id)
        if target is None: _fail(BillingErrorCode.MISSING_REFERENCE)
        key = (adjustment.adjustment_type, adjustment.target_id)
        if key in adjusted: _fail(BillingErrorCode.DUPLICATE_REVERSAL)
        adjusted.add(key)
        target_tenant = target.tenant_context.tenant_id if type(target) is UsageEvent else target.tenant_id
        if target_tenant != adjustment.tenant_id: _fail(BillingErrorCode.TENANT_CONFLICT)
        if adjustment.occurred_at < getattr(target, "occurred_at", getattr(target, "recorded_at", getattr(target, "created_at", adjustment.occurred_at))): _fail(BillingErrorCode.CHRONOLOGY_CONFLICT)
        if adjustment.money is not None and hasattr(target, "amount") and target.amount is not None:
            if adjustment.money.currency is not target.amount.currency: _fail(BillingErrorCode.CURRENCY_CONFLICT)
            if adjustment.money.amount > target.amount.amount: _fail(BillingErrorCode.ADJUSTMENT_EXCEEDED)
        if adjustment.quantity is not None:
            available: list[UsageQuantity] = []
            if type(target) is UsageEvent:
                available.extend(target.input_quantities + target.output_quantities)
                if target.duration is not None: available.append(target.duration)
            elif type(target) is CreditMovement:
                available.append(target.quantity)
            matching = [item for item in available if item.unit is adjustment.quantity.unit]
            if not matching: _fail(BillingErrorCode.UNIT_CONFLICT)
            if adjustment.quantity.quantity > sum((item.quantity for item in matching), Decimal("0")):
                _fail(BillingErrorCode.ADJUSTMENT_EXCEEDED)
    tenants = sorted({getattr(item, "tenant_id", getattr(getattr(item, "tenant_context", None), "tenant_id", "")) for group in groups for item in group if hasattr(item, "tenant_id") or hasattr(item, "tenant_context")})
    return _new(ValidatedBillingCollection, {"record_ids": tuple(sorted(ids)), "tenant_ids": tuple(tenants),
        "usage_integrities": tuple(sorted((x.usage_event_id, x._integrity) for x in usage_events)),
        "charge_integrities": tuple(sorted((x.customer_charge_id, x._integrity) for x in charges)),
        "evaluated_at": _utc(evaluated_at)})


_PROJECTABLE = {BillingProfile: ("billing_profile", "billing_profile_id"), PlanVersion: ("billing_plan_version", "plan_version_id"),
    Entitlement: ("billing_entitlement", "entitlement_id"), Allowance: ("billing_allowance", "allowance_id"),
    CreditBalance: ("billing_credit_balance", "account_id"), UsageEstimate: ("billing_estimate", "estimate_id"),
    UsageEvent: ("billing_usage", "usage_event_id"), ProviderCost: ("billing_provider_cost", "provider_cost_id"),
    CustomerCharge: ("billing_customer_charge", "customer_charge_id"), BudgetEvaluation: ("billing_budget_evaluation", "budget_id"),
    Adjustment: ("billing_adjustment", "adjustment_id")}
_VIEW_CAPABILITIES = {"billing_profile": "billing.profile.view", "billing_plan_version": "billing.plan.view",
    "billing_entitlement": "billing.entitlement.view", "billing_allowance": "billing.allowance.view",
    "billing_credit_balance": "billing.credit.view", "billing_estimate": "billing.usage.view",
    "billing_usage": "billing.usage.view", "billing_provider_cost": "billing.provider_cost.view",
    "billing_customer_charge": "billing.charge.view", "billing_budget_evaluation": "billing.budget.view",
    "billing_adjustment": "billing.adjustment.view"}


def _metadata(record: object) -> dict[str, Any]:
    result: dict[str, Any] = {"contract_version": BILLING_CONTRACT_VERSION}
    for name in type(record).__dataclass_fields__:
        if name.startswith("_") or name in {"tenant_context", "authority_provenance", "margin", "quantities", "input_quantities", "output_quantities", "evidence_ids"}: continue
        value = getattr(record, name)
        if isinstance(value, Enum): value = value.value
        elif type(value) is datetime: value = _iso(value)
        elif type(value) is Decimal: value = format(value, "f")
        elif type(value) is Money: result[name + "_amount"] = format(value.amount, "f"); result[name + "_currency"] = value.currency.value; continue
        elif type(value) is UsageQuantity: result[name + "_quantity"] = format(value.quantity, "f"); result[name + "_unit"] = value.unit.value; continue
        elif type(value) in (tuple, list, dict, set, frozenset): continue
        if value is None or type(value) in (str, bool, int): result[name] = value
    context = getattr(record, "tenant_context", None)
    if _valid_tenant_context(context):
        result.update({"tenant_id": context.tenant_id, "organization_id": context.organization_id,
                       "product_context_id": context.product_context_id,
                       "workspace_context_id": context.workspace_context_id,
                       "project_context_id": context.project_context_id,
                       "policy_id": context.policy_id, "policy_version": context.policy_version})
    return result


def project_billing_record(record: object, decision: AuthorizationDecision) -> dict[str, Any]:
    try:
        spec = _PROJECTABLE.get(type(record))
        if spec is None or not _valid(record, type(record)): _fail(BillingErrorCode.INVALID_PROJECTION)
        resource_type, id_field = spec; source = _metadata(record); record_id = source[id_field]
        project_authorized_fields({}, decision, behavior=ProjectionBehavior.OMIT)
        context = getattr(record, "tenant_context", None)
        provenance = getattr(record, "authority_provenance", None)
        policy_id = (context.policy_id if _valid_tenant_context(context)
                     else getattr(record, "policy_id", getattr(provenance, "policy_id", None)))
        policy_version = (context.policy_version if _valid_tenant_context(context)
                          else getattr(record, "policy_version", getattr(provenance, "policy_version", None)))
        if (decision.resource_type != resource_type or decision.resource_id != record_id
                or decision.action_code != _VIEW_CAPABILITIES[resource_type]
                or decision.policy_id != policy_id or decision.policy_version != policy_version
                or (decision.allowed and decision.effective_scope is None)
                or (decision.allowed and _valid_tenant_context(context)
                    and decision.effective_scope != _scope_for(context, record_id))):
            _fail(BillingErrorCode.INVALID_AUTHORITY)
        return project_authorized_fields(source, decision, behavior=ProjectionBehavior.OMIT)
    except BillingContractError: raise
    except Exception: _fail(BillingErrorCode.INVALID_PROJECTION)


@dataclass(frozen=True, slots=True, init=False)
class SafeBillingAudit(_FactoryRecord):
    audit_id: str; action_code: str; record_type: str; record_id: str; tenant_id: str
    outcome_code: str; reason_code: str; capability_code: str | None; usage_unit: str | None
    currency: str | None; policy_id: str; policy_version: str; timestamp: str
    correlation_id: str | None; contract_version: str
    _integrity: tuple[object, ...] = field(repr=False, compare=False)


_AUDIT_ACTIONS = frozenset({"billing.profile.record", "billing.plan.activate", "billing.entitlement.evaluate",
    "billing.entitlement.grant", "billing.allowance.evaluate", "billing.credit.record",
    "billing.usage.estimate", "billing.usage.record", "billing.usage.reject",
    "billing.provider_cost.record", "billing.charge.calculate", "billing.charge.reverse",
    "billing.threshold.evaluate", "billing.budget.evaluate", "billing.adjust", "billing.cross_tenant.deny"})
_AUDIT_OUTCOMES = frozenset({"allowed", "denied", "recorded", "calculated", "rejected", "required", "satisfied", "reversed"})
_AUDIT_TARGETS = {"billing_profile": validate_billing_profile_id, "plan": validate_plan_id,
    "plan_version": validate_plan_version_id, "entitlement": validate_entitlement_id,
    "allowance": validate_allowance_id, "credit_account": validate_credit_account_id,
    "credit_movement": validate_credit_movement_id, "estimate": validate_estimate_id,
    "usage": validate_usage_id, "provider_cost": validate_provider_cost_id,
    "customer_charge": validate_customer_charge_id, "adjustment": validate_adjustment_id,
    "threshold": validate_threshold_id, "budget": validate_budget_id}
_AUDIT_REASONS = frozenset(
    {item.value.lower() for item in BillingErrorCode}
    | {f"billing.{item.value.lower()}" for item in EvaluationReason}
    | {"usage.recorded", "cost.recorded", "charge.calculated", "credit.recorded",
       "plan.activated", "entitlement.granted", "adjustment.recorded"}
)


def build_safe_billing_audit(*, audit_id: str, action_code: str, record_type: str,
                             record_id: str, tenant_id: str, outcome_code: str,
                             reason_code: str, policy_id: str, policy_version: str,
                             timestamp: datetime, capability_code: str | None = None,
                             usage_unit: UsageUnit | None = None, currency: Currency | None = None,
                             correlation_id: str | None = None) -> SafeBillingAudit:
    try:
        validate_billing_audit_id(audit_id); validate_tenant_id(tenant_id)
        _code(record_type)
        validator = _AUDIT_TARGETS.get(record_type)
        if validator is None: _fail(BillingErrorCode.INVALID_AUDIT)
        validator(record_id)
    except Exception:
        _fail(BillingErrorCode.INVALID_AUDIT)
    for value in (action_code, outcome_code, reason_code, policy_id, policy_version): _code(value, BillingErrorCode.INVALID_AUDIT, sensitive=False)
    if (action_code not in _AUDIT_ACTIONS or outcome_code not in _AUDIT_OUTCOMES
            or reason_code not in _AUDIT_REASONS):
        _fail(BillingErrorCode.INVALID_AUDIT)
    if capability_code is not None: _code(capability_code)
    if usage_unit is not None and type(usage_unit) is not UsageUnit: _fail(BillingErrorCode.INVALID_AUDIT)
    if currency is not None and type(currency) is not Currency: _fail(BillingErrorCode.INVALID_AUDIT)
    if correlation_id is not None: _code(correlation_id)
    return _new(SafeBillingAudit, {"audit_id": audit_id, "action_code": action_code,
        "record_type": record_type, "record_id": record_id, "tenant_id": tenant_id,
        "outcome_code": outcome_code, "reason_code": reason_code,
        "capability_code": capability_code, "usage_unit": None if usage_unit is None else usage_unit.value,
        "currency": None if currency is None else currency.value, "policy_id": policy_id,
        "policy_version": policy_version, "timestamp": _iso(_utc(timestamp)),
        "correlation_id": correlation_id, "contract_version": BILLING_CONTRACT_VERSION})


@dataclass(frozen=True, slots=True)
class DerivedBillingView:
    view_kind: str; tenant_id: str; record_ids: tuple[str, ...]
    totals: tuple[tuple[str, str], ...]; derived: bool = True; official_statement: bool = False


def create_derived_billing_view(sources: Sequence[tuple[object, AuthorizationDecision]], *,
                                view_kind: str) -> DerivedBillingView:
    _code(view_kind, BillingErrorCode.SOURCE_NOT_AUTHORIZED, sensitive=False)
    if type(sources) not in (list, tuple) or not sources: _fail(BillingErrorCode.SOURCE_NOT_AUTHORIZED)
    tenants: set[str] = set(); policies: set[tuple[str, str]] = set()
    ids: list[str] = []; totals: dict[str, Decimal] = {}
    for pair in sources:
        if type(pair) is not tuple or len(pair) != 2: _fail(BillingErrorCode.SOURCE_NOT_AUTHORIZED)
        record, decision = pair
        try: projected = project_billing_record(record, decision)
        except Exception: _fail(BillingErrorCode.SOURCE_NOT_AUTHORIZED)
        if not decision.allowed or not projected: _fail(BillingErrorCode.SOURCE_NOT_AUTHORIZED)
        spec = _PROJECTABLE[type(record)]; record_id = getattr(record, spec[1]); ids.append(record_id)
        tenant = getattr(record, "tenant_id", getattr(getattr(record, "tenant_context", None), "tenant_id", None))
        if tenant is None: _fail(BillingErrorCode.SOURCE_NOT_AUTHORIZED)
        tenants.add(tenant)
        policies.add((decision.policy_id, decision.policy_version))
        amount = getattr(record, "amount", None)
        if (type(amount) is Money and "amount_amount" in projected
                and "amount_currency" in projected):
            key = projected["amount_currency"]
            totals[key] = totals.get(key, Decimal("0")) + Decimal(projected["amount_amount"])
    if len(tenants) != 1: _fail(BillingErrorCode.TENANT_CONFLICT)
    if len(policies) != 1: _fail(BillingErrorCode.POLICY_CONFLICT)
    return DerivedBillingView(view_kind, next(iter(tenants)), tuple(sorted(ids)),
                              tuple(sorted((key, format(value, ".6f")) for key, value in totals.items())))


def validate_work_usage_attribution(usage: UsageEvent, *, work: WorkItem,
                                    assignment: Assignment | None,
                                    attempt: ExecutionAttempt) -> bool:
    if not _valid(usage, UsageEvent): _fail(BillingErrorCode.INVALID_RECORD)
    try:
        for source, expected_type in ((work, WorkItem), (attempt, ExecutionAttempt)):
            if type(source) is not expected_type or source._integrity != tuple(
                    getattr(source, name) for name in expected_type.__dataclass_fields__ if name != "_integrity"):
                _fail(BillingErrorCode.INVALID_RECORD)
        if assignment is not None and (type(assignment) is not Assignment or assignment._integrity != tuple(
                getattr(assignment, name) for name in Assignment.__dataclass_fields__ if name != "_integrity")):
            _fail(BillingErrorCode.INVALID_RECORD)
        if usage.work_id != work.work_id or usage.attempt_id != attempt.attempt_id or attempt.work_id != work.work_id:
            _fail(BillingErrorCode.MISSING_REFERENCE)
        if usage.assignment_id is not None:
            if assignment is None or usage.assignment_id != assignment.assignment_id or attempt.assignment_id != assignment.assignment_id:
                _fail(BillingErrorCode.MISSING_REFERENCE)
        for source in (work, attempt) + (() if assignment is None else (assignment,)):
            if (source.scope.organization_id != usage.tenant_context.organization_id
                    or source.scope.product_id != usage.tenant_context.scope.product_id
                    or source.scope.workspace_id != usage.tenant_context.scope.workspace_id
                    or source.scope.project_id != usage.tenant_context.scope.project_id): _fail(BillingErrorCode.TENANT_CONFLICT)
        if attempt.executor.executor_id != usage.principal_ref: _fail(BillingErrorCode.TENANT_CONFLICT)
        if usage.occurred_at < attempt.started_at:
            _fail(BillingErrorCode.CHRONOLOGY_CONFLICT)
        return True
    except BillingContractError: raise
    except Exception: _fail(BillingErrorCode.INVALID_RECORD)


def validate_membership_commercial_access(*, membership: MembershipRecord,
                                          membership_evaluation: MembershipEvaluation,
                                          tenant_context: TenantContext) -> bool:
    try:
        if (membership.tenant_id != tenant_context.tenant_id
                or membership.organization_id != tenant_context.organization_id
                or membership_evaluation.effective is not True
                or membership_evaluation.membership_id != membership.membership_id
                or membership_evaluation.capability_granted is not False): _fail(BillingErrorCode.TENANT_CONFLICT)
        return True
    except BillingContractError: raise
    except Exception: _fail(BillingErrorCode.INVALID_RECORD)


def validate_billing_source_reference(tenant_context: TenantContext, source: object) -> bool:
    _context_tuple(tenant_context)
    try:
        if type(source) is OperationalEvent: validate_event_id(source.event_id)
        elif type(source) is DecisionRecord: validate_decision_id(source.decision_id)
        elif type(source) is ApprovalRecord: validate_approval_id(source.approval_id)
        elif type(source) is WorkItem: validate_work_id(source.work_id)
        elif type(source) is Assignment: validate_assignment_id(source.assignment_id)
        elif type(source) is ExecutionAttempt: validate_attempt_id(source.attempt_id)
        else: _fail(BillingErrorCode.INVALID_RECORD)
        if (source.scope.organization_id != tenant_context.organization_id
                or source.scope.product_id != tenant_context.scope.product_id
                or source.scope.workspace_id != tenant_context.scope.workspace_id
                or source.scope.project_id != tenant_context.scope.project_id): _fail(BillingErrorCode.TENANT_CONFLICT)
        return True
    except BillingContractError: raise
    except Exception: _fail(BillingErrorCode.INVALID_RECORD)


def adapt_current_usage(*args: object, **kwargs: object) -> NoReturn:
    del args, kwargs; _fail(BillingErrorCode.UNSUPPORTED_ADAPTER)


def validate_usage_estimate(estimate: UsageEstimate, *, tenant_context: TenantContext,
                            capability_code: str, as_of: datetime) -> bool:
    if not _valid(estimate, UsageEstimate): _fail(BillingErrorCode.INVALID_RECORD)
    _context_tuple(tenant_context); _code(capability_code); now = _utc(as_of)
    if not _same_context(estimate.tenant_context, tenant_context): _fail(BillingErrorCode.TENANT_CONFLICT)
    if estimate.capability_code != capability_code: _fail(BillingErrorCode.NOT_ENTITLED)
    if now < estimate.created_at or now >= estimate.expires_at: _fail(BillingErrorCode.CHRONOLOGY_CONFLICT)
    return True


__all__ = tuple(name for name in globals() if not name.startswith("_") and name not in {
    "Any", "Mapping", "NoReturn", "Sequence", "Decimal", "datetime", "timezone", "field", "dataclass", "Enum", "re"
})
