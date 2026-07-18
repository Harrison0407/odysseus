"""Domain service layer for the Delivery -> Installation -> Inspection ->
Final Acceptance chain.

This module owns every quantity invariant along that chain (never let a
delivered/installed quantity exceed what was validly issued/delivered)
and every inventory consequence (every physical change is a real,
auditable `InventoryMovement` — core principle 4.5). It deliberately does
NOT reimplement stage-transition/handoff logic: creating and deciding the
`project_delivery_to_installation`, `installation_to_inspection`, and
`inspection_to_acceptance` handoffs is always done through
`apps.workflow.services`, called directly from the views. The one place
this module *does* react to a handoff decision is `record_final_acceptance`,
which is called right after a successful `apps.workflow.services.accept_handoff`
for the `inspection_to_acceptance` gate, to capture accepted-vs-conditional
detail that the generic handoff model has no reason to know about.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.inventory.models import (
    InventoryLot,
    InventoryMovement,
    InventoryReservation,
    MovementType,
    QuarantineRecord,
    WarehouseLocation,
)
from apps.workflow.services import can_override_gates

from .models import (
    AcceptanceRecord,
    Delivery,
    DeliveryLine,
    Dispatch,
    DispatchLine,
    InstallationRecord,
    InspectionRecord,
    MaterialRequest,
    MaterialRequestLine,
    PickList,
    ProjectReceipt,
    PunchListItem,
    Transfer,
)


class QuantityInvariantError(ValueError):
    """Raised whenever an operation would let a quantity exceed what was
    validly issued, reserved, delivered, or available — never silently
    clamped, always a hard stop unless an authorized override is used."""


# ---------------------------------------------------------------------------
# Lot/location helpers (ledger-derived, never a stored "current location")
# ---------------------------------------------------------------------------


def _lot_movement_totals(lot):
    on_hand = 0
    location_balances = {}
    for movement in InventoryMovement.objects.filter(lot=lot):
        if movement.to_location_id:
            location_balances[movement.to_location_id] = location_balances.get(movement.to_location_id, 0) + movement.quantity
            on_hand += movement.quantity
        if movement.from_location_id:
            location_balances[movement.from_location_id] = location_balances.get(movement.from_location_id, 0) - movement.quantity
            on_hand -= movement.quantity
    return on_hand, location_balances


def lot_on_hand_quantity(lot):
    on_hand, _ = _lot_movement_totals(lot)
    return on_hand


def lot_current_location(lot):
    _, balances = _lot_movement_totals(lot)
    for location_id, qty in balances.items():
        if qty > 0:
            return WarehouseLocation.objects.filter(pk=location_id).first()
    return None


def lot_reserved_quantity(lot):
    return (
        InventoryReservation.objects.filter(lot=lot, is_active=True).aggregate(total=Sum("quantity"))["total"] or 0
    )


def lot_available_quantity(lot):
    return lot_on_hand_quantity(lot) - lot_reserved_quantity(lot)


def _damage_quarantine_location():
    return WarehouseLocation.objects.filter(zone__code="cuarentena").first()


@transaction.atomic
def transfer_lot(lot, from_location, to_location, quantity, user, *, reason="", override_reason=None) -> "Transfer":
    """Wires up the previously-unused `Transfer` model (modeled since
    Priority 0, never posted by any code path before this milestone).
    Never a silent relocation: posts a real `InventoryMovement`
    (`MovementType.TRANSFER`) and enforces destination storage
    suitability/capacity exactly like receiving put-away does — an
    authorized override requires a written reason and
    `can_override_gates`, never a silent bypass."""
    from apps.inventory.services import enforce_location_suitability

    if quantity <= 0:
        raise QuantityInvariantError("La cantidad a transferir debe ser mayor que cero.")
    _, location_balances = _lot_movement_totals(lot)
    available_at_from = location_balances.get(from_location.id, 0)
    if quantity > available_at_from:
        raise QuantityInvariantError(
            f"Cantidad a transferir ({quantity}) excede lo disponible en {from_location} ({available_at_from})."
        )

    enforce_location_suitability(to_location, lot.item, user, quantity=quantity, override_reason=override_reason)

    InventoryMovement.objects.create(
        lot=lot, movement_type=MovementType.TRANSFER, quantity=quantity, unit_of_measure=lot.item.base_unit,
        from_location=from_location, to_location=to_location, posted_by=user,
        reason=reason or "Transferencia entre ubicaciones.", created_by=user,
    )
    transfer = Transfer.objects.create(
        lot=lot, from_location=from_location, to_location=to_location, quantity=quantity, reason=reason,
        created_by=user,
    )
    audit.log(
        AuditEvent.Action.INVENTORY_MOVEMENT, instance=transfer, actor=user,
        summary=f"Transferencia de {quantity} {lot.item} de {from_location} a {to_location}",
    )
    return transfer


def reservation_dispatched_quantity(reservation):
    return DispatchLine.objects.filter(reservation=reservation).aggregate(total=Sum("quantity"))["total"] or 0


def reservation_remaining_quantity(reservation):
    """How much of this specific reservation (one lot) has not yet been
    dispatched — distinct from `MaterialRequestLine.quantity_reserved -
    quantity_dispatched`, which is a line-wide aggregate across every
    reservation and cannot by itself prevent over-dispatching one
    particular lot when a line's reservations span several lots."""
    return reservation.quantity - reservation_dispatched_quantity(reservation)


