from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.marketmatch_authority import (
    AuthorityScope, AuthorizationRequest, PartyKind, PartyReference,
    PrincipalContext, ResourceClassification, ResourceContext,
    ScopedCapabilityGrant, VisibilityMode, VisibilityPolicy,
    evaluate_authorization,
)
from src.marketmatch_billing import (
    BILLING_CONTRACT_VERSION, AdjustmentTarget, AdjustmentType, ApprovalThreshold,
    BillingContractError, BillingErrorCode, BillingPolicy, BillingProfileStatus,
    BudgetBasis, BudgetEnforcement, ChargeStatus, CostSource, CreditMovementType,
    CreditType, Currency, EntitlementEvaluation, EvaluationReason, MarginMethod,
    MarginPolicy, Money, PeriodType, PlanStatus, PricingMechanism, RecordStatus,
    RolloverPolicy, SafeBillingAudit, ThresholdBasis, UsageQuantity, UsageStatus,
    UsageUnit, adapt_current_usage, build_safe_billing_audit,
    calculate_credit_balance, calculate_customer_charge, create_adjustment,
    create_allowance, create_approval_threshold, create_billing_profile,
    create_budget_policy, create_credit_account, create_credit_movement,
    create_derived_billing_view, create_entitlement, create_plan,
    create_plan_version, create_provider_cost, create_usage_estimate,
    create_usage_event, evaluate_allowance, evaluate_approval_threshold,
    evaluate_budget, evaluate_entitlement, project_billing_record,
    validate_billing_collection, validate_billing_profile_id,
    validate_customer_charge_id, validate_entitlement_id, validate_usage_estimate,
    validate_usage_id,
)
from src.marketmatch_tenancy import TenantContext
import src.marketmatch_tenancy as tenancy_kernel
from src.marketmatch_truth_accountability import (
    ActorKind, ActorReference, ApprovalStatus, ApprovalType, TargetType,
    create_approval_record,
)


NOW = datetime(2026, 7, 20, 18, tzinfo=timezone.utc)
TENANT = "tenant1:fictional:alpha"
ORG = "org1:fictional:alpha"
PRODUCT = "product:fictional"
WORKSPACE = "workspace:fictional"
PROJECT = "project:fictional"
OWNER = "party:fictional:owner"
PRODUCT_CTX = "ctx1:product:fictional"
WORKSPACE_CTX = "ctx1:workspace:fictional"
PROJECT_CTX = "ctx1:project:fictional"
POLICY = "billing.visibility"
VERSION = "v1"
ACTOR = "principal:fictional:billing"
CLASSIFICATION = ResourceClassification.CONFIDENTIAL


def assert_code(code: BillingErrorCode, call) -> None:
    with pytest.raises(BillingContractError) as caught:
        call()
    assert caught.value.code is code
    assert str(caught.value) == code.value


def context(*, tenant: str = TENANT, org: str = ORG, project: str = PROJECT) -> TenantContext:
    scope = AuthorityScope(org, PRODUCT, WORKSPACE, project, PROJECT_CTX, OWNER, False)
    return tenancy_kernel._new_tenant_context(
        tenant_id=tenant, organization_id=org, product_context_id=PRODUCT_CTX,
        workspace_context_id=WORKSPACE_CTX, project_context_id=PROJECT_CTX,
        scope=scope, classification=CLASSIFICATION, policy_id=POLICY,
        policy_version=VERSION,
    )


def scope_for(record_id: str, ctx: TenantContext | None = None) -> AuthorityScope:
    value = ctx or context()
    return AuthorityScope(value.organization_id, value.scope.product_id,
                          value.scope.workspace_id, value.scope.project_id,
                          record_id, value.scope.owner_party_id, False)


def decision(resource_type: str, record_id: str, capability: str, *,
             actor: str = ACTOR, ctx: TenantContext | None = None,
             fields: frozenset[str] = frozenset(), visible: frozenset[str] | None = None,
             allowed: bool = True, locale: str = "en", tz: str = "UTC"):
    actual = ctx or context(); scope = scope_for(record_id, actual)
    principal = PrincipalContext(
        actor, PartyReference(actor, PartyKind.PERSON),
        capability_grants=(ScopedCapabilityGrant(capability, scope, "test:grant"),) if allowed else (),
    )
    policy = VisibilityPolicy(POLICY, VERSION, VisibilityMode.CONTROLLED_CONFIDENTIALITY,
                              frozenset({capability}), fields if visible is None else visible,
                              frozenset(ResourceClassification))
    return evaluate_authorization(
        AuthorizationRequest(principal, capability,
            ResourceContext(resource_type, record_id, scope, CLASSIFICATION, fields),
            fields, presentation_locale=locale, display_timezone=tz), policy)


