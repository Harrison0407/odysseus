"""Browser-facing tests for the Increment 2 A1 vertical slice."""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.governance import services as governance
from apps.governance.models import CapabilityGrant, Party, RiskFlag, RoleAssignment
from apps.procurement.models import ProcurementPackage
from apps.procurement_gates import services as gates
from apps.procurement_gates.models import (
    GateAttempt,
    GateDecision,
    GatePolicyVersion,
    PackagePolicyAssignment,
    _allow_controlled_assignment_write,
)
from apps.workflow.models import Handoff


pytestmark = pytest.mark.django_db
User = get_user_model()

CONFIDENTIAL_SENTINELS = (
    "CONFIDENTIAL_PACKAGE_NOTES_BROWSER",
    "CONFIDENTIAL_FACTORY_BROWSER",
    "CONFIDENTIAL_LEGAL_BROWSER",
    "CONFIDENTIAL_RISK_FREE_TEXT_BROWSER",
)


def _grant(user, capability, *, package=None, organization=None):
    return governance.grant_capability(
        capability,
        granted_to_user=user,
        package=package,
        organization=organization,
    )


def _pin(package):
    version = GatePolicyVersion.objects.get(
        policy__is_canonical_default=True,
        status=GatePolicyVersion.Status.PUBLISHED,
    )
    with _allow_controlled_assignment_write():
        return PackagePolicyAssignment.objects.create(
            package=package,
            policy_version=version,
            pinned_at=timezone.now(),
        )


def _add_roles(package, *, omit=None):
    role_codes = {
        "buyer_party": "buyer",
        "seller_or_exporter_party": "seller_of_record",
        "procurement_operator_party": "china_procurement_operator",
        "factory_or_site_party": "production_factory",
        "logistics_authority": "logistics_operator",
        "quality_authority": "quality_operator",
        "approval_authority": "buyer_approver",
    }
    for requirement, role_code in role_codes.items():
        if requirement == omit:
            continue
        party = Party.objects.create(
            display_name=(
                CONFIDENTIAL_SENTINELS[1]
                if role_code == "production_factory"
                else f"PRIVATE_{role_code}"
            ),
            legal_name=CONFIDENTIAL_SENTINELS[2],
            hosting_organization=package.organization,
            is_hidden_by_default=True,
        )
        RoleAssignment.objects.create(
            party=party,
            role_code=role_code,
            organization_context=package.organization,
            package=package,
            effective_from=timezone.now().date(),
        )


@pytest.fixture
def browser_context(organization):
    package = ProcurementPackage.objects.create(
        organization=organization,
        code="a1-browser-test",
        name="Synthetic A1 Browser Package",
        status=ProcurementPackage.Status.ACTIVE,
        notes=CONFIDENTIAL_SENTINELS[0],
    )
    assignment = _pin(package)
    users = {
        role: User.objects.create_user(username=f"browser-{role}", password="testpass123")
        for role in ("initializer", "evaluator", "reviewer", "approver", "viewer", "limited")
    }
    _grant(users["initializer"], "VIEW_PROCUREMENT_GATE_STATE", package=package)
    _grant(
        users["initializer"],
        gates.CREATE_PROCUREMENT_GATE_ATTEMPT,
        organization=organization,
    )
    _grant(users["evaluator"], "VIEW_PROCUREMENT_GATE_STATE", package=package)
    _grant(users["evaluator"], gates.EVALUATE_PROCUREMENT_GATE, package=package)
    _grant(users["evaluator"], gates.CREATE_PROCUREMENT_GATE_ATTEMPT, package=package)
    _grant(users["reviewer"], "VIEW_PROCUREMENT_GATE_STATE", package=package)
    _grant(users["reviewer"], gates.REQUEST_PROCUREMENT_GATE_REVIEW, package=package)
    _grant(users["approver"], "VIEW_PROCUREMENT_GATE_STATE", package=package)
    _grant(users["approver"], "APPROVE_GATE", package=package)
    _grant(users["viewer"], "VIEW_PROCUREMENT_GATE_STATE", package=package)
    _grant(users["limited"], "CREATE_EVIDENCE", package=package)
    _add_roles(package)
    handoff = Handoff.objects.create(
        content_type=ContentType.objects.get_for_model(ProcurementPackage),
        object_id=package.pk,
        organization=organization,
        status="submitted",
        readiness_snapshot={"preserve": "HANDOFF_BROWSER_SENTINEL"},
    )
    return {
        "package": package,
        "assignment": assignment,
        "users": users,
        "handoff": handoff,
        "initial_package_state": {
            "status": package.status,
            "is_frozen": package.is_frozen,
            "is_on_hold": package.is_on_hold,
            "frozen_snapshot": package.frozen_snapshot,
        },
    }


def _detail(context):
    return reverse("procurement:package-detail", args=[context["package"].pk])


def _initialize(context, key="browser-init"):
    return gates.initialize_package_gates(
        context["users"]["initializer"], context["package"].pk, idempotency_key=key
    )


