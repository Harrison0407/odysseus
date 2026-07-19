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

from .models import (
    ClientQuote,
    InternalCommercialSheet,
    OrderLineAllocation,
    ProcurementPackage,
    PurchaseOrderLine,
    PurchasedSpare,
    Quotation,
)


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


# ---------------------------------------------------------------------------
# Controlled Transparency / Commercial Confidentiality foundation —
# ProcurementPackage lifecycle and the commercial-layer objects. Every
# sensitive read/write here is gated through apps.governance.services,
# never a bespoke ad hoc permission check.
# ---------------------------------------------------------------------------


class PackageError(ValueError):
    """Raised for any procurement-package/commercial-layer precondition
    that isn't met — including authorization failures, so callers have
    one exception type to catch."""


def create_package(organization, code, name, user, *, project=None, visibility_mode=None) -> "ProcurementPackage":
    from apps.governance.models import VisibilityMode

    from .models import ProcurementPackage

    return ProcurementPackage.objects.create(
        organization=organization, project=project, code=code, name=name,
        visibility_mode=visibility_mode or VisibilityMode.CONTROLLED_CONFIDENTIALITY, created_by=user,
    )


def assign_package_role(package, party, role_code, user, *, effective_from=None, effective_until=None, client_visible=False):
    from apps.governance import services as governance_services

    return governance_services.create_role_assignment(
        party, role_code, package.organization, user=user, package=package,
        effective_from=effective_from, effective_until=effective_until, client_visible=client_visible,
    )


@transaction.atomic
def submit_factory_quote(package, factory_party, supplier, user, *, reference, currency="USD", total_amount=None,
                          issued_date=None, valid_until=None, trade_terms="") -> "Quotation":
    """Anyone holding CREATE_COMMERCIAL_DOCUMENT in this package may
    record a factory's submitted quote — the factory itself is not
    expected to hold a system login."""
    from apps.governance import services as governance_services

    if not governance_services.has_capability(user, "CREATE_COMMERCIAL_DOCUMENT", package=package):
        raise PackageError("No tiene permiso para registrar cotizaciones de fábrica en este paquete.")
    quotation = Quotation.objects.create(
        organization=package.organization, supplier=supplier, reference=reference, currency=currency,
        total_amount=total_amount, issued_date=issued_date, valid_until=valid_until, trade_terms=trade_terms,
        package=package, factory_party=factory_party, classification="source_private", created_by=user,
    )
    audit.log(AuditEvent.Action.OTHER, instance=quotation, actor=user, summary=f"Cotización de fábrica registrada: {quotation}")
    return quotation


@transaction.atomic
def create_internal_commercial_sheet(package, user, *, source_quotation=None, factory_price, currency="USD",
                                      inland_transport=0, inspection_qc=0, consolidation=0, freight=0, insurance=0,
                                      duties_taxes=0, administration=0, contingency=0, markup_method="",
                                      markup_value=0, recommended_sell_price=None, exchange_rate_note="") -> "InternalCommercialSheet":
    from apps.governance import services as governance_services

    if not governance_services.has_capability(user, "CREATE_COMMERCIAL_DOCUMENT", package=package):
        raise PackageError("No tiene permiso para preparar hojas comerciales internas en este paquete.")
    sheet = InternalCommercialSheet.objects.create(
        package=package, source_quotation=source_quotation, factory_price=factory_price, currency=currency,
        inland_transport=inland_transport, inspection_qc=inspection_qc, consolidation=consolidation, freight=freight,
        insurance=insurance, duties_taxes=duties_taxes, administration=administration, contingency=contingency,
        markup_method=markup_method, markup_value=markup_value, recommended_sell_price=recommended_sell_price,
        exchange_rate_note=exchange_rate_note, prepared_by=user, created_by=user,
    )
    audit.log(AuditEvent.Action.OTHER, instance=sheet, actor=user, summary=f"Hoja comercial interna creada: {sheet}")
    return sheet


@transaction.atomic
def create_client_quote(package, user, *, visible_seller_party, product_description, quantity, sell_price,
                         currency="USD", client_facing_terms="", delivery_terms="", source_internal_sheet=None) -> "ClientQuote":
    """Deliberately accepts only explicit client-facing fields — never
    copies factory_price/markup/margin/internal fields from the source
    sheet, even when one is linked for internal traceability."""
    from apps.governance import services as governance_services

    if not governance_services.has_capability(user, "CREATE_COMMERCIAL_DOCUMENT", package=package):
        raise PackageError("No tiene permiso para preparar cotizaciones de cliente en este paquete.")
    quote = ClientQuote.objects.create(
        package=package, source_internal_sheet=source_internal_sheet, visible_seller_party=visible_seller_party,
        product_description=product_description, quantity=quantity, sell_price=sell_price, currency=currency,
        client_facing_terms=client_facing_terms, delivery_terms=delivery_terms, prepared_by=user, created_by=user,
    )
    audit.log(AuditEvent.Action.OTHER, instance=quote, actor=user, summary=f"Cotización de cliente preparada: {quote}")
    return quote