def plan():
    return create_plan(plan_id="plan1:fictional:standard", tenant_id=TENANT,
                       display_label="Fictional Standard", status=PlanStatus.ACTIVE,
                       created_at=NOW - timedelta(days=10), policy_id=POLICY,
                       policy_version=VERSION)


def plan_version(*, entitlement_ids=(), allowance_ids=(), effective_at=NOW - timedelta(days=9),
                 expires_at=NOW + timedelta(days=30)):
    return create_plan_version(
        plan_version_id="planv1:fictional:standard:v1", plan_id="plan1:fictional:standard",
        tenant_id=TENANT, version_number=1, effective_at=effective_at,
        expires_at=expires_at, currency=Currency.USD, policy_id=POLICY,
        policy_version=VERSION, entitlement_ids=entitlement_ids,
        allowance_ids=allowance_ids, billing_policy_ids=("pricing:standard",),
    )


def profile():
    rid = "bprof1:fictional:alpha"
    return create_billing_profile(
        billing_profile_id=rid, tenant_context=context(), status=BillingProfileStatus.ACTIVE,
        effective_at=NOW - timedelta(days=8), default_currency=Currency.USD,
        pricing_policy_id="pricing:standard", plan_version_id="planv1:fictional:standard:v1",
        actor_ref=ACTOR, authority_decision=decision("billing_profile", rid, "billing.profile.create"),
        classification=CLASSIFICATION,
    )


def entitlement(*, ctx=None, status=RecordStatus.ACTIVE, expires_at=NOW + timedelta(days=30)):
    rid = "ent1:fictional:analyze"; actual = ctx or context()
    return create_entitlement(
        entitlement_id=rid, tenant_context=actual, capability_code="capability.analyze",
        status=status, effective_at=NOW - timedelta(days=7), expires_at=expires_at,
        grant_source="plan:standard", actor_ref=ACTOR,
        authority_decision=decision("billing_entitlement", rid, "billing.entitlement.grant", ctx=actual),
        classification=CLASSIFICATION, plan_version_id="planv1:fictional:standard:v1",
    )


def usage(*, rid="usage1:fictional:run:alpha", ctx=None, occurred_at=NOW - timedelta(minutes=2),
          recorded_at=NOW - timedelta(minutes=1), idem="meter:fictional:alpha", provider="provider:fictional"):
    actual = ctx or context()
    return create_usage_event(
        usage_event_id=rid, tenant_context=actual, principal_ref="principal:fictional:worker",
        capability_code="capability.analyze", work_id="wrk1:fictional:analysis",
        assignment_id="asg1:fictional:worker", attempt_id="atm1:fictional:run",
        input_quantities=(UsageQuantity(Decimal("100"), UsageUnit.TOKEN),),
        output_quantities=(UsageQuantity(Decimal("50"), UsageUnit.TOKEN),),
        duration=UsageQuantity(Decimal("1200"), UsageUnit.MILLISECOND),
        status=UsageStatus.SUCCEEDED, occurred_at=occurred_at, recorded_at=recorded_at,
        billing_policy_id="pricing:standard", idempotency_ref=idem,
        actor_ref=ACTOR, authority_decision=decision("billing_usage", rid, "billing.usage.record", ctx=actual),
        classification=CLASSIFICATION, provider_id=provider, engine_id="engine:fictional:v1",
        evidence_ids=("ev1:system:usage",),
    )


def provider_cost(event=None, amount=Decimal("1.250000")):
    event = event or usage(); rid = "pcost1:fictional:alpha"
    return create_provider_cost(
        provider_cost_id=rid, usage=event, cost_source=CostSource.REPORTED_BY_PROVIDER,
        amount=Money(amount, Currency.USD), recorded_at=NOW, actor_ref=ACTOR,
        authority_decision=decision("billing_provider_cost", rid, "billing.provider_cost.record", ctx=event.tenant_context),
    )


def threshold_approval(*, status=ApprovalStatus.APPROVED,
                       expires_at=NOW + timedelta(days=1)):
    rid = "apr1:fictional:billing:threshold"
    actor = ActorReference("principal:fictional:approver", ActorKind.HUMAN)
    auth = decision("truth_approval", rid, "approval.billing_threshold",
                    actor=actor.actor_ref)
    return create_approval_record(
        approval_id=rid, approval_type=ApprovalType.AUTHORIZATION,
        target_type=TargetType.OPERATIONAL_CONTEXT, target_id=PROJECT_CTX,
        scope=scope_for(rid), approver=actor, status=status,
        recorded_at=NOW - timedelta(minutes=2),
        effective_at=NOW - timedelta(minutes=1), expires_at=expires_at,
        authority_decision=auth, classification=CLASSIFICATION,
        visibility_policy_id=POLICY, visibility_policy_version=VERSION,
        authority_capability="approval.billing_threshold",
    )


