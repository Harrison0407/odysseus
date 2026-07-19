from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.models import Department, UserRole
from apps.documents.views import DocumentUploadForm
from apps.projects.models import Building, Floor, Unit
from apps.workflow import services as wfsvc

from . import services
from .models import FieldIssue, FieldIssueEvidence, IssueCategory


def _is_management(user) -> bool:
    return UserRole.objects.filter(user=user, role__is_management=True, is_active=True).exists()


def _accessible_project_ids(user):
    return set(user.project_access.values_list("project_id", flat=True))


def _scope_to_accessible_projects(request, queryset):
    if _is_management(request.user):
        return queryset
    return queryset.filter(building__project_id__in=_accessible_project_ids(request.user))


class ReportIssueForm(forms.Form):
    building = forms.ModelChoiceField(queryset=Building.objects.none(), label="Edificio")
    floor = forms.ModelChoiceField(queryset=Floor.objects.none(), required=False, label="Piso")
    unit = forms.ModelChoiceField(queryset=Unit.objects.none(), required=False, label="Unidad/Apartamento")
    room_or_location = forms.CharField(max_length=255, required=False, label="Ambiente/ubicación exacta")
    category = forms.ModelChoiceField(queryset=IssueCategory.objects.none(), required=False, label="Categoría")
    title = forms.CharField(max_length=255, label="Título")
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False, label="Descripción")
    priority = forms.ChoiceField(choices=FieldIssue.Priority.choices, initial=FieldIssue.Priority.MEDIUM, label="Prioridad")
    photo = forms.ImageField(required=False, label="Foto")

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["building"].queryset = Building.objects.filter(project__organization=organization)
        self.fields["floor"].queryset = Floor.objects.filter(building__project__organization=organization)
        self.fields["unit"].queryset = Unit.objects.filter(building__project__organization=organization)
        self.fields["category"].queryset = IssueCategory.objects.filter(organization=organization)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


class AssignIssueForm(forms.Form):
    responsible_user = forms.ModelChoiceField(queryset=None, required=False, label="Responsable")
    responsible_department = forms.ModelChoiceField(queryset=Department.objects.none(), required=False, label="Equipo/Departamento")

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.fields["responsible_user"].queryset = User.objects.filter(profile__organization=organization)
        self.fields["responsible_department"].queryset = Department.objects.filter(organization=organization)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


@login_required
def issue_list(request):
    organization = request.user.profile.organization
    issues = FieldIssue.objects.select_related("building", "unit", "responsible_user", "responsible_department").filter(
        building__project__organization=organization
    )
    issues = _scope_to_accessible_projects(request, issues)

    view = request.GET.get("view")
    today = timezone.now().date()
    if view == "reported-by-me":
        issues = issues.filter(created_by=request.user)
    elif view == "assigned-to-me":
        issues = issues.filter(responsible_user=request.user)
    elif view == "assigned-to-my-team":
        department_ids = UserRole.objects.filter(user=request.user, is_active=True).values_list("department_id", flat=True)
        issues = issues.filter(responsible_department_id__in=department_ids)
    elif view == "overdue":
        issues = issues.filter(due_date__lt=today).exclude(status=FieldIssue.Status.VERIFIED_CLOSED)
    elif view == "awaiting-correction":
        issues = issues.filter(status__in=[FieldIssue.Status.ASSIGNED, FieldIssue.Status.IN_PROGRESS, FieldIssue.Status.RETURNED_FOR_CORRECTION])
    elif view == "awaiting-verification":
        issues = issues.filter(status__in=[FieldIssue.Status.READY_FOR_VERIFICATION, FieldIssue.Status.REINSPECTION])
    elif view == "rejected-reopened":
        issues = issues.filter(status__in=[FieldIssue.Status.RETURNED_FOR_CORRECTION, FieldIssue.Status.RESUBMITTED])
    elif view == "closed":
        issues = issues.filter(status=FieldIssue.Status.VERIFIED_CLOSED)

    building_id = request.GET.get("building")
    if building_id:
        issues = issues.filter(building_id=building_id)
    supplier_id = request.GET.get("supplier")
    if supplier_id:
        issues = issues.filter(supplier_id=supplier_id)

    issues = issues.order_by("-created_at")[:300]
    return render(request, "fieldissues/list.html", {"issues": issues, "current_view": view})


@login_required
def issue_report(request):
    organization = request.user.profile.organization
    if request.method == "POST":
        form = ReportIssueForm(request.POST, request.FILES, organization=organization)
        if form.is_valid():
            data = form.cleaned_data
            photo = data.pop("photo")
            try:
                issue = services.report_issue(
                    data.pop("building"), request.user, title=data.pop("title"), description=data.pop("description"),
                    category=data.pop("category"), priority=data.pop("priority"), floor=data.pop("floor"),
                    unit=data.pop("unit"), room_or_location=data.pop("room_or_location"),
                )
                if photo:
                    doc_type, _ = _photo_document_type(organization)
                    services.add_evidence(
                        issue, request.user, document_type=doc_type, title=f"Foto — {issue.title}",
                        uploaded_file=photo, stage=FieldIssueEvidence.Stage.BEFORE,
                    )
                messages.success(request, "Incidencia reportada.")
                return redirect("fieldissues:detail", pk=issue.pk)
            except services.FieldIssueError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los datos del reporte.")
    else:
        form = ReportIssueForm(organization=organization)
    return render(request, "fieldissues/report.html", {"form": form})


