# MarketMatch Procurement Product Prototype

Harrison authorized this team-review prototype on 2026-07-21. It is available only in DEBUG when `PROCUREMENT_PROTOTYPE_ENABLED=1` at `/prototype/procurement/`.

It is synthetic-only and session-backed: there are no models, migrations, database writes, production package references, gate attempts, handoffs, evidence, change requests, risk flags, or authorization shortcuts. A1 is visual reference only; A2–A6 are explicitly simulated and Increment 3 production implementation remains unauthorized.

The demo scenario is `PI-MM-2026-017` / `PL-MM-2026-017`; run `python manage.py seed_procurement_prototype` in a DEBUG environment. It includes matched, short, over, unknown, missing, and variant-mismatch examples. The functional comparison normalizes cm/m and kg/g, computes CBM per carton, preserves synthetic revisions, and exports PI, packing, comparison, and discrepancy CSV files.

Team-review decisions (approve, approve with changes, reject, defer) and comments are session-only display data. Import/OCR/PDF parsing and any real supplier-document ingestion are deliberately out of scope.

## Design controls

`static/css/design-system.css` controls colors, typography, spacing, cards, tables, badges, alerts, forms, buttons, responsive navigation, and prototype layout through `--mm-*` tokens. `templates/base.html` controls shared shell/navigation; `templates/procurement_prototype/` contains reusable prototype navigation and page layouts. Domain calculations are kept in `apps/procurement_prototype/domain.py`, not templates.

## Validation boundary

This delivery was validated against local SQLite only when `USE_SQLITE_FOR_TESTS=1`; no PostgreSQL validation is claimed. The module’s tests cover gating, screens, export, actions, matching, units, totals, CBM, status, and synthetic data behavior.

## Closure verification — 2026-07-21

Verified implemented: centralized `--mm-*` design tokens and responsive shell;
DEBUG/explicit-flag route protection; prominent warning on every prototype
screen; A2–A6 simulated screens; PI/Packing List comparison; all six synthetic
outcomes (match, shortage, overage, unknown SKU, missing PI line, and variant
mismatch); revision/action and review controls; and synthetic PI, packing,
comparison, and discrepancy CSV exports. The seed command is DEBUG-only and
prints a URL without creating records.

Security/isolation verification found no prototype ORM models, migrations,
production service imports, credentials, confidential fixtures, or production
exports. An automated action test proves no `ProcurementPackage`, gate attempt/
evaluation/decision, evidence, handoff, `ChangeRequest`, or `RiskFlag` row is
changed. Route resolution is unavailable when DEBUG/feature protection is off.

Automated validation on SQLite: Django checks and migration checks clean; 79
migrations applied and 0 pending; focused prototype coverage passed; complete
pytest result **669 collected, 663 passed, 6 PostgreSQL-only skipped, 0 failed,
exit 0, 128.39s**. PostgreSQL was not validated. HTTP validation of all seven
prototype pages and four CSV endpoints passed; browser automation was not
available in this verification environment, so desktop/tablet visual review is
pending owner walkthrough. This prototype is not deployed and Increment 3
remains unauthorized.

Known limitation: **supplier CSV import and import validation are not
implemented**. No real supplier document may be uploaded or parsed through this
prototype. Exact owner walkthrough URL: `http://127.0.0.1:8765/prototype/procurement/`.