# ---------------------------------------------------------------------------
# Reservation and dispatch
# ---------------------------------------------------------------------------


@transaction.atomic
def reserve_line(line: MaterialRequestLine, lot: InventoryLot, quantity, user) -> InventoryReservation:
    if line.quantity_approved is None:
        raise QuantityInvariantError("La línea no ha sido aprobada; no se puede reservar.")
    if quantity <= 0:
        raise QuantityInvariantError("La cantidad a reservar debe ser mayor que cero.")
    if line.quantity_reserved + quantity > line.quantity_approved:
        raise QuantityInvariantError(
            f"La reserva ({line.quantity_reserved + quantity}) excedería la cantidad aprobada ({line.quantity_approved})."
        )
    available = lot_available_quantity(lot)
    if quantity > available:
        raise QuantityInvariantError(f"Cantidad solicitada ({quantity}) excede la disponible en el lote ({available}).")

    reservation = InventoryReservation.objects.create(
        lot=lot, quantity=quantity, material_request_line=line, is_active=True, created_by=user
    )
    line.quantity_reserved = line.quantity_reserved + quantity
    line.save(update_fields=["quantity_reserved"])
    if line.request.status == MaterialRequest.Status.APPROVED:
        line.request.status = MaterialRequest.Status.RESERVED
        line.request.save(update_fields=["status"])
    audit.log(
        AuditEvent.Action.INVENTORY_MOVEMENT, instance=reservation, actor=user,
        summary=f"Reserva de {quantity} {line.item} para solicitud {line.request_id}",
    )
    return reservation


