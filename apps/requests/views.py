from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.shortcuts import get_object_or_404, redirect, render

from apps.audit import services as audit_services
from apps.documents.views import DocumentUploadForm
from apps.inventory.models import InventoryLot, InventoryReservation
from apps.items.models import Item
from apps.projects.models import Project
from apps.workflow import services as wfsvc
from apps.workflow.models import GateDefinition, Handoff, HandoffStatus

from . import services as rsvc
from .models import (
    AcceptanceRecord,
    Delivery,
    DeliveryLine,
    InspectionRecord,
    InstallationRecord,
    MaterialRequest,
    MaterialRequestLine,
    ProjectReceipt,
    PunchListItem,
    RequestApproval,
)


def _form_control(fields):
    for field in fields.values():
        field.widget.attrs.setdefault("class", "form-control")


def _is_management(user) -> bool:
    from apps.accounts.models import UserRole

    return UserRole.objects.filter(user=user, role__is_management=True, is_active=True).exists()


def _accessible_project_ids(user):
    return set(user.project_access.values_list("project_id", flat=True))


def _scope_to_accessible_projects(request, queryset, project_lookup):
    """Same project-isolation rule as `_deny_cross_project`, applied to a
    list view's queryset so a non-management user without project access
    never even sees the row (not just gets denied on click-through)."""
    if _is_management(request.user):
        return queryset
    return queryset.filter(**{f"{project_lookup}__in": _accessible_project_ids(request.user)})


def _add_evidence(request, target):
    """Shared POST handler for the "adjuntar evidencia" form on the
    delivery/installation/inspection detail pages — reuses
    apps.documents.views.DocumentUploadForm (extension/size validation,
    SHA-256 dedup) and apps.audit.services.attach_evidence (the one place
    a file becomes evidence linked to an arbitrary target) rather than
    reimplementing upload handling here."""
    organization = getattr(request.user.profile, "organization", None)
    form = DocumentUploadForm(request.POST, request.FILES, organization=organization)
    if not form.is_valid():
        messages.error(request, "Revise el archivo de evidencia — " + "; ".join(
            f"{field}: {', '.join(errs)}" for field, errs in form.errors.items()
        ))
        return
    _, is_duplicate = audit_services.attach_evidence(
        target, request.user,
        document_type=form.cleaned_data["document_type"],
        title=form.cleaned_data["title"],
        uploaded_file=form.cleaned_data["file"],
    )
    if is_duplicate:
        messages.warning(request, "Evidencia adjuntada — mismo contenido (SHA-256) que un archivo ya cargado.")
    else:
        messages.success(request, "Evidencia adjuntada.")


def _deny_cross_project(request, target) -> bool:
    """Enforced at the view/service boundary, not just hidden in a
    template — cross-project isolation for the delivery/installation/
    inspection/acceptance chain, mirroring apps.workflow.services.can_view_handoff."""
    if not wfsvc.can_view_target(request.user, target):
        messages.error(request, "No tiene acceso a este proyecto.")
        return True
    return False


