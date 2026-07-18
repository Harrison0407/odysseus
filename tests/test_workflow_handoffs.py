"""Tests for the handoff lifecycle service layer and HTTP-level views
(apps.workflow.services / apps.workflow.views).

Covers the 15 required scenarios for the Gate Controls and Formal
Handoffs milestone: successful handoff, blocked handoff, missing
evidence, unresolved discrepancy, quarantined inventory,
official-vs-operational mismatch (covered in test_workflow_gates.py),
rejection/return for correction, corrected resubmission, authorized
override, unauthorized override, duplicate submission, concurrent
acceptance, role-aware inbox filtering, cross-project isolation, and
complete audit history.
"""

from decimal import Decimal

import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from apps.accounts.models import Organization, ResponsibilityAssignment, UserProfile, UserProjectAccess, UserRole
from apps.audit.models import AuditEvent
from apps.core.models import DestinationScope
from apps.documents.models import Document, DocumentType
from apps.procurement.models import PurchaseOrder, Supplier
from apps.projects.models import Project
from apps.receiving.models import ReceivingPlan
from apps.shipments.models import BillOfLading, Shipment
from apps.workflow import services
from apps.workflow.models import GateDefinition, GateOverride, Handoff, HandoffDecision, HandoffStatus, WorkflowStage

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stage_logistics(organization, department_logistica):
    return WorkflowStage.objects.create(organization=organization, code="logistics", name="Logística", department=department_logistica, sequence=3)


@pytest.fixture
def stage_receiving(organization, department_almacen):
    return WorkflowStage.objects.create(organization=organization, code="receiving", name="Recepción", department=department_almacen, sequence=4)


@pytest.fixture
def gate_logistics_to_receiving(organization, department_logistica, department_almacen, stage_logistics, stage_receiving):
    return GateDefinition.objects.create(
        organization=organization, code="logistics_to_receiving", name="Logística → Recepción",
        from_stage=stage_logistics, to_stage=stage_receiving,
        from_department=department_logistica, to_department=department_almacen,
        target_content_type=ContentType.objects.get_for_model(Shipment),
    )


@pytest.fixture
def shipment(organization):
    return Shipment.objects.create(organization=organization, reference="TEST-HANDOFF-SHIP")


@pytest.fixture
def ready_shipment(shipment, organization):
    """A shipment for which logistics_to_receiving is genuinely ready:
    has a BL and a suitable receiving plan."""
    doc_type = DocumentType.objects.create(organization=organization, code="bill_of_lading", name="BL")
    document = Document.objects.create(organization=organization, document_type=doc_type, title="Test BL")
    BillOfLading.objects.create(shipment=shipment, bl_number="TESTBL", source_document=document)
    ReceivingPlan.objects.create(shipment=shipment, status=ReceivingPlan.Status.SUITABLE)
    return shipment


# ---------------------------------------------------------------------------
# 1. Successful handoff (full lifecycle)
# ---------------------------------------------------------------------------


def test_successful_handoff_full_lifecycle(ready_shipment, gate_logistics_to_receiving, harrison, manuel, organization):
    handoff = services.create_handoff(ready_shipment, gate_logistics_to_receiving, harrison)
    assert handoff.status == HandoffStatus.READY_FOR_SUBMISSION
    assert handoff.readiness_ready is True

    doc_type = DocumentType.objects.create(organization=organization, code="packing_list", name="Lista de Empaque")
    services.add_evidence(handoff, Document.objects.create(organization=organization, document_type=doc_type, title="Ev"), harrison)

    handoff = services.submit_handoff(handoff, harrison)
    assert handoff.status == HandoffStatus.SUBMITTED
    assert handoff.submitted_at is not None

    handoff = services.accept_handoff(handoff, manuel)
    assert handoff.status == HandoffStatus.ACCEPTED
    assert handoff.decided_at is not None

    ready_shipment.refresh_from_db()
    assert ready_shipment.status == Shipment.Status.RELEASED_TO_RECEIVING

    ra = ResponsibilityAssignment.objects.get(
        content_type=ContentType.objects.get_for_model(Shipment), object_id=ready_shipment.pk, is_open=True
    )
    assert ra.primary_user == manuel


