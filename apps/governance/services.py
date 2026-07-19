"""Central authorization, projection, and party/role/capability services
for the Controlled Transparency / Controlled Confidentiality foundation.

Deliberately the smallest reusable policy layer compatible with the
existing architecture — not a speculative general-purpose policy
language. Every check here is deny-by-default: an unknown relationship,
an expired grant, or a missing capability all resolve to False/denied,
never to an implicit allow.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditEvent

from .models import (
    ALL_CAPABILITY_CODES,
    CLASSIFICATION_RANK,
    CapabilityGrant,
    ChangeRequest,
    Classification,
    DisclosureGrant,
    Party,
    PartyMembership,
    RiskFlag,
    RoleAssignment,
    ROLE_DEFAULT_CAPABILITIES,
    is_less_restrictive,
)

# Which capability is required to approve a Change Request against a
# given frozen field — never a single blanket "can change anything"
# permission.
CHANGE_REQUEST_APPROVAL_CAPABILITY = {
    "visibility_mode": "APPROVE_VISIBILITY_CHANGE",
    "seller_of_record": "APPROVE_ROLE_CHANGE",
    "exporter_of_record": "APPROVE_ROLE_CHANGE",
    "china_procurement_operator": "APPROVE_ROLE_CHANGE",
    "production_factory": "APPROVE_ROLE_CHANGE",
    "production_site": "APPROVE_ROLE_CHANGE",
}


class AuthorizationDenied(Exception):
    """Raised by any governance action a user is not authorized for."""


# ---------------------------------------------------------------------------
# Party / Role / Capability resolution
# ---------------------------------------------------------------------------


def resolve_parties_for_user(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return Party.objects.none()
    return Party.objects.filter(memberships__user=user, memberships__is_active=True, is_active=True)


def active_role_assignments(user, *, package=None, project=None, organization_context=None):
    parties = resolve_parties_for_user(user)
    if not parties.exists():
        return RoleAssignment.objects.none()
    qs = RoleAssignment.objects.filter(party__in=parties)
    if package is not None:
        qs = qs.filter(package=package)
    if project is not None:
        qs = qs.filter(project=project)
    if organization_context is not None:
        qs = qs.filter(organization_context=organization_context)
    today = timezone.now().date()
    qs = qs.filter(status=RoleAssignment.Status.ACTIVE, effective_from__lte=today)
    qs = qs.exclude(effective_until__lt=today)
    return qs


def has_role(user, role_code, *, package=None, project=None) -> bool:
    return active_role_assignments(user, package=package, project=project).filter(role_code=role_code).exists()


def has_capability(user, capability_code, *, package=None) -> bool:
    """Deny by default. A capability is granted only through:
    (1) an explicit, currently-active CapabilityGrant tied directly to
    the user, or to one of their active role assignments in `package`;
    or (2) a role-implied default from ROLE_DEFAULT_CAPABILITIES — which
    deliberately never includes any APPROVE_*/AUTHORIZE_*/EXPORT_*/
    VIEW_PRIVILEGED_AUDIT-type action."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False

    today = timezone.now().date()

    direct_grants = CapabilityGrant.objects.filter(user=user, capability_code=capability_code, is_active=True)
    if package is not None:
        direct_grants = direct_grants.filter(package=package)
    for grant in direct_grants:
        if grant.is_currently_active(on_date=today):
            return True

    assignments = active_role_assignments(user, package=package)
    for assignment in assignments:
        if capability_code in ROLE_DEFAULT_CAPABILITIES.get(assignment.role_code, set()):
            return True
        role_grants = CapabilityGrant.objects.filter(
            role_assignment=assignment, capability_code=capability_code, is_active=True,
        )
        for grant in role_grants:
            if grant.is_currently_active(on_date=today):
                return True
    return False


@transaction.atomic
def create_role_assignment(party, role_code, organization_context, *, user=None, package=None, project=None,
                            shipment=None, effective_from=None, effective_until=None, client_visible=False) -> RoleAssignment:
    assignment = RoleAssignment.objects.create(
        party=party, role_code=role_code, organization_context=organization_context, package=package,
        project=project, shipment=shipment, effective_from=effective_from or timezone.now().date(),
        effective_until=effective_until, client_visible=client_visible, created_by=user,
    )
    audit.log(AuditEvent.Action.ROLE_ASSIGNMENT, instance=assignment, actor=user, summary=f"Rol asignado: {assignment}")
    return assignment