def _ready_for_decision(context, *, prefix="browser"):
    attempt = _initialize(context, f"{prefix}-init")
    evaluation = gates.evaluate_a1(
        context["users"]["evaluator"], context["package"].pk, attempt.pk,
        idempotency_key=f"{prefix}-eval",
    )
    gates.request_a1_review(
        context["users"]["reviewer"], context["package"].pk, attempt.pk,
        idempotency_key=f"{prefix}-review",
    )
    return attempt, evaluation


def _post(client, user, name, context, data, *extra_args):
    client.force_login(user)
    url = reverse(name, args=[context["package"].pk, *extra_args])
    return client.post(url, data, follow=True)


def _assert_confidential_absent(response):
    body = response.content.decode()
    for sentinel in CONFIDENTIAL_SENTINELS:
        assert sentinel not in body


def test_package_detail_renders_safe_gate_section_and_denies_protected_detail(client, browser_context):
    context = browser_context
    client.force_login(context["users"]["viewer"])
    response = client.get(_detail(context))
    assert response.status_code == 200
    assert "Procurement Gates — A1 Deal Established" in response.content.decode()
    assert [row["code"] for row in response.context["gate_rows"]] == list("A1 A2 A3 A4 A5 A6".split())
    assert response.context["current_gate"] == "A1"
    assert str(context["assignment"].policy_version_id) in response.content.decode()
    _assert_confidential_absent(response)

    client.force_login(context["users"]["limited"])
    denied = client.get(_detail(context))
    body = denied.content.decode()
    assert denied.status_code == 200
    assert "No tiene permiso para ver el estado protegido" in body
    assert str(context["assignment"].policy_version_id) not in body
    _assert_confidential_absent(denied)


def test_initialize_action_and_idempotent_form_resubmission(client, browser_context):
    data = {"idempotency_key": "http-init-repeat"}
    first = _post(
        client, browser_context["users"]["initializer"],
        "procurement:package-gates-initialize", browser_context, data,
    )
    second = _post(
        client, browser_context["users"]["initializer"],
        "procurement:package-gates-initialize", browser_context, data,
    )
    assert first.status_code == second.status_code == 200
    assert GateAttempt.objects.filter(package=browser_context["package"]).count() == 1
    assert "A1 está abierto" in second.content.decode()
    conflict = _post(
        client, browser_context["users"]["initializer"],
        "procurement:package-gates-initialize", browser_context,
        {"idempotency_key": "http-init-conflict"},
    )
    assert "clave de idempotencia" in conflict.content.decode()
    _assert_confidential_absent(second)


def test_evaluate_blocked_and_safe_blocker_rendering(client, browser_context):
    attempt = _initialize(browser_context)
    RoleAssignment.objects.filter(package=browser_context["package"], role_code="buyer").delete()
    RiskFlag.objects.create(
        package=browser_context["package"],
        level=RiskFlag.Level.HIGH_RISK,
        notes=CONFIDENTIAL_SENTINELS[3],
    )
    response = _post(
        client, browser_context["users"]["evaluator"],
        "procurement:package-a1-evaluate", browser_context,
        {"attempt_id": attempt.pk, "idempotency_key": "http-eval-blocked"},
    )
    body = response.content.decode()
    assert "Evaluación A1 bloqueada" in body
    assert "A1_BUYER_ROLE_MISSING" in body
    assert "A1_UNRESOLVED_BLOCKING_RISK" in body
    _assert_confidential_absent(response)


def test_review_and_self_approval_are_authorized_and_safe(client, browser_context):
    context = browser_context
    attempt = _initialize(context)
    evaluated = _post(
        client, context["users"]["evaluator"], "procurement:package-a1-evaluate", context,
        {"attempt_id": attempt.pk, "idempotency_key": "http-eval-ready"},
    )
    evaluation = attempt.evaluations.get()
    assert "Evaluación A1 satisfecha" in evaluated.content.decode()
    reviewed = _post(
        client, context["users"]["reviewer"], "procurement:package-a1-request-review", context,
        {"attempt_id": attempt.pk, "idempotency_key": "http-review"},
    )
    assert "Revisión A1 solicitada" in reviewed.content.decode()

    _grant(context["users"]["reviewer"], "APPROVE_GATE", package=context["package"])
    denied = _post(
        client, context["users"]["reviewer"], "procurement:package-a1-decide", context,
        {
            "attempt_id": attempt.pk,
            "evaluation_id": evaluation.pk,
            "idempotency_key": "http-self-approve",
            "comment": "",
        },
        "approve",
    )
    assert "no puede aprobar su propio intento" in denied.content.decode()
    assert not GateDecision.objects.exists()
    _assert_confidential_absent(denied)


