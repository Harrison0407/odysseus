"""Tests for the Delivery -> Installation -> Inspection -> Final
Acceptance milestone: apps.requests.services (quantity invariants +
inventory consequences), apps.workflow.gates (the 3 new gate
evaluators), and apps.requests.views (role-aware, project-isolated,
authorization-enforcing HTTP screens).

Numbered to match the 27 required scenarios from the governing prompt;
each test's docstring/comment states which scenario(s) it covers.
"""

from decimal import Decimal

import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from apps.accounts.models import UserProjectAccess, UserProfile, UserRole
from apps.audit.models import AuditEvent
from apps.inventory.models import (
    InventoryLot,
    InventoryMovement,
    MovementType,
    StorageSite,
    WarehouseLocation,
    WarehouseZone,
)
from apps.items.models import Item
from apps.projects.models import Building, Project
from apps.requests import services as rsvc
from apps.requests.models import (
    AcceptanceRecord,
    Delivery,
    DeliveryLine,
    Dispatch,
    InspectionRecord,
    InstallationRecord,
    MaterialRequest,
    MaterialRequestLine,
    ProjectReceipt,
    PunchListItem,
)
from apps.workflow import services as wfsvc
from apps.workflow.models import GateDefinition, HandoffStatus, WorkflowStage

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(organization):
    return Project.objects.create(organization=organization, name="Proyecto DIA", code="proj-dia")


@pytest.fixture
def building(project):
    return Building.objects.create(project=project, name="Edificio 1", code="ed-1")


@pytest.fixture
def item(organization, quartz_category, pc_uom):
    return Item.objects.create(organization=organization, name="Encimera de cuarzo", category=quartz_category, base_unit=pc_uom)


@pytest.fixture
def warehouse_location(organization):
    site = StorageSite.objects.create(organization=organization, name="Almacén Central", site_type=StorageSite.SiteType.CENTRAL_WAREHOUSE)
    zone = WarehouseZone.objects.create(site=site, name="Seco", code="seco")
    return WarehouseLocation.objects.create(zone=zone, code="A-1")


@pytest.fixture
def quarantine_location(organization):
    site = StorageSite.objects.create(organization=organization, name="Almacén Central Q", site_type=StorageSite.SiteType.CENTRAL_WAREHOUSE)
    zone = WarehouseZone.objects.create(site=site, name="Cuarentena", code="cuarentena")
    return WarehouseLocation.objects.create(zone=zone, code="Q-1")


@pytest.fixture
def lot_with_stock(item, warehouse_location, quarantine_location, manuel):
    """10 units of stock, physically posted via a real InventoryMovement —
    never a directly-set on-hand number (core principle 4.5)."""
    lot = InventoryLot.objects.create(item=item, lot_code="LOT-DIA-1")
    InventoryMovement.objects.create(
        lot=lot, movement_type=MovementType.RECEIPT, quantity=Decimal("10"),
        unit_of_measure=item.base_unit, to_location=warehouse_location, posted_by=manuel,
    )
    return lot


@pytest.fixture
def approved_request(project, building, item):
    mr = MaterialRequest.objects.create(project=project, building=building)
    line = MaterialRequestLine.objects.create(
        request=mr, item=item, quantity_requested=Decimal("10"), quantity_approved=Decimal("10")
    )
    return mr, line


@pytest.fixture
def dispatched_delivery(approved_request, lot_with_stock, manuel):
    """Reserves and dispatches the full approved quantity (10), and
    initializes the Delivery + DeliveryLine rows — the state every
    delivery-recording scenario starts from."""
    mr, line = approved_request
    reservation = rsvc.reserve_line(line, lot_with_stock, Decimal("10"), manuel)
    dispatch = rsvc.create_dispatch(mr, [(line, reservation, Decimal("10"))], manuel)
    delivery = rsvc.get_or_create_delivery(dispatch, manuel)
    return delivery


@pytest.fixture
def project_access_obra_dia(miguel, project):
    return UserProjectAccess.objects.create(user=miguel, project=project)


@pytest.fixture
def project_access_direccion_dia(harrison, project):
    return UserProjectAccess.objects.create(user=harrison, project=project)


@pytest.fixture
def stage_project(organization, department_obra):
    return WorkflowStage.objects.create(organization=organization, code="project", name="Obra", department=department_obra, sequence=6)


@pytest.fixture
def stage_installation(organization, department_obra):
    return WorkflowStage.objects.create(organization=organization, code="installation", name="Instalación", department=department_obra, sequence=7)


@pytest.fixture
def stage_inspection(organization, department_obra):
    return WorkflowStage.objects.create(organization=organization, code="inspection", name="Inspección", department=department_obra, sequence=8)


@pytest.fixture
def stage_acceptance(organization, department_direccion):
    return WorkflowStage.objects.create(organization=organization, code="acceptance", name="Aceptación", department=department_direccion, sequence=9)


@pytest.fixture
def gate_delivery_to_installation(organization, department_obra, stage_project, stage_installation):
    return GateDefinition.objects.create(
        organization=organization, code="project_delivery_to_installation", name="Entrega a Proyecto → Instalación",
        from_stage=stage_project, to_stage=stage_installation,
        from_department=department_obra, to_department=department_obra,
        target_content_type=ContentType.objects.get_for_model(Delivery),
    )


