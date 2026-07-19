"""Apartment walkthroughs and corrective actions (one-shot release,
section 6).

Building-agnostic by construction — every function here only ever
requires a `building`; nothing branches on which family/building it
is, so the exact same code path already covers every configured active
building (all 8 Arena T1 buildings, Palmera, Sole 26, Sole, Sole PH,
Mare B) and any future one added purely through
`apps.projects.services.import_physical_property_master`.

A defect found on a `WalkthroughItem` becomes a real `FieldIssue`
(`create_issue_from_item`) — the entire assign/correct/verify/reject/
resubmit/reinspect lifecycle is `apps.fieldissues.services`'s, never
duplicated here. A walkthrough's own "delivery readiness" is computed
by reading that same `FieldIssue` status, never a second correction
tracker.
"""

from __future__ import annotations

import dataclasses

from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.workflow.services import can_override_gates

from .models import (
    Walkthrough,
    WalkthroughChecklistTemplateItem,
    WalkthroughItem,
    WalkthroughItemEvidence,
)


class WalkthroughError(ValueError):
    """Raised for any walkthrough/item precondition that isn't met."""


@transaction.atomic
def create_walkthrough(building, user, *, purpose, category=None, floor=None, unit=None, units=None, area=None,
                        inspector=None, participants=None, scheduled_date=None, drawing=None,
                        training_session=None, previous_walkthrough=None) -> Walkthrough:
    walkthrough = Walkthrough.objects.create(
        building=building, floor=floor, unit=unit, area=area, purpose=purpose, category=category,
        inspector=inspector, scheduled_date=scheduled_date, drawing=drawing, training_session=training_session,
        previous_walkthrough=previous_walkthrough, created_by=user,
    )
    if units:
        walkthrough.units.set(units)
    if participants:
        walkthrough.participants.set(participants)
    audit.log(AuditEvent.Action.OTHER, instance=walkthrough, actor=user, summary=f"Recorrido creado: {walkthrough}")
    return walkthrough


def populate_checklist_from_template(walkthrough: Walkthrough, user) -> list:
    """Bulk-creates one `WalkthroughItem` per configured template entry
    for the walkthrough's category (e.g. the 16 window/sliding-door
    checks) — a genuinely configurable seed, editable/removable
    afterward, never a hard-coded per-request branch."""
    if walkthrough.category is None:
        raise WalkthroughError("El recorrido no tiene categoría — no hay plantilla de checklist que aplicar.")
    template_items = WalkthroughChecklistTemplateItem.objects.filter(category=walkthrough.category).order_by("sort_order")
    created = []
    for template_item in template_items:
        item = WalkthroughItem.objects.create(
            walkthrough=walkthrough, unit=walkthrough.unit, room_or_location=template_item.description,
            created_by=user,
        )
        created.append(item)
    return created


def add_item(walkthrough: Walkthrough, user, *, room_or_location="", item=None, component_description="", unit=None) -> WalkthroughItem:
    return WalkthroughItem.objects.create(
        walkthrough=walkthrough, unit=unit or walkthrough.unit, room_or_location=room_or_location, item=item,
        component_description=component_description, created_by=user,
    )


@transaction.atomic
def record_item_result(walkthrough_item: WalkthroughItem, user, *, checklist_result, measurement=None,
                        measurement_unit="", digital_level_reading="", level_condition=None, plumb_condition=None,
                        square_condition=None, operational_test=None, condition_found="", adjustment_performed="",
                        condition_after_adjustment="", remaining_defect="", is_blocking_defect=False) -> WalkthroughItem:
    """`condition_found` and `condition_after_adjustment` are always
    both preserved — recording a new adjustment never overwrites the
    condition originally found."""
    walkthrough_item.checklist_result = checklist_result
    walkthrough_item.measurement = measurement
    walkthrough_item.measurement_unit = measurement_unit
    walkthrough_item.digital_level_reading = digital_level_reading
    if level_condition is not None:
        walkthrough_item.level_condition = level_condition
    if plumb_condition is not None:
        walkthrough_item.plumb_condition = plumb_condition
    if square_condition is not None:
        walkthrough_item.square_condition = square_condition
    if operational_test is not None:
        walkthrough_item.operational_test = operational_test
    if condition_found:
        walkthrough_item.condition_found = condition_found
    if adjustment_performed:
        walkthrough_item.adjustment_performed = adjustment_performed
    if condition_after_adjustment:
        walkthrough_item.condition_after_adjustment = condition_after_adjustment
    walkthrough_item.remaining_defect = remaining_defect
    walkthrough_item.is_blocking_defect = is_blocking_defect
    walkthrough_item.save()
    audit.log(
        AuditEvent.Action.OTHER, instance=walkthrough_item, actor=user,
        summary=f"Resultado de ítem registrado: {walkthrough_item} -> {checklist_result}",
    )
    return walkthrough_item


