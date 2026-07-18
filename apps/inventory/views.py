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
from .services import StoragePermissionError, StorageSuitabilityError


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
    locations = WarehouseLocation.objects.select_related("zone__site").filter(
        is_active=True, zone__site__organization=request.user.profile.organization
    )
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


class CheckSuitabilityForm(forms.Form):
    item = forms.ModelChoiceField(queryset=None, label="Artículo")
    quantity = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0, label="Cantidad proyectada")

    def __init__(self, *args, organization=None, **kwargs):
        from apps.items.models import Item
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = Item.objects.filter(organization=organization) if organization else Item.objects.none()
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


class TransferForm(forms.Form):
    lot = forms.ModelChoiceField(queryset=InventoryLot.objects.none(), label="Lote")
    quantity = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0.001, label="Cantidad a transferir")
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False, label="Motivo")
    override_reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}), required=False,
        label="Motivo de anulación (solo si la ubicación destino no es apta)",
    )

    def __init__(self, *args, lots=None, **kwargs):
        super().__init__(*args, **kwargs)
        if lots is not None:
            self.fields["lot"].queryset = lots
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


@login_required
def location_detail(request, pk):
    location = get_object_or_404(
        WarehouseLocation.objects.select_related("zone__site"),
        pk=pk, zone__site__organization=request.user.profile.organization,
    )
    organization = request.user.profile.organization
    capacity = getattr(location, "capacity", None)
    suitability = getattr(location, "suitability", None)
    utilization = services.location_current_utilization(location)

    balances: dict = {}
    for m in InventoryMovement.objects.filter(to_location=location).select_related("lot__item"):
        balances[m.lot_id] = balances.get(m.lot_id, Decimal("0")) + m.quantity
    for m in InventoryMovement.objects.filter(from_location=location).select_related("lot__item"):
        balances[m.lot_id] = balances.get(m.lot_id, Decimal("0")) - m.quantity
    assigned_lots = [
        {"lot": InventoryLot.objects.select_related("item").get(pk=lot_id), "quantity": qty}
        for lot_id, qty in balances.items() if qty > 0
    ]

    from apps.receiving.models import ReceivingPlanLine
    pending_inbound = [
        rpl for rpl in ReceivingPlanLine.objects.filter(
            proposed_final_location=location
        ).select_related("manifest_line__item")
        if not rpl.manifest_line.receipt_lines.filter(quantity_received__gt=0).exists()
    ]

    check_result = None
    check_form = CheckSuitabilityForm(organization=organization)
    if request.GET.get("item"):
        check_form = CheckSuitabilityForm(request.GET, organization=organization)
        if check_form.is_valid():
            check_result = services.check_location_suitability(
                location, check_form.cleaned_data["item"], quantity=check_form.cleaned_data["quantity"]
            )

    # Lots eligible to transfer *into* this location — any lot elsewhere
    # in the organization with on-hand stock (not restricted to lots
    # already at this location, which would make an incoming transfer
    # impossible to express).
    transferable_lots = InventoryLot.objects.filter(item__organization=organization).exclude(
        pk__in=[row["lot"].pk for row in assigned_lots]
    )
    transfer_form = TransferForm(lots=transferable_lots)

    return render(request, "inventory/location_detail.html", {
        "location": location,
        "capacity": capacity,
        "suitability": suitability,
        "utilization": utilization,
        "assigned_lots": assigned_lots,
        "pending_inbound": pending_inbound,
        "responsible": location.zone.site.custodian,
        "check_form": check_form,
        "check_result": check_result,
        "transfer_form": transfer_form,
    })


@login_required
def location_transfer(request, pk):
    to_location = get_object_or_404(
        WarehouseLocation, pk=pk, zone__site__organization=request.user.profile.organization
    )
    organization = request.user.profile.organization
    lots_here = InventoryLot.objects.filter(item__organization=organization)
    if request.method == "POST":
        form = TransferForm(request.POST, lots=lots_here)
        if form.is_valid():
            from apps.requests.services import QuantityInvariantError, transfer_lot

            lot = form.cleaned_data["lot"]
            _, location_balances = _current_lot_locations(lot)
            if not location_balances:
                messages.error(request, "Este lote no tiene una ubicación de origen determinable.")
                return redirect("inventory:location-detail", pk=pk)
            if len(location_balances) > 1:
                messages.error(
                    request,
                    "Este lote está presente en más de una ubicación — la transferencia de lotes "
                    "distribuidos en varias ubicaciones no está soportada todavía en esta pantalla "
                    "(use el historial del lote para identificar la ubicación exacta y registre la "
                    "transferencia por otro medio si es necesario).",
                )
                return redirect("inventory:location-detail", pk=pk)
            from_location_id = next(iter(location_balances))
            from_location = WarehouseLocation.objects.get(pk=from_location_id)
            try:
                transfer_lot(
                    lot, from_location, to_location, form.cleaned_data["quantity"], request.user,
                    reason=form.cleaned_data["reason"],
                    override_reason=form.cleaned_data["override_reason"] or None,
                )
                messages.success(request, "Transferencia registrada.")
            except QuantityInvariantError as exc:
                messages.error(request, str(exc))
            except StorageSuitabilityError as exc:
                messages.error(request, "Ubicación destino no apta: " + "; ".join(exc.result.blockers))
            except StoragePermissionError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los datos de la transferencia.")
    return redirect("inventory:location-detail", pk=pk)


def _current_lot_locations(lot):
    balances: dict = {}
    on_hand = Decimal("0")
    for m in InventoryMovement.objects.filter(lot=lot):
        if m.to_location_id:
            balances[m.to_location_id] = balances.get(m.to_location_id, Decimal("0")) + m.quantity
            on_hand += m.quantity
        if m.from_location_id:
            balances[m.from_location_id] = balances.get(m.from_location_id, Decimal("0")) - m.quantity
            on_hand -= m.quantity
    return on_hand, {k: v for k, v in balances.items() if v > 0}


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