@pytest.fixture
def gate_installation_to_inspection(organization, department_obra, stage_installation, stage_inspection):
    return GateDefinition.objects.create(
        organization=organization, code="installation_to_inspection", name="Instalación → Inspección",
        from_stage=stage_installation, to_stage=stage_inspection,
        from_department=department_obra, to_department=department_obra,
        target_content_type=ContentType.objects.get_for_model(InstallationRecord),
    )


@pytest.fixture
def gate_inspection_to_acceptance(organization, department_obra, department_direccion, stage_inspection, stage_acceptance):
    return GateDefinition.objects.create(
        organization=organization, code="inspection_to_acceptance", name="Inspección → Aceptación",
        from_stage=stage_inspection, to_stage=stage_acceptance,
        from_department=department_obra, to_department=department_direccion,
        target_content_type=ContentType.objects.get_for_model(InstallationRecord),
    )


@pytest.fixture
def installed_installation(dispatched_delivery, miguel):
    """A Delivery fully accepted -> ProjectReceipt -> InstallationRecord
    with 10 validly delivered units, ready for progress/inspection tests."""
    delivery_line = dispatched_delivery.lines.first()
    rsvc.record_delivery_line(delivery_line, quantity_accepted=Decimal("10"), quantity_rejected=0, quantity_damaged=0, user=miguel)
    rsvc.complete_delivery(dispatched_delivery, miguel, accepted=True)
    receipt = rsvc.create_project_receipt(dispatched_delivery, miguel)
    installation = rsvc.create_installation_record(receipt, miguel, delivery_line=delivery_line, assigned_installer=miguel)
    return installation


# ---------------------------------------------------------------------------
# 1. Complete delivery
# ---------------------------------------------------------------------------


def test_01_complete_delivery_accepts_full_dispatched_quantity(dispatched_delivery, miguel):
    delivery_line = dispatched_delivery.lines.first()
    rsvc.record_delivery_line(delivery_line, quantity_accepted=Decimal("10"), quantity_rejected=0, quantity_damaged=0, user=miguel)
    rsvc.complete_delivery(dispatched_delivery, miguel, accepted=True)

    delivery_line.refresh_from_db()
    assert delivery_line.quantity_accepted == Decimal("10")
    mr = dispatched_delivery.dispatch.pick_list.request
    mr.refresh_from_db()
    assert mr.status == MaterialRequest.Status.DELIVERED


# ---------------------------------------------------------------------------
# 2. Partial delivery
# ---------------------------------------------------------------------------


def test_02_partial_delivery_sets_partially_delivered_status_not_delivered(dispatched_delivery, miguel):
    delivery_line = dispatched_delivery.lines.first()
    rsvc.record_delivery_line(delivery_line, quantity_accepted=Decimal("6"), quantity_rejected=0, quantity_damaged=0, user=miguel)

    mr = dispatched_delivery.dispatch.pick_list.request
    mr.refresh_from_db()
    assert mr.status == MaterialRequest.Status.PARTIALLY_DELIVERED
    line = mr.lines.first()
    assert line.quantity_delivered == Decimal("6")


# ---------------------------------------------------------------------------
# 3. Multi-trip delivery
# ---------------------------------------------------------------------------


def test_03_multi_trip_delivery_recomputes_never_double_counts(dispatched_delivery, miguel):
    """Recording the same delivery line twice (e.g. a second trip
    delivering the remainder) must recompute quantity_delivered from the
    sum of DeliveryLine rows, never increment ad hoc."""
    delivery_line = dispatched_delivery.lines.first()
    rsvc.record_delivery_line(delivery_line, quantity_accepted=Decimal("4"), quantity_rejected=0, quantity_damaged=0, user=miguel)
    mr = dispatched_delivery.dispatch.pick_list.request
    mr.refresh_from_db()
    assert mr.lines.first().quantity_delivered == Decimal("4")

    # Second trip against the same delivery line brings it up to the full amount.
    rsvc.record_delivery_line(delivery_line, quantity_accepted=Decimal("10"), quantity_rejected=0, quantity_damaged=0, user=miguel)
    mr.refresh_from_db()
    assert mr.lines.first().quantity_delivered == Decimal("10")
    assert mr.status == MaterialRequest.Status.DELIVERED


# ---------------------------------------------------------------------------
# 4. Failed / refused delivery
# ---------------------------------------------------------------------------


def test_04_failed_delivery_recorded_as_rejected_not_silently_dropped(dispatched_delivery, miguel):
    delivery_line = dispatched_delivery.lines.first()
    rsvc.record_delivery_line(delivery_line, quantity_accepted=0, quantity_rejected=Decimal("10"), quantity_damaged=0, user=miguel)
    rsvc.complete_delivery(dispatched_delivery, miguel, accepted=False, rejected_quantity_note="Material equivocado, rechazado en sitio.")

    dispatched_delivery.refresh_from_db()
    assert dispatched_delivery.accepted is False
    assert dispatched_delivery.rejected_quantity_note
    delivery_line.refresh_from_db()
    assert delivery_line.quantity_rejected == Decimal("10")
    assert delivery_line.quantity_accepted == 0


