from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.storage import document_storage

from apps.evidenceinbox import services as evidenceinbox_services
from apps.fieldissues import services as fieldissues_services
from apps.fieldissues.models import FieldIssue, IssueCategory
from apps.projects.models import Unit
from apps.walkthroughs import services as walkthroughs_services
from apps.walkthroughs.models import Walkthrough
from apps.workflow.services import can_override_gates, user_can_access_project

from . import services
from .models import PlanZone, UnitPlanTemplate


class ZoneIssueForm(forms.Form):
    category = forms.ModelChoiceField(queryset=IssueCategory.objects.none(), required=False, label="Categoría")
    title = forms.CharField(max_length=255, label="Título")
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False, label="Descripción")
    priority = forms.ChoiceField(choices=FieldIssue.Priority.choices, initial=FieldIssue.Priority.MEDIUM, label="Prioridad")

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = IssueCategory.objects.filter(organization=organization)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


@login_required
def unit_plan(request, pk):
    organization = request.user.profile.organization
    unit = get_object_or_404(
        Unit.objects.select_related("building__family", "building__project", "floor"),
        pk=pk, building__project__organization=organization,
    )
    if not user_can_access_project(request.user, unit.building.project):
        raise Http404
    template = services.effective_template_for_unit(unit)
    zones = []
    if template is not None:
        zones = list(template.zones.filter(is_active=True).order_by("display_order"))
        for zone in zones:
            zone.open_issue_count = services.zone_open_issue_count(zone)
    highlight_zone = request.GET.get("highlight_zone", "")
    return render(request, "unitplans/unit_plan.html", {
        "unit": unit, "template": template, "zones": zones, "highlight_zone": highlight_zone,
    })


@login_required
def template_plan_image(request, pk):
    """Serves the derived operational plan image inline (never the
    original architect PDF) — organization-scoped like every other
    document download in this system, but rendered inline (not
    `as_attachment`) so it can be used as an <img> source in the
    interactive viewer."""
    organization = request.user.profile.organization
    template = get_object_or_404(UnitPlanTemplate, pk=pk, organization=organization)
    if template.derived_plan_document_id is None:
        raise Http404
    version = template.derived_plan_document.versions.order_by("-version_number").first()
    if version is None:
        raise Http404
    try:
        fh = document_storage.open(version.stored_name)
    except FileNotFoundError:
        raise Http404("Archivo no encontrado en almacenamiento.")
    return FileResponse(fh, as_attachment=False, filename=version.original_filename)


@login_required
def zone_create_issue(request, pk):
    organization = request.user.profile.organization
    zone = get_object_or_404(PlanZone, pk=pk, template__organization=organization)
    unit_pk = request.POST.get("unit_id") or request.GET.get("unit_id")
    unit = get_object_or_404(Unit, pk=unit_pk, building__project__organization=organization)
    if not user_can_access_project(request.user, unit.building.project):
        raise Http404
    if request.method == "POST":
        form = ZoneIssueForm(request.POST, organization=organization)
        if form.is_valid():
            try:
                issue = fieldissues_services.report_issue(
                    unit.building, request.user, title=form.cleaned_data["title"],
                    description=form.cleaned_data["description"], category=form.cleaned_data["category"],
                    priority=form.cleaned_data["priority"], floor=unit.floor, unit=unit,
                    room_or_location=zone.name_es, plan_template=zone.template, plan_zone=zone,
                )
                messages.success(request, "Incidencia creada desde el plano.")
                return redirect("fieldissues:detail", pk=issue.pk)
            except fieldissues_services.FieldIssueError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los datos de la incidencia.")
    else:
        form = ZoneIssueForm(organization=organization)
    return render(request, "unitplans/zone_create_issue.html", {"form": form, "zone": zone, "unit": unit})


def _zone_photo_document_type(organization):
    from apps.documents.models import DocumentType

    return DocumentType.objects.get_or_create(
        organization=organization, code="unit-plan-zone-photo", defaults={"name": "Foto de zona de plano"},
    )[0]


@login_required
def zone_add_photo(request, pk):
    organization = request.user.profile.organization
    zone = get_object_or_404(PlanZone, pk=pk, template__organization=organization)
    unit_pk = request.POST.get("unit_id") or request.GET.get("unit_id")
    unit = get_object_or_404(Unit, pk=unit_pk, building__project__organization=organization)
    if not user_can_access_project(request.user, unit.building.project):
        raise Http404
    if request.method == "POST":
        uploaded_file = request.FILES.get("file")
        title = request.POST.get("title", "").strip()
        if not uploaded_file or not title:
            messages.error(request, "Revise el archivo y el título de la foto.")
        else:
            evidence, _ = evidenceinbox_services.upload_unclassified_evidence(
                request.user, organization, document_type=_zone_photo_document_type(organization),
                title=title, uploaded_file=uploaded_file, building=unit.building,
                notes=f"Foto tomada desde el plano interactivo — {zone.name_es}",
            )
            evidenceinbox_services.classify_evidence(evidence, request.user, target=zone)
            messages.success(request, "Foto adjuntada y clasificada a esta zona.")
    return redirect(f"{reverse('unitplans:unit-plan', args=[unit.pk])}?highlight_zone={zone.pk}")


