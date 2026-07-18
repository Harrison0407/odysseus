# Canonical Data Model

Full model source is authoritative; this is a navigable summary organized
the same way as spec section 12. All models use UUID primary keys and
inherit timestamps + creator from `apps.core.models.BaseModel` unless noted.

## Organization and access — `apps.accounts`, `apps.projects`
`Organization`, `Department`, `Role`, `UserProfile`, `UserRole`,
`UserProjectAccess`, `ResponsibilityAssignment`, `Delegation`,
`Project`, `Building`, `Floor`, `Unit`, `Area`, `ProjectMilestone`.

## Documents and provenance — `apps.documents`
`DocumentType`, `DocumentTypeAlias`, `SupplierDocumentProfile`,
`RequiredDocumentRule`, `Document`, `DocumentVersion` (immutable, SHA-256,
duplicate-detection via `is_duplicate_of`), `DocumentClassificationResult`,
`HumanConfirmedDocumentType`, `DocumentFieldSource` (the full provenance
chain: source → extracted → normalized → proposed/confirmed translation →
canonical, with confidence and confirming user/time).

## Procurement — `apps.procurement`
`Supplier`, `SupplierContact`, `Quotation`, `QuotationLine`,
`PurchaseOrder` (carries `document_marked_received_in_full` and
`usage_note_on_document` to preserve exactly what the source PO said),
`PurchaseOrderRevision`, `PurchaseOrderLine`, `PaymentTerm`,
`PaymentMilestone`, `PaymentRecord`, `PaymentEvidence`,
`OpenPurchaseOrderRelease`.

## Items and complex products — `apps.items`
`UnitOfMeasure`, `UnitConversion`, `ProductCategory`, `ProductRiskProfile`,
`Item` (with `physical_form`: raw_material / fabricated_piece / component /
finished_unit / kit — the field that keeps a quartz slab and a fabricated
countertop from ever being confused), `ItemAlias`, `ItemVariant`,
`AssemblyDefinition`, `AssemblyComponent`, `KitDefinition`, `KitInstance`,
`Package`, `PackageComponent`, `ItemPhoto`, `TechnicalDrawing`.

## Supplier/shipment documents and the dual-manifest engine — `apps.shipments`
`SupplierInvoice`, `SupplierInvoiceLine`, `PackingList`, `PackingListLine`,
`TechnicalPackingList`, `Shipment`, `ShipmentLeg`, `Container`,
`BillOfLading` (official, immutable), `ShipmentLineAllocation`,
`ShipmentManifest` + `ShipmentManifestVersion` (`ManifestPurpose`:
official_carrier_summary / official_customs_cargo_set /
internal_operational_manifest / detailed_receiving_manifest /
warehouse_putaway_manifest / historical_reconstruction), `ManifestLine`,
`ManifestLineSource`, `ManifestLineAllocation`, `ManifestVariance`,
`CustomsReviewDecision`, `ContainerLoadEvent`, `ContainerLoadEvidence`,
`ContainerSpaceAddition`, `OpenCommitment`, `OpenCommitmentLine`,
`FulfillmentEvent`, `PriorFulfillmentEvidence`,
`ShipmentCarryoverAllocation`, `ReplacementCase`, `CorrectiveActionCase`,
`WarrantyOrNoChargeLine`, `LoadingEvidence`, `LogisticsQuotation`,
`LogisticsOption`.

## Customs and CONFOTUR — `apps.customs`
`CustomsDeclaration`, `CustomsLiquidation`, `ConfoturList`, `ConfoturLine`
(with `is_duplicate_of` for duplicate-exemption detection),
`ConfoturApproval`, `ExemptionAllocation`, `ImportEntry`,
`CustomsDocumentLink`.

## Cost — `apps.cost`
`Currency`, `ExchangeRate`, `CostDocument` (with `is_duplicate_of` for
duplicate-invoice detection), `CostCharge`, `CostAllocationRun`,
`CostAllocationLine`, `LandedCostVersion`, `LandedCostLine`.

## Matching and exceptions — `apps.matching`
`MatchRun`, `MatchCandidate` (`MatchStatus`: exact / strong_candidate /
possible_candidate / manually_confirmed / unmatched / conflict /
superseded), `ConfirmedMatch`, `QuantityReconciliation` (the full
required→quoted→ordered→paid→produced→packed→shipped→expected→received→
accepted→installed chain, spec core principle 4.2), `Discrepancy`
(`DiscrepancyType`, 30 values covering every case in spec section 13),
`DiscrepancyResolution`, `Waiver`.

## Receiving planning and physical receiving — `apps.receiving`
`ReceivingPlan`, `ReceivingPlanLine`, `StorageCapacityReservation`,
`UnloadingRoute`, `EquipmentRequirement`, `PersonnelAssignment`,
`SamplingRule`, `InspectionPlan`, `AlternativeStorageOption`,
`ReleasePacket` + `ReleasePacketVersion` (versioned, frozen expected
quantities), `Receipt`, `ReceiptLine`, `ReceiptPackage`, `Inspection`,
`InspectionSample`, `ReceiptEvidence`, `DamageRecord`,
`UnidentifiedMaterial`, `ContainerClosureAct`.

## Locations and inventory — `apps.inventory`
`StorageSite`, `WarehouseZone`, `WarehouseLocation`, `LocationSuitability`,
`LocationCapacity`, `InventoryLot`, `InventoryMovement` (append-only
ledger; `MovementType` covers receipt/quarantine/put-away/transfer/
reservation/pick/dispatch/project-delivery/return/adjustment/damage/
write-off/installation-consumption/reclassification/historical-opening-
balance), `InventoryReservation`, `QuarantineRecord`, `CycleCount`,
`CycleCountLine`, `InventoryAdjustment`.

## Requests and project delivery — `apps.requests`
`MaterialRequest`, `MaterialRequestLine`, `RequestApproval`, `PickList`,
`Dispatch`, `DispatchLine`, `Delivery`, `DeliveryLine`, `ProjectReceipt`,
`Return`, `Transfer`, `DestinationReassignment`, `InstallationRecord`,
`InspectionRecord`, `AcceptanceRecord`.

## Local stock and tools — `apps.tools`
`Tool`, `ToolAssignment`, `ToolCheckout`, `ToolReturn`, `ToolRepair`.
(`StockPolicy`/`ReorderPoint`/`ReplenishmentSuggestion`/`EmergencyPurchase`
are Priority 1 and not yet modeled — see `KNOWN_LIMITATIONS.md`.)

## Governance, evidence, sharing — `apps.audit`, `apps.reports`, `apps.workflow`
`AuditEvent`, `Comment`, `Attachment`, `Notification`,
`WorkflowStage`, `ServiceLevelTarget`, `StageAssignment`, `Handoff`,
`HandoffChecklist`, `HandoffEvidence`, `HandoffDecision`,
`ReportVersion`, `SecureShareLink`, `ShareSnapshot`.

## Design invariant recap

- `DestinationScope` (organization/project/zone/building/floor/unit/
  common_area/warehouse_stock/replacement_reserve/unknown_pending) is a
  shared enum used on `PurchaseOrderLine`, `ManifestLine`, and
  `InventoryLot` so "project-wide stock" is a first-class value everywhere,
  never a null hack.
- `Severity` (info/warning/critical) and `ConfidenceLevel`
  (confirmed/high/medium/low/unknown) are shared enums used consistently
  across `Discrepancy`, `ManifestVariance`, `DocumentFieldSource`, and
  `MatchCandidate`.