@transaction.atomic
def create_dispatch(request: MaterialRequest, lines, user) -> Dispatch:
    """`lines`: iterable of (MaterialRequestLine, InventoryReservation, quantity).
    A single request line may be split across several reservations/lots
    in one call (or across repeated calls) — never dispatches more than
    was reserved-and-not-yet-dispatched for the *line* as a whole, and
    never more than remains undispatched on that *specific* reservation
    (a line-wide check alone cannot catch over-dispatching one lot when
    a line's reservations span several). Each `request_line` is
    re-fetched with `select_for_update()` by primary key on every
    iteration — never trusting whatever in-memory copy the caller passed
    in — because a split dispatch legitimately passes several entries for
    the *same* line (one per reservation/lot), and those may arrive as
    distinct Python objects for the same DB row (e.g. from separate
    `select_related` joins); mutating a stale copy's `quantity_dispatched`
    and saving it would silently clobber an update just made by an
    earlier entry for the same line in this same call. The row lock also
    makes two concurrent dispatch calls for the same line serialize
    safely. Posts one DISPATCH InventoryMovement per line, decrementing
    on-hand at the lot's current location (core principle 4.5 — no
    inventory consequence without a real, posted movement)."""
    pick_list = PickList.objects.create(request=request, prepared_by=user, created_by=user)
    dispatch = Dispatch.objects.create(pick_list=pick_list, prepared_by=user, created_by=user)

    for request_line_hint, reservation, quantity in lines:
        if quantity <= 0:
            raise QuantityInvariantError("La cantidad a despachar debe ser mayor que cero.")
        request_line = MaterialRequestLine.objects.select_for_update().get(pk=request_line_hint.pk)
        remaining_reserved = request_line.quantity_reserved - request_line.quantity_dispatched
        if quantity > remaining_reserved:
            raise QuantityInvariantError(
                f"Cantidad a despachar ({quantity}) excede lo reservado y no despachado ({remaining_reserved}) "
                f"para {request_line.item}."
            )
        remaining_on_reservation = reservation_remaining_quantity(reservation)
        if quantity > remaining_on_reservation:
            raise QuantityInvariantError(
                f"Cantidad a despachar ({quantity}) excede lo disponible en esta reserva específica "
                f"({remaining_on_reservation}) — {reservation.lot}."
            )
        lot = reservation.lot
        DispatchLine.objects.create(
            dispatch=dispatch, request_line=request_line, lot=lot, reservation=reservation,
            quantity=quantity, created_by=user,
        )
        location = lot_current_location(lot)
        InventoryMovement.objects.create(
            lot=lot, movement_type=MovementType.DISPATCH, quantity=quantity, unit_of_measure=lot.item.base_unit,
            from_location=location, to_location=None, posted_by=user,
            reason=f"Despacho a proyecto — solicitud {request.id}", created_by=user,
        )
        request_line.quantity_dispatched = request_line.quantity_dispatched + quantity
        request_line.save(update_fields=["quantity_dispatched"])

    request.status = MaterialRequest.Status.DISPATCHED
    request.save(update_fields=["status"])
    audit.log(AuditEvent.Action.DISPATCH, instance=dispatch, actor=user, summary=f"Despacho creado para solicitud {request.id}")
    return dispatch


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


@transaction.atomic
def get_or_create_delivery(dispatch: Dispatch, user, *, delivery_location_note="") -> Delivery:
    delivery, created = Delivery.objects.get_or_create(
        dispatch=dispatch, defaults={"created_by": user, "delivery_location_note": delivery_location_note}
    )
    if created:
        for dispatch_line in dispatch.lines.all():
            DeliveryLine.objects.get_or_create(delivery=delivery, dispatch_line=dispatch_line, defaults={"created_by": user})
    return delivery


def _recompute_material_request_status(request: MaterialRequest):
    lines = list(request.lines.all())
    if not lines:
        return
    total_dispatched = sum(line.quantity_dispatched for line in lines)
    total_delivered = sum(line.quantity_delivered for line in lines)
    total_target = sum((line.quantity_approved if line.quantity_approved is not None else line.quantity_requested) for line in lines)

    if total_delivered <= 0:
        return
    if total_delivered >= total_target and total_delivered >= total_dispatched:
        new_status = MaterialRequest.Status.DELIVERED
    else:
        new_status = MaterialRequest.Status.PARTIALLY_DELIVERED
    if request.status != new_status:
        request.status = new_status
        request.save(update_fields=["status"])


