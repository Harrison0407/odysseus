"""Central authorization, projection, and party/role/capability services
for the Controlled Transparency / Controlled Confidentiality foundation.

Deliberately the smallest reusable policy layer compatible with the
existing architecture — not a speculative general-purpose policy
language. Every check here is deny-by-default: an unknown relationship,
an expired grant, or a missing capability all resolve to False/denied,
never to an implicit allow.
"""

from __future__ import annotations

import hashlib
import json

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditEvent, EvidenceBundle, EvidenceItem

from .models import (
    ALL_CAPABILITY_CODES,
    CLASSIFICATION_RANK,
    CapabilityGrant,
    ChangeRequest,
    Classification,
    DerivedArtifact,
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


def user_can_access_package(user, package) -> bool:
    """Package discovery requires package participation or explicit authority.

    Tenant membership alone is intentionally insufficient. Existing executive
    oversight remains available only through the already-established
    ``can_override_gates`` authority and only inside that executive's tenant.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if active_role_assignments(user, package=package).exists():
        return True

    today = timezone.now().date()
    direct_grants = CapabilityGrant.objects.filter(
        user=user, package=package, is_active=True,
    ).filter(Q(effective_from__isnull=True) | Q(effective_from__lte=today)).filter(
        Q(effective_until__isnull=True) | Q(effective_until__gte=today)
    )
    if direct_grants.exists():
        return True

    profile_org_id = getattr(getattr(user, "profile", None), "organization_id", None)
    if profile_org_id == package.organization_id:
        from apps.workflow.services import can_override_gates

        return can_override_gates(user)
    return False


def authorized_package_ids(user):
    """Return package ids discoverable by ``user`` under the same policy."""
    from apps.procurement.models import ProcurementPackage

    if user is None or not getattr(user, "is_authenticated", False):
        return set()
    ids = set(active_role_assignments(user).values_list("package_id", flat=True))
    today = timezone.now().date()
    ids.update(
        CapabilityGrant.objects.filter(user=user, package__isnull=False, is_active=True)
        .filter(Q(effective_from__isnull=True) | Q(effective_from__lte=today))
        .filter(Q(effective_until__isnull=True) | Q(effective_until__gte=today))
        .values_list("package_id", flat=True)
    )
    from apps.workflow.services import can_override_gates

    if can_override_gates(user):
        profile_org_id = getattr(getattr(user, "profile", None), "organization_id", None)
        ids.update(ProcurementPackage.objects.filter(organization_id=profile_org_id).values_list("id", flat=True))
    ids.discard(None)
    return ids


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


def log_denied_attempt(user, action_code, *, package=None, resource=None, required_capability=None):
    """Every denied privileged attempt must appear in the restricted
    audit trail (spec section 20/22) — not only successful actions."""
    audit.log(
        AuditEvent.Action.PRIVILEGED_ACCESS_DENIED, instance=resource if resource is not None else package,
        actor=user, summary=f"Acceso privilegiado denegado: {action_code}",
        required_capability=required_capability, package=str(package) if package is not None else None,
    )


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
# Privileged audit — scope-before-retrieval (CTCF-AUDIT-017).
#
# Access requires an explicit, currently-active VIEW_PRIVILEGED_AUDIT
# CapabilityGrant (organization-scoped or package-scoped) — never
# `can_override_gates` alone and never Django superuser status alone. The
# queryset is built from the caller's authorized scopes BEFORE any
# AuditEvent is treated as visible; an event whose target cannot be
# resolved through an existing persisted relationship is excluded, never
# included (fail closed). This reuses the existing CapabilityGrant/
# RoleAssignment/package/organization relationships already in this
# codebase — no second audit store, no second authorization system.
# ---------------------------------------------------------------------------

PRIVILEGED_AUDIT_ACTIONS = [
    AuditEvent.Action.PRIVILEGED_ACCESS_GRANTED, AuditEvent.Action.PRIVILEGED_ACCESS_DENIED,
    AuditEvent.Action.ROLE_ASSIGNMENT, AuditEvent.Action.CAPABILITY_GRANT,
    AuditEvent.Action.DISCLOSURE_GRANT, AuditEvent.Action.DISCLOSURE_REVOKED,
    AuditEvent.Action.VISIBILITY_MODE_CHANGE, AuditEvent.Action.CHANGE_REQUEST,
    AuditEvent.Action.PACKAGE_FREEZE, AuditEvent.Action.VERIFICATION_ASSERTION,
    AuditEvent.Action.EVIDENCE_VERIFICATION, AuditEvent.Action.RISK_FLAG, AuditEvent.Action.DERIVED_ARTIFACT,
]

# Generic, non-identifying per-action descriptions used for the privileged
# audit projection. Deliberately never derived from a target's own
# `__str__` (which can embed factory/Party names, package identity, or
# Disclosure Grant field scopes) and never copies `AuditEvent.metadata`
# (which can carry free-text reasons) into the browser.
PRIVILEGED_AUDIT_ACTION_DESCRIPTIONS = {
    AuditEvent.Action.PRIVILEGED_ACCESS_GRANTED: "Acceso privilegiado concedido.",
    AuditEvent.Action.PRIVILEGED_ACCESS_DENIED: "Acceso privilegiado denegado.",
    AuditEvent.Action.ROLE_ASSIGNMENT: "Asignación de rol registrada.",
    AuditEvent.Action.CAPABILITY_GRANT: "Capacidad otorgada.",
    AuditEvent.Action.DISCLOSURE_GRANT: "Divulgación autorizada.",
    AuditEvent.Action.DISCLOSURE_REVOKED: "Divulgación revocada.",
    AuditEvent.Action.VISIBILITY_MODE_CHANGE: "Modo de visibilidad cambiado.",
    AuditEvent.Action.CHANGE_REQUEST: "Solicitud de cambio registrada.",
    AuditEvent.Action.PACKAGE_FREEZE: "Paquete congelado.",
    AuditEvent.Action.VERIFICATION_ASSERTION: "Aserción de verificación registrada.",
    AuditEvent.Action.EVIDENCE_VERIFICATION: "Verificación de evidencia registrada.",
    AuditEvent.Action.RISK_FLAG: "Señal de riesgo registrada.",
    AuditEvent.Action.DERIVED_ARTIFACT: "Artefacto derivado registrado.",
}


def _resolve_scope_for_target(target):
    """Best-effort, deny-by-default resolution of (organization_id,
    package_id) for an arbitrary governance/audit target, reusing only
    existing persisted relationships. Returns (None, None) when the
    target's scope cannot be safely resolved — callers must treat that as
    unresolved and exclude the event, never include it."""
    if target is None:
        return None, None

    if isinstance(target, Party):
        return target.hosting_organization_id, None

    if isinstance(target, RoleAssignment):
        if target.package_id:
            return target.package.organization_id, target.package_id
        return target.organization_context_id, None

    if isinstance(target, EvidenceBundle):
        # Reuses the same target resolution evidence authorization itself
        # uses (apps.audit.services.evidence_bundle_package) — never a
        # second, parallel evidence-scope resolver.
        package = audit.evidence_bundle_package(target)
        if package is not None:
            return package.organization_id, package.id
        return None, None

    if isinstance(target, EvidenceItem):
        return _resolve_scope_for_target(target.bundle)

    package = getattr(target, "package", None)
    if package is not None:
        return package.organization_id, package.id

    organization_id = getattr(target, "organization_id", None)
    if organization_id is not None:
        return organization_id, None

    return None, None


def _resolve_audit_event_scope(event):
    if event.content_type_id is None or event.object_id is None:
        return None, None
    try:
        target = event.content_type.get_object_for_this_type(pk=event.object_id)
    except ObjectDoesNotExist:
        return None, None
    return _resolve_scope_for_target(target)


def authorized_privileged_audit_scopes(user):
    """Organizations/packages the user holds an active, currently-effective
    VIEW_PRIVILEGED_AUDIT CapabilityGrant for — direct or via an active
    role assignment. `can_override_gates` and Django superuser status alone
    never satisfy this; only an explicit grant does. A package-scoped
    grant only ever adds to `package_ids` (it can never widen to the whole
    organization); an organization-scoped grant only ever adds to
    `org_ids` (it can never widen to every organization)."""
    org_ids, package_ids = set(), set()
    if user is None or not getattr(user, "is_authenticated", False):
        return org_ids, package_ids
    today = timezone.now().date()

    direct_grants = CapabilityGrant.objects.filter(
        user=user, capability_code="VIEW_PRIVILEGED_AUDIT", is_active=True,
    )
    for grant in direct_grants:
        if not grant.is_currently_active(on_date=today):
            continue
        if grant.package_id:
            package_ids.add(grant.package_id)
        elif grant.organization_id:
            org_ids.add(grant.organization_id)

    for assignment in active_role_assignments(user):
        role_grants = CapabilityGrant.objects.filter(
            role_assignment=assignment, capability_code="VIEW_PRIVILEGED_AUDIT", is_active=True,
        )
        for grant in role_grants:
            if not grant.is_currently_active(on_date=today):
                continue
            if assignment.package_id:
                package_ids.add(assignment.package_id)
            elif assignment.organization_context_id:
                org_ids.add(assignment.organization_context_id)
    return org_ids, package_ids


def privileged_audit_queryset(user, *, org_ids=None, package_ids=None, limit=200, scan_limit=1000):
    """Deny-by-default: only AuditEvents whose persisted target resolves,
    through existing relationships, into an organization or package the
    caller holds an active VIEW_PRIVILEGED_AUDIT grant for. An event whose
    target cannot be resolved is excluded, never included. `scan_limit`
    bounds how many recent candidate events are inspected before the
    result is sliced to `limit` authorized rows — still the single
    existing AuditEvent store, never a second one."""
    if org_ids is None or package_ids is None:
        org_ids, package_ids = authorized_privileged_audit_scopes(user)
    if not org_ids and not package_ids:
        return AuditEvent.objects.none()

    candidates = (
        AuditEvent.objects.filter(action__in=PRIVILEGED_AUDIT_ACTIONS)
        .select_related("actor", "content_type")
        .order_by("-occurred_at")[:scan_limit]
    )
    allowed_ids = []
    for event in candidates:
        event_org_id, event_package_id = _resolve_audit_event_scope(event)
        if event_package_id is not None:
            if event_package_id in package_ids or event_org_id in org_ids:
                allowed_ids.append(event.id)
        elif event_org_id is not None and event_org_id in org_ids:
            allowed_ids.append(event.id)
        if len(allowed_ids) >= limit:
            break
    return AuditEvent.objects.filter(id__in=allowed_ids).select_related("actor").order_by("-occurred_at")


def privileged_audit_projection(event) -> dict:
    """Server-side-safe representation of a privileged AuditEvent for
    display: action type, actor, and timestamp only. Raw `summary`/
    `metadata` — which may embed confidential target identifiers via a
    model's own `__str__` (factory/Party names, package identity,
    Disclosure Grant field scopes, free-text reasons) — are never copied
    into this projection or the browser."""
    return {
        "occurred_at": event.occurred_at,
        "action_display": event.get_action_display(),
        "actor": str(event.actor) if event.actor_id else "—",
        "description": PRIVILEGED_AUDIT_ACTION_DESCRIPTIONS.get(event.action, "Evento de gobernanza registrado."),
    }


# ---------------------------------------------------------------------------
# Disclosure grants
# ---------------------------------------------------------------------------


def create_disclosure_grant(source_party, package, recipient_organization, field_scope: list, reason, user, *,
                             recipient_party=None, classification_before=Classification.SOURCE_PRIVATE,
                             expires_at=None, permitted_projection=None) -> DisclosureGrant:
    if not has_capability(user, "AUTHORIZE_DISCLOSURE", package=package):
        log_denied_attempt(user, "AUTHORIZE_DISCLOSURE", package=package, required_capability="AUTHORIZE_DISCLOSURE")
        raise AuthorizationDenied("No tiene permiso para autorizar una divulgación.")
    if not field_scope:
        raise AuthorizationDenied("Debe especificarse al menos un campo a divulgar.")
    safe_source_fields = {
        "manufacturer_name": source_party.display_name,
        "factory_address": getattr(getattr(source_party, "supplier", None), "address", ""),
    }
    projection_source = safe_source_fields if permitted_projection is None else permitted_projection
    frozen_projection = {
        field: projection_source.get(field)
        for field in field_scope
        if field in projection_source
    }
    with transaction.atomic():
        grant = DisclosureGrant.objects.create(
            source_party=source_party, package=package, recipient_organization=recipient_organization,
            recipient_party=recipient_party, field_scope=field_scope, classification_before=classification_before,
            permitted_projection=frozen_projection, reason=reason, requested_by=user, approved_by=user,
            effective_from=timezone.now(), expires_at=expires_at, created_by=user,
        )
        audit.log(AuditEvent.Action.DISCLOSURE_GRANT, instance=grant, actor=user, summary=f"Divulgación autorizada: {grant}", reason=reason)
    return grant


def revoke_disclosure_grant(grant: DisclosureGrant, user) -> DisclosureGrant:
    if not has_capability(user, "AUTHORIZE_DISCLOSURE", package=grant.package):
        log_denied_attempt(
            user, "REVOKE_DISCLOSURE", package=grant.package, resource=grant,
            required_capability="AUTHORIZE_DISCLOSURE",
        )
        raise AuthorizationDenied("No tiene permiso para revocar esta divulgación.")
    if grant.revoked_at is not None:
        raise AuthorizationDenied("Esta divulgación ya fue revocada.")
    with transaction.atomic():
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


def disclosure_projection_for_user(package, user) -> dict:
    """Return the live, field-scoped projection released to this user.

    The persisted projection is a frozen, client-safe value set. Revoked,
    expired, future, wrong-organization, and wrong-recipient-party grants
    contribute nothing.
    """
    organization_id = getattr(getattr(user, "profile", None), "organization_id", None)
    if organization_id is None:
        return {}
    recipient_party_ids = set(resolve_parties_for_user(user).values_list("id", flat=True))
    projection = {}
    grants = DisclosureGrant.objects.filter(package=package, recipient_organization_id=organization_id)
    for grant in grants:
        if not grant.is_currently_active():
            continue
        if grant.recipient_party_id and grant.recipient_party_id not in recipient_party_ids:
            continue
        for field in grant.field_scope:
            if field in grant.permitted_projection:
                projection[field] = grant.permitted_projection[field]
    return projection


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


def approve_change_request(change_request: ChangeRequest, user, *, comment="") -> ChangeRequest:
    if change_request.status != ChangeRequest.Status.PENDING:
        raise AuthorizationDenied("Esta solicitud de cambio ya fue resuelta.")
    required_capability = CHANGE_REQUEST_APPROVAL_CAPABILITY.get(change_request.field_name, "APPROVE_ROLE_CHANGE")
    if not has_capability(user, required_capability, package=change_request.package):
        log_denied_attempt(
            user, required_capability, package=change_request.package, resource=change_request,
            required_capability=required_capability,
        )
        raise AuthorizationDenied("No tiene permiso para aprobar esta solicitud de cambio.")

    with transaction.atomic():
        locked_change = ChangeRequest.objects.select_for_update().get(pk=change_request.pk)
        if locked_change.status != ChangeRequest.Status.PENDING:
            raise AuthorizationDenied("Esta solicitud de cambio ya fue resuelta.")
        locked_change.status = ChangeRequest.Status.APPROVED
        locked_change.decided_by = user
        locked_change.decided_at = timezone.now()
        locked_change.decision_comment = comment
        locked_change.save()

        from apps.procurement.models import ProcurementPackage

        package = ProcurementPackage.objects.select_for_update().get(pk=locked_change.package_id)
        if locked_change.field_name == "visibility_mode":
            package.visibility_mode = locked_change.proposed_new_value
            package.visibility_mode_version += 1
            audit.log(AuditEvent.Action.VISIBILITY_MODE_CHANGE, instance=package, actor=user, summary=f"Modo de visibilidad cambiado: {package}")
        else:
            package.frozen_snapshot.setdefault("roles", {})[locked_change.field_name] = locked_change.proposed_new_value
        package.is_on_hold = _package_has_unresolved_holds(package)
        package.save()

        audit.log(AuditEvent.Action.CHANGE_REQUEST, instance=locked_change, actor=user, summary=f"Solicitud de cambio aprobada: {locked_change}")
    return locked_change


def reject_change_request(change_request: ChangeRequest, user, *, comment="") -> ChangeRequest:
    if change_request.status != ChangeRequest.Status.PENDING:
        raise AuthorizationDenied("Esta solicitud de cambio ya fue resuelta.")
    required_capability = CHANGE_REQUEST_APPROVAL_CAPABILITY.get(change_request.field_name, "APPROVE_ROLE_CHANGE")
    if not has_capability(user, required_capability, package=change_request.package):
        log_denied_attempt(
            user, f"REJECT_CHANGE_REQUEST:{change_request.field_name}", package=change_request.package,
            resource=change_request, required_capability=required_capability,
        )
        raise AuthorizationDenied("No tiene permiso para rechazar esta solicitud de cambio.")
    with transaction.atomic():
        locked_change = ChangeRequest.objects.select_for_update().get(pk=change_request.pk)
        if locked_change.status != ChangeRequest.Status.PENDING:
            raise AuthorizationDenied("Esta solicitud de cambio ya fue resuelta.")
        locked_change.status = ChangeRequest.Status.REJECTED
        locked_change.decided_by = user
        locked_change.decided_at = timezone.now()
        locked_change.decision_comment = comment
        locked_change.save()
        from apps.procurement.models import ProcurementPackage

        package = ProcurementPackage.objects.select_for_update().get(pk=locked_change.package_id)
        package.is_on_hold = _package_has_unresolved_holds(package)
        package.save(update_fields=["is_on_hold"])
        audit.log(AuditEvent.Action.CHANGE_REQUEST, instance=locked_change, actor=user, summary=f"Solicitud de cambio rechazada: {locked_change}")
    return locked_change


def _package_has_unresolved_holds(package) -> bool:
    pending_changes = ChangeRequest.objects.filter(package=package, status=ChangeRequest.Status.PENDING)
    if pending_changes.exists():
        return True
    active_risks = RiskFlag.objects.filter(package=package, resolved_at__isnull=True).exclude(level=RiskFlag.Level.STANDARD)
    return active_risks.exists()


# ---------------------------------------------------------------------------
# Change Request projection — read authorization is a separate axis from
# decision authority and from mere package participation (CTCF-CR-PROJ-018).
#
# Three distinct permissions:
#   1. knowing a Change Request exists (package participation);
#   2. reading its raw field_name/old/new/reason values (this section);
#   3. approving or rejecting it (CHANGE_REQUEST_APPROVAL_CAPABILITY,
#      unchanged, enforced in approve_change_request/reject_change_request).
# Decision-button visibility is never treated as read authorization.
# ---------------------------------------------------------------------------


def can_view_change_request_detail(user, change_request) -> bool:
    """Detailed Change Request values are visible only to the requester,
    the decider (once decided), or an actor holding the same field-specific
    capability required to decide that field — package participation alone
    is never sufficient."""
    if user is not None and getattr(user, "is_authenticated", False):
        if change_request.requested_by_id and change_request.requested_by_id == user.id:
            return True
        if change_request.decided_by_id and change_request.decided_by_id == user.id:
            return True
    required_capability = CHANGE_REQUEST_APPROVAL_CAPABILITY.get(change_request.field_name, "APPROVE_ROLE_CHANGE")
    return has_capability(user, required_capability, package=change_request.package)


def change_request_projection(user, change_request) -> dict:
    """Server-side safe projection for a package-facing Change Request
    listing. A viewer with detailed read authority receives the raw
    field_name/frozen_current_value/proposed_new_value/reason; every other
    package-authorized viewer receives only a safe, generic projection with
    none of those values — never a fetched-then-hidden template value."""
    required_capability = CHANGE_REQUEST_APPROVAL_CAPABILITY.get(change_request.field_name, "APPROVE_ROLE_CHANGE")
    projection = {
        "id": change_request.id,
        "status": change_request.status,
        "status_display": change_request.get_status_display(),
        "created_at": change_request.created_at,
        "can_decide": (
            change_request.status == ChangeRequest.Status.PENDING
            and has_capability(user, required_capability, package=change_request.package)
        ),
    }
    if can_view_change_request_detail(user, change_request):
        projection.update({
            "detailed": True,
            "field_name": change_request.field_name,
            "frozen_current_value": change_request.frozen_current_value,
            "proposed_new_value": change_request.proposed_new_value,
            "reason": change_request.reason,
        })
    else:
        projection.update({
            "detailed": False,
            "safe_summary": (
                "Cambio pendiente de revisión autorizada." if change_request.status == ChangeRequest.Status.PENDING
                else "Cambio ya resuelto — revisión autorizada requerida para ver detalles."
            ),
        })
    return projection


# ---------------------------------------------------------------------------
# Risk flags — foundation only (spec section 17). Records risk and may
# trigger review/hold; never declares legality or approves an opaque
# transaction.
# ---------------------------------------------------------------------------


def raise_risk_flag(package, level, indicator_codes, notes="", user=None) -> RiskFlag:
    if not has_capability(user, "MANAGE_RISK_FLAGS", package=package):
        log_denied_attempt(user, "RAISE_RISK_FLAG", package=package, required_capability="MANAGE_RISK_FLAGS")
        raise AuthorizationDenied("No tiene permiso para crear señales de riesgo.")
    if level not in RiskFlag.Level.values or not isinstance(indicator_codes, (list, tuple)) or not indicator_codes:
        raise AuthorizationDenied("La señal de riesgo requiere un nivel y al menos un indicador válidos.")
    with transaction.atomic():
        from apps.procurement.models import ProcurementPackage

        package = ProcurementPackage.objects.select_for_update().get(pk=package.pk)
        if RiskFlag.objects.filter(
            package=package, level=level, indicator_codes=list(indicator_codes), resolved_at__isnull=True,
        ).exists():
            raise AuthorizationDenied("Ya existe una señal de riesgo activa equivalente.")
        flag = RiskFlag.objects.create(
            package=package, level=level, indicator_codes=list(indicator_codes), notes=notes,
            raised_by=user, created_by=user,
        )
        if level != RiskFlag.Level.STANDARD and not package.is_on_hold:
            package.is_on_hold = True
            package.save(update_fields=["is_on_hold"])
        audit.log(AuditEvent.Action.RISK_FLAG, instance=flag, actor=user, summary=f"Señal de riesgo: {flag}")
    return flag


def resolve_risk_flag(flag: RiskFlag, user) -> RiskFlag:
    if not has_capability(user, "MANAGE_RISK_FLAGS", package=flag.package):
        log_denied_attempt(
            user, "RESOLVE_RISK_FLAG", package=flag.package, resource=flag,
            required_capability="MANAGE_RISK_FLAGS",
        )
        raise AuthorizationDenied("No tiene permiso para resolver señales de riesgo.")
    if flag.resolved_at is not None:
        raise AuthorizationDenied("Esta señal de riesgo ya fue resuelta.")
    with transaction.atomic():
        locked_flag = RiskFlag.objects.select_for_update().get(pk=flag.pk)
        if locked_flag.resolved_at is not None:
            raise AuthorizationDenied("Esta señal de riesgo ya fue resuelta.")
        locked_flag.resolved_at = timezone.now()
        locked_flag.resolved_by = user
        locked_flag.save()
        from apps.procurement.models import ProcurementPackage

        package = ProcurementPackage.objects.select_for_update().get(pk=locked_flag.package_id)
        package.is_on_hold = _package_has_unresolved_holds(package)
        package.save(update_fields=["is_on_hold"])
        audit.log(AuditEvent.Action.RISK_FLAG, instance=locked_flag, actor=user, summary=f"Señal de riesgo resuelta: {locked_flag}")
    return locked_flag


# ---------------------------------------------------------------------------
# Derived Artifacts and the authorization-before-transformation boundary
# (spec sections 10, 12). The required order is always:
#   authorization -> permitted retrieval -> permitted field projection
#   -> optional transformation (translation/summary/AI)
# never the reverse. `transform_fn` is called ONLY with the already
# authorized projection dict — it structurally cannot see the full
# source object, which is the actual guarantee (not just a convention).
# ---------------------------------------------------------------------------


def _hash_projection(projection: dict) -> str:
    return hashlib.sha256(json.dumps(projection, sort_keys=True, default=str).encode()).hexdigest()


@transaction.atomic
def create_derived_artifact(source_obj, user, *, artifact_type, field_capability_map, transform_fn, language="",
                             package=None, policy_version="1", author_or_model="", target_classification=None) -> DerivedArtifact:
    allowed, projection = authorize_derived_artifact_source(user, source_obj, field_capability_map, package=package)
    if not allowed:
        raise AuthorizationDenied("No tiene permiso para generar un artefacto derivado de este recurso.")

    source_classification = getattr(source_obj, "classification", Classification.OPERATIONAL_SHARED)
    artifact_classification = target_classification or source_classification
    if is_less_restrictive(artifact_classification, source_classification):
        if not has_capability(user, "AUTHORIZE_DISCLOSURE", package=package):
            raise AuthorizationDenied(
                "Un artefacto derivado nunca puede volverse menos restrictivo que su fuente sin una "
                "decisión de divulgación autorizada explícita."
            )

    body_text = transform_fn(projection)

    artifact = DerivedArtifact.objects.create(
        content_type=ContentType.objects.get_for_model(source_obj), object_id=source_obj.pk,
        source_version=str(getattr(source_obj, "updated_at", "") or getattr(source_obj, "id", "")),
        source_hash=_hash_projection(projection), source_classification=source_classification,
        authorized_source_projection=projection, artifact_type=artifact_type, language=language,
        author_or_model=author_or_model or getattr(user, "username", "system"), policy_version=policy_version,
        classification=artifact_classification, body_text=body_text, created_by=user,
    )
    audit.log(AuditEvent.Action.DERIVED_ARTIFACT, instance=artifact, actor=user, summary=f"Artefacto derivado creado: {artifact}")
    return artifact


def mark_stale_if_source_changed(artifact: DerivedArtifact, source_obj) -> DerivedArtifact:
    current_version = str(getattr(source_obj, "updated_at", "") or getattr(source_obj, "id", ""))
    if current_version != artifact.source_version and not artifact.is_stale:
        artifact.is_stale = True
        artifact.save(update_fields=["is_stale"])
    return artifact


@transaction.atomic
def supersede_derived_artifact(old_artifact: DerivedArtifact, user, *, body_text, source_obj, field_capability_map,
                                transform_fn=None, package=None) -> DerivedArtifact:
    allowed, projection = authorize_derived_artifact_source(user, source_obj, field_capability_map, package=package)
    if not allowed:
        raise AuthorizationDenied("No tiene permiso para generar un artefacto derivado de este recurso.")
    new_body = transform_fn(projection) if transform_fn else body_text
    new_artifact = DerivedArtifact.objects.create(
        content_type=old_artifact.content_type, object_id=old_artifact.object_id,
        source_version=str(getattr(source_obj, "updated_at", "") or getattr(source_obj, "id", "")),
        source_hash=_hash_projection(projection), source_classification=getattr(source_obj, "classification", old_artifact.source_classification),
        authorized_source_projection=projection, artifact_type=old_artifact.artifact_type, language=old_artifact.language,
        author_or_model=old_artifact.author_or_model, policy_version=old_artifact.policy_version,
        classification=old_artifact.classification, body_text=new_body, supersedes=old_artifact, created_by=user,
    )
    audit.log(AuditEvent.Action.DERIVED_ARTIFACT, instance=new_artifact, actor=user, summary=f"Artefacto derivado reemplazado: {new_artifact}")
    return new_artifact
