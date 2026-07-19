"""Handoff lifecycle service layer.

Every stage transition in the system goes through the functions in this
module. Views never flip `Handoff.status` (or a target's own `.status`)
directly — that is exactly the behavior core principle 4.6/spec section 8
forbids ("a stage is not transferred merely by changing a status").

All mutating functions here are wrapped in a transaction with
`select_for_update()` on the `Handoff` row, so two concurrent requests
(a duplicate submit from a repeated click, or two people accepting the
same handoff at once) serialize on that row lock: the second request
always sees the already-updated status and raises a clear
`InvalidTransitionError` instead of creating a second decision.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import ResponsibilityAssignment, UserProjectAccess, UserRole
from apps.audit import services as audit
from apps.audit.models import AuditEvent, Comment

from .gates import GateResult, evaluate_gate
from .models import GateOverride, Handoff, HandoffDecision, HandoffEvidence, HandoffStatus

OPEN_HANDOFF_STATUSES = [
    HandoffStatus.NOT_READY,
    HandoffStatus.READY_FOR_SUBMISSION,
    HandoffStatus.SUBMITTED,
]


class GateBlockedError(Exception):
    """Raised by submit_handoff when the gate is blocked and no valid
    override was supplied. Carries the full GateResult so the caller can
    show exactly what's missing."""

    def __init__(self, result: GateResult):
        self.result = result
        super().__init__("El gate está bloqueado y no se proporcionó una anulación autorizada.")


class InvalidTransitionError(Exception):
    """Raised when a transition is attempted from a status that does not
    allow it (e.g. accepting a handoff that was already accepted/rejected —
    this is what makes duplicate/concurrent decisions safe)."""


class PermissionDeniedError(Exception):
    """Raised for any authorization failure — always at the service layer,
    never only hidden behind a UI element."""


# ---------------------------------------------------------------------------
# Target resolution helpers
# ---------------------------------------------------------------------------


def get_target(handoff: Handoff):
    model_class = handoff.content_type.model_class()
    return model_class.objects.get(pk=handoff.object_id)


def resolve_project(target):
    """Best-effort project resolution across the target types this
    milestone's gates operate on. Returns None for org-scoped-only
    targets (e.g. Shipment, which can legitimately carry cargo for
    several projects at once — see docs/OFFICIAL_VS_OPERATIONAL_MANIFEST_ANALYSIS.md)."""
    project = getattr(target, "project", None)
    if project is not None:
        return project
    dispatch = getattr(target, "dispatch", None)  # Delivery -> Dispatch -> PickList -> MaterialRequest
    if dispatch is not None:
        request = getattr(dispatch.pick_list, "request", None)
        if request is not None:
            return request.project
    project_receipt = getattr(target, "project_receipt", None)  # InstallationRecord -> ProjectReceipt -> Delivery
    if project_receipt is not None:
        return resolve_project(project_receipt.delivery)
    installation = getattr(target, "installation", None)  # InspectionRecord -> InstallationRecord
    if installation is not None:
        return resolve_project(installation)
    inspection = getattr(target, "inspection", None)  # PunchListItem -> InspectionRecord
    if inspection is not None:
        return resolve_project(inspection)
    unit = getattr(target, "unit", None)  # FieldIssue/Walkthrough/TrainingSession -> Unit -> Building -> Project
    if unit is not None:
        return resolve_project(unit)
    building = getattr(target, "building", None)  # Unit/Floor/Area -> Building -> Project
    if building is not None:
        return resolve_project(building)
    return None


def resolve_organization(target):
    organization = getattr(target, "organization", None)
    if organization is not None:
        return organization
    shipment = getattr(target, "shipment", None)  # Container -> Shipment
    if shipment is not None:
        return resolve_organization(shipment)
    purchase_order = getattr(target, "purchase_order", None)  # PurchaseOrderLine -> PurchaseOrder
    if purchase_order is not None:
        return resolve_organization(purchase_order)
    walkthrough = getattr(target, "walkthrough", None)  # WalkthroughItem -> Walkthrough -> Building -> Project
    if walkthrough is not None:
        return resolve_organization(walkthrough)
    plan_template = getattr(target, "template", None)  # PlanZone -> UnitPlanTemplate
    if plan_template is not None:
        return resolve_organization(plan_template)
    project = resolve_project(target)
    if project is not None:
        return project.organization
    return None