class MaterialRequestForm(forms.ModelForm):
    item = forms.ModelChoiceField(queryset=Item.objects.all(), label="Artículo")
    quantity_requested = forms.DecimalField(max_digits=14, decimal_places=3, label="Cantidad")

    class Meta:
        model = MaterialRequest
        fields = ["project", "building", "floor", "unit", "area", "priority", "needed_by_date", "purpose"]
        widgets = {"needed_by_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _form_control(self.fields)


@login_required
def request_list(request):
    requests_qs = MaterialRequest.objects.select_related("project", "building").order_by("-created_at")
    requests_qs = _scope_to_accessible_projects(request, requests_qs, "project_id")

    project_id = request.GET.get("project")
    if project_id:
        requests_qs = requests_qs.filter(project_id=project_id)
    status = request.GET.get("status")
    if status:
        requests_qs = requests_qs.filter(status=status)

    return render(request, "requests/list.html", {
        "requests": requests_qs,
        "projects": Project.objects.all(),
        "statuses": MaterialRequest.Status.choices,
    })


@login_required
def request_create(request):
    if request.method == "POST":
        form = MaterialRequestForm(request.POST)
        if form.is_valid():
            mr = form.save(commit=False)
            mr.requester = request.user
            mr.created_by = request.user
            mr.save()
            MaterialRequestLine.objects.create(
                request=mr,
                item=form.cleaned_data["item"],
                quantity_requested=form.cleaned_data["quantity_requested"],
                created_by=request.user,
            )
            messages.success(request, "Solicitud creada.")
            return redirect("requests:detail", pk=mr.pk)
    else:
        form = MaterialRequestForm()
    return render(request, "requests/create.html", {"form": form})


class ReserveForm(forms.Form):
    lot = forms.ModelChoiceField(queryset=InventoryLot.objects.none(), label="Lote")
    quantity = forms.DecimalField(max_digits=14, decimal_places=3, label="Cantidad a reservar")

    def __init__(self, *args, item=None, **kwargs):
        super().__init__(*args, **kwargs)
        if item is not None:
            self.fields["lot"].queryset = InventoryLot.objects.filter(item=item)
        _form_control(self.fields)


@login_required
def request_detail(request, pk):
    mr = get_object_or_404(
        MaterialRequest.objects.select_related("project", "building", "unit", "area").prefetch_related("lines__item"),
        pk=pk,
    )
    if _deny_cross_project(request, mr):
        return redirect("requests:list")
    content_type = ContentType.objects.get_for_model(MaterialRequest)
    available_gates = GateDefinition.objects.filter(
        organization=getattr(request.user.profile, "organization", None),
        target_content_type=content_type,
        code="warehouse_to_project",
    )
    existing_handoffs = Handoff.objects.filter(
        content_type=content_type, object_id=mr.pk
    ).select_related("gate_definition", "to_department").order_by("-created_at")

    lines_with_forms = []
    for line in mr.lines.all():
        reservations = [
            (reservation, rsvc.reservation_remaining_quantity(reservation))
            for reservation in InventoryReservation.objects.filter(material_request_line=line, is_active=True)
        ]
        lines_with_forms.append({
            "line": line,
            "reserve_form": ReserveForm(item=line.item, prefix=str(line.pk)),
            "reservations": [(r, remaining) for r, remaining in reservations if remaining > 0],
        })
    deliveries = Delivery.objects.filter(dispatch__pick_list__request=mr).select_related("dispatch")

    return render(
        request,
        "requests/detail.html",
        {
            "request_obj": mr,
            "request_content_type_id": content_type.id,
            "available_gates": available_gates,
            "existing_handoffs": existing_handoffs,
            "lines_with_forms": lines_with_forms,
            "deliveries": deliveries,
        },
    )


@login_required
def request_approve(request, pk):
    mr = get_object_or_404(MaterialRequest, pk=pk)
    if _deny_cross_project(request, mr):
        return redirect("requests:list")
    if request.method == "POST":
        for line in mr.lines.all():
            if line.quantity_approved is None:
                line.quantity_approved = line.quantity_requested
                line.save(update_fields=["quantity_approved"])
        mr.status = MaterialRequest.Status.APPROVED
        mr.save(update_fields=["status"])
        RequestApproval.objects.create(request=mr, approver=request.user, decision="approved", created_by=request.user)
        messages.success(request, "Solicitud aprobada — cantidades aprobadas según lo solicitado.")
    return redirect("requests:detail", pk=pk)


@login_required
def request_reserve_line(request, pk, line_pk):
    line = get_object_or_404(MaterialRequestLine, pk=line_pk, request_id=pk)
    if _deny_cross_project(request, line.request):
        return redirect("requests:list")
    if request.method == "POST":
        form = ReserveForm(request.POST, item=line.item, prefix=str(line.pk))
        if form.is_valid():
            try:
                rsvc.reserve_line(line, form.cleaned_data["lot"], form.cleaned_data["quantity"], request.user)
                messages.success(request, "Cantidad reservada.")
            except rsvc.QuantityInvariantError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los datos de la reserva.")
    return redirect("requests:detail", pk=pk)


@login_required
def request_dispatch(request, pk):
    """Dispatches explicit per-reservation quantities submitted by the
    form on the request detail page — a single request line can be split
    across several reservations/lots in one submission (each reservation
    field defaults to that reservation's full undispatched remainder, but
    can be edited down for a partial dispatch of a specific lot).
    apps.requests.services.create_dispatch owns the actual quantity
    guards; this view only parses the submitted quantities."""
    mr = get_object_or_404(MaterialRequest, pk=pk)
    if _deny_cross_project(request, mr):
        return redirect("requests:list")
    if request.method == "POST":
        entries = []
        reservations = InventoryReservation.objects.filter(
            material_request_line__request=mr, is_active=True
        ).select_related("material_request_line")
        for reservation in reservations:
            raw = request.POST.get(f"resv-{reservation.pk}-quantity")
            if not raw:
                continue
            try:
                quantity = Decimal(raw)
            except (InvalidOperation, TypeError):
                messages.error(request, "Cantidad inválida en una de las reservas.")
                return redirect("requests:detail", pk=pk)
            if quantity <= 0:
                continue
            entries.append((reservation.material_request_line, reservation, quantity))
        if not entries:
            messages.error(request, "No hay cantidades pendientes de despachar.")
        else:
            try:
                dispatch = rsvc.create_dispatch(mr, entries, request.user)
                rsvc.get_or_create_delivery(dispatch, request.user)
                messages.success(request, "Despacho creado y entrega inicializada.")
            except rsvc.QuantityInvariantError as exc:
                messages.error(request, str(exc))
    return redirect("requests:detail", pk=pk)


# ---------------------------------------------------------------------------
# Deliveries
# ---------------------------------------------------------------------------


@login_required
def delivery_list(request):
    deliveries = Delivery.objects.select_related(
        "dispatch__pick_list__request__project", "dispatch__pick_list__request__building"
    ).order_by("-created_at")
    deliveries = _scope_to_accessible_projects(request, deliveries, "dispatch__pick_list__request__project_id")

    project_id = request.GET.get("project")
    if project_id:
        deliveries = deliveries.filter(dispatch__pick_list__request__project_id=project_id)
    pending_only = request.GET.get("pendientes") == "1"
    if pending_only:
        deliveries = deliveries.filter(accepted__isnull=True)

    return render(request, "requests/delivery_list.html", {
        "deliveries": deliveries, "projects": Project.objects.all(),
    })


class DeliveryLineForm(forms.Form):
    quantity_accepted = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0, initial=0)
    quantity_rejected = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0, initial=0)
    quantity_damaged = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0, initial=0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _form_control(self.fields)


