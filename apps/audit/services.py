from django.contrib.contenttypes.models import ContentType

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
