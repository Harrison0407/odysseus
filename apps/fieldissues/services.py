"""Field issue reporting and corrective-action tracking (one-shot
release, section 4).

Lifecycle:
    REPORTED -> ASSIGNED -> IN_PROGRESS -> CORRECTION_COMPLETED
    -> READY_FOR_VERIFICATION -> VERIFIED_CLOSED
                             \\-> RETURNED_FOR_CORRECTION -> RESUBMITTED
                                 -> REINSPECTION -> VERIFIED_CLOSED (loop)

The person who performs the correction never automatically gains
closure authority — `verify_and_close_issue` always re-checks the same
senior-authorization permission every other approval-style action in
this system uses, regardless of who did the work.
"""

from __future__ import annotations

import datetime

from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.workflow.services import can_override_gates

from .models import FieldIssue, FieldIssueEvidence


class FieldIssueError(ValueError):
    """Raised for any field-issue lifecycle precondition that isn't
    met — never silently skipped, reordered, or repeated."""


DUPLICATE_SUBMISSION_WINDOW = datetime.timedelta(seconds=60)


@transaction.atomic
def report_issue(building, reporter, *, title, description="", category=None, priority=FieldIssue.Priority.MEDIUM,
                  floor=None, unit=None, room_or_location="", item=None, component_description="", **extra_refs) -> FieldIssue:
    """Idempotent against a double-click/refresh: an identical report
    (same building, reporter, title) created within the last minute is
    returned as-is rather than duplicated."""
    if not title or not title.strip():
        raise FieldIssueError("Se requiere un título para el reporte.")
    recent_cutoff = timezone.now() - DUPLICATE_SUBMISSION_WINDOW
    existing = FieldIssue.objects.filter(
        building=building, created_by=reporter, title=title, created_at__gte=recent_cutoff,
    ).order_by("-created_at").first()
    if existing is not None:
        return existing

    issue = FieldIssue.objects.create(
        building=building, floor=floor, unit=unit, room_or_location=room_or_location, category=category,
        priority=priority, title=title, description=description, item=item, component_description=component_description,
        created_by=reporter, **extra_refs,
    )
    audit.log(AuditEvent.Action.OTHER, instance=issue, actor=reporter, summary=f"Incidencia reportada: {issue.title}")
    return issue


@transaction.atomic
def assign_issue(issue: FieldIssue, user, *, responsible_user=None, responsible_department=None) -> FieldIssue:
    if issue.status not in (FieldIssue.Status.REPORTED, FieldIssue.Status.ASSIGNED):
        raise FieldIssueError("Solo una incidencia reportada o ya asignada puede reasignarse.")
    if responsible_user is None and responsible_department is None:
        raise FieldIssueError("Debe indicarse un responsable (usuario o equipo).")
    issue.responsible_user = responsible_user
    issue.responsible_department = responsible_department
    issue.status = FieldIssue.Status.ASSIGNED
    issue.save()
    audit.log(AuditEvent.Action.OTHER, instance=issue, actor=user, summary=f"Incidencia asignada: {issue.title} -> {responsible_user or responsible_department}")
    return issue


@transaction.atomic
def start_progress(issue: FieldIssue, user) -> FieldIssue:
    if issue.status != FieldIssue.Status.ASSIGNED:
        raise FieldIssueError("Solo una incidencia asignada puede marcarse en progreso.")
    issue.status = FieldIssue.Status.IN_PROGRESS
    issue.save()
    audit.log(AuditEvent.Action.OTHER, instance=issue, actor=user, summary=f"Incidencia en progreso: {issue.title}")
    return issue


def _has_evidence(issue: FieldIssue, stage) -> bool:
    return issue.evidence.filter(stage=stage).exists()


@transaction.atomic
def record_correction(issue: FieldIssue, user, *, description, before_evidence_waived_reason=None) -> FieldIssue:
    if issue.status not in (FieldIssue.Status.IN_PROGRESS, FieldIssue.Status.REINSPECTION):
        raise FieldIssueError("Solo una incidencia en progreso o en reinspección puede registrar una corrección.")
    if not description or not description.strip():
        raise FieldIssueError("Se requiere una descripción de la corrección realizada.")
    if not _has_evidence(issue, FieldIssueEvidence.Stage.BEFORE) and not (before_evidence_waived_reason or issue.before_evidence_waived_reason):
        raise FieldIssueError(
            "Se requiere evidencia 'antes' — o una razón autorizada registrada de por qué no está disponible."
        )
    if before_evidence_waived_reason:
        issue.before_evidence_waived_reason = before_evidence_waived_reason
    issue.correction_description = description
    issue.correction_performed_by = user
    issue.correction_completed_at = timezone.now()
    issue.status = FieldIssue.Status.CORRECTION_COMPLETED
    issue.save()
    audit.log(AuditEvent.Action.OTHER, instance=issue, actor=user, summary=f"Corrección registrada: {issue.title}")
    return issue