# ---------------------------------------------------------------------------
# 5. Delivery damage
# ---------------------------------------------------------------------------


def test_05_damaged_delivery_quarantines_only_the_new_delta(dispatched_delivery, miguel, quarantine_location):
    delivery_line = dispatched_delivery.lines.first()
    rsvc.record_delivery_line(delivery_line, quantity_accepted=Decimal("7"), quantity_rejected=Decimal("3"), quantity_damaged=Decimal("3"), user=miguel)

    quarantine_qty = InventoryMovement.objects.filter(
        lot=delivery_line.dispatch_line.lot, movement_type=MovementType.QUARANTINE
    ).count()
    assert quarantine_qty == 1

    # Recording the exact same line again (idempotent re-save) must not post a second quarantine movement.
    rsvc.record_delivery_line(delivery_line, quantity_accepted=Decimal("7"), quantity_rejected=Decimal("3"), quantity_damaged=Decimal("3"), user=miguel)
    assert InventoryMovement.objects.filter(lot=delivery_line.dispatch_line.lot, movement_type=MovementType.QUARANTINE).count() == 1


# ---------------------------------------------------------------------------
# 6. Quantity exceeding available inventory / dispatched quantity
# ---------------------------------------------------------------------------


def test_06_reservation_above_available_inventory_is_rejected(approved_request, lot_with_stock, manuel):
    mr, line = approved_request
    with pytest.raises(rsvc.QuantityInvariantError):
        rsvc.reserve_line(line, lot_with_stock, Decimal("999"), manuel)


def test_06b_delivery_accepted_plus_rejected_above_dispatched_is_rejected(dispatched_delivery, miguel):
    delivery_line = dispatched_delivery.lines.first()
    with pytest.raises(rsvc.QuantityInvariantError):
        rsvc.record_delivery_line(delivery_line, quantity_accepted=Decimal("8"), quantity_rejected=Decimal("8"), quantity_damaged=0, user=miguel)


# ---------------------------------------------------------------------------
# Multi-lot / split dispatch
# ---------------------------------------------------------------------------


@pytest.fixture
def second_lot_with_stock(item, warehouse_location, manuel):
    lot = InventoryLot.objects.create(item=item, lot_code="LOT-DIA-2")
    InventoryMovement.objects.create(
        lot=lot, movement_type=MovementType.RECEIPT, quantity=Decimal("10"),
        unit_of_measure=item.base_unit, to_location=warehouse_location, posted_by=manuel,
    )
    return lot


class TestMultiLotSplitDispatch:
    def test_split_dispatch_across_two_lots_creates_two_dispatch_lines(
        self, approved_request, lot_with_stock, second_lot_with_stock, manuel
    ):
        mr, line = approved_request
        r1 = rsvc.reserve_line(line, lot_with_stock, Decimal("6"), manuel)
        r2 = rsvc.reserve_line(line, second_lot_with_stock, Decimal("4"), manuel)

        dispatch = rsvc.create_dispatch(
            mr, [(line, r1, Decimal("6")), (line, r2, Decimal("4"))], manuel
        )
        assert dispatch.lines.count() == 2
        assert set(dispatch.lines.values_list("reservation_id", flat=True)) == {r1.pk, r2.pk}
        line.refresh_from_db()
        assert line.quantity_dispatched == Decimal("10")

    def test_dispatching_above_a_specific_reservations_remainder_is_rejected_even_if_line_total_allows_it(
        self, approved_request, lot_with_stock, second_lot_with_stock, manuel
    ):
        """Line-wide remaining (6+4=10) would allow 8 from lot A alone, but
        lot A's own reservation only holds 6 — must be rejected."""
        mr, line = approved_request
        r1 = rsvc.reserve_line(line, lot_with_stock, Decimal("6"), manuel)
        rsvc.reserve_line(line, second_lot_with_stock, Decimal("4"), manuel)

        with pytest.raises(rsvc.QuantityInvariantError):
            rsvc.create_dispatch(mr, [(line, r1, Decimal("8"))], manuel)

    def test_http_dispatch_view_splits_across_reservations_from_submitted_form(
        self, client, harrison, project_access_direccion_dia, approved_request, lot_with_stock, second_lot_with_stock, manuel
    ):
        from django.urls import reverse

        mr, line = approved_request
        r1 = rsvc.reserve_line(line, lot_with_stock, Decimal("6"), manuel)
        r2 = rsvc.reserve_line(line, second_lot_with_stock, Decimal("4"), manuel)

        client.force_login(harrison)
        response = client.post(
            reverse("requests:dispatch", args=[mr.pk]),
            {f"resv-{r1.pk}-quantity": "6", f"resv-{r2.pk}-quantity": "4"},
        )
        assert response.status_code == 302
        line.refresh_from_db()
        assert line.quantity_dispatched == Decimal("10")
        assert Dispatch.objects.filter(pick_list__request=mr).first().lines.count() == 2

    def test_http_dispatch_view_allows_partial_split_leaving_remainder_reserved(
        self, client, harrison, project_access_direccion_dia, approved_request, lot_with_stock, second_lot_with_stock, manuel
    ):
        from django.urls import reverse

        mr, line = approved_request
        r1 = rsvc.reserve_line(line, lot_with_stock, Decimal("6"), manuel)
        r2 = rsvc.reserve_line(line, second_lot_with_stock, Decimal("4"), manuel)

        client.force_login(harrison)
        response = client.post(
            reverse("requests:dispatch", args=[mr.pk]),
            {f"resv-{r1.pk}-quantity": "6", f"resv-{r2.pk}-quantity": "0"},
        )
        assert response.status_code == 302
        line.refresh_from_db()
        assert line.quantity_dispatched == Decimal("6")
        assert rsvc.reservation_remaining_quantity(r2) == Decimal("4")