def test_wrong_package_and_stale_identifiers_fail_closed(client, browser_context, organization):
    context = browser_context
    attempt = _initialize(context)
    evaluation = gates.evaluate_a1(
        context["users"]["evaluator"], context["package"].pk, attempt.pk,
        idempotency_key="earlier-evaluation",
    )
    latest = gates.evaluate_a1(
        context["users"]["evaluator"], context["package"].pk, attempt.pk,
        idempotency_key="later-evaluation",
    )
    gates.request_a1_review(
        context["users"]["reviewer"], context["package"].pk, attempt.pk,
        idempotency_key="review-latest-evaluation",
    )
    other = ProcurementPackage.objects.create(
        organization=organization, code="a1-other-browser", name="Other Synthetic Package"
    )
    _pin(other)
    _grant(context["users"]["approver"], "VIEW_PROCUREMENT_GATE_STATE", package=other)
    _grant(context["users"]["approver"], "APPROVE_GATE", package=other)
    wrong_url = reverse("procurement:package-a1-decide", args=[other.pk, "approve"])
    client.force_login(context["users"]["approver"])
    wrong = client.post(wrong_url, {
        "attempt_id": attempt.pk,
        "evaluation_id": evaluation.pk,
        "idempotency_key": "wrong-package",
        "comment": "",
    }, follow=True)
    assert "No tiene permiso" in wrong.content.decode()
    assert not GateDecision.objects.exists()

    # The immutable earlier evaluation is stale after the later reviewed evaluation.
    stale = _post(
        client, context["users"]["approver"], "procurement:package-a1-decide", context,
        {
            "attempt_id": attempt.pk,
            "evaluation_id": evaluation.pk,
            "idempotency_key": "stale-evaluation-decision",
            "comment": "",
        },
        "approve",
    )
    assert "ya no es vigente" in stale.content.decode()
    assert not GateDecision.objects.exists()


def test_return_opens_new_attempt_and_preserves_history(client, browser_context):
    context = browser_context
    attempt, evaluation = _ready_for_decision(context)
    returned = _post(
        client, context["users"]["approver"], "procurement:package-a1-decide", context,
        {
            "attempt_id": attempt.pk,
            "evaluation_id": evaluation.pk,
            "idempotency_key": "http-return",
            "comment": "Synthetic correction requested",
        },
        "return",
    )
    assert "A1 devuelto" in returned.content.decode()
    assert "FAILED" in returned.content.decode()
    stale = _post(
        client, context["users"]["evaluator"], "procurement:package-a1-evaluate", context,
        {"attempt_id": attempt.pk, "idempotency_key": "http-stale-attempt"},
    )
    assert "ya no es vigente" in stale.content.decode()
    opened = _post(
        client, context["users"]["evaluator"], "procurement:package-a1-open-attempt", context,
        {"idempotency_key": "http-reattempt"},
    )
    assert "Nuevo intento A1 abierto" in opened.content.decode()
    assert list(
        GateAttempt.objects.filter(package=context["package"]).order_by("attempt_number")
        .values_list("attempt_number", flat=True)
    ) == [1, 2]
    assert GateDecision.objects.get(attempt=attempt).outcome == GateDecision.Outcome.FAILED
    _assert_confidential_absent(opened)


def test_approval_exposes_non_executable_a2_and_preserves_package_and_handoff(client, browser_context):
    context = browser_context
    attempt, evaluation = _ready_for_decision(context)
    data = {
        "attempt_id": attempt.pk,
        "evaluation_id": evaluation.pk,
        "idempotency_key": "http-approve-repeat",
        "comment": "",
    }
    approved = _post(
        client, context["users"]["approver"], "procurement:package-a1-decide", context,
        data, "approve",
    )
    replayed = _post(
        client, context["users"]["approver"], "procurement:package-a1-decide", context,
        data, "approve",
    )
    body = replayed.content.decode()
    assert approved.status_code == replayed.status_code == 200
    assert GateDecision.objects.filter(attempt=attempt).count() == 1
    assert "A1 aprobado" in body
    assert "A2 está disponible como gate actual" in body
    assert "No ejecutable todavía" in body
    assert "gates/a2" not in body.lower()
    attempt.refresh_from_db()
    assert attempt.closure_code == GateDecision.Outcome.PASSED
    context["package"].refresh_from_db()
    assert {
        key: getattr(context["package"], key)
        for key in context["initial_package_state"]
    } == context["initial_package_state"]
    context["handoff"].refresh_from_db()
    assert context["handoff"].status == "submitted"
    assert context["handoff"].readiness_snapshot == {"preserve": "HANDOFF_BROWSER_SENTINEL"}
    _assert_confidential_absent(replayed)


def test_unauthorized_action_is_denied_before_gate_history_exposure(client, browser_context):
    context = browser_context
    attempt = _initialize(context)
    response = _post(
        client, context["users"]["limited"], "procurement:package-a1-evaluate", context,
        {"attempt_id": attempt.pk, "idempotency_key": "unauthorized-http-eval"},
    )
    assert "No tiene permiso" in response.content.decode()
    assert not attempt.evaluations.exists()
    assert AuditEvent.objects.filter(action=AuditEvent.Action.PRIVILEGED_ACCESS_DENIED).exists()
    _assert_confidential_absent(response)
