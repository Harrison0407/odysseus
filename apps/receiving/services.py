"""Physical receiving posting logic.

Core principle 4.5 (ledger-based inventory) and 4.3 (no silent
confirmation): posting a receipt line here is the *only* way inventory
increases from a shipment. A commercial document or an unverified PO can
never create inventory on its own.
"""

import dataclasses
from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.core.models import Severity
from apps.inventory.models import InventoryLot, InventoryMovement, MovementType, QuarantineRecord
from apps.inventory.services import enforce_location_suitability
from apps.matching.models import Discrepancy, DiscrepancyType

from .models import AlternativeStorageOption, DamageRecord, ReceiptLine, StorageComparisonScenario


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


# ---------------------------------------------------------------------------
# External storage comparison calculator (spec section 17, Priority 1)
# ---------------------------------------------------------------------------


class StorageComparisonError(ValueError):
    """Raised for any comparison-scenario precondition that isn't
    met — never silently skipped (e.g. editing a finalized scenario)."""


@dataclasses.dataclass
class OptionComparisonResult:
    option: "AlternativeStorageOption"
    guaranteed_total: Decimal | None
    used_minimum_commitment: bool
    demurrage_exposure: Decimal | None
    converted_total: Decimal | None
    conversion_note: str
    suitability_warnings: list = dataclasses.field(default_factory=list)
    rank: int | None = None


@transaction.atomic
def create_comparison_scenario(receiving_plan, user, *, name="") -> StorageComparisonScenario:
    """Starts a new comparison version for this receiving plan. Any
    prior version is marked `is_current=False` but never deleted or
    edited — immutable scenario history (spec section 17)."""
    StorageComparisonScenario.objects.filter(receiving_plan=receiving_plan, is_current=True).update(is_current=False)
    last_version = (
        StorageComparisonScenario.objects.filter(receiving_plan=receiving_plan).aggregate(m=Max("version_number"))["m"]
        or 0
    )
    return StorageComparisonScenario.objects.create(
        receiving_plan=receiving_plan, name=name, version_number=last_version + 1, created_by=user,
    )


def add_storage_option(scenario, *, created_by=None, **fields) -> AlternativeStorageOption:
    """Adds one option to a scenario. Raises `StorageComparisonError`
    if the scenario is already `FINALIZED` — a decided comparison is
    immutable, matching every other decided/finalized record in this
    system (`LandedCostVersion.is_final`, `Handoff` acceptance, etc.)."""
    if scenario.status == StorageComparisonScenario.Status.FINALIZED:
        raise StorageComparisonError("No se puede modificar una comparación ya finalizada — cree una nueva versión.")
    return AlternativeStorageOption.objects.create(scenario=scenario, created_by=created_by, **fields)


def update_storage_option(option: AlternativeStorageOption, **fields) -> AlternativeStorageOption:
    if option.scenario.status == StorageComparisonScenario.Status.FINALIZED:
        raise StorageComparisonError("No se puede modificar una comparación ya finalizada — cree una nueva versión.")
    for field_name, value in fields.items():
        setattr(option, field_name, value)
    option.save()
    return option


def _option_guaranteed_total(option: AlternativeStorageOption):
    """Sums the components that make up a firm, non-contingent total
    cost (storage/handling/transport/insurance), applying the minimum
    commitment as a floor when it exceeds the calculated sum.
    Demurrage/penalty exposure is deliberately excluded — it is
    contingent risk exposure, not a guaranteed cost (see
    `docs/ASSUMPTIONS.md`). Returns `(None, False)` if every component
    is unknown, never a fabricated zero."""
    components = [
        option.storage_cost, option.handling_cost,
        option.inbound_transport_cost, option.outbound_transport_cost, option.insurance_cost,
    ]
    known = [c for c in components if c is not None]
    if not known:
        return None, False
    total = sum(known, Decimal("0"))
    if option.minimum_commitment_amount is not None and option.minimum_commitment_amount > total:
        return option.minimum_commitment_amount, True
    return total, False