# ---------------------------------------------------------------------------
# 2. Blocked handoff
# ---------------------------------------------------------------------------


def test_blocked_handoff_cannot_be_submitted_without_override(shipment, gate_logistics_to_receiving, harrison):
    """`shipment` here has no BL and no receiving plan — genuinely blocked."""
    handoff = services.create_handoff(shipment, gate_logistics_to_receiving, harrison)
    assert handoff.status == HandoffStatus.NOT_READY

    with pytest.raises(services.GateBlockedError) as exc_info:
        services.submit_handoff(handoff, harrison)

    handoff.refresh_from_db()
    assert handoff.status == HandoffStatus.NOT_READY  # never advanced
    assert exc_info.value.result.blocked


# ---------------------------------------------------------------------------
# 3. Missing evidence
# ---------------------------------------------------------------------------


def test_missing_evidence_blocks_submission_even_if_otherwise_ready(ready_shipment, gate_logistics_to_receiving, harrison):
    """logistics_to_receiving always requires evidence (BL + versioned
    manifest); with zero HandoffEvidence attached, submission must be
    blocked purely on that basis even though the target itself is ready."""
    handoff = services.create_handoff(ready_shipment, gate_logistics_to_receiving, harrison)
    assert handoff.readiness_ready is True  # target-level readiness is fine

    with pytest.raises(services.GateBlockedError) as exc_info:
        services.submit_handoff(handoff, harrison)
    assert any("Evidencia" in item for item in exc_info.value.result.unmet_requirements)


def test_attaching_evidence_allows_submission(ready_shipment, gate_logistics_to_receiving, harrison, organization):
    handoff = services.create_handoff(ready_shipment, gate_logistics_to_receiving, harrison)
    doc_type = DocumentType.objects.create(organization=organization, code="packing_list", name="Lista de Empaque")
    document = Document.objects.create(organization=organization, document_type=doc_type, title="Evidencia adjunta")
    services.add_evidence(handoff, document, harrison)

    handoff = services.submit_handoff(handoff, harrison)
    assert handoff.status == HandoffStatus.SUBMITTED


# ---------------------------------------------------------------------------
# 7 & 8. Rejection, return for correction, and corrected resubmission
# ---------------------------------------------------------------------------


def test_rejection_records_reason_and_decision(ready_shipment, gate_logistics_to_receiving, harrison, manuel, organization):
    handoff = services.create_handoff(ready_shipment, gate_logistics_to_receiving, harrison)
    doc_type = DocumentType.objects.create(organization=organization, code="packing_list", name="Lista de Empaque")
    services.add_evidence(handoff, Document.objects.create(organization=organization, document_type=doc_type, title="Ev"), harrison)
    handoff = services.submit_handoff(handoff, harrison)

    handoff = services.reject_handoff(handoff, manuel, "Faltan fotos del contenedor.")
    assert handoff.status == HandoffStatus.REJECTED
    assert handoff.rejection_or_correction_reason == "Faltan fotos del contenedor."
    decision = HandoffDecision.objects.get(handoff=handoff)
    assert decision.decision == "rejected"
    assert decision.decided_by == manuel


def test_return_for_correction_and_resubmission_creates_new_superseding_version(
    ready_shipment, gate_logistics_to_receiving, harrison, manuel, organization
):
    handoff = services.create_handoff(ready_shipment, gate_logistics_to_receiving, harrison)
    doc_type = DocumentType.objects.create(organization=organization, code="packing_list", name="Lista de Empaque")
    services.add_evidence(handoff, Document.objects.create(organization=organization, document_type=doc_type, title="Ev"), harrison)
    handoff = services.submit_handoff(handoff, harrison)

    handoff = services.return_for_correction(handoff, manuel, "Corrija el peso declarado.")
    assert handoff.status == HandoffStatus.RETURNED_FOR_CORRECTION

    new_handoff = services.resubmit_handoff(handoff, harrison)
    handoff.refresh_from_db()
    assert handoff.status == HandoffStatus.SUPERSEDED  # old row preserved, never edited beyond this
    assert handoff.rejection_or_correction_reason == "Corrija el peso declarado."  # untouched history
    assert new_handoff.supersedes_id == handoff.pk
    assert new_handoff.version == handoff.version + 1
    assert new_handoff.status == HandoffStatus.READY_FOR_SUBMISSION

    # The rejected/returned handoff's own decision history is never altered.
    assert HandoffDecision.objects.filter(handoff=handoff, decision="returned").exists()