@transaction.atomic
def approve_client_quote(quote: "ClientQuote", user) -> "ClientQuote":
    """Separation of duties: APPROVE_CLIENT_QUOTE is never implied by
    CREATE_COMMERCIAL_DOCUMENT — the two capabilities are granted
    independently, so a quote's preparer cannot approve their own work
    unless explicitly, separately granted that capability too."""
    from apps.governance import services as governance_services

    if not governance_services.has_capability(user, "APPROVE_CLIENT_QUOTE", package=quote.package):
        raise PackageError("No tiene permiso para aprobar cotizaciones de cliente.")
    quote.status = ClientQuote.Status.APPROVED
    quote.approved_by = user
    quote.approved_at = timezone.now()
    quote.save()
    audit.log(AuditEvent.Action.OTHER, instance=quote, actor=user, summary=f"Cotización de cliente aprobada: {quote}")
    return quote


@transaction.atomic
def freeze_package(package, user, *, incoterm="", currency="USD", payment_terms="", specification_drawing=None,
                    evidence_policy="") -> "ProcurementPackage":
    """Freezes the critical package terms (spec section 16) — seller of
    record, exporter of record, China procurement operator, production
    factory/site, visibility mode, incoterm, currency, payment terms,
    approved specification/drawing revision, evidence policy. Any later
    change requires a governance.ChangeRequest, never a direct edit."""
    from apps.governance import services as governance_services
    from apps.governance.models import RoleAssignment

    if not governance_services.has_capability(user, "APPROVE_GATE", package=package):
        raise PackageError("No tiene permiso para congelar los términos de este paquete.")

    critical_roles = ["seller_of_record", "exporter_of_record", "china_procurement_operator", "production_factory", "production_site"]
    role_snapshot = {}
    for role_code in critical_roles:
        assignment = RoleAssignment.objects.filter(
            package=package, role_code=role_code, status=RoleAssignment.Status.ACTIVE,
        ).order_by("-created_at").first()
        role_snapshot[role_code] = str(assignment.party_id) if assignment else None

    package.frozen_snapshot = {
        "roles": role_snapshot, "visibility_mode": package.visibility_mode, "incoterm": incoterm,
        "currency": currency, "payment_terms": payment_terms,
        "specification_drawing_id": str(specification_drawing.id) if specification_drawing else None,
        "evidence_policy": evidence_policy,
    }
    package.is_frozen = True
    package.status = ProcurementPackage.Status.FROZEN
    package.frozen_at = timezone.now()
    package.frozen_by = user
    package.save()
    audit.log(AuditEvent.Action.PACKAGE_FREEZE, instance=package, actor=user, summary=f"Paquete congelado: {package}")
    return package


# ---------------------------------------------------------------------------
# Client-safe verification assertions and site aliases (spec sections 7-8)
# ---------------------------------------------------------------------------


def client_safe_site_alias(party, package) -> str:
    """A package-scoped alias (e.g. "Verified Production Site 1") that
    never reveals the source Party and is never a globally stable
    correlation identifier — the same real factory may receive a
    different alias number in a different package."""
    from apps.governance.models import RoleAssignment

    ordered_ids = list(
        RoleAssignment.objects.filter(package=package, role_code__in=["production_factory", "production_site"])
        .order_by("created_at").values_list("party_id", flat=True).distinct()
    )
    try:
        index = ordered_ids.index(party.id) + 1
    except ValueError:
        index = len(ordered_ids) + 1
    return f"Verified Production Site {index}"


@transaction.atomic
def create_verification_assertion(package, assertion_code, client_visible_wording, user, *, source_evidence_bundle=None,
                                   source_party=None, valid_until=None) -> "VerificationAssertion":
    from apps.audit.models import EvidenceBundle

    from .models import VerificationAssertion

    from apps.governance import services as governance_services

    if not governance_services.has_capability(user, "CREATE_COMMERCIAL_DOCUMENT", package=package):
        raise PackageError("No tiene permiso para crear aserciones de verificación en este paquete.")
    if source_evidence_bundle is not None and source_evidence_bundle.status != EvidenceBundle.Status.VERIFIED:
        raise PackageError("La aserción requiere un paquete de evidencia ya verificado, no solo cargado.")
    assertion = VerificationAssertion.objects.create(
        package=package, assertion_code=assertion_code, client_visible_wording=client_visible_wording,
        source_evidence_bundle=source_evidence_bundle, source_party=source_party, verifier=user,
        verified_at=timezone.now(), valid_until=valid_until, created_by=user,
    )
    audit.log(AuditEvent.Action.VERIFICATION_ASSERTION, instance=assertion, actor=user, summary=f"Aserción de verificación creada: {assertion}")
    return assertion


@transaction.atomic
def revoke_verification_assertion(assertion: "VerificationAssertion", user) -> "VerificationAssertion":
    assertion.is_revoked = True
    assertion.revoked_at = timezone.now()
    assertion.revoked_by = user
    assertion.save()
    audit.log(AuditEvent.Action.VERIFICATION_ASSERTION, instance=assertion, actor=user, summary=f"Aserción de verificación revocada: {assertion}")
    return assertion
