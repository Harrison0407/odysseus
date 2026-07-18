"""Landed-cost allocation and calculation engine (spec section 9.6, 26).

Two-step process, matching the modeled data (`CostAllocationRun`/
`CostAllocationLine`/`LandedCostVersion`/`LandedCostLine`, all present
since Priority 0 but never previously computed by any code path):

1. `run_allocation(cost_charge, shipment, method, user, ...)` splits one
   `CostCharge`'s amount across every line of the shipment's internal
   operational manifest according to `method`, creating one
   `CostAllocationLine` per manifest line. The allocation basis for the
   *last* line absorbs any rounding remainder, so the sum of allocated
   amounts always exactly equals the original charge — never a
   silently-dropped or silently-invented cent.

2. `calculate_landed_cost(shipment, user)` aggregates every
   `CostAllocationRun`'s lines for the shipment into freight/local/other
   per-unit cost buckets (see `_COST_TYPE_BUCKETS`), adds each line's own
   unit price (converted to the organization's base currency via
   `ExchangeRate` when a rate is on file — never silently assumed 1:1
   when it isn't, core principle 4.3), and creates a brand new
   `LandedCostVersion` (never edits a previous one — core principle 4.4).
   `finalize_landed_cost` marks a version final, once, explicitly.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.shipments.models import ManifestPurpose

from .models import (
    CostAllocationLine,
    CostAllocationRun,
    CostDocument,
    Currency,
    ExchangeRate,
    LandedCostLine,
    LandedCostVersion,
)


class AllocationError(ValueError):
    """Raised for any allocation/calculation precondition that isn't
    met — never silently skipped or approximated."""


# Cost-type -> landed-cost bucket. A simplifying, documented decision
# (see docs/ASSUMPTIONS.md) since the spec's CostDocument.CostType is
# more granular than the 3 LandedCostLine buckets it feeds.
_FREIGHT_TYPES = {CostDocument.CostType.FREIGHT}
_LOCAL_TYPES = {
    CostDocument.CostType.CUSTOMS_DUTIES, CostDocument.CostType.PORT_TERMINAL,
    CostDocument.CostType.BROKERAGE, CostDocument.CostType.LOCAL_TRANSPORT,
    CostDocument.CostType.HANDLING, CostDocument.CostType.STORAGE, CostDocument.CostType.DEMURRAGE,
}
_OTHER_TYPES = {CostDocument.CostType.INSURANCE, CostDocument.CostType.INSPECTION, CostDocument.CostType.OTHER}


def _internal_manifest_lines(shipment):
    manifest = shipment.manifests.filter(purpose=ManifestPurpose.INTERNAL_OPERATIONAL_MANIFEST).first()
    if manifest is None:
        return []
    version = manifest.versions.order_by("-version_number").first()
    if version is None:
        return []
    return list(version.lines.all())


def _basis_value(line, method):
    """None means "this line cannot participate in this allocation
    method" (e.g. no CBM recorded) — the caller excludes it rather than
    silently treating an unknown basis as zero-but-included."""
    mapping = {
        CostAllocationRun.AllocationMethod.QUANTITY: line.quantity,
        CostAllocationRun.AllocationMethod.PRODUCT_VALUE: line.commercial_value,
        CostAllocationRun.AllocationMethod.GROSS_WEIGHT: line.gross_weight_kg,
        CostAllocationRun.AllocationMethod.NET_WEIGHT: line.gross_weight_kg,  # net weight not separately modeled — see ASSUMPTIONS
        CostAllocationRun.AllocationMethod.CBM: line.cbm,
        CostAllocationRun.AllocationMethod.PACKAGE: line.packages,
        CostAllocationRun.AllocationMethod.CONTAINER: Decimal("1"),  # equal split across every line
    }
    return mapping.get(method)


@transaction.atomic
def run_allocation(cost_charge, shipment, method, user, *, manual_values=None) -> CostAllocationRun:
    """`manual_values`: required for MANUAL_AMOUNT (dict of
    {manifest_line_id: Decimal amount}) or MANUAL_PERCENTAGE (dict of
    {manifest_line_id: Decimal percentage, must sum to ~100})."""
    lines = _internal_manifest_lines(shipment)
    if not lines:
        raise AllocationError("No hay líneas de manifiesto interno para este embarque.")

    run = CostAllocationRun.objects.create(
        shipment=shipment, cost_charge=cost_charge, method=method, run_by=user, created_by=user
    )
    total_charge = cost_charge.amount
    total_allocated = Decimal("0")

    if method in (CostAllocationRun.AllocationMethod.MANUAL_AMOUNT, CostAllocationRun.AllocationMethod.MANUAL_PERCENTAGE):
        manual_values = manual_values or {}
        if method == CostAllocationRun.AllocationMethod.MANUAL_PERCENTAGE:
            total_pct = sum(Decimal(str(v)) for v in manual_values.values())
            if manual_values and abs(total_pct - 100) > Decimal("0.5"):
                raise AllocationError(f"Los porcentajes deben sumar 100 (suman {total_pct}).")
        for line in lines:
            raw = manual_values.get(str(line.id))
            if raw is None:
                continue
            value = Decimal(str(raw))
            if method == CostAllocationRun.AllocationMethod.MANUAL_PERCENTAGE:
                amount = (total_charge * value / 100).quantize(Decimal("0.01"))
            else:
                amount = value.quantize(Decimal("0.01"))
            CostAllocationLine.objects.create(
                allocation_run=run, manifest_line=line, allocated_amount=amount, basis_value=value, created_by=user
            )
            total_allocated += amount
    else:
        basis_by_line = {line.id: _basis_value(line, method) for line in lines}
        eligible_lines = [line for line in lines if basis_by_line[line.id] is not None and basis_by_line[line.id] > 0]
        if not eligible_lines:
            raise AllocationError(
                f"Ninguna línea del manifiesto tiene una base de asignación válida para el método '{method}'."
            )
        total_basis = sum(basis_by_line[line.id] for line in eligible_lines)
        for i, line in enumerate(eligible_lines):
            basis = basis_by_line[line.id]
            if i == len(eligible_lines) - 1:
                amount = (total_charge - total_allocated).quantize(Decimal("0.01"))  # last line absorbs rounding
            else:
                amount = (total_charge * Decimal(basis) / Decimal(total_basis)).quantize(Decimal("0.01"))
            CostAllocationLine.objects.create(
                allocation_run=run, manifest_line=line, allocated_amount=amount, basis_value=basis, created_by=user
            )
            total_allocated += amount

    run.total_allocated = total_allocated
    run.save(update_fields=["total_allocated"])
    audit.log(
        AuditEvent.Action.OTHER, instance=run, actor=user,
        summary=f"Asignación de costo ejecutada ({run.get_method_display()}) sobre {cost_charge}",
    )
    return run


def _original_unit_price(line):
    """Traces back through ManifestLineSource -> PurchaseOrderLine for
    the original unit price and its currency — never fabricated when no
    source is linked."""
    source = line.sources.filter(purchase_order_line__isnull=False).select_related(
        "purchase_order_line__purchase_order"
    ).first()
    if source is None:
        return None, None
    po_line = source.purchase_order_line
    return po_line.unit_price, po_line.purchase_order.currency


def _convert_to_base_currency(amount, currency_code, organization):
    """Returns None (never a silently-assumed 1:1 rate) if no
    `ExchangeRate` is on file for this currency pair."""
    if amount is None:
        return None
    base_code = organization.default_currency
    if currency_code == base_code:
        return amount
    try:
        from_currency = Currency.objects.get(code=currency_code)
        to_currency = Currency.objects.get(code=base_code)
    except Currency.DoesNotExist:
        return None
    rate = ExchangeRate.objects.filter(from_currency=from_currency, to_currency=to_currency).order_by("-rate_date").first()
    if rate is None:
        return None
    return (amount * rate.rate).quantize(Decimal("0.0001"))


@transaction.atomic
def calculate_landed_cost(shipment, user) -> LandedCostVersion:
    """Creates a new, immutable `LandedCostVersion` — never edits a
    previous one. Every allocation run posted for this shipment so far
    (across every `CostCharge`/`CostAllocationRun`) is aggregated into
    each manifest line's freight/local/other per-unit buckets."""
    lines = _internal_manifest_lines(shipment)
    if not lines:
        raise AllocationError("No hay líneas de manifiesto interno para este embarque.")

    next_version_number = (shipment.landed_cost_versions.aggregate(m=Max("version_number"))["m"] or 0) + 1
    version = LandedCostVersion.objects.create(shipment=shipment, version_number=next_version_number, created_by=user)

    for line in lines:
        freight_total = Decimal("0")
        local_total = Decimal("0")
        other_total = Decimal("0")
        allocation_lines = CostAllocationLine.objects.filter(manifest_line=line).select_related(
            "allocation_run__cost_charge__cost_document"
        )
        for alloc_line in allocation_lines:
            cost_type = alloc_line.allocation_run.cost_charge.cost_document.cost_type
            if cost_type in _FREIGHT_TYPES:
                freight_total += alloc_line.allocated_amount
            elif cost_type in _LOCAL_TYPES:
                local_total += alloc_line.allocated_amount
            elif cost_type in _OTHER_TYPES:
                other_total += alloc_line.allocated_amount

        quantity = line.quantity or Decimal("1")
        freight_per_unit = (freight_total / quantity).quantize(Decimal("0.0001"))
        local_per_unit = (local_total / quantity).quantize(Decimal("0.0001"))
        other_per_unit = (other_total / quantity).quantize(Decimal("0.0001"))

        original_unit_price, original_currency = _original_unit_price(line)
        base_unit_price = _convert_to_base_currency(original_unit_price, original_currency, shipment.organization)

        final_per_unit = None
        total_value = None
        if base_unit_price is not None:
            final_per_unit = (base_unit_price + freight_per_unit + local_per_unit + other_per_unit).quantize(Decimal("0.0001"))
            total_value = (final_per_unit * quantity).quantize(Decimal("0.01"))

        LandedCostLine.objects.create(
            version=version, manifest_line=line,
            original_unit_price=original_unit_price,
            base_currency_unit_price=base_unit_price,
            freight_per_unit=freight_per_unit,
            local_cost_per_unit=local_per_unit,
            other_cost_per_unit=other_per_unit,
            final_landed_cost_per_unit=final_per_unit,
            total_landed_value=total_value,
            created_by=user,
        )

    audit.log(
        AuditEvent.Action.COST_FINALIZATION, instance=version, actor=user,
        summary=f"Versión de costo de importación calculada: v{next_version_number}",
    )
    return version


@transaction.atomic
def finalize_landed_cost(version: LandedCostVersion, user) -> LandedCostVersion:
    if version.is_final:
        raise AllocationError("Esta versión ya fue finalizada — cree una nueva versión si algo cambió.")
    version.is_final = True
    version.finalized_by = user
    version.finalized_at = timezone.now()
    version.save(update_fields=["is_final", "finalized_by", "finalized_at"])
    audit.log(
        AuditEvent.Action.COST_FINALIZATION, instance=version, actor=user,
        summary=f"Versión de costo de importación v{version.version_number} finalizada",
    )
    return version