# ---------------------------------------------------------------------------
# Permission helpers — role/department/project based, never a hard-coded name
# ---------------------------------------------------------------------------


def _user_in_department(user, department) -> bool:
    if user is None or department is None:
        return False
    return UserRole.objects.filter(user=user, department=department, is_active=True).exists()


def can_accept_handoff(user, handoff: Handoff) -> bool:
    if user is None or not user.is_authenticated:
        return False
    if not can_view_handoff(user, handoff):
        return False
    if handoff.to_user_id and handoff.to_user_id == user.id:
        return True
    if handoff.to_department_id and _user_in_department(user, handoff.to_department):
        required_role = handoff.gate_definition.required_role_to_accept if handoff.gate_definition else None
        if required_role is None:
            return True
        return UserRole.objects.filter(user=user, role=required_role, is_active=True).exists()
    return False


def can_override_gates(user) -> bool:
    if user is None or not user.is_authenticated:
        return False
    return UserRole.objects.filter(user=user, role__can_override_gates=True, is_active=True).exists()


def can_view_handoff(user, handoff: Handoff) -> bool:
    """Organization scoping (same pattern as every other view in this
    codebase) plus project-level isolation via UserProjectAccess for
    project-scoped targets, with a management-role bypass consistent with
    the executive-oversight responsibility already defined for
    Harrison/María Luisa in the governing spec."""
    profile = getattr(user, "profile", None)
    if profile is None:
        return False
    if handoff.organization_id and profile.organization_id != handoff.organization_id:
        return False
    if handoff.project_id:
        if not user_can_access_project(user, handoff.project):
            return False
    return True


def user_can_access_project(user, project) -> bool:
    """Same project-isolation rule `can_view_handoff` applies to a Handoff's
    `project_id` — factored out so it can be applied directly to a target
    (Delivery, InstallationRecord, MaterialRequest, ...) before any handoff
    necessarily exists yet."""
    if project is None:
        return True
    if UserProjectAccess.objects.filter(user=user, project=project).exists():
        return True
    return UserRole.objects.filter(user=user, role__is_management=True, is_active=True).exists()


def can_view_target(user, target) -> bool:
    """Organization + project isolation applied directly to a delivery/
    installation-chain target, mirroring `can_view_handoff` — used by the
    Delivery/InstallationRecord/InspectionRecord views (apps.requests.views)
    so cross-project access is denied the same way whether or not a
    handoff has been created yet for that target."""
    profile = getattr(user, "profile", None)
    if profile is None:
        return False
    organization = resolve_organization(target)
    if organization is not None and profile.organization_id != organization.id:
        return False
    return user_can_access_project(user, resolve_project(target))


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@transaction.atomic
def create_handoff(target, gate_definition, from_user, *, to_department=None, to_user=None, comment=""):
    """Idempotent: a repeated call for the same (target, gate) while one
    is already open (not_ready/ready_for_submission/submitted) returns the
    existing row rather than creating a duplicate — this is what makes a
    double-click or a page refresh on a "create handoff" action safe, both
    via an app-level pre-check and the DB-level unique constraint as a
    race-safe backstop."""
    content_type = ContentType.objects.get_for_model(target)

    existing = (
        Handoff.objects.select_for_update()
        .filter(
            content_type=content_type,
            object_id=target.pk,
            gate_definition=gate_definition,
            status__in=OPEN_HANDOFF_STATUSES,
        )
        .first()
    )
    if existing is not None:
        return existing

    organization = resolve_organization(target)
    project = resolve_project(target)

    try:
        handoff = Handoff.objects.create(
            content_type=content_type,
            object_id=target.pk,
            organization=organization,
            project=project,
            gate_definition=gate_definition,
            from_department=gate_definition.from_department,
            from_user=from_user,
            to_department=to_department or gate_definition.to_department,
            to_user=to_user,
            status=HandoffStatus.NOT_READY,
            created_by=from_user,
        )
    except IntegrityError:
        # Lost a race with a concurrent create for the same target+gate.
        return Handoff.objects.get(
            content_type=content_type, object_id=target.pk, gate_definition=gate_definition,
            status__in=OPEN_HANDOFF_STATUSES,
        )

    result = evaluate_gate(gate_definition, target)
    handoff.readiness_ready = result.ready
    handoff.readiness_snapshot = result.to_dict()
    handoff.status = HandoffStatus.READY_FOR_SUBMISSION if result.ready else HandoffStatus.NOT_READY
    handoff.save()

    if comment:
        add_comment(handoff, from_user, comment)

    audit.log(AuditEvent.Action.HANDOFF, instance=handoff, actor=from_user, summary=f"Entrega creada: {handoff}")
    return handoff


