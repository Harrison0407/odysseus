from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from . import services
from .models import Tool, ToolCheckout, ToolRepair

User = get_user_model()


class CheckoutForm(forms.Form):
    assigned_to = forms.ModelChoiceField(queryset=User.objects.none(), label="Asignar a")
    expected_return_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Fecha esperada de devolución")

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization is not None:
            self.fields["assigned_to"].queryset = User.objects.filter(profile__organization=organization, is_active=True)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class ReturnForm(forms.Form):
    damage_noted = forms.BooleanField(required=False, label="Se detectó daño")
    damage_description = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False, label="Descripción del daño")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class RepairForm(forms.Form):
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), label="Descripción de la reparación")
    cost = forms.DecimalField(max_digits=10, decimal_places=2, required=False, label="Costo")
    resulted_in_write_off = forms.BooleanField(required=False, label="Resultó en baja de la herramienta")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


@login_required
def tool_list(request):
    tools = Tool.objects.filter(organization=request.user.profile.organization).select_related("current_location")
    rows = [{"tool": tool, "checkout": services.current_checkout(tool)} for tool in tools]
    return render(request, "tools/list.html", {"rows": rows})


@login_required
def tool_detail(request, pk):
    tool = get_object_or_404(Tool, pk=pk, organization=request.user.profile.organization)
    checkout = services.current_checkout(tool)
    history = ToolCheckout.objects.filter(assignment__tool=tool).select_related(
        "assignment__assigned_to", "tool_return"
    ).order_by("-checkout_date")
    repairs = ToolRepair.objects.filter(tool=tool).order_by("-repaired_at")
    return render(request, "tools/detail.html", {
        "tool": tool,
        "current_checkout": checkout,
        "history": history,
        "repairs": repairs,
        "checkout_form": CheckoutForm(organization=request.user.profile.organization),
        "return_form": ReturnForm(),
        "repair_form": RepairForm(),
    })


@login_required
def tool_checkout(request, pk):
    tool = get_object_or_404(Tool, pk=pk, organization=request.user.profile.organization)
    if request.method == "POST":
        form = CheckoutForm(request.POST, organization=request.user.profile.organization)
        if form.is_valid():
            try:
                services.checkout_tool(
                    tool, form.cleaned_data["assigned_to"], request.user,
                    expected_return_date=form.cleaned_data["expected_return_date"],
                )
                messages.success(request, "Herramienta entregada.")
            except services.ToolCustodyError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los datos de la entrega.")
    return redirect("tools:detail", pk=pk)


@login_required
def tool_return(request, pk, checkout_pk):
    tool = get_object_or_404(Tool, pk=pk, organization=request.user.profile.organization)
    checkout = get_object_or_404(ToolCheckout, pk=checkout_pk, assignment__tool=tool)
    if request.method == "POST":
        form = ReturnForm(request.POST)
        if form.is_valid():
            try:
                services.return_tool(
                    checkout, request.user,
                    damage_noted=form.cleaned_data["damage_noted"],
                    damage_description=form.cleaned_data["damage_description"],
                )
                messages.success(request, "Devolución registrada.")
            except services.ToolCustodyError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Revise los datos de la devolución.")
    return redirect("tools:detail", pk=pk)


@login_required
def tool_repair(request, pk):
    tool = get_object_or_404(Tool, pk=pk, organization=request.user.profile.organization)
    if request.method == "POST":
        form = RepairForm(request.POST)
        if form.is_valid():
            services.record_repair(
                tool, request.user, description=form.cleaned_data["description"],
                cost=form.cleaned_data["cost"], resulted_in_write_off=form.cleaned_data["resulted_in_write_off"],
            )
            messages.success(request, "Reparación registrada.")
        else:
            messages.error(request, "Revise los datos de la reparación.")
    return redirect("tools:detail", pk=pk)
