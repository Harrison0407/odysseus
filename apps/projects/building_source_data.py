"""Transcribed source data for the physical property master import.

Provenance: `imports/buildings/DT Beach Building Apartments.xlsx.pdf`
(per-unit floor template table) and `imports/buildings/Buildings Plans
Main.pdf` (site-plan building index/legend, giving the physical building
numbers per family). Transcribed by hand from both PDFs, page by page;
values are taken verbatim from the source and never invented. Any
number/measurement not present in the source is left `None` rather than
guessed (core principle 4.3 — never silently assume).

Each "unit spec" tuple is:
    (apartment_letter, internal_sqm, floor1_terrace_sqm_or_None, bedrooms_label, bathrooms, has_service_room)

`floor1_terrace_sqm_or_None` is the terrace area for floor 1 only (every
family's floors 2+ have terrace=0 per the source table); `None` means
that letter does not exist on floor 1 at all (e.g. Arena T1's "D" unit,
confirmed present only from floor 2 onward in the source).
"""

FLOORS_1_TO_5 = [1, 2, 3, 4, 5]


def expand_standard_floors(unit_specs, floors=FLOORS_1_TO_5):
    """Expands a per-letter unit-spec list across `floors`, applying the
    source's own rule that only floor 1 carries a terrace (floors 2+ have
    terrace=0, total=internal) — this mirrors the source spreadsheet
    exactly rather than approximating it."""
    rows = []
    for floor in floors:
        for letter, internal, floor1_terrace, bedrooms, bathrooms, has_service_room in unit_specs:
            if floor == 1 and floor1_terrace is None:
                continue  # letter confirmed absent from floor 1 in the source (e.g. Arena T1 "D")
            terrace = floor1_terrace if floor == 1 else 0
            total = internal + terrace
            apt_number = f"{letter}{floor}"
            rows.append({
                "floor": floor, "floor_label": str(floor), "letter": letter, "apt_number": apt_number,
                "internal": internal, "terrace": terrace, "total": total,
                "bedrooms": bedrooms, "bathrooms": bathrooms, "service_room": has_service_room,
                "is_penthouse": False,
            })
    return rows


# "MARE B" family — source label "MARE B", type code "MB".
MARE_B_SPECS = [
    ("A", 89, 41, "2", 2.0, False),
    ("B", 83, 31, "2", 2.0, False),
    ("C", 83, 31, "2", 2.0, False),
    ("D", 89, 41, "2", 2.0, False),
]

# "SOLE" family (regular, no penthouse) — source label "SOLE B", type code "SB".
SOLE_B_SPECS = [
    ("A", 146, 62, "3", 3.5, True),
    ("B", 106, 35, "2", 2.0, False),
    ("C", 106, 35, "2", 2.0, False),
    ("D", 146, 62, "3", 3.5, True),
]

# "SOLE PH" family (penthouse-inclusive) — source label "SOLE A", type code "PH".
# Floors 1-3 use the same footprint as SOLE B; floor "4-5" is a distinct,
# larger combined penthouse floor (source-confirmed, not derived).
SOLE_PH_SPECS_1_TO_3 = SOLE_B_SPECS
SOLE_PH_FLOOR_4_5 = [
    {"floor": 4, "floor_label": "4-5", "letter": "A (PH)", "apt_number": "A (PH)4-5", "internal": 226, "terrace": 66, "total": 292, "bedrooms": "4", "bathrooms": 4.0, "service_room": True, "is_penthouse": True},
    {"floor": 4, "floor_label": "4-5", "letter": "B (PH)", "apt_number": "B (PH)4-5", "internal": 186, "terrace": 43, "total": 229, "bedrooms": "3", "bathrooms": 3.0, "service_room": True, "is_penthouse": True},
    {"floor": 4, "floor_label": "4-5", "letter": "C (PH)", "apt_number": "C (PH)4-5", "internal": 186, "terrace": 43, "total": 229, "bedrooms": "3", "bathrooms": 3.0, "service_room": True, "is_penthouse": True},
    {"floor": 4, "floor_label": "4-5", "letter": "D (PH)", "apt_number": "D (PH)4-5", "internal": 226, "terrace": 66, "total": 292, "bedrooms": "4", "bathrooms": 4.0, "service_room": True, "is_penthouse": True},
]