def charge(*, event=None, policy=None):
    event = event or usage(); rid = "charge1:fictional:alpha"
    return calculate_customer_charge(
        customer_charge_id=rid, usage=event, billing_profile=profile(),
        plan_version=plan_version(),
        billing_policy=policy or BillingPolicy("pricing:standard", VERSION, PricingMechanism.PASS_THROUGH),
        provider_cost=provider_cost(event), created_at=NOW + timedelta(minutes=1),
        actor_ref=ACTOR, authority_decision=decision("billing_customer_charge", rid, "billing.charge.calculate", ctx=event.tenant_context),
    )


@pytest.mark.parametrize("validator,value", [
    (validate_billing_profile_id, "tenant1:fictional:alpha"),
    (validate_entitlement_id, "ctx1:project:fictional"),
    (validate_usage_id, "usage1:12345"),
    (validate_customer_charge_id, "charge1:" + "a" * 64),
    (validate_usage_id, "usage1:user@example.com"),
    (validate_usage_id, "usage1:../../secret"),
    (validate_usage_id, "usage1:cost:usd:4"),
])
def test_type_distinct_bounded_safe_identifiers(validator, value):
    assert_code(BillingErrorCode.INVALID_IDENTIFIER, lambda: validator(value))


def test_identity_is_language_neutral_and_distinct_from_display_label():
    item = plan()
    assert item.plan_id != item.display_label
    assert plan().plan_id == item.plan_id


def test_money_is_exact_normalized_and_currency_explicit():
    value = Money(Decimal("1.25"), Currency.USD)
    assert value.amount == Decimal("1.250000")
    assert value.serialize() == "1.250000 USD"
    assert value.add(Money(Decimal("0.25"), Currency.USD)).amount == Decimal("1.500000")


@pytest.mark.parametrize("value", [1.2, True, Decimal("NaN"), Decimal("Infinity"), Decimal("-1"), Decimal("1.0000001")])
def test_money_rejects_float_boolean_nonfinite_negative_and_excess_scale(value):
    assert_code(BillingErrorCode.INVALID_MONEY, lambda: Money(value, Currency.USD))


def test_money_rejects_unknown_currency_and_cross_currency_arithmetic():
    assert_code(BillingErrorCode.INVALID_MONEY, lambda: Money(Decimal("1"), "USD"))
    assert_code(BillingErrorCode.CURRENCY_CONFLICT,
                lambda: Money(Decimal("1"), Currency.USD).add(Money(Decimal("1"), Currency.EUR)))


@pytest.mark.parametrize("quantity", [True, -1, Decimal("NaN"), Decimal("Infinity"), Decimal("1.0000001")])
def test_usage_quantity_rejects_malformed_values(quantity):
    assert_code(BillingErrorCode.INVALID_QUANTITY,
                lambda: UsageQuantity(quantity if type(quantity) is Decimal else quantity, UsageUnit.TOKEN))


def test_usage_quantity_never_converts_units_or_translated_codes():
    assert_code(BillingErrorCode.INVALID_QUANTITY, lambda: UsageQuantity(Decimal("1"), "FICHA"))
    assert_code(BillingErrorCode.UNIT_CONFLICT,
                lambda: UsageQuantity(Decimal("1"), UsageUnit.TOKEN).add(UsageQuantity(Decimal("1"), UsageUnit.BYTE)))


def test_plan_and_plan_version_are_distinct_and_future_version_not_active():
    p = plan(); pv = plan_version(effective_at=NOW + timedelta(days=1))
    assert p.plan_id != pv.plan_version_id
    evaluation = evaluate_entitlement([entitlement()], tenant_context=context(),
                                      capability_code="capability.analyze", as_of=NOW,
                                      plan_version=pv)
    assert not evaluation.entitled and evaluation.reason_code is EvaluationReason.NOT_YET_EFFECTIVE


def test_entitlement_is_separate_from_authority_and_exact_context():
    item = entitlement(); pv = plan_version(entitlement_ids=(item.entitlement_id,))
    result = evaluate_entitlement([item], tenant_context=context(), capability_code="capability.analyze",
                                  as_of=NOW, plan_version=pv)
    assert result.entitled and result.authority_granted is False
    wrong = context(tenant="tenant1:fictional:other", org="org1:fictional:other")
    denied = evaluate_entitlement([item], tenant_context=wrong, capability_code="capability.analyze", as_of=NOW)
    assert not denied.entitled


@pytest.mark.parametrize("status", [RecordStatus.SUSPENDED, RecordStatus.REVOKED, RecordStatus.EXPIRED, RecordStatus.INVALIDATED])
def test_inactive_entitlement_fails_closed(status):
    result = evaluate_entitlement([entitlement(status=status)], tenant_context=context(),
                                  capability_code="capability.analyze", as_of=NOW)
    assert not result.entitled