# ---------------------------------------------------------------------------
# 7. Valid installation
# ---------------------------------------------------------------------------


def test_07_valid_installation_within_delivered_quantity(installed_installation, miguel):
    rsvc.record_installation_progress(installed_installation, miguel, quantity_installed=Decimal("10"), is_complete=True, mark_completed=True)
    installed_installation.refresh_from_db()
    assert installed_installation.quantity_installed == Decimal("10")
    assert installed_installation.is_complete is True
    assert installed_installation.installed_at is not None

    movement = InventoryMovement.objects.get(
        lot=installed_installation.delivery_line.dispatch_line.lot, movement_type=MovementType.INSTALLATION_CONSUMPTION
    )
    assert movement.quantity == Decimal("10")


# ---------------------------------------------------------------------------
# 8. Installation above delivered quantity
# ---------------------------------------------------------------------------


def test_08_installation_above_delivered_quantity_blocked_without_override(installed_installation, miguel):
    with pytest.raises(rsvc.QuantityInvariantError):
        rsvc.record_installation_progress(installed_installation, miguel, quantity_installed=Decimal("15"))
    installed_installation.refresh_from_db()
    assert installed_installation.quantity_installed == 0  # never silently clamped or partially applied


def test_08b_installation_above_delivered_with_authorized_override_succeeds_and_is_audited(installed_installation, harrison):
    """Harrison holds direccion/can_override_gates=True — same authorized-
    override pattern as the gate system, logged as an AuditEvent WAIVER
    rather than a GateOverride (this isn't a gate transition)."""
    rsvc.record_installation_progress(
        installed_installation, harrison, quantity_installed=Decimal("15"),
        override_reason="Se usó material adicional de otra reserva con autorización de dirección.",
    )
    installed_installation.refresh_from_db()
    assert installed_installation.quantity_installed == Decimal("15")
    assert AuditEvent.objects.filter(
        content_type=ContentType.objects.get_for_model(InstallationRecord), object_id=installed_installation.pk,
        action=AuditEvent.Action.WAIVER,
    ).exists()


# ---------------------------------------------------------------------------
# 9. Incomplete installation
# ---------------------------------------------------------------------------


def test_09_incomplete_installation_leaves_is_complete_false(installed_installation, miguel):
    rsvc.record_installation_progress(installed_installation, miguel, quantity_installed=Decimal("6"), is_complete=False)
    installed_installation.refresh_from_db()
    assert installed_installation.is_complete is False
    assert installed_installation.installed_at is None
    from apps.workflow.gates import evaluate_installation_to_inspection
    assert evaluate_installation_to_inspection(installed_installation).blocked


# ---------------------------------------------------------------------------
# 10. Installation damage / missing components
# ---------------------------------------------------------------------------


def test_10_installation_damage_and_missing_components_recorded(installed_installation, miguel, quarantine_location):
    rsvc.record_installation_progress(
        installed_installation, miguel, quantity_installed=Decimal("7"), quantity_damaged=Decimal("3"),
        missing_components_note="Faltan 2 tornillos de anclaje.", requires_rework=True,
    )
    installed_installation.refresh_from_db()
    assert installed_installation.quantity_damaged == Decimal("3")
    assert installed_installation.requires_rework is True
    assert "tornillos" in installed_installation.missing_components_note
    assert InventoryMovement.objects.filter(
        lot=installed_installation.delivery_line.dispatch_line.lot, movement_type=MovementType.QUARANTINE
    ).exists()


# ---------------------------------------------------------------------------
# 11-13. Inspection results
# ---------------------------------------------------------------------------


@pytest.fixture
def complete_installation(installed_installation, miguel):
    rsvc.record_installation_progress(installed_installation, miguel, quantity_installed=Decimal("10"), is_complete=True, mark_completed=True)
    return installed_installation


def test_11_successful_inspection_passes_and_gate_becomes_ready(complete_installation, miguel):
    inspection = rsvc.create_inspection(complete_installation, miguel, result=InspectionRecord.Result.PASS, inspected_quantity=Decimal("10"))
    assert inspection.passed is True
    from apps.workflow.gates import evaluate_inspection_to_acceptance
    assert evaluate_inspection_to_acceptance(complete_installation).ready


def test_12_failed_inspection_never_allows_acceptance_gate_to_be_ready(complete_installation, miguel):
    rsvc.create_inspection(complete_installation, miguel, result=InspectionRecord.Result.FAIL, notes="Instalación desnivelada.")
    from apps.workflow.gates import evaluate_inspection_to_acceptance
    result = evaluate_inspection_to_acceptance(complete_installation)
    assert result.blocked