class CompleteDeliveryForm(forms.Form):
    accepted = forms.ChoiceField(choices=[("yes", "Aceptada"), ("no", "Rechazada / fallida")], label="Resultado")
    rejected_quantity_note = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False, label="Nota")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _form_control(self.fields)


class ProjectReceiptForm(forms.ModelForm):
    class Meta:
        model = ProjectReceipt
        fields = [
            "confirmed_destination_building", "confirmed_destination_unit", "confirmed_destination_area",
            "site_damage_reported", "missing_items_reported", "wrong_material_reported", "notes",
        ]
        labels = {
            "confirmed_destination_building": "Edificio de destino confirmado",
            "confirmed_destination_unit": "Unidad de destino confirmada",
            "confirmed_destination_area": "Área común de destino confirmada",
            "site_damage_reported": "Daño reportado en sitio",
            "missing_items_reported": "Faltantes reportados",
            "wrong_material_reported": "Material incorrecto reportado",
            "notes": "Notas",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _form_control(self.fields)


@login_required
def delivery_detail(request, pk):
    delivery = get_object_or_404(
        Delivery.objects.select_related("dispatch__pick_list__request__project"), pk=pk
    )
    if _deny_cross_project(request, delivery):
        return redirect("requests:delivery-list")
    lines_with_forms = [
        (line, DeliveryLineForm(initial={
            "quantity_accepted": line.quantity_accepted,
            "quantity_rejected": line.quantity_rejected,
            "quantity_damaged": line.quantity_damaged,
        }, prefix=str(line.pk)))
        for line in delivery.lines.select_related("dispatch_line__request_line__item")
    ]

    content_type = ContentType.objects.get_for_model(Delivery)
    available_gates = GateDefinition.objects.filter(
        organization=getattr(request.user.profile, "organization", None),
        target_content_type=content_type, code="project_delivery_to_installation",
    )
    existing_handoffs = Handoff.objects.filter(
        content_type=content_type, object_id=delivery.pk
    ).select_related("gate_definition", "to_department").order_by("-created_at")

    return render(request, "requests/delivery_detail.html", {
        "delivery": delivery,
        "lines_with_forms": lines_with_forms,
        "complete_form": CompleteDeliveryForm(),
        "receipt_form": ProjectReceiptForm(),
        "delivery_content_type_id": content_type.id,
        "available_gates": available_gates,
        "existing_handoffs": existing_handoffs,
        "project_receipts": delivery.project_receipts.all(),
        "evidence": audit_services.list_evidence(delivery),
        "evidence_form": DocumentUploadForm(organization=getattr(request.user.profile, "organization", None)),
    })


@login_required
def delivery_add_evidence(request, pk):
    delivery = get_object_or_404(Delivery, pk=pk)
    if _deny_cross_project(request, delivery):
        return redirect("requests:delivery-list")
    if request.method == "POST":
        _add_evidence(request, delivery)
    return redirect("requests:delivery-detail", pk=pk)


@login_required
def delivery_record_line(request, pk, line_pk):
    delivery_line = get_object_or_404(DeliveryLine, pk=line_pk, delivery_id=pk)
    if _deny_cross_project(request, delivery_line.delivery):
        return redirect("requests:delivery-list")
    if request.method == "POST":
        form = DeliveryLineForm(request.POST, prefix=str(delivery_line.pk))
        if form.is_valid():
            try:
                rsvc.record_delivery_line(
                    delivery_line,
                    quantity_accepted=form.cleaned_data["quantity_accepted"],
                    quantity_rejected=form.cleaned_data["quantity_rejected"],
                    quantity_damaged=form.cleaned_data["quantity_damaged"],
                    user=request.user,
                )
                messages.success(request, "Línea de entrega registrada.")
            except rsvc.QuantityInvariantError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise las cantidades ingresadas.")
    return redirect("requests:delivery-detail", pk=pk)


@login_required
def delivery_complete(request, pk):
    delivery = get_object_or_404(Delivery, pk=pk)
    if _deny_cross_project(request, delivery):
        return redirect("requests:delivery-list")
    if request.method == "POST":
        form = CompleteDeliveryForm(request.POST)
        if form.is_valid():
            rsvc.complete_delivery(
                delivery, request.user,
                accepted=(form.cleaned_data["accepted"] == "yes"),
                rejected_quantity_note=form.cleaned_data["rejected_quantity_note"],
            )
            messages.success(request, "Entrega marcada como completa.")
    return redirect("requests:delivery-detail", pk=pk)


@login_required
def delivery_create_receipt(request, pk):
    delivery = get_object_or_404(Delivery, pk=pk)
    if _deny_cross_project(request, delivery):
        return redirect("requests:delivery-list")
    if request.method == "POST":
        form = ProjectReceiptForm(request.POST)
        if form.is_valid():
            rsvc.create_project_receipt(delivery, request.user, **form.cleaned_data)
            messages.success(request, "Recepción de proyecto registrada.")
        else:
            messages.error(request, "Revise los datos de la recepción.")
    return redirect("requests:delivery-detail", pk=pk)


# ---------------------------------------------------------------------------
# Installations
# ---------------------------------------------------------------------------


@login_required
def installation_list(request):
    installations = InstallationRecord.objects.select_related(
        "project_receipt__delivery", "assigned_installer", "delivery_line__dispatch_line__request_line__item"
    ).order_by("-created_at")
    installations = _scope_to_accessible_projects(
        request, installations, "project_receipt__delivery__dispatch__pick_list__request__project_id"
    )

    status = request.GET.get("estado")
    if status == "incompletas":
        installations = installations.filter(is_complete=False)
    elif status == "rework":
        installations = installations.filter(requires_rework=True)

    return render(request, "requests/installation_list.html", {"installations": installations})


def _department_for_gate(organization, gate_code, attr="from_department"):
    """The department actually configured to do a stage's work — read
    from the same `GateDefinition` rows the gate-evaluation engine uses,
    never a hard-coded department name — used to scope who can be picked
    as an installer/assignee for that stage."""
    gate = GateDefinition.objects.filter(organization=organization, code=gate_code).first()
    return getattr(gate, attr, None) if gate else None


def _assignable_users(organization, project=None, department=None):
    """Users eligible to be assigned installation/inspection work:
    active accounts, scoped to the configured department (never every
    user in the organization), further narrowed to users with explicit
    project access when the project has any such grants configured (a
    project with no `UserProjectAccess` rows at all falls back to plain
    department scoping, so a pilot that hasn't set up per-project access
    yet isn't left with an empty dropdown)."""
    from django.contrib.auth import get_user_model

    from apps.accounts.models import UserProjectAccess

    User = get_user_model()
    qs = User.objects.filter(is_active=True, profile__organization=organization)
    if department is not None:
        qs = qs.filter(user_roles__department=department, user_roles__is_active=True)
    if project is not None:
        project_user_ids = set(UserProjectAccess.objects.filter(project=project).values_list("user_id", flat=True))
        if project_user_ids:
            qs = qs.filter(pk__in=project_user_ids)
    return qs.distinct().order_by("first_name", "username")


class InstallationCreateForm(forms.ModelForm):
    class Meta:
        model = InstallationRecord
        fields = ["delivery_line", "assigned_installer", "scheduled_date"]
        widgets = {"scheduled_date": forms.DateInput(attrs={"type": "date"})}
        labels = {
            "delivery_line": "Línea de entrega (producto y cantidad entregada)",
            "assigned_installer": "Instalador asignado",
            "scheduled_date": "Fecha programada",
        }

    def __init__(self, *args, project_receipt=None, **kwargs):
        super().__init__(*args, **kwargs)
        if project_receipt is not None:
            self.fields["delivery_line"].queryset = project_receipt.delivery.lines.all()
            project = wfsvc.resolve_project(project_receipt.delivery)
            organization = wfsvc.resolve_organization(project_receipt.delivery)
            department = _department_for_gate(organization, "installation_to_inspection", "from_department")
            self.fields["assigned_installer"].queryset = _assignable_users(organization, project, department)
        _form_control(self.fields)


@login_required
def installation_create(request, receipt_pk):
    receipt = get_object_or_404(ProjectReceipt, pk=receipt_pk)
    if _deny_cross_project(request, receipt.delivery):
        return redirect("requests:delivery-list")
    if request.method == "POST":
        form = InstallationCreateForm(request.POST, project_receipt=receipt)
        if form.is_valid():
            installation = rsvc.create_installation_record(receipt, request.user, **form.cleaned_data)
            messages.success(request, "Registro de instalación creado.")
            return redirect("requests:installation-detail", pk=installation.pk)
        messages.error(request, "Revise los datos de la instalación.")
    else:
        form = InstallationCreateForm(project_receipt=receipt)
    return render(request, "requests/installation_create.html", {"form": form, "receipt": receipt})


class InstallationProgressForm(forms.Form):
    quantity_installed = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0, initial=0)
    quantity_not_used = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0, initial=0)
    quantity_damaged = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0, initial=0)
    is_complete = forms.BooleanField(required=False, label="Instalación completa")
    mark_completed = forms.BooleanField(required=False, label="Registrar fecha de finalización ahora")
    requires_rework = forms.BooleanField(required=False, label="Requiere retrabajo")
    missing_components_note = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    observations = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    override_reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}), required=False,
        label="Motivo de anulación (solo si excede lo entregado válidamente)",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _form_control(self.fields)


