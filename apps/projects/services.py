"""Physical property master: permanent unit identifiers and the
idempotent building/floor/unit import (one-shot release).

Core rule: a `Unit.permanent_code` is generated once, at creation, and
stored — never regenerated from the unit's current FKs later. This is
what lets it "remain unchanged throughout construction, purchasing,
shipment, receiving, inventory, delivery, installation, inspection,
acceptance, sales, occupancy, property management, maintenance, issue
history" even if the unit's floor/building assignment is later corrected.
"""

from __future__ import annotations

import dataclasses

from django.db import transaction

from .models import Area, Building, BuildingFamily, Floor, Project, Unit


def build_permanent_code(family: BuildingFamily | None, building: Building, apartment_number: str) -> str:
    """Pure function — does not touch the database. Exposed separately
    from unit creation so tests can assert the exact format without
    needing a full import run."""
    apt = apartment_number.strip().upper()
    if family is None or not family.uses_building_segment:
        base = (family.code if family else building.code).upper()
        return f"{base}-{apt}"
    building_number = (building.building_number or building.code).strip()
    building_segment = f"B{building_number.zfill(2)}" if building_number.isdigit() else building_number.upper()
    return f"{family.code.upper()}-{building_segment}-{apt}"


@dataclasses.dataclass
class ImportResult:
    dry_run: bool
    families_created: int = 0
    buildings_created: int = 0
    floors_created: int = 0
    units_created: int = 0
    units_updated: int = 0
    units_skipped_unchanged: int = 0
    rejected: list = dataclasses.field(default_factory=list)
    uncertain: list = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@transaction.atomic
def import_physical_property_master(organization, *, dry_run=False) -> ImportResult:
    """Idempotent: safe to run repeatedly. Matches existing rows by
    `Unit.permanent_code` (created once, never altered) — a second run
    with identical source data creates nothing new and updates nothing;
    the result always reports created/updated/skipped counts so a
    repeat run is visibly a no-op, not silently swallowed."""
    from . import building_source_data as source
    return _run_import(organization, source, dry_run=dry_run)


def _run_import(organization, source, *, dry_run) -> ImportResult:
    result = ImportResult(dry_run=dry_run)
    savepoint = transaction.savepoint()

    template_map = {
        "arena-t1": source.expand_standard_floors(source.ARENA_T1_SPECS),
        "mare-b": source.expand_standard_floors(source.MARE_B_SPECS),
        "sole": source.expand_standard_floors(source.SOLE_B_SPECS),
        "sole-ph": source.expand_standard_floors(source.SOLE_PH_SPECS_1_TO_3, floors=[1, 2, 3]) + source.SOLE_PH_FLOOR_4_5,
        "sole-26": source.expand_standard_floors(source.SOLE_26_SPECS),
        "palmera": source.palmera_rows(),
    }

    all_families = {**source.FAMILIES, **source.INACTIVE_FAMILIES}
    for family_code, spec in all_families.items():
        project, created = Project.objects.get_or_create(
            organization=organization, code=spec["project_code"], defaults={"name": spec["project_name"]},
        )
        family, family_created = BuildingFamily.objects.get_or_create(
            project=project, code=family_code,
            defaults={
                "display_name": spec["display_name"], "source_label": spec["source_label"],
                "is_active": spec["is_active"], "uses_building_segment": spec["uses_building_segment"],
            },
        )
        if family_created:
            result.families_created += 1

        if not spec["building_numbers"]:
            continue  # inactive family with no known buildings yet — catalog entry only

        units_for_family = template_map.get(family_code, [])
        for building_number in spec["building_numbers"]:
            building_code = f"{family_code}-b{building_number.zfill(2)}"
            building, building_created = Building.objects.get_or_create(
                project=project, code=building_code,
                defaults={
                    "name": f"{spec['display_name']} — Edificio {building_number}",
                    "family": family, "building_number": building_number,
                },
            )
            if building_created:
                result.buildings_created += 1
            elif building.family_id is None:
                building.family = family
                building.building_number = building_number
                building.save()

            floor_cache = {}
            for row in units_for_family:
                floor_key = row["floor_label"]
                floor = floor_cache.get(floor_key)
                if floor is None:
                    floor, floor_created = Floor.objects.get_or_create(
                        building=building, name=f"Piso {floor_key}",
                        defaults={"level": row["floor"], "label": floor_key if floor_key != str(row["floor"]) else ""},
                    )
                    if floor_created:
                        result.floors_created += 1
                    floor_cache[floor_key] = floor

                permanent_code = build_permanent_code(family, building, row["apt_number"])
                existing = Unit.objects.filter(permanent_code=permanent_code).first()
                unit_fields = {
                    "floor": floor, "building": building, "name": row["apt_number"],
                    "apartment_number": row["apt_number"], "apartment_letter": row["letter"],
                    "internal_area_sqm": row["internal"], "terrace_area_sqm": row["terrace"],
                    "total_area_sqm": row["total"], "bedrooms_label": row["bedrooms"],
                    "bathrooms": row["bathrooms"], "has_service_room": row["service_room"],
                    "is_penthouse": row["is_penthouse"],
                    "source_row_reference": "DT Beach Building Apartments.xlsx.pdf",
                }
                if existing is None:
                    Unit.objects.create(permanent_code=permanent_code, **unit_fields)
                    result.units_created += 1
                else:
                    changed = any(getattr(existing, k) != v for k, v in unit_fields.items() if k not in ("floor", "building"))
                    if changed:
                        for k, v in unit_fields.items():
                            setattr(existing, k, v)
                        existing.save()
                        result.units_updated += 1
                    else:
                        result.units_skipped_unchanged += 1

    if dry_run:
        transaction.savepoint_rollback(savepoint)
    else:
        transaction.savepoint_commit(savepoint)
    return result