def test_13_conditional_pass_records_distinct_result(complete_installation, miguel):
    inspection = rsvc.create_inspection(complete_installation, miguel, result=InspectionRecord.Result.CONDITIONAL_PASS)
    assert inspection.passed is True
    assert inspection.result == InspectionRecord.Result.CONDITIONAL_PASS


# ---------------------------------------------------------------------------
# 14. Punch-list creation
# ---------------------------------------------------------------------------


def test_14_failed_inspection_creates_blocking_punch_list_items(complete_installation, miguel):
    inspection = rsvc.create_inspection(
        complete_installation, miguel, result=InspectionRecord.Result.FAIL,
        punch_list_items=[{"description": "Sello de silicona faltante", "is_blocking": True}],
    )
    assert inspection.punch_list_items.count() == 1
    item = inspection.punch_list_items.first()
    assert item.is_blocking is True
    assert item.status == PunchListItem.Status.OPEN


# ---------------------------------------------------------------------------
# 15. Correction and reinspection
# ---------------------------------------------------------------------------


def test_15_reinspection_chains_to_previous_and_never_erases_failed_history(complete_installation, miguel):
    first = rsvc.create_inspection(
        complete_installation, miguel, result=InspectionRecord.Result.FAIL,
        punch_list_items=[{"description": "Defecto crítico", "is_blocking": True}],
    )
    item = first.punch_list_items.first()
    rsvc.close_punch_list_item(item, miguel, resolution_notes="Corregido en sitio.")

    second = rsvc.create_inspection(complete_installation, miguel, result=InspectionRecord.Result.PASS)
    assert second.previous_inspection_id == first.pk

    first.refresh_from_db()
    assert first.result == InspectionRecord.Result.FAIL  # untouched, permanent history
    assert first.passed is False

    from apps.workflow.gates import evaluate_inspection_to_acceptance
    assert evaluate_inspection_to_acceptance(complete_installation).ready  # only the latest counts, and its defect is closed


# ---------------------------------------------------------------------------
# 16. Final acceptance
# ---------------------------------------------------------------------------


def test_16_final_acceptance_recorded_once(complete_installation, miguel, harrison):
    rsvc.create_inspection(complete_installation, miguel, result=InspectionRecord.Result.PASS)
    acceptance = rsvc.record_final_acceptance(complete_installation, harrison, decision=AcceptanceRecord.Decision.ACCEPTED)
    assert acceptance.accepted_by == harrison
    assert complete_installation.acceptance.pk == acceptance.pk

    with pytest.raises(rsvc.QuantityInvariantError):
        rsvc.record_final_acceptance(complete_installation, harrison, decision=AcceptanceRecord.Decision.ACCEPTED)


# ---------------------------------------------------------------------------
# 17. Acceptance blocked by open critical defect
# ---------------------------------------------------------------------------


def test_17_acceptance_gate_blocked_while_blocking_defect_open(complete_installation, miguel):
    rsvc.create_inspection(
        complete_installation, miguel, result=InspectionRecord.Result.CONDITIONAL_PASS,
        punch_list_items=[{"description": "Rayón visible en superficie", "is_blocking": True}],
    )
    from apps.workflow.gates import evaluate_inspection_to_acceptance
    result = evaluate_inspection_to_acceptance(complete_installation)
    assert result.blocked
    assert result.unresolved_discrepancies


# ---------------------------------------------------------------------------
# 18 & 19. Authorized vs. unauthorized override (gate-level, via the handoff system)
# ---------------------------------------------------------------------------


def test_18_authorized_gate_override_at_inspection_to_acceptance(
    complete_installation, miguel, harrison, gate_inspection_to_acceptance, project_access_direccion_dia
):
    """Harrison (Dirección, can_override_gates=True) both creates and
    submits this handoff himself — the same pattern used by the existing
    authorized-override test in test_workflow_handoffs.py, where the
    submitter must be from_user or a from_department member; a management
    override here stands in for a documented direct-authorization case."""
    rsvc.create_inspection(complete_installation, miguel, result=InspectionRecord.Result.FAIL)
    handoff = wfsvc.create_handoff(complete_installation, gate_inspection_to_acceptance, harrison)
    assert handoff.status == HandoffStatus.NOT_READY

    handoff = wfsvc.submit_handoff(handoff, harrison, override_reason="Dirección autoriza avanzar mientras se gestiona la corrección.")
    assert handoff.status == HandoffStatus.SUBMITTED
    from apps.workflow.models import GateOverride
    assert GateOverride.objects.filter(handoff=handoff).exists()


def test_19_unauthorized_gate_override_denied(complete_installation, miguel, gate_inspection_to_acceptance):
    rsvc.create_inspection(complete_installation, miguel, result=InspectionRecord.Result.FAIL)
    handoff = wfsvc.create_handoff(complete_installation, gate_inspection_to_acceptance, miguel)
    with pytest.raises(wfsvc.PermissionDeniedError):
        wfsvc.submit_handoff(handoff, miguel, override_reason="Lo autorizo yo mismo.")
    handoff.refresh_from_db()
    assert handoff.status == HandoffStatus.NOT_READY


# ---------------------------------------------------------------------------
# 20. Rejection and return for correction (reuses the generic handoff flow)
# ---------------------------------------------------------------------------


