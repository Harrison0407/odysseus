from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from apps.documents.views import DocumentUploadForm
from apps.projects.models import Building, Project
from apps.workflow.services import resolve_organization

from . import services
from .models import EvidenceClassification, UnclassifiedEvidence


def _resolve_target_or_none(request, app_label, model, object_id):
    """Resolves a classification target generically and verifies it
    belongs to the requesting user's organization — never allows
    classifying evidence to another organization's record."""
    try:
        content_type = ContentType.objects.get(app_label=app_label, model=model)
        model_class = content_type.model_class()
        target = model_class.objects.get(pk=object_id)
    except Exception:
        return None
    target_organization = resolve_organization(target)
    if target_organization is not None and target_organization.id != request.user.profile.organization_id:
        return None
    return target

# Classifiable target types, exposed as a simple app_label/model picker
# — mirrors the same generic app_label/model/pk resolution pattern
# apps.labels already uses for QR entities.
CLASSIFIABLE_TARGETS = [
    ("projects", "building", "Edificio físico"),
    ("projects", "floor", "Piso"),
    ("projects", "unit", "Unidad/Apartamento"),
    ("walkthroughs", "walkthrough", "Recorrido"),
    ("walkthroughs", "walkthroughitem", "Ítem de recorrido"),
    ("training", "trainingsession", "Sesión de capacitación"),
    ("fieldissues", "fieldissue", "Incidencia de campo"),
    ("items", "item", "Producto"),
    ("procurement", "supplier", "Proveedor"),
    ("procurement", "purchaseorderline", "Línea de orden de compra"),
    ("shipments", "shipment", "Embarque"),
    ("shipments", "container", "Contenedor"),
    ("requests", "installationrecord", "Instalación"),
    ("requests", "inspectionrecord", "Inspección"),
]


class UploadForm(forms.Form):
    project = forms.ModelChoiceField(queryset=Project.objects.none(), required=False, label="Proyecto")
    building = forms.ModelChoiceField(queryset=Building.objects.none(), required=False, label="Edificio")
    date_taken = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Fecha en que se tomó")
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False, label="Notas")

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = Project.objects.filter(organization=organization)
        self.fields["building"].queryset = Building.objects.filter(project__organization=organization)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


@login_required
def inbox_list(request):
    organization = request.user.profile.organization
    evidence = UnclassifiedEvidence.objects.select_related("building", "project", "document").filter(organization=organization)
    status = request.GET.get("status")
    if status:
        evidence = evidence.filter(classification_status=status)
    evidence = evidence.order_by("-created_at")[:200]
    return render(request, "evidenceinbox/list.html", {
        "evidence": evidence, "statuses": UnclassifiedEvidence.ClassificationStatus.choices, "current_status": status,
        "classifiable_targets": CLASSIFIABLE_TARGETS,
    })


@login_required
def inbox_upload(request):
    organization = request.user.profile.organization
    if request.method == "POST":
        doc_form = DocumentUploadForm(request.POST, request.FILES, organization=organization)
        meta_form = UploadForm(request.POST, organization=organization)
        if doc_form.is_valid() and meta_form.is_valid():
            evidence, is_duplicate = services.upload_unclassified_evidence(
                request.user, organization, document_type=doc_form.cleaned_data["document_type"],
                title=doc_form.cleaned_data["title"], uploaded_file=doc_form.cleaned_data["file"],
                project=meta_form.cleaned_data["project"], building=meta_form.cleaned_data["building"],
                date_taken=meta_form.cleaned_data["date_taken"], notes=meta_form.cleaned_data["notes"],
            )
            if is_duplicate:
                messages.warning(request, "Evidencia cargada — mismo contenido (SHA-256) que un archivo ya existente.")
            else:
                messages.success(request, "Evidencia cargada al inbox sin clasificar.")
            return redirect("evidenceinbox:detail", pk=evidence.pk)
        messages.error(request, "Revise el archivo y los datos.")
    else:
        doc_form = DocumentUploadForm(organization=organization)
        meta_form = UploadForm(organization=organization)
    return render(request, "evidenceinbox/upload.html", {"doc_form": doc_form, "meta_form": meta_form})


@login_required
def inbox_detail(request, pk):
    organization = request.user.profile.organization
    evidence = get_object_or_404(UnclassifiedEvidence, pk=pk, organization=organization)
    classifications = evidence.classifications.filter(is_active=True).select_related("content_type")
    return render(request, "evidenceinbox/detail.html", {
        "evidence": evidence, "classifications": classifications, "classifiable_targets": CLASSIFIABLE_TARGETS,
    })


@login_required
def inbox_classify(request, pk):
    organization = request.user.profile.organization
    evidence = get_object_or_404(UnclassifiedEvidence, pk=pk, organization=organization)
    if request.method == "POST":
        target = _resolve_target_or_none(request, request.POST.get("app_label"), request.POST.get("model"), request.POST.get("object_id"))
        if target is None:
            messages.error(request, "No se pudo encontrar el destino indicado.")
            return redirect("evidenceinbox:detail", pk=pk)
        services.classify_evidence(evidence, request.user, target=target, reason=request.POST.get("reason", ""))
        messages.success(request, "Evidencia clasificada.")
    return redirect("evidenceinbox:detail", pk=pk)


@login_required
def inbox_reclassify(request, classification_pk):
    organization = request.user.profile.organization
    classification = get_object_or_404(
        EvidenceClassification, pk=classification_pk, evidence__organization=organization,
    )
    if request.method == "POST":
        target = _resolve_target_or_none(request, request.POST.get("app_label"), request.POST.get("model"), request.POST.get("object_id"))
        if target is None:
            messages.error(request, "No se pudo encontrar el nuevo destino indicado.")
            return redirect("evidenceinbox:detail", pk=classification.evidence_id)
        try:
            services.reclassify_evidence(classification, request.user, target=target, reason=request.POST.get("reason", ""))
            messages.success(request, "Clasificación reasignada.")
        except services.EvidenceInboxError as exc:
            messages.error(request, str(exc))
    return redirect("evidenceinbox:detail", pk=classification.evidence_id)


@login_required
def inbox_batch_classify(request):
    organization = request.user.profile.organization
    if request.method == "POST":
        evidence_ids = request.POST.getlist("evidence_id")
        target = _resolve_target_or_none(request, request.POST.get("app_label"), request.POST.get("model"), request.POST.get("object_id"))
        if target is None:
            messages.error(request, "No se pudo encontrar el destino indicado.")
            return redirect("evidenceinbox:list")
        queryset = UnclassifiedEvidence.objects.filter(pk__in=evidence_ids, organization=organization)
        services.batch_classify(queryset, request.user, target=target, reason=request.POST.get("reason", ""))
        messages.success(request, f"{queryset.count()} evidencia(s) clasificada(s).")
    return redirect("evidenceinbox:list")
