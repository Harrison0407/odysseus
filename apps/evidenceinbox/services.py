"""Unclassified Evidence Inbox (one-shot release). Every upload
immediately preserves uploader/timestamp/original-filename/checksum via
the existing `Document`/`DocumentVersion` mechanism — classification is
layered on top afterward and never touches that original record.
"""

from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.documents.models import Document, DocumentVersion

from .models import EvidenceClassification, UnclassifiedEvidence

# Models considered "leaf-level" for classification-status purposes —
# classifying evidence to one of these means it's genuinely findable
# again (a specific apartment, issue, walkthrough item, etc.), not just
# narrowed to a general area.
_LEAF_MODEL_NAMES = {
    "unit", "walkthroughitem", "walkthrough", "fieldissue", "trainingsession",
    "installationrecord", "inspectionrecord", "purchaseorderline", "planzone",
}


class EvidenceInboxError(ValueError):
    """Raised for any evidence-inbox precondition that isn't met."""


@transaction.atomic
def upload_unclassified_evidence(user, organization, *, document_type, title, uploaded_file, project=None,
                                  building=None, date_taken=None, notes=""):
    """Mirrors `apps.audit.services.attach_evidence`'s upload internals
    (SHA-256, duplicate detection) but never attaches to a target —
    that's the whole point of this inbox. Returns
    `(evidence, is_duplicate)`."""
    from apps.core.storage import document_storage

    stored = document_storage.save(uploaded_file, uploaded_file.name)
    duplicate = DocumentVersion.objects.filter(sha256=stored["sha256"]).first()

    document = Document.objects.create(
        organization=organization, document_type=document_type, title=title, uploaded_by=user, created_by=user,
    )
    DocumentVersion.objects.create(
        document=document, version_number=1, stored_name=stored["stored_name"],
        original_filename=stored["original_filename"], sha256=stored["sha256"], size_bytes=stored["size_bytes"],
        mime_type=getattr(uploaded_file, "content_type", "") or "", uploaded_by=user, is_duplicate_of=duplicate,
        created_by=user,
    )
    evidence = UnclassifiedEvidence.objects.create(
        organization=organization, project=project, building=building, document=document, date_taken=date_taken,
        notes=notes, created_by=user,
    )
    audit.log(
        AuditEvent.Action.DOCUMENT_UPLOAD, instance=evidence, actor=user,
        summary=f"Evidencia sin clasificar cargada: {document.title}", sha256=stored["sha256"],
    )
    return evidence, bool(duplicate)


def _is_leaf_target(target) -> bool:
    return target.__class__.__name__.lower() in _LEAF_MODEL_NAMES


@transaction.atomic
def classify_evidence(evidence: UnclassifiedEvidence, user, *, target, reason="") -> EvidenceClassification:
    """Never replaces or modifies the original upload provenance — only
    adds a classification pointer and updates `classification_status`."""
    classification = EvidenceClassification.objects.create(
        evidence=evidence, content_type=ContentType.objects.get_for_model(target), object_id=target.pk,
        reason=reason, created_by=user,
    )
    evidence.classification_status = (
        UnclassifiedEvidence.ClassificationStatus.CLASSIFIED if _is_leaf_target(target)
        else UnclassifiedEvidence.ClassificationStatus.PARTIALLY_CLASSIFIED
    )
    evidence.save()
    audit.log(
        AuditEvent.Action.OTHER, instance=classification, actor=user,
        summary=f"Evidencia clasificada: {evidence.document.title} -> {target}",
    )
    return classification


@transaction.atomic
def reclassify_evidence(old_classification: EvidenceClassification, user, *, target, reason) -> EvidenceClassification:
    """The prior classification is never deleted — it is marked
    inactive and linked via `superseded_by`, preserving the full
    reassignment history."""
    if not old_classification.is_active:
        raise EvidenceInboxError("Esta clasificación ya fue reasignada.")
    if not reason or not reason.strip():
        raise EvidenceInboxError("Se requiere un motivo para reasignar la clasificación.")
    new_classification = EvidenceClassification.objects.create(
        evidence=old_classification.evidence, content_type=ContentType.objects.get_for_model(target),
        object_id=target.pk, reason=reason, created_by=user,
    )
    old_classification.is_active = False
    old_classification.superseded_by = new_classification
    old_classification.save()

    evidence = old_classification.evidence
    evidence.classification_status = (
        UnclassifiedEvidence.ClassificationStatus.CLASSIFIED if _is_leaf_target(target)
        else UnclassifiedEvidence.ClassificationStatus.PARTIALLY_CLASSIFIED
    )
    evidence.save()
    audit.log(
        AuditEvent.Action.OTHER, instance=new_classification, actor=user,
        summary=f"Clasificación reasignada: {evidence.document.title} -> {target}", reason=reason,
    )
    return new_classification


def batch_classify(evidence_queryset, user, *, target, reason=""):
    """Classifies every item in `evidence_queryset` to the same
    `target` in one action — the practical "batch review" path for a
    folder of historical photographs all from the same apartment."""
    return [classify_evidence(evidence, user, target=target, reason=reason) for evidence in evidence_queryset]
