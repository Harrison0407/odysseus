"""Supplier claim lifecycle (spec section 17/26).

A claim is only ever built from real, existing records — a
`Discrepancy`, a `ReceiptLine`, a `QuarantineRecord`, an `Inspection`, a
`ReplacementCase`, the Official-vs-Operational `ManifestVariance` — never
a re-entered copy of quantities that could then silently drift from the
record it was supposed to represent (core principle 4.3/4.4). The
Official Carrier Summary and the Internal Operational Manifest are never
touched or reconciled by this app; a claim referencing a
`ManifestVariance` simply points at that already-immutable record.

Lifecycle (each transition is a deliberate one-way step, guarded so it
can never be silently skipped, repeated, or run out of order — this is
what prevents duplicate submissions and gives the claim a genuine,
complete chronology via `AuditEvent`):

    DRAFT -> APPROVED -> SUBMITTED -> SUPPLIER_RESPONDED -> RESOLVED -> CLOSED

Evidence reuses `apps.audit.models.Attachment` (generic content-type
pointer to a `Document`) — no separate `ClaimEvidence` model. Package
generation reuses `apps.reports._save_html_snapshot`/`ReportVersion`
(already generic via `content_type`/`object_id`) — no separate
per-claim package-version model.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Max
from django.template.loader import render_to_string
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditEvent

from .models import SupplierClaim


class ClaimError(ValueError):
    """Raised for any claim-lifecycle precondition that isn't met —
    never silently skipped, reordered, or repeated."""


def _generate_claim_number(organization) -> str:
    year = timezone.now().year
    prefix = f"CLM-{year}-"
    last = (
        SupplierClaim.objects.filter(organization=organization, claim_number__startswith=prefix)
        .aggregate(m=Max("claim_number"))["m"]
    )
    next_seq = int(last[len(prefix):]) + 1 if last else 1
    return f"{prefix}{next_seq:04d}"


@transaction.atomic
def create_claim(shipment, supplier, claim_type, reason, user, **fields) -> SupplierClaim:
    """`fields` may include any of: purchase_order, purchase_order_line,
    item, container, manifest_variance, receipt, receipt_line,
    discrepancy, quarantine_record, inspection, replacement_case,
    quantity_claimed, quantity_damaged, quantity_missing,
    amount_claimed, currency, responsible_internal_owner,
    responsible_supplier_contact."""
    if not reason or not reason.strip():
        raise ClaimError("Se requiere una razón para el reclamo.")
    organization = shipment.organization
    claim = SupplierClaim.objects.create(
        organization=organization, claim_number=_generate_claim_number(organization),
        claim_type=claim_type, reason=reason, supplier=supplier, shipment=shipment, created_by=user, **fields,
    )
    audit.log(
        AuditEvent.Action.CLAIM_ACTION, instance=claim, actor=user,
        summary=f"Reclamo {claim.claim_number} creado ({claim.get_claim_type_display()})",
    )
    return claim


def submission_readiness(claim: SupplierClaim) -> list:
    """Non-blocking missing-document warnings — package generation and
    approval are still possible with these present, but they are always
    surfaced, never silently hidden (core principle 4.3)."""
    warnings = []
    has_evidence = audit.list_evidence(claim).exists()
    if not has_evidence:
        warnings.append("Sin evidencia adjunta (fotos, documentos, correos).")
    if claim.quantity_claimed is None and claim.amount_claimed is None:
        warnings.append("Sin cantidad ni monto reclamado especificado.")
    if claim.amount_claimed is not None and claim.currency is None:
        warnings.append("Monto reclamado sin moneda especificada.")
    if not any([claim.discrepancy_id, claim.receipt_line_id, claim.quarantine_record_id, claim.inspection_id, claim.replacement_case_id]):
        warnings.append("Sin discrepancia, línea de recepción, cuarentena, inspección o caso de reemplazo vinculado.")
    if not claim.purchase_order_id and not claim.purchase_order_line_id:
        warnings.append("Sin orden de compra ni línea de orden de compra vinculada.")
    return warnings


@transaction.atomic
def approve_claim(claim: SupplierClaim, user) -> SupplierClaim:
    """Requires at least one evidence attachment — a conservative,
    non-legal operational rule (see `docs/ASSUMPTIONS.md`): a claim
    with zero evidence should not be approved for external submission."""
    if claim.status != SupplierClaim.Status.DRAFT:
        raise ClaimError("Solo un reclamo en borrador puede aprobarse.")
    if not audit.list_evidence(claim).exists():
        raise ClaimError("No se puede aprobar un reclamo sin evidencia adjunta.")
    claim.status = SupplierClaim.Status.APPROVED
    claim.approved_by = user
    claim.approved_at = timezone.now()
    claim.save()
    audit.log(AuditEvent.Action.CLAIM_ACTION, instance=claim, actor=user, summary=f"Reclamo {claim.claim_number} aprobado para envío")
    return claim


@transaction.atomic
def submit_claim(claim: SupplierClaim, user) -> SupplierClaim:
    """Refuses to run twice — the guard against a duplicate submission
    is simply that only an `APPROVED` claim can transition here, and
    this call always leaves it `SUBMITTED`."""
    if claim.status != SupplierClaim.Status.APPROVED:
        raise ClaimError("Solo un reclamo aprobado puede marcarse como enviado.")
    claim.status = SupplierClaim.Status.SUBMITTED
    claim.submitted_by = user
    claim.submitted_at = timezone.now()
    claim.save()
    audit.log(AuditEvent.Action.CLAIM_ACTION, instance=claim, actor=user, summary=f"Reclamo {claim.claim_number} marcado como enviado al proveedor")
    return claim


@transaction.atomic
def record_supplier_response(claim: SupplierClaim, user, *, response, notes="", response_date=None) -> SupplierClaim:
    if claim.status not in (SupplierClaim.Status.SUBMITTED, SupplierClaim.Status.SUPPLIER_RESPONDED):
        raise ClaimError("Solo un reclamo enviado puede recibir una respuesta del proveedor.")
    if response == SupplierClaim.SupplierResponse.PENDING:
        raise ClaimError("Debe registrarse una respuesta concreta del proveedor, no 'pendiente'.")
    claim.supplier_response = response
    claim.supplier_response_notes = notes
    claim.supplier_response_date = response_date or timezone.now().date()
    claim.status = SupplierClaim.Status.SUPPLIER_RESPONDED
    claim.save()
    audit.log(
        AuditEvent.Action.CLAIM_ACTION, instance=claim, actor=user,
        summary=f"Reclamo {claim.claim_number}: respuesta del proveedor registrada ({claim.get_supplier_response_display()})",
    )
    return claim


@transaction.atomic
def resolve_claim(claim: SupplierClaim, user, *, resolution_type, resolution_reference="", resolution_amount=None) -> SupplierClaim:
    if claim.status != SupplierClaim.Status.SUPPLIER_RESPONDED:
        raise ClaimError("Solo un reclamo con respuesta del proveedor registrada puede resolverse.")
    if resolution_type == SupplierClaim.ResolutionType.NONE:
        raise ClaimError("Debe indicarse un tipo de resolución concreto (reemplazo, nota de crédito o liquidación).")
    claim.resolution_type = resolution_type
    claim.resolution_reference = resolution_reference
    claim.resolution_amount = resolution_amount
    claim.resolved_by = user
    claim.resolved_at = timezone.now()
    claim.status = SupplierClaim.Status.RESOLVED
    claim.save()
    audit.log(
        AuditEvent.Action.CLAIM_ACTION, instance=claim, actor=user,
        summary=f"Reclamo {claim.claim_number} resuelto ({claim.get_resolution_type_display()})",
    )
    return claim


@transaction.atomic
def close_claim(claim: SupplierClaim, user, *, closure_notes="") -> SupplierClaim:
    if claim.status != SupplierClaim.Status.RESOLVED:
        raise ClaimError("Solo un reclamo resuelto puede cerrarse.")
    claim.closed_by = user
    claim.closed_at = timezone.now()
    claim.closure_notes = closure_notes
    claim.status = SupplierClaim.Status.CLOSED
    claim.save()
    audit.log(AuditEvent.Action.CLAIM_ACTION, instance=claim, actor=user, summary=f"Reclamo {claim.claim_number} cerrado")
    return claim


def generate_claim_package(claim: SupplierClaim, user):
    """Renders and persists the professional claim package: cover
    summary, supplier/PO info, shipment/container references, affected
    product lines, discrepancy detail, quantity/value calculation,
    chronology (from `AuditEvent`), evidence index, receiving/
    inspection findings, requested remedy, contacts, and
    provenance/timestamp. Reuses the exact same self-contained-HTML +
    `Document`/`ReportVersion` mechanism as every other snapshot in this
    system (`apps.reports._save_html_snapshot`) — never a bespoke export
    path. Missing-document warnings are always rendered into the
    document itself, never silently omitted."""
    from django.contrib.contenttypes.models import ContentType

    from apps.reports.models import ReportVersion
    from apps.reports.views import _save_html_snapshot

    evidence = audit.list_evidence(claim)
    chronology = AuditEvent.objects.filter(
        content_type=ContentType.objects.get_for_model(SupplierClaim), object_id=claim.pk
    ).order_by("created_at")
    warnings = submission_readiness(claim)

    html = render_to_string("claims/claim_package.html", {
        "claim": claim, "evidence": evidence, "chronology": chronology, "warnings": warnings,
        "generated_at": timezone.now(),
    })
    filename = f"reclamo-{claim.claim_number}.html"
    report_version = _save_html_snapshot(
        html, report_type=ReportVersion.ReportType.CLAIM_PACKAGE, user=user, organization=claim.organization,
        title=f"Paquete de reclamo — {claim.claim_number}", filename=filename, content_object=claim,
    )
    audit.log(
        AuditEvent.Action.CLAIM_ACTION, instance=claim, actor=user,
        summary=f"Paquete de reclamo generado para {claim.claim_number}",
    )
    return report_version, html
