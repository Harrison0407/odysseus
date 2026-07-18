from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.models import UserRole

from . import services
from .gates import evaluate_gate
from .models import GateDefinition, Handoff, HandoffStatus


class ReasonForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}), label="Motivo")


class CommentForm(forms.Form):
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 2, "class": "form-control"}), label="Comentario")


def _visible_handoffs(user):
    """Base queryset: organization-scoped, then narrowed to what this
    user is allowed to see (project isolation, see services.can_view_handoff).
    Kept as a queryset (not a Python filter) so inbox filtering stays a
    single efficient query."""
    profile = getattr(user, "profile", None)
    if profile is None:
        return Handoff.objects.none()

    qs = Handoff.objects.filter(organization=profile.organization).select_related(
        "gate_definition", "from_department", "to_department", "from_user", "to_user", "project"
    )

    is_management = UserRole.objects.filter(user=user, role__is_management=True, is_active=True).exists()
    if is_management:
        return qs

    accessible_project_ids = set(
        user.project_access.values_list("project_id", flat=True)
    )
    from django.db.models import Q
    return qs.filter(Q(project__isnull=True) | Q(project_id__in=accessible_project_ids))


@login_required
def inbox(request):
    """Role-aware, filterable handoff inbox — the main new screen for
    this milestone. Cards elsewhere in the app link here with query
    params pre-filled rather than showing decorative totals."""
    qs = _visible_handoffs(request.user)

    view = request.GET.get("vista", "para_mi")
    if view == "para_mi":
        awaiting_ids = services.handoffs_awaiting_user(request.user).values_list("id", flat=True)
        qs = qs.filter(id__in=list(awaiting_ids))
    elif view == "enviadas_por_mi":
        qs = qs.filter(from_user=request.user)
    elif view == "devueltas":
        qs = qs.filter(status=HandoffStatus.RETURNED_FOR_CORRECTION, from_user=request.user)
    elif view == "bloqueadas":
        qs = qs.filter(status=HandoffStatus.NOT_READY)
    elif view == "completadas":
        qs = qs.filter(status__in=[HandoffStatus.ACCEPTED, HandoffStatus.REJECTED]).order_by("-decided_at")
    elif view == "todas":
        pass

    project_id = request.GET.get("project")
    if project_id:
        qs = qs.filter(project_id=project_id)
    department_id = request.GET.get("department")
    if department_id:
        qs = qs.filter(to_department_id=department_id)
    gate_code = request.GET.get("gate")
    if gate_code:
        qs = qs.filter(gate_definition__code=gate_code)
    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)

    handoffs = list(qs.order_by("-created_at")[:200])

    overdue_only = request.GET.get("overdue") == "1"
    rows = []
    for h in handoffs:
        overdue = services.is_overdue(h)
        if overdue_only and not overdue:
            continue
        rows.append({"handoff": h, "overdue": overdue})

    return render(
        request,
        "workflow/inbox.html",
        {
            "rows": rows,
            "view": view,
            "gate_definitions": GateDefinition.objects.filter(
                organization=getattr(request.user.profile, "organization", None)
            ),
        },
    )


@login_required
def handoff_detail(request, pk):
    handoff = get_object_or_404(Handoff.objects.select_related(
        "gate_definition", "from_department", "to_department", "from_user", "to_user", "project"
    ), pk=pk)
    if not services.can_view_handoff(request.user, handoff):
        messages.error(request, "No tiene acceso a esta entrega.")
        return redirect("workflow:inbox")

    target = services.get_target(handoff)
    live_result = evaluate_gate(handoff.gate_definition, target) if handoff.gate_definition else None

    context = {
        "handoff": handoff,
        "target": target,
        "live_result": live_result,
        "can_accept": services.can_accept_handoff(request.user, handoff),
        "can_override": services.can_override_gates(request.user),
        "is_overdue": services.is_overdue(handoff),
        "comments": services.list_comments(handoff),
        "decisions": handoff.decisions.select_related("decided_by").order_by("decided_at"),
        "evidence": handoff.evidence.select_related("document"),
        "checklist_items": handoff.checklist_items.all(),
        "reason_form": ReasonForm(),
        "comment_form": CommentForm(),
        "superseded_chain": _superseded_chain(handoff),
    }
    return render(request, "workflow/detail.html", context)


