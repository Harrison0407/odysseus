"""Administrative screens for Party/Role/Capability/privileged-audit
(spec section 20). Gated by the same senior-authorization permission
(`can_override_gates`) used for every other administrative action in
this system — never Django superuser status, never a bespoke new flag."""

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from apps.workflow.services import can_override_gates

from . import services
from .models import CapabilityGrant, Party, PartyMembership, RoleAssignment


def _require_governance_admin(request):
    if not can_override_gates(request.user):
        raise Http404


@login_required
def party_list(request):
    _require_governance_admin(request)
    organization = request.user.profile.organization
    parties = Party.objects.filter(hosting_organization=organization).order_by("display_name")
    return render(request, "governance/party_list.html", {"parties": parties})


@login_required
def party_detail(request, pk):
    _require_governance_admin(request)
    organization = request.user.profile.organization
    party = get_object_or_404(Party, pk=pk, hosting_organization=organization)
    role_assignments = party.role_assignments.select_related("package", "project").order_by("-created_at")
    memberships = party.memberships.select_related("user")
    return render(request, "governance/party_detail.html", {"party": party, "role_assignments": role_assignments, "memberships": memberships})


class PartyForm(forms.Form):
    display_name = forms.CharField(max_length=255)
    party_type = forms.ChoiceField(choices=Party.PartyType.choices)
    is_hidden_by_default = forms.BooleanField(required=False)
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)


@login_required
def party_create(request):
    _require_governance_admin(request)
    organization = request.user.profile.organization
    if request.method == "POST":
        form = PartyForm(request.POST)
        if form.is_valid():
            party = Party.objects.create(hosting_organization=organization, created_by=request.user, **form.cleaned_data)
            messages.success(request, "Parte creada.")
            return redirect("governance:party-detail", pk=party.pk)
        messages.error(request, "Revise los datos de la parte.")
    else:
        form = PartyForm()
    return render(request, "governance/party_form.html", {"form": form})


@login_required
def privileged_audit(request):
    """Read-only access explanation screen (spec section 20) — never
    exposed to unauthorized users."""
    _require_governance_admin(request)
    from apps.audit.models import AuditEvent

    events = AuditEvent.objects.filter(
        action__in=[
            AuditEvent.Action.PRIVILEGED_ACCESS_GRANTED, AuditEvent.Action.PRIVILEGED_ACCESS_DENIED,
            AuditEvent.Action.ROLE_ASSIGNMENT, AuditEvent.Action.CAPABILITY_GRANT,
            AuditEvent.Action.DISCLOSURE_GRANT, AuditEvent.Action.DISCLOSURE_REVOKED,
            AuditEvent.Action.VISIBILITY_MODE_CHANGE, AuditEvent.Action.CHANGE_REQUEST,
            AuditEvent.Action.PACKAGE_FREEZE, AuditEvent.Action.VERIFICATION_ASSERTION,
            AuditEvent.Action.EVIDENCE_VERIFICATION, AuditEvent.Action.RISK_FLAG, AuditEvent.Action.DERIVED_ARTIFACT,
        ],
    ).select_related("actor").order_by("-occurred_at")[:200]
    return render(request, "governance/privileged_audit.html", {"events": events})
