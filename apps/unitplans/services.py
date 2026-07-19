"""Interactive Apartment Plan / Room-Zone layer services (one-shot
release addendum). Templates are reusable across every unit that shares
family/unit-type-letter/floor-variant — never one row per apartment.
Reassigning a unit to a new template revision, or superseding a zone's
shape, never edits or deletes the prior row (versioned-immutable-row
pattern, matching Drawing/OrderLineAllocation/EvidenceClassification
elsewhere in this release).
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.workflow.services import can_override_gates

from .models import PlanZone, UnitPlanAssignment, UnitPlanTemplate


class UnitPlanError(ValueError):
    """Raised for any unit-plan precondition that isn't met."""


def template_code_for(family_code: str, unit_type_letter: str, floor_variant: str) -> str:
    letter_slug = slugify(unit_type_letter.replace("(", "").replace(")", "")) or "unit"
    return f"{family_code}-{letter_slug}-{floor_variant}"[:80]


@transaction.atomic
def create_template(organization, family, *, unit_type_letter, floor_variant, status=UnitPlanTemplate.Status.MISSING_SOURCE,
                     source_drawing=None, source_page=None, derived_plan_document=None, uncertainty_notes="",
                     is_mirrored=False, orientation="", user=None, code=None) -> UnitPlanTemplate:
    code = code or template_code_for(family.code, unit_type_letter, floor_variant)
    return UnitPlanTemplate.objects.create(
        organization=organization, family=family, code=code, unit_type_letter=unit_type_letter,
        floor_variant=floor_variant, status=status, source_drawing=source_drawing, source_page=source_page,
        derived_plan_document=derived_plan_document, uncertainty_notes=uncertainty_notes, is_mirrored=is_mirrored,
        orientation=orientation, created_by=user,
    )


@transaction.atomic
def add_zone(template: UnitPlanTemplate, *, zone_code, name_es, zone_type, coordinates, name_en="",
             custom_type_label="", shape_type=PlanZone.ShapeType.RECT, duplex_floor="", display_order=0,
             validation_state=PlanZone.ValidationState.DRAFT, source_confidence="low", user=None) -> PlanZone:
    if not can_override_gates(user):
        raise UnitPlanError("No tiene permiso para editar el mapeo de planos de unidad.")
    return PlanZone.objects.create(
        template=template, zone_code=zone_code, name_es=name_es, name_en=name_en, zone_type=zone_type,
        custom_type_label=custom_type_label, shape_type=shape_type, coordinates=coordinates,
        duplex_floor=duplex_floor, display_order=display_order, validation_state=validation_state,
        source_confidence=source_confidence, created_by=user,
    )


@transaction.atomic
def supersede_zone(old_zone: PlanZone, user, *, name_es=None, name_en=None, zone_type=None, custom_type_label=None,
                    coordinates=None, shape_type=None, duplex_floor=None, display_order=None,
                    source_confidence=None) -> PlanZone:
    """Editing a zone's shape/name/type never mutates the row in
    place — a FieldIssue/WalkthroughItem created earlier from this exact
    zone must keep pointing at the shape it was actually created
    against. A new zone row is created (`supersedes`), the old one is
    marked inactive."""
    if not can_override_gates(user):
        raise UnitPlanError("No tiene permiso para editar el mapeo de planos de unidad.")
    new_zone = PlanZone.objects.create(
        template=old_zone.template, zone_code=old_zone.zone_code,
        name_es=name_es if name_es is not None else old_zone.name_es,
        name_en=name_en if name_en is not None else old_zone.name_en,
        zone_type=zone_type if zone_type is not None else old_zone.zone_type,
        custom_type_label=custom_type_label if custom_type_label is not None else old_zone.custom_type_label,
        shape_type=shape_type if shape_type is not None else old_zone.shape_type,
        coordinates=coordinates if coordinates is not None else old_zone.coordinates,
        duplex_floor=duplex_floor if duplex_floor is not None else old_zone.duplex_floor,
        display_order=display_order if display_order is not None else old_zone.display_order,
        validation_state=PlanZone.ValidationState.NEEDS_REVIEW,
        source_confidence=source_confidence if source_confidence is not None else old_zone.source_confidence,
        supersedes=old_zone, created_by=user,
    )
    old_zone.is_active = False
    old_zone.save()
    return new_zone


@transaction.atomic
def validate_zone(zone: PlanZone, user) -> PlanZone:
    if not can_override_gates(user):
        raise UnitPlanError("No tiene permiso para validar zonas del plano.")
    zone.validation_state = PlanZone.ValidationState.VALIDATED
    zone.verifier = user
    zone.verified_at = timezone.now()
    zone.save()
    return zone


@transaction.atomic
def approve_template(template: UnitPlanTemplate, user) -> UnitPlanTemplate:
    if not can_override_gates(user):
        raise UnitPlanError("No tiene permiso para aprobar plantillas de plano para uso operativo.")
    if template.status == UnitPlanTemplate.Status.MISSING_SOURCE:
        raise UnitPlanError("No se puede aprobar una plantilla sin fuente (Missing Source).")
    template.status = UnitPlanTemplate.Status.APPROVED
    template.verifier = user
    template.verified_at = timezone.now()
    template.save()
    return template


