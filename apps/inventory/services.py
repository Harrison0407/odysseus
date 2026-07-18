"""Cycle-count workflow (Priority 1): create a count for a storage site,
enter physical quantities against the ledger-derived system quantity
(optionally "blind" — the counter isn't shown the system quantity while
entering their count, to avoid anchoring/copying), and post an approved
variance as a real `InventoryMovement` — never a silent stock edit
(core principle 4.5, same rule the rest of the ledger already follows).

Also: storage capacity/suitability checking (Priority 1) — see
`check_location_suitability` below.

Note: `lot_on_hand_quantity` here duplicates the equivalent helper in
`apps.requests.services` (added in an earlier milestone for the
delivery/installation chain) rather than reaching across app boundaries
for a one-line pure function — this app is the more natural home for
inventory-ledger utilities. See `docs/ASSUMPTIONS.md` for this noted,
minor duplication.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal

from django.db import transaction

from apps.audit import services as audit
from apps.audit.models import AuditEvent

from .models import (
    CycleCount,
    CycleCountLine,
    InventoryAdjustment,
    InventoryLot,
    InventoryMovement,
    MovementType,
    WarehouseLocation,
)


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


# ---------------------------------------------------------------------------
# Storage capacity and location suitability (Priority 1)
# ---------------------------------------------------------------------------


class StorageSuitabilityError(ValueError):
    """Raised when a receiving/put-away/transfer/reassignment would
    violate a configured, *blocking* storage restriction and no
    authorized override was supplied. Carries the full
    `LocationSuitabilityResult` so the caller can show exactly why."""

    def __init__(self, result: "LocationSuitabilityResult"):
        self.result = result
        super().__init__("La ubicación no es apta y no se proporcionó una anulación autorizada.")


class StoragePermissionError(ValueError):
    """Raised when an override reason was supplied but the user lacks
    `can_override_gates` — a distinct case from a plain blocked/no-reason
    result, so callers can show a permission-specific message."""


@dataclasses.dataclass
class LocationSuitabilityResult:
    ready: bool
    severity: str  # "ready" | "warning" | "blocked"
    warnings: list = dataclasses.field(default_factory=list)
    blockers: list = dataclasses.field(default_factory=list)
    override_available: bool = True

    @property
    def blocked(self) -> bool:
        return not self.ready

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _item_per_unit_footprint(item):
    """Derives a per-unit CBM/weight footprint from the most recent
    `ManifestLine` linked to this item, since `Item` itself has no
    per-unit physical-footprint field (only free-text `dimensions`) —
    see `docs/ASSUMPTIONS.md`. Returns `(None, None)` when not
    derivable, never a fabricated guess (core principle 4.3)."""
    from apps.shipments.models import ManifestLine

    line = (
        ManifestLine.objects.filter(item=item, quantity__gt=0)
        .exclude(cbm__isnull=True, gross_weight_kg__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if line is None:
        return None, None
    per_volume = (line.cbm / line.quantity) if line.cbm else None
    per_weight = (line.gross_weight_kg / line.quantity) if line.gross_weight_kg else None
    return per_volume, per_weight


def location_current_utilization(location: WarehouseLocation) -> dict:
    """Ledger-derived current volume/weight utilization at this
    location, using each on-hand lot's manifest-derived footprint when
    traceable. `None` (never `0`) for a bucket if no lot at this
    location has a derivable footprint for it, so an unknown
    utilization is never silently reported as "no utilization"."""
    volume_total = Decimal("0")
    weight_total = Decimal("0")
    any_volume_known = False
    any_weight_known = False

    lot_balances: dict = {}
    for movement in InventoryMovement.objects.filter(to_location=location).select_related("lot"):
        lot_balances[movement.lot_id] = lot_balances.get(movement.lot_id, Decimal("0")) + movement.quantity
    for movement in InventoryMovement.objects.filter(from_location=location).select_related("lot"):
        lot_balances[movement.lot_id] = lot_balances.get(movement.lot_id, Decimal("0")) - movement.quantity

    for lot_id, quantity in lot_balances.items():
        if quantity <= 0:
            continue
        lot = InventoryLot.objects.filter(pk=lot_id).select_related("item").first()
        if lot is None:
            continue
        per_volume, per_weight = _item_per_unit_footprint(lot.item)
        if per_volume is not None:
            volume_total += per_volume * quantity
            any_volume_known = True
        if per_weight is not None:
            weight_total += per_weight * quantity
            any_weight_known = True

    return {
        "volume_cbm": volume_total if any_volume_known else None,
        "weight_kg": weight_total if any_weight_known else None,
    }


def check_location_suitability(location: WarehouseLocation, item, quantity=None) -> LocationSuitabilityResult:
    """Never blocks merely because a `LocationSuitability`/
    `LocationCapacity` row is absent — only an explicitly configured
    restriction (a category allowlist, a capacity ceiling) can block.
    Sensitive-material-vs-conditions mismatches are warnings, not hard
    blocks, since the pilot's real-world failure mode (spec section 17 /
    `BUSINESS_REQUIREMENTS.md`: sensitive materials exposed to rain/
    humidity) was about *visibility*, not making the location
    impossible to use in a pinch."""
    from apps.items.models import ProductRiskProfile

    warnings: list = []
    blockers: list = []

    allowed_categories = list(location.allowed_categories.all())
    if allowed_categories and item.category not in allowed_categories:
        allowed_names = ", ".join(str(c) for c in allowed_categories)
        blockers.append(
            f"La categoría '{item.category}' no está permitida en esta ubicación (restringida a: {allowed_names})."
        )

    risk_profile = ProductRiskProfile.objects.filter(category=item.category).first()
    suitability = getattr(location, "suitability", None)
    if risk_profile is not None and risk_profile.risk_level == ProductRiskProfile.RiskLevel.HIGH:
        if suitability is None:
            warnings.append(
                "No hay condiciones de idoneidad registradas para esta ubicación — no se puede confirmar "
                "que sea adecuada para material de alto riesgo."
            )
        else:
            if not suitability.covered:
                warnings.append("Material de alto riesgo asignado a una ubicación no techada.")
            if not suitability.dry:
                warnings.append("Material de alto riesgo asignado a una ubicación no clasificada como seca.")
            if suitability.flood_risk:
                warnings.append("Ubicación con riesgo de inundación para material de alto riesgo.")
            if not suitability.secure:
                warnings.append("Material de alto riesgo en una ubicación sin condición de seguridad marcada.")

    capacity = getattr(location, "capacity", None)
    if quantity is not None and quantity > 0 and capacity is not None:
        per_volume, per_weight = _item_per_unit_footprint(item)
        current = location_current_utilization(location)
        if per_volume is not None and capacity.available_volume_cbm is not None:
            projected = (current["volume_cbm"] or Decimal("0")) + per_volume * quantity
            if projected > capacity.available_volume_cbm:
                blockers.append(
                    f"La cantidad proyectada excede la capacidad de volumen de la ubicación "
                    f"({projected:.4f} m³ > {capacity.available_volume_cbm} m³ disponibles)."
                )
        if per_weight is not None and capacity.weight_capacity_kg is not None:
            projected_w = (current["weight_kg"] or Decimal("0")) + per_weight * quantity
            if projected_w > capacity.weight_capacity_kg:
                blockers.append(
                    f"La cantidad proyectada excede la capacidad de peso de la ubicación "
                    f"({projected_w:.3f} kg > {capacity.weight_capacity_kg} kg disponibles)."
                )

    ready = not blockers
    severity = "blocked" if blockers else ("warning" if warnings else "ready")
    return LocationSuitabilityResult(ready=ready, severity=severity, warnings=warnings, blockers=blockers)


def enforce_location_suitability(location: WarehouseLocation, item, user, *, quantity=None, override_reason=None) -> LocationSuitabilityResult:
    """Server-side gate for any workflow that assigns inventory to a
    location (receiving put-away, transfer, reassignment): raises
    `StorageSuitabilityError` if blocked and not properly overridden
    (permission + written reason), always logs a warning-level audit
    entry when there are non-blocking warnings, and always logs a
    `WAIVER` audit entry (with the before-state) when an override is
    used — mirroring the gate-override pattern (never a silent bypass)."""
    from apps.workflow.services import can_override_gates

    result = check_location_suitability(location, item, quantity=quantity)
    if result.blocked:
        if override_reason and override_reason.strip() and can_override_gates(user):
            audit.log(
                AuditEvent.Action.WAIVER, instance=location, actor=user,
                summary=f"Anulación autorizada de restricción de almacenamiento para {item} en {location}",
                reason=override_reason, before_state=result.to_dict(),
            )
        elif override_reason and override_reason.strip():
            raise StoragePermissionError("No tiene permiso para anular una restricción de almacenamiento.")
        else:
            raise StorageSuitabilityError(result)
    elif result.warnings:
        audit.log(
            AuditEvent.Action.OTHER, instance=location, actor=user,
            summary=f"Advertencia de idoneidad de almacenamiento para {item} en {location}: "
            + "; ".join(result.warnings),
        )
    return result


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
