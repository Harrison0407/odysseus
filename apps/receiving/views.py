from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from apps.inventory.models import WarehouseLocation

from . import services
from .models import Receipt, ReceiptLine


class ReceiptLineForm(forms.Form):
    quantity_received = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0)
    quantity_damaged = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0, initial=0)
    quantity_missing = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0, initial=0)
    exception_type = forms.ChoiceField(choices=ReceiptLine.ExceptionType.choices, required=False)
    notes = forms.CharField(widget=forms.Textarea, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


@login_required
def receipt_list(request):
    receipts = (
        Receipt.objects.select_related("container", "release_packet__shipment")
        .order_by("-created_at")
    )
    return render(request, "receiving/list.html", {"receipts": receipts})


@login_required
def receipt_detail(request, pk):
    receipt = get_object_or_404(
        Receipt.objects.select_related("container", "release_packet__shipment").prefetch_related(
            "lines__manifest_line__item"
        ),
        pk=pk,
    )
    lines_with_forms = [
        (line, ReceiptLineForm(initial={
            "quantity_received": line.quantity_received,
            "quantity_damaged": line.quantity_damaged,
            "quantity_missing": line.quantity_missing,
            "exception_type": line.exception_type,
        }))
        for line in receipt.lines.all()
    ]
    return render(request, "receiving/detail.html", {"receipt": receipt, "lines_with_forms": lines_with_forms})


@login_required
def receipt_line_update(request, pk, line_pk):
    receipt = get_object_or_404(Receipt, pk=pk)
    line = get_object_or_404(ReceiptLine, pk=line_pk, receipt=receipt)
    if request.method == "POST":
        form = ReceiptLineForm(request.POST)
        if form.is_valid():
            receiving_location = WarehouseLocation.objects.first()
            quarantine_location = WarehouseLocation.objects.filter(zone__code="cuarentena").first()
            services.post_receipt_line(
                line,
                quantity_received=form.cleaned_data["quantity_received"],
                quantity_damaged=form.cleaned_data["quantity_damaged"],
                quantity_missing=form.cleaned_data["quantity_missing"],
                exception_type=form.cleaned_data["exception_type"],
                notes=form.cleaned_data["notes"],
                user=request.user,
                receiving_location=receiving_location,
                quarantine_location=quarantine_location,
            )
            messages.success(request, "Línea de recepción registrada.")
        else:
            messages.error(request, "Revise los valores ingresados.")
    return redirect("receiving:detail", pk=receipt.pk)