@transaction.atomic
def mark_ready_for_verification(issue: FieldIssue, user) -> FieldIssue:
    if issue.status != FieldIssue.Status.CORRECTION_COMPLETED:
        raise FieldIssueError("Solo una incidencia con corrección completada puede enviarse a verificación.")
    issue.status = FieldIssue.Status.READY_FOR_VERIFICATION
    issue.save()
    audit.log(AuditEvent.Action.OTHER, instance=issue, actor=user, summary=f"Lista para verificación: {issue.title}")
    return issue


@transaction.atomic
def verify_and_close_issue(issue: FieldIssue, verifier, *, notes="") -> FieldIssue:
    """Requires the same senior-authorization permission used
    throughout this system — completing the correction never grants
    this by itself, regardless of who performed the work."""
    if issue.status not in (FieldIssue.Status.READY_FOR_VERIFICATION, FieldIssue.Status.REINSPECTION):
        raise FieldIssueError("Solo una incidencia lista para verificación o en reinspección puede cerrarse.")
    if not can_override_gates(verifier):
        raise FieldIssueError("No tiene permiso para verificar y cerrar incidencias.")
    if not issue.correction_description:
        raise FieldIssueError("Falta la descripción de la corrección.")
    if not issue.responsible_user and not issue.responsible_department:
        raise FieldIssueError("Falta el responsable que realizó el trabajo.")
    if not issue.correction_completed_at:
        raise FieldIssueError("Falta la marca de tiempo de finalización de la corrección.")
    if not _has_evidence(issue, FieldIssueEvidence.Stage.BEFORE) and not issue.before_evidence_waived_reason:
        raise FieldIssueError("Falta evidencia 'antes' (o su justificación autorizada).")
    if not _has_evidence(issue, FieldIssueEvidence.Stage.AFTER):
        raise FieldIssueError("Falta evidencia 'después' para poder cerrar la incidencia.")

    issue.status = FieldIssue.Status.VERIFIED_CLOSED
    issue.verified_by = verifier
    issue.verified_at = timezone.now()
    issue.verification_notes = notes
    issue.save()
    audit.log(AuditEvent.Action.OTHER, instance=issue, actor=verifier, summary=f"Incidencia verificada y cerrada: {issue.title}", reason=notes)
    return issue


@transaction.atomic
def reject_correction(issue: FieldIssue, verifier, *, reason) -> FieldIssue:
    """The rejected evidence/comments are never deleted — a later
    resubmission only ever adds new evidence and starts a new
    verification cycle."""
    if issue.status not in (FieldIssue.Status.READY_FOR_VERIFICATION, FieldIssue.Status.REINSPECTION):
        raise FieldIssueError("Solo una incidencia lista para verificación o en reinspección puede rechazarse.")
    if not can_override_gates(verifier):
        raise FieldIssueError("No tiene permiso para rechazar una corrección.")
    if not reason or not reason.strip():
        raise FieldIssueError("Se requiere una razón para rechazar la corrección.")
    issue.status = FieldIssue.Status.RETURNED_FOR_CORRECTION
    issue.verification_notes = reason
    issue.save()
    audit.log(AuditEvent.Action.OTHER, instance=issue, actor=verifier, summary=f"Corrección rechazada: {issue.title}", reason=reason)
    return issue


@transaction.atomic
def resubmit_correction(issue: FieldIssue, user, *, description) -> FieldIssue:
    if issue.status != FieldIssue.Status.RETURNED_FOR_CORRECTION:
        raise FieldIssueError("Solo una incidencia devuelta para corrección puede reenviarse.")
    if not description or not description.strip():
        raise FieldIssueError("Se requiere una descripción de la nueva corrección.")
    issue.correction_description = description
    issue.correction_performed_by = user
    issue.correction_completed_at = timezone.now()
    issue.status = FieldIssue.Status.RESUBMITTED
    issue.save()
    audit.log(AuditEvent.Action.OTHER, instance=issue, actor=user, summary=f"Corrección reenviada: {issue.title}")
    return issue


@transaction.atomic
def mark_for_reinspection(issue: FieldIssue, user) -> FieldIssue:
    if issue.status != FieldIssue.Status.RESUBMITTED:
        raise FieldIssueError("Solo una incidencia reenviada puede pasar a reinspección.")
    issue.status = FieldIssue.Status.REINSPECTION
    issue.save()
    audit.log(AuditEvent.Action.OTHER, instance=issue, actor=user, summary=f"Incidencia en reinspección: {issue.title}")
    return issue


def add_evidence(issue: FieldIssue, user, *, document_type, title, uploaded_file, stage, caption=""):
    """Reuses `apps.audit.services.attach_evidence` (the one place a
    file becomes evidence, extension/size validated, SHA-256 duplicate
    detected) for the actual upload, then wraps the resulting Document
    with the before/during/after `stage` this specific closure rule
    needs — never a second upload mechanism."""
    attachment, is_duplicate = audit.attach_evidence(issue, user, document_type=document_type, title=title, uploaded_file=uploaded_file)
    evidence = FieldIssueEvidence.objects.create(
        field_issue=issue, document=attachment.document, stage=stage, caption=caption, created_by=user,
    )
    return evidence, is_duplicate