@login_required
def zone_create_walkthrough_item(request, pk):
    organization = request.user.profile.organization
    zone = get_object_or_404(PlanZone, pk=pk, template__organization=organization)
    unit_pk = request.POST.get("unit_id") or request.GET.get("unit_id")
    unit = get_object_or_404(Unit, pk=unit_pk, building__project__organization=organization)
    if not user_can_access_project(request.user, unit.building.project):
        raise Http404
    if request.method == "POST":
        walkthrough = Walkthrough.objects.filter(
            unit=unit, progress_status=Walkthrough.ProgressStatus.IN_PROGRESS,
        ).order_by("-created_at").first()
        if walkthrough is None:
            walkthrough = walkthroughs_services.create_walkthrough(
                unit.building, request.user, purpose=Walkthrough.Purpose.QUALITY_CONTROL, unit=unit,
            )
            walkthroughs_services.set_progress(walkthrough, request.user, progress_status=Walkthrough.ProgressStatus.IN_PROGRESS)
        walkthroughs_services.add_item(
            walkthrough, request.user, room_or_location=zone.name_es, unit=unit,
            plan_template=zone.template, plan_zone=zone,
        )
        messages.success(request, "Ítem de recorrido creado desde el plano.")
        return redirect("walkthroughs:detail", pk=walkthrough.pk)
    return redirect("unitplans:unit-plan", pk=unit.pk)


# ---------------------------------------------------------------------------
# Secure administrative mapping/review screen. Every mutating action
# (validate, approve, supersede, upload/replace a derived plan) requires
# `can_override_gates` — the same universal senior-authorization
# permission used for every other approval-style action in this
# release, never a bespoke new flag.
# ---------------------------------------------------------------------------


class ZoneEditForm(forms.Form):
    zone_code = forms.CharField(max_length=50, label="Código de zona")
    name_es = forms.CharField(max_length=150, label="Nombre (Español)")
    name_en = forms.CharField(max_length=150, required=False, label="Nombre (Inglés)")
    zone_type = forms.ChoiceField(choices=PlanZone.ZoneType.choices, label="Tipo de zona")
    custom_type_label = forms.CharField(max_length=100, required=False, label='Etiqueta personalizada (si tipo="Otro")')
    duplex_floor = forms.CharField(max_length=10, required=False, label="Piso del dúplex (si aplica)")
    display_order = forms.IntegerField(initial=0, label="Orden de despliegue")
    x0 = forms.FloatField(label="X0 (0-1)")
    y0 = forms.FloatField(label="Y0 (0-1)")
    x1 = forms.FloatField(label="X1 (0-1)")
    y1 = forms.FloatField(label="Y1 (0-1)")
    source_confidence = forms.ChoiceField(choices=[("high", "Alta"), ("medium", "Media"), ("low", "Baja")], initial="low", label="Confianza de la fuente")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


class TemplatePlanUploadForm(forms.Form):
    title = forms.CharField(max_length=255, label="Título")
    file = forms.FileField(label="Archivo (imagen o PDF de la hoja)")
    source_page = forms.IntegerField(required=False, label="Página/hoja fuente")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


def _require_override(request):
    if not can_override_gates(request.user):
        messages.error(request, "No tiene permiso para administrar planos de unidad.")
        return False
    return True


@login_required
def review_queue(request):
    organization = request.user.profile.organization
    queue = services.review_queue(organization)
    return render(request, "unitplans/review_queue.html", queue)


@login_required
def template_admin_detail(request, pk):
    organization = request.user.profile.organization
    template = get_object_or_404(UnitPlanTemplate, pk=pk, organization=organization)
    zones = template.zones.filter(is_active=True).order_by("display_order")
    zone_form = ZoneEditForm(initial={"display_order": zones.count()})
    upload_form = TemplatePlanUploadForm()
    return render(request, "unitplans/template_admin_detail.html", {
        "template": template, "zones": zones, "zone_form": zone_form, "upload_form": upload_form,
        "can_administer": can_override_gates(request.user),
    })


@login_required
def template_approve(request, pk):
    organization = request.user.profile.organization
    template = get_object_or_404(UnitPlanTemplate, pk=pk, organization=organization)
    if request.method == "POST":
        try:
            services.approve_template(template, request.user)
            messages.success(request, "Plantilla aprobada para uso operativo.")
        except services.UnitPlanError as exc:
            messages.error(request, str(exc))
    return redirect("unitplans:template-admin-detail", pk=pk)


@login_required
def template_supersede(request, pk):
    organization = request.user.profile.organization
    template = get_object_or_404(UnitPlanTemplate, pk=pk, organization=organization)
    if request.method == "POST":
        if not _require_override(request):
            return redirect("unitplans:template-admin-detail", pk=pk)
        new_template = services.supersede_template(
            template, request.user, uncertainty_notes=request.POST.get("uncertainty_notes", ""),
        )
        messages.success(request, "Nueva revisión de la plantilla creada; la anterior se conserva como reemplazada.")
        return redirect("unitplans:template-admin-detail", pk=new_template.pk)
    return redirect("unitplans:template-admin-detail", pk=pk)


