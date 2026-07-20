"""Adversarial coverage for CTCF-AUDIT-017: privileged-audit access must
require an explicit, currently-active, scoped VIEW_PRIVILEGED_AUDIT
CapabilityGrant -- never `can_override_gates` alone, never Django
superuser status alone -- and the underlying AuditEvent queryset must be
scoped to the caller's authorized organizations/packages BEFORE any event
is treated as visible. Confidential target identifiers (factory/Party
names, package identity, Disclosure Grant field scopes) must never reach
the rendered projection, for any viewer, authorized or not.
"""

import datetime

import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Organization, UserProfile
from apps.audit.models import AuditEvent
from apps.governance import services as governance_services
from apps.governance.models import CapabilityGrant, Party, PartyMembership
from apps.procurement import services as procurement_services
from apps.procurement.models import ProcurementPackage

pytestmark = pytest.mark.django_db


@pytest.fixture
def org_b():
    return Organization.objects.create(name="AUDIT_SCOPE_ORG_B", default_currency="USD")


@pytest.fixture
def package_a(organization):
    return procurement_services.create_package(organization, "audit-scope-pkg-a", "AUDIT_SCOPE_PACKAGE_A_SENTINEL", None)


@pytest.fixture
def package_a2(organization):
    """A second package in the SAME organization as package_a, used to
    prove a package-scoped grant does not widen to the whole organization."""
    return procurement_services.create_package(organization, "audit-scope-pkg-a2", "AUDIT_SCOPE_PACKAGE_A2_SENTINEL", None)


@pytest.fixture
def package_b(org_b):
    return procurement_services.create_package(org_b, "audit-scope-pkg-b", "AUDIT_SCOPE_PACKAGE_B_SENTINEL", None)


def _make_disclosure_event(package, org):
    factory = Party.objects.create(
        display_name=f"HIDDEN_FACTORY_SENTINEL_{package.code}", hosting_organization=org, is_hidden_by_default=True,
    )
    grant = governance_services.DisclosureGrant.objects.create(
        source_party=factory, package=package, recipient_organization=org,
        field_scope=["manufacturer_name"], classification_before="source_private",
        permitted_projection={"manufacturer_name": factory.display_name},
        reason=f"REASON_SENTINEL_{package.code}", effective_from=timezone.now(),
    )
    from apps.audit import services as audit

    event = audit.log(
        AuditEvent.Action.DISCLOSURE_GRANT, instance=grant,
        summary=f"Divulgación autorizada: {grant}", reason=grant.reason,
    )
    return event, grant, factory


def _grant_view_privileged_audit(user, *, organization=None, package=None, effective_from=None, effective_until=None, is_active=True):
    return governance_services.grant_capability(
        "VIEW_PRIVILEGED_AUDIT", user=user, granted_to_user=user, organization=organization, package=package,
        effective_from=effective_from, effective_until=effective_until,
    ) if is_active else CapabilityGrant.objects.create(
        user=user, capability_code="VIEW_PRIVILEGED_AUDIT", organization=organization, package=package, is_active=False,
    )


class TestPrivilegedAuditAuthority:
    def test_no_authority_is_denied_and_audited(self, client, manuel):
        client.force_login(manuel)
        response = client.get(reverse("governance:privileged-audit"))
        assert response.status_code == 404
        assert AuditEvent.objects.filter(
            action=AuditEvent.Action.PRIVILEGED_ACCESS_DENIED, actor=manuel, metadata__required_capability="VIEW_PRIVILEGED_AUDIT",
        ).exists()

    def test_can_override_gates_alone_is_not_sufficient(self, client, harrison):
        """harrison holds a can_override_gates role but no explicit
        VIEW_PRIVILEGED_AUDIT grant -- must be denied (the exact defect
        CTCF-AUDIT-017 requires closed)."""
        client.force_login(harrison)
        response = client.get(reverse("governance:privileged-audit"))
        assert response.status_code == 404

    def test_same_tenant_without_capability_is_denied(self, client, package_a, manuel):
        procurement_services.assign_package_role(
            package_a, Party.objects.create(display_name="Manuel Party", hosting_organization=package_a.organization), "buyer", manuel,
        )
        PartyMembership.objects.create(
            party=package_a.role_assignments.get(role_code="buyer").party, user=manuel,
        )
        client.force_login(manuel)
        response = client.get(reverse("governance:privileged-audit"))
        assert response.status_code == 404

    def test_active_organization_scoped_grant_is_authorized(self, client, organization, manuel):
        _grant_view_privileged_audit(manuel, organization=organization)
        client.force_login(manuel)
        response = client.get(reverse("governance:privileged-audit"))
        assert response.status_code == 200

    def test_active_package_scoped_grant_is_authorized(self, client, package_a, manuel):
        _grant_view_privileged_audit(manuel, package=package_a)
        client.force_login(manuel)
        response = client.get(reverse("governance:privileged-audit"))
        assert response.status_code == 200

    def test_expired_grant_is_denied(self, client, organization, manuel):
        yesterday = timezone.now().date() - datetime.timedelta(days=1)
        _grant_view_privileged_audit(manuel, organization=organization, effective_until=yesterday)
        client.force_login(manuel)
        response = client.get(reverse("governance:privileged-audit"))
        assert response.status_code == 404

    def test_revoked_grant_is_denied(self, client, organization, manuel):
        _grant_view_privileged_audit(manuel, organization=organization, is_active=False)
        client.force_login(manuel)
        response = client.get(reverse("governance:privileged-audit"))
        assert response.status_code == 404

    def test_future_grant_is_denied(self, client, organization, manuel):
        tomorrow = timezone.now().date() + datetime.timedelta(days=1)
        _grant_view_privileged_audit(manuel, organization=organization, effective_from=tomorrow)
        client.force_login(manuel)
        response = client.get(reverse("governance:privileged-audit"))
        assert response.status_code == 404


