# Fable Interactive Plans Final Report — 90874ed

## Report status

This report reconstructs the final implementation state from the committed Git evidence and repository documentation associated with commit `90874ed`.

It is not represented as a verbatim copy of an independently preserved Fable response.

## Milestone

Interactive Apartment Plan / Room-Zone Layer

## Repository

`/Users/harrison/Downloads/DT_Beach_Supply_Control_Fable5:/Application`

## Commit evidence

- Short commit: `90874ed`
- Full commit: `90874edacd980983fa67022a6f57e1157ad4b52e`
- Commit title: `Add Interactive Apartment Plan / Room-Zone layer (apps.unitplans)`
- Author: Harrison Vargas
- Commit date: July 19, 2026
- Files changed: 40
- Lines added: 2,298
- Lines removed: 4
- New milestone tests: 24
- Regression suite reported: `394/394 passing`

## Implementation summary

A new Django application, `apps.unitplans`, implemented the Interactive Apartment Plan / Room-Zone layer.

The implemented workflow allows an authorized user to:

1. select a property family, building, floor and apartment;
2. resolve the effective plan template for the apartment;
3. open the interactive apartment-plan viewer;
4. click a room or operational zone;
5. create a field issue linked to that room and plan;
6. upload photographic evidence linked to the plan context;
7. create a walkthrough item linked to the room and plan;
8. preserve the exact plan-template and zone revision referenced by historical records.

## Principal models

### UnitPlanTemplate

Reusable operational plan template resolved from:

- property family;
- apartment type or `apartment_letter`;
- computed floor variant;
- penthouse status where applicable.

The design intentionally avoids creating one duplicated plan-template row for every physical apartment.

### PlanZone

Stores a room or operational zone with:

- stable room or zone type;
- relative rectangular or polygon coordinates;
- validation status;
- immutable version history;
- supersession relationship.

### UnitPlanAssignment

Links each apartment unit to its current effective plan template.

Only one current assignment is allowed per unit.

## Resolution logic

The effective plan template is resolved deterministically from already-imported property data, including:

- property family;
- `Unit.apartment_letter`;
- `Floor.level`;
- `Unit.is_penthouse`;
- computed floor variants such as first floor, upper floor, all floors or penthouse duplex.

The implementation does not introduce a separate invented source of apartment identity.

## Immutable versioning

`UnitPlanTemplate` and `PlanZone` use the established:

- `supersedes`;
- `is_current`;

immutable-row versioning pattern.

Changing a plan template or correcting a room boundary creates a new record rather than mutating the historical record.

When a template is superseded:

- currently assigned units move to the replacement revision;
- existing field issues remain linked to the exact historical template and zone they originally referenced;
- walkthrough and inspection history is preserved;
- prior geometry is not silently altered.

## Source-plan investigation

The architectural source package was directly inspected before implementation.

The repository documentation reports that all 16 pages of:

`imports/buildings/PALMERA - PLANOS 13.11.2025.pdf`

were rendered and visually reviewed.

### Reliable source found

PALMERA contains genuine individual apartment-type drawings:

- Tipo A;
- Tipo B;
- Tipo C;
- Tipo D.

Relevant sheets:

- H-05;
- H-06;
- H-07;
- H-08.

The apartment-type interpretation was cross-checked against the H-09 apartment occupancy table and the already-imported `apartment_letter` values.

### Missing individual apartment plans

No reliable per-unit architectural drawings were found in the supplied source material for:

- ARENA T1;
- MARE B;
- SOLE;
- SOLE PH;
- SOLE 26.

These families were deliberately represented as:

`Missing Source`

No room layouts were invented.

Their template slots remain operationally usable and can receive real plans later through the administrative mapping workflow without requiring code changes.

## PALMERA derived operational plans

The four PALMERA apartment-type templates use genuine derived crops from the architectural source.

Those crops are stored through the normal document and document-version mechanism with checksum tracking.

AI-proposed room-zone mappings remain clearly classified as:

- `Draft`;
- `Needs Review`.

They are not presented as architect-approved plans.

The source drawings themselves were also reported as marked:

`PLANOS AUN EN PROCESO`

## User interfaces

### Interactive plan viewer

Route:

`/propiedades/unidades/<id>/plano/`

Capabilities include:

- effective-plan display;
- clickable zone overlays;
- room or zone identification;
- open-issue counts by zone;
- create field issue;
- add photograph;
- create walkthrough item;
- preserve plan and drawing revision context.

### Plan administration and review

Route:

`/propiedades/admin-planos/`

Capabilities include:

- template mapping;
- source review;
- zone creation and correction;
- validation workflow;
- approval and supersession;
- Missing Source review.

## Security and authorization

The unit-plan viewer and plan-administration interfaces are organization- and project-scoped.

Cross-organization access was reported to return `404`.

Mutating administrative actions require senior authorization through:

`can_override_gates`

This applies to actions including:

- adding or editing a zone;
- validating a zone;
- approving a template;
- superseding a template.

Authorization is enforced in the service layer, not only in the user interface.

## Integration points

Plan references were added to operational records including:

- `FieldIssue`;
- `WalkthroughItem`;
- `InstallationRecord`;
- `InspectionRecord`.

The milestone also modified integrations in:

- `apps.evidenceinbox`;
- `apps.fieldissues`;
- `apps.requests`;
- `apps.walkthroughs`;
- `apps.workflow`;
- project and unit-detail templates;
- configuration and URL routing.

## Defects found and corrected during implementation

Three defects were reported as discovered through actual execution:

1. A plain uniqueness constraint conflicted with the intended immutable versioning pattern.
2. A supersession operation temporarily violated the uniqueness constraint because of write ordering.
3. Superseding a template initially left currently assigned units pointing to the replaced template rather than moving them automatically to the replacement revision.

All three were corrected before the milestone was committed.

## Validation

The milestone was reported live-validated across:

- MARE B — Building 25, Floor 4, Apartment C4;
- SOLE — Building 17;
- SOLE PH — Building 18, including the duplex penthouse;
- SOLE 26 — Building 26;
- PALMERA;
- ARENA T1 — Building 9.

The live validation included a complete operational cycle:

1. open apartment plan;
2. click a room or zone;
3. create a field issue;
4. upload a photograph;
5. reopen the issue from the plan;
6. supersede a plan template;
7. confirm that historical issue references remain unchanged.

## Test evidence

Dedicated test module:

`tests/test_unit_plans.py`

Reported milestone tests:

`24`

Reported full regression suite:

`394/394 passing`

## Documentation evidence

The implementation is documented in:

- `ASSUMPTIONS.md`;
- `docs/implementation-log.md`, entry 49;
- `docs/architecture-decisions.md`, ADR-040;
- `docs/KNOWN_LIMITATIONS.md`;
- `docs/REQUIREMENTS_TRACEABILITY.md`;
- `docs/SECURITY.md`;
- `docs/implementation-roadmap.md`;
- `docs/ui-navigation-map.md`.

## Final milestone conclusion

The Interactive Apartment Plan / Room-Zone layer was completed and committed at `90874ed`.

The system provides a reusable, immutable and authorization-controlled operational plan layer while preserving a strict distinction between:

- original architectural source drawings;
- derived operational plan images;
- AI-proposed zone mappings;
- validated operational mappings;
- missing source material.

Only PALMERA had reliable per-unit-type architectural plans in the supplied source package.

ARENA T1, MARE B, SOLE, SOLE PH and SOLE 26 correctly remain `Missing Source` until reliable individual apartment plans are supplied.
