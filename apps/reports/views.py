import io

from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.http import HttpResponse, HttpResponseGone, HttpResponseNotFound
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.core.ratelimit import is_rate_limited, record_attempt
from apps.core.storage import document_storage
from apps.documents.models import Document, DocumentType, DocumentVersion
from apps.matching.models import Discrepancy
from apps.receiving.models import Inspection, Receipt
from apps.shipments.models import ManifestPurpose, ManifestVariance, Shipment

from .models import ReportVersion, SecureShareLink, ShareSnapshot

SHARE_VIEW_MAX_REQUESTS = 30
SHARE_VIEW_WINDOW_SECONDS = 60


def _save_html_snapshot(html, *, report_type, user, organization, title, filename, content_object=None):
    """Shared persistence for every self-contained HTML snapshot (spec
    section 29): stores the rendered HTML as a real `Document`/
    `DocumentVersion` (SHA-256 hashed, same as any other upload — never
    a bespoke storage path) and records a `ReportVersion` pointing at it.
    `shipment_snapshot`, `receiving_manifest_snapshot`, and the claim
    package generator all call this rather than duplicating the
    create-document-and-version dance. `content_object`, when given,
    links the `ReportVersion` back to the specific record it documents
    (e.g. a `SupplierClaim`) via the existing generic content-type
    pointer, so every package generated for that record can be found
    and counted — no separate per-domain versioning model needed."""
    report_version = ReportVersion.objects.create(report_type=report_type, generated_by=user, created_by=user)
    if content_object is not None:
        report_version.content_type = ContentType.objects.get_for_model(content_object)
        report_version.object_id = content_object.pk

    stored = document_storage.save(io.BytesIO(html.encode("utf-8")), filename)
    doc_type, _ = DocumentType.objects.get_or_create(
        organization=organization, code="generated-html-snapshot",
        defaults={"name": "Instantánea HTML generada"},
    )
    document = Document.objects.create(
        organization=organization, document_type=doc_type, title=title, uploaded_by=user, created_by=user,
    )
    DocumentVersion.objects.create(
        document=document, version_number=1, stored_name=stored["stored_name"],
        original_filename=stored["original_filename"], sha256=stored["sha256"], size_bytes=stored["size_bytes"],
        mime_type="text/html", uploaded_by=user, created_by=user,
    )
    report_version.rendered_html_document = document
    report_version.save()
    return report_version


def _build_shipment_snapshot_context(shipment):
    official_manifest = shipment.manifests.filter(purpose=ManifestPurpose.OFFICIAL_CARRIER_SUMMARY).first()
    internal_manifest = shipment.manifests.filter(purpose=ManifestPurpose.INTERNAL_OPERATIONAL_MANIFEST).first()
    official_version = official_manifest.versions.order_by("-version_number").first() if official_manifest else None
    internal_version = internal_manifest.versions.order_by("-version_number").first() if internal_manifest else None
    return {
        "shipment": shipment,
        "bl": shipment.bills_of_lading.first(),
        "internal_lines": internal_version.lines.select_related("building", "unit").all() if internal_version else [],
        "variances": ManifestVariance.objects.filter(shipment=shipment).select_related("internal_manifest_line"),
        "generated_at": timezone.now(),
    }


