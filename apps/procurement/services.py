"""Order destination allocation and purchased-spare control (spec
section 3, one-shot release).

`allocated + confirmed purchased spares must never exceed ordered
quantity` is enforced on every write. Unallocated quantity never
silently becomes spare inventory — it only becomes a spare through an
explicit, authorized `confirm_purchased_spare` call, exactly like every
other authorized action in this system (permission checked server-side,
audit-logged, never inferred from a UI hint alone).
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.workflow.services import can_override_gates

from .models import OrderLineAllocation, PurchaseOrderLine, PurchasedSpare


class AllocationError(ValueError):
    """Raised for any allocation/spare-confirmation precondition that
    isn't met — never silently clamped or ignored."""


def allocated_quantity(line: PurchaseOrderLine) -> Decimal:
    return line.allocations.filter(is_active=True).aggregate(total=Sum("quantity"))["total"] or Decimal("0")


def confirmed_spare_quantity(line: PurchaseOrderLine) -> Decimal:
    return line.purchased_spares.aggregate(total=Sum("quantity"))["total"] or Decimal("0")


def unallocated_quantity(line: PurchaseOrderLine) -> Decimal:
    return line.quantity_ordered - allocated_quantity(line) - confirmed_spare_quantity(line)


def shortage_quantity(line: PurchaseOrderLine):
    """Returns `None` (never a fabricated zero) when `required_quantity`
    was never recorded for this line."""
    if line.required_quantity is None:
        return None
    return line.required_quantity - line.quantity_ordered


@transaction.atomic
def allocate_order_line(line: PurchaseOrderLine, user, *, destination_scope, quantity, building_family=None,
                         building=None, floor=None, unit=None, area=None, drawing=None, notes="") -> OrderLineAllocation:
    if quantity <= 0:
        raise AllocationError("La cantidad a asignar debe ser mayor que cero.")
    if quantity > unallocated_quantity(line):
        raise AllocationError(
            f"La cantidad a asignar ({quantity}) excede lo no asignado en esta línea ({unallocated_quantity(line)})."
        )
    allocation = OrderLineAllocation.objects.create(
        purchase_order_line=line, destination_scope=destination_scope, quantity=quantity,
        building_family=building_family, building=building, floor=floor, unit=unit, area=area,
        drawing=drawing, notes=notes, created_by=user,
    )
    audit.log(
        AuditEvent.Action.OTHER, instance=allocation, actor=user,
        summary=f"Asignación de destino: {quantity} de {line} -> {allocation.get_destination_scope_display()}",
    )
    return allocation


@transaction.atomic
def reassign_allocation(allocation: OrderLineAllocation, user, *, reason, destination_scope=None, building_family=None,
                         building=None, floor=None, unit=None, area=None, drawing=None, notes="") -> OrderLineAllocation:
    """Never deletes or edits the original allocation — it is marked
    inactive and a brand-new row is created, linked back via
    `reassigned_from`, so the original planned destination remains
    fully visible in history."""
    if not allocation.is_active:
        raise AllocationError("Esta asignación ya fue reasignada.")
    if not reason or not reason.strip():
        raise AllocationError("Se requiere un motivo para reasignar el destino.")
    allocation.is_active = False
    allocation.reassignment_reason = reason
    allocation.reassigned_by = user
    allocation.reassigned_at = timezone.now()
    allocation.save()

    new_allocation = OrderLineAllocation.objects.create(
        purchase_order_line=allocation.purchase_order_line,
        destination_scope=destination_scope or allocation.destination_scope,
        quantity=allocation.quantity,
        building_family=building_family if building_family is not None else allocation.building_family,
        building=building if building is not None else allocation.building,
        floor=floor if floor is not None else allocation.floor,
        unit=unit if unit is not None else allocation.unit,
        area=area if area is not None else allocation.area,
        drawing=drawing if drawing is not None else allocation.drawing,
        notes=notes, reassigned_from=allocation, created_by=user,
    )
    audit.log(
        AuditEvent.Action.OTHER, instance=new_allocation, actor=user,
        summary=f"Destino reasignado: {allocation} -> {new_allocation.get_destination_scope_display()}", reason=reason,
    )
    return new_allocation


@transaction.atomic
def confirm_purchased_spare(line: PurchaseOrderLine, user, *, quantity, reason, compatible_typology="") -> PurchasedSpare:
    """Requires the same senior-authorization permission every other
    approval-style action in this system uses — never a silent
    conversion of unallocated stock into a spare."""
    if not can_override_gates(user):
        raise AllocationError("No tiene permiso para confirmar repuestos comprados.")
    if quantity <= 0:
        raise AllocationError("La cantidad de repuesto debe ser mayor que cero.")
    if not reason or not reason.strip():
        raise AllocationError("Se requiere una razón para confirmar un repuesto comprado.")
    if quantity > unallocated_quantity(line):
        raise AllocationError(
            f"La cantidad de repuesto ({quantity}) excede lo no asignado en esta línea ({unallocated_quantity(line)})."
        )
    spare = PurchasedSpare.objects.create(
        purchase_order_line=line, quantity=quantity, reason=reason, compatible_typology=compatible_typology,
        confirmed_by=user, created_by=user,
    )
    audit.log(
        AuditEvent.Action.WAIVER, instance=spare, actor=user,
        summary=f"Repuesto comprado confirmado: {quantity} de {line}", reason=reason,
    )
    return spare


def line_allocation_summary(line: PurchaseOrderLine) -> dict:
    return {
        "quantity_ordered": line.quantity_ordered,
        "required_quantity": line.required_quantity,
        "allocated_quantity": allocated_quantity(line),
        "confirmed_spare_quantity": confirmed_spare_quantity(line),
        "unallocated_quantity": unallocated_quantity(line),
        "shortage_quantity": shortage_quantity(line),
    }


def spare_inventory_summary(spare: PurchasedSpare) -> dict:
    """Read-only aggregation over the existing ledger/reservation
    architecture for the lot(s) received against this spare
    confirmation — never a second, independently-tracked balance."""
    from apps.inventory.models import InventoryMovement, MovementType
    from apps.inventory.services import lot_on_hand_quantity

    lots = list(spare.inventory_lots.all())
    on_hand = sum((lot_on_hand_quantity(lot) for lot in lots), Decimal("0"))
    reserved = sum(
        (lot.reservations.filter(is_active=True).aggregate(total=Sum("quantity"))["total"] or Decimal("0") for lot in lots),
        Decimal("0"),
    )
    received = InventoryMovement.objects.filter(
        lot__in=lots, movement_type=MovementType.RECEIPT
    ).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
    return {
        "quantity_confirmed": spare.quantity,
        "quantity_received": received,
        "quantity_available": on_hand - reserved,
        "quantity_reserved": reserved,
        "lots": lots,
    }
