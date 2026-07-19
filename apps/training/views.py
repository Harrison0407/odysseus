from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import UserRole
from apps.documents.views import DocumentUploadForm
from apps.drawings.models import Drawing
from apps.projects.models import Building, Floor, Unit
from apps.workflow import services as wfsvc

from . import services
from .models import TrainingCategory, TrainingEvidence, TrainingSession


def _is_management(user) -> bool:
    return UserRole.objects.filter(user=user, role__is_management=True, is_active=True).exists()


def _scope_to_accessible_projects(request, queryset):
    if _is_management(request.user):
        return queryset
    accessible = set(request.user.project_access.values_list("project_id", flat=True))
    return queryset.filter(building__project_id__in=accessible)


class CreateSessionForm(forms.Form):
    building = forms.ModelChoiceField(queryset=Building.objects.none(), label="Edificio")
    floor = forms.ModelChoiceField(queryset=Floor.objects.none(), required=False, label="Piso")
    unit = forms.ModelChoiceField(queryset=Unit.objects.none(), required=False, label="Unidad")
    room_or_area = forms.CharField(max_length=255, required=False, label="Ambiente/área")
    category = forms.ModelChoiceField(queryset=TrainingCategory.objects.none(), required=False, label="Categoría")
    trainer = forms.ModelChoiceField(queryset=None, required=False, label="Capacitador")
    participants = forms.ModelMultipleChoiceField(queryset=None, required=False, label="Participantes")
    scheduled_at = forms.DateTimeField(required=False, label="Fecha/hora programada", widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    procedure_drawing = forms.ModelChoiceField(queryset=Drawing.objects.none(), required=False, label="Plano/procedimiento")

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.fields["building"].queryset = Building.objects.filter(project__organization=organization)
        self.fields["floor"].queryset = Floor.objects.filter(building__project__organization=organization)
        self.fields["unit"].queryset = Unit.objects.filter(building__project__organization=organization)
        self.fields["category"].queryset = TrainingCategory.objects.filter(organization=organization)
        self.fields["trainer"].queryset = User.objects.filter(profile__organization=organization)
        self.fields["participants"].queryset = User.objects.filter(profile__organization=organization)
        self.fields["procedure_drawing"].queryset = Drawing.objects.filter(project__organization=organization, is_current=True)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


@login_required
def session_list(request):
    organization = request.user.profile.organization
    sessions = TrainingSession.objects.select_related("building", "category", "trainer").filter(
        building__project__organization=organization
    )
    sessions = _scope_to_accessible_projects(request, sessions)
    if request.GET.get("reference") == "1":
        sessions = sessions.filter(is_approved_reference_installation=True)
    sessions = sessions.order_by("-created_at")[:200]
    return render(request, "training/list.html", {"sessions": sessions})


@login_required
def session_create(request):
    organization = request.user.profile.organization
    if request.method == "POST":
        form = CreateSessionForm(request.POST, organization=organization)
        if form.is_valid():
            data = form.cleaned_data
            session = services.create_training_session(
                data.pop("building"), request.user, trainer=data.pop("trainer"),
                participants=data.pop("participants"), category=data.pop("category"), floor=data.pop("floor"),
                unit=data.pop("unit"), room_or_area=data.pop("room_or_area"), scheduled_at=data.pop("scheduled_at"),
                procedure_drawing=data.pop("procedure_drawing"),
            )
            messages.success(request, "Sesión de capacitación creada.")
            return redirect("training:detail", pk=session.pk)
        messages.error(request, "Revise los datos de la sesión.")
    else:
        form = CreateSessionForm(organization=organization)
    return render(request, "training/create.html", {"form": form})


@login_required
def session_detail(request, pk):
    organization = request.user.profile.organization
    session = get_object_or_404(
        TrainingSession.objects.select_related("building", "category", "trainer").prefetch_related("participants", "checklist_items", "evidence__document", "acknowledgements"),
        pk=pk, building__project__organization=organization,
    )
    if not wfsvc.user_can_access_project(request.user, session.building.project):
        raise Http404
    upload_form = DocumentUploadForm(organization=organization)
    return render(request, "training/detail.html", {"session": session, "upload_form": upload_form})


@login_required
def session_start(request, pk):
    organization = request.user.profile.organization
    session = get_object_or_404(TrainingSession, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        try:
            services.start_session(session, request.user)
            messages.success(request, "Sesión iniciada.")
        except services.TrainingError as exc:
            messages.error(request, str(exc))
    return redirect("training:detail", pk=pk)


@login_required
def session_finish(request, pk):
    organization = request.user.profile.organization
    session = get_object_or_404(TrainingSession, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        try:
            services.finish_session(
                session, request.user, defects_discovered=request.POST.get("defects_discovered", ""),
                adjustment_demonstrated=request.POST.get("adjustment_demonstrated", ""),
                remaining_actions=request.POST.get("remaining_actions", ""),
                used_digital_level=bool(request.POST.get("used_digital_level")),
                digital_level_reading=request.POST.get("digital_level_reading", ""),
            )
            messages.success(request, "Sesión finalizada.")
        except services.TrainingError as exc:
            messages.error(request, str(exc))
    return redirect("training:detail", pk=pk)


@login_required
def session_add_checklist_item(request, pk):
    organization = request.user.profile.organization
    session = get_object_or_404(TrainingSession, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        services.add_checklist_item(
            session, request.user, description=request.POST.get("description", ""),
            result=request.POST.get("result", "pass"), notes=request.POST.get("notes", ""),
        )
        messages.success(request, "Ítem de checklist agregado.")
    return redirect("training:detail", pk=pk)


@login_required
def session_add_evidence(request, pk):
    organization = request.user.profile.organization
    session = get_object_or_404(TrainingSession, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        form = DocumentUploadForm(request.POST, request.FILES, organization=organization)
        stage = request.POST.get("stage", TrainingEvidence.Stage.DURING)
        if form.is_valid():
            _, is_duplicate = services.add_evidence(
                session, request.user, document_type=form.cleaned_data["document_type"], title=form.cleaned_data["title"],
                uploaded_file=form.cleaned_data["file"], stage=stage,
            )
            messages.success(request, "Evidencia adjuntada." + (" (contenido duplicado)" if is_duplicate else ""))
        else:
            messages.error(request, "Revise el archivo de evidencia.")
    return redirect("training:detail", pk=pk)


@login_required
def session_acknowledge(request, pk):
    organization = request.user.profile.organization
    session = get_object_or_404(TrainingSession, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        try:
            services.acknowledge_participation(session, request.user, notes=request.POST.get("notes", ""))
            messages.success(request, "Participación reconocida.")
        except services.TrainingError as exc:
            messages.error(request, str(exc))
    return redirect("training:detail", pk=pk)


@login_required
def session_supervisor_sign_off(request, pk):
    organization = request.user.profile.organization
    session = get_object_or_404(TrainingSession, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        try:
            services.supervisor_sign_off(session, request.user, notes=request.POST.get("notes", ""))
            messages.success(request, "Firma de supervisor registrada.")
        except services.TrainingError as exc:
            messages.error(request, str(exc))
    return redirect("training:detail", pk=pk)


@login_required
def session_approve_reference(request, pk):
    organization = request.user.profile.organization
    session = get_object_or_404(TrainingSession, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        try:
            services.approve_as_reference_installation(session, request.user, notes=request.POST.get("notes", ""))
            messages.success(request, "Instalación de referencia aprobada.")
        except services.TrainingError as exc:
            messages.error(request, str(exc))
    return redirect("training:detail", pk=pk)


@login_required
def session_create_issue(request, pk):
    organization = request.user.profile.organization
    session = get_object_or_404(TrainingSession, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        issue = services.create_issue_from_training(
            session, request.user, title=request.POST.get("title", ""), description=request.POST.get("description", ""),
        )
        messages.success(request, "Incidencia creada desde la sesión.")
        return redirect("fieldissues:detail", pk=issue.pk)
    return redirect("training:detail", pk=pk)