def test_conflicting_entitlements_detected_and_evaluation_tampering_rejected():
    first = entitlement(); second = entitlement()
    object.__setattr__(second, "entitlement_id", "ent1:fictional:duplicate")
    object.__setattr__(second, "_integrity", tuple(getattr(second, n) for n in type(second).__dataclass_fields__ if n != "_integrity"))
    result = evaluate_entitlement([first, second], tenant_context=context(), capability_code="capability.analyze", as_of=NOW)
    assert result.reason_code is EvaluationReason.CONFLICT
    object.__setattr__(result, "authority_granted", True)
    assert_code(BillingErrorCode.NOT_ENTITLED,
        lambda: calculate_customer_charge(customer_charge_id="charge1:fictional:tamper", usage=usage(),
            billing_profile=profile(), plan_version=plan_version(),
            billing_policy=BillingPolicy("pricing:included", VERSION, PricingMechanism.NO_CHARGE),
            provider_cost=None, created_at=NOW + timedelta(minutes=1), actor_ref=ACTOR,
            authority_decision=decision("billing_customer_charge", "charge1:fictional:tamper", "billing.charge.calculate"),
            entitlement_evaluation=result))


def test_allowance_consumption_is_derived_and_does_not_mutate_source():
    item = create_allowance(
        allowance_id="allow1:fictional:tokens", plan_version_id="planv1:fictional:standard:v1",
        tenant_context=context(), capability_code="capability.analyze",
        included=UsageQuantity(Decimal("200"), UsageUnit.TOKEN), period_type=PeriodType.MONTHLY,
        period_start=NOW - timedelta(days=1), period_end=NOW + timedelta(days=29),
        rollover_policy=RolloverPolicy.NONE,
    )
    original = item
    result = evaluate_allowance(item, [usage()], as_of=NOW)
    assert result.allowed and result.consumed.quantity == Decimal("150.000000")
    assert result.remaining.quantity == Decimal("50.000000") and item == original


def test_credit_ledger_balance_is_derived_and_overconsumption_rejects():
    account = create_credit_account(account_id="cracct1:fictional:alpha", tenant_id=TENANT,
        organization_id=ORG, credit_type=CreditType.EXPLORATION, status=RecordStatus.ACTIVE,
        unit=UsageUnit.EXECUTION, policy_id=POLICY, policy_version=VERSION)
    def movement(rid, kind, qty, at):
        return create_credit_movement(movement_id=rid, account=account, tenant_context=context(),
            movement_type=kind, quantity=UsageQuantity(Decimal(qty), UsageUnit.EXECUTION),
            occurred_at=at, source_ref="source:fictional", actor_ref=ACTOR,
            authority_decision=decision("billing_credit_movement", rid, "billing.credit.record"))
    grant = movement("crmov1:fictional:grant", CreditMovementType.GRANT, "3", NOW - timedelta(hours=2))
    use = movement("crmov1:fictional:consume", CreditMovementType.CONSUMPTION, "1", NOW - timedelta(hours=1))
    balance = calculate_credit_balance(account, [grant, use], as_of=NOW)
    assert balance.quantity.quantity == Decimal("2.000000")
    too_much = movement("crmov1:fictional:over", CreditMovementType.CONSUMPTION, "4", NOW)
    assert_code(BillingErrorCode.CREDIT_EXCEEDED,
                lambda: calculate_credit_balance(account, [grant, too_much], as_of=NOW))


def test_credits_are_not_money_and_expired_grant_is_unavailable():
    account = create_credit_account(account_id="cracct1:fictional:alpha", tenant_id=TENANT,
        organization_id=ORG, credit_type=CreditType.PREPAID, status=RecordStatus.ACTIVE,
        unit=UsageUnit.EXECUTION, policy_id=POLICY, policy_version=VERSION)
    rid = "crmov1:fictional:expired"
    expired = create_credit_movement(movement_id=rid, account=account, tenant_context=context(),
        movement_type=CreditMovementType.GRANT, quantity=UsageQuantity(Decimal("5"), UsageUnit.EXECUTION),
        occurred_at=NOW - timedelta(days=2), expires_at=NOW - timedelta(days=1),
        source_ref="source:fictional", actor_ref=ACTOR,
        authority_decision=decision("billing_credit_movement", rid, "billing.credit.record"))
    balance = calculate_credit_balance(account, [expired], as_of=NOW)
    assert type(balance.quantity) is UsageQuantity and balance.quantity.quantity == 0


def test_estimate_is_not_usage_and_absent_provider_remains_absent():
    estimate = create_usage_estimate(
        estimate_id="uest1:fictional:alpha", tenant_context=context(), principal_ref=ACTOR,
        capability_code="capability.analyze", quantities=(UsageQuantity(Decimal("10"), UsageUnit.TOKEN),),
        billing_policy_id="pricing:standard", created_at=NOW, expires_at=NOW + timedelta(minutes=5),
        classification=CLASSIFICATION,
    )
    assert estimate.provider_id is None and estimate.engine_id is None
    assert type(estimate) is not type(usage())
    assert validate_usage_estimate(estimate, tenant_context=context(),
                                   capability_code="capability.analyze", as_of=NOW)
    assert_code(BillingErrorCode.CHRONOLOGY_CONFLICT,
        lambda: validate_usage_estimate(estimate, tenant_context=context(),
            capability_code="capability.analyze", as_of=NOW + timedelta(minutes=6)))