@login_required
def template_upload_plan(request, pk):
    organization = request.user.profile.organization
    template = get_object_or_404(UnitPlanTemplate, pk=pk, organization=organization)
    if request.method == "POST":
        if not _require_override(request):
            return redirect("unitplans:template-admin-detail", pk=pk)
        form = TemplatePlanUploadForm(request.POST, request.FILES)
        if form.is_valid():
            from apps.documents.models import Document, DocumentType, DocumentVersion

            doc_type, _ = DocumentType.objects.get_or_create(
                organization=organization, code="unit-plan-derived", defaults={"name": "Plano de unidad derivado (operativo)"},
            )
            uploaded_file = form.cleaned_data["file"]
            stored = document_storage.save(uploaded_file, uploaded_file.name)
            duplicate = DocumentVersion.objects.filter(sha256=stored["sha256"]).first()
            document = Document.objects.create(
                organization=organization, document_type=doc_type, title=form.cleaned_data["title"], created_by=request.user,
            )
            DocumentVersion.objects.create(
                document=document, version_number=1, stored_name=stored["stored_name"],
                original_filename=stored["original_filename"], sha256=stored["sha256"], size_bytes=stored["size_bytes"],
                mime_type=getattr(uploaded_file, "content_type", "") or "", uploaded_by=request.user,
                is_duplicate_of=duplicate, created_by=request.user,
            )
            if template.status == UnitPlanTemplate.Status.APPROVED:
                # Already in operational use — a replacement is a genuine
                # new revision, never an in-place overwrite.
                new_template = services.supersede_template(
                    template, request.user, derived_plan_document=document,
                    source_page=form.cleaned_data["source_page"], copy_zones=True,
                )
                messages.success(request, "Plano reemplazado como nueva revisión (la anterior se conserva).")
                return redirect("unitplans:template-admin-detail", pk=new_template.pk)
            template.derived_plan_document = document
            if form.cleaned_data["source_page"]:
                template.source_page = form.cleaned_data["source_page"]
            if template.status == UnitPlanTemplate.Status.MISSING_SOURCE:
                template.status = UnitPlanTemplate.Status.NEEDS_REVIEW
            template.save()
            messages.success(request, "Plano cargado.")
        else:
            messages.error(request, "Revise el archivo del plano.")
    return redirect("unitplans:template-admin-detail", pk=pk)


@login_required
def zone_add(request, pk):
    organization = request.user.profile.organization
    template = get_object_or_404(UnitPlanTemplate, pk=pk, organization=organization)
    if request.method == "POST":
        if not _require_override(request):
            return redirect("unitplans:template-admin-detail", pk=pk)
        form = ZoneEditForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            services.add_zone(
                template, zone_code=data["zone_code"], name_es=data["name_es"], name_en=data["name_en"],
                zone_type=data["zone_type"], custom_type_label=data["custom_type_label"],
                coordinates={"x0": data["x0"], "y0": data["y0"], "x1": data["x1"], "y1": data["y1"]},
                duplex_floor=data["duplex_floor"], display_order=data["display_order"],
                source_confidence=data["source_confidence"], user=request.user,
            )
            messages.success(request, "Zona agregada.")
        else:
            messages.error(request, "Revise los datos de la zona.")
    return redirect("unitplans:template-admin-detail", pk=pk)


@login_required
def zone_edit(request, pk):
    organization = request.user.profile.organization
    zone = get_object_or_404(PlanZone, pk=pk, template__organization=organization)
    if request.method == "POST":
        if not _require_override(request):
            return redirect("unitplans:template-admin-detail", pk=zone.template_id)
        form = ZoneEditForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            services.supersede_zone(
                zone, request.user, name_es=data["name_es"], name_en=data["name_en"], zone_type=data["zone_type"],
                custom_type_label=data["custom_type_label"],
                coordinates={"x0": data["x0"], "y0": data["y0"], "x1": data["x1"], "y1": data["y1"]},
                duplex_floor=data["duplex_floor"], display_order=data["display_order"],
                source_confidence=data["source_confidence"],
            )
            messages.success(request, "Zona actualizada (se conserva la versión anterior como historial).")
        else:
            messages.error(request, "Revise los datos de la zona.")
    return redirect("unitplans:template-admin-detail", pk=zone.template_id)


@login_required
def zone_validate(request, pk):
    organization = request.user.profile.organization
    zone = get_object_or_404(PlanZone, pk=pk, template__organization=organization)
    if request.method == "POST":
        try:
            services.validate_zone(zone, request.user)
            messages.success(request, "Zona validada.")
        except services.UnitPlanError as exc:
            messages.error(request, str(exc))
    return redirect("unitplans:template-admin-detail", pk=zone.template_id)
