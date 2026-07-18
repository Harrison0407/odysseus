from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from apps.items.models import Item
from apps.projects.models import Project

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
    return render(request, "requests/detail.html", {"request_obj": mr})
