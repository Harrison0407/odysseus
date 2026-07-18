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
| `/compras/<id>/` | `po_detail` | Lines, payment milestones, open-commitment carryover balance |
| `/embarques/` | `apps.shipments.views.shipment_list` | All shipments |
| `/embarques/<id>/` | `shipment_detail` | **Flagship screen**: Official Summary panel, Internal Operational Manifest panel, totals reconciliation, variance matrix |
| `/recepcion/` | `apps.receiving.views.receipt_list` | Containers in receiving |
| `/recepcion/<id>/` | `receipt_detail` | Per-line receiving form: received/damaged/missing quantities, exception type, notes |
| `/recepcion/<id>/linea/<line_id>/registrar/` | `receipt_line_update` (POST) | Posts the receiving line — the only path that creates `InventoryMovement` |
| `/almacen/ubicaciones/` | `apps.inventory.views.location_list` | On-hand quantity per location, computed live from the movement ledger |
| `/almacen/lotes/<id>/` | `lot_detail` | Full movement history for one lot |
| `/solicitudes/` | `apps.requests.views.request_list` | Material requests |
| `/solicitudes/nueva/` | `request_create` | Create a material request |
| `/solicitudes/<id>/` | `request_detail` | Requested/approved/reserved/dispatched/delivered quantities |
| `/costos/` | `apps.cost.views.landed_cost_list` | Landed cost versions (provisional vs. final) |
| `/costos/<id>/` | `landed_cost_detail` | Per-line cost breakdown |
| `/flujo/` | `apps.workflow.views.inbox` | **Role-aware handoff inbox** — tabs: para mí / enviadas por mí / devueltas / bloqueadas / completadas / todas; filterable by gate, status, overdue |
| `/flujo/<id>/` | `handoff_detail` | Live gate readiness (why blocked/ready), evidence, comments, decision history, action buttons |
| `/flujo/<id>/enviar/` (POST) | `handoff_submit` | Submit — blocked if the gate is not ready |
| `/flujo/<id>/anular-enviar/` (POST) | `handoff_override_submit` | Submit with an authorized override + written reason (only shown to users with `can_override_gates`) |
| `/flujo/<id>/aceptar/` (POST) | `handoff_accept` | Accept — transfers `ResponsibilityAssignment` and, for `Shipment`-anchored gates, advances `Shipment.status` |
| `/flujo/<id>/rechazar/` (POST) | `handoff_reject` | Reject with a required reason |
| `/flujo/<id>/devolver/` (POST) | `handoff_return` | Return for correction with a required reason |
| `/flujo/<id>/reenviar/` (POST) | `handoff_resubmit` | Corrected resubmission — creates a new superseding `Handoff` version |
| `/flujo/<id>/comentario/` (POST) | `handoff_comment` | Add a comment (reuses `apps.audit.Comment`) |
| `/flujo/crear/<content_type_id>/<object_id>/<gate_code>/` | `handoff_create` | Generic create-handoff entry point, linked from the Shipment and Material Request detail pages |
| `/reportes/embarque/<id>/instantanea/` | `apps.reports.views.shipment_snapshot` | Generates and downloads a self-contained HTML snapshot |
| `/reportes/compartir/<token>/` | `shared_view` | Public, revocable, logged read-only share link |
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