def _superseded_chain(handoff):
    chain = []
    node = handoff.supersedes
    while node is not None:
        chain.append(node)
        node = node.supersedes
    return chain


@login_required
def handoff_create(request, content_type_id, object_id, gate_code):
    content_type = get_object_or_404(ContentType, pk=content_type_id)
    model_class = content_type.model_class()
    target = get_object_or_404(model_class, pk=object_id)
    gate_definition = get_object_or_404(
        GateDefinition, code=gate_code, organization=getattr(request.user.profile, "organization", None)
    )
    handoff = services.create_handoff(target, gate_definition, request.user)
    return redirect("workflow:detail", pk=handoff.pk)


@login_required
def handoff_submit(request, pk):
    handoff = get_object_or_404(Handoff, pk=pk)
    if request.method == "POST":
        try:
            services.submit_handoff(handoff, request.user)
            messages.success(request, "Entrega enviada correctamente.")
        except services.GateBlockedError as exc:
            messages.error(
                request,
                "No se pudo enviar: el gate está bloqueado. "
                + "; ".join(exc.result.unmet_requirements + exc.result.unresolved_discrepancies) or "Ver detalle.",
            )
        except (services.InvalidTransitionError, services.PermissionDeniedError) as exc:
            messages.error(request, str(exc))
    return redirect("workflow:detail", pk=pk)


@login_required
def handoff_override_submit(request, pk):
    handoff = get_object_or_404(Handoff, pk=pk)
    if request.method == "POST":
        form = ReasonForm(request.POST)
        if form.is_valid():
            try:
                services.submit_handoff(handoff, request.user, override_reason=form.cleaned_data["reason"])
                messages.warning(request, "Entrega enviada mediante anulación autorizada del gate.")
            except services.PermissionDeniedError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Debe indicar un motivo para anular el gate.")
    return redirect("workflow:detail", pk=pk)


@login_required
def handoff_accept(request, pk):
    handoff = get_object_or_404(Handoff, pk=pk)
    if request.method == "POST":
        try:
            services.accept_handoff(handoff, request.user)
            messages.success(request, "Entrega aceptada. La responsabilidad ha sido transferida.")
        except (services.InvalidTransitionError, services.PermissionDeniedError) as exc:
            messages.error(request, str(exc))
    return redirect("workflow:detail", pk=pk)


@login_required
def handoff_reject(request, pk):
    handoff = get_object_or_404(Handoff, pk=pk)
    if request.method == "POST":
        form = ReasonForm(request.POST)
        if form.is_valid():
            try:
                services.reject_handoff(handoff, request.user, form.cleaned_data["reason"])
                messages.success(request, "Entrega rechazada.")
            except (services.InvalidTransitionError, services.PermissionDeniedError) as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Debe indicar un motivo para rechazar.")
    return redirect("workflow:detail", pk=pk)


@login_required
def handoff_return(request, pk):
    handoff = get_object_or_404(Handoff, pk=pk)
    if request.method == "POST":
        form = ReasonForm(request.POST)
        if form.is_valid():
            try:
                services.return_for_correction(handoff, request.user, form.cleaned_data["reason"])
                messages.success(request, "Entrega devuelta para corrección.")
            except (services.InvalidTransitionError, services.PermissionDeniedError) as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Debe indicar un motivo para devolver la entrega.")
    return redirect("workflow:detail", pk=pk)


@login_required
def handoff_resubmit(request, pk):
    old_handoff = get_object_or_404(Handoff, pk=pk)
    if request.method == "POST":
        try:
            new_handoff = services.resubmit_handoff(old_handoff, request.user)
            messages.success(request, "Reenvío corregido creado.")
            return redirect("workflow:detail", pk=new_handoff.pk)
        except services.InvalidTransitionError as exc:
            messages.error(request, str(exc))
    return redirect("workflow:detail", pk=pk)


@login_required
def handoff_comment(request, pk):
    handoff = get_object_or_404(Handoff, pk=pk)
    if request.method == "POST":
        form = CommentForm(request.POST)
        if form.is_valid():
            services.add_comment(handoff, request.user, form.cleaned_data["body"])
            messages.success(request, "Comentario agregado.")
    return redirect("workflow:detail", pk=pk)