@transaction.atomic
def supersede_role_assignment(old_assignment: RoleAssignment, user, **new_fields) -> RoleAssignment:
    old_assignment.status = RoleAssignment.Status.ENDED
    old_assignment.effective_until = new_fields.get("effective_from", timezone.now().date())
    old_assignment.save()
    new_assignment = RoleAssignment.objects.create(
        party=old_assignment.party, role_code=new_fields.get("role_code", old_assignment.role_code),
        organization_context=old_assignment.organization_context, package=old_assignment.package,
        project=old_assignment.project, shipment=old_assignment.shipment,
        effective_from=new_fields.get("effective_from", timezone.now().date()),
        effective_until=new_fields.get("effective_until"),
        client_visible=new_fields.get("client_visible", old_assignment.client_visible),
        version=old_assignment.version + 1, supersedes=old_assignment, created_by=user,
    )
    audit.log(AuditEvent.Action.ROLE_ASSIGNMENT, instance=new_assignment, actor=user, summary=f"Rol reasignado: {new_assignment}")
    return new_assignment


@transaction.atomic
def grant_capability(capability_code, *, user=None, granted_to_user=None, role_assignment=None, package=None,
                      organization=None, effective_from=None, effective_until=None) -> CapabilityGrant:
    if capability_code not in ALL_CAPABILITY_CODES:
        raise AuthorizationDenied(f"Código de capacidad desconocido: {capability_code}")
    grant = CapabilityGrant.objects.create(
        capability_code=capability_code, user=granted_to_user, role_assignment=role_assignment, package=package,
        organization=organization, granted_by=user, effective_from=effective_from, effective_until=effective_until,
        created_by=user,
    )
    audit.log(AuditEvent.Action.CAPABILITY_GRANT, instance=grant, actor=user, summary=f"Capacidad otorgada: {grant}")
    return grant


# ---------------------------------------------------------------------------
# Classification-based authorization
# ---------------------------------------------------------------------------

CLASSIFICATION_REQUIRED_CAPABILITY = {
    Classification.CHINA_INTERNAL: "VIEW_INTERNAL_COST_COMPONENTS",
    Classification.SOURCE_PRIVATE: "VIEW_FACTORY_QUOTE",
    Classification.TRADING_COMPANY_CONFIDENTIAL: "VIEW_MARKUP",
    Classification.RESTRICTED_FINANCE: "VIEW_RESTRICTED_FINANCE",
    Classification.SUPPLIER_SHARED: None,
    Classification.CLIENT_PROJECT: None,
    Classification.CLIENT_SHARED: None,
    Classification.OPERATIONAL_SHARED: None,
    Classification.LEGAL_REQUIRED: None,
}


def can_view_classification(user, classification, *, package=None) -> bool:
    """Deny by default. `OPERATIONAL_SHARED`/`CLIENT_SHARED`/
    `CLIENT_PROJECT`/`SUPPLIER_SHARED`/`LEGAL_REQUIRED` require only a
    legitimate relationship to the package (any active role assignment)
    when a package is given — no package at all (a plain legacy,
    non-package resource) preserves the system's existing org-scoped
    behavior. Every other classification requires its specific
    capability, matching or exceeding the source classification's
    sensitivity."""
    required_capability = CLASSIFICATION_REQUIRED_CAPABILITY.get(classification)
    if required_capability is None:
        if package is None:
            return True
        return active_role_assignments(user, package=package).exists()
    return has_capability(user, required_capability, package=package)


def visible_classifications_for(user, package=None) -> set:
    return {c for c in Classification.values if can_view_classification(user, c, package=package)}


def filter_authorized_queryset(user, queryset, *, package=None, classification_field="classification"):
    allowed = visible_classifications_for(user, package=package)
    return queryset.filter(**{f"{classification_field}__in": allowed})


def project_authorized_fields(user, obj, field_capability_map: dict, *, package=None) -> dict:
    """Returns only the fields of `obj` the user's capabilities allow,
    per an explicit {field_name: capability_code_or_None} map. A field
    mapped to None is always included (non-sensitive)."""
    result = {}
    for field_name, capability_code in field_capability_map.items():
        if capability_code is None or has_capability(user, capability_code, package=package):
            result[field_name] = getattr(obj, field_name, None)
    return result