def test_usage_requires_exact_chronology_and_honest_provider_pair():
    assert_code(BillingErrorCode.CHRONOLOGY_CONFLICT,
                lambda: usage(occurred_at=NOW, recorded_at=NOW - timedelta(seconds=1)))
    assert_code(BillingErrorCode.INVALID_RECORD,
        lambda: create_usage_event(usage_event_id="usage1:fictional:bad", tenant_context=context(),
            principal_ref="principal:worker", capability_code="capability.analyze",
            work_id="wrk1:fictional:analysis", attempt_id="atm1:fictional:run",
            input_quantities=(), output_quantities=(), status=UsageStatus.FAILED,
            occurred_at=NOW, recorded_at=NOW, billing_policy_id="pricing:standard",
            idempotency_ref="meter:bad", actor_ref=ACTOR,
            authority_decision=decision("billing_usage", "usage1:fictional:bad", "billing.usage.record"),
            classification=CLASSIFICATION, provider_id="provider:fictional"))
    assert_code(BillingErrorCode.INVALID_RECORD,
                lambda: usage(rid="usage1:fictional:unsafe",
                              idem="meter:unsafe", provider="provider:token:secret"))


def test_usage_record_contains_references_not_raw_content_and_is_immutable():
    item = usage()
    assert not hasattr(item, "prompt") and not hasattr(item, "response") and not hasattr(item, "metadata")
    with pytest.raises(FrozenInstanceError): item.status = UsageStatus.FAILED
    original = item.status; object.__setattr__(item, "status", UsageStatus.FAILED)
    assert_code(BillingErrorCode.INVALID_RECORD,
                lambda: validate_billing_collection(usage_events=[item], evaluated_at=NOW))
    object.__setattr__(item, "status", original)


def test_provider_cost_unknown_has_no_amount_and_known_cost_is_separate():
    event = usage(); rid = "pcost1:fictional:unknown"
    unknown = create_provider_cost(provider_cost_id=rid, usage=event, cost_source=CostSource.UNKNOWN,
        amount=None, recorded_at=NOW, actor_ref=ACTOR,
        authority_decision=decision("billing_provider_cost", rid, "billing.provider_cost.record"))
    assert unknown.amount is None
    assert provider_cost(event).usage_event_id == event.usage_event_id
    assert type(provider_cost(event)) is not type(charge(event=event))


def test_pass_through_and_margin_calculation_are_exact():
    event = usage()
    passthrough = charge(event=event)
    assert passthrough.amount == Money(Decimal("1.25"), Currency.USD)
    margin = BillingPolicy("pricing:margin", VERSION, PricingMechanism.PASS_THROUGH_WITH_MARGIN,
                           MarginPolicy(MarginMethod.PERCENT_OF_PROVIDER_COST, Decimal("20")))
    marked = charge(event=event, policy=margin)
    assert marked.amount == Money(Decimal("1.5"), Currency.USD)


def test_pass_through_requires_cost_and_no_charge_does_not_erase_cost():
    event = usage(); rid = "charge1:fictional:nocost"
    assert_code(BillingErrorCode.MISSING_REFERENCE,
        lambda: calculate_customer_charge(customer_charge_id=rid, usage=event, billing_profile=profile(),
            plan_version=plan_version(), billing_policy=BillingPolicy("pricing:pass", VERSION, PricingMechanism.PASS_THROUGH),
            provider_cost=None, created_at=NOW, actor_ref=ACTOR,
            authority_decision=decision("billing_customer_charge", rid, "billing.charge.calculate")))
    no_charge = BillingPolicy("pricing:none", VERSION, PricingMechanism.NO_CHARGE)
    zero = charge(event=event, policy=no_charge)
    assert zero.amount.amount == 0 and provider_cost(event).amount.amount > 0


def test_customer_charge_requires_exact_tenant_context_and_calculated_status():
    other_ctx = context(tenant="tenant1:fictional:other", org="org1:fictional:other")
    event = usage(rid="usage1:fictional:other:charge", ctx=other_ctx,
                  idem="meter:other:charge")
    rid = "charge1:fictional:other"
    assert_code(BillingErrorCode.TENANT_CONFLICT,
        lambda: calculate_customer_charge(customer_charge_id=rid, usage=event,
            billing_profile=profile(), plan_version=plan_version(),
            billing_policy=BillingPolicy("pricing:standard", VERSION, PricingMechanism.PASS_THROUGH),
            provider_cost=provider_cost(event), created_at=NOW + timedelta(minutes=1),
            actor_ref=ACTOR, authority_decision=decision("billing_customer_charge", rid,
                "billing.charge.calculate", ctx=other_ctx)))
    assert_code(BillingErrorCode.INVALID_RECORD,
        lambda: calculate_customer_charge(customer_charge_id="charge1:fictional:waived",
            usage=usage(), billing_profile=profile(), plan_version=plan_version(),
            billing_policy=BillingPolicy("pricing:none", VERSION, PricingMechanism.NO_CHARGE),
            provider_cost=None, created_at=NOW + timedelta(minutes=1), actor_ref=ACTOR,
            authority_decision=decision("billing_customer_charge", "charge1:fictional:waived",
                "billing.charge.calculate"), status=ChargeStatus.WAIVED))


