# Responsibility Matrix (RACI-style)

Derived directly from the recommended matrices in both business-context
interviews (Manuel §22, Markeris §21), reconciled into one table and
implemented as configurable `Role`/`Department`/`StageAssignment` data —
never hard-coded to a person's name (`ASSUMPTIONS.md` A2).

| Activity | Responsible | Support / Consulted | Approves |
|---|---|---|---|
| Forecast material need | Obra | Compras, Almacén | — |
| Arrival calendar | Compras / Logística | Almacén | — |
| Storage plan | Almacén | Obra, Dirección | — |
| Space preparation | Almacén | Obra | — |
| Quotation | Compras | Obra (specs) | Dirección |
| Purchase order | Compras | Almacén, Obra | Dirección (budget) |
| Payment approval | Finanzas | Compras | Dirección |
| Shipment / origin documentation | Compras / China-Origen (Harrison, Edison) | Logística | Harrison (origin release) |
| Physical unloading | Almacén | Compras | — |
| Counting | Almacén | Obra (witness, when applicable) | — |
| Inspection | Almacén | Obra / Arquitectura (technical) | — |
| System entry (receipt posting) | Almacén | — | Manuel (if delegated to Óscar) |
| Custody | Almacén | — | — |
| Material request | Obra | — | Assigned approver per project |
| Dispatch | Almacén | Obra (requester) | — |
| Building transfer | Almacén (records) | Obra (requests) | Finanzas (cost review) |
| Supplier claim | Compras | Almacén, Obra (evidence) | — |
| Inventory reconciliation | Contabilidad / Finanzas | Almacén | — |
| Container closure | Almacén | Compras, Obra | Recibe reporte: Dirección |

## Named pilot mapping (configuration, not logic)

| Person | Roles (`Role.code`) | Department |
|---|---|---|
| Harrison | `direccion`, `china_origin`, `management` | Dirección |
| Edison | `china_origin` | China / Origen |
| Markeris | `compras` | Compras |
| Lucía | `finanzas` | Finanzas |
| Manuel | `almacen`, `recepcion` | Almacén |
| Óscar | `recepcion` | Almacén |
| Miguel | `obra` | Obra |
| María Luisa | `direccion`, `management` | Dirección |

Configurable additional roles present in the seed data but not yet
assigned to a named pilot user: `customs_logistics`, `read_only`,
`system_admin`. Adding a new person with any of these roles requires no
code change — only a `UserRole` row.

## Core principle enforced here

"Responsibility follows control" (core principle 4.6): Compras' modeled
responsibility ends at `Handoff` acceptance by Almacén; Almacén's
responsibility is quantity/condition/location from that acceptance
forward; Obra's responsibility begins at `Delivery` acceptance. No model
in this system lets one department's action silently discharge another's
— every transition requires the *incoming* party's own action
(`HandoffDecision`, `Receipt`, `ProjectReceipt`, `AcceptanceRecord`).
