from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.cost.models import Currency
from apps.inventory.models import WarehouseLocation
from apps.inventory.services import StoragePermissionError, StorageSuitabilityError

from . import services
from .models import AlternativeStorageOption, Receipt, ReceiptLine, ReceivingPlan, StorageComparisonScenario


class ReceiptLineForm(forms.Form):
    quantity_received = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0)
    quantity_damaged = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0, initial=0)
    quantity_missing = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0, initial=0)
    exception_type = forms.ChoiceField(choices=ReceiptLine.ExceptionType.choices, required=False)
    notes = forms.CharField(widget=forms.Textarea, required=False)
    receiving_location = forms.ModelChoiceField(queryset=WarehouseLocation.objects.none(), label="Ubicación de recepción")
    storage_override_reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}), required=False,
        label="Motivo de anulación (solo si la ubicación no es apta)",
    )

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization is not None:
            self.fields["receiving_location"].queryset = WarehouseLocation.objects.filter(
                is_active=True, zone__site__organization=organization
            )
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
    organization = request.user.profile.organization
    lines_with_forms = [
        (line, ReceiptLineForm(organization=organization, initial={
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
        form = ReceiptLineForm(request.POST, organization=request.user.profile.organization)
        if form.is_valid():
            quarantine_location = WarehouseLocation.objects.filter(zone__code="cuarentena").first()
            try:
                services.post_receipt_line(
                    line,
                    quantity_received=form.cleaned_data["quantity_received"],
                    quantity_damaged=form.cleaned_data["quantity_damaged"],
                    quantity_missing=form.cleaned_data["quantity_missing"],
                    exception_type=form.cleaned_data["exception_type"],
                    notes=form.cleaned_data["notes"],
                    user=request.user,
                    receiving_location=form.cleaned_data["receiving_location"],
                    quarantine_location=quarantine_location,
                    storage_override_reason=form.cleaned_data["storage_override_reason"] or None,
                )
                messages.success(request, "Línea de recepción registrada.")
            except StorageSuitabilityError as exc:
                messages.error(request, "Ubicación no apta: " + "; ".join(exc.result.blockers))
            except StoragePermissionError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los valores ingresados.")
    return redirect("receiving:detail", pk=receipt.pk)


# ---------------------------------------------------------------------------
# External storage comparison calculator (spec section 17)
# ---------------------------------------------------------------------------


class StorageOptionForm(forms.ModelForm):
    class Meta:
        model = AlternativeStorageOption
        exclude = ["scenario", "created_by"]
        widgets = {
            "risk_notes": forms.Textarea(attrs={"rows": 2}),
            "demurrage_penalty_notes": forms.Textarea(attrs={"rows": 2}),
            "access_restrictions_notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["internal_location"].queryset = (
            WarehouseLocation.objects.filter(zone__site__organization=organization)
            if organization else WarehouseLocation.objects.none()
        )
        self.fields["currency"].queryset = Currency.objects.all().order_by("code")
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


class FinalizeComparisonForm(forms.Form):
    chosen_option = forms.ModelChoiceField(queryset=AlternativeStorageOption.objects.none(), label="Opción elegida")
    rationale = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="Justificación de la decisión")

    def __init__(self, *args, scenario=None, **kwargs):
        super().__init__(*args, **kwargs)
        if scenario is not None:
            self.fields["chosen_option"].queryset = scenario.options.all()
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


@login_required
def receiving_plan_detail(request, pk):
    plan = get_object_or_404(
        ReceivingPlan.objects.select_related("shipment", "project", "proposed_unloading_location"),
        pk=pk, shipment__organization=request.user.profile.organization,
    )
    scenarios = plan.storage_comparison_scenarios.select_related("chosen_option").order_by("-version_number")
    return render(request, "receiving/receiving_plan_detail.html", {"plan": plan, "scenarios": scenarios})


@login_required
def comparison_scenario_create(request, plan_pk):
    plan = get_object_or_404(ReceivingPlan, pk=plan_pk, shipment__organization=request.user.profile.organization)
    if request.method == "POST":
        scenario = services.create_comparison_scenario(plan, request.user, name=request.POST.get("name", ""))
        messages.success(request, "Nueva versión de comparación de almacenaje iniciada.")
        return redirect("receiving:comparison-detail", pk=scenario.pk)
    return redirect("receiving:plan-detail", pk=plan_pk)


@login_required
def comparison_scenario_detail(request, pk):
    scenario = get_object_or_404(
        StorageComparisonScenario.objects.select_related("receiving_plan__shipment", "chosen_option"),
        pk=pk, receiving_plan__shipment__organization=request.user.profile.organization,
    )
    organization = request.user.profile.organization
    option_form = StorageOptionForm(organization=organization)
    results = services.compare_scenario_options(scenario)
    finalize_form = FinalizeComparisonForm(scenario=scenario)
    return render(request, "receiving/comparison_scenario_detail.html", {
        "scenario": scenario, "option_form": option_form, "results": results, "finalize_form": finalize_form,
    })


@login_required
def comparison_option_create(request, pk):
    scenario = get_object_or_404(
        StorageComparisonScenario, pk=pk, receiving_plan__shipment__organization=request.user.profile.organization
    )
    if request.method == "POST":
        form = StorageOptionForm(request.POST, organization=request.user.profile.organization)
        if form.is_valid():
            try:
                services.add_storage_option(scenario, created_by=request.user, **form.cleaned_data)
                messages.success(request, "Opción de almacenaje agregada.")
            except services.StorageComparisonError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los datos de la opción.")
    return redirect("receiving:comparison-detail", pk=pk)


@login_required
def comparison_finalize(request, pk):
    scenario = get_object_or_404(
        StorageComparisonScenario, pk=pk, receiving_plan__shipment__organization=request.user.profile.organization
    )
    if request.method == "POST":
        form = FinalizeComparisonForm(request.POST, scenario=scenario)
        if form.is_valid():
            try:
                services.finalize_comparison_scenario(
                    scenario, form.cleaned_data["chosen_option"], request.user, rationale=form.cleaned_data["rationale"]
                )
                messages.success(request, "Comparación finalizada.")
            except services.StorageComparisonError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los datos de la decisión.")
    return redirect("receiving:comparison-detail", pk=pk)


@login_required
def comparison_export(request, pk):
    from apps.reports.models import ReportVersion
    from apps.reports.views import _save_html_snapshot

    scenario = get_object_or_404(
        StorageComparisonScenario.objects.select_related("receiving_plan__shipment", "chosen_option"),
        pk=pk, receiving_plan__shipment__organization=request.user.profile.organization,
    )
    results = services.compare_scenario_options(scenario)
    html = render_to_string(
        "receiving/snapshot_storage_comparison.html",
        {"scenario": scenario, "results": results, "generated_at": timezone.now()},
    )
    filename = f"comparacion-almacenaje-{scenario.receiving_plan.shipment.reference}-v{scenario.version_number}.html"
    _save_html_snapshot(
        html, report_type=ReportVersion.ReportType.EXTERNAL_STORAGE_COMPARISON, user=request.user,
        organization=request.user.profile.organization, title=f"Comparación de almacenaje — {scenario}",
        filename=filename,
    )
    audit.log(
        AuditEvent.Action.OTHER, actor=request.user,
        summary=f"Comparación de almacenaje exportada: {scenario}",
    )
    response = HttpResponse(html, content_type="text/html; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
