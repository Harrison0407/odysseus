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
| `/reclamos/` | `apps.claims.views.claim_list` | Supplier claims, scoped to organization, filterable by status |
| `/reclamos/nuevo/` | `claim_create` | Create a claim linked to a shipment/supplier and, optionally, any of its supporting records |
| `/reclamos/<id>/` | `claim_detail` | Full traceability, quantities/amount, evidence upload, missing-document warnings, lifecycle action buttons |
| `/reclamos/<id>/evidencia/` (POST) | `claim_evidence_upload` | Attaches evidence via the shared `attach_evidence` mechanism |
| `/reclamos/<id>/aprobar/` (POST) | `claim_approve` | DRAFT → APPROVED (requires at least one evidence attachment) |
| `/reclamos/<id>/enviar/` (POST) | `claim_submit` | APPROVED → SUBMITTED (refuses to run twice — no duplicate submission) |
| `/reclamos/<id>/respuesta/` (POST) | `claim_record_response` | Records the supplier's accepted/partially-accepted/rejected response |
| `/reclamos/<id>/resolver/` (POST) | `claim_resolve` | Records replacement/credit-note/settlement resolution |
| `/reclamos/<id>/cerrar/` (POST) | `claim_close` | RESOLVED → CLOSED |
| `/reclamos/<id>/paquete/` | `claim_generate_package` | Downloads the self-contained HTML claim package |
| `/etiquetas/<app_label>/<model>/<id>/` | `apps.labels.views.label_print` | QR label view/print page for any registered entity (lot, location, receipt, tool, container, delivery, installation record); GET previews, POST logs a print event |
| `/etiquetas/lote/imprimir/` (POST) | `label_batch_print` | Batch-prints QR labels for a checkbox-selected set of same-type entities (e.g. every lot at a location) |
| `/etiquetas/<label_id>/invalidar/` (POST) | `label_invalidate` | Invalidates a label and generates its numbered replacement |
| `/qr/<token>/` | `apps.labels.views.qr_scan_landing` | Controlled scan landing page — `login_required`; forwards into the entity's own existing detail page after an organization check |
| `/api/v1/...` | DRF router | `shipments`, `purchase-orders`, `manifest-lines`, `manifest-variances`, `discrepancies` (read-only, org-scoped) |
| `/admin/` | Django admin | Back-office/debug only — every model auto-registered; never the intended business-user interface (spec requirement) |

## Persona → default landing dashboard

## Physical property / field operations release (added in a later session)