def authorize_file_access(user, document) -> bool:
    """A private document must never reach an unauthorized browser.
    Deny by default: an unclassified/legacy document (OPERATIONAL_SHARED)
    keeps the system's existing organization-scoped behavior; anything
    more sensitive requires the matching capability."""
    classification = getattr(document, "classification", Classification.OPERATIONAL_SHARED)
    package = getattr(document, "package", None)
    return can_view_classification(user, classification, package=package)


def authorize_export(user, resource, *, package=None) -> bool:
    if not has_capability(user, "EXPORT_COMMERCIAL_DATA", package=package):
        return False
    classification = getattr(resource, "classification", Classification.OPERATIONAL_SHARED)
    return can_view_classification(user, classification, package=package)


def authorize_derived_artifact_source(user, source_obj, field_capability_map: dict, *, package=None):
    """Authorization occurs BEFORE any transformation: returns
    (allowed, authorized_projection) — a transformation service (mock
    translation/AI) must only ever receive `authorized_projection`,
    never the full source object."""
    classification = getattr(source_obj, "classification", Classification.OPERATIONAL_SHARED)
    if not can_view_classification(user, classification, package=package):
        return False, {}
    return True, project_authorized_fields(user, source_obj, field_capability_map, package=package)


def explain_privileged_decision(admin_user, *, target_user, action_code, resource=None, package=None, allowed: bool,
                                 matched_role=None, matched_capability=None) -> dict:
    """Read-only access explanation for privileged administrators (spec
    section 20/3). Never exposed to unauthorized users."""
    explanation = {
        "requested_user": str(target_user),
        "requested_action": action_code,
        "resource": str(resource) if resource is not None else None,
        "package": str(package) if package is not None else None,
        "decision": "allow" if allowed else "deny",
        "matched_role": matched_role,
        "matched_capability": matched_capability,
        "classification": getattr(resource, "classification", None),
        "explained_at": timezone.now().isoformat(),
    }
    audit.log(
        AuditEvent.Action.PRIVILEGED_ACCESS_GRANTED if allowed else AuditEvent.Action.PRIVILEGED_ACCESS_DENIED,
        instance=resource if resource is not None else package, actor=target_user,
        summary=f"Decisión de acceso privilegiado: {action_code} -> {'permitido' if allowed else 'denegado'}",
        **explanation,
    )
    return explanation


# ---------------------------------------------------------------------------
# Disclosure grants
# ---------------------------------------------------------------------------


@transaction.atomic
def create_disclosure_grant(source_party, package, recipient_organization, field_scope: list, reason, user, *,
                             recipient_party=None, classification_before=Classification.SOURCE_PRIVATE,
                             expires_at=None, permitted_projection=None) -> DisclosureGrant:
    if not has_capability(user, "AUTHORIZE_DISCLOSURE", package=package):
        raise AuthorizationDenied("No tiene permiso para autorizar una divulgación.")
    if not field_scope:
        raise AuthorizationDenied("Debe especificarse al menos un campo a divulgar.")
    grant = DisclosureGrant.objects.create(
        source_party=source_party, package=package, recipient_organization=recipient_organization,
        recipient_party=recipient_party, field_scope=field_scope, classification_before=classification_before,
        permitted_projection=permitted_projection or {}, reason=reason, requested_by=user, approved_by=user,
        effective_from=timezone.now(), expires_at=expires_at, created_by=user,
    )
    audit.log(AuditEvent.Action.DISCLOSURE_GRANT, instance=grant, actor=user, summary=f"Divulgación autorizada: {grant}", reason=reason)
    return grant


@transaction.atomic
def revoke_disclosure_grant(grant: DisclosureGrant, user) -> DisclosureGrant:
    if grant.revoked_at is not None:
        raise AuthorizationDenied("Esta divulgación ya fue revocada.")
    grant.revoked_at = timezone.now()
    grant.revoked_by = user
    grant.save()
    audit.log(AuditEvent.Action.DISCLOSURE_REVOKED, instance=grant, actor=user, summary=f"Divulgación revocada: {grant}")
    return grant


def disclosed_fields(package, recipient_organization) -> set:
    """Union of field codes currently disclosed to `recipient_organization`
    for `package` — expired/revoked grants never contribute."""
    fields = set()
    for grant in DisclosureGrant.objects.filter(package=package, recipient_organization=recipient_organization):
        if grant.is_currently_active():
            fields.update(grant.field_scope)
    return fields