def add_item_evidence(walkthrough_item: WalkthroughItem, user, *, document_type, title, uploaded_file, stage, caption=""):
    attachment, is_duplicate = audit.attach_evidence(walkthrough_item, user, document_type=document_type, title=title, uploaded_file=uploaded_file)
    evidence = WalkthroughItemEvidence.objects.create(
        item=walkthrough_item, document=attachment.document, stage=stage, caption=caption, created_by=user,
    )
    return evidence, is_duplicate


@transaction.atomic
def create_issue_from_item(walkthrough_item: WalkthroughItem, user, *, title, description="", priority=None) -> "FieldIssue":  # noqa: F821
    from apps.fieldissues.models import FieldIssue
    from apps.fieldissues.services import report_issue

    kwargs = {"walkthrough_item": walkthrough_item}
    if priority:
        kwargs["priority"] = priority
    issue = report_issue(
        walkthrough_item.walkthrough.building, user, title=title, description=description,
        unit=walkthrough_item.unit or walkthrough_item.walkthrough.unit,
        floor=walkthrough_item.walkthrough.floor, room_or_location=walkthrough_item.room_or_location, **kwargs,
    )
    walkthrough_item.field_issue = issue
    walkthrough_item.save()
    audit.log(
        AuditEvent.Action.OTHER, instance=walkthrough_item, actor=user,
        summary=f"Incidencia correctiva creada desde recorrido: {issue.title}",
    )
    return issue


@transaction.atomic
def set_progress(walkthrough: Walkthrough, user, *, progress_status) -> Walkthrough:
    walkthrough.progress_status = progress_status
    walkthrough.save()
    audit.log(AuditEvent.Action.OTHER, instance=walkthrough, actor=user, summary=f"Progreso del recorrido: {progress_status}")
    return walkthrough


@dataclasses.dataclass
class DeliveryReadinessSummary:
    total_items: int
    passed_items: int
    conditional_items: int
    failed_items: int
    open_issues: int
    blocking_defects: int
    overdue_corrective_actions: int
    missing_evidence_items: int
    pending_verification_issues: int

    @property
    def is_blocked(self) -> bool:
        return self.blocking_defects > 0 or self.pending_verification_issues > 0

    def to_dict(self) -> dict:
        return dataclasses.asdict(self) | {"is_blocked": self.is_blocked}


