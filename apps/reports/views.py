import io

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseGone, HttpResponseNotFound
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.core.ratelimit import is_rate_limited, record_attempt
from apps.core.storage import document_storage
from apps.documents.models import Document, DocumentType, DocumentVersion
from apps.shipments.models import ManifestPurpose, ManifestVariance, Shipment

from .models import ReportVersion, SecureShareLink, ShareSnapshot

SHARE_VIEW_MAX_REQUESTS = 30
SHARE_VIEW_WINDOW_SECONDS = 60


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

    report_version = ReportVersion.objects.create(
        report_type=ReportVersion.ReportType.SHIPMENT_DOSSIER,
        generated_by=request.user,
        created_by=request.user,
    )

    stored = document_storage.save(io.BytesIO(html.encode("utf-8")), f"snapshot-{shipment.reference}.html")
    doc_type, _ = DocumentType.objects.get_or_create(
        organization=request.user.profile.organization,
        code="generated-html-snapshot",
        defaults={"name": "Instantánea HTML generada"},
    )
    document = Document.objects.create(
        organization=request.user.profile.organization,
        document_type=doc_type,
        title=f"Instantánea — {shipment.reference}",
        uploaded_by=request.user,
        created_by=request.user,
    )
    DocumentVersion.objects.create(
        document=document,
        version_number=1,
        stored_name=stored["stored_name"],
        original_filename=stored["original_filename"],
        sha256=stored["sha256"],
        size_bytes=stored["size_bytes"],
        mime_type="text/html",
        uploaded_by=request.user,
        created_by=request.user,
    )
    report_version.rendered_html_document = document
    report_version.save()

    audit.log(
        AuditEvent.Action.OTHER,
        instance=report_version,
        actor=request.user,
        summary=f"Instantánea HTML generada para {shipment.reference}",
    )

    response = HttpResponse(html, content_type="text/html; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="snapshot-{shipment.reference}.html"'
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