@login_required
def installation_detail(request, pk):
    installation = get_object_or_404(
        InstallationRecord.objects.select_related(
            "project_receipt__delivery", "assigned_installer", "installed_by", "delivery_line__dispatch_line__request_line__item"
        ).prefetch_related("inspections__punch_list_items"),
        pk=pk,
    )
    if _deny_cross_project(request, installation):
        return redirect("requests:installation-list")
    content_type = ContentType.objects.get_for_model(InstallationRecord)
    org = getattr(request.user.profile, "organization", None)
    available_gates = GateDefinition.objects.filter(
        organization=org, target_content_type=content_type,
        code__in=["installation_to_inspection", "inspection_to_acceptance"],
    )
    existing_handoffs = Handoff.objects.filter(
        content_type=content_type, object_id=installation.pk
    ).select_related("gate_definition", "to_department").order_by("-created_at")

    submitted_acceptance_handoff = existing_handoffs.filter(
        gate_definition__code="inspection_to_acceptance", status=HandoffStatus.SUBMITTED
    ).first()
    accepted_acceptance_handoff = existing_handoffs.filter(
        gate_definition__code="inspection_to_acceptance", status=HandoffStatus.ACCEPTED
    ).first()
    acceptance = getattr(installation, "acceptance", None)
    can_finalize = (
        accepted_acceptance_handoff is not None and acceptance is None
        and wfsvc.can_accept_handoff(request.user, accepted_acceptance_handoff)
    )

    return render(request, "requests/installation_detail.html", {
        "installation": installation,
        "progress_form": InstallationProgressForm(initial={
            "quantity_installed": installation.quantity_installed,
            "quantity_not_used": installation.quantity_not_used,
            "quantity_damaged": installation.quantity_damaged,
            "is_complete": installation.is_complete,
            "requires_rework": installation.requires_rework,
        }),
        "installation_content_type_id": content_type.id,
        "available_gates": available_gates,
        "existing_handoffs": existing_handoffs,
        "submitted_acceptance_handoff": submitted_acceptance_handoff,
        "accepted_acceptance_handoff": accepted_acceptance_handoff,
        "can_finalize": can_finalize,
        "acceptance": acceptance,
        "inspections": installation.inspections.order_by("-created_at"),
        "evidence": audit_services.list_evidence(installation),
        "evidence_form": DocumentUploadForm(organization=org),
    })


