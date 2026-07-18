from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.audit import services as audit_services
from apps.cost.models import Currency
from apps.documents.views import DocumentUploadForm
from apps.inventory.models import QuarantineRecord
from apps.items.models import Item
from apps.matching.models import Discrepancy
from apps.procurement.models import PurchaseOrder, PurchaseOrderLine, Supplier, SupplierContact
from apps.receiving.models import Inspection, Receipt, ReceiptLine
from apps.shipments.models import Container, ManifestVariance, ReplacementCase, Shipment

from . import services
from .models import SupplierClaim

User = get_user_model()


class ClaimForm(forms.ModelForm):
    class Meta:
        model = SupplierClaim
        fields = [
            "claim_type", "reason", "shipment", "supplier", "purchase_order", "purchase_order_line", "item",
            "container", "manifest_variance", "receipt", "receipt_line", "discrepancy", "quarantine_record",
            "inspection", "replacement_case", "quantity_claimed", "quantity_damaged", "quantity_missing",
            "amount_claimed", "currency", "responsible_internal_owner", "responsible_supplier_contact",
        ]
        widgets = {"reason": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["shipment"].queryset = Shipment.objects.filter(organization=organization)
        self.fields["supplier"].queryset = Supplier.objects.filter(organization=organization)
        self.fields["purchase_order"].queryset = PurchaseOrder.objects.filter(organization=organization)
        self.fields["purchase_order_line"].queryset = PurchaseOrderLine.objects.filter(purchase_order__organization=organization)
        self.fields["item"].queryset = Item.objects.filter(organization=organization)
        self.fields["container"].queryset = Container.objects.filter(shipment__organization=organization)
        self.fields["manifest_variance"].queryset = ManifestVariance.objects.filter(shipment__organization=organization)
        self.fields["receipt"].queryset = Receipt.objects.filter(release_packet__shipment__organization=organization)
        self.fields["receipt_line"].queryset = ReceiptLine.objects.filter(receipt__release_packet__shipment__organization=organization)
        self.fields["discrepancy"].queryset = Discrepancy.objects.filter(shipment__organization=organization)
        self.fields["quarantine_record"].queryset = QuarantineRecord.objects.filter(lot__item__organization=organization)
        self.fields["inspection"].queryset = Inspection.objects.filter(receipt__release_packet__shipment__organization=organization)
        self.fields["replacement_case"].queryset = ReplacementCase.objects.filter(original_item__organization=organization)
        self.fields["currency"].queryset = Currency.objects.all().order_by("code")
        self.fields["responsible_internal_owner"].queryset = User.objects.filter(profile__organization=organization)
        self.fields["responsible_supplier_contact"].queryset = SupplierContact.objects.filter(supplier__organization=organization)
        for field in self.fields.values():
            field.required = False
        self.fields["claim_type"].required = True
        self.fields["reason"].required = True
        self.fields["shipment"].required = True
        self.fields["supplier"].required = True
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


class SupplierResponseForm(forms.Form):
    response = forms.ChoiceField(choices=[
        c for c in SupplierClaim.SupplierResponse.choices if c[0] != SupplierClaim.SupplierResponse.PENDING
    ], label="Respuesta del proveedor")
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False, label="Notas")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


class ResolveClaimForm(forms.Form):
    resolution_type = forms.ChoiceField(choices=[
        c for c in SupplierClaim.ResolutionType.choices if c[0] != SupplierClaim.ResolutionType.NONE
    ], label="Tipo de resolución")
    resolution_reference = forms.CharField(max_length=150, required=False, label="Referencia (# nota de crédito, caso de reemplazo, etc.)")
    resolution_amount = forms.DecimalField(max_digits=14, decimal_places=2, required=False, label="Monto de la resolución")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


class CloseClaimForm(forms.Form):
    closure_notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False, label="Notas de cierre")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


def _organization(request):
    return request.user.profile.organization


@login_required
def claim_list(request):
    claims = SupplierClaim.objects.filter(organization=_organization(request)).select_related("supplier", "shipment")
    status = request.GET.get("status")
    if status:
        claims = claims.filter(status=status)
    return render(request, "claims/list.html", {"claims": claims, "status": status, "statuses": SupplierClaim.Status.choices})


@login_required
def claim_create(request):
    organization = _organization(request)
    if request.method == "POST":
        form = ClaimForm(request.POST, organization=organization)
        if form.is_valid():
            data = form.cleaned_data
            claim = services.create_claim(
                data.pop("shipment"), data.pop("supplier"), data.pop("claim_type"), data.pop("reason"),
                request.user, **data,
            )
            messages.success(request, f"Reclamo {claim.claim_number} creado.")
            return redirect("claims:detail", pk=claim.pk)
        messages.error(request, "Revise los datos del reclamo.")
    else:
        form = ClaimForm(organization=organization)
    return render(request, "claims/create.html", {"form": form})