# ---------------------------------------------------------------------------
# Change Requests — a critical, frozen package term may only ever change
# through one of these, never a direct edit (spec section 16).
# ---------------------------------------------------------------------------


@transaction.atomic
def request_change(package, field_name, proposed_new_value, reason, user, *, affected_relationships="", risk_impact="") -> ChangeRequest:
    if not package.is_frozen:
        raise AuthorizationDenied("Este paquete aún no está congelado — no se requiere una solicitud de cambio.")
    frozen_current_value = str(package.frozen_snapshot.get(field_name, package.frozen_snapshot.get("roles", {}).get(field_name)))
    change_request = ChangeRequest.objects.create(
        package=package, field_name=field_name, frozen_current_value=frozen_current_value,
        proposed_new_value=proposed_new_value, reason=reason, affected_relationships=affected_relationships,
        risk_impact=risk_impact, requested_by=user, created_by=user,
    )
    package.is_on_hold = True
    package.save(update_fields=["is_on_hold"])
    audit.log(AuditEvent.Action.CHANGE_REQUEST, instance=change_request, actor=user, summary=f"Solicitud de cambio creada: {change_request}", reason=reason)
    return change_request


@transaction.atomic
def approve_change_request(change_request: ChangeRequest, user, *, comment="") -> ChangeRequest:
    if change_request.status != ChangeRequest.Status.PENDING:
        raise AuthorizationDenied("Esta solicitud de cambio ya fue resuelta.")
    required_capability = CHANGE_REQUEST_APPROVAL_CAPABILITY.get(change_request.field_name, "APPROVE_ROLE_CHANGE")
    if not has_capability(user, required_capability, package=change_request.package):
        raise AuthorizationDenied("No tiene permiso para aprobar esta solicitud de cambio.")

    change_request.status = ChangeRequest.Status.APPROVED
    change_request.decided_by = user
    change_request.decided_at = timezone.now()
    change_request.decision_comment = comment
    change_request.save()

    package = change_request.package
    if change_request.field_name == "visibility_mode":
        package.visibility_mode = change_request.proposed_new_value
        package.visibility_mode_version += 1
        audit.log(AuditEvent.Action.VISIBILITY_MODE_CHANGE, instance=package, actor=user, summary=f"Modo de visibilidad cambiado: {package}")
    else:
        package.frozen_snapshot.setdefault("roles", {})[change_request.field_name] = change_request.proposed_new_value
    package.is_on_hold = False
    package.save()

    audit.log(AuditEvent.Action.CHANGE_REQUEST, instance=change_request, actor=user, summary=f"Solicitud de cambio aprobada: {change_request}")
    return change_request


@transaction.atomic
def reject_change_request(change_request: ChangeRequest, user, *, comment="") -> ChangeRequest:
    if change_request.status != ChangeRequest.Status.PENDING:
        raise AuthorizationDenied("Esta solicitud de cambio ya fue resuelta.")
    change_request.status = ChangeRequest.Status.REJECTED
    change_request.decided_by = user
    change_request.decided_at = timezone.now()
    change_request.decision_comment = comment
    change_request.save()
    package = change_request.package
    package.is_on_hold = False
    package.save(update_fields=["is_on_hold"])
    audit.log(AuditEvent.Action.CHANGE_REQUEST, instance=change_request, actor=user, summary=f"Solicitud de cambio rechazada: {change_request}")
    return change_request


# ---------------------------------------------------------------------------
# Risk flags — foundation only (spec section 17). Records risk and may
# trigger review/hold; never declares legality or approves an opaque
# transaction.
# ---------------------------------------------------------------------------


@transaction.atomic
def raise_risk_flag(package, level, indicator_codes, notes="", user=None) -> RiskFlag:
    flag = RiskFlag.objects.create(package=package, level=level, indicator_codes=indicator_codes, notes=notes, raised_by=user, created_by=user)
    audit.log(AuditEvent.Action.RISK_FLAG, instance=flag, actor=user, summary=f"Señal de riesgo: {flag}")
    return flag


@transaction.atomic
def resolve_risk_flag(flag: RiskFlag, user) -> RiskFlag:
    flag.resolved_at = timezone.now()
    flag.resolved_by = user
    flag.save()
    audit.log(AuditEvent.Action.RISK_FLAG, instance=flag, actor=user, summary=f"Señal de riesgo resuelta: {flag}")
    return flag