@login_required
def installation_add_evidence(request, pk):
    installation = get_object_or_404(InstallationRecord, pk=pk)
    if _deny_cross_project(request, installation):
        return redirect("requests:installation-list")
    if request.method == "POST":
        _add_evidence(request, installation)
    return redirect("requests:installation-detail", pk=pk)


@login_required
def installation_progress(request, pk):
    installation = get_object_or_404(InstallationRecord, pk=pk)
    if _deny_cross_project(request, installation):
        return redirect("requests:installation-list")
    if request.method == "POST":
        form = InstallationProgressForm(request.POST)
        if form.is_valid():
            data = dict(form.cleaned_data)
            override_reason = data.pop("override_reason", "")
            try:
                rsvc.record_installation_progress(installation, request.user, override_reason=override_reason or None, **data)
                messages.success(request, "Progreso de instalación registrado.")
            except rsvc.QuantityInvariantError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los datos de progreso.")
    return redirect("requests:installation-detail", pk=pk)


@login_required
def installation_acknowledge(request, pk):
    installation = get_object_or_404(InstallationRecord, pk=pk)
    if _deny_cross_project(request, installation):
        return redirect("requests:installation-list")
    if request.method == "POST":
        rsvc.acknowledge_installation(installation, request.user)
        messages.success(request, "Instalación reconocida.")
    return redirect("requests:installation-detail", pk=pk)


