# UI Navigation Map

| URL | View | Purpose |
|---|---|---|
| `/accounts/login/` | Django auth | Login (Spanish UI) |
| `/` | `apps.core.views.dashboard_home` | Persona dashboard, branched by `Role.code`: Compras / China / Finanzas / Almacén / Obra / Dirección / generic |
| `/documentos/` | `apps.documents.views.document_list` | All documents for the user's organization |
| `/documentos/subir/` | `document_upload` | Upload form: type, title, file — validates extension/size, computes SHA-256, flags duplicates |
| `/documentos/<id>/` | `document_detail` | Version history, SHA-256, duplicate warning |
| `/documentos/<id>/descargar/<version_id>/` | `document_download` | Authenticated, org-scoped file download |
| `/compras/` | `apps.procurement.views.po_list` | Purchase orders, with "RECIBIDO EN FULL sin verificar" badge when the source document carries that stamp |
| `/compras/<id>/` | `po_detail` | Lines, payment milestones, open-commitment carryover balance, "create handoff" for `purchasing_to_finance`/`finance_to_logistics` |
| `/embarques/` | `apps.shipments.views.shipment_list` | All shipments |
| `/embarques/<id>/` | `shipment_detail` | **Flagship screen**: Official Summary panel, Internal Operational Manifest panel, totals reconciliation, variance matrix |
| `/recepcion/` | `apps.receiving.views.receipt_list` | Containers in receiving |
| `/recepcion/<id>/` | `receipt_detail` | Per-line receiving form: received/damaged/missing quantities, exception type, notes; link to the detailed receiving manifest download |
| `/recepcion/<id>/linea/<line_id>/registrar/` | `receipt_line_update` (POST) | Posts the receiving line — the only path that creates `InventoryMovement` |
| `/recepcion/planes/<id>/` | `receiving_plan_detail` | Receiving-plan overview (expected quantities, project, status) plus its external-storage comparison scenarios; linked from the Shipment detail page when a plan exists |
| `/recepcion/planes/<id>/comparaciones/nueva/` (POST) | `comparison_scenario_create` | Starts a new versioned storage-comparison scenario for this plan |
| `/recepcion/comparaciones/<id>/` | `comparison_scenario_detail` | Comparison table (ranked, converted totals, suitability warnings), add-option form, finalize-decision form |
| `/recepcion/comparaciones/<id>/opciones/nueva/` (POST) | `comparison_option_create` | Adds one internal-baseline/external storage option to the scenario |
| `/recepcion/comparaciones/<id>/finalizar/` (POST) | `comparison_finalize` | Records the chosen option + rationale, once — immutable afterward |
| `/recepcion/comparaciones/<id>/exportar/` | `comparison_export` | Downloads a self-contained HTML comparison snapshot (same mechanism as the receiving manifest snapshot) |
| `/almacen/ubicaciones/` | `apps.inventory.views.location_list` | On-hand quantity per location, computed live from the movement ledger; org-scoped |
| `/almacen/ubicaciones/<id>/` | `location_detail` | Capacity/utilization, suitability conditions, live suitability checker, assigned inventory, pending inbound quantities, responsible custodian, transfer form |
| `/almacen/ubicaciones/<id>/transferir/` (POST) | `location_transfer` | Transfers a lot into this location via `apps.requests.services.transfer_lot`, enforcing storage suitability |
| `/almacen/lotes/<id>/` | `lot_detail` | Full movement history for one lot |
| `/almacen/conteos/` | `apps.inventory.views.cycle_count_list` | Cycle counts, scoped to organization |
| `/almacen/conteos/nuevo/` | `cycle_count_create` | Start a cycle count for a site and set of lots |
| `/almacen/conteos/<id>/` | `cycle_count_detail` | Per-lot count entry (blind or not), variance, adjustment approval |
| `/almacen/conteos/<id>/linea/<line_id>/registrar/` (POST) | `cycle_count_record` | Records a physical count (supports recounting) |
| `/almacen/conteos/<id>/linea/<line_id>/aprobar-ajuste/` (POST) | `cycle_count_approve_adjustment` | Posts an approved variance as a real `InventoryMovement` |
| `/solicitudes/` | `apps.requests.views.request_list` | Material requests, scoped to the user's accessible projects |
| `/solicitudes/nueva/` | `request_create` | Create a material request |
| `/solicitudes/<id>/` | `request_detail` | Requested/approved/reserved/dispatched/delivered quantities; reserve-per-line and dispatch actions; links to generated deliveries |
| `/solicitudes/<id>/aprobar/` (POST) | `request_approve` | Approve a request (sets `quantity_approved` per line to the requested amount) |
| `/solicitudes/<id>/linea/<line_id>/reservar/` (POST) | `request_reserve_line` | Reserve inventory against a lot for one request line |
| `/solicitudes/<id>/despachar/` (POST) | `request_dispatch` | Dispatch reserved quantities — supports splitting one line across several reservations/lots in a single submission; creates the `Dispatch` + initializes the `Delivery` |
| `/solicitudes/entregas/` | `delivery_list` | Deliveries, filterable by project / pending-acceptance |
| `/solicitudes/entregas/<id>/` | `delivery_detail` | Per-line accepted/rejected/damaged recording, delivery completion, project-receipt creation, "create handoff" for `project_delivery_to_installation`, evidence upload |
| `/solicitudes/entregas/<id>/linea/<line_id>/registrar/` (POST) | `delivery_record_line` | Records accepted/rejected/damaged quantities for one delivery line (recomputes, never increments) |
| `/solicitudes/entregas/<id>/completar/` (POST) | `delivery_complete` | Marks the delivery accepted or rejected/failed |
| `/solicitudes/entregas/<id>/recepcion/` (POST) | `delivery_create_receipt` | Records the project's confirmed destination/damage/missing-items receipt |
| `/solicitudes/entregas/<id>/evidencia/` (POST) | `delivery_add_evidence` | Uploads a file as evidence (reuses `apps.documents`/`apps.audit.Attachment`) |
| `/solicitudes/instalaciones/` | `installation_list` | Installations, filterable by incomplete/rework, scoped to accessible projects |
| `/solicitudes/recepciones/<receipt_id>/instalacion/nueva/` | `installation_create` | Create an installation record for a delivered line (idempotent) |
| `/solicitudes/instalaciones/<id>/` | `installation_detail` | Current stage/quantities/blockers/open defects, progress form, ack/supervisor-confirm, inspections, handoff actions for `installation_to_inspection` and `inspection_to_acceptance`, final-acceptance detail, evidence upload |
| `/solicitudes/instalaciones/<id>/progreso/` (POST) | `installation_progress` | Records installed/not-used/damaged quantities; enforces the delivered-quantity guard (with an authorized-override path) |
| `/solicitudes/instalaciones/<id>/reconocer/` (POST) | `installation_acknowledge` | Installer acknowledgement |
| `/solicitudes/instalaciones/<id>/confirmar-supervisor/` (POST) | `installation_supervisor_confirm` | Supervisor confirmation |
| `/solicitudes/instalaciones/<id>/inspeccionar/` (POST) | `installation_create_inspection` | Records a new inspection (or reinspection) — pass/conditional/fail, punch-list defects one per line |
| `/solicitudes/instalaciones/<id>/aceptar-final/` (POST) | `installation_final_accept` | Records the accepted-vs-conditional decision detail — only once the `inspection_to_acceptance` handoff is already `ACCEPTED` (see ADR-021) |
| `/solicitudes/instalaciones/<id>/evidencia/` (POST) | `installation_add_evidence` | Uploads a file as evidence |
| `/solicitudes/inspecciones/` | `inspection_list` | Inspections, filterable by result |
| `/solicitudes/inspecciones/<id>/` | `inspection_detail` | Punch-list, technical sign-off, reinspection chain link, evidence upload |
| `/solicitudes/inspecciones/<id>/items/<item_id>/cerrar/` (POST) | `inspection_close_item` | Closes one punch-list defect |
| `/solicitudes/inspecciones/<id>/firma-tecnica/` (POST) | `inspection_sign_off` | Records technical sign-off |
| `/solicitudes/inspecciones/<id>/evidencia/` (POST) | `inspection_add_evidence` | Uploads a file as evidence |
| `/solicitudes/aceptaciones/` | `acceptance_list` | Final acceptance history, scoped to accessible projects |
| `/costos/` | `apps.cost.views.landed_cost_list` | Landed cost versions (provisional vs. final), scoped to accessible organization |
| `/costos/<id>/` | `landed_cost_detail` | Per-line cost breakdown, "finalizar" action |
| `/costos/<id>/finalizar/` (POST) | `landed_cost_finalize` | Marks a landed-cost version final, once |
| `/costos/embarque/<shipment_id>/` | `shipment_cost_dashboard` | Cost documents/charges, allocation runs, "ejecutar asignación" and "calcular nueva versión" actions — linked from the shipment detail page |
| `/costos/embarque/<shipment_id>/asignar/` (POST) | `shipment_run_allocation` | Runs a cost allocation across the shipment's internal manifest lines |
| `/costos/embarque/<shipment_id>/calcular/` (POST) | `shipment_calculate_version` | Calculates a new, immutable landed-cost version |
| `/flujo/` | `apps.workflow.views.inbox` | **Role-aware handoff inbox** — tabs: para mí / enviadas por mí / devueltas / bloqueadas / completadas / todas; filterable by gate, status, overdue |
| `/flujo/<id>/` | `handoff_detail` | Live gate readiness (why blocked/ready), evidence, comments, decision history, action buttons |
| `/flujo/<id>/enviar/` (POST) | `handoff_submit` | Submit — blocked if the gate is not ready |
| `/flujo/<id>/anular-enviar/` (POST) | `handoff_override_submit` | Submit with an authorized override + written reason (only shown to users with `can_override_gates`) |
| `/flujo/<id>/aceptar/` (POST) | `handoff_accept` | Accept — transfers `ResponsibilityAssignment` and, for `Shipment`-anchored gates, advances `Shipment.status` |
| `/flujo/<id>/rechazar/` (POST) | `handoff_reject` | Reject with a required reason |
| `/flujo/<id>/devolver/` (POST) | `handoff_return` | Return for correction with a required reason |
| `/flujo/<id>/reenviar/` (POST) | `handoff_resubmit` | Corrected resubmission — creates a new superseding `Handoff` version |
| `/flujo/<id>/comentario/` (POST) | `handoff_comment` | Add a comment (reuses `apps.audit.Comment`) |
| `/flujo/crear/<content_type_id>/<object_id>/<gate_code>/` | `handoff_create` | Generic create-handoff entry point, linked from the Shipment, Material Request, Delivery, and Installation detail pages |
| `/reportes/embarque/<id>/instantanea/` | `apps.reports.views.shipment_snapshot` | Generates and downloads a self-contained HTML snapshot |
| `/reportes/recepcion/<receipt_id>/manifiesto/` | `apps.reports.views.receiving_manifest_snapshot` | Detailed internal receiving manifest (spec 13A.8) — 10-section self-contained HTML download, linked from the receiving detail page |
| `/reportes/compartir/<token>/` | `shared_view` | Public, revocable, logged read-only share link |
| `/aduanas/confotur/` | `apps.customs.views.confotur_list_list` | CONFOTUR listings, scoped to organization |
| `/aduanas/confotur/<id>/` | `confotur_list_detail` | Lines, exemption amounts, duplicate status |
| `/aduanas/confotur/reconciliacion/` | `confotur_reconciliation` | Duplicate-exemption candidates (spec section 25), grouped by shared quotation/manifest line |
| `/aduanas/confotur/lineas/<id>/confirmar-duplicado/` (POST) | `confotur_confirm_duplicate` | Confirms one line as a duplicate of another |
| `/herramientas/` | `apps.tools.views.tool_list` | Tools, scoped to organization, with current custody status |
| `/herramientas/<id>/` | `tool_detail` | Custody history, repairs, checkout/return/repair actions |
| `/herramientas/<id>/entregar/` (POST) | `tool_checkout` | Checks out a tool — blocked if already checked out |
| `/herramientas/<id>/entrega/<checkout_id>/devolver/` (POST) | `tool_return` | Records a return, optionally with damage noted |
| `/herramientas/<id>/reparacion/` (POST) | `tool_repair` | Records a repair, optionally resulting in write-off |
| `/api/v1/...` | DRF router | `shipments`, `purchase-orders`, `manifest-lines`, `manifest-variances`, `discrepancies` (read-only, org-scoped) |
| `/admin/` | Django admin | Back-office/debug only — every model auto-registered; never the intended business-user interface (spec requirement) |

## Persona → default landing dashboard

| Role code | Dashboard template |
|---|---|
| `compras` | `core/dashboard_compras.html` |
| `china_origin` | `core/dashboard_china.html` |
| `finanzas` | `core/dashboard_finanzas.html` |
| `almacen` / `recepcion` | `core/dashboard_almacen.html` |
| `obra` | `core/dashboard_obra.html` |
| `direccion` / `management` | `core/dashboard_direccion.html` |
| (none matched) | `core/dashboard_generic.html` |