def test_threshold_below_is_explicit_and_missing_approval_fails_closed():
    threshold = create_approval_threshold(threshold_id="thresh1:fictional:alpha",
        tenant_context=context(), capability_code="capability.analyze",
        basis=ThresholdBasis.ESTIMATED_CUSTOMER_CHARGE,
        approval_capability="approval.billing_threshold", effective_at=NOW - timedelta(days=1),
        money_threshold=Money(Decimal("5"), Currency.USD), policy_id=POLICY, policy_version=VERSION)
    below = evaluate_approval_threshold(threshold, as_of=NOW, money=Money(Decimal("4"), Currency.USD))
    above = evaluate_approval_threshold(threshold, as_of=NOW, money=Money(Decimal("6"), Currency.USD))
    assert below.satisfied and not below.approval_required
    assert above.approval_required and not above.satisfied


def test_threshold_accepts_only_intact_active_committed_approval():
    threshold = create_approval_threshold(threshold_id="thresh1:fictional:approval",
        tenant_context=context(), capability_code="capability.analyze",
        basis=ThresholdBasis.ACTUAL_CUSTOMER_CHARGE,
        approval_capability="approval.billing_threshold", effective_at=NOW - timedelta(days=1),
        money_threshold=Money(Decimal("1"), Currency.USD), policy_id=POLICY, policy_version=VERSION)
    valid = threshold_approval()
    result = evaluate_approval_threshold(threshold, as_of=NOW,
        money=Money(Decimal("2"), Currency.USD), approvals=[valid])
    assert result.satisfied and result.approval_id == valid.approval_id
    rejected = threshold_approval(status=ApprovalStatus.REJECTED)
    denied = evaluate_approval_threshold(threshold, as_of=NOW,
        money=Money(Decimal("2"), Currency.USD), approvals=[rejected])
    assert not denied.satisfied
    object.__setattr__(valid, "status", ApprovalStatus.WITHDRAWN)
    tampered = evaluate_approval_threshold(threshold, as_of=NOW,
        money=Money(Decimal("2"), Currency.USD), approvals=[valid])
    assert not tampered.satisfied


def test_budget_hard_and_soft_limits_and_cross_currency():
    hard = create_budget_policy(budget_id="budget1:fictional:hard", tenant_context=context(),
        basis=BudgetBasis.CUSTOMER_CHARGE, capability_code="capability.analyze",
        period_start=NOW - timedelta(days=1), period_end=NOW + timedelta(days=1),
        limit=Money(Decimal("10"), Currency.USD), warning_threshold=Money(Decimal("8"), Currency.USD),
        enforcement=BudgetEnforcement.HARD, override_capability="approval.billing_budget",
        policy_id=POLICY, policy_version=VERSION)
    denied = evaluate_budget(hard, [Money(Decimal("11"), Currency.USD)], as_of=NOW)
    assert not denied.allowed and denied.reason_code is EvaluationReason.BUDGET_DENIED
    assert_code(BillingErrorCode.CURRENCY_CONFLICT,
                lambda: evaluate_budget(hard, [Money(Decimal("1"), Currency.EUR)], as_of=NOW))


def test_adjustment_preserves_original_and_collection_bounds_reversal():
    event = usage(); original = provider_cost(event); rid = "adj1:fictional:correction"
    adjustment = create_adjustment(adjustment_id=rid, target_type=AdjustmentTarget.PROVIDER_COST,
        target_id=original.provider_cost_id, tenant_context=context(),
        adjustment_type=AdjustmentType.COST_CORRECTION, money=Money(Decimal("0.25"), Currency.USD),
        reason_code="cost.correction", occurred_at=NOW + timedelta(minutes=1), actor_ref=ACTOR,
        authority_decision=decision("billing_adjustment", rid, "billing.adjust"))
    validated = validate_billing_collection(usage_events=[event], provider_costs=[original],
                                             adjustments=[adjustment], evaluated_at=NOW)
    assert original.amount == Money(Decimal("1.25"), Currency.USD)
    assert adjustment.adjustment_id in validated.record_ids
    excessive = create_adjustment(adjustment_id="adj1:fictional:excess",
        target_type=AdjustmentTarget.PROVIDER_COST, target_id=original.provider_cost_id,
        tenant_context=context(), adjustment_type=AdjustmentType.COST_CORRECTION,
        money=Money(Decimal("2"), Currency.USD), reason_code="cost.correction",
        occurred_at=NOW + timedelta(minutes=1), actor_ref=ACTOR,
        authority_decision=decision("billing_adjustment", "adj1:fictional:excess", "billing.adjust"))
    assert_code(BillingErrorCode.ADJUSTMENT_EXCEEDED,
        lambda: validate_billing_collection(usage_events=[event], provider_costs=[original],
                                            adjustments=[excessive], evaluated_at=NOW))


