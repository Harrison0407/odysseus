"""Seeds the initial Unit Plan Template catalog (one-shot release
addendum — Interactive Apartment Plan / Room-Zone layer).

For every already-imported physical `Unit`, derives the
family/unit-type-letter/floor-variant slot it belongs to and ensures a
`UnitPlanTemplate` exists for that slot (idempotent — re-running this
command creates no duplicates), then assigns the unit to it via
`UnitPlanAssignment`.

Only PALMERA's 4 letter templates (A/B/C/D) have a real per-unit-type
architectural source (imports/buildings/PALMERA - PLANOS 13.11.2025.pdf,
sheets H-05 through H-08 — confirmed by direct visual inspection) and a
matching real APT-number -> letter occupancy table (sheet H-09,
cross-checked against apps.projects.building_source_data.palmera_rows()
and found consistent). Those 4 templates are seeded with a real derived
crop image (checksum-tracked via the normal Document/DocumentVersion
mechanism) and AI-proposed room zones — Draft by default, never
presented as architect-approved, since the source itself is stamped
"PLANOS AUN EN PROCESO" (plans still in process).

Every other family (ARENA T1, MARE B, SOLE, SOLE PH, SOLE 26) has no
per-unit-type floor-plan drawing in the supplied source material — only
a site-plan legend and a unit-typology spreadsheet, neither of which
shows room layouts. Per the release's explicit instruction, no room
boundary is invented for these: their template slots are created as
Missing Source, ready to receive a real derived plan later through the
admin mapping tool without any code change.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Organization
from apps.core.storage import document_storage
from apps.documents.models import Document, DocumentType, DocumentVersion
from apps.drawings.models import Drawing
from apps.projects.models import Unit
from apps.unitplans import services
from apps.unitplans.models import PlanZone, UnitPlanTemplate

DERIVED_PLANS_DIR = settings.BASE_DIR / "imports" / "buildings" / "derived_plans"

# (letter, source_page, crop_filename, zones) — zones as
# (zone_code, name_es, zone_type, (x0,y0,x1,y1))
# Coordinates are relative (0-1) bounding boxes read directly off the
# real cropped furnished-plan sub-view for each type (H-05/06/07/08),
# rectangle-approximated (shape_type=rect) — never pixel-perfect
# polygons — pending human refinement via the admin mapping tool
# (validation_state=needs_review, source_confidence=low).
PALMERA_TEMPLATES = [
    {
        "letter": "A", "page": 5, "file": "palmera_tipo_a.jpg",
        "notes": 'Hoja H-05 "APARTAMENTO TIPO A" — estudio de hotel. Zonas propuestas por IA a partir '
        "del plano amueblado (sub-vista 1); pendiente de validación humana.",
        "zones": [
            ("kitchen", "Cocina", PlanZone.ZoneType.KITCHEN, (0.174, 0.055, 0.301, 0.318)),
            ("bathroom", "Baño", PlanZone.ZoneType.BATHROOM, (0.532, 0.132, 0.752, 0.318)),
            ("closet", "Closet", PlanZone.ZoneType.CLOSET, (0.428, 0.143, 0.532, 0.318)),
            ("living_room", "Sala", PlanZone.ZoneType.LIVING_ROOM, (0.174, 0.351, 0.752, 0.515)),
            ("primary_bedroom", "Habitación", PlanZone.ZoneType.PRIMARY_BEDROOM, (0.174, 0.548, 0.752, 0.775)),
            ("balcony", "Balcón", PlanZone.ZoneType.BALCONY, (0.174, 0.775, 0.752, 0.899)),
        ],
    },
    {
        "letter": "B", "page": 6, "file": "palmera_tipo_b.jpg",
        "notes": 'Hoja H-06 "APARTAMENTO TIPO B" — 2 habitaciones. Zonas propuestas por IA a partir '
        "del plano de nivel 2 (sub-vista 1); pendiente de validación humana. Planta rotada en la "
        "hoja fuente — límites de zona son aproximados.",
        "zones": [
            ("closet", "Closet entrada", PlanZone.ZoneType.CLOSET, (0.165, 0.168, 0.265, 0.380)),
            ("kitchen", "Cocina", PlanZone.ZoneType.KITCHEN, (0.149, 0.380, 0.284, 0.599)),
            ("living_room", "Sala/comedor", PlanZone.ZoneType.LIVING_ROOM, (0.284, 0.110, 0.661, 0.694)),
            ("bathroom", "Baño", PlanZone.ZoneType.BATHROOM, (0.463, 0.278, 0.628, 0.475)),
            ("secondary_bedroom", "Habitación secundaria", PlanZone.ZoneType.SECONDARY_BEDROOM, (0.628, 0.439, 0.926, 0.694)),
            ("primary_bedroom", "Habitación principal", PlanZone.ZoneType.PRIMARY_BEDROOM, (0.661, 0.073, 0.993, 0.475)),
        ],
    },
    {
        "letter": "C", "page": 7, "file": "palmera_tipo_c.jpg",
        "notes": 'Hoja H-07 "APARTAMENTO TIPO C" — 1 habitación. Zonas propuestas por IA; pendiente de '
        'validación humana. La hoja fuente anota "M2 DE BALCON VARIA DEPENDIENDO DE UNIDAD" — el '
        "área de balcón varía por unidad y no debe asumirse fija.",
        "zones": [
            ("balcony", "Balcón", PlanZone.ZoneType.BALCONY, (0.303, 0.0, 0.888, 0.26)),
            ("living_room", "Sala/comedor", PlanZone.ZoneType.LIVING_ROOM, (0.303, 0.26, 0.888, 0.525)),
            ("kitchen", "Cocina", PlanZone.ZoneType.KITCHEN, (0.607, 0.525, 0.888, 0.61)),
            ("bathroom", "Baño", PlanZone.ZoneType.BATHROOM, (0.433, 0.575, 0.888, 0.65)),
            ("closet", "Closet", PlanZone.ZoneType.CLOSET, (0.412, 0.54, 0.607, 0.65)),
            ("primary_bedroom", "Habitación", PlanZone.ZoneType.PRIMARY_BEDROOM, (0.303, 0.675, 0.888, 0.875)),
        ],
    },
    {
        "letter": "D", "page": 8, "file": "palmera_tipo_d.jpg",
        "notes": 'Hoja H-08 "APARTAMENTO TIPO D" — estudio. Zonas propuestas por IA; pendiente de '
        'validación humana. Nota en la hoja fuente: "area de lavado/closet depende si firman o no '
        'con hilton" — el uso de esa área es condicional y debe confirmarse por unidad.',
        "zones": [
            ("balcony", "Balcón/terraza", PlanZone.ZoneType.BALCONY, (0.14, 0.115, 0.83, 0.23)),
            ("primary_bedroom", "Habitación", PlanZone.ZoneType.PRIMARY_BEDROOM, (0.14, 0.28, 0.49, 0.41)),
            ("living_room", "Sala", PlanZone.ZoneType.LIVING_ROOM, (0.14, 0.44, 0.49, 0.54)),
            ("bathroom", "Baño", PlanZone.ZoneType.BATHROOM, (0.14, 0.61, 0.54, 0.71)),
            ("closet", "Closet/lavado (condicional)", PlanZone.ZoneType.LAUNDRY, (0.39, 0.62, 0.58, 0.70)),
            ("kitchen", "Cocina", PlanZone.ZoneType.KITCHEN, (0.71, 0.625, 0.86, 0.825)),
        ],
    },
]


def _floor_variant_for(family_code: str, unit: Unit) -> str:
    if unit.is_penthouse:
        return UnitPlanTemplate.FloorVariant.PENTHOUSE_DUPLEX
    if family_code == "palmera":
        return UnitPlanTemplate.FloorVariant.ALL_FLOORS
    level = unit.floor.level if unit.floor_id else None
    if level == 1:
        return UnitPlanTemplate.FloorVariant.FIRST_FLOOR
    return UnitPlanTemplate.FloorVariant.UPPER_FLOOR


class Command(BaseCommand):
    help = (
        "Seeds the Unit Plan Template catalog for every imported unit, with real "
        "derived-plan zones for PALMERA A/B/C/D and Missing Source slots elsewhere."
    )

    def add_arguments(self, parser):
        parser.add_argument("--organization", default=None)

    def handle(self, *args, **options):
        if options["organization"]:
            try:
                org = Organization.objects.get(name=options["organization"])
            except Organization.DoesNotExist:
                raise CommandError(f"No existe una organización llamada '{options['organization']}'.")
        else:
            org = Organization.objects.first()
        if org is None:
            raise CommandError("Run `manage.py seed_pilot_data` first.")

        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.filter(username="harrison", profile__organization=org).first() \
            or User.objects.filter(profile__organization=org).first()

        created_templates = 0
        assigned_units = 0
        units = Unit.objects.filter(building__project__organization=org).select_related(
            "building", "building__family", "floor",
        )
        for unit in units:
            family = unit.building.family
            if family is None or not unit.apartment_letter:
                continue
            variant = _floor_variant_for(family.code, unit)
            code = services.template_code_for(family.code, unit.apartment_letter, variant)
            template = UnitPlanTemplate.objects.filter(organization=org, code=code, is_current=True).first()
            if template is None:
                template = UnitPlanTemplate.objects.create(
                    organization=org, code=code, family=family, unit_type_letter=unit.apartment_letter,
                    floor_variant=variant, status=UnitPlanTemplate.Status.MISSING_SOURCE, created_by=user,
                    uncertainty_notes="No se localizó un plano de planta por unidad en las fuentes "
                    "suministradas para esta familia — solo la leyenda del plano de conjunto y la "
                    "planilla de tipologías. Configure el plano cuando esté disponible; el flujo "
                    "operativo funciona en cuanto se asigne un plano real a esta plantilla.",
                )
                created_templates += 1
            existing = template.assignments.filter(unit=unit, is_current=True).first()
            if existing is None:
                services.assign_unit_to_template(unit, template, user)
                assigned_units += 1

        # Upgrade the 4 real PALMERA templates with their genuine derived
        # crop + AI-proposed (Draft) zones, if not already done.
        palmera_family = None
        for unit in units:
            if unit.building.family and unit.building.family.code == "palmera":
                palmera_family = unit.building.family
                break

        upgraded = 0
        if palmera_family is not None:
            source_drawing = Drawing.objects.filter(
                project=palmera_family.project, drawing_type=Drawing.DrawingType.BUILDING_PLAN,
            ).first()
            doc_type, _ = DocumentType.objects.get_or_create(
                organization=org, code="unit-plan-derived", defaults={"name": "Plano de unidad derivado (operativo)"},
            )
            for spec in PALMERA_TEMPLATES:
                variant = UnitPlanTemplate.FloorVariant.ALL_FLOORS
                code = services.template_code_for("palmera", spec["letter"], variant)
                template = UnitPlanTemplate.objects.filter(organization=org, code=code, is_current=True).first()
                if template is None:
                    continue
                if template.derived_plan_document_id is not None:
                    continue  # already upgraded — idempotent re-run

                crop_path = DERIVED_PLANS_DIR / spec["file"]
                if not crop_path.exists():
                    continue
                with open(crop_path, "rb") as fh:
                    stored = document_storage.save(fh, spec["file"])
                duplicate = DocumentVersion.objects.filter(sha256=stored["sha256"]).first()
                document = Document.objects.create(
                    organization=org, document_type=doc_type,
                    title=f"Plano operativo derivado — PALMERA Tipo {spec['letter']}", created_by=user,
                )
                DocumentVersion.objects.create(
                    document=document, version_number=1, stored_name=stored["stored_name"],
                    original_filename=stored["original_filename"], sha256=stored["sha256"],
                    size_bytes=stored["size_bytes"], mime_type="image/jpeg", uploaded_by=user,
                    is_duplicate_of=duplicate, created_by=user,
                )
                template.source_drawing = source_drawing
                template.source_page = spec["page"]
                template.derived_plan_document = document
                template.status = UnitPlanTemplate.Status.DRAFT
                template.uncertainty_notes = spec["notes"]
                template.save()

                for order, (zone_code, name_es, zone_type, box) in enumerate(spec["zones"]):
                    x0, y0, x1, y1 = box
                    services.add_zone(
                        template, zone_code=zone_code, name_es=name_es, zone_type=zone_type,
                        coordinates={"x0": x0, "y0": y0, "x1": x1, "y1": y1},
                        display_order=order, validation_state=PlanZone.ValidationState.NEEDS_REVIEW,
                        source_confidence="low", user=user,
                    )
                upgraded += 1

        self.stdout.write(self.style.SUCCESS(
            f"Plantillas creadas: {created_templates}. Unidades asignadas: {assigned_units}. "
            f"Plantillas PALMERA actualizadas con plano real: {upgraded}."
        ))