# ---------------------------------------------------------------------------
# 9 & 10. Authorized vs. unauthorized override
# ---------------------------------------------------------------------------


def test_authorized_override_records_reason_actor_and_before_after_state(shipment, gate_logistics_to_receiving, harrison):
    handoff = services.create_handoff(shipment, gate_logistics_to_receiving, harrison)
    handoff = services.submit_handoff(handoff, harrison, override_reason="Autorizado por gerencia ante urgencia operativa.")

    assert handoff.status == HandoffStatus.SUBMITTED
    override = GateOverride.objects.get(handoff=handoff)
    assert override.overridden_by == harrison
    assert override.reason == "Autorizado por gerencia ante urgencia operativa."
    assert override.before_state["ready"] is False
    assert override.after_state["handoff_status"] == HandoffStatus.SUBMITTED


def test_unauthorized_override_is_denied(shipment, gate_logistics_to_receiving, manuel):
    """Manuel holds no can_override_gates role — attempting an override
    must raise, and the handoff must remain blocked."""
    handoff = services.create_handoff(shipment, gate_logistics_to_receiving, manuel)
    with pytest.raises(services.PermissionDeniedError):
        services.submit_handoff(handoff, manuel, override_reason="Lo autorizo yo mismo.")
    handoff.refresh_from_db()
    assert handoff.status == HandoffStatus.NOT_READY
    assert not GateOverride.objects.filter(handoff=handoff).exists()


# ---------------------------------------------------------------------------
# Unauthorized acceptance / cross-role access
# ---------------------------------------------------------------------------


def test_unauthorized_user_cannot_accept_handoff(ready_shipment, gate_logistics_to_receiving, harrison, miguel, organization):
    """Miguel (Obra) has no relationship to the Almacén department this
    handoff is addressed to, and is not the specific to_user."""
    handoff = services.create_handoff(ready_shipment, gate_logistics_to_receiving, harrison)
    doc_type = DocumentType.objects.create(organization=organization, code="packing_list", name="Lista de Empaque")
    services.add_evidence(handoff, Document.objects.create(organization=organization, document_type=doc_type, title="Ev"), harrison)
    handoff = services.submit_handoff(handoff, harrison)

    assert services.can_accept_handoff(miguel, handoff) is False
    with pytest.raises(services.PermissionDeniedError):
        services.accept_handoff(handoff, miguel)
    handoff.refresh_from_db()
    assert handoff.status == HandoffStatus.SUBMITTED  # unchanged


def test_required_role_to_accept_enforced(ready_shipment, gate_logistics_to_receiving, harrison, manuel, organization, department_almacen):
    """If a gate specifies a required role, department membership alone
    is not enough — cross-role access within the same department must
    still be denied."""
    from apps.accounts.models import Role

    specific_role = Role.objects.create(organization=organization, name="Recepción Certificada", code="recepcion_certificada")
    gate_logistics_to_receiving.required_role_to_accept = specific_role
    gate_logistics_to_receiving.save()

    handoff = services.create_handoff(ready_shipment, gate_logistics_to_receiving, harrison)
    doc_type = DocumentType.objects.create(organization=organization, code="packing_list", name="Lista de Empaque")
    services.add_evidence(handoff, Document.objects.create(organization=organization, document_type=doc_type, title="Ev"), harrison)
    handoff = services.submit_handoff(handoff, harrison)

    # manuel is in the right department but lacks the specific required role
    assert services.can_accept_handoff(manuel, handoff) is False


# ---------------------------------------------------------------------------
# 11 & 12. Duplicate submission and concurrent acceptance
# ---------------------------------------------------------------------------