@transaction.atomic
def submit_handoff(handoff: Handoff, user, *, override_reason: str | None = None) -> Handoff:
    handoff = Handoff.objects.select_for_update().get(pk=handoff.pk)

    if handoff.status not in (HandoffStatus.NOT_READY, HandoffStatus.READY_FOR_SUBMISSION):
        raise InvalidTransitionError(
            f"Esta entrega ya fue {handoff.get_status_display()}; no se puede enviar de nuevo."
        )
    if not (user == handoff.from_user or _user_in_department(user, handoff.from_department)):
        raise PermissionDeniedError("No tiene permiso para enviar esta entrega.")

    target = get_target(handoff)
    result = evaluate_gate(handoff.gate_definition, target) if handoff.gate_definition else GateResult(True, "ready")

    if result.evidence_requirements and not handoff.evidence.exists():
        result.unmet_requirements = [*result.unmet_requirements, "Evidencia requerida no adjunta a la entrega."]
        result.ready = False
        result.severity = "blocked"

    handoff.readiness_ready = result.ready
    handoff.readiness_snapshot = result.to_dict()

    if not result.ready:
        if not override_reason or not override_reason.strip():
            handoff.status = HandoffStatus.NOT_READY
            handoff.save()
            raise GateBlockedError(result)

        if not can_override_gates(user):
            raise PermissionDeniedError("No tiene permiso para anular un gate bloqueado.")

        override = GateOverride.objects.create(
            gate_definition=handoff.gate_definition,
            content_type=handoff.content_type,
            object_id=handoff.object_id,
            handoff=handoff,
            overridden_by=user,
            reason=override_reason,
            before_state=result.to_dict(),
            after_state={},
            created_by=user,
        )
        handoff.status = HandoffStatus.SUBMITTED
        handoff.submitted_at = timezone.now()
        handoff.save()
        override.after_state = {"handoff_status": handoff.status, "submitted_at": handoff.submitted_at.isoformat()}
        override.save(update_fields=["after_state"])
        audit.log(
            AuditEvent.Action.WAIVER, instance=handoff, actor=user,
            summary=f"Gate anulado para enviar: {handoff.gate_definition}", reason=override_reason,
        )
        return handoff

    handoff.status = HandoffStatus.SUBMITTED
    handoff.submitted_at = timezone.now()
    handoff.save()
    audit.log(AuditEvent.Action.HANDOFF, instance=handoff, actor=user, summary=f"Entrega enviada: {handoff}")
    return handoff


# Shipment.status only ever advances through an accepted handoff — never a
# direct field edit anywhere else in the codebase (spec section 8/9).
def _apply_status_transition(target, gate_code, user):
    from apps.shipments.models import Shipment

    if not isinstance(target, Shipment):
        return
    mapping = {
        "logistics_to_receiving": Shipment.Status.RELEASED_TO_RECEIVING,
        "receiving_to_warehouse": Shipment.Status.WAREHOUSE_CUSTODY,
    }
    new_status = mapping.get(gate_code)
    if new_status:
        target.status = new_status
        target.save(update_fields=["status"])