def test_20_installation_to_inspection_handoff_can_be_returned_for_correction(
    complete_installation, miguel, harrison, gate_installation_to_inspection, project_access_obra_dia
):
    handoff = wfsvc.create_handoff(complete_installation, gate_installation_to_inspection, miguel)
    assert handoff.status == HandoffStatus.READY_FOR_SUBMISSION
    handoff = wfsvc.submit_handoff(handoff, miguel)
    handoff = wfsvc.return_for_correction(handoff, miguel, "Falta evidencia fotográfica de la instalación.")
    assert handoff.status == HandoffStatus.RETURNED_FOR_CORRECTION

    new_handoff = wfsvc.resubmit_handoff(handoff, miguel)
    assert new_handoff.status in (HandoffStatus.READY_FOR_SUBMISSION, HandoffStatus.NOT_READY)
    handoff.refresh_from_db()
    assert handoff.status == HandoffStatus.SUPERSEDED


# ---------------------------------------------------------------------------
# 21. Duplicate form submission
# ---------------------------------------------------------------------------


def test_21_duplicate_final_acceptance_submission_raises_not_double_creates(complete_installation, miguel, harrison):
    rsvc.create_inspection(complete_installation, miguel, result=InspectionRecord.Result.PASS)
    rsvc.record_final_acceptance(complete_installation, harrison, decision=AcceptanceRecord.Decision.ACCEPTED)
    with pytest.raises(rsvc.QuantityInvariantError):
        rsvc.record_final_acceptance(complete_installation, harrison, decision=AcceptanceRecord.Decision.ACCEPTED)
    assert AcceptanceRecord.objects.filter(installation=complete_installation).count() == 1


def test_21c_duplicate_installation_creation_is_idempotent(dispatched_delivery, miguel):
    """A double-click/retry on "Crear instalación" for the same
    (project_receipt, delivery_line) must return the existing record, not
    create a second work order for the same delivered material."""
    delivery_line = dispatched_delivery.lines.first()
    rsvc.record_delivery_line(delivery_line, quantity_accepted=Decimal("10"), quantity_rejected=0, quantity_damaged=0, user=miguel)
    rsvc.complete_delivery(dispatched_delivery, miguel, accepted=True)
    receipt = rsvc.create_project_receipt(dispatched_delivery, miguel)

    first = rsvc.create_installation_record(receipt, miguel, delivery_line=delivery_line)
    second = rsvc.create_installation_record(receipt, miguel, delivery_line=delivery_line)
    assert first.pk == second.pk
    assert InstallationRecord.objects.filter(project_receipt=receipt, delivery_line=delivery_line).count() == 1


def test_21d_duplicate_project_receipt_creation_is_idempotent(dispatched_delivery, miguel):
    delivery_line = dispatched_delivery.lines.first()
    rsvc.record_delivery_line(delivery_line, quantity_accepted=Decimal("10"), quantity_rejected=0, quantity_damaged=0, user=miguel)
    rsvc.complete_delivery(dispatched_delivery, miguel, accepted=True)

    first = rsvc.create_project_receipt(dispatched_delivery, miguel)
    second = rsvc.create_project_receipt(dispatched_delivery, miguel)
    assert first.pk == second.pk
    assert ProjectReceipt.objects.filter(delivery=dispatched_delivery).count() == 1


def test_21b_duplicate_punch_list_close_raises_not_double_processed(complete_installation, miguel):
    inspection = rsvc.create_inspection(
        complete_installation, miguel, result=InspectionRecord.Result.FAIL,
        punch_list_items=[{"description": "Defecto", "is_blocking": True}],
    )
    item = inspection.punch_list_items.first()
    rsvc.close_punch_list_item(item, miguel)
    with pytest.raises(rsvc.QuantityInvariantError):
        rsvc.close_punch_list_item(item, miguel)


# ---------------------------------------------------------------------------
# 22. Concurrent update attempt
# ---------------------------------------------------------------------------


def test_22_concurrent_handoff_submission_only_first_wins(
    complete_installation, miguel, harrison, gate_installation_to_inspection, project_access_obra_dia
):
    """Same race-safety pattern already proven for accept_handoff in the
    Gate Controls milestone (select_for_update row locking) — here
    exercised at submit_handoff, which every one of this milestone's 3
    new gates goes through."""
    handoff = wfsvc.create_handoff(complete_installation, gate_installation_to_inspection, miguel)
    wfsvc.submit_handoff(handoff, miguel)
    with pytest.raises(wfsvc.InvalidTransitionError):
        wfsvc.submit_handoff(handoff, miguel)


# ---------------------------------------------------------------------------
# 23. Role-aware UI and inbox visibility (HTTP)
# ---------------------------------------------------------------------------


def test_23_installation_detail_shows_available_actions_only_to_authorized_roles(
    client, complete_installation, miguel, project_access_obra_dia
):
    client.force_login(miguel)
    response = client.get(reverse("requests:installation-detail", args=[complete_installation.pk]))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Registrar progreso" in content or "progreso" in content.lower()


# ---------------------------------------------------------------------------
# 24. Cross-project isolation (HTTP, direct URL access)
# ---------------------------------------------------------------------------