class TestPrivilegedAuditCrossScopeIsolation:
    def test_organization_grant_does_not_expose_other_organization_events(self, organization, org_b, package_a, package_b, manuel):
        event_a, grant_a, factory_a = _make_disclosure_event(package_a, organization)
        event_b, grant_b, factory_b = _make_disclosure_event(package_b, org_b)
        _grant_view_privileged_audit(manuel, organization=organization)

        ids = set(
            governance_services.privileged_audit_queryset(manuel).values_list("id", flat=True)
        )
        assert event_a.id in ids
        assert event_b.id not in ids

    def test_package_grant_does_not_widen_to_whole_organization(self, organization, package_a, package_a2, manuel):
        """A grant scoped to package_a must not expose package_a2's events
        even though both packages share the same hosting organization."""
        event_a, _, _ = _make_disclosure_event(package_a, organization)
        event_a2, _, _ = _make_disclosure_event(package_a2, organization)
        _grant_view_privileged_audit(manuel, package=package_a)

        ids = set(
            governance_services.privileged_audit_queryset(manuel).values_list("id", flat=True)
        )
        assert event_a.id in ids
        assert event_a2.id not in ids

    def test_organization_grant_does_not_become_platform_wide(self, organization, org_b, package_b, manuel):
        """An organization-scoped grant for `organization` must not expose
        an entirely unrelated organization's events."""
        event_b, _, _ = _make_disclosure_event(package_b, org_b)
        _grant_view_privileged_audit(manuel, organization=organization)

        ids = set(
            governance_services.privileged_audit_queryset(manuel).values_list("id", flat=True)
        )
        assert event_b.id not in ids

    def test_unresolvable_target_scope_is_excluded_not_included(self, organization, manuel):
        """An AuditEvent with no resolvable target (no content_type/object_id)
        must fail closed -- excluded, never included -- even for an actor
        holding a broad organization-scoped grant."""
        from apps.audit import services as audit

        orphan_event = audit.log(AuditEvent.Action.RISK_FLAG, summary="UNRESOLVABLE_TARGET_SENTINEL")
        _grant_view_privileged_audit(manuel, organization=organization)

        ids = set(
            governance_services.privileged_audit_queryset(manuel).values_list("id", flat=True)
        )
        assert orphan_event.id not in ids

    def test_http_response_row_count_reflects_scoped_queryset_only(self, client, organization, org_b, package_a, package_b, manuel):
        # Granting VIEW_PRIVILEGED_AUDIT itself creates one legitimate,
        # in-scope CAPABILITY_GRANT event -- accounted for below alongside
        # the org-A disclosure event; the org-B disclosure event must be
        # the only thing excluded.
        _grant_view_privileged_audit(manuel, organization=organization)
        _make_disclosure_event(package_a, organization)
        _make_disclosure_event(package_b, org_b)
        client.force_login(manuel)
        response = client.get(reverse("governance:privileged-audit"))
        assert response.status_code == 200
        rows = response.context["rows"]
        disclosure_rows = [r for r in rows if r["description"] == "Divulgación autorizada."]
        assert len(disclosure_rows) == 1


class TestPrivilegedAuditInformationAbsence:
    def test_confidential_target_identifiers_never_reach_the_response(self, client, organization, package_a, manuel):
        event, grant, factory = _make_disclosure_event(package_a, organization)
        _grant_view_privileged_audit(manuel, organization=organization)
        client.force_login(manuel)
        response = client.get(reverse("governance:privileged-audit"))
        body = response.content.decode()
        assert factory.display_name not in body
        assert "AUDIT_SCOPE_PACKAGE_A_SENTINEL" not in body
        assert "manufacturer_name" not in body
        assert grant.reason not in body
        assert "REASON_SENTINEL" not in body
        # Confirmed at the projection layer too, independent of template escaping.
        for row in response.context["rows"]:
            assert "summary" not in row
            assert "metadata" not in row

    def test_unauthorized_denial_response_is_non_disclosing(self, client, organization, package_a, manuel):
        _make_disclosure_event(package_a, organization)
        client.force_login(manuel)
        response = client.get(reverse("governance:privileged-audit"))
        assert response.status_code == 404
        assert b"AUDIT_SCOPE_PACKAGE_A_SENTINEL" not in response.content

    def test_denial_audit_payload_carries_no_confidential_values(self, client, organization, manuel):
        client.force_login(manuel)
        client.get(reverse("governance:privileged-audit"))
        denial = AuditEvent.objects.filter(
            action=AuditEvent.Action.PRIVILEGED_ACCESS_DENIED, actor=manuel,
        ).order_by("-occurred_at").first()
        assert denial is not None
        assert denial.metadata.get("required_capability") == "VIEW_PRIVILEGED_AUDIT"
        assert "AUDIT_SCOPE" not in str(denial.metadata)
