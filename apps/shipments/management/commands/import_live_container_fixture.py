"""Imports the representative live-container fixture (BL MEDUWY575021 /
container TCNU8926924) described in docs/source-package-analysis.md.

Every figure here was read by a human from the actual supplied source
documents during Phase 0 analysis (see that document for the full
citation of which PDF/XLSX each fact came from) — nothing is invented.
Ambiguities the source documents themselves left unresolved (the
W5057/W5097 slab reference, the Sole-26 apartment-labeling conflict, the
unattributed "GENERAL" accessories line) are imported *as* ambiguities:
Possible Candidate matches, Customs & Logistics review flags, and open
discrepancies — never silently resolved.

This is the mandatory acceptance fixture required by spec section 13A.10.
"""

from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Organization
from apps.core.models import DestinationScope, Severity
from apps.cost.models import CostCharge, CostDocument, Currency
from apps.documents.models import Document, DocumentType, DocumentVersion
from apps.items.models import Item, ItemAlias, ProductCategory, UnitOfMeasure
from apps.matching.models import MatchCandidate, MatchRun, MatchStatus
from apps.procurement.models import PaymentMilestone, PurchaseOrder, PurchaseOrderLine, Quotation, QuotationLine, Supplier
from apps.projects.models import Building, Project
from apps.shipments.models import (
    BillOfLading,
    CargoDeclarationStatus,
    Container,
    CustomsReviewDecision,
    ManifestLine,
    ManifestPurpose,
    ManifestVariance,
    OpenCommitment,
    OpenCommitmentLine,
    ReplacementCase,
    Shipment,
    ShipmentManifest,
    ShipmentManifestVersion,
)
from apps.matching.models import Discrepancy, DiscrepancyType

User = get_user_model()


def _placeholder_document(org, code, name, title, user):
    doc_type, _ = DocumentType.objects.get_or_create(organization=org, code=code, defaults={"name": name})
    doc, created = Document.objects.get_or_create(
        organization=org, document_type=doc_type, title=title,
        defaults={"uploaded_by": user, "created_by": user, "human_confirmed_type": True},
    )
    if created:
        DocumentVersion.objects.create(
            document=doc, version_number=1, stored_name=f"placeholder-{doc.id}.txt",
            original_filename=f"{title}.txt", sha256="0" * 64, size_bytes=0,
            uploaded_by=user, created_by=user,
        )
    return doc


