from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from .models import (
    Container,
    ManifestPurpose,
    ManifestVariance,
    Shipment,
)


@login_required
def shipment_list(request):
    shipments = (
        Shipment.objects.filter(organization=request.user.profile.organization)
        .prefetch_related("containers", "bills_of_lading")
        .order_by("-created_at")
    )
    return render(request, "shipments/list.html", {"shipments": shipments})


@login_required
def shipment_detail(request, pk):
    """The flagship dual-manifest screen (spec section 13A.8): shows the
    Official Carrier Summary (verbatim from the BL) side by side with the
    Internal Operational Manifest (fully decomposed, sourced lines), plus
    the official-vs-operational variance matrix."""

    shipment = get_object_or_404(
        Shipment.objects.filter(organization=request.user.profile.organization).prefetch_related(
            "containers", "bills_of_lading"
        ),
        pk=pk,
    )

    official_manifest = shipment.manifests.filter(purpose=ManifestPurpose.OFFICIAL_CARRIER_SUMMARY).first()
    internal_manifest = shipment.manifests.filter(purpose=ManifestPurpose.INTERNAL_OPERATIONAL_MANIFEST).first()

    official_version = official_manifest.versions.order_by("-version_number").first() if official_manifest else None
    internal_version = internal_manifest.versions.order_by("-version_number").first() if internal_manifest else None

    official_lines = official_version.lines.all() if official_version else []
    internal_lines = (
        internal_version.lines.select_related("item", "building", "unit").prefetch_related("sources", "allocations")
        if internal_version
        else []
    )

    variances = (
        ManifestVariance.objects.filter(shipment=shipment)
        .select_related("internal_manifest_line", "customs_review")
        .order_by("-severity")
    )

    bl_first = shipment.bills_of_lading.first()
    official_packages = bl_first.declared_packages if bl_first else None
    official_weight = bl_first.declared_gross_weight_kg if bl_first else None
    official_cbm = bl_first.declared_cbm if bl_first else None
    internal_packages = sum((l.packages or 0) for l in internal_lines)
    internal_weight = sum((l.gross_weight_kg or 0) for l in internal_lines)
    internal_cbm = sum((l.cbm or 0) for l in internal_lines)

    totals = {
        "official_packages": official_packages,
        "internal_packages": internal_packages,
        "packages_diff": (official_packages - internal_packages) if official_packages is not None else None,
        "official_weight": official_weight,
        "internal_weight": internal_weight,
        "weight_diff": (official_weight - internal_weight) if official_weight is not None else None,
        "official_cbm": official_cbm,
        "internal_cbm": internal_cbm,
        "cbm_diff": (official_cbm - internal_cbm) if official_cbm is not None else None,
    }

    return render(
        request,
        "shipments/detail.html",
        {
            "shipment": shipment,
            "bl": shipment.bills_of_lading.first(),
            "official_lines": official_lines,
            "internal_lines": internal_lines,
            "variances": variances,
            "totals": totals,
            "unresolved_critical_count": variances.filter(severity="critical", is_explained=False).count(),
        },
    )
