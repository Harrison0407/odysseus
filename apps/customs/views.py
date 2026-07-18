from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from . import services
from .models import ConfoturLine, ConfoturList


class ConfirmDuplicateForm(forms.Form):
    duplicate_line = forms.ModelChoiceField(queryset=ConfoturLine.objects.none(), label="Marcar como duplicado de")

    def __init__(self, *args, candidates=None, **kwargs):
        super().__init__(*args, **kwargs)
        if candidates is not None:
            self.fields["duplicate_line"].queryset = ConfoturLine.objects.filter(pk__in=[l.pk for l in candidates])
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-select form-select-sm")


@login_required
def confotur_list_list(request):
    lists = ConfoturList.objects.filter(
        organization=request.user.profile.organization
    ).prefetch_related("lines").order_by("-submitted_date")
    return render(request, "customs/confotur_list.html", {"lists": lists})


@login_required
def confotur_list_detail(request, pk):
    confotur_list = get_object_or_404(
        ConfoturList.objects.prefetch_related("lines__quotation", "lines__manifest_line", "lines__is_duplicate_of"),
        pk=pk, organization=request.user.profile.organization,
    )
    return render(request, "customs/confotur_detail.html", {"confotur_list": confotur_list})


@login_required
def confotur_reconciliation(request):
    """The CONFOTUR duplicate-exemption reconciliation screen (spec
    section 25) — lists every group of still-unresolved ConfoturLine
    rows sharing the same quotation or manifest line, with a form to
    confirm one as a duplicate of another. Never auto-resolved."""
    organization = request.user.profile.organization
    candidates = services.detect_duplicate_candidates(organization)
    candidates_with_forms = [
        {
            "basis": c["basis"], "lines": c["lines"],
            # The dropdown excludes the first line (the one the form marks
            # as the duplicate) so a user can never select it as "duplicate
            # of itself" — the service layer also rejects that case
            # defensively either way.
            "form": ConfirmDuplicateForm(candidates=c["lines"][1:], prefix=str(i)),
        }
        for i, c in enumerate(candidates)
    ]
    return render(request, "customs/reconciliation.html", {"candidates": candidates_with_forms})


@login_required
def confotur_confirm_duplicate(request, pk):
    line = get_object_or_404(
        ConfoturLine, pk=pk, confotur_list__organization=request.user.profile.organization
    )
    if request.method == "POST":
        duplicate_of_id = request.POST.get("duplicate_line")
        duplicate_of = get_object_or_404(
            ConfoturLine, pk=duplicate_of_id, confotur_list__organization=request.user.profile.organization
        )
        try:
            services.confirm_duplicate(line, duplicate_of, request.user)
            messages.success(request, "Línea marcada como duplicado.")
        except ValueError as exc:
            messages.error(request, str(exc))
    return redirect("customs:reconciliation")