def test_collection_detects_overlapping_plan_versions_and_duplicate_usage():
    p = plan(); one = plan_version(); two = create_plan_version(
        plan_version_id="planv1:fictional:standard:v2", plan_id=p.plan_id, tenant_id=TENANT,
        version_number=2, effective_at=NOW, expires_at=NOW + timedelta(days=40),
        currency=Currency.USD, policy_id=POLICY, policy_version=VERSION)
    assert_code(BillingErrorCode.PLAN_CONFLICT,
        lambda: validate_billing_collection(plans=[p], plan_versions=[one, two], evaluated_at=NOW))
    first = usage(); second = usage(rid="usage1:fictional:run:beta")
    assert_code(BillingErrorCode.DUPLICATE_USAGE,
        lambda: validate_billing_collection(usage_events=[first, second], evaluated_at=NOW))


def test_cross_tenant_cost_charge_and_view_deny():
    first = usage(); other_ctx = context(tenant="tenant1:fictional:other", org="org1:fictional:other")
    second = usage(rid="usage1:fictional:other", ctx=other_ctx, idem="meter:fictional:other")
    fields = frozenset({"usage_event_id", "tenant_id", "status"})
    d1 = decision("billing_usage", first.usage_event_id, "billing.usage.view", fields=fields)
    d2 = decision("billing_usage", second.usage_event_id, "billing.usage.view", fields=fields, ctx=other_ctx)
    assert_code(BillingErrorCode.TENANT_CONFLICT,
                lambda: create_derived_billing_view([(first, d1), (second, d2)], view_kind="usage.by_tenant"))


def test_projection_is_authority_bound_scalar_new_and_cost_permission_separate():
    event = usage(); fields = frozenset({"usage_event_id", "status", "principal_ref", "evidence_ids", "unknown"})
    auth = decision("billing_usage", event.usage_event_id, "billing.usage.view",
                    fields=fields, visible=frozenset({"usage_event_id", "status"}))
    projected = project_billing_record(event, auth)
    assert projected == {"usage_event_id": event.usage_event_id, "status": "SUCCEEDED"}
    assert projected is not event and all(type(v) in (str, bool, int, float, type(None)) for v in projected.values())
    cost = provider_cost(event); wrong = decision("billing_provider_cost", cost.provider_cost_id,
        "billing.charge.view", fields=frozenset({"amount_amount"}))
    assert_code(BillingErrorCode.INVALID_AUTHORITY, lambda: project_billing_record(cost, wrong))


def test_hostile_mapping_and_caller_computed_fields_fail_closed():
    class Hostile(dict):
        def items(self): raise RuntimeError("token=secret cost=999")
    auth = decision("billing_usage", "usage1:fictional:hostile", "billing.usage.view")
    assert_code(BillingErrorCode.INVALID_PROJECTION, lambda: project_billing_record(Hostile(), auth))
    with pytest.raises(BillingContractError): EntitlementEvaluation(True, EvaluationReason.ENTITLED, None, TENANT, "capability.analyze", True)


def test_safe_audit_is_bounded_and_contains_no_commercial_or_secret_values():
    audit = build_safe_billing_audit(audit_id="baudit1:fictional:alpha",
        action_code="billing.usage.record", record_type="usage",
        record_id="usage1:fictional:alpha", tenant_id=TENANT, outcome_code="recorded",
        reason_code="usage.recorded", policy_id=POLICY, policy_version=VERSION,
        timestamp=NOW, capability_code="capability.analyze", usage_unit=UsageUnit.TOKEN,
        currency=Currency.USD)
    assert type(audit) is SafeBillingAudit
    serialized = repr(audit).lower()
    for forbidden in ("prompt", "response", "password", "cookie", "session", "margin", "1.25"):
        assert forbidden not in serialized
    assert not hasattr(audit, "authorized") and not hasattr(audit, "payment")
    assert_code(BillingErrorCode.INVALID_AUDIT,
        lambda: build_safe_billing_audit(audit_id="baudit1:bad\nline", action_code="billing.usage.record",
            record_type="usage", record_id="usage1:fictional:alpha", tenant_id=TENANT,
            outcome_code="recorded", reason_code="usage.recorded", policy_id=POLICY,
            policy_version=VERSION, timestamp=NOW))


