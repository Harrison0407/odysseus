"""Physical receiving posting logic.

Core principle 4.5 (ledger-based inventory) and 4.3 (no silent
confirmation): posting a receipt line here is the *only* way inventory
increases from a shipment. A commercial document or an unverified PO can
never create inventory on its own.
"""

from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.core.models import Severity
from apps.inventory.models import InventoryLot, InventoryMovement, MovementType, QuarantineRecord
from apps.inventory.services import enforce_location_suitability
from apps.matching.models import Discrepancy, DiscrepancyType

from .models import DamageRecord, ReceiptLine


@transaction.atomic
def post_receipt_line(receipt_line: ReceiptLine, *, quantity_received, quantity_damaged, quantity_missing,
                       exception_type, notes, user, receiving_location, quarantine_location=None,
                       storage_override_reason=None):
    """`storage_override_reason`: only used if `receiving_location` fails
    a *blocking* storage-suitability/capacity check (spec section 17 —
    see `apps.inventory.services.check_location_suitability`); requires
    `can_override_gates(user)`, exactly like every other authorized
    override in this system — never a silent bypass."""
    receipt_line.quantity_received = quantity_received
    receipt_line.quantity_damaged = quantity_damaged
    receipt_line.quantity_missing = quantity_missing
    receipt_line.exception_type = exception_type
    receipt_line.notes = notes
    receipt_line.save()

    manifest_line = receipt_line.manifest_line
    item = manifest_line.item

    good_quantity = quantity_received - quantity_damaged
    lot = None
    if item is not None and good_quantity > 0:
        enforce_location_suitability(
            receiving_location, item, user, quantity=good_quantity, override_reason=storage_override_reason
        )
        lot = InventoryLot.objects.create(
            item=item,
            source_receipt_line=receipt_line,
            lot_code=f"{receipt_line.receipt_id}-{receipt_line.id}",
            bought_for_scope=manifest_line.destination_scope,
            bought_for_building=manifest_line.building,
            created_by=user,
        )
        InventoryMovement.objects.create(
            lot=lot,
            movement_type=MovementType.RECEIPT,
            quantity=good_quantity,
            unit_of_measure=manifest_line.unit_of_measure,
            to_location=receiving_location,
            posted_by=user,
            reason="Recepción física posteada desde recibo.",
            created_by=user,
        )

    if quantity_damaged > 0 and item is not None:
        damage_lot = InventoryLot.objects.create(
            item=item,
            source_receipt_line=receipt_line,
            lot_code=f"{receipt_line.receipt_id}-{receipt_line.id}-DAMAGE",
            created_by=user,
        )
        InventoryMovement.objects.create(
            lot=damage_lot,
            movement_type=MovementType.QUARANTINE,
            quantity=quantity_damaged,
            unit_of_measure=manifest_line.unit_of_measure,
            to_location=quarantine_location or receiving_location,
            posted_by=user,
            reason="Material dañado en recepción — cuarentena automática.",
            created_by=user,
        )
        if quarantine_location:
            QuarantineRecord.objects.create(
                lot=damage_lot,
                location=quarantine_location,
                quantity=quantity_damaged,
                reason="Daño detectado en recepción física.",
                placed_by=user,
                created_by=user,
            )
        DamageRecord.objects.create(
            receipt_line=receipt_line,
            quantity=quantity_damaged,
            description=notes,
            created_by=user,
        )

    if exception_type and exception_type != ReceiptLine.ExceptionType.NONE:
        severity = Severity.CRITICAL if exception_type in (
            ReceiptLine.ExceptionType.SHORTAGE,
            ReceiptLine.ExceptionType.DAMAGE,
            ReceiptLine.ExceptionType.MISSING_COMPONENT,
        ) else Severity.WARNING
        type_map = {
            ReceiptLine.ExceptionType.SHORTAGE: DiscrepancyType.RECEIPT_SHORTAGE,
            ReceiptLine.ExceptionType.OVERAGE: DiscrepancyType.RECEIPT_OVERAGE,
            ReceiptLine.ExceptionType.DAMAGE: DiscrepancyType.DAMAGE,
        }
        Discrepancy.objects.create(
            shipment=manifest_line.manifest_version.manifest.shipment,
            discrepancy_type=type_map.get(exception_type, DiscrepancyType.RECEIPT_SHORTAGE),
            severity=severity,
            description=f"{receipt_line}: {notes}",
            created_by=user,
        )

    audit.log(
        AuditEvent.Action.RECEIPT_POSTING,
        instance=receipt_line.receipt,
        actor=user,
        summary=f"Línea {manifest_line} recibida: {quantity_received} (dañado {quantity_damaged}, faltante {quantity_missing})",
    )

    return lot