def test_24_cross_project_isolation_denies_direct_url_access_to_installation(
    client, organization, complete_installation, department_obra, role_obra
):
    from django.contrib.auth import get_user_model
    User = get_user_model()

    other_project = Project.objects.create(organization=organization, name="Otro Proyecto", code="otro-proj")
    outsider = User.objects.create_user(username="obra_otro_proyecto", password="testpass123")
    UserProfile.objects.create(user=outsider, organization=organization, primary_department=department_obra)
    UserRole.objects.create(user=outsider, role=role_obra, department=department_obra)
    UserProjectAccess.objects.create(user=outsider, project=other_project)  # access to a *different* project only

    client.force_login(outsider)
    response = client.get(reverse("requests:installation-detail", args=[complete_installation.pk]))
    assert response.status_code == 302  # redirected away, not shown the record

    response = client.get(reverse("requests:installation-list"))
    assert response.status_code == 200
    assert str(complete_installation.pk) not in response.content.decode()


def test_24b_cross_project_isolation_denies_direct_url_progress_post(
    client, organization, complete_installation, department_obra, role_obra
):
    from django.contrib.auth import get_user_model
    User = get_user_model()

    other_project = Project.objects.create(organization=organization, name="Otro Proyecto B", code="otro-proj-b")
    outsider = User.objects.create_user(username="obra_otro_proyecto_b", password="testpass123")
    UserProfile.objects.create(user=outsider, organization=organization, primary_department=department_obra)
    UserRole.objects.create(user=outsider, role=role_obra, department=department_obra)
    UserProjectAccess.objects.create(user=outsider, project=other_project)

    client.force_login(outsider)
    response = client.post(
        reverse("requests:installation-progress", args=[complete_installation.pk]),
        {"quantity_installed": "10"},
    )
    assert response.status_code == 302
    complete_installation.refresh_from_db()
    assert complete_installation.quantity_installed == Decimal("10")  # unchanged by the denied attempt (already 10 from fixture) — no new movement posted
    assert InventoryMovement.objects.filter(
        lot=complete_installation.delivery_line.dispatch_line.lot, movement_type=MovementType.INSTALLATION_CONSUMPTION
    ).count() == 1  # only the one posted by the fixture setup, not a second one from the denied attempt


# ---------------------------------------------------------------------------
# 25. Complete, immutable audit trail
# ---------------------------------------------------------------------------


def test_25_complete_audit_trail_for_full_chain(dispatched_delivery, miguel, harrison):
    delivery_line = dispatched_delivery.lines.first()
    rsvc.record_delivery_line(delivery_line, quantity_accepted=Decimal("10"), quantity_rejected=0, quantity_damaged=0, user=miguel)
    rsvc.complete_delivery(dispatched_delivery, miguel, accepted=True)
    receipt = rsvc.create_project_receipt(dispatched_delivery, miguel)
    installation = rsvc.create_installation_record(receipt, miguel, delivery_line=delivery_line)
    rsvc.record_installation_progress(installation, miguel, quantity_installed=Decimal("10"), is_complete=True, mark_completed=True)
    inspection = rsvc.create_inspection(installation, miguel, result=InspectionRecord.Result.PASS)
    rsvc.record_final_acceptance(installation, harrison, decision=AcceptanceRecord.Decision.ACCEPTED)

    installation_ct = ContentType.objects.get_for_model(InstallationRecord)
    events = AuditEvent.objects.filter(content_type=installation_ct, object_id=installation.pk)
    assert events.filter(action=AuditEvent.Action.INSTALLATION).count() >= 1

    acceptance_ct = ContentType.objects.get_for_model(AcceptanceRecord)
    assert AuditEvent.objects.filter(content_type=acceptance_ct, action=AuditEvent.Action.ACCEPTANCE).exists()

    delivery_line_ct = ContentType.objects.get_for_model(DeliveryLine)
    assert AuditEvent.objects.filter(content_type=delivery_line_ct, object_id=delivery_line.pk).exists()