def _transfer_ownership(handoff: Handoff, accepting_user):
    ResponsibilityAssignment.objects.filter(
        content_type=handoff.content_type, object_id=handoff.object_id, is_open=True
    ).update(is_open=False)
    ResponsibilityAssignment.objects.create(
        content_type=handoff.content_type,
        object_id=handoff.object_id,
        department=handoff.to_department,
        primary_user=accepting_user,
        is_open=True,
        created_by=accepting_user,
    )


@transaction.atomic
def accept_handoff(handoff: Handoff, user) -> Handoff:
    handoff = Handoff.objects.select_for_update().get(pk=handoff.pk)
    if handoff.status != HandoffStatus.SUBMITTED:
        raise InvalidTransitionError(
            f"Esta entrega ya fue {handoff.get_status_display()}; no se puede aceptar de nuevo."
        )
    if not can_accept_handoff(user, handoff):
        raise PermissionDeniedError("No tiene el rol, departamento o asignación necesarios para aceptar esta entrega.")

    handoff.status = HandoffStatus.ACCEPTED
    handoff.decided_at = timezone.now()
    handoff.save()
    HandoffDecision.objects.create(handoff=handoff, decided_by=user, decision="accepted", created_by=user)

    if handoff.to_department_id:
        _transfer_ownership(handoff, user)

    if handoff.gate_definition_id:
        target = get_target(handoff)
        _apply_status_transition(target, handoff.gate_definition.code, user)

    audit.log(AuditEvent.Action.HANDOFF, instance=handoff, actor=user, summary=f"Entrega aceptada: {handoff}")
    return handoff


@transaction.atomic
def reject_handoff(handoff: Handoff, user, reason: str) -> Handoff:
    if not reason or not reason.strip():
        raise ValueError("Se requiere un motivo para rechazar la entrega.")
    handoff = Handoff.objects.select_for_update().get(pk=handoff.pk)
    if handoff.status != HandoffStatus.SUBMITTED:
        raise InvalidTransitionError(
            f"Esta entrega ya fue {handoff.get_status_display()}; no se puede rechazar de nuevo."
        )
    if not can_accept_handoff(user, handoff):
        raise PermissionDeniedError("No tiene el rol, departamento o asignación necesarios para rechazar esta entrega.")

    handoff.status = HandoffStatus.REJECTED
    handoff.decided_at = timezone.now()
    handoff.rejection_or_correction_reason = reason
    handoff.save()
    HandoffDecision.objects.create(handoff=handoff, decided_by=user, decision="rejected", comment=reason, created_by=user)
    audit.log(AuditEvent.Action.HANDOFF, instance=handoff, actor=user, summary=f"Entrega rechazada: {handoff}", reason=reason)
    return handoff


@transaction.atomic
def return_for_correction(handoff: Handoff, user, reason: str) -> Handoff:
    if not reason or not reason.strip():
        raise ValueError("Se requiere un motivo para devolver la entrega para corrección.")
    handoff = Handoff.objects.select_for_update().get(pk=handoff.pk)
    if handoff.status != HandoffStatus.SUBMITTED:
        raise InvalidTransitionError(
            f"Esta entrega ya fue {handoff.get_status_display()}; no se puede devolver de nuevo."
        )
    if not can_accept_handoff(user, handoff):
        raise PermissionDeniedError("No tiene el rol, departamento o asignación necesarios para devolver esta entrega.")

    handoff.status = HandoffStatus.RETURNED_FOR_CORRECTION
    handoff.decided_at = timezone.now()
    handoff.rejection_or_correction_reason = reason
    handoff.save()
    HandoffDecision.objects.create(handoff=handoff, decided_by=user, decision="returned", comment=reason, created_by=user)
    audit.log(AuditEvent.Action.HANDOFF, instance=handoff, actor=user, summary=f"Entrega devuelta para corrección: {handoff}", reason=reason)
    return handoff


