"""Lawson training and reference-installation sessions (one-shot
release, section 5). Lawson is a configured user — every function here
takes `trainer`/`participants` as ordinary user references, never a
hard-coded name.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.workflow.services import can_override_gates

from .models import TrainingChecklistItem, TrainingParticipantAcknowledgement, TrainingSession


class TrainingError(ValueError):
    """Raised for any training-session precondition that isn't met."""


@transaction.atomic
def create_training_session(building, user, *, trainer=None, participants=None, category=None, floor=None, unit=None,
                             room_or_area="", scheduled_at=None, procedure_drawing=None) -> TrainingSession:
    session = TrainingSession.objects.create(
        building=building, floor=floor, unit=unit, room_or_area=room_or_area, category=category, trainer=trainer,
        scheduled_at=scheduled_at, procedure_drawing=procedure_drawing, created_by=user,
    )
    if participants:
        session.participants.set(participants)
    audit.log(AuditEvent.Action.OTHER, instance=session, actor=user, summary=f"Sesión de capacitación creada: {session}")
    return session


@transaction.atomic
def start_session(session: TrainingSession, user) -> TrainingSession:
    if session.actual_start is not None:
        raise TrainingError("Esta sesión ya fue iniciada.")
    session.actual_start = timezone.now()
    session.save()
    audit.log(AuditEvent.Action.OTHER, instance=session, actor=user, summary=f"Sesión de capacitación iniciada: {session}")
    return session


@transaction.atomic
def finish_session(session: TrainingSession, user, *, defects_discovered="", adjustment_demonstrated="",
                    remaining_actions="", used_digital_level=False, digital_level_reading="") -> TrainingSession:
    if session.actual_start is None:
        raise TrainingError("La sesión debe iniciarse antes de poder finalizarse.")
    if session.actual_finish is not None:
        raise TrainingError("Esta sesión ya fue finalizada.")
    session.actual_finish = timezone.now()
    session.defects_discovered = defects_discovered
    session.adjustment_demonstrated = adjustment_demonstrated
    session.remaining_actions = remaining_actions
    session.used_digital_level = used_digital_level
    session.digital_level_reading = digital_level_reading
    session.save()
    audit.log(AuditEvent.Action.OTHER, instance=session, actor=user, summary=f"Sesión de capacitación finalizada: {session}")
    return session


def add_checklist_item(session: TrainingSession, user, *, description, result, notes="") -> TrainingChecklistItem:
    item = TrainingChecklistItem.objects.create(session=session, description=description, result=result, notes=notes, created_by=user)
    audit.log(AuditEvent.Action.OTHER, instance=item, actor=user, summary=f"Ítem de checklist registrado: {description} ({result})")
    return item


def add_evidence(session: TrainingSession, user, *, document_type, title, uploaded_file, stage, caption=""):
    from .models import TrainingEvidence

    attachment, is_duplicate = audit.attach_evidence(session, user, document_type=document_type, title=title, uploaded_file=uploaded_file)
    evidence = TrainingEvidence.objects.create(session=session, document=attachment.document, stage=stage, caption=caption, created_by=user)
    return evidence, is_duplicate


@transaction.atomic
def acknowledge_participation(session: TrainingSession, participant, *, notes="") -> TrainingParticipantAcknowledgement:
    if not session.participants.filter(pk=participant.pk).exists():
        raise TrainingError("Este usuario no es un participante registrado de la sesión.")
    ack, _ = TrainingParticipantAcknowledgement.objects.get_or_create(
        session=session, participant=participant, defaults={"notes": notes, "created_by": participant},
    )
    audit.log(AuditEvent.Action.OTHER, instance=ack, actor=participant, summary=f"Participación reconocida: {participant} en {session}")
    return ack


@transaction.atomic
def supervisor_sign_off(session: TrainingSession, supervisor, *, notes="") -> TrainingSession:
    if not can_override_gates(supervisor):
        raise TrainingError("No tiene permiso para firmar como supervisor.")
    if session.actual_finish is None:
        raise TrainingError("La sesión debe finalizarse antes de la firma del supervisor.")
    session.supervisor = supervisor
    session.supervisor_signed_off_at = timezone.now()
    session.supervisor_notes = notes
    session.save()
    audit.log(AuditEvent.Action.OTHER, instance=session, actor=supervisor, summary=f"Firma de supervisor: {session}", reason=notes)
    return session


@transaction.atomic
def approve_as_reference_installation(session: TrainingSession, user, *, notes="") -> TrainingSession:
    if not can_override_gates(user):
        raise TrainingError("No tiene permiso para aprobar una instalación de referencia.")
    if session.supervisor_signed_off_at is None:
        raise TrainingError("La sesión debe tener firma de supervisor antes de aprobarse como referencia.")
    session.is_approved_reference_installation = True
    session.approved_by = user
    session.approved_at = timezone.now()
    if notes:
        session.supervisor_notes = f"{session.supervisor_notes}\n{notes}".strip()
    session.save()
    audit.log(AuditEvent.Action.OTHER, instance=session, actor=user, summary=f"Instalación de referencia aprobada: {session}")
    return session


@transaction.atomic
def create_issue_from_training(session: TrainingSession, user, *, title, description="", category=None):
    """Preserves the relationship via `FieldIssue.training_session` —
    never a copy-pasted, disconnected issue."""
    from apps.fieldissues.services import report_issue

    issue = report_issue(
        session.building, user, title=title, description=description, category=category,
        floor=session.floor, unit=session.unit, room_or_location=session.room_or_area,
        training_session=session,
    )
    audit.log(AuditEvent.Action.OTHER, instance=session, actor=user, summary=f"Incidencia creada desde sesión de capacitación: {issue.title}")
    return issue