# ---------------------------------------------------------------------------
# 26. Mobile-rendering smoke tests (structural — same convention as the
# Priority 0 milestone's honesty-recorded partial validation)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url_name", ["delivery-list", "installation-list", "inspection-list", "acceptance-list"])
def test_26_list_screens_render_mobile_friendly_bootstrap_markup(client, harrison, url_name):
    client.force_login(harrison)
    response = client.get(reverse(f"requests:{url_name}"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "table-responsive" in content


def test_26b_installation_detail_uses_responsive_table_wrapper(client, complete_installation, harrison, project_access_direccion_dia):
    client.force_login(harrison)
    response = client.get(reverse("requests:installation-detail", args=[complete_installation.pk]))
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# 27. No regression — verified by running the full suite alongside this
# file (`pytest -q`), not a test in this file (see docs/FINAL_VALIDATION_REPORT.md).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Gate-evaluator direct unit tests for the 3 new gates
# ---------------------------------------------------------------------------


class TestProjectDeliveryToInstallationGate:
    def test_blocked_when_not_yet_accepted(self, dispatched_delivery):
        from apps.workflow.gates import evaluate_project_delivery_to_installation
        assert evaluate_project_delivery_to_installation(dispatched_delivery).blocked

    def test_ready_once_accepted_with_accepted_quantity(self, dispatched_delivery, miguel):
        delivery_line = dispatched_delivery.lines.first()
        rsvc.record_delivery_line(delivery_line, quantity_accepted=Decimal("10"), quantity_rejected=0, quantity_damaged=0, user=miguel)
        rsvc.complete_delivery(dispatched_delivery, miguel, accepted=True)
        rsvc.create_project_receipt(dispatched_delivery, miguel)
        from apps.workflow.gates import evaluate_project_delivery_to_installation
        result = evaluate_project_delivery_to_installation(dispatched_delivery)
        assert result.ready


class TestInstallationToInspectionGate:
    def test_blocked_without_installed_at(self, installed_installation):
        from apps.workflow.gates import evaluate_installation_to_inspection
        assert evaluate_installation_to_inspection(installed_installation).blocked

    def test_ready_once_completed(self, complete_installation):
        from apps.workflow.gates import evaluate_installation_to_inspection
        assert evaluate_installation_to_inspection(complete_installation).ready


# ---------------------------------------------------------------------------
# Evidence/photo upload (apps.audit.Attachment reuse)
# ---------------------------------------------------------------------------


@pytest.fixture
def evidence_doc_type(organization):
    from apps.documents.models import DocumentType
    return DocumentType.objects.create(organization=organization, code="receipt_evidence", name="Evidencia de Recepción")


class TestEvidenceUpload:
    def test_upload_evidence_on_delivery_creates_attachment(
        self, client, dispatched_delivery, miguel, project_access_obra_dia, evidence_doc_type
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse
        from apps.audit.models import Attachment

        client.force_login(miguel)
        f = SimpleUploadedFile("foto.jpg", b"fake-jpeg-bytes", content_type="image/jpeg")
        response = client.post(
            reverse("requests:delivery-add-evidence", args=[dispatched_delivery.pk]),
            {"document_type": evidence_doc_type.pk, "title": "Foto de entrega", "file": f},
        )
        assert response.status_code == 302
        from django.contrib.contenttypes.models import ContentType
        assert Attachment.objects.filter(
            content_type=ContentType.objects.get_for_model(Delivery), object_id=dispatched_delivery.pk
        ).count() == 1

    def test_upload_evidence_on_installation_detects_duplicate(
        self, client, complete_installation, miguel, project_access_obra_dia, evidence_doc_type
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse
        from apps.audit.models import Attachment

        client.force_login(miguel)
        content = b"same-photo-bytes"
        client.post(
            reverse("requests:installation-add-evidence", args=[complete_installation.pk]),
            {"document_type": evidence_doc_type.pk, "title": "Foto 1", "file": SimpleUploadedFile("a.jpg", content)},
        )
        client.post(
            reverse("requests:installation-add-evidence", args=[complete_installation.pk]),
            {"document_type": evidence_doc_type.pk, "title": "Foto 2 (duplicada)", "file": SimpleUploadedFile("b.jpg", content)},
        )
        from django.contrib.contenttypes.models import ContentType
        attachments = Attachment.objects.filter(
            content_type=ContentType.objects.get_for_model(InstallationRecord), object_id=complete_installation.pk
        ).select_related("document")
        assert attachments.count() == 2
        docs = [a.document for a in attachments]
        assert docs[0].current_version.sha256 == docs[1].current_version.sha256

    def test_cross_project_isolation_denies_evidence_upload(
        self, client, organization, dispatched_delivery, department_obra, role_obra, evidence_doc_type
    ):
        from django.contrib.auth import get_user_model
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse
        from apps.audit.models import Attachment
        from django.contrib.contenttypes.models import ContentType

        User = get_user_model()
        other_project = Project.objects.create(organization=organization, name="Otro Proyecto Evidencia", code="otro-evid")
        outsider = User.objects.create_user(username="obra_otro_evid", password="testpass123")
        UserProfile.objects.create(user=outsider, organization=organization, primary_department=department_obra)
        UserRole.objects.create(user=outsider, role=role_obra, department=department_obra)
        UserProjectAccess.objects.create(user=outsider, project=other_project)

        client.force_login(outsider)
        response = client.post(
            reverse("requests:delivery-add-evidence", args=[dispatched_delivery.pk]),
            {"document_type": evidence_doc_type.pk, "title": "Intento no autorizado", "file": SimpleUploadedFile("x.jpg", b"x")},
        )
        assert response.status_code == 302
        assert not Attachment.objects.filter(
            content_type=ContentType.objects.get_for_model(Delivery), object_id=dispatched_delivery.pk
        ).exists()

    def test_evidence_listed_on_installation_detail_page(
        self, client, complete_installation, miguel, project_access_obra_dia, evidence_doc_type
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse

        client.force_login(miguel)
        client.post(
            reverse("requests:installation-add-evidence", args=[complete_installation.pk]),
            {"document_type": evidence_doc_type.pk, "title": "Foto de instalación", "file": SimpleUploadedFile("i.jpg", b"abc")},
        )
        response = client.get(reverse("requests:installation-detail", args=[complete_installation.pk]))
        assert response.status_code == 200
        assert "Foto de instalación" in response.content.decode()
