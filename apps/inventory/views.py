from collections import defaultdict
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from . import services
from .models import (
    CycleCount,
    CycleCountLine,
    InventoryLot,
    InventoryMovement,
    StorageSite,
    WarehouseLocation,
)


def _on_hand_by_location_and_item():
    """On-hand is always *derived* from the movement ledger, never stored
    directly (core principle 4.5). Nets to_location (+) against
    from_location (-) for every posted movement."""
    balances = defaultdict(Decimal)
    movements = InventoryMovement.objects.select_related("lot__item", "to_location", "from_location")
    for m in movements:
        if m.to_location_id:
            balances[(m.to_location_id, m.lot.item_id)] += m.quantity
        if m.from_location_id:
            balances[(m.from_location_id, m.lot.item_id)] -= m.quantity
    return balances


@login_required
def location_list(request):
    locations = WarehouseLocation.objects.select_related("zone__site").filter(is_active=True)
    balances = _on_hand_by_location_and_item()

    rows = []
    for loc in locations:
        items = {}
        for (loc_id, item_id), qty in balances.items():
            if loc_id == loc.id and qty != 0:
                items[item_id] = qty
        if items:
            from apps.items.models import Item
            for item in Item.objects.filter(id__in=items.keys()):
                rows.append({"location": loc, "item": item, "quantity": items[item.id]})
        else:
            rows.append({"location": loc, "item": None, "quantity": None})

    return render(request, "inventory/locations.html", {"rows": rows})


@login_required
def lot_detail(request, pk):
    lot = get_object_or_404(InventoryLot.objects.select_related("item"), pk=pk)
    movements = lot.movements.select_related("from_location", "to_location", "posted_by").order_by("posted_at")
    return render(request, "inventory/lot_detail.html", {"lot": lot, "movements": movements})


class StartCycleCountForm(forms.Form):
    site = forms.ModelChoiceField(queryset=StorageSite.objects.none(), label="Sitio de almacenaje")
    planned_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Fecha planeada")
    is_blind_count = forms.BooleanField(required=False, initial=True, label="Conteo ciego (ocultar cantidad de sistema al contar)")
    lots = forms.ModelMultipleChoiceField(queryset=InventoryLot.objects.none(), label="Lotes a incluir")

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization is not None:
            self.fields["site"].queryset = StorageSite.objects.filter(organization=organization)
            self.fields["lots"].queryset = InventoryLot.objects.filter(item__organization=organization).select_related("item")
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class RecordCountForm(forms.Form):
    physical_quantity = forms.DecimalField(max_digits=14, decimal_places=3, label="Cantidad física contada")
    explanation = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False, label="Explicación de la variación")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


class ApproveAdjustmentForm(forms.Form):
    location = forms.ModelChoiceField(queryset=WarehouseLocation.objects.none(), label="Ubicación del ajuste")
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), label="Motivo del ajuste")

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization is not None:
            self.fields["location"].queryset = WarehouseLocation.objects.filter(zone__site__organization=organization)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


@login_required
def cycle_count_list(request):
    counts = CycleCount.objects.filter(
        site__organization=request.user.profile.organization
    ).select_related("site", "assigned_counter").order_by("-planned_date")
    return render(request, "inventory/cycle_count_list.html", {"counts": counts})


@login_required
def cycle_count_create(request):
    organization = request.user.profile.organization
    if request.method == "POST":
        form = StartCycleCountForm(request.POST, organization=organization)
        if form.is_valid():
            cycle_count = services.start_cycle_count(
                form.cleaned_data["site"], request.user,
                planned_date=form.cleaned_data["planned_date"],
                is_blind_count=form.cleaned_data["is_blind_count"],
                assigned_counter=request.user,
                lots=form.cleaned_data["lots"],
            )
            messages.success(request, "Conteo cíclico iniciado.")
            return redirect("inventory:cycle-count-detail", pk=cycle_count.pk)
        messages.error(request, "Revise los datos del conteo.")
    else:
        form = StartCycleCountForm(organization=organization)
    return render(request, "inventory/cycle_count_create.html", {"form": form})


@login_required
def cycle_count_detail(request, pk):
    cycle_count = get_object_or_404(
        CycleCount.objects.select_related("site", "assigned_counter"),
        pk=pk, site__organization=request.user.profile.organization,
    )
    lines = cycle_count.lines.select_related("lot__item", "approved_adjustment").order_by("lot__item__name")
    lines_with_forms = [
        {
            "line": line,
            "count_form": RecordCountForm(prefix=str(line.pk)),
            "adjustment_form": ApproveAdjustmentForm(organization=request.user.profile.organization, prefix=str(line.pk)),
        }
        for line in lines
    ]
    return render(request, "inventory/cycle_count_detail.html", {
        "cycle_count": cycle_count,
        "lines_with_forms": lines_with_forms,
    })


@login_required
def cycle_count_record(request, pk, line_pk):
    cycle_count = get_object_or_404(CycleCount, pk=pk, site__organization=request.user.profile.organization)
    line = get_object_or_404(CycleCountLine, pk=line_pk, cycle_count=cycle_count)
    if request.method == "POST":
        form = RecordCountForm(request.POST, prefix=str(line.pk))
        if form.is_valid():
            try:
                services.record_physical_count(
                    line, form.cleaned_data["physical_quantity"], request.user,
                    explanation=form.cleaned_data["explanation"],
                )
                messages.success(request, "Conteo físico registrado.")
            except services.CycleCountError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise la cantidad ingresada.")
    return redirect("inventory:cycle-count-detail", pk=pk)


@login_required
def cycle_count_approve_adjustment(request, pk, line_pk):
    cycle_count = get_object_or_404(CycleCount, pk=pk, site__organization=request.user.profile.organization)
    line = get_object_or_404(CycleCountLine, pk=line_pk, cycle_count=cycle_count)
    if request.method == "POST":
        form = ApproveAdjustmentForm(request.POST, organization=request.user.profile.organization, prefix=str(line.pk))
        if form.is_valid():
            try:
                services.approve_adjustment(
                    line, request.user,
                    reason=form.cleaned_data["reason"], location=form.cleaned_data["location"],
                )
                messages.success(request, "Ajuste de inventario aprobado y registrado.")
            except services.CycleCountError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los datos del ajuste.")
    return redirect("inventory:cycle-count-detail", pk=pk)
