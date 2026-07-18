from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from .models import PurchaseOrder


@login_required
def po_list(request):
    orders = (
        PurchaseOrder.objects.filter(organization=request.user.profile.organization)
        .select_related("supplier", "quotation", "project", "building")
        .order_by("-order_date")
    )
    return render(request, "procurement/po_list.html", {"orders": orders})


@login_required
def po_detail(request, pk):
    order = get_object_or_404(
        PurchaseOrder.objects.select_related("supplier", "quotation", "project", "building").prefetch_related(
            "lines", "payment_milestones", "open_commitments__lines"
        ),
        pk=pk,
        organization=request.user.profile.organization,
    )
    return render(request, "procurement/po_detail.html", {"order": order})