@login_required
def installation_supervisor_confirm(request, pk):
    installation = get_object_or_404(InstallationRecord, pk=pk)
    if _deny_cross_project(request, installation):
        return redirect("requests:installation-list")
    if request.method == "POST":
        rsvc.confirm_installation_supervisor(installation, request.user)
        messages.success(request, "Instalación confirmada por supervisor.")
    return redirect("requests:installation-detail", pk=pk)


class InspectionForm(forms.Form):
    result = forms.ChoiceField(choices=InspectionRecord.Result.choices, label="Resultado")
    inspected_quantity = forms.DecimalField(max_digits=14, decimal_places=3, required=False)
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    defects = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), required=False,
        label="Defectos (uno por línea, cada uno crea un ítem de punch-list bloqueante)",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _form_control(self.fields)


@login_required
def installation_create_inspection(request, pk):
    installation = get_object_or_404(InstallationRecord, pk=pk)
    if _deny_cross_project(request, installation):
        return redirect("requests:installation-list")
    if request.method == "POST":
        form = InspectionForm(request.POST)
        if form.is_valid():
            defect_lines = [d.strip() for d in form.cleaned_data["defects"].splitlines() if d.strip()]
            punch_items = [{"description": d, "is_blocking": True} for d in defect_lines]
            inspection = rsvc.create_inspection(
                installation, request.user,
                result=form.cleaned_data["result"],
                inspected_quantity=form.cleaned_data["inspected_quantity"],
                notes=form.cleaned_data["notes"],
                punch_list_items=punch_items,
            )
            messages.success(request, f"Inspección registrada: {inspection.get_result_display()}.")
        else:
            messages.error(request, "Revise los datos de la inspección.")
    return redirect("requests:installation-detail", pk=pk)