def test_derived_view_requires_all_sources_authorized_and_keeps_currency_buckets():
    item = charge(); fields = frozenset({"customer_charge_id", "amount_amount", "amount_currency", "tenant_id"})
    allowed = decision("billing_customer_charge", item.customer_charge_id, "billing.charge.view", fields=fields)
    view = create_derived_billing_view([(item, allowed)], view_kind="charge.summary")
    assert view.totals == (("USD", "1.250000"),) and not view.official_statement
    denied = decision("billing_customer_charge", item.customer_charge_id, "billing.charge.view", fields=fields, allowed=False)
    assert_code(BillingErrorCode.SOURCE_NOT_AUTHORIZED,
                lambda: create_derived_billing_view([(item, denied)], view_kind="charge.summary"))
    id_only = decision("billing_customer_charge", item.customer_charge_id,
        "billing.charge.view", fields=fields, visible=frozenset({"customer_charge_id"}))
    hidden = create_derived_billing_view([(item, id_only)], view_kind="charge.summary")
    assert hidden.totals == ()


def test_forged_and_mutated_authority_decisions_reject_and_locale_timezone_do_not_matter():
    rid = "usage1:fictional:locale"; ctx = context()
    en = decision("billing_usage", rid, "billing.usage.record", ctx=ctx, locale="en", tz="UTC")
    zh = decision("billing_usage", rid, "billing.usage.record", ctx=ctx, locale="zh-Hans", tz="Asia/Shanghai")
    assert en.allowed == zh.allowed and en.action_code == zh.action_code
    object.__setattr__(en, "resource_id", "usage1:fictional:mutated")
    assert_code(BillingErrorCode.INVALID_AUTHORITY,
        lambda: create_usage_event(usage_event_id=rid, tenant_context=ctx,
            principal_ref="principal:worker", capability_code="capability.analyze",
            work_id="wrk1:fictional:analysis", attempt_id="atm1:fictional:run",
            input_quantities=(), output_quantities=(), status=UsageStatus.FAILED,
            occurred_at=NOW, recorded_at=NOW, billing_policy_id="pricing:standard",
            idempotency_ref="meter:locale", actor_ref=ACTOR, authority_decision=en,
            classification=CLASSIFICATION))


def test_unknown_contract_major_fails_closed():
    assert_code(BillingErrorCode.INVALID_VERSION,
        lambda: create_plan(plan_id="plan1:fictional:future", tenant_id=TENANT,
            display_label="Future", status=PlanStatus.ACTIVE, created_at=NOW,
            policy_id=POLICY, policy_version=VERSION,
            contract_version="marketmatch-billing-entitlements-usage-v2"))


def test_compatibility_adapter_fails_honestly_and_no_runtime_seam_exists():
    assert_code(BillingErrorCode.UNSUPPORTED_ADAPTER,
                lambda: adapt_current_usage({"tokens_used": 10, "model": "fictional"}))
    root = Path(__file__).parents[1]
    route_files = [root / "app.py", *(root / "routes").rglob("*.py")]
    assert all("marketmatch_billing" not in path.read_text(encoding="utf-8") for path in route_files)
    assert not any("billing" in path.name.lower() for path in (root / "alembic" / "versions").glob("*.py"))


def test_module_is_pure_and_reuses_all_committed_kernels():
    root = Path(__file__).parents[1]; path = root / "src" / "marketmatch_billing.py"
    source = path.read_text(encoding="utf-8"); tree = ast.parse(source)
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert {"src.marketmatch_authority", "src.marketmatch_evidence",
            "src.marketmatch_operational_context", "src.marketmatch_tenancy",
            "src.marketmatch_truth_accountability", "src.marketmatch_work_orchestration"} <= imports
    forbidden_imports = ("requests", "httpx", "sqlalchemy", "sqlite3", "stripe", "quickbooks", "openai")
    assert not any(f"import {name}" in source or f"from {name}" in source for name in forbidden_imports)
    for token in ("localStorage", "sessionStorage", "open(", ".write(", ".execute(", "SessionLocal", "subprocess."):
        assert token not in source


def test_adr_covers_required_boundaries_and_deferrals():
    adr = (Path(__file__).parents[1] / "docs" / "adr" /
           "0007-marketmatch-billing-entitlements-usage-kernel-v1.md").read_text(encoding="utf-8")
    for phrase in ("Entitlement", "Authority", "membership", "Plan Version", "Allowance", "Credits",
                   "Usage Estimate", "Usage Event", "Provider Cost", "Customer Charge", "Approval",
                   "Budget", "Decimal", "currency", "Tenant", "Work", "Evidence", "safe audit",
                   "Derived views", "persistence", "Stripe", "QuickBooks", "tax", "invoice", "Limitations"):
        assert phrase.lower() in adr.lower()