@transaction.atomic
def supersede_template(old_template: UnitPlanTemplate, user, *, source_drawing=None, source_page=None,
                        derived_plan_document=None, uncertainty_notes="", copy_zones=True) -> UnitPlanTemplate:
    """A later plan revision (e.g. an executed/as-built drawing)
    creates a NEW template row — the old one is marked superseded, never
    edited or deleted. Historical FieldIssue/WalkthroughItem rows that
    reference the old template keep pointing at it forever."""
    if not can_override_gates(user):
        raise UnitPlanError("No tiene permiso para reemplazar plantillas de plano de unidad.")
    # Old row must stop being "current" before the new one is inserted —
    # both rows briefly existing as is_current=True would violate the
    # one-current-row-per-code constraint.
    old_template.status = UnitPlanTemplate.Status.SUPERSEDED
    old_template.is_current = False
    old_template.save()

    new_template = UnitPlanTemplate.objects.create(
        organization=old_template.organization, family=old_template.family, code=old_template.code,
        unit_type_letter=old_template.unit_type_letter, floor_variant=old_template.floor_variant,
        is_mirrored=old_template.is_mirrored, orientation=old_template.orientation,
        source_drawing=source_drawing or old_template.source_drawing, source_page=source_page or old_template.source_page,
        derived_plan_document=derived_plan_document or old_template.derived_plan_document,
        uncertainty_notes=uncertainty_notes, status=UnitPlanTemplate.Status.NEEDS_REVIEW,
        supersedes=old_template, created_by=user,
    )

    if copy_zones:
        for old_zone in old_template.zones.filter(is_active=True):
            new_zone = PlanZone.objects.create(
                template=new_template, zone_code=old_zone.zone_code, name_es=old_zone.name_es, name_en=old_zone.name_en,
                zone_type=old_zone.zone_type, custom_type_label=old_zone.custom_type_label, shape_type=old_zone.shape_type,
                coordinates=old_zone.coordinates, duplex_floor=old_zone.duplex_floor, display_order=old_zone.display_order,
                validation_state=PlanZone.ValidationState.NEEDS_REVIEW, source_confidence=old_zone.source_confidence,
                supersedes=old_zone, created_by=user,
            )
            old_zone.is_active = False
            old_zone.save()

    # Every unit currently assigned to the old template moves forward to
    # this new revision automatically — it's the same family/letter/
    # floor-variant slot, just an improved plan. This never touches any
    # FieldIssue/WalkthroughItem/InstallationRecord/InspectionRecord
    # already created — those keep their own direct FK to the old
    # template/zone forever.
    assignments_to_move = list(UnitPlanAssignment.objects.filter(template=old_template, is_current=True).select_related("unit"))
    for assignment in assignments_to_move:
        assign_unit_to_template(assignment.unit, new_template, user)
    return new_template


@transaction.atomic
def assign_unit_to_template(unit, template: UnitPlanTemplate, user=None) -> UnitPlanAssignment:
    """Reassigning a unit to a (possibly new) template never edits the
    prior assignment — it creates a new current one and marks the old
    row `is_current=False`, linked via `supersedes`."""
    previous = UnitPlanAssignment.objects.filter(unit=unit, is_current=True).first()
    if previous is not None:
        previous.is_current = False
        previous.save()
    return UnitPlanAssignment.objects.create(
        unit=unit, template=template, is_current=True, assigned_by=user,
        supersedes=previous, created_by=user,
    )


def effective_template_for_unit(unit) -> UnitPlanTemplate | None:
    assignment = UnitPlanAssignment.objects.filter(unit=unit, is_current=True).select_related("template").first()
    return assignment.template if assignment else None


def zone_open_issue_count(zone: PlanZone) -> int:
    from apps.fieldissues.models import FieldIssue

    return FieldIssue.objects.filter(plan_zone=zone).exclude(status=FieldIssue.Status.VERIFIED_CLOSED).count()


def review_queue(organization):
    """Every unit-plan item that still needs human attention: templates
    in Draft/Needs Review/Missing Source, zones in Draft/Needs Review,
    and physical units with no effective template assigned at all."""
    from apps.projects.models import Unit

    templates = UnitPlanTemplate.objects.filter(
        organization=organization, is_current=True,
    ).exclude(status=UnitPlanTemplate.Status.APPROVED).select_related("family")
    zones = PlanZone.objects.filter(
        template__organization=organization, is_active=True,
    ).exclude(validation_state=PlanZone.ValidationState.VALIDATED).select_related("template")
    unmapped_units = Unit.objects.filter(
        building__project__organization=organization,
    ).exclude(plan_assignments__is_current=True).select_related("building", "floor")
    return {"templates": templates, "zones": zones, "unmapped_units": unmapped_units}
