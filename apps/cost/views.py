from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from apps.shipments.models import Shipment

from . import services
from .models import CostAllocationRun, CostCharge, LandedCostVersion

AUTOMATIC_ALLOCATION_METHODS = [
    (CostAllocationRun.AllocationMethod.QUANTITY, "Cantidad"),
    (CostAllocationRun.AllocationMethod.PRODUCT_VALUE, "Valor de producto"),
    (CostAllocationRun.AllocationMethod.GROSS_WEIGHT, "Peso bruto"),
    (CostAllocationRun.AllocationMethod.NET_WEIGHT, "Peso neto"),
    (CostAllocationRun.AllocationMethod.CBM, "CBM"),
    (CostAllocationRun.AllocationMethod.PACKAGE, "Paquete"),
    (CostAllocationRun.AllocationMethod.CONTAINER, "Contenedor (partes iguales)"),
]


class RunAllocationForm(forms.Form):
    cost_charge = forms.ModelChoiceField(queryset=CostCharge.objects.none(), label="Cargo a asignar")
    method = forms.ChoiceField(choices=AUTOMATIC_ALLOCATION_METHODS, label="Método de asignación")

    def __init__(self, *args, shipment=None, **kwargs):
        super().__init__(*args, **kwargs)
        if shipment is not None:
            self.fields["cost_charge"].queryset = CostCharge.objects.filter(cost_document__shipment=shipment)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


@login_required
def landed_cost_list(request):
    versions = LandedCostVersion.objects.filter(
        shipment__organization=request.user.profile.organization
    ).select_related("shipment").order_by("-created_at")
    return render(request, "cost/list.html", {"versions": versions})


@login_required
def landed_cost_detail(request, pk):
    version = get_object_or_404(
        LandedCostVersion.objects.select_related("shipment").prefetch_related("lines__manifest_line"),
        pk=pk, shipment__organization=request.user.profile.organization,
    )
    return render(request, "cost/detail.html", {"version": version})


@login_required
def landed_cost_finalize(request, pk):
    version = get_object_or_404(
        LandedCostVersion, pk=pk, shipment__organization=request.user.profile.organization
    )
    if request.method == "POST":
        try:
            services.finalize_landed_cost(version, request.user)
            messages.success(request, "Versión de costo de importación finalizada.")
        except services.AllocationError as exc:
            messages.error(request, str(exc))
    return redirect("cost:detail", pk=pk)


@login_required
def shipment_cost_dashboard(request, shipment_pk):
    shipment = get_object_or_404(
        Shipment, pk=shipment_pk, organization=request.user.profile.organization
    )
    allocation_runs = CostAllocationRun.objects.filter(shipment=shipment).select_related(
        "cost_charge__cost_document"
    ).order_by("-run_at")
    versions = shipment.landed_cost_versions.all()
    return render(request, "cost/shipment_dashboard.html", {
        "shipment": shipment,
        "cost_documents": shipment.cost_documents.prefetch_related("charges"),
        "allocation_runs": allocation_runs,
        "versions": versions,
        "allocation_form": RunAllocationForm(shipment=shipment),
    })


@login_required
def shipment_run_allocation(request, shipment_pk):
    shipment = get_object_or_404(
        Shipment, pk=shipment_pk, organization=request.user.profile.organization
    )
    if request.method == "POST":
        form = RunAllocationForm(request.POST, shipment=shipment)
        if form.is_valid():
            try:
                services.run_allocation(
                    form.cleaned_data["cost_charge"], shipment, form.cleaned_data["method"], request.user
                )
                messages.success(request, "Asignación de costo ejecutada.")
            except services.AllocationError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los datos de la asignación.")
    return redirect("cost:shipment-dashboard", shipment_pk=shipment_pk)


@login_required
def shipment_calculate_version(request, shipment_pk):
    shipment = get_object_or_404(
        Shipment, pk=shipment_pk, organization=request.user.profile.organization
    )
    if request.method == "POST":
        try:
            version = services.calculate_landed_cost(shipment, request.user)
            messages.success(request, f"Versión de costo de importación v{version.version_number} calculada.")
            return redirect("cost:detail", pk=version.pk)
        except services.AllocationError as exc:
            messages.error(request, str(exc))
    return redirect("cost:shipment-dashboard", shipment_pk=shipment_pk)