# "SOLE 26" family — source label "SOLE 26", type code "S26".
SOLE_26_SPECS = [
    ("A", 90, 30, "2", 2.0, False),
    ("B", 69, 28, "1", 2.0, False),
    ("C", 47, 14, "Studio", 1.0, False),
    ("D", 47, 14, "Studio", 1.0, False),
    ("E", 47, 14, "Studio", 1.0, False),
    ("F", 47, 14, "Studio", 1.0, False),
    ("G", 69, 28, "1", 2.0, False),
    ("H", 90, 30, "2", 2.0, False),
]

# "ARENA T1" family — source label "Arena T1", type code "T1". Floor 1 has
# no "D" unit (source-confirmed — ground floor layout omits it); floors
# 2-5 add a smaller "D" unit not present on floor 1.
ARENA_T1_SPECS = [
    ("A", 125, 26, "3", 2.5, False),
    ("B", 100, 26, "2", 2.0, False),
    ("C", 100, 26, "2", 2.0, False),
    ("D", 70, None, "1", 2.0, False),  # absent from floor 1
    ("E", 100, 26, "2", 2.0, False),
    ("F", 100, 26, "2", 2.0, False),
    ("G", 125, 26, "3", 2.5, False),
]


def _palmera_row(floor, letter, number, internal, bedrooms):
    return {
        "floor": floor, "floor_label": str(floor), "letter": letter, "apt_number": str(number),
        "internal": internal, "terrace": 0, "total": internal, "bedrooms": bedrooms, "bathrooms": 1.0,
        "service_room": False, "is_penthouse": False,
    }


# PALMERA (hotel-style, sequential room numbers, no letter/floor segment
# in its permanent code) — floor 2 has unique per-room areas (transcribed
# individually below); floors 3-5 repeat one uniform per-letter template
# (source-confirmed: only floor 2 varies).
PALMERA_FLOOR_2 = [
    _palmera_row(2, "A", 201, 46, "Estudio"), _palmera_row(2, "A", 202, 38, "Estudio"),
    _palmera_row(2, "A", 203, 38, "Estudio"), _palmera_row(2, "A", 204, 38, "Estudio"),
    _palmera_row(2, "A", 205, 31, "Estudio"), _palmera_row(2, "B", 206, 60, "2 habitaciones"),
    _palmera_row(2, "C", 207, 34, "1 habitación"), _palmera_row(2, "C", 208, 34, "1 habitación"),
    _palmera_row(2, "C", 209, 34, "1 habitación"), _palmera_row(2, "C", 210, 34, "1 habitación"),
    _palmera_row(2, "C", 211, 33, "1 habitación"), _palmera_row(2, "C", 212, 33, "1 habitación"),
    _palmera_row(2, "D", 213, 37, "Estudio"), _palmera_row(2, "D", 214, 37, "Estudio"),
    _palmera_row(2, "C", 215, 33, "1 habitación"), _palmera_row(2, "C", 216, 33, "1 habitación"),
    _palmera_row(2, "C", 217, 34, "1 habitación"), _palmera_row(2, "C", 218, 34, "1 habitación"),
    _palmera_row(2, "C", 219, 34, "1 habitación"), _palmera_row(2, "C", 220, 34, "1 habitación"),
    _palmera_row(2, "B", 221, 31, "2 habitaciones"),
    _palmera_row(2, "A", 222, 32, "Estudio"), _palmera_row(2, "A", 223, 32, "Estudio"),
    _palmera_row(2, "A", 224, 32, "Estudio"), _palmera_row(2, "A", 225, 32, "Estudio"),
    _palmera_row(2, "A", 226, 32, "Estudio"),
]

