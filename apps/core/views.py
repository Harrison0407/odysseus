from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.matching.models import Discrepancy
from apps.procurement.models import PaymentMilestone, PurchaseOrder
from apps.receiving.models import Receipt
from apps.requests.models import MaterialRequest
from apps.shipments.models import ManifestVariance, Shipment
from apps.tools.models import ToolCheckout
from apps.workflow import services as workflow_services


@login_required
def dashboard_home(request):
    """Persona-specific workspace (spec section 27). The Spanish-language
    UI defaults each user to a useful view based on their configured
    roles — never on a hard-coded person's name."""

    role_codes = set(
        request.user.user_roles.filter(is_active=True).values_list("role__code", flat=True)
    )

    handoffs_awaiting_me = workflow_services.handoffs_awaiting_user(request.user).select_related(
        "from_department", "to_department", "gate_definition"
    )
    overdue_count = len(workflow_services.overdue_handoffs_for_user(request.user))

    context = {
        "role_codes": role_codes,
        "pending_handoffs_to_me": handoffs_awaiting_me[:20],
        "pending_handoffs_count": handoffs_awaiting_me.count(),
        "overdue_handoffs_count": overdue_count,
        "critical_discrepancies": Discrepancy.objects.filter(
            severity="critical", is_resolved=False
        ).select_related("shipment")[:20],
    }

    if "compras" in role_codes:
        context.update(
            purchase_orders_in_preparation=PurchaseOrder.objects.filter(
                approval_status=PurchaseOrder.ApprovalStatus.DRAFT
            )[:20],
            payment_milestones_pending=PaymentMilestone.objects.exclude(
                status=PaymentMilestone.Status.PAID
            ).select_related("purchase_order")[:20],
        )
        template = "core/dashboard_compras.html"
    elif "china_origin" in role_codes or "china_operations" in role_codes:
        context.update(
            shipments_awaiting_origin_release=Shipment.objects.filter(
                status=Shipment.Status.CHINA_ORIGIN_PREPARATION
            )[:20],
        )
        template = "core/dashboard_china.html"
    elif "finanzas" in role_codes:
        context.update(
            payment_milestones_pending=PaymentMilestone.objects.exclude(
                status=PaymentMilestone.Status.PAID
            ).select_related("purchase_order")[:20],
        )
        template = "core/dashboard_finanzas.html"
    elif "almacen" in role_codes or "recepcion" in role_codes:
        context.update(
            receipts_in_progress=Receipt.objects.exclude(
                status=Receipt.Status.MATCHED
            ).select_related("container")[:20],
            tools_overdue=ToolCheckout.objects.filter(
                actual_return_date__isnull=True
            ).select_related("assignment__tool")[:20],
        )
        template = "core/dashboard_almacen.html"
    elif "obra" in role_codes:
        context.update(
            my_requests=MaterialRequest.objects.filter(requester=request.user)[:20],
        )
        template = "core/dashboard_obra.html"
    elif "direccion" in role_codes or "management" in role_codes:
        context.update(
            unexplained_variances=ManifestVariance.objects.filter(
                is_explained=False
            ).select_related("shipment")[:20],
        )
        template = "core/dashboard_direccion.html"
    else:
        template = "core/dashboard_generic.html"

    return render(request, template, context)