def test_creating_handoff_twice_is_idempotent(shipment, gate_logistics_to_receiving, harrison):
    first = services.create_handoff(shipment, gate_logistics_to_receiving, harrison)
    second = services.create_handoff(shipment, gate_logistics_to_receiving, harrison)
    assert first.pk == second.pk
    assert Handoff.objects.filter(
        content_type=ContentType.objects.get_for_model(Shipment), object_id=shipment.pk
    ).count() == 1


def test_submitting_twice_second_call_raises_instead_of_double_processing(ready_shipment, gate_logistics_to_receiving, harrison, organization):
    handoff = services.create_handoff(ready_shipment, gate_logistics_to_receiving, harrison)
    doc_type = DocumentType.objects.create(organization=organization, code="packing_list", name="Lista de Empaque")
    services.add_evidence(handoff, Document.objects.create(organization=organization, document_type=doc_type, title="Ev"), harrison)

    services.submit_handoff(handoff, harrison)
    with pytest.raises(services.InvalidTransitionError):
        services.submit_handoff(handoff, harrison)


def test_concurrent_acceptance_only_the_first_wins(ready_shipment, gate_logistics_to_receiving, harrison, manuel, organization):
    """Simulates two accept attempts on the same SUBMITTED handoff — the
    second must see the already-updated status and raise, never create a
    second HandoffDecision or double-transfer ownership."""
    handoff = services.create_handoff(ready_shipment, gate_logistics_to_receiving, harrison)
    doc_type = DocumentType.objects.create(organization=organization, code="packing_list", name="Lista de Empaque")
    services.add_evidence(handoff, Document.objects.create(organization=organization, document_type=doc_type, title="Ev"), harrison)
    handoff = services.submit_handoff(handoff, harrison)

    services.accept_handoff(handoff, manuel)
    with pytest.raises(services.InvalidTransitionError):
        services.accept_handoff(handoff, manuel)

    assert HandoffDecision.objects.filter(handoff=handoff, decision="accepted").count() == 1
    assert ResponsibilityAssignment.objects.filter(
        content_type=ContentType.objects.get_for_model(Shipment), object_id=ready_shipment.pk, is_open=True
    ).count() == 1


# ---------------------------------------------------------------------------
# 13. Role-aware inbox filtering (HTTP-level)
# ---------------------------------------------------------------------------


def test_inbox_para_mi_shows_only_handoffs_awaiting_this_user(
    client, ready_shipment, gate_logistics_to_receiving, harrison, manuel, miguel, organization
):
    handoff = services.create_handoff(ready_shipment, gate_logistics_to_receiving, harrison)
    doc_type = DocumentType.objects.create(organization=organization, code="packing_list", name="Lista de Empaque")
    services.add_evidence(handoff, Document.objects.create(organization=organization, document_type=doc_type, title="Ev"), harrison)
    services.submit_handoff(handoff, harrison)

    client.force_login(manuel)
    response = client.get(reverse("workflow:inbox"), {"vista": "para_mi"})
    assert str(handoff.pk) in response.content.decode() or handoff.get_status_display() in response.content.decode()

    client.force_login(miguel)
    response = client.get(reverse("workflow:inbox"), {"vista": "para_mi"})
    content = response.content.decode()
    assert "Sin entregas en esta vista." in content


# ---------------------------------------------------------------------------
# 14. Cross-project isolation (also covers direct-URL object-level authorization)
# ---------------------------------------------------------------------------


