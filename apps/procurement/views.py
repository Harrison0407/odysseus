from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.shortcuts import get_object_or_404, redirect, render

from apps.drawings.models import Drawing
from apps.projects.models import Area, Building, BuildingFamily, Floor, Unit
from apps.workflow.models import GateDefinition, Handoff

from . import services
from .models import OrderLineAllocation, PurchaseOrder, PurchaseOrderLine, PurchasedSpare


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
    line_rows = [(line, services.line_allocation_summary(line)) for line in order.lines.all()]
    has_unresolved_excess = any(summary["unallocated_quantity"] > 0 for _, summary in line_rows)
    return render(request, "procurement/po_detail.html", {
        "order": order,
        "po_content_type_id": content_type.id,
        "available_gates": available_gates,
        "existing_handoffs": existing_handoffs,
        "line_rows": line_rows,
        "has_unresolved_excess": has_unresolved_excess,
    })


class AllocationForm(forms.ModelForm):
    class Meta:
        model = OrderLineAllocation
        fields = ["destination_scope", "building_family", "building", "floor", "unit", "area", "quantity", "drawing", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["building_family"].queryset = BuildingFamily.objects.filter(project__organization=organization)
        self.fields["building"].queryset = Building.objects.filter(project__organization=organization)
        self.fields["floor"].queryset = Floor.objects.filter(building__project__organization=organization)
        self.fields["unit"].queryset = Unit.objects.filter(building__project__organization=organization)
        self.fields["area"].queryset = Area.objects.filter(building__project__organization=organization)
        self.fields["drawing"].queryset = Drawing.objects.filter(project__organization=organization, is_current=True)
        for field in self.fields.values():
            field.required = False
            field.widget.attrs.setdefault("class", "form-control form-control-sm")
        self.fields["destination_scope"].required = True
        self.fields["quantity"].required = True


class ReassignmentForm(forms.Form):
    destination_scope = forms.ChoiceField(choices=OrderLineAllocation._meta.get_field("destination_scope").choices, required=False, label="Nuevo tipo de destino")
    building_family = forms.ModelChoiceField(queryset=BuildingFamily.objects.none(), required=False, label="Familia de edificio")
    building = forms.ModelChoiceField(queryset=Building.objects.none(), required=False, label="Edificio")
    floor = forms.ModelChoiceField(queryset=Floor.objects.none(), required=False, label="Piso")
    unit = forms.ModelChoiceField(queryset=Unit.objects.none(), required=False, label="Unidad")
    area = forms.ModelChoiceField(queryset=Area.objects.none(), required=False, label="Área común")
    reassignment_reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), label="Motivo de la reasignación")

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["building_family"].queryset = BuildingFamily.objects.filter(project__organization=organization)
        self.fields["building"].queryset = Building.objects.filter(project__organization=organization)
        self.fields["floor"].queryset = Floor.objects.filter(building__project__organization=organization)
        self.fields["unit"].queryset = Unit.objects.filter(building__project__organization=organization)
        self.fields["area"].queryset = Area.objects.filter(building__project__organization=organization)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


class ConfirmSpareForm(forms.Form):
    quantity = forms.DecimalField(max_digits=14, decimal_places=3, label="Cantidad de repuesto")
    compatible_typology = forms.CharField(max_length=255, required=False, label="Tipología/compatibilidad")
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), label="Razón")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


@login_required
def line_allocation_detail(request, pk):
    organization = request.user.profile.organization
    line = get_object_or_404(
        PurchaseOrderLine.objects.select_related("purchase_order"),
        pk=pk, purchase_order__organization=organization,
    )
    summary = services.line_allocation_summary(line)
    allocations = line.allocations.filter(is_active=True).select_related("building", "unit", "area")
    spares = line.purchased_spares.select_related("confirmed_by")
    spare_summaries = [(spare, services.spare_inventory_summary(spare)) for spare in spares]
    allocation_form = AllocationForm(organization=organization)
    spare_form = ConfirmSpareForm()
    reassign_form = ReassignmentForm(organization=organization)
    return render(request, "procurement/line_allocation_detail.html", {
        "line": line, "summary": summary, "allocations": allocations, "spare_summaries": spare_summaries,
        "allocation_form": allocation_form, "spare_form": spare_form, "reassign_form": reassign_form,
    })


@login_required
def line_allocate(request, pk):
    organization = request.user.profile.organization
    line = get_object_or_404(PurchaseOrderLine, pk=pk, purchase_order__organization=organization)
    if request.method == "POST":
        form = AllocationForm(request.POST, organization=organization)
        if form.is_valid():
            try:
                services.allocate_order_line(line, request.user, **form.cleaned_data)
                messages.success(request, "Asignación de destino registrada.")
            except services.AllocationError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los datos de la asignación.")
    return redirect("procurement:line-allocation-detail", pk=pk)


@login_required
def line_confirm_spare(request, pk):
    organization = request.user.profile.organization
    line = get_object_or_404(PurchaseOrderLine, pk=pk, purchase_order__organization=organization)
    if request.method == "POST":
        form = ConfirmSpareForm(request.POST)
        if form.is_valid():
            try:
                services.confirm_purchased_spare(line, request.user, **form.cleaned_data)
                messages.success(request, "Repuesto comprado confirmado.")
            except services.AllocationError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los datos del repuesto.")
    return redirect("procurement:line-allocation-detail", pk=pk)


@login_required
def allocation_reassign(request, pk):
    organization = request.user.profile.organization
    allocation = get_object_or_404(
        OrderLineAllocation, pk=pk, purchase_order_line__purchase_order__organization=organization,
    )
    if request.method == "POST":
        form = ReassignmentForm(request.POST, organization=organization)
        if form.is_valid():
            data = form.cleaned_data
            reason = data.pop("reassignment_reason")
            destination_scope = data.pop("destination_scope") or None
            try:
                services.reassign_allocation(allocation, request.user, reason=reason, destination_scope=destination_scope, **data)
                messages.success(request, "Destino reasignado.")
            except services.AllocationError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los datos de la reasignación.")
    return redirect("procurement:line-allocation-detail", pk=allocation.purchase_order_line_id)


@login_required
def purchased_spares_list(request):
    organization = request.user.profile.organization
    spares = PurchasedSpare.objects.filter(
        purchase_order_line__purchase_order__organization=organization
    ).select_related("purchase_order_line__purchase_order", "confirmed_by").order_by("-confirmed_at")
    rows = [(spare, services.spare_inventory_summary(spare)) for spare in spares]
    return render(request, "procurement/purchased_spares_list.html", {"rows": rows})


@login_required
def allocations_by_destination(request):
    organization = request.user.profile.organization
    allocations = OrderLineAllocation.objects.filter(
        purchase_order_line__purchase_order__organization=organization, is_active=True,
    ).select_related("purchase_order_line__purchase_order", "building", "unit", "building_family").order_by(
        "building_family__display_name", "building__name", "unit__name"
    )
    return render(request, "procurement/allocations_by_destination.html", {"allocations": allocations})
