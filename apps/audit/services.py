from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from .middleware import get_current_user
from .models import Attachment, AuditEvent


def log(action, instance=None, actor=None, summary="", **metadata):
    """Convenience helper: apps.audit.services.log(AuditEvent.Action.RECEIPT_POSTING, receipt, summary="...")."""
    kwargs = {
        "action": action,
        "actor": actor or get_current_user(),
        "summary": summary,
        "metadata": metadata,
    }
    if instance is not None:
        kwargs["content_type"] = ContentType.objects.get_for_model(instance)
        kwargs["object_id"] = instance.pk
    return AuditEvent.objects.create(**kwargs)


def attach_evidence(target, user, *, document_type, title, uploaded_file):
    """Uploads a file as a `Document` (SHA-256 hashed, duplicate-detected,
    extension/size validated by the caller's form —
    `apps.documents.views.DocumentUploadForm`, reused as-is, never
    duplicated) and links it to an arbitrary target via the generic
    `Attachment` model (content_type/object_id). This is the one place
    evidence/photo upload happens for any target in the system — never a
    bespoke per-model file field."""
    from apps.core.storage import document_storage
    from apps.documents.models import Document, DocumentVersion

    organization = getattr(getattr(user, "profile", None), "organization", None)
    stored = document_storage.save(uploaded_file, uploaded_file.name)
    duplicate = DocumentVersion.objects.filter(sha256=stored["sha256"]).first()

    document = Document.objects.create(
        organization=organization, document_type=document_type, title=title,
        uploaded_by=user, created_by=user,
    )
    DocumentVersion.objects.create(
        document=document, version_number=1, stored_name=stored["stored_name"],
        original_filename=stored["original_filename"], sha256=stored["sha256"],
        size_bytes=stored["size_bytes"], mime_type=getattr(uploaded_file, "content_type", "") or "",
        uploaded_by=user, is_duplicate_of=duplicate, created_by=user,
    )
    attachment = Attachment.objects.create(
        content_type=ContentType.objects.get_for_model(target), object_id=target.pk,
        document=document, created_by=user,
    )
    log(
        AuditEvent.Action.DOCUMENT_UPLOAD, instance=attachment, actor=user,
        summary=f"Evidencia adjuntada: {document.title}", sha256=stored["sha256"],
    )
    return attachment, bool(duplicate)


def list_evidence(target):
    return Attachment.objects.filter(
        content_type=ContentType.objects.get_for_model(target), object_id=target.pk
    ).select_related("document__document_type").prefetch_related("document__versions")


# ---------------------------------------------------------------------------
# Evidence Bundles (spec section 13) — grouping EvidenceItems for a
# configurable workflow checkpoint. Uploading a file is never
# automatically verification; a bundle only reaches VERIFIED once an
# independently-authorized user (never the uploader of the item being
# verified) records that check.
# ---------------------------------------------------------------------------


class EvidenceBundleError(ValueError):
    """Raised for any evidence-bundle precondition that isn't met."""


def create_evidence_bundle(target, bundle_type, user, *, required_evidence_types=None, minimum_count=1,
                            required_verifier_capability="VERIFY_EVIDENCE", requires_geolocation=False,
                            classification="operational_shared"):
    from .models import EvidenceBundle

    return EvidenceBundle.objects.create(
        content_type=ContentType.objects.get_for_model(target), object_id=target.pk, bundle_type=bundle_type,
        required_evidence_types=required_evidence_types or [], minimum_count=minimum_count,
        required_verifier_capability=required_verifier_capability, requires_geolocation=requires_geolocation,
        classification=classification, created_by=user,
    )


def add_evidence_item(bundle, user, *, document, evidence_type="", capture_method="", captured_at=None,
                       device_metadata=None, location="", classification=None):
    from .models import EvidenceItem

    if bundle.requires_geolocation and not location:
        raise EvidenceBundleError("Este paquete de evidencia requiere geolocalización y no fue provista.")
    item = EvidenceItem.objects.create(
        bundle=bundle, document=document, evidence_type=evidence_type, capture_method=capture_method,
        captured_at=captured_at, device_metadata=device_metadata or {}, location=location,
        classification=classification or bundle.classification, uploaded_by=user, created_by=user,
    )
    update_bundle_status(bundle)
    log(AuditEvent.Action.DOCUMENT_UPLOAD, instance=item, actor=user, summary=f"Evidencia agregada al paquete: {bundle}")
    return item


def bundle_missing_requirements(bundle) -> list:
    from .models import EvidenceItem

    missing = []
    items = bundle.items.all()
    if items.count() < bundle.minimum_count:
        missing.append(f"Se requieren al menos {bundle.minimum_count} evidencia(s); hay {items.count()}.")
    for required_type in bundle.required_evidence_types:
        if not items.filter(evidence_type=required_type).exists():
            missing.append(f"Falta evidencia de tipo requerido: {required_type}.")
    if bundle.minimum_review_state == EvidenceItem.ReviewStatus.VERIFIED:
        unverified = items.exclude(review_status=EvidenceItem.ReviewStatus.VERIFIED)
        if unverified.exists():
            missing.append(f"{unverified.count()} evidencia(s) aún no verificada(s).")
    return missing


def update_bundle_status(bundle):
    from .models import EvidenceBundle

    missing = bundle_missing_requirements(bundle)
    if missing:
        bundle.status = EvidenceBundle.Status.INCOMPLETE if bundle.items.count() < bundle.minimum_count else EvidenceBundle.Status.COMPLETE
    else:
        bundle.status = EvidenceBundle.Status.VERIFIED
    bundle.save(update_fields=["status"])
    return bundle


def verify_evidence_item(item, verifying_user, *, verification_basis="", package=None):
    """Enforces the uploader/verifier separation-of-duty rule — the
    person who verifies a piece of evidence may never be the same
    person who uploaded it."""
    from .models import EvidenceItem
    from apps.governance import services as governance_services
    from apps.workflow.services import can_override_gates

    if item.uploaded_by_id is not None and item.uploaded_by_id == verifying_user.id:
        raise EvidenceBundleError("Quien carga una evidencia no puede verificarla — se requiere un verificador distinto.")

    required_capability = item.bundle.required_verifier_capability
    if package is not None:
        authorized = governance_services.has_capability(verifying_user, required_capability, package=package)
    else:
        authorized = can_override_gates(verifying_user)
    if not authorized:
        raise EvidenceBundleError("No tiene permiso para verificar esta evidencia.")

    item.review_status = EvidenceItem.ReviewStatus.VERIFIED
    item.reviewed_by = verifying_user
    item.reviewed_at = timezone.now()
    item.verification_basis = verification_basis
    item.save()
    update_bundle_status(item.bundle)
    log(AuditEvent.Action.EVIDENCE_VERIFICATION, instance=item, actor=verifying_user, summary=f"Evidencia verificada: {item}")
    return item


def reject_evidence_item(item, user, reason):
    from .models import EvidenceItem

    item.review_status = EvidenceItem.ReviewStatus.REJECTED
    item.reviewed_by = user
    item.reviewed_at = timezone.now()
    item.verification_basis = reason
    item.save()
    update_bundle_status(item.bundle)
    log(AuditEvent.Action.EVIDENCE_VERIFICATION, instance=item, actor=user, summary=f"Evidencia rechazada: {item}", reason=reason)
    return item
