from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.shortcuts import get_object_or_404, redirect, render

from apps.items.models import Item
from apps.projects.models import Project
from apps.workflow.models import GateDefinition, Handoff

from .models import MaterialRequest, MaterialRequestLine


class MaterialRequestForm(forms.ModelForm):
    item = forms.ModelChoiceField(queryset=Item.objects.all(), label="Artículo")
    quantity_requested = forms.DecimalField(max_digits=14, decimal_places=3, label="Cantidad")

    class Meta:
        model = MaterialRequest
        fields = ["project", "building", "unit", "priority", "needed_by_date", "purpose"]
        widgets = {"needed_by_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


@login_required
def request_list(request):
    requests_qs = MaterialRequest.objects.select_related("project", "building").order_by("-created_at")
    return render(request, "requests/list.html", {"requests": requests_qs})


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


@login_required
def request_detail(request, pk):
    mr = get_object_or_404(
        MaterialRequest.objects.select_related("project", "building", "unit").prefetch_related("lines__item"),
        pk=pk,
    )
    content_type = ContentType.objects.get_for_model(MaterialRequest)
    available_gates = GateDefinition.objects.filter(
        organization=getattr(request.user.profile, "organization", None),
        target_content_type=content_type,
        code="warehouse_to_project",
    )
    existing_handoffs = Handoff.objects.filter(
        content_type=content_type, object_id=mr.pk
    ).select_related("gate_definition", "to_department").order_by("-created_at")
    return render(
        request,
        "requests/detail.html",
        {
            "request_obj": mr,
            "request_content_type_id": content_type.id,
            "available_gates": available_gates,
            "existing_handoffs": existing_handoffs,
        },
    )