def delivery_readiness_summary(walkthrough: Walkthrough) -> DeliveryReadinessSummary:
    from apps.fieldissues.models import FieldIssue

    items = walkthrough.items.all()
    today = timezone.now().date()
    open_statuses = [
        FieldIssue.Status.REPORTED, FieldIssue.Status.ASSIGNED, FieldIssue.Status.IN_PROGRESS,
        FieldIssue.Status.CORRECTION_COMPLETED, FieldIssue.Status.READY_FOR_VERIFICATION,
        FieldIssue.Status.RETURNED_FOR_CORRECTION, FieldIssue.Status.RESUBMITTED, FieldIssue.Status.REINSPECTION,
    ]
    pending_verification_statuses = [FieldIssue.Status.READY_FOR_VERIFICATION, FieldIssue.Status.REINSPECTION]
    linked_issue_ids = [item.field_issue_id for item in items if item.field_issue_id]
    open_issues = FieldIssue.objects.filter(pk__in=linked_issue_ids, status__in=open_statuses)

    return DeliveryReadinessSummary(
        total_items=items.count(),
        passed_items=items.filter(checklist_result=WalkthroughItem.Result.PASS).count(),
        conditional_items=items.filter(checklist_result=WalkthroughItem.Result.CONDITIONAL).count(),
        failed_items=items.filter(checklist_result=WalkthroughItem.Result.FAIL).count(),
        open_issues=open_issues.count(),
        blocking_defects=items.filter(is_blocking_defect=True).exclude(
            field_issue__status__in=[FieldIssue.Status.VERIFIED_CLOSED]
        ).count(),
        overdue_corrective_actions=open_issues.filter(due_date__lt=today).count(),
        missing_evidence_items=items.filter(checklist_result=WalkthroughItem.Result.FAIL, evidence__isnull=True).distinct().count(),
        pending_verification_issues=FieldIssue.objects.filter(pk__in=linked_issue_ids, status__in=pending_verification_statuses).count(),
    )


@transaction.atomic
def mark_delivery_decision(walkthrough: Walkthrough, user, *, decision, override_reason=None) -> Walkthrough:
    """An apartment cannot be marked ready for delivery while blocking
    defects or unverified required corrections remain, unless an
    authorized override is recorded with a written reason — the exact
    same override pattern (`can_override_gates` + `AuditEvent.Action
    .WAIVER`) used throughout this system."""
    from apps.walkthroughs.models import Walkthrough as _Walkthrough

    if decision == _Walkthrough.DeliveryDecision.READY:
        summary = delivery_readiness_summary(walkthrough)
        if summary.is_blocked:
            if not override_reason or not override_reason.strip():
                raise WalkthroughError(
                    f"No se puede marcar listo para entrega: {summary.blocking_defects} defecto(s) bloqueante(s), "
                    f"{summary.pending_verification_issues} incidencia(s) pendiente(s) de verificación."
                )
            if not can_override_gates(user):
                raise WalkthroughError("No tiene permiso para anular el bloqueo de listo-para-entrega.")
            walkthrough.delivery_override_reason = override_reason
            walkthrough.delivery_overridden_by = user
            audit.log(
                AuditEvent.Action.WAIVER, instance=walkthrough, actor=user,
                summary=f"Anulación autorizada de bloqueo de entrega: {walkthrough}", reason=override_reason,
                before_state=summary.to_dict(),
            )
    walkthrough.delivery_decision = decision
    walkthrough.delivery_decided_by = user
    walkthrough.delivery_decided_at = timezone.now()
    walkthrough.save()
    audit.log(AuditEvent.Action.OTHER, instance=walkthrough, actor=user, summary=f"Decisión de entrega: {walkthrough} -> {decision}")
    return walkthrough


def create_reinspection_walkthrough(previous_walkthrough: Walkthrough, user) -> Walkthrough:
    """The prior walkthrough and all its items/evidence are never
    edited — this creates a brand-new `Walkthrough` linked via
    `previous_walkthrough`, preserving every earlier attempt in full."""
    return create_walkthrough(
        previous_walkthrough.building, user, purpose=Walkthrough.Purpose.REINSPECTION,
        category=previous_walkthrough.category, floor=previous_walkthrough.floor, unit=previous_walkthrough.unit,
        area=previous_walkthrough.area, inspector=previous_walkthrough.inspector,
        previous_walkthrough=previous_walkthrough,
    )


def create_next_sequential_walkthrough(previous_walkthrough: Walkthrough, next_unit, user) -> Walkthrough:
    """Efficient sequential walkthroughs: moving apartment-by-apartment
    through a floor/building without re-entering building/category/
    purpose/inspector each time."""
    return create_walkthrough(
        previous_walkthrough.building, user, purpose=previous_walkthrough.purpose,
        category=previous_walkthrough.category, floor=previous_walkthrough.floor, unit=next_unit,
        inspector=previous_walkthrough.inspector,
    )