@transaction.atomic
def resubmit_handoff(old_handoff: Handoff, user, comment: str = "") -> Handoff:
    """Corrected resubmission: creates a NEW Handoff row (never edits the
    rejected/returned one) and marks the old one SUPERSEDED, preserving its
    full decision history untouched (core principle 4.4)."""
    old_handoff = Handoff.objects.select_for_update().get(pk=old_handoff.pk)
    if old_handoff.status not in (HandoffStatus.REJECTED, HandoffStatus.RETURNED_FOR_CORRECTION):
        raise InvalidTransitionError("Solo se puede reenviar una entrega rechazada o devuelta para corrección.")

    target = get_target(old_handoff)

    new_handoff = Handoff.objects.create(
        content_type=old_handoff.content_type,
        object_id=old_handoff.object_id,
        organization=old_handoff.organization,
        project=old_handoff.project,
        gate_definition=old_handoff.gate_definition,
        from_department=old_handoff.from_department,
        from_user=user,
        to_department=old_handoff.to_department,
        to_user=old_handoff.to_user,
        version=old_handoff.version + 1,
        supersedes=old_handoff,
        status=HandoffStatus.NOT_READY,
        created_by=user,
    )
    old_handoff.status = HandoffStatus.SUPERSEDED
    old_handoff.save()

    result = (
        evaluate_gate(new_handoff.gate_definition, target)
        if new_handoff.gate_definition
        else GateResult(True, "ready")
    )
    new_handoff.readiness_ready = result.ready
    new_handoff.readiness_snapshot = result.to_dict()
    new_handoff.status = HandoffStatus.READY_FOR_SUBMISSION if result.ready else HandoffStatus.NOT_READY
    new_handoff.save()

    if comment:
        add_comment(new_handoff, user, comment)

    audit.log(
        AuditEvent.Action.HANDOFF, instance=new_handoff, actor=user,
        summary=f"Reenvío corregido de la entrega {old_handoff.pk}",
    )
    return new_handoff


# ---------------------------------------------------------------------------
# Evidence and comments
# ---------------------------------------------------------------------------


def add_comment(handoff: Handoff, user, body: str) -> Comment:
    return Comment.objects.create(
        content_type=ContentType.objects.get_for_model(Handoff),
        object_id=handoff.pk,
        author=user,
        body=body,
        created_by=user,
    )


def list_comments(handoff: Handoff):
    return Comment.objects.filter(
        content_type=ContentType.objects.get_for_model(Handoff), object_id=handoff.pk
    ).order_by("created_at")


def add_evidence(handoff: Handoff, document, user) -> HandoffEvidence:
    return HandoffEvidence.objects.create(handoff=handoff, document=document, created_by=user)


def is_overdue(handoff: Handoff) -> bool:
    """Reuses the existing ServiceLevelTarget (tied to WorkflowStage) so
    overdue tracking needs no new SLA model — a handoff is overdue if it
    has been sitting SUBMITTED longer than its destination stage's
    configured target."""
    if handoff.status != HandoffStatus.SUBMITTED or not handoff.submitted_at:
        return False
    if not handoff.gate_definition_id or not handoff.gate_definition.to_stage_id:
        return False
    sla = handoff.gate_definition.to_stage.sla_targets.first()
    if sla is None:
        return False
    return timezone.now() - handoff.submitted_at > timedelta(hours=sla.target_hours)


def handoffs_awaiting_user(user):
    """Submitted handoffs this user can accept — directly (to_user) or via
    department membership. Used by both the dashboard card count and the
    inbox's "para mi aceptación" view, so the two never disagree."""
    from django.db.models import Q

    department_ids = set(
        UserRole.objects.filter(user=user, is_active=True).values_list("department_id", flat=True)
    )
    return Handoff.objects.filter(status=HandoffStatus.SUBMITTED).filter(
        Q(to_user=user) | Q(to_department_id__in=department_ids)
    )


def overdue_handoffs_for_user(user):
    return [h for h in handoffs_awaiting_user(user) if is_overdue(h)]