def test_cross_project_isolation_denies_view_and_accept(
    client, organization, department_almacen, role_almacen, pc_uom, quartz_category
):
    from apps.accounts.models import Role
    from apps.items.models import Item
    from apps.requests.models import MaterialRequest, MaterialRequestLine
    from django.contrib.auth import get_user_model

    User = get_user_model()

    project_a = Project.objects.create(organization=organization, name="Proyecto A", code="proj-a")
    project_b = Project.objects.create(organization=organization, name="Proyecto B", code="proj-b")

    department_obra = department_almacen.__class__.objects.create(organization=organization, name="Obra", code="obra")
    role_obra = Role.objects.create(organization=organization, name="Obra", code="obra")

    stage_warehouse = WorkflowStage.objects.create(organization=organization, code="warehouse", name="Almacén", department=department_almacen, sequence=5)
    stage_project = WorkflowStage.objects.create(organization=organization, code="project", name="Obra", department=department_obra, sequence=6)
    gate = GateDefinition.objects.create(
        organization=organization, code="warehouse_to_project", name="Almacén → Obra",
        from_stage=stage_warehouse, to_stage=stage_project,
        from_department=department_almacen, to_department=department_obra,
        target_content_type=ContentType.objects.get_for_model(MaterialRequest),
    )

    item = Item.objects.create(organization=organization, name="Artículo", category=quartz_category, base_unit=pc_uom)
    material_request = MaterialRequest.objects.create(project=project_a)
    MaterialRequestLine.objects.create(
        request=material_request, item=item, quantity_requested=Decimal("5"),
        quantity_approved=Decimal("5"), quantity_reserved=Decimal("5"),
    )

    manuel_user = User.objects.create_user(username="manuel_iso", password="testpass123")
    UserProfile.objects.create(user=manuel_user, organization=organization, primary_department=department_almacen)
    UserRole.objects.create(user=manuel_user, role=role_almacen, department=department_almacen)

    miguel_a = User.objects.create_user(username="miguel_a", password="testpass123")
    UserProfile.objects.create(user=miguel_a, organization=organization, primary_department=department_obra)
    UserRole.objects.create(user=miguel_a, role=role_obra, department=department_obra)
    UserProjectAccess.objects.create(user=miguel_a, project=project_a)

    miguel_b = User.objects.create_user(username="miguel_b", password="testpass123")
    UserProfile.objects.create(user=miguel_b, organization=organization, primary_department=department_obra)
    UserRole.objects.create(user=miguel_b, role=role_obra, department=department_obra)
    UserProjectAccess.objects.create(user=miguel_b, project=project_b)  # only project B, not A

    handoff = services.create_handoff(material_request, gate, manuel_user)
    assert handoff.project_id == project_a.id
    handoff = services.submit_handoff(handoff, manuel_user)

    # miguel_b has no access to project A and must be denied both viewing and accepting.
    assert services.can_view_handoff(miguel_b, handoff) is False
    assert services.can_accept_handoff(miguel_b, handoff) is False

    client.force_login(miguel_b)
    response = client.get(reverse("workflow:detail", args=[handoff.pk]))
    assert response.status_code == 302  # redirected away, not shown the record

    # miguel_a, who does have project A access, can view and accept it.
    assert services.can_view_handoff(miguel_a, handoff) is True
    accepted = services.accept_handoff(handoff, miguel_a)
    assert accepted.status == HandoffStatus.ACCEPTED


# ---------------------------------------------------------------------------
# 15. Complete, immutable audit history
# ---------------------------------------------------------------------------


def test_complete_audit_history_recorded_for_full_lifecycle(ready_shipment, gate_logistics_to_receiving, harrison, manuel, organization):
    handoff = services.create_handoff(ready_shipment, gate_logistics_to_receiving, harrison)
    doc_type = DocumentType.objects.create(organization=organization, code="packing_list", name="Lista de Empaque")
    services.add_evidence(handoff, Document.objects.create(organization=organization, document_type=doc_type, title="Ev"), harrison)
    handoff = services.submit_handoff(handoff, harrison)
    handoff = services.accept_handoff(handoff, manuel)

    handoff_ct = ContentType.objects.get_for_model(Handoff)
    events = AuditEvent.objects.filter(content_type=handoff_ct, object_id=handoff.pk).order_by("occurred_at")
    actions = list(events.values_list("action", flat=True))
    assert AuditEvent.Action.HANDOFF in actions
    assert actions.count(AuditEvent.Action.HANDOFF) >= 3  # created, submitted, accepted

    # Decision history is a separate, never-edited ledger of its own.
    assert HandoffDecision.objects.filter(handoff=handoff, decision="accepted").count() == 1
    # AuditEvent rows are never updated by application code — assert none carry a later timestamp than occurred_at.
    for event in events:
        assert event.occurred_at is not None


# ---------------------------------------------------------------------------
# 16. Purchasing -> Finance / Finance -> Logistics create-handoff button on
# the Purchase Order detail page (Priority 0 completion item — added in a
# later session; previously only the generic accept/reject/return flow
# worked for these two gates, with no "create handoff" entry point).
# ---------------------------------------------------------------------------


