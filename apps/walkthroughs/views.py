from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import UserRole
from apps.documents.views import DocumentUploadForm
from apps.drawings.models import Drawing
from apps.projects.models import Area, Building, Floor, Unit
from apps.training.models import TrainingSession
from apps.workflow import services as wfsvc

from . import services
from .models import Walkthrough, WalkthroughCategory, WalkthroughItem, WalkthroughItemEvidence


def _is_management(user) -> bool:
    return UserRole.objects.filter(user=user, role__is_management=True, is_active=True).exists()


def _scope_to_accessible_projects(request, queryset):
    if _is_management(request.user):
        return queryset
    accessible = set(request.user.project_access.values_list("project_id", flat=True))
    return queryset.filter(building__project_id__in=accessible)


class CreateWalkthroughForm(forms.Form):
    building = forms.ModelChoiceField(queryset=Building.objects.none(), label="Edificio")
    floor = forms.ModelChoiceField(queryset=Floor.objects.none(), required=False, label="Piso")
    unit = forms.ModelChoiceField(queryset=Unit.objects.none(), required=False, label="Unidad")
    area = forms.ModelChoiceField(queryset=Area.objects.none(), required=False, label="Área común")
    purpose = forms.ChoiceField(choices=Walkthrough.Purpose.choices, label="Propósito")
    category = forms.ModelChoiceField(queryset=WalkthroughCategory.objects.none(), required=False, label="Categoría")
    inspector = forms.ModelChoiceField(queryset=None, required=False, label="Inspector")
    scheduled_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Fecha programada")
    drawing = forms.ModelChoiceField(queryset=Drawing.objects.none(), required=False, label="Plano/revisión")
    training_session = forms.ModelChoiceField(queryset=TrainingSession.objects.none(), required=False, label="Sesión de capacitación relacionada")

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.fields["building"].queryset = Building.objects.filter(project__organization=organization)
        self.fields["floor"].queryset = Floor.objects.filter(building__project__organization=organization)
        self.fields["unit"].queryset = Unit.objects.filter(building__project__organization=organization)
        self.fields["area"].queryset = Area.objects.filter(building__project__organization=organization)
        self.fields["category"].queryset = WalkthroughCategory.objects.filter(organization=organization)
        self.fields["inspector"].queryset = User.objects.filter(profile__organization=organization)
        self.fields["drawing"].queryset = Drawing.objects.filter(project__organization=organization, is_current=True)
        self.fields["training_session"].queryset = TrainingSession.objects.filter(building__project__organization=organization)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


@login_required
def walkthrough_list(request):
    organization = request.user.profile.organization
    walkthroughs = Walkthrough.objects.select_related("building", "unit", "category").filter(
        building__project__organization=organization
    )
    walkthroughs = _scope_to_accessible_projects(request, walkthroughs)
    purpose = request.GET.get("purpose")
    if purpose:
        walkthroughs = walkthroughs.filter(purpose=purpose)
    building_id = request.GET.get("building")
    if building_id:
        walkthroughs = walkthroughs.filter(building_id=building_id)
    walkthroughs = walkthroughs.order_by("-created_at")[:200]
    return render(request, "walkthroughs/list.html", {"walkthroughs": walkthroughs, "purposes": Walkthrough.Purpose.choices})


@login_required
def walkthrough_create(request):
    organization = request.user.profile.organization
    if request.method == "POST":
        form = CreateWalkthroughForm(request.POST, organization=organization)
        if form.is_valid():
            walkthrough = services.create_walkthrough(form.cleaned_data.pop("building"), request.user, **form.cleaned_data)
            messages.success(request, "Recorrido creado.")
            return redirect("walkthroughs:detail", pk=walkthrough.pk)
        messages.error(request, "Revise los datos del recorrido.")
    else:
        form = CreateWalkthroughForm(organization=organization)
    return render(request, "walkthroughs/create.html", {"form": form})


@login_required
def walkthrough_detail(request, pk):
    organization = request.user.profile.organization
    walkthrough = get_object_or_404(
        Walkthrough.objects.select_related("building", "floor", "unit", "area", "category", "inspector"),
        pk=pk, building__project__organization=organization,
    )
    if not wfsvc.user_can_access_project(request.user, walkthrough.building.project):
        raise Http404
    items = walkthrough.items.select_related("unit", "field_issue").prefetch_related("evidence").all()
    readiness = None
    if walkthrough.purpose in (Walkthrough.Purpose.PRE_DELIVERY_FINAL, Walkthrough.Purpose.FINAL_HANDOVER_DELIVERY):
        readiness = services.delivery_readiness_summary(walkthrough)
    upload_form = DocumentUploadForm(organization=organization)
    return render(request, "walkthroughs/detail.html", {
        "walkthrough": walkthrough, "items": items, "readiness": readiness, "upload_form": upload_form,
        "result_choices": WalkthroughItem.Result.choices, "condition_choices": WalkthroughItem.ConditionCheck.choices,
    })