def _photo_document_type(organization):
    from apps.documents.models import DocumentType
    return DocumentType.objects.get_or_create(
        organization=organization, code="field-issue-photo", defaults={"name": "Foto de incidencia de campo"},
    )


@login_required
def issue_detail(request, pk):
    organization = request.user.profile.organization
    issue = get_object_or_404(
        FieldIssue.objects.select_related("building", "floor", "unit", "responsible_user", "responsible_department"),
        pk=pk, building__project__organization=organization,
    )
    if not wfsvc.user_can_access_project(request.user, issue.building.project):
        raise Http404
    evidence = issue.evidence.select_related("document").all()
    assign_form = AssignIssueForm(organization=organization)
    upload_form = DocumentUploadForm(organization=organization)
    return render(request, "fieldissues/detail.html", {
        "issue": issue, "evidence": evidence, "assign_form": assign_form, "upload_form": upload_form,
    })


@login_required
def issue_assign(request, pk):
    organization = request.user.profile.organization
    issue = get_object_or_404(FieldIssue, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        form = AssignIssueForm(request.POST, organization=organization)
        if form.is_valid():
            try:
                services.assign_issue(issue, request.user, **form.cleaned_data)
                messages.success(request, "Incidencia asignada.")
            except services.FieldIssueError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los datos de la asignación.")
    return redirect("fieldissues:detail", pk=pk)


@login_required
def issue_start_progress(request, pk):
    organization = request.user.profile.organization
    issue = get_object_or_404(FieldIssue, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        try:
            services.start_progress(issue, request.user)
            messages.success(request, "Incidencia en progreso.")
        except services.FieldIssueError as exc:
            messages.error(request, str(exc))
    return redirect("fieldissues:detail", pk=pk)


@login_required
def issue_record_correction(request, pk):
    organization = request.user.profile.organization
    issue = get_object_or_404(FieldIssue, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        try:
            services.record_correction(
                issue, request.user, description=request.POST.get("description", ""),
                before_evidence_waived_reason=request.POST.get("before_evidence_waived_reason") or None,
            )
            messages.success(request, "Corrección registrada.")
        except services.FieldIssueError as exc:
            messages.error(request, str(exc))
    return redirect("fieldissues:detail", pk=pk)


@login_required
def issue_mark_ready(request, pk):
    organization = request.user.profile.organization
    issue = get_object_or_404(FieldIssue, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        try:
            services.mark_ready_for_verification(issue, request.user)
            messages.success(request, "Incidencia lista para verificación.")
        except services.FieldIssueError as exc:
            messages.error(request, str(exc))
    return redirect("fieldissues:detail", pk=pk)


@login_required
def issue_verify_close(request, pk):
    organization = request.user.profile.organization
    issue = get_object_or_404(FieldIssue, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        try:
            services.verify_and_close_issue(issue, request.user, notes=request.POST.get("notes", ""))
            messages.success(request, "Incidencia verificada y cerrada.")
        except services.FieldIssueError as exc:
            messages.error(request, str(exc))
    return redirect("fieldissues:detail", pk=pk)


@login_required
def issue_reject(request, pk):
    organization = request.user.profile.organization
    issue = get_object_or_404(FieldIssue, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        try:
            services.reject_correction(issue, request.user, reason=request.POST.get("reason", ""))
            messages.success(request, "Corrección rechazada — devuelta para corrección.")
        except services.FieldIssueError as exc:
            messages.error(request, str(exc))
    return redirect("fieldissues:detail", pk=pk)


@login_required
def issue_resubmit(request, pk):
    organization = request.user.profile.organization
    issue = get_object_or_404(FieldIssue, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        try:
            services.resubmit_correction(issue, request.user, description=request.POST.get("description", ""))
            messages.success(request, "Corrección reenviada.")
        except services.FieldIssueError as exc:
            messages.error(request, str(exc))
    return redirect("fieldissues:detail", pk=pk)


@login_required
def issue_mark_reinspection(request, pk):
    organization = request.user.profile.organization
    issue = get_object_or_404(FieldIssue, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        try:
            services.mark_for_reinspection(issue, request.user)
            messages.success(request, "Incidencia en reinspección.")
        except services.FieldIssueError as exc:
            messages.error(request, str(exc))
    return redirect("fieldissues:detail", pk=pk)


@login_required
def issue_add_evidence(request, pk):
    organization = request.user.profile.organization
    issue = get_object_or_404(FieldIssue, pk=pk, building__project__organization=organization)
    if request.method == "POST":
        form = DocumentUploadForm(request.POST, request.FILES, organization=organization)
        stage = request.POST.get("stage", FieldIssueEvidence.Stage.DURING)
        if form.is_valid():
            _, is_duplicate = services.add_evidence(
                issue, request.user, document_type=form.cleaned_data["document_type"], title=form.cleaned_data["title"],
                uploaded_file=form.cleaned_data["file"], stage=stage,
            )
            if is_duplicate:
                messages.warning(request, "Evidencia adjuntada — mismo contenido (SHA-256) que un archivo ya cargado.")
            else:
                messages.success(request, "Evidencia adjuntada.")
        else:
            messages.error(request, "Revise el archivo de evidencia.")
    return redirect("fieldissues:detail", pk=pk)