# Uniform template for floors 3, 4, 5 (room numbers offset by 100 per floor).
_PALMERA_FLOOR_TEMPLATE = [
    ("A", 1, 32, "Estudio"), ("A", 2, 32, "Estudio"), ("A", 3, 32, "Estudio"),
    ("A", 4, 32, "Estudio"), ("A", 5, 32, "Estudio"), ("B", 6, 31, "2 habitaciones"),
    ("C", 7, 36, "1 habitación"), ("C", 8, 36, "1 habitación"), ("C", 9, 36, "1 habitación"),
    ("C", 10, 36, "1 habitación"), ("C", 11, 35, "1 habitación"), ("C", 12, 35, "1 habitación"),
    ("D", 13, 39, "Estudio"), ("D", 14, 39, "Estudio"),
    ("C", 15, 35, "1 habitación"), ("C", 16, 35, "1 habitación"), ("C", 17, 36, "1 habitación"),
    ("C", 18, 36, "1 habitación"), ("C", 19, 36, "1 habitación"), ("C", 20, 36, "1 habitación"),
    ("B", 21, 31, "2 habitaciones"),
    ("A", 22, 32, "Estudio"), ("A", 23, 32, "Estudio"), ("A", 24, 32, "Estudio"),
    ("A", 25, 32, "Estudio"), ("A", 26, 32, "Estudio"),
]


def palmera_rows():
    rows = list(PALMERA_FLOOR_2)
    for floor in (3, 4, 5):
        base = floor * 100
        for letter, offset, internal, bedrooms in _PALMERA_FLOOR_TEMPLATE:
            rows.append(_palmera_row(floor, letter, base + offset, internal, bedrooms))
    return rows


# ---------------------------------------------------------------------------
# Site-plan building index (from "Buildings Plans Main.pdf" legend table):
# which physical building numbers belong to which family. Arena T1's list
# is exactly the 8 buildings named explicitly in the release instruction;
# the rest are read directly off the legend's Building/Bldg.Code/Type/Apts
# columns.
# ---------------------------------------------------------------------------

FAMILIES = {
    "arena-t1": {
        "project_code": "arena", "project_name": "Arena",
        "display_name": "ARENA T1", "source_label": "Arena T1",
        "building_numbers": ["1", "2", "3", "4", "9", "10", "11", "12"],
        "uses_building_segment": True, "is_active": True,
    },
    "mare-b": {
        "project_code": "mare", "project_name": "Mare",
        "display_name": "MARE B", "source_label": "MARE B",
        "building_numbers": ["12", "14", "16", "19", "21", "23", "24", "25"],
        "uses_building_segment": True, "is_active": True,
    },
    "sole": {
        "project_code": "sole", "project_name": "Sole",
        "display_name": "SOLE", "source_label": "SOLE B",
        "building_numbers": ["13", "17", "22"],
        "uses_building_segment": True, "is_active": True,
    },
    "sole-ph": {
        "project_code": "sole", "project_name": "Sole",
        "display_name": "SOLE PH", "source_label": "SOLE A",
        "building_numbers": ["15", "18", "20"],
        "uses_building_segment": True, "is_active": True,
    },
    "sole-26": {
        "project_code": "sole-26", "project_name": "Sole-26",
        "display_name": "SOLE 26", "source_label": "SOLE 26",
        "building_numbers": ["26"],
        "uses_building_segment": True, "is_active": True,
    },
    "palmera": {
        "project_code": "palmera", "project_name": "Palmera",
        "display_name": "PALMERA", "source_label": "PALMERA HOTEL",
        "building_numbers": ["1"],
        "uses_building_segment": False, "is_active": True,
    },
}

# Families named by the release but explicitly not to be activated yet.
# Present so a future import can flip is_active without a schema change.
INACTIVE_FAMILIES = {
    "mare-a": {"project_code": "mare", "project_name": "Mare", "display_name": "MARE A", "source_label": "", "building_numbers": [], "uses_building_segment": True, "is_active": False},
    "arena-t2": {"project_code": "arena", "project_name": "Arena", "display_name": "ARENA T2", "source_label": "", "building_numbers": [], "uses_building_segment": True, "is_active": False},
    "arena-t3": {"project_code": "arena", "project_name": "Arena", "display_name": "ARENA T3", "source_label": "", "building_numbers": [], "uses_building_segment": True, "is_active": False},
}