@transaction.atomic
def record_delivery_line(delivery_line: DeliveryLine, *, quantity_accepted, quantity_rejected, quantity_damaged, user):
    """Supports partial and multi-trip delivery: `quantity_delivered` on
    the underlying MaterialRequestLine is always *recomputed* from the sum
    of every DeliveryLine against it (never incremented ad hoc), so
    recording this same line twice, or several delivery trips against the
    same request line, can never double count or overstate progress.
    Damaged quantity is a classification within rejected, not additional
    to it, and is quarantined via a real InventoryMovement + QuarantineRecord."""
    dispatch_line = delivery_line.dispatch_line

    if quantity_damaged > quantity_rejected:
        raise QuantityInvariantError("La cantidad dañada no puede exceder la cantidad rechazada.")
    if quantity_accepted < 0 or quantity_rejected < 0:
        raise QuantityInvariantError("Las cantidades no pueden ser negativas.")
    if quantity_accepted + quantity_rejected > dispatch_line.quantity:
        raise QuantityInvariantError(
            f"La suma de aceptado y rechazado ({quantity_accepted + quantity_rejected}) excede la cantidad "
            f"despachada ({dispatch_line.quantity})."
        )

    previously_damaged = delivery_line.quantity_damaged
    delivery_line.quantity_accepted = quantity_accepted
    delivery_line.quantity_rejected = quantity_rejected
    delivery_line.quantity_damaged = quantity_damaged
    delivery_line.save()

    request_line = dispatch_line.request_line
    total_accepted = DeliveryLine.objects.filter(dispatch_line__request_line=request_line).aggregate(
        total=Sum("quantity_accepted")
    )["total"] or 0
    request_line.quantity_delivered = total_accepted
    request_line.save(update_fields=["quantity_delivered"])

    new_damage = quantity_damaged - previously_damaged
    if new_damage > 0:
        quarantine_location = _damage_quarantine_location()
        if quarantine_location is not None:
            InventoryMovement.objects.create(
                lot=dispatch_line.lot, movement_type=MovementType.QUARANTINE, quantity=new_damage,
                unit_of_measure=dispatch_line.lot.item.base_unit, to_location=quarantine_location,
                posted_by=user, reason="Daño detectado en la entrega a proyecto.", created_by=user,
            )
            QuarantineRecord.objects.create(
                lot=dispatch_line.lot, location=quarantine_location, quantity=new_damage,
                reason="Daño detectado en la entrega a proyecto.", placed_by=user, created_by=user,
            )

    _recompute_material_request_status(request_line.request)
    audit.log(
        AuditEvent.Action.DISPATCH, instance=delivery_line, actor=user,
        summary=f"Línea de entrega registrada: aceptado {quantity_accepted}, rechazado {quantity_rejected}, dañado {quantity_damaged}",
    )
    return delivery_line


@transaction.atomic
def complete_delivery(delivery: Delivery, user, *, accepted, rejected_quantity_note=""):
    delivery.accepted = accepted
    delivery.rejected_quantity_note = rejected_quantity_note
    delivery.delivered_at = timezone.now()
    delivery.received_by = user
    delivery.save()
    audit.log(
        AuditEvent.Action.DISPATCH, instance=delivery, actor=user,
        summary=f"Entrega marcada como {'aceptada' if accepted else 'rechazada/fallida'}",
    )
    return delivery


@transaction.atomic
def create_project_receipt(delivery: Delivery, user, **fields) -> ProjectReceipt:
    """Idempotent per delivery — a double-click/retry on "Registrar
    recepción" returns the existing receipt rather than creating a
    duplicate (the UI only ever offers one create-receipt form per
    delivery; see docs/KNOWN_LIMITATIONS.md for the deliberately-not-
    covered case of an intentional second, later receipt entry)."""
    existing = ProjectReceipt.objects.select_for_update().filter(delivery=delivery).first()
    if existing is not None:
        return existing
    receipt = ProjectReceipt.objects.create(delivery=delivery, created_by=user, **fields)
    audit.log(AuditEvent.Action.PROJECT_ACCEPTANCE, instance=receipt, actor=user, summary="Recepción de proyecto registrada")
    return receipt


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------


@transaction.atomic
def create_installation_record(project_receipt: ProjectReceipt, user, *, delivery_line=None, **fields) -> InstallationRecord:
    """Idempotent when `delivery_line` is given (the common case, and the
    only case the UI currently offers): a repeated call for the same
    (project_receipt, delivery_line) — a double-click, a retried request,
    a resubmitted form after a refresh — returns the existing record
    rather than creating a duplicate work order for the same delivered
    material, mirroring apps.requests.services.get_or_create_delivery and
    apps.workflow.services.create_handoff."""
    if delivery_line is not None:
        existing = InstallationRecord.objects.select_for_update().filter(
            project_receipt=project_receipt, delivery_line=delivery_line
        ).first()
        if existing is not None:
            return existing

    installation = InstallationRecord.objects.create(
        project_receipt=project_receipt, delivery_line=delivery_line, created_by=user, **fields
    )
    audit.log(AuditEvent.Action.INSTALLATION, instance=installation, actor=user, summary="Registro de instalación creado")
    return installation