| Route | View | Notes |
|---|---|---|
| `/propiedades/` | `apps.projects.views.building_family_list` | Active building-family catalog (org + project scoped), building/unit counts |
| `/propiedades/familias/<id>/` | `building_family_detail` | Physical buildings within a family |
| `/propiedades/edificios/<id>/` | `building_detail` | Floors and units within one physical building, common areas |
| `/propiedades/unidades/` | `unit_search` | Search/filter by permanent code, apartment number, or family |
| `/propiedades/unidades/<id>/` | `unit_detail` | Unit detail — measurements, linked drawings, and (extended in later milestones) allocations/issues/walkthroughs |
| `/planos/` | `apps.drawings.views.drawing_list` | Drawing register, org + project scoped |
| `/planos/<id>/` | `drawing_detail` | One drawing's metadata, download link, approve/supersede actions, full revision history |
| `/planos/<id>/reemplazar/` (POST) | `drawing_supersede` | Registers a new revision; the prior one is preserved, never edited |
| `/planos/<id>/aprobar/` (POST) | `drawing_approve` | Requires the same senior-authorization permission as every other approval gate |
| `/compras/lineas/<id>/asignacion/` | `apps.procurement.views.line_allocation_detail` | Per-line allocation summary (ordered/required/allocated/unallocated/shortage/spare), active allocations, purchased spares with ledger-derived availability |
| `/compras/lineas/<id>/asignar/` (POST) | `line_allocate` | Adds a split destination allocation |
| `/compras/lineas/<id>/confirmar-repuesto/` (POST) | `line_confirm_spare` | Authorized-only purchased-spare confirmation |
| `/compras/asignaciones/<id>/reasignar/` (POST) | `allocation_reassign` | Closes the original allocation, creates a new one, preserves full history |
| `/compras/asignaciones/` | `allocations_by_destination` | All active allocations grouped by building/unit |
| `/compras/repuestos/` | `purchased_spares_list` | All confirmed purchased spares with ledger-derived received/available/reserved quantities |
| `/incidencias/` | `apps.fieldissues.views.issue_list` | Filtered dashboards: reported-by-me, assigned-to-me/team, overdue, awaiting correction/verification, rejected/reopened, closed |
| `/incidencias/reportar/` | `issue_report` | Mobile-first report form — only building is required |
| `/incidencias/<id>/` | `issue_detail` | Full detail, evidence gallery by stage, all lifecycle action buttons |
| `/incidencias/<id>/asignar/` (POST) | `issue_assign` | Assigns/reassigns to a user or department |
| `/incidencias/<id>/en-progreso/`, `/corregir/`, `/listo-para-verificar/`, `/verificar-cerrar/`, `/rechazar/`, `/reenviar/`, `/reinspeccion/` (POST) | lifecycle actions | Each transition server-side guarded; closure requires `can_override_gates` regardless of who did the work |
| `/incidencias/<id>/evidencia/` (POST) | `issue_add_evidence` | Before/during/after staged evidence upload, reusing the shared upload path |
| `/capacitaciones/` | `apps.training.views.session_list` | All sessions; `?reference=1` filters to approved reference installations |
| `/capacitaciones/nueva/` | `session_create` | Building required; trainer/participants/category/floor/unit/drawing all configurable, none hard-coded |
| `/capacitaciones/<id>/` | `session_detail` | Full detail: checklist, staged evidence, participant acknowledgements, lifecycle actions |
| `/capacitaciones/<id>/iniciar/`, `/finalizar/`, `/firma-supervisor/`, `/aprobar-referencia/` (POST) | lifecycle actions | Reference-installation approval requires supervisor sign-off first (both gated by `can_override_gates`) |
| `/capacitaciones/<id>/crear-incidencia/` (POST) | `session_create_issue` | Creates a real `FieldIssue` linked via `training_session`, never a disconnected copy |
| `/recorridos/` | `apps.walkthroughs.views.walkthrough_list` | All walkthroughs; filterable by `?purpose=` (6 configurable purposes) or `?building=` |
| `/recorridos/nuevo/` | `walkthrough_create` | Building required; floor/unit/units(group)/area/category/purpose/inspector all configurable |
| `/recorridos/<id>/` | `walkthrough_detail` | Items, delivery-readiness control (pre-delivery/final-handover purposes only), per-item result/evidence/corrective-issue forms |
| `/recorridos/<id>/poblar-checklist/` (POST) | `walkthrough_populate_checklist` | Bulk-creates items from the walkthrough's category's configured checklist template |
| `/recorridos/<id>/decision-entrega/` (POST) | `walkthrough_delivery_decision` | Blocked by open blocking defects/unverified corrections unless an authorized override with a written reason is supplied |
| `/recorridos/<id>/reinspeccion/` (POST) | `walkthrough_create_reinspection` | Creates a new linked walkthrough; the original is never edited |
| `/recorridos/<id>/siguiente-secuencial/` (POST) | `walkthrough_create_next_sequential` | Creates the next unit's walkthrough pre-filled from this one, for efficient floor/building sweeps |
| `/recorridos/items/<id>/registrar-resultado/`, `/evidencia/`, `/crear-incidencia/` (POST) | item actions | Checklist result + measurements/conditions, staged evidence, and linking a real corrective `FieldIssue` |
| `/evidencias-sin-clasificar/` | `apps.evidenceinbox.views.inbox_list` | All unclassified/partially-classified/classified evidence for the org; filterable by `?status=`; supports batch classification of multiple selected items |
| `/evidencias-sin-clasificar/subir/` | `inbox_upload` | Upload form — only the file/document type/title are required; project/building/date-taken/notes are optional starting guesses |
| `/evidencias-sin-clasificar/<id>/` | `inbox_detail` | Upload provenance (uploader, timestamp, checksum, download link), active classifications, classify/reclassify forms |
| `/evidencias-sin-clasificar/<id>/clasificar/` (POST) | `inbox_classify` | Links the evidence to a real target (14 supported types); target is resolved and organization-verified server-side |
| `/evidencias-sin-clasificar/clasificaciones/<id>/reasignar/` (POST) | `inbox_reclassify` | Requires a written reason; the prior classification is preserved, marked inactive and linked via `superseded_by` |
| `/evidencias-sin-clasificar/clasificar-lote/` (POST) | `inbox_batch_classify` | Classifies every selected evidence row to the same target in one action |
| `/propiedades/unidades/<id>/plano/` | `apps.unitplans.views.unit_plan` | Interactive plan viewer — effective template's derived image with clickable zone overlays, per-zone open-issue counts, `?highlight_zone=` support; shows "Fuente faltante" honestly where no real plan exists yet |
| `/propiedades/plantillas/<id>/imagen/` | `template_plan_image` | Serves the derived operational plan image inline (never the original architect PDF) |
| `/propiedades/zonas/<id>/crear-incidencia/` | `zone_create_issue` | Quick issue creation prefilled with building/floor/unit/room/plan template/zone |
| `/propiedades/zonas/<id>/agregar-foto/` (POST) | `zone_add_photo` | One-click photo upload, classified directly to the zone via the Unclassified Evidence Inbox mechanism |
| `/propiedades/zonas/<id>/crear-item-recorrido/` (POST) | `zone_create_walkthrough_item` | Creates (or reuses) an in-progress walkthrough for the unit and adds an item prefilled from the zone |
| `/propiedades/admin-planos/` | `review_queue` | Operational review queue: non-approved templates, non-validated zones, units with no plan assigned |
| `/propiedades/admin-planos/plantillas/<id>/` | `template_admin_detail` | Full mapping screen: upload/replace plan, add/edit zones, validate, approve, supersede |
| `/propiedades/admin-planos/plantillas/<id>/aprobar/`, `/reemplazar/`, `/cargar-plano/` (POST) | template admin actions | All require `can_override_gates`; supersede/upload-when-approved always create a new revision, never an in-place overwrite |
| `/propiedades/admin-planos/plantillas/<id>/agregar-zona/`, `/propiedades/admin-planos/zonas/<id>/editar/`, `/validar/` (POST) | zone admin actions | Editing a zone always supersedes rather than mutating in place; requires `can_override_gates` |
| `/compras/paquetes/` | `procurement.package_views.package_list` | Packages hosted by the user's own organization, plus any package where they hold an active role assignment |
| `/compras/paquetes/<id>/` | `package_detail` | Role-based projection — factory quotes/internal-cost sheets/client quotes/verification assertions/change requests/disclosure grants each render only for a capability-holding viewer; an unauthorized package is a 404 |
| `/compras/paquetes/<id>/cotizacion-fabrica/crear/`, `/hoja-comercial/crear/`, `/cotizacion-cliente/crear/` | commercial-layer creation | Each requires `CREATE_COMMERCIAL_DOCUMENT` in the package |
| `/compras/cotizaciones-cliente/<id>/aprobar/` (POST) | `client_quote_approve` | Requires `APPROVE_CLIENT_QUOTE`, never implied by whoever prepared the quote |
| `/compras/paquetes/<id>/congelar/` (POST) | `package_freeze` | Requires `APPROVE_GATE`; snapshots critical roles/terms |
| `/compras/paquetes/<id>/cambios/crear/`, `/compras/cambios/<id>/<approve\|reject>/` (POST) | Change Request lifecycle | Creation requires the package already frozen and places it on hold; approval requires a field-specific capability |
| `/compras/paquetes/<id>/divulgaciones/crear/`, `/compras/divulgaciones/<id>/revocar/` (POST) | Disclosure Grant lifecycle | Creation requires `AUTHORIZE_DISCLOSURE` and a non-empty field scope; revocation never deletes the historical row |
| `/compras/paquetes/<id>/evidencia/crear/`, `/compras/evidencia/<id>/agregar/`, `/compras/evidencia-item/<id>/verificar/` | Evidence Bundle lifecycle | Upload never implies verification; verification enforces uploader ≠ verifier plus the bundle's required capability |
| `/compras/paquetes/<id>/aserciones/crear/` (POST) | `verification_assertion_create` | Refuses an unverified source Evidence Bundle |
| `/gobernanza/partes/`, `/partes/nueva/`, `/partes/<id>/` | Party admin | Gated by `can_override_gates` |
| `/gobernanza/auditoria-privilegiada/` | `privileged_audit` | Read-only log of every governance action (role/capability grants, disclosure grants/revocations, visibility-mode changes, package freezes, change requests, verification assertions, evidence verifications, privileged access grants/denials); gated by `can_override_gates` |

| Role code | Dashboard template |
|---|---|
| `compras` | `core/dashboard_compras.html` |
| `china_origin` | `core/dashboard_china.html` |
| `finanzas` | `core/dashboard_finanzas.html` |
| `almacen` / `recepcion` | `core/dashboard_almacen.html` |
| `obra` | `core/dashboard_obra.html` |
| `direccion` / `management` | `core/dashboard_direccion.html` |
| (none matched) | `core/dashboard_generic.html` |