@login_required
def shipment_snapshot(request, pk):
    """Self-contained HTML snapshot (spec section 29): no external assets,
    read-only, states its own generation date/version, and is explicitly
    NOT the live source of truth."""

    shipment = get_object_or_404(Shipment, pk=pk, organization=request.user.profile.organization)
    context = _build_shipment_snapshot_context(shipment)
    html = render_to_string("reports/snapshot_shipment.html", context)

    _save_html_snapshot(
        html, report_type=ReportVersion.ReportType.SHIPMENT_DOSSIER, user=request.user,
        organization=request.user.profile.organization, title=f"Instantánea — {shipment.reference}",
        filename=f"snapshot-{shipment.reference}.html",
    )
    audit.log(
        AuditEvent.Action.OTHER, actor=request.user,
        summary=f"Instantánea HTML generada para {shipment.reference}",
    )

    response = HttpResponse(html, content_type="text/html; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="snapshot-{shipment.reference}.html"'
    return response


@login_required
def receiving_manifest_snapshot(request, pk):
    """Detailed internal receiving manifest (spec 13A.8) for Manuel's
    team — a self-contained, downloadable HTML document covering the
    official summary, the full internal manifest, physical receiving
    detail per line, inspections, quarantine/damage, discrepancies, the
    receiving plan, and the official-vs-operational variance matrix that
    requires attention (spec 13A.8 item 10 — the one section number this
    session could independently confirm from `OFFICIAL_VS_OPERATIONAL_MANIFEST_ANALYSIS.md`;
    see `docs/ASSUMPTIONS.md` for why the remaining section numbering is
    a faithful reconstruction from `BUSINESS_REQUIREMENTS.md` and the
    existing data model rather than a verbatim copy of the original spec
    text, which was not available to re-read in this session)."""
    receipt = get_object_or_404(
        Receipt.objects.select_related("container", "release_packet__shipment").prefetch_related(
            "lines__manifest_line__item", "inspections", "packages",
        ),
        pk=pk,
        release_packet__shipment__organization=request.user.profile.organization,
    )
    shipment = receipt.release_packet.shipment
    context = _build_shipment_snapshot_context(shipment)
    context.update({
        "receipt": receipt,
        "receiving_plan": getattr(shipment, "receiving_plan", None),
        "discrepancies": Discrepancy.objects.filter(shipment=shipment).order_by("-severity", "-created_at"),
        "inspections": Inspection.objects.filter(receipt=receipt).select_related("receipt_line__manifest_line", "inspector"),
        "damaged_package_count": receipt.packages.filter(is_damaged=True).count(),
    })
    html = render_to_string("reports/snapshot_receiving_manifest.html", context)

    organization = request.user.profile.organization
    _save_html_snapshot(
        html, report_type=ReportVersion.ReportType.ACTUAL_RECEIVING_REPORT, user=request.user,
        organization=organization, title=f"Manifiesto detallado de recepción — {receipt.container}",
        filename=f"manifiesto-recepcion-{receipt.container}.html",
    )
    audit.log(
        AuditEvent.Action.OTHER, actor=request.user,
        summary=f"Manifiesto detallado de recepción generado para {receipt.container}",
    )

    response = HttpResponse(html, content_type="text/html; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="manifiesto-recepcion-{receipt.container}.html"'
    return response


def shared_view(request, token):
    """Public (unauthenticated) read-only view for a revocable share link
    (spec section 29). Access is logged; expired/revoked links are
    denied. Rate limited per IP (spec: "rate limiting or reasonable
    protection" — see apps.core.ratelimit, docs/KNOWN_LIMITATIONS.md) —
    this endpoint is fully unauthenticated and reachable by anyone, so
    it is the highest-value place in the whole app to apply this."""
    ip = request.META.get("REMOTE_ADDR", "unknown")
    rate_limit_key = f"share-view:{ip}"
    if is_rate_limited(rate_limit_key, limit=SHARE_VIEW_MAX_REQUESTS, window_seconds=SHARE_VIEW_WINDOW_SECONDS):
        return HttpResponse("Demasiadas solicitudes. Intente de nuevo en un minuto.", status=429)
    record_attempt(rate_limit_key, window_seconds=SHARE_VIEW_WINDOW_SECONDS)

    share_link = SecureShareLink.objects.filter(token=token).first()
    if share_link is None:
        return HttpResponseNotFound("Enlace no encontrado.")
    if not share_link.is_active:
        return HttpResponseGone("Este enlace ha expirado o fue revocado.")

    ShareSnapshot.objects.create(share_link=share_link, ip_address=request.META.get("REMOTE_ADDR"))

    document = share_link.report_version.rendered_html_document
    if document is None:
        return HttpResponseNotFound("Sin contenido disponible para este enlace.")
    version = document.current_version
    with document_storage.open(version.stored_name) as fh:
        content = fh.read()
    return HttpResponse(content, content_type="text/html; charset=utf-8")