def _validly_delivered_quantity(installation: InstallationRecord):
    if installation.delivery_line is not None:
        return installation.delivery_line.quantity_accepted
    delivery = installation.project_receipt.delivery
    return sum(line.quantity_accepted for line in delivery.lines.all())


def _already_consumed_by_other_installations(installation: InstallationRecord):
    qs = InstallationRecord.objects.filter(project_receipt=installation.project_receipt).exclude(pk=installation.pk)
    if installation.delivery_line is not None:
        qs = qs.filter(delivery_line=installation.delivery_line)
    return sum((i.quantity_installed + i.quantity_not_used + i.quantity_damaged) for i in qs)


@transaction.atomic
def record_installation_progress(
    installation: InstallationRecord, user, *,
    quantity_installed, quantity_not_used=0, quantity_damaged=0,
    is_complete=False, requires_rework=False, missing_components_note="", observations="",
    started_at=None, mark_completed=False, override_reason=None,
):
    """Prevents installing quantities that were not validly delivered
    unless an explicitly authorized and audited exception is used
    (`override_reason` + `can_override_gates(user)` — logged as an
    AuditEvent WAIVER, mirroring the gate-override pattern from the
    Gate Controls milestone without misusing GateOverride, which is
    specifically tied to an 8-gate transition, not this model-level
    quantity guard — see docs/architecture-decisions.md)."""
    validly_delivered = _validly_delivered_quantity(installation)
    consumed_elsewhere = _already_consumed_by_other_installations(installation)
    requested_total = quantity_installed + quantity_not_used + quantity_damaged
    remaining_available = validly_delivered - consumed_elsewhere

    if requested_total > remaining_available:
        if override_reason and can_override_gates(user):
            audit.log(
                AuditEvent.Action.WAIVER, instance=installation, actor=user,
                summary="Instalación registrada por encima de la cantidad válidamente entregada (anulación autorizada)",
                reason=override_reason, requested_total=str(requested_total), remaining_available=str(remaining_available),
            )
        else:
            raise QuantityInvariantError(
                f"La cantidad total registrada ({requested_total}) excede lo válidamente entregado disponible "
                f"({remaining_available}). Use una anulación autorizada con motivo si esto es intencional."
            )

    installation.quantity_installed = quantity_installed
    installation.quantity_not_used = quantity_not_used
    installation.quantity_damaged = quantity_damaged
    installation.is_complete = is_complete
    installation.requires_rework = requires_rework
    installation.missing_components_note = missing_components_note
    installation.observations = observations
    if started_at:
        installation.started_at = started_at
    if mark_completed:
        installation.installed_at = timezone.now()
        installation.installed_by = user

    installation.save()

    if quantity_installed > 0 and installation.delivery_line is not None:
        lot = installation.delivery_line.dispatch_line.lot
        InventoryMovement.objects.create(
            lot=lot, movement_type=MovementType.INSTALLATION_CONSUMPTION, quantity=quantity_installed,
            unit_of_measure=lot.item.base_unit, from_location=None, to_location=None,
            posted_by=user, reason=f"Consumo por instalación {installation.id}", created_by=user,
        )

    if quantity_damaged > 0 and installation.delivery_line is not None:
        quarantine_location = _damage_quarantine_location()
        lot = installation.delivery_line.dispatch_line.lot
        if quarantine_location is not None:
            InventoryMovement.objects.create(
                lot=lot, movement_type=MovementType.QUARANTINE, quantity=quantity_damaged,
                unit_of_measure=lot.item.base_unit, to_location=quarantine_location,
                posted_by=user, reason="Daño detectado durante la instalación.", created_by=user,
            )
            QuarantineRecord.objects.create(
                lot=lot, location=quarantine_location, quantity=quantity_damaged,
                reason="Daño detectado durante la instalación.", placed_by=user, created_by=user,
            )

    audit.log(
        AuditEvent.Action.INSTALLATION, instance=installation, actor=user,
        summary=f"Progreso de instalación registrado: instalado {quantity_installed}, no usado {quantity_not_used}, dañado {quantity_damaged}",
    )
    return installation


