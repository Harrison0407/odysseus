"""Cycle-count workflow (Priority 1): create a count for a storage site,
enter physical quantities against the ledger-derived system quantity
(optionally "blind" — the counter isn't shown the system quantity while
entering their count, to avoid anchoring/copying), and post an approved
variance as a real `InventoryMovement` — never a silent stock edit
(core principle 4.5, same rule the rest of the ledger already follows).

Note: `lot_on_hand_quantity` here duplicates the equivalent helper in
`apps.requests.services` (added in an earlier milestone for the
delivery/installation chain) rather than reaching across app boundaries
for a one-line pure function — this app is the more natural home for
inventory-ledger utilities. See `docs/ASSUMPTIONS.md` for this noted,
minor duplication.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.audit import services as audit
from apps.audit.models import AuditEvent

from .models import CycleCount, CycleCountLine, InventoryAdjustment, InventoryMovement, MovementType


class CycleCountError(ValueError):
    pass


def lot_on_hand_quantity(lot) -> Decimal:
    on_hand = Decimal("0")
    for movement in InventoryMovement.objects.filter(lot=lot):
        if movement.to_location_id:
            on_hand += movement.quantity
        if movement.from_location_id:
            on_hand -= movement.quantity
    return on_hand


@transaction.atomic
def start_cycle_count(site, user, *, planned_date=None, is_blind_count=True, assigned_counter=None, lots=None) -> CycleCount:
    """`lots`: iterable of `InventoryLot` to include — each gets a
    `CycleCountLine` seeded with the current ledger-derived on-hand
    quantity as `system_quantity`."""
    cycle_count = CycleCount.objects.create(
        site=site, planned_date=planned_date, is_blind_count=is_blind_count,
        assigned_counter=assigned_counter, created_by=user,
    )
    for lot in lots or []:
        CycleCountLine.objects.create(
            cycle_count=cycle_count, lot=lot, system_quantity=lot_on_hand_quantity(lot), created_by=user
        )
    audit.log(
        AuditEvent.Action.OTHER, instance=cycle_count, actor=user,
        summary=f"Conteo cíclico iniciado para {site} ({len(lots or [])} lote(s))",
    )
    return cycle_count


@transaction.atomic
def record_physical_count(line: CycleCountLine, physical_quantity, user, *, explanation="") -> CycleCountLine:
    if line.approved_adjustment_id is not None:
        raise CycleCountError("Esta línea ya tiene un ajuste aprobado — no se puede recontar sin revertirlo primero.")
    was_already_counted = CycleCountLine.objects.filter(pk=line.pk, physical_quantity__isnull=False).exists()
    line.physical_quantity = physical_quantity
    line.variance = physical_quantity - line.system_quantity
    if was_already_counted:
        line.recounted = True
    line.explanation = explanation
    line.save(update_fields=["physical_quantity", "variance", "recounted", "explanation"])
    audit.log(
        AuditEvent.Action.OTHER, instance=line, actor=user,
        summary=f"Conteo físico registrado: {physical_quantity} (sistema: {line.system_quantity}, variación: {line.variance})",
    )
    return line


@transaction.atomic
def approve_adjustment(line: CycleCountLine, user, *, reason, location) -> InventoryAdjustment:
    """Posts the variance as a real `InventoryMovement` (never a direct
    edit to any stored quantity — there isn't one to edit; on-hand is
    always ledger-derived) and links it via `InventoryAdjustment`."""
    if line.physical_quantity is None:
        raise CycleCountError("Esta línea no tiene un conteo físico registrado todavía.")
    if line.approved_adjustment_id is not None:
        raise CycleCountError("Esta línea ya tiene un ajuste aprobado.")
    if not line.variance:
        raise CycleCountError("No hay variación que ajustar en esta línea (el conteo coincide con el sistema).")

    movement = InventoryMovement.objects.create(
        lot=line.lot, movement_type=MovementType.ADJUSTMENT, quantity=abs(line.variance),
        unit_of_measure=line.lot.item.base_unit,
        to_location=location if line.variance > 0 else None,
        from_location=location if line.variance < 0 else None,
        posted_by=user, reason=reason, requires_approval=True, approved_by=user, created_by=user,
    )
    adjustment = InventoryAdjustment.objects.create(
        lot=line.lot, quantity_delta=line.variance, reason=reason, approved_by=user,
        resulting_movement=movement, created_by=user,
    )
    line.approved_adjustment = adjustment
    line.save(update_fields=["approved_adjustment"])
    audit.log(
        AuditEvent.Action.ADJUSTMENT, instance=adjustment, actor=user,
        summary=f"Ajuste de inventario aprobado: {line.variance} {line.lot.item} en {location}",
    )
    return adjustment