class Command(BaseCommand):
    help = "Imports the representative live-container fixture (BL MEDUWY575021 / TCNU8926924)."

    @transaction.atomic
    def handle(self, *args, **options):
        org = Organization.objects.first()
        if org is None:
            self.stderr.write(self.style.ERROR("Run `manage.py seed_pilot_data` first."))
            return
        harrison = User.objects.filter(username="harrison").first()
        markeris = User.objects.filter(username="markeris").first()

        usd, _ = Currency.objects.get_or_create(code="USD", defaults={"name": "US Dollar"})
        Currency.objects.get_or_create(code="CNY", defaults={"name": "Chinese Yuan"})

        set_uom, _ = UnitOfMeasure.objects.get_or_create(organization=org, code="SET", defaults={"name": "Juego / Set"})
        pc_uom, _ = UnitOfMeasure.objects.get_or_create(organization=org, code="PC", defaults={"name": "Pieza"})

        quartz_cat, _ = ProductCategory.objects.get_or_create(organization=org, name="Cuarzo y piedra")
        doors_cat, _ = ProductCategory.objects.get_or_create(organization=org, name="Puertas")
        kitchen_cat, _ = ProductCategory.objects.get_or_create(organization=org, name="Cocinas")
        vanity_cat, _ = ProductCategory.objects.get_or_create(organization=org, name="Vanities")
        other_cat, _ = ProductCategory.objects.get_or_create(organization=org, name="Otros / sin clasificar")

        # -- Suppliers --------------------------------------------------
        boman, _ = Supplier.objects.get_or_create(
            organization=org, name="Guangzhou Boman Building Materials Co., Ltd", defaults={"country": "China"}
        )
        yekalon, _ = Supplier.objects.get_or_create(
            organization=org, name="Yekalon Industry, Inc.", defaults={"country": "China"}
        )

        # -- Projects / buildings ----------------------------------------
        arena, _ = Project.objects.get_or_create(organization=org, code="arena", defaults={"name": "Arena"})
        palmera, _ = Project.objects.get_or_create(organization=org, code="palmera", defaults={"name": "Palmera"})
        arena9 = Building.objects.get_or_create(project=arena, code="arena-9", defaults={"name": "Arena #9"})[0]
        arena10 = Building.objects.get_or_create(project=arena, code="arena-10", defaults={"name": "Arena #10"})[0]
        arena11 = Building.objects.get_or_create(project=arena, code="arena-11", defaults={"name": "Arena #11"})[0]
        arena12 = Building.objects.get_or_create(project=arena, code="arena-12", defaults={"name": "Arena #12"})[0]
        edif26 = Building.objects.get_or_create(project=arena, code="edificio-26", defaults={"name": "Edificio 26"})[0]
        # Sole-26: PI W5084 itself says "PROYECT NAME: ARENA" while the matched
        # local PO says "USO: EDIFICIO SOLE 26" and the internal CI/PL calls the
        # same lines "SOLE-26 APT-A/APT-B" -- modeled as a building under Arena
        # per the weight of fixture evidence, WITHOUT deleting the conflict (see
        # docs/source-package-analysis.md section 5, item 2).
        sole26 = Building.objects.get_or_create(project=arena, code="sole-26", defaults={"name": "Sole-26"})[0]
        edif_17_22 = Building.objects.get_or_create(
            project=arena, code="edificios-17-22", defaults={"name": "Edificios 17 al 22"}
        )[0]

        # -- Items (deliberately kept as DISTINCT identities per spec 14.1) --
        quartz_slab = Item.objects.get_or_create(
            organization=org, name="Plancha de cuarzo JY101 3200x1600x20mm",
            defaults={"category": quartz_cat, "physical_form": Item.PhysicalForm.RAW_MATERIAL, "base_unit": pc_uom,
                      "dimensions": "3200x1600x20mm", "material": "Cuarzo"},
        )[0]
        ItemAlias.objects.get_or_create(item=quartz_slab, alias_text="W5097", defaults={"source": "PI"})
        ItemAlias.objects.get_or_create(item=quartz_slab, alias_text="W5057", defaults={"source": "CI/PL interno"})

        fab_top_a = Item.objects.get_or_create(
            organization=org, name="Tope de cuarzo fabricado (APT-D/F/C/E)",
            defaults={"category": quartz_cat, "physical_form": Item.PhysicalForm.FABRICATED_PIECE, "base_unit": pc_uom,
                      "dimensions": "1750x1200x800mm"},
        )[0]
        replacement_top = Item.objects.get_or_create(
            organization=org, name="Tope de cuarzo — reemplazo Arena (C/F K2-2, G K1-2, B/E K2-1)",
            defaults={"category": quartz_cat, "physical_form": Item.PhysicalForm.FABRICATED_PIECE, "base_unit": pc_uom},
        )[0]

        wooden_door = Item.objects.get_or_create(
            organization=org, name="Puerta MDF + marco WPC (reposición edificios 17-22)",
            defaults={"category": doors_cat, "physical_form": Item.PhysicalForm.COMPONENT, "base_unit": set_uom,
                      "dimensions": "2100x980mm"},
        )[0]

        kitchen_a = Item.objects.get_or_create(
            organization=org, name="Cocina modular — Sole-26 APT-A",
            defaults={"category": kitchen_cat, "physical_form": Item.PhysicalForm.KIT, "base_unit": set_uom},
        )[0]
        kitchen_b = Item.objects.get_or_create(
            organization=org, name="Cocina modular — Sole-26 APT-B",
            defaults={"category": kitchen_cat, "physical_form": Item.PhysicalForm.KIT, "base_unit": set_uom},
        )[0]
        vanity_v1 = Item.objects.get_or_create(
            organization=org, name="Vanity GH-V1", defaults={"category": vanity_cat, "base_unit": set_uom},
        )[0]
        vanity_v2 = Item.objects.get_or_create(
            organization=org, name="Vanity GH-V2", defaults={"category": vanity_cat, "base_unit": set_uom},
        )[0]
        general_accessories = Item.objects.get_or_create(
            organization=org, name="Accesorios generales (sin referencia documental)",
            defaults={"category": other_cat, "base_unit": set_uom},
        )[0]

        # -- Quotations (China-side PIs) ---------------------------------
        q_w5082 = Quotation.objects.get_or_create(
            organization=org, supplier=boman, reference="W5082",
            defaults={"project_label_on_document": "PALMERA", "project": palmera, "currency": "USD",
                      "total_amount": Decimal("162401.11"), "trade_terms": "EXW"},
        )[0]
        q_w5076 = Quotation.objects.get_or_create(
            organization=org, supplier=boman, reference="W5076",
            defaults={"project_label_on_document": "SOL-26 & ARENA 9-12", "project": arena, "currency": "USD",
                      "total_amount": Decimal("293561.67")},
        )[0]
        q_w5097 = Quotation.objects.get_or_create(
            organization=org, supplier=boman, reference="W5097",
            defaults={"project_label_on_document": "", "currency": "USD", "total_amount": Decimal("6830.08")},
        )[0]
        q_w5084 = Quotation.objects.get_or_create(
            organization=org, supplier=boman, reference="W5084",
            defaults={"project_label_on_document": "ARENA", "project": arena, "currency": "USD",
                      "total_amount": Decimal("51559.74")},
        )[0]
        q_yekalon = Quotation.objects.get_or_create(
            organization=org, supplier=yekalon, reference="PI-60044678",
            defaults={"currency": "USD", "total_amount": Decimal("14108.07")},
        )[0]

        # -- Local purchase orders (Markeris), matched 1:1 to the PIs above --
        po_786 = PurchaseOrder.objects.get_or_create(
            organization=org, supplier=boman, po_number="DT-BEACH786",
            defaults={
                "quotation": q_w5076, "project": arena, "order_date": date(2025, 8, 25),
                "currency": "USD", "total_amount": Decimal("293563.00"), "requested_by": "María Luisa",
                "usage_note_on_document": "USO: EDIFICIO ARENA 9,10,11,12 / EDIFICIO 26",
                "document_marked_received_in_full": True,
                "intended_destination_scope": DestinationScope.BUILDING,
                "approval_status": PurchaseOrder.ApprovalStatus.APPROVED,
            },
        )[0]
        po_1260 = PurchaseOrder.objects.get_or_create(
            organization=org, supplier=boman, po_number="1260",
            defaults={
                "quotation": q_w5097, "order_date": date(2026, 3, 24),
                "currency": "USD", "total_amount": Decimal("6830.08"), "requested_by": "María Luisa",
                "usage_note_on_document": "USO: GENERAL DEL PROYECTO",
                "document_marked_received_in_full": True,
                "intended_destination_scope": DestinationScope.PROJECT,
                "approval_status": PurchaseOrder.ApprovalStatus.APPROVED,
            },
        )[0]
        po_804 = PurchaseOrder.objects.get_or_create(
            organization=org, supplier=boman, po_number="DT-BEACH804",
            defaults={
                "quotation": q_w5084, "project": arena, "building": sole26, "order_date": date(2025, 9, 2),
                "currency": "USD", "total_amount": Decimal("51559.76"), "requested_by": "María Luisa",
                "usage_note_on_document": "USO: EDIFICIO SOLE 26",
                "document_marked_received_in_full": True,
                "intended_destination_scope": DestinationScope.BUILDING,
                "approval_status": PurchaseOrder.ApprovalStatus.APPROVED,
            },
        )[0]
        po_793 = PurchaseOrder.objects.get_or_create(
            organization=org, supplier=boman, po_number="DT-BEACH793",
            defaults={
                "quotation": q_w5082, "project": palmera, "order_date": date(2025, 8, 27),
                "currency": "USD", "total_amount": Decimal("162401.05"), "requested_by": "María Luisa",
                "usage_note_on_document": "USO: COCINAS / CLOSET / MUEBLES DE BAÑO / PALMERA",
                "document_marked_received_in_full": True,
                "intended_destination_scope": DestinationScope.PROJECT,
                "approval_status": PurchaseOrder.ApprovalStatus.APPROVED,
            },
        )[0]
        po_1111 = PurchaseOrder.objects.get_or_create(
            organization=org, supplier=yekalon, po_number="DT-BEACH1111",
            defaults={
                "quotation": q_yekalon, "project": arena, "building": edif_17_22, "order_date": date(2026, 1, 19),
                "currency": "USD", "total_amount": Decimal("14108.13"), "requested_by": "María Luisa",
                "usage_note_on_document": "USO: PUERTAS ADICIONALES DEL EDIFICIO 17 AL 22 ÚNICAMENTE PARA REPOSICIÓN — IMPORTACIÓN / CIF",
                "document_marked_received_in_full": True,
                "intended_destination_scope": DestinationScope.BUILDING,
                "approval_status": PurchaseOrder.ApprovalStatus.APPROVED,
            },
        )[0]

        po_804_line, _ = PurchaseOrderLine.objects.get_or_create(
            purchase_order=po_804, line_no=1,
            defaults={"description": "Cocinas modulares Sole-26 (K01/K02/K03) — total 40 sets según PI W5084",
                      "item": kitchen_a, "quantity_ordered": Decimal("40"), "unit_of_measure": set_uom,
                      "unit_price": Decimal("1013.00"), "line_total": Decimal("40520.00"),
                      "destination_scope": DestinationScope.BUILDING, "building": sole26},
        )

        # -- Shipment / container / official BL --------------------------
        shipment = Shipment.objects.get_or_create(
            organization=org, reference="MEDUWY575021",
            defaults={
                "booking_reference": "181AY26S4210706J1", "carrier": "MSC",
                "origin_port": "Yantian, China", "destination_port": "Caucedo, Dominican Republic",
                "etd": date(2026, 6, 9), "eta": date(2026, 6, 30),
                "status": Shipment.Status.RECEIVED_WITH_EXCEPTIONS,
            },
        )[0]
        container = Container.objects.get_or_create(
            shipment=shipment, container_number="TCNU8926924",
            defaults={"seal_number": "FJ28053326", "container_type": "40HQ"},
        )[0]

        bl_doc = _placeholder_document(org, "bill_of_lading", "Conocimiento de Embarque", "BL MEDUWY575021", harrison)
        bl, _ = BillOfLading.objects.get_or_create(
            shipment=shipment, bl_number="MEDUWY575021",
            defaults={
                "shipper_name": "Guangzhou Boman Building Materials Co., Ltd",
                "consignee_name": "DT Beach, S.R.L.",
                "vessel_and_voyage": "MSC REGULUS - UX623A",
                "port_of_loading": "Yantian, China", "port_of_discharge": "Caucedo, Dominican Republic",
                "telex_release": True, "shipped_on_board_date": date(2026, 6, 9),
                "official_cargo_description": "QUARTZ STONE COUNTERTOP\nWOODEN DOORS\nKITCHEN CABINET",
                "declared_packages": 520, "declared_gross_weight_kg": Decimal("22500.000"),
                "declared_cbm": Decimal("40.0000"), "source_document": bl_doc,
            },
        )

        # -- Freight cost -------------------------------------------------
        freight_doc = _placeholder_document(org, "freight_invoice", "Factura de Flete", "Freight invoice GZYF2605529", harrison)
        cost_doc, _ = CostDocument.objects.get_or_create(
            shipment=shipment, cost_type=CostDocument.CostType.FREIGHT, invoice_reference="GZYF2605529",
            defaults={"document": freight_doc, "amount": Decimal("6900.00"), "currency": usd},
        )
        CostCharge.objects.get_or_create(
            cost_document=cost_doc, description="Ocean freight 1x40HQ",
            defaults={"amount": Decimal("6900.00")},
        )

        # -- Manifests: Official Carrier Summary (verbatim) --------------
        official_manifest, _ = ShipmentManifest.objects.get_or_create(
            shipment=shipment, purpose=ManifestPurpose.OFFICIAL_CARRIER_SUMMARY
        )
        official_version, _ = ShipmentManifestVersion.objects.get_or_create(
            manifest=official_manifest, version_number=1, defaults={"is_frozen": True}
        )
        official_line, _ = ManifestLine.objects.get_or_create(
            manifest_version=official_version, line_no=1,
            defaults={
                "description": "QUARTZ STONE COUNTERTOP / WOODEN DOORS / KITCHEN CABINET",
                "quantity": Decimal("520"), "unit_of_measure": pc_uom, "packages": 520,
                "gross_weight_kg": Decimal("22500.000"), "cbm": Decimal("40.0000"),
                "declaration_status": CargoDeclarationStatus.CLEARLY_REPRESENTED,
            },
        )

        # -- Manifests: Internal Operational Manifest (fully decomposed) --
        internal_manifest, _ = ShipmentManifest.objects.get_or_create(
            shipment=shipment, purpose=ManifestPurpose.INTERNAL_OPERATIONAL_MANIFEST
        )
        internal_version, _ = ShipmentManifestVersion.objects.get_or_create(
            manifest=internal_manifest, version_number=1, defaults={"is_frozen": True}
        )

        def make_line(no, description, item, qty, packages, gw, cbm, ref, dest_scope, building, status, value=None, zero_reason=""):
            return ManifestLine.objects.get_or_create(
                manifest_version=internal_version, line_no=no,
                defaults={
                    "description": description, "item": item, "reference_on_document": ref,
                    "destination_scope": dest_scope, "building": building,
                    "quantity": qty, "unit_of_measure": pc_uom if item and item.base_unit_id == pc_uom.id else set_uom,
                    "packages": packages, "gross_weight_kg": gw, "cbm": cbm,
                    "commercial_value": value, "value_is_zero_reason": zero_reason,
                    "declaration_status": status,
                },
            )[0]

        line_fab_a = make_line(
            1, "Tope de cuarzo fabricado APT-D/F/C/E (1750x1200x800)", fab_top_a, Decimal("5"), 1,
            Decimal("1824"), Decimal("1.6"), "W5084", DestinationScope.BUILDING, sole26,
            CargoDeclarationStatus.BROADER_DESCRIPTION, Decimal("1700"),
        )
        line_fab_type = make_line(
            2, 'Tope de cuarzo fabricado "TYPE" (1400x1100x1100)', fab_top_a, Decimal("5"), 1,
            Decimal("1685"), Decimal("1.5"), "W5085", DestinationScope.UNKNOWN_PENDING, None,
            CargoDeclarationStatus.NOT_REPRESENTED_REVIEW_REQUIRED, Decimal("1700"),
        )
        line_repl_1 = make_line(
            3, "Arena Countertop Replacement C/F(K2-2) G(K1-2) B/E(K2-1) — 2610x970x1155", replacement_top,
            Decimal("5"), 1, Decimal("1965"), Decimal("1.6"), "W5086", DestinationScope.BUILDING, None,
            CargoDeclarationStatus.REPLACEMENT_OR_WARRANTY, Decimal("1700"),
        )
        line_repl_2 = make_line(
            4, "Arena Countertop Replacement C/F(K2-2) G(K1-2) B/E(K2-1) — 1605x660x800", replacement_top,
            Decimal("5"), 1, Decimal("250"), Decimal("0.6"), "W5087", DestinationScope.BUILDING, None,
            CargoDeclarationStatus.REPLACEMENT_OR_WARRANTY, Decimal("1700"),
        )
        line_slabs = make_line(
            5, "Plancha de cuarzo JY101 3200x1600x20mm (20 planchas comerciales; ver discrepancia de empaque)",
            quartz_slab, Decimal("20"), 2, Decimal("4900"), Decimal("3.4"), "W5057 (¿= W5097?)",
            DestinationScope.PROJECT, None, CargoDeclarationStatus.PROJECT_WIDE_STOCK, Decimal("6830.08"),
        )
        line_doors = make_line(
            6, "Puertas MDF + marco WPC — reposición edificios 17 al 22 (35 principales + 35 dormitorio)",
            wooden_door, Decimal("70"), 275, Decimal("6665"), Decimal("17.81"), "DOC05226 / Yekalon PI-60044678",
            DestinationScope.BUILDING, edif_17_22, CargoDeclarationStatus.REPLACEMENT_OR_WARRANTY,
            zero_reason="", value=Decimal("5590.20"),
        )
        line_kitchen_a = make_line(
            7, "Cocina modular Sole-26 APT-A (5 sets, 100 cajas)", kitchen_a, Decimal("5"), 100,
            Decimal("2817"), Decimal("6.8"), "W5084", DestinationScope.BUILDING, sole26,
            CargoDeclarationStatus.BROADER_DESCRIPTION, Decimal("5631.58"),
        )
        line_kitchen_b = make_line(
            8, "Cocina modular Sole-26 APT-B (5 sets, 75 cajas)", kitchen_b, Decimal("5"), 75,
            Decimal("1970"), Decimal("5.4"), "W5084", DestinationScope.BUILDING, sole26,
            CargoDeclarationStatus.BROADER_DESCRIPTION, Decimal("5065.00"),
        )
        line_vanity_1 = make_line(
            9, "Vanity GH-V1 (15 sets)", vanity_v1, Decimal("15"), 15, Decimal("175"), Decimal("0.51"),
            "W5084", DestinationScope.BUILDING, sole26, CargoDeclarationStatus.BROADER_DESCRIPTION, Decimal("930.00"),
        )
        line_vanity_2 = make_line(
            10, "Vanity GH-V2 (15 sets)", vanity_v2, Decimal("15"), 15, Decimal("185"), Decimal("0.45"),
            "W5084", DestinationScope.BUILDING, sole26, CargoDeclarationStatus.BROADER_DESCRIPTION, Decimal("1620.00"),
        )
        line_general = make_line(
            11, "Accesorios generales (200 sets) — sin referencia de PO/PI en el CI/PL interno",
            general_accessories, Decimal("200"), 31, Decimal("12"), Decimal("0.1"), "(sin referencia)",
            DestinationScope.UNKNOWN_PENDING, None, CargoDeclarationStatus.NOT_REPRESENTED_REVIEW_REQUIRED,
            Decimal("4000.00"),
        )

        # -- Official vs operational variance matrix ----------------------
        def make_variance(internal_line, explained, severity, explanation, review_required):
            variance, _ = ManifestVariance.objects.get_or_create(
                shipment=shipment, internal_manifest_line=internal_line,
                defaults={
                    "official_category_text": "QUARTZ STONE COUNTERTOP / WOODEN DOORS / KITCHEN CABINET",
                    "is_explained": explained, "severity": severity, "explanation": explanation,
                    "customs_review_required": review_required,
                },
            )
            if review_required:
                CustomsReviewDecision.objects.get_or_create(manifest_variance=variance)
            return variance

        make_variance(line_fab_a, True, Severity.INFO, "Tope fabricado bajo la categoría oficial 'quartz stone countertop'.", False)
        make_variance(line_fab_type, False, Severity.CRITICAL, "Referencia W5085 no aparece en ningún PI/PO del expediente.", True)
        make_variance(line_repl_1, True, Severity.WARNING, "Carga de reemplazo vinculada a caso de reemplazo Arena.", False)
        make_variance(line_repl_2, True, Severity.WARNING, "Carga de reemplazo vinculada a caso de reemplazo Arena.", False)
        make_variance(
            line_slabs, False, Severity.CRITICAL,
            "PO 1260 / PI W5097 ordenan 20 planchas; la lista de empaque detallada solo desglosa 19 piezas físicas "
            "(10+9). Posible referencia cruzada W5057=W5097 sin confirmar.",
            True,
        )
        make_variance(line_doors, True, Severity.WARNING, "Puertas de reposición vinculadas a PI Yekalon 60044678, no es compra nueva.", False)
        make_variance(line_kitchen_a, True, Severity.INFO, "Cocina bajo la categoría oficial 'kitchen cabinet'.", False)
        make_variance(line_kitchen_b, True, Severity.INFO, "Cocina bajo la categoría oficial 'kitchen cabinet'.", False)
        make_variance(line_vanity_1, True, Severity.INFO, "Vanity bajo la categoría oficial 'kitchen cabinet'.", False)
        make_variance(line_vanity_2, True, Severity.INFO, "Vanity bajo la categoría oficial 'kitchen cabinet'.", False)
        make_variance(
            line_general, False, Severity.CRITICAL,
            "Línea de 200 accesorios sin PO/PI de origen identificado en ningún documento del expediente.",
            True,
        )

        # -- Open commitment / carryover: W5084 kitchens (40 total, only 10 in this container) --
        open_commitment, _ = OpenCommitment.objects.get_or_create(
            purchase_order=po_804, defaults={"status": OpenCommitment.Status.PARTIALLY_FULFILLED}
        )
        OpenCommitmentLine.objects.get_or_create(
            open_commitment=open_commitment, purchase_order_line=po_804_line,
            defaults={
                "quantity_original": Decimal("40"), "quantity_approved": Decimal("40"),
                "quantity_allocated_current_shipment": Decimal("10"),
                "quantity_loaded_current_shipment": Decimal("10"),
            },
        )

        # -- Replacement cases ---------------------------------------------
        ReplacementCase.objects.get_or_create(
            original_purchase_order_line=None, replacement_manifest_line=line_doors,
            defaults={
                "defect_or_drawing_error": "Reposición de puertas para edificios 17 al 22 (PI Yekalon 60044678)",
                "supplier_responsibility": ReplacementCase.ResponsibilityParty.UNKNOWN,
                "charge_type": ReplacementCase.ChargeType.PAID,
                "destination_building": edif_17_22,
                "claim_status": "No aplica — reposición planificada, no reclamo por defecto de fábrica confirmado.",
            },
        )
        ReplacementCase.objects.get_or_create(
            original_purchase_order_line=None, replacement_manifest_line=line_repl_1,
            defaults={
                "defect_or_drawing_error": "Arena Countertop Replacement C/F(K2-2) G(K1-2) B/E(K2-1)",
                "supplier_responsibility": ReplacementCase.ResponsibilityParty.UNKNOWN,
                "charge_type": ReplacementCase.ChargeType.FREE,
            },
        )

        # -- Matching: W5057 vs W5097 possible-candidate, never auto-merged --
        match_run = MatchRun.objects.create(notes="Fixture: posible coincidencia W5057 (CI/PL interno) vs W5097 (PI Boman)")
        from django.contrib.contenttypes.models import ContentType
        item_ct = ContentType.objects.get_for_model(Item)
        MatchCandidate.objects.get_or_create(
            match_run=match_run,
            left_content_type=item_ct, left_object_id=quartz_slab.id,
            right_content_type=item_ct, right_object_id=quartz_slab.id,
            defaults={
                "status": MatchStatus.POSSIBLE_CANDIDATE,
                "agreeing_fields": ["unit_price_usd=66.7", "size_sqm=5.12", "dimensions=3200x1600x20mm"],
                "conflicting_fields": ["reference_text: 'W5057' vs 'W5097'"],
                "quantity_relationship": "20 = 20 (coincide en cantidad comercial)",
                "explanation": "Mismo precio unitario y tamaño; referencia difiere en un dígito. Podría ser error "
                               "de transcripción del CI/PL interno. Requiere confirmación humana antes de fusionar.",
            },
        )

        # -- Discrepancy: 20 ordered vs 19 physically packed --------------
        Discrepancy.objects.get_or_create(
            shipment=shipment, discrepancy_type=DiscrepancyType.PACKED_QUANTITY_MISMATCH,
            defaults={
                "severity": Severity.CRITICAL,
                "description": "PO 1260 / PI W5097 ordenan 20 planchas de cuarzo; la lista de empaque detallada "
                               "(Replacement & Slabs of Quartz.xlsx) solo desglosa 19 piezas físicas (10+9).",
            },
        )

        self.stdout.write(self.style.SUCCESS(
            f"Fixture importado: {shipment.reference} / {container.container_number} — "
            f"{internal_version.lines.count()} líneas internas, {ManifestVariance.objects.filter(shipment=shipment).count()} variaciones."
        ))