@transaction.atomic
def acknowledge_installation(installation: InstallationRecord, user):
    installation.installer_acknowledged_at = timezone.now()
    installation.save(update_fields=["installer_acknowledged_at"])
    audit.log(AuditEvent.Action.INSTALLATION, instance=installation, actor=user, summary="Instalación reconocida por el instalador")
    return installation


@transaction.atomic
def confirm_installation_supervisor(installation: InstallationRecord, user):
    installation.supervisor_confirmed_by = user
    installation.supervisor_confirmed_at = timezone.now()
    installation.save(update_fields=["supervisor_confirmed_by", "supervisor_confirmed_at"])
    audit.log(AuditEvent.Action.INSTALLATION, instance=installation, actor=user, summary="Instalación confirmada por supervisor")
    return installation


# ---------------------------------------------------------------------------
# Inspection and punch list
# ---------------------------------------------------------------------------


@transaction.atomic
def create_inspection(
    installation: InstallationRecord, inspector, *, result, inspected_quantity=None, notes="",
    inspection_date=None, punch_list_items=None,
) -> InspectionRecord:
    """Chains reinspections via `previous_inspection` rather than
    overwriting failed history — every inspection/correction cycle stays
    its own permanent row."""
    previous = installation.inspections.order_by("-created_at").first()
    passed = result != InspectionRecord.Result.FAIL

    inspection = InspectionRecord.objects.create(
        installation=installation,
        inspector=inspector,
        inspection_date=inspection_date or timezone.now().date(),
        inspected_quantity=inspected_quantity,
        result=result,
        passed=passed,
        notes=notes,
        previous_inspection=previous,
        created_by=inspector,
    )

    for item in punch_list_items or []:
        PunchListItem.objects.create(inspection=inspection, created_by=inspector, **item)

    audit.log(
        AuditEvent.Action.INSTALLATION, instance=inspection, actor=inspector,
        summary=f"Inspección registrada: {inspection.get_result_display()}",
    )
    return inspection


@transaction.atomic
def close_punch_list_item(item: PunchListItem, user, *, resolution_notes=""):
    if item.status == PunchListItem.Status.CLOSED:
        raise QuantityInvariantError("Este defecto ya fue cerrado.")
    item.status = PunchListItem.Status.CLOSED
    item.resolution_notes = resolution_notes
    item.resolved_by = user
    item.resolved_at = timezone.now()
    item.save()
    audit.log(AuditEvent.Action.INSTALLATION, instance=item, actor=user, summary=f"Defecto cerrado: {item.description[:60]}")
    return item


@transaction.atomic
def technical_sign_off(inspection: InspectionRecord, user):
    inspection.technical_sign_off_by = user
    inspection.technical_sign_off_at = timezone.now()
    inspection.save(update_fields=["technical_sign_off_by", "technical_sign_off_at"])
    audit.log(AuditEvent.Action.INSTALLATION, instance=inspection, actor=user, summary="Firma técnica registrada")
    return inspection


# ---------------------------------------------------------------------------
# Final acceptance
# ---------------------------------------------------------------------------


@transaction.atomic
def record_final_acceptance(installation: InstallationRecord, user, *, decision, conditions_note=""):
    """Called immediately after a successful
    `apps.workflow.services.accept_handoff` for the `inspection_to_acceptance`
    gate — never a replacement for it. Rejection / return-for-correction at
    this stage is handled entirely by the generic Handoff reject/return
    flow (`apps.workflow.services.reject_handoff` /
    `return_for_correction`), never duplicated here."""
    acceptance, created = AcceptanceRecord.objects.get_or_create(
        installation=installation,
        defaults={"decision": decision, "conditions_note": conditions_note, "accepted_by": user, "created_by": user},
    )
    if not created:
        raise QuantityInvariantError("Esta instalación ya tiene un registro de aceptación final.")
    audit.log(
        AuditEvent.Action.ACCEPTANCE, instance=acceptance, actor=user,
        summary=f"Aceptación final registrada: {acceptance.get_decision_display()}",
    )
    return acceptance
