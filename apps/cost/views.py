from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from .models import LandedCostVersion


@login_required
def landed_cost_list(request):
    versions = LandedCostVersion.objects.select_related("shipment").order_by("-created_at")
    return render(request, "cost/list.html", {"versions": versions})


@login_required
def landed_cost_detail(request, pk):
    version = get_object_or_404(
        LandedCostVersion.objects.select_related("shipment").prefetch_related("lines__manifest_line"), pk=pk
    )
    return render(request, "cost/detail.html", {"version": version})
