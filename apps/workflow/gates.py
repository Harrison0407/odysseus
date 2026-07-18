"""Reusable gate-evaluation engine.

This is the single place gate readiness logic lives. Views, templates,
and the handoff service call `evaluate_gate(gate_definition, target)` —
none of them re-implement or duplicate a readiness rule. Every evaluator
returns a `GateResult`, a plain, JSON-serializable structure so it can be
frozen onto `Handoff.readiness_snapshot` at submission time and shown to
the reviewing party exactly as it was when submitted.

Official Logistics and Operational Logistics are never merged here: the
`logistics_to_receiving` evaluator reads `ManifestVariance`/
`CustomsReviewDecision` (the dual-manifest engine built in the Priority 0
milestone) and reports a mismatch as a gate condition — it never
resolves a mismatch by editing either the official `BillOfLading` or the
internal manifest.
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass
class GateResult:
    ready: bool
    severity: str  # "ready" | "warning" | "blocked"
    unmet_requirements: list = dataclasses.field(default_factory=list)
    unresolved_discrepancies: list = dataclasses.field(default_factory=list)
    missing_documents: list = dataclasses.field(default_factory=list)
    missing_approvals: list = dataclasses.field(default_factory=list)
    quarantined_inventory: list = dataclasses.field(default_factory=list)
    open_claims: list = dataclasses.field(default_factory=list)
    incomplete_receiving: list = dataclasses.field(default_factory=list)
    customs_review_required: list = dataclasses.field(default_factory=list)
    evidence_requirements: list = dataclasses.field(default_factory=list)
    override_available: bool = True

    @property
    def blocked(self) -> bool:
        return not self.ready

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Purchasing -> Finance
# ---------------------------------------------------------------------------


def evaluate_purchasing_to_finance(purchase_order) -> GateResult:
    unmet = []
    missing_approvals = []
    if purchase_order.approval_status != purchase_order.ApprovalStatus.APPROVED:
        unmet.append("La orden de compra no está aprobada.")
        missing_approvals.append("Aprobación de la orden de compra")

    ready = not unmet
    return GateResult(
        ready=ready,
        severity="ready" if ready else "blocked",
        unmet_requirements=unmet,
        missing_approvals=missing_approvals,
    )


# ---------------------------------------------------------------------------
# Finance -> Logistics
# ---------------------------------------------------------------------------


def evaluate_finance_to_logistics(purchase_order) -> GateResult:
    from apps.procurement.models import PaymentMilestone

    pending = list(
        purchase_order.payment_milestones.filter(
            status__in=[PaymentMilestone.Status.PENDING_APPROVAL, PaymentMilestone.Status.ON_HOLD]
        ).values_list("name", flat=True)
    )
    unmet = [f"Hito de pago pendiente o en espera: {name}" for name in pending]
    ready = not unmet
    return GateResult(
        ready=ready,
        severity="ready" if ready else "blocked",
        unmet_requirements=unmet,
        missing_approvals=pending,
    )


# ---------------------------------------------------------------------------
# Logistics -> Receiving  (the operational-verification gate, spec 9.5)
# ---------------------------------------------------------------------------


def evaluate_logistics_to_receiving(shipment) -> GateResult:
    missing_documents = []
    unresolved_discrepancies = []
    customs_review_required = []
    unmet = []

    if not shipment.bills_of_lading.exists():
        missing_documents.append("Conocimiento de embarque (BL)")

    critical_variances = shipment.manifest_variances.filter(severity="critical", is_explained=False)
    for variance in critical_variances:
        unresolved_discrepancies.append(
            f"{variance.official_category_text or '(sin categoría oficial)'} vs {variance.internal_manifest_line}"
        )
    for variance in critical_variances.filter(customs_review_required=True):
        customs_review_required.append(str(variance.internal_manifest_line))

    plan = getattr(shipment, "receiving_plan", None)
    if plan is None or plan.status not in (plan.Status.SUITABLE, plan.Status.OVERRIDDEN):
        unmet.append("Plan de recepción no confirmado como adecuado.")

    other_open_discrepancies = shipment.discrepancies.filter(severity="critical", is_resolved=False, manifest_variance__isnull=True)
    for discrepancy in other_open_discrepancies:
        unresolved_discrepancies.append(discrepancy.get_discrepancy_type_display())

    ready = not (missing_documents or unresolved_discrepancies or unmet)
    return GateResult(
        ready=ready,
        severity="ready" if ready else "blocked",
        unmet_requirements=unmet,
        unresolved_discrepancies=unresolved_discrepancies,
        missing_documents=missing_documents,
        customs_review_required=customs_review_required,
        evidence_requirements=["Conocimiento de embarque (BL)", "Manifiesto operativo interno versionado"],
    )


# ---------------------------------------------------------------------------
# Receiving -> Warehouse
# ---------------------------------------------------------------------------


def evaluate_receiving_to_warehouse(shipment) -> GateResult:
    from apps.inventory.models import QuarantineRecord
    from apps.receiving.models import Receipt

    incomplete_receiving = []
    quarantined_inventory = []

    receipts = Receipt.objects.filter(release_packet__shipment=shipment)
    if not receipts.exists():
        incomplete_receiving.append("No existe un recibo físico registrado para este embarque.")
    else:
        for receipt in receipts.filter(status=Receipt.Status.PROVISIONAL):
            incomplete_receiving.append(f"Recibo del contenedor {receipt.container} sigue provisional.")

    open_quarantine = QuarantineRecord.objects.filter(
        lot__source_receipt_line__receipt__release_packet__shipment=shipment,
        released_at__isnull=True,
    ).select_related("lot__item")
    for quarantine in open_quarantine:
        quarantined_inventory.append(f"{quarantine.lot.item}: {quarantine.quantity} en cuarentena ({quarantine.reason})")

    unresolved_discrepancies = [
        d.get_discrepancy_type_display()
        for d in shipment.discrepancies.filter(severity="critical", is_resolved=False)
    ]

    ready = not (incomplete_receiving or quarantined_inventory or unresolved_discrepancies)
    return GateResult(
        ready=ready,
        severity="ready" if ready else "blocked",
        unresolved_discrepancies=unresolved_discrepancies,
        quarantined_inventory=quarantined_inventory,
        incomplete_receiving=incomplete_receiving,
    )


# ---------------------------------------------------------------------------
# Warehouse -> Project
# ---------------------------------------------------------------------------


def evaluate_warehouse_to_project(material_request) -> GateResult:
    unmet = []
    for line in material_request.lines.all():
        if line.quantity_approved is None:
            unmet.append(f"{line.item}: cantidad aún no aprobada.")
        elif line.quantity_reserved < line.quantity_approved:
            unmet.append(
                f"{line.item}: reservado {line.quantity_reserved} de {line.quantity_approved} aprobado."
            )
    ready = not unmet
    return GateResult(ready=ready, severity="ready" if ready else "blocked", unmet_requirements=unmet)


# ---------------------------------------------------------------------------
# Project Delivery -> Installation
# ---------------------------------------------------------------------------


def evaluate_project_delivery_to_installation(delivery) -> GateResult:
    unmet = []
    if delivery.accepted is not True:
        unmet.append("La entrega no ha sido aceptada por el proyecto.")
    if not delivery.project_receipts.exists():
        unmet.append("No existe una recepción de proyecto vinculada a esta entrega.")

    total_accepted = sum((line.quantity_accepted or 0) for line in delivery.lines.all())
    if total_accepted <= 0:
        unmet.append("No hay cantidad aceptada registrada en esta entrega — nada disponible para instalar.")

    ready = not unmet
    return GateResult(ready=ready, severity="ready" if ready else "blocked", unmet_requirements=unmet)


# ---------------------------------------------------------------------------
# Installation -> Inspection
# ---------------------------------------------------------------------------


def evaluate_installation_to_inspection(installation) -> GateResult:
    unmet = []
    if not installation.installed_at:
        unmet.append("La instalación aún no tiene fecha registrada.")
    if not installation.quantity_installed or installation.quantity_installed <= 0:
        unmet.append("Cantidad instalada no registrada.")
    ready = not unmet
    return GateResult(ready=ready, severity="ready" if ready else "blocked", unmet_requirements=unmet)


# ---------------------------------------------------------------------------
# Inspection -> Acceptance
# ---------------------------------------------------------------------------


def evaluate_inspection_to_acceptance(installation) -> GateResult:
    """A failed inspection must never transition to acceptance, and open
    *blocking* punch-list defects must prevent it too (unless a properly
    authorized gate override is used at submission — see
    apps.workflow.services.submit_handoff). Only the most recent
    inspection in the chain (`previous_inspection`) counts — a passed
    reinspection supersedes an earlier fail without erasing it."""
    from apps.requests.models import PunchListItem

    unmet = []
    latest = installation.inspections.order_by("-created_at").first()
    if latest is None:
        unmet.append("No existe inspección registrada.")
    elif latest.passed is not True:
        unmet.append(
            "La inspección más reciente no fue aprobada — se requiere una reinspección aprobada antes de aceptar."
        )

    open_blocking_defects = [
        item.description[:80]
        for inspection in installation.inspections.all()
        for item in inspection.punch_list_items.filter(status=PunchListItem.Status.OPEN, is_blocking=True)
    ]
    if open_blocking_defects:
        unmet.append("Existen defectos críticos abiertos que bloquean la aceptación final.")

    ready = not unmet
    return GateResult(
        ready=ready,
        severity="ready" if ready else "blocked",
        unmet_requirements=unmet,
        unresolved_discrepancies=open_blocking_defects,
    )


GATE_EVALUATORS = {
    "purchasing_to_finance": evaluate_purchasing_to_finance,
    "finance_to_logistics": evaluate_finance_to_logistics,
    "logistics_to_receiving": evaluate_logistics_to_receiving,
    "receiving_to_warehouse": evaluate_receiving_to_warehouse,
    "warehouse_to_project": evaluate_warehouse_to_project,
    "project_delivery_to_installation": evaluate_project_delivery_to_installation,
    "installation_to_inspection": evaluate_installation_to_inspection,
    "inspection_to_acceptance": evaluate_inspection_to_acceptance,
}


def evaluate_gate(gate_definition, target) -> GateResult:
    """The single dispatch point every caller (service layer, views,
    tests) must use — gate logic never gets re-implemented inline."""
    evaluator = GATE_EVALUATORS.get(gate_definition.code)
    if evaluator is None:
        raise ValueError(f"No hay evaluador registrado para el gate '{gate_definition.code}'.")
    return evaluator(target)