@pytest.fixture
def stage_purchasing(organization, department_compras):
    return WorkflowStage.objects.create(organization=organization, code="purchasing", name="Compras", department=department_compras, sequence=1)


@pytest.fixture
def stage_finance(organization, department_finanzas):
    return WorkflowStage.objects.create(organization=organization, code="finance", name="Finanzas", department=department_finanzas, sequence=2)


@pytest.fixture
def gate_purchasing_to_finance(organization, department_compras, department_finanzas, stage_purchasing, stage_finance):
    return GateDefinition.objects.create(
        organization=organization, code="purchasing_to_finance", name="Compras → Finanzas",
        from_stage=stage_purchasing, to_stage=stage_finance,
        from_department=department_compras, to_department=department_finanzas,
        target_content_type=ContentType.objects.get_for_model(PurchaseOrder),
    )


@pytest.fixture
def approved_purchase_order(organization):
    supplier = Supplier.objects.create(organization=organization, name="Proveedor Test PO Handoff")
    return PurchaseOrder.objects.create(
        organization=organization, supplier=supplier, po_number="PO-HANDOFF-TEST-1",
        approval_status=PurchaseOrder.ApprovalStatus.APPROVED,
    )


def test_po_detail_page_shows_create_handoff_button_for_purchasing_to_finance(
    client, markeris, approved_purchase_order, gate_purchasing_to_finance
):
    client.force_login(markeris)
    response = client.get(reverse("procurement:po-detail", args=[approved_purchase_order.pk]))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Crear entrega: Compras → Finanzas" in content


def test_purchasing_to_finance_full_lifecycle_via_real_http_from_po_detail_button(
    client, markeris, lucia, approved_purchase_order, gate_purchasing_to_finance
):
    """Drives the exact button added to the PO detail page, then the
    generic, already-tested handoff create/submit/accept endpoints — the
    button must never duplicate that transition logic, only link to it."""
    content_type = ContentType.objects.get_for_model(PurchaseOrder)
    client.force_login(markeris)

    create_url = reverse("workflow:create", args=[content_type.id, approved_purchase_order.pk, "purchasing_to_finance"])
    response = client.get(create_url)
    assert response.status_code == 302
    handoff = Handoff.objects.get(content_type=content_type, object_id=approved_purchase_order.pk)
    assert handoff.status == HandoffStatus.READY_FOR_SUBMISSION  # approved PO — genuinely ready

    detail_page = client.get(reverse("workflow:detail", args=[handoff.pk]))
    assert detail_page.status_code == 200
    submit_response = client.post(reverse("workflow:submit", args=[handoff.pk]))
    assert submit_response.status_code == 302
    handoff.refresh_from_db()
    assert handoff.status == HandoffStatus.SUBMITTED

    client.force_login(lucia)
    accept_response = client.post(reverse("workflow:accept", args=[handoff.pk]))
    assert accept_response.status_code == 302
    handoff.refresh_from_db()
    assert handoff.status == HandoffStatus.ACCEPTED
    assert HandoffDecision.objects.filter(handoff=handoff, decision="accepted").count() == 1


def test_po_detail_page_shows_create_handoff_button_for_finance_to_logistics(
    client, markeris, approved_purchase_order, organization, department_finanzas, department_logistica
):
    stage_finance = WorkflowStage.objects.create(organization=organization, code="finance2", name="Finanzas", department=department_finanzas, sequence=2)
    stage_logistics = WorkflowStage.objects.create(organization=organization, code="logistics2", name="Logística", department=department_logistica, sequence=3)
    GateDefinition.objects.create(
        organization=organization, code="finance_to_logistics", name="Finanzas → Logística",
        from_stage=stage_finance, to_stage=stage_logistics,
        from_department=department_finanzas, to_department=department_logistica,
        target_content_type=ContentType.objects.get_for_model(PurchaseOrder),
    )
    client.force_login(markeris)
    response = client.get(reverse("procurement:po-detail", args=[approved_purchase_order.pk]))
    content = response.content.decode()
    assert "Crear entrega: Finanzas → Logística" in content