@login_required
def walkthrough_populate_checklist(request, pk):
    organization = request.user.profile.organization
    walkthrough = get_object_or_404(Walkthrough, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        try:
            created = services.populate_checklist_from_template(walkthrough, request.user)
            messages.success(request, f"Se agregaron {len(created)} ítems desde la plantilla.")
        except services.WalkthroughError as exc:
            messages.error(request, str(exc))
    return redirect("walkthroughs:detail", pk=pk)


@login_required
def walkthrough_add_item(request, pk):
    organization = request.user.profile.organization
    walkthrough = get_object_or_404(Walkthrough, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        services.add_item(walkthrough, request.user, room_or_location=request.POST.get("room_or_location", ""))
        messages.success(request, "Ítem agregado.")
    return redirect("walkthroughs:detail", pk=pk)


@login_required
def walkthrough_set_progress(request, pk):
    organization = request.user.profile.organization
    walkthrough = get_object_or_404(Walkthrough, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        services.set_progress(walkthrough, request.user, progress_status=request.POST.get("progress_status", Walkthrough.ProgressStatus.IN_PROGRESS))
        messages.success(request, "Progreso actualizado.")
    return redirect("walkthroughs:detail", pk=pk)


@login_required
def walkthrough_delivery_decision(request, pk):
    organization = request.user.profile.organization
    walkthrough = get_object_or_404(Walkthrough, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        try:
            services.mark_delivery_decision(
                walkthrough, request.user, decision=request.POST.get("decision"),
                override_reason=request.POST.get("override_reason") or None,
            )
            messages.success(request, "Decisión de entrega registrada.")
        except services.WalkthroughError as exc:
            messages.error(request, str(exc))
    return redirect("walkthroughs:detail", pk=pk)


@login_required
def walkthrough_create_reinspection(request, pk):
    organization = request.user.profile.organization
    walkthrough = get_object_or_404(Walkthrough, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        new_walkthrough = services.create_reinspection_walkthrough(walkthrough, request.user)
        messages.success(request, "Recorrido de reinspección creado.")
        return redirect("walkthroughs:detail", pk=new_walkthrough.pk)
    return redirect("walkthroughs:detail", pk=pk)


@login_required
def walkthrough_create_next_sequential(request, pk):
    organization = request.user.profile.organization
    walkthrough = get_object_or_404(Walkthrough, pk=pk, building__project__organization=organization)
    next_unit_id = request.POST.get("next_unit")
    if request.method == "POST" and next_unit_id:
        next_unit = get_object_or_404(Unit, pk=next_unit_id, building__project__organization=organization)
        new_walkthrough = services.create_next_sequential_walkthrough(walkthrough, next_unit, request.user)
        messages.success(request, "Siguiente recorrido secuencial creado.")
        return redirect("walkthroughs:detail", pk=new_walkthrough.pk)
    return redirect("walkthroughs:detail", pk=pk)


@login_required
def item_record_result(request, pk):
    organization = request.user.profile.organization
    item = get_object_or_404(WalkthroughItem, pk=pk, walkthrough__building__project__organization=organization)
    if request.method == "POST":
        services.record_item_result(
            item, request.user, checklist_result=request.POST.get("checklist_result", WalkthroughItem.Result.PENDING),
            measurement=request.POST.get("measurement") or None, measurement_unit=request.POST.get("measurement_unit", ""),
            digital_level_reading=request.POST.get("digital_level_reading", ""),
            level_condition=request.POST.get("level_condition") or None, plumb_condition=request.POST.get("plumb_condition") or None,
            square_condition=request.POST.get("square_condition") or None, operational_test=request.POST.get("operational_test") or None,
            condition_found=request.POST.get("condition_found", ""), adjustment_performed=request.POST.get("adjustment_performed", ""),
            condition_after_adjustment=request.POST.get("condition_after_adjustment", ""),
            remaining_defect=request.POST.get("remaining_defect", ""),
            is_blocking_defect=bool(request.POST.get("is_blocking_defect")),
        )
        messages.success(request, "Resultado del ítem registrado.")
    return redirect("walkthroughs:detail", pk=item.walkthrough_id)


@login_required
def item_add_evidence(request, pk):
    organization = request.user.profile.organization
    item = get_object_or_404(WalkthroughItem, pk=pk, walkthrough__building__project__organization=organization)
    if request.method == "POST":
        form = DocumentUploadForm(request.POST, request.FILES, organization=organization)
        stage = request.POST.get("stage", WalkthroughItemEvidence.Stage.DURING)
        if form.is_valid():
            services.add_item_evidence(
                item, request.user, document_type=form.cleaned_data["document_type"], title=form.cleaned_data["title"],
                uploaded_file=form.cleaned_data["file"], stage=stage,
            )
            messages.success(request, "Evidencia adjuntada.")
        else:
            messages.error(request, "Revise el archivo de evidencia.")
    return redirect("walkthroughs:detail", pk=item.walkthrough_id)


@login_required
def item_create_issue(request, pk):
    from apps.fieldissues.services import FieldIssueError

    organization = request.user.profile.organization
    item = get_object_or_404(WalkthroughItem, pk=pk, walkthrough__building__project__organization=organization)
    if request.method == "POST":
        try:
            issue = services.create_issue_from_item(
                item, request.user, title=request.POST.get("title", ""), description=request.POST.get("description", ""),
            )
        except FieldIssueError as exc:
            messages.error(request, str(exc))
            return redirect("walkthroughs:detail", pk=item.walkthrough_id)
        messages.success(request, "Incidencia correctiva creada.")
        return redirect("fieldissues:detail", pk=issue.pk)
    return redirect("walkthroughs:detail", pk=item.walkthrough_id)