@login_required
def installation_final_accept(request, pk):
    """Records the domain-specific decision detail (accepted vs.
    conditional + notes) for an inspection_to_acceptance handoff that has
    already been accepted through the generic, reused
    apps.workflow.services.accept_handoff (the "Aceptar" button on the
    handoff itself, or the workflow inbox) — this view never performs the
    accept transition itself, only adds the domain detail the generic
    Handoff model has no reason to know about. The handoff must already
    be ACCEPTED before this is offered (see installation_detail)."""
    installation = get_object_or_404(InstallationRecord, pk=pk)
    if _deny_cross_project(request, installation):
        return redirect("requests:installation-list")
    if request.method == "POST":
        content_type = ContentType.objects.get_for_model(InstallationRecord)
        handoff = get_object_or_404(
            Handoff, content_type=content_type, object_id=installation.pk,
            gate_definition__code="inspection_to_acceptance", status=HandoffStatus.ACCEPTED,
        )
        if not wfsvc.can_accept_handoff(request.user, handoff):
            messages.error(request, "No tiene permiso para registrar la aceptación final.")
            return redirect("requests:installation-detail", pk=pk)
        decision = request.POST.get("decision", AcceptanceRecord.Decision.ACCEPTED)
        conditions_note = request.POST.get("conditions_note", "")
        try:
            rsvc.record_final_acceptance(installation, request.user, decision=decision, conditions_note=conditions_note)
            messages.success(request, "Aceptación final registrada.")
        except rsvc.QuantityInvariantError as exc:
            messages.error(request, str(exc))
    return redirect("requests:installation-detail", pk=pk)


# ---------------------------------------------------------------------------
# Inspections (dedicated list/detail, punch-list actions)
# ---------------------------------------------------------------------------


@login_required
def inspection_list(request):
    inspections = InspectionRecord.objects.select_related("installation", "inspector").order_by("-created_at")
    inspections = _scope_to_accessible_projects(
        request, inspections, "installation__project_receipt__delivery__dispatch__pick_list__request__project_id"
    )
    result = request.GET.get("resultado")
    if result:
        inspections = inspections.filter(result=result)
    return render(request, "requests/inspection_list.html", {"inspections": inspections})


@login_required
def inspection_detail(request, pk):
    inspection = get_object_or_404(
        InspectionRecord.objects.select_related("installation", "inspector", "previous_inspection")
        .prefetch_related("punch_list_items", "reinspections"),
        pk=pk,
    )
    if _deny_cross_project(request, inspection):
        return redirect("requests:inspection-list")
    return render(request, "requests/inspection_detail.html", {
        "inspection": inspection,
        "evidence": audit_services.list_evidence(inspection),
        "evidence_form": DocumentUploadForm(organization=getattr(request.user.profile, "organization", None)),
    })


@login_required
def inspection_add_evidence(request, pk):
    inspection = get_object_or_404(InspectionRecord, pk=pk)
    if _deny_cross_project(request, inspection):
        return redirect("requests:inspection-list")
    if request.method == "POST":
        _add_evidence(request, inspection)
    return redirect("requests:inspection-detail", pk=pk)


@login_required
def inspection_close_item(request, pk, item_pk):
    item = get_object_or_404(PunchListItem, pk=item_pk, inspection_id=pk)
    if _deny_cross_project(request, item.inspection):
        return redirect("requests:inspection-list")
    if request.method == "POST":
        try:
            rsvc.close_punch_list_item(item, request.user, resolution_notes=request.POST.get("resolution_notes", ""))
            messages.success(request, "Defecto cerrado.")
        except rsvc.QuantityInvariantError as exc:
            messages.error(request, str(exc))
    return redirect("requests:inspection-detail", pk=pk)


@login_required
def inspection_sign_off(request, pk):
    inspection = get_object_or_404(InspectionRecord, pk=pk)
    if _deny_cross_project(request, inspection):
        return redirect("requests:inspection-list")
    if request.method == "POST":
        rsvc.technical_sign_off(inspection, request.user)
        messages.success(request, "Firma técnica registrada.")
    return redirect("requests:inspection-detail", pk=pk)


@login_required
def acceptance_list(request):
    acceptances = AcceptanceRecord.objects.select_related(
        "installation__project_receipt__delivery", "accepted_by"
    ).order_by("-accepted_at")
    acceptances = _scope_to_accessible_projects(
        request, acceptances, "installation__project_receipt__delivery__dispatch__pick_list__request__project_id"
    )
    return render(request, "requests/acceptance_list.html", {"acceptances": acceptances})
