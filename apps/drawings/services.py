"""Drawing and floor-plan register (spec section 2, one-shot release).

Reuses `apps.documents.Document`/`DocumentVersion` for the actual file
(SHA-256, duplicate detection) — a `Drawing` is metadata wrapped around
an existing Document, never a second file-storage mechanism. A new
revision is always a new `Drawing` row; the prior one is marked
`is_current=False`/`SUPERSEDED` but never edited or deleted, so any
existing FK elsewhere (order allocation, installation, walkthrough,
issue) that points at a specific `Drawing` keeps pointing at that exact
historical revision forever.
"""

from __future__ import annotations

from django.db import transaction

from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.workflow.services import can_override_gates

from .models import Drawing


class DrawingError(ValueError):
    """Raised for any drawing-register precondition that isn't met."""


@transaction.atomic
def register_drawing(source_document, user, *, project, drawing_type, title, discipline=Drawing.Discipline.OTHER,
                      drawing_number="", building_family=None, building=None, floor=None, unit=None,
                      issue_date=None, status=Drawing.Status.DRAFT, notes="") -> Drawing:
    drawing = Drawing.objects.create(
        project=project, building_family=building_family, building=building, floor=floor, unit=unit,
        drawing_type=drawing_type, discipline=discipline, title=title, drawing_number=drawing_number,
        source_document=source_document, issue_date=issue_date, status=status, notes=notes, created_by=user,
    )
    audit.log(AuditEvent.Action.DOCUMENT_UPLOAD, instance=drawing, actor=user, summary=f"Plano registrado: {drawing.title}")
    return drawing


@transaction.atomic
def approve_drawing(drawing: Drawing, user, *, approval_date=None) -> Drawing:
    """Requires the same senior-authorization permission every other
    approval gate in this system uses — never a hard-coded name."""
    if not can_override_gates(user):
        raise DrawingError("No tiene permiso para aprobar planos.")
    if drawing.status == Drawing.Status.SUPERSEDED:
        raise DrawingError("No se puede aprobar un plano ya reemplazado.")
    drawing.status = Drawing.Status.APPROVED
    drawing.approver = user
    from django.utils import timezone
    drawing.approval_date = approval_date or timezone.now().date()
    drawing.save()
    audit.log(AuditEvent.Action.OTHER, instance=drawing, actor=user, summary=f"Plano aprobado: {drawing.title}")
    return drawing


@transaction.atomic
def supersede_drawing(old_drawing: Drawing, user, *, new_source_document, title=None, notes="") -> Drawing:
    """Creates the new revision as its own row; the old row is never
    edited — only flagged. A drawing already marked SUPERSEDED cannot
    be superseded again (its replacement already exists)."""
    if old_drawing.status == Drawing.Status.SUPERSEDED:
        raise DrawingError("Este plano ya fue reemplazado.")
    new_drawing = Drawing.objects.create(
        project=old_drawing.project, building_family=old_drawing.building_family, building=old_drawing.building,
        floor=old_drawing.floor, unit=old_drawing.unit, drawing_type=old_drawing.drawing_type,
        discipline=old_drawing.discipline, title=title or old_drawing.title, drawing_number=old_drawing.drawing_number,
        source_document=new_source_document, revision=old_drawing.revision + 1, status=Drawing.Status.DRAFT,
        notes=notes, supersedes=old_drawing, created_by=user,
    )
    old_drawing.status = Drawing.Status.SUPERSEDED
    old_drawing.is_current = False
    old_drawing.save()
    audit.log(
        AuditEvent.Action.OTHER, instance=new_drawing, actor=user,
        summary=f"Plano reemplazado: {old_drawing.title} rev.{old_drawing.revision} -> rev.{new_drawing.revision}",
    )
    return new_drawing
