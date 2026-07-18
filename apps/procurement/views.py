from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.shortcuts import get_object_or_404, render

from apps.workflow.models import GateDefinition, Handoff

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
    content_type = ContentType.objects.get_for_model(PurchaseOrder)
    available_gates = GateDefinition.objects.filter(
        organization=request.user.profile.organization,
        target_content_type=content_type,
        code__in=["purchasing_to_finance", "finance_to_logistics"],
    )
    existing_handoffs = Handoff.objects.filter(
        content_type=content_type, object_id=order.pk
    ).select_related("gate_definition", "to_department").order_by("-created_at")
    return render(request, "procurement/po_detail.html", {
        "order": order,
        "po_content_type_id": content_type.id,
        "available_gates": available_gates,
        "existing_handoffs": existing_handoffs,
    })