def _option_suitability_warnings(option: AlternativeStorageOption, receiving_plan) -> list:
    """A qualitative, non-blocking advisory only — this is a planning
    comparison, not a put-away gate (that enforcement already happens
    at `apps.inventory.services.enforce_location_suitability` when
    inventory actually moves). For an `internal_baseline` option linked
    to a real `WarehouseLocation`, reuses that location's already
    registered `LocationSuitability` instead of duplicating it; for an
    external option, reads its own covered/dry/secure/climate_controlled
    fields directly."""
    warnings: list = []
    if not receiving_plan.has_sensitive_materials:
        return warnings

    if option.option_type == AlternativeStorageOption.OptionType.INTERNAL_BASELINE and option.internal_location is not None:
        suitability = getattr(option.internal_location, "suitability", None)
        if suitability is None:
            warnings.append("Sin condiciones de idoneidad registradas para la ubicación interna de referencia.")
        else:
            if not suitability.covered:
                warnings.append("Ubicación interna no techada para material sensible.")
            if not suitability.dry:
                warnings.append("Ubicación interna no clasificada como seca para material sensible.")
            if not suitability.secure:
                warnings.append("Ubicación interna sin condición de seguridad marcada para material sensible.")
    else:
        if not option.covered:
            warnings.append("Opción no techada para material sensible.")
        if not option.dry:
            warnings.append("Opción no clasificada como seca para material sensible.")
        if not option.secure:
            warnings.append("Opción sin condición de seguridad marcada para material sensible.")
    return warnings


def compare_scenario_options(scenario) -> list:
    """Computes a ranked comparison across every option in this
    scenario. Options whose total cost cannot be expressed in the
    organization's base currency (no `ExchangeRate` on file, and not
    already in that currency) are still returned — with
    `converted_total=None` and an explanatory `conversion_note` — never
    silently converted at a fabricated rate (core principle 4.3), and
    never silently dropped from the comparison."""
    from apps.cost.services import convert_to_base_currency

    receiving_plan = scenario.receiving_plan
    organization = receiving_plan.shipment.organization
    results = []
    for option in scenario.options.select_related("currency", "internal_location__suitability").all():
        guaranteed_total, used_minimum = _option_guaranteed_total(option)
        conversion_note = ""
        converted_total = None
        if guaranteed_total is None:
            conversion_note = "Sin costos suficientes registrados para calcular un total."
        elif option.currency is None:
            conversion_note = "Sin moneda especificada — no se puede confirmar comparabilidad."
        elif option.currency.code == organization.default_currency:
            converted_total = guaranteed_total
        else:
            converted_total = convert_to_base_currency(guaranteed_total, option.currency.code, organization)
            if converted_total is None:
                conversion_note = (
                    f"Sin tasa de cambio registrada de {option.currency.code} a "
                    f"{organization.default_currency} — comparación no disponible en una sola moneda."
                )
            else:
                conversion_note = f"Convertido de {option.currency.code} a {organization.default_currency}."
        results.append(
            OptionComparisonResult(
                option=option, guaranteed_total=guaranteed_total, used_minimum_commitment=used_minimum,
                demurrage_exposure=option.demurrage_penalty_estimated_cost, converted_total=converted_total,
                conversion_note=conversion_note, suitability_warnings=_option_suitability_warnings(option, receiving_plan),
            )
        )

    rankable = [r for r in results if r.converted_total is not None]
    for rank, result in enumerate(sorted(rankable, key=lambda r: r.converted_total), start=1):
        result.rank = rank
    return results


@transaction.atomic
def finalize_comparison_scenario(scenario, chosen_option, user, *, rationale) -> StorageComparisonScenario:
    """Records the recommendation/decision, once. Immutable afterward —
    `add_storage_option`/`update_storage_option` both refuse further
    edits to a `FINALIZED` scenario."""
    if scenario.status == StorageComparisonScenario.Status.FINALIZED:
        raise StorageComparisonError("Esta comparación ya fue finalizada.")
    if chosen_option.scenario_id != scenario.id:
        raise StorageComparisonError("La opción elegida no pertenece a esta comparación.")
    if not rationale or not rationale.strip():
        raise StorageComparisonError("Se requiere una justificación para la decisión.")
    scenario.status = StorageComparisonScenario.Status.FINALIZED
    scenario.chosen_option = chosen_option
    scenario.recommendation_rationale = rationale
    scenario.decided_by = user
    scenario.decided_at = timezone.now()
    scenario.save()
    audit.log(
        AuditEvent.Action.OTHER, instance=scenario, actor=user,
        summary=f"Comparación de almacenaje finalizada: opción elegida '{chosen_option.option_name}'",
        reason=rationale,
    )
    return scenario