@login_required
def claim_detail(request, pk):
    claim = get_object_or_404(
        SupplierClaim.objects.select_related(
            "supplier", "shipment", "purchase_order", "purchase_order_line", "item", "container",
            "manifest_variance", "receipt", "receipt_line", "discrepancy", "quarantine_record", "inspection",
            "replacement_case", "currency", "responsible_internal_owner", "responsible_supplier_contact",
        ),
        pk=pk, organization=_organization(request),
    )
    evidence = audit_services.list_evidence(claim)
    warnings = services.submission_readiness(claim)
    upload_form = DocumentUploadForm(organization=_organization(request))
    response_form = SupplierResponseForm()
    resolve_form = ResolveClaimForm()
    close_form = CloseClaimForm()
    return render(request, "claims/detail.html", {
        "claim": claim, "evidence": evidence, "warnings": warnings, "upload_form": upload_form,
        "evidence_upload_url": reverse("claims:evidence-upload", args=[claim.pk]),
        "response_form": response_form, "resolve_form": resolve_form, "close_form": close_form,
    })


@login_required
def claim_evidence_upload(request, pk):
    claim = get_object_or_404(SupplierClaim, pk=pk, organization=_organization(request))
    if request.method == "POST":
        form = DocumentUploadForm(request.POST, request.FILES, organization=_organization(request))
        if form.is_valid():
            _, is_duplicate = audit_services.attach_evidence(
                claim, request.user, document_type=form.cleaned_data["document_type"],
                title=form.cleaned_data["title"], uploaded_file=form.cleaned_data["file"],
            )
            if is_duplicate:
                messages.warning(request, "Evidencia adjuntada — mismo contenido (SHA-256) que un archivo ya cargado.")
            else:
                messages.success(request, "Evidencia adjuntada.")
        else:
            messages.error(request, "Revise el archivo de evidencia.")
    return redirect("claims:detail", pk=pk)


@login_required
def claim_approve(request, pk):
    claim = get_object_or_404(SupplierClaim, pk=pk, organization=_organization(request))
    if request.method == "POST":
        try:
            services.approve_claim(claim, request.user)
            messages.success(request, "Reclamo aprobado para envío.")
        except services.ClaimError as exc:
            messages.error(request, str(exc))
    return redirect("claims:detail", pk=pk)


@login_required
def claim_submit(request, pk):
    claim = get_object_or_404(SupplierClaim, pk=pk, organization=_organization(request))
    if request.method == "POST":
        try:
            services.submit_claim(claim, request.user)
            messages.success(request, "Reclamo marcado como enviado al proveedor.")
        except services.ClaimError as exc:
            messages.error(request, str(exc))
    return redirect("claims:detail", pk=pk)


@login_required
def claim_record_response(request, pk):
    claim = get_object_or_404(SupplierClaim, pk=pk, organization=_organization(request))
    if request.method == "POST":
        form = SupplierResponseForm(request.POST)
        if form.is_valid():
            try:
                services.record_supplier_response(
                    claim, request.user, response=form.cleaned_data["response"], notes=form.cleaned_data["notes"],
                )
                messages.success(request, "Respuesta del proveedor registrada.")
            except services.ClaimError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los datos de la respuesta.")
    return redirect("claims:detail", pk=pk)


@login_required
def claim_resolve(request, pk):
    claim = get_object_or_404(SupplierClaim, pk=pk, organization=_organization(request))
    if request.method == "POST":
        form = ResolveClaimForm(request.POST)
        if form.is_valid():
            try:
                services.resolve_claim(
                    claim, request.user, resolution_type=form.cleaned_data["resolution_type"],
                    resolution_reference=form.cleaned_data["resolution_reference"],
                    resolution_amount=form.cleaned_data["resolution_amount"],
                )
                messages.success(request, "Reclamo resuelto.")
            except services.ClaimError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los datos de la resolución.")
    return redirect("claims:detail", pk=pk)


@login_required
def claim_close(request, pk):
    claim = get_object_or_404(SupplierClaim, pk=pk, organization=_organization(request))
    if request.method == "POST":
        form = CloseClaimForm(request.POST)
        if form.is_valid():
            try:
                services.close_claim(claim, request.user, closure_notes=form.cleaned_data["closure_notes"])
                messages.success(request, "Reclamo cerrado.")
            except services.ClaimError as exc:
                messages.error(request, str(exc))
    return redirect("claims:detail", pk=pk)


@login_required
def claim_generate_package(request, pk):
    claim = get_object_or_404(SupplierClaim, pk=pk, organization=_organization(request))
    _, html = services.generate_claim_package(claim, request.user)
    response = HttpResponse(html, content_type="text/html; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="reclamo-{claim.claim_number}.html"'
    return response
