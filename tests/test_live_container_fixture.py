"""Integration test for the mandatory live-container acceptance fixture
(spec section 13A.10). Runs the actual management commands end to end
against a fresh test database and asserts the real facts documented in
docs/source-package-analysis.md.
"""

import pytest
from django.core.management import call_command

from apps.items.models import Item
from apps.matching.models import Discrepancy, DiscrepancyType, MatchCandidate, MatchStatus
from apps.procurement.models import PurchaseOrder
from apps.shipments.models import (
    BillOfLading,
    CargoDeclarationStatus,
    ManifestPurpose,
    ManifestVariance,
    OpenCommitmentLine,
    Shipment,
)


@pytest.fixture
def imported_fixture(db):
    call_command("seed_pilot_data")
    call_command("import_live_container_fixture")
    return Shipment.objects.get(reference="MEDUWY575021")


@pytest.mark.django_db
def test_official_bl_preserved_verbatim(imported_fixture):
    bl = BillOfLading.objects.get(shipment=imported_fixture)
    assert bl.bl_number == "MEDUWY575021"
    assert bl.declared_packages == 520
    assert bl.declared_gross_weight_kg == 22500
    assert bl.declared_cbm == 40
    assert "QUARTZ STONE COUNTERTOP" in bl.official_cargo_description
    assert "WOODEN DOORS" in bl.official_cargo_description
    assert "KITCHEN CABINET" in bl.official_cargo_description


@pytest.mark.django_db
def test_official_manifest_is_not_edited_to_match_internal(imported_fixture):
    """Core principle 4.9 / spec 13A.1: the official manifest line stays a
    broad, verbatim category — it is never decomposed in place."""
    official_manifest = imported_fixture.manifests.get(purpose=ManifestPurpose.OFFICIAL_CARRIER_SUMMARY)
    version = official_manifest.versions.get(version_number=1)
    assert version.lines.count() == 1
    assert version.lines.first().declaration_status == CargoDeclarationStatus.CLEARLY_REPRESENTED


@pytest.mark.django_db
def test_internal_manifest_decomposes_broad_categories(imported_fixture):
    internal_manifest = imported_fixture.manifests.get(purpose=ManifestPurpose.INTERNAL_OPERATIONAL_MANIFEST)
    version = internal_manifest.versions.get(version_number=1)
    assert version.lines.count() == 11


@pytest.mark.django_db
def test_unattributed_cargo_flagged_for_customs_review(imported_fixture):
    """The 'GENERAL' 200-unit accessories line has no PO/PI source
    anywhere in the fixture and must be flagged, never silently accepted."""
    flagged = ManifestVariance.objects.filter(
        shipment=imported_fixture, customs_review_required=True
    )
    assert flagged.count() == 3
    for variance in flagged:
        assert hasattr(variance, "customs_review")


@pytest.mark.django_db
def test_quartz_slab_and_fabricated_top_are_distinct_identities(imported_fixture):
    """Spec section 14.1 / acceptance scenario 6: a slab and a fabricated
    top must never be treated as the same product merely because both are
    quartz."""
    slab = Item.objects.get(name__icontains="Plancha de cuarzo")
    fabricated = Item.objects.get(name__icontains="Tope de cuarzo fabricado (APT-D/F/C/E)")
    assert slab.id != fabricated.id
    assert slab.physical_form == Item.PhysicalForm.RAW_MATERIAL
    assert fabricated.physical_form == Item.PhysicalForm.FABRICATED_PIECE


@pytest.mark.django_db
def test_w5057_w5097_is_possible_candidate_not_auto_merged(imported_fixture):
    """Core principle 4.3: no silent confirmation. The likely-same
    reference must stay a human-reviewable candidate."""
    candidate = MatchCandidate.objects.get(status=MatchStatus.POSSIBLE_CANDIDATE)
    assert candidate.status != MatchStatus.MANUALLY_CONFIRMED
    assert "W5057" in str(candidate.conflicting_fields)


@pytest.mark.django_db
def test_packed_vs_ordered_quartz_quantity_discrepancy_recorded(imported_fixture):
    disc = Discrepancy.objects.get(
        shipment=imported_fixture, discrepancy_type=DiscrepancyType.PACKED_QUANTITY_MISMATCH
    )
    assert disc.severity == "critical"
    assert not disc.is_resolved


@pytest.mark.django_db
def test_open_commitment_carryover_for_w5084_kitchens(imported_fixture):
    """Spec section 13A.4: PI W5084 totals 40 kitchen sets; only 10 are in
    this container. The remaining 30 must stay open, not silently closed."""
    po = PurchaseOrder.objects.get(po_number="DT-BEACH804")
    line = OpenCommitmentLine.objects.get(open_commitment__purchase_order=po)
    assert line.quantity_original == 40
    assert line.quantity_allocated_current_shipment == 10
    assert line.quantity_open == 30


@pytest.mark.django_db
def test_local_pos_received_in_full_stamp_is_not_treated_as_receipt(imported_fixture):
    """All 5 local POs in the fixture bear a 'RECEIVED IN FULL' stamp
    applied before physical arrival — this must be recorded as document
    metadata only, never as receiving evidence (core principle 4.3)."""
    stamped_pos = PurchaseOrder.objects.filter(document_marked_received_in_full=True)
    assert stamped_pos.count() == 5
    from apps.receiving.models import Receipt
    assert Receipt.objects.count() == 0, "No PO should have created a Receipt merely from its stamp."


@pytest.mark.django_db
def test_replacement_cargo_not_counted_as_new_purchase(imported_fixture):
    from apps.shipments.models import ReplacementCase
    cases = ReplacementCase.objects.all()
    assert cases.count() == 2
    door_case = cases.get(defect_or_drawing_error__icontains="17 al 22")
    assert door_case.replacement_manifest_line.description.startswith("Puertas MDF")
