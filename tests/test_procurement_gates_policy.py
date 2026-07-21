"""Tests for the Milestone 1 Increment 1 procurement gate policy and
package-assignment foundation (apps.procurement_gates), per
docs/MILESTONE_1_PROCUREMENT_GATES_CHARTER.md Sections 1-4, 10, 13, 14.

Gate execution (GateAttempt/GateEvaluation/GateDecision/overrides) is a
separate, not-yet-authorized increment and is not exercised here.
"""

import re

import pytest
from django.apps import apps as django_apps
from django.conf import settings
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError

from apps.accounts.models import Organization
from apps.audit.models import AuditEvent
from apps.governance import services as governance_services
from apps.governance.services import AuthorizationConfigurationError, AuthorizationDenied, has_capability
from apps.procurement.models import ProcurementPackage
from apps.procurement_gates import services
from apps.procurement_gates.models import (
    GATE_CODES, GatePolicy, GatePolicyVersion, ImmutableGateHistoryError,
    PackagePolicyAssignment, _allow_controlled_assignment_write,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def china_org():
    return Organization.objects.create(name="Gates Test China Org", default_currency="USD")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Gates Test Other Org", default_currency="USD")


@pytest.fixture
def package_a(china_org):
    return ProcurementPackage.objects.create(organization=china_org, code="gates-pkg-a", name="Package A")


@pytest.fixture
def package_b(china_org):
    return ProcurementPackage.objects.create(organization=china_org, code="gates-pkg-b", name="Package B")


@pytest.fixture
def platform_admin(harrison):
    """A user holding PUBLISH_GATE_POLICY as a pure platform-scoped grant
    (organization, package, and role_assignment all NULL)."""
    governance_services.grant_capability("PUBLISH_GATE_POLICY", granted_to_user=harrison)
    return harrison


def _grant_org_publish(user, organization):
    return governance_services.grant_capability("PUBLISH_GATE_POLICY", granted_to_user=user, organization=organization)


def _grant_assignment(user, package):
    return governance_services.grant_capability("ASSIGN_GATE_POLICY", granted_to_user=user, package=package)


def _historical_assignment(**kwargs):
    """Create fixture history through the same narrow internal write gate."""
    with _allow_controlled_assignment_write():
        return PackagePolicyAssignment.objects.create(**kwargs)


def _assign(package, actor, policy_version=None):
    _grant_assignment(actor, package)
    return services.assign_policy_to_package(
        actor, package.pk,
        explicit_policy_version_id=policy_version.pk if policy_version is not None else None,
    )


def _valid_schema(**overrides_per_gate):
    schema = {
        gate_code: {
            "evidence_requirement_codes": [],
            "decision_capability": "APPROVE_GATE",
            "attempt_creation_capability": services.CREATE_PROCUREMENT_GATE_ATTEMPT,
            "overridable": False,
            "non_overridable_requirements": [],
            "override_satisfies_successor_predecessor": False,
        }
        for gate_code in GATE_CODES
    }
    for gate_code, entry_overrides in overrides_per_gate.items():
        schema[gate_code].update(entry_overrides)
    return schema


# ---------------------------------------------------------------------------
# Installation and gate codes
# ---------------------------------------------------------------------------


class TestInstallationAndGateCodes:
    def test_app_is_installed(self):
        assert "apps.procurement_gates" in settings.INSTALLED_APPS
        django_apps.get_app_config("procurement_gates")

    def test_canonical_gate_codes_exact_and_ordered(self):
        assert GATE_CODES == ("A1", "A2", "A3", "A4", "A5", "A6")


# ---------------------------------------------------------------------------
# Publication rule (Charter tests 5, 6, 10, 10a, 10c, 10d)
# ---------------------------------------------------------------------------


class TestPublicationRule:
    def test_draft_version_is_editable(self, china_org, platform_admin):
        _grant_org_publish(platform_admin, china_org)
        policy = services.create_gate_policy(organization=china_org, code="p1", name="P1", actor=platform_admin)
        version = services.create_draft_policy_version(policy=policy, actor=platform_admin, gate_schema=_valid_schema())
        updated = services.update_draft_policy_version(
            policy_version=version, actor=platform_admin, gate_schema=_valid_schema(A1={"decision_capability": "APPROVE_TECHNICAL_SPEC"})
        )
        assert updated.gate_schema["A1"]["decision_capability"] == "APPROVE_TECHNICAL_SPEC"

    def test_published_version_is_not_editable(self, china_org, platform_admin):
        _grant_org_publish(platform_admin, china_org)
        policy = services.create_gate_policy(organization=china_org, code="p2", name="P2", actor=platform_admin)
        version = services.create_draft_policy_version(policy=policy, actor=platform_admin, gate_schema=_valid_schema())
        published = services.publish_policy_version(version, platform_admin)

        with pytest.raises(services.GatePolicyStateError):
            services.update_draft_policy_version(policy_version=published, actor=platform_admin, gate_schema=_valid_schema())

    def test_editing_published_version_rejected_at_model_layer_too(self, china_org, platform_admin):
        _grant_org_publish(platform_admin, china_org)
        policy = services.create_gate_policy(organization=china_org, code="p3", name="P3", actor=platform_admin)
        version = services.create_draft_policy_version(policy=policy, actor=platform_admin, gate_schema=_valid_schema())
        published = services.publish_policy_version(version, platform_admin)

        published.gate_schema = _valid_schema(A1={"decision_capability": "APPROVE_TECHNICAL_SPEC"})
        with pytest.raises(ValidationError):
            published.save()

    def test_published_version_cannot_be_deleted_while_pinned(self, china_org, platform_admin, package_a):
        _grant_org_publish(platform_admin, china_org)
        policy = services.create_gate_policy(organization=china_org, code="p4", name="P4", actor=platform_admin)
        version = services.create_draft_policy_version(policy=policy, actor=platform_admin, gate_schema=_valid_schema())
        published = services.publish_policy_version(version, platform_admin)
        _assign(package_a, platform_admin, published)

        with pytest.raises(ImmutableGateHistoryError):
            published.delete()

    def test_missing_gate_key_rejected(self):
        schema = _valid_schema()
        del schema["A3"]
        with pytest.raises(services.GatePolicyValidationError):
            services.validate_gate_schema(schema)

    def test_extra_gate_key_rejected(self):
        schema = _valid_schema()
        schema["A7"] = schema["A1"]
        with pytest.raises(services.GatePolicyValidationError):
            services.validate_gate_schema(schema)

    def test_malformed_overridable_rejected(self):
        schema = _valid_schema(A3={"overridable": "yes"})
        with pytest.raises(services.GatePolicyValidationError):
            services.validate_gate_schema(schema)

    def test_a2_overridable_true_rejected_regardless_of_other_validity(self):
        schema = _valid_schema(A2={"overridable": True})
        with pytest.raises(services.GatePolicyValidationError):
            services.validate_gate_schema(schema)

    def test_attempt_creation_capability_missing_rejected(self):
        schema = _valid_schema(A5={"attempt_creation_capability": ""})
        with pytest.raises(services.GatePolicyValidationError):
            services.validate_gate_schema(schema)

    def test_attempt_creation_capability_unregistered_rejected(self):
        schema = _valid_schema(A5={"attempt_creation_capability": "NOT_A_REAL_CAPABILITY"})
        with pytest.raises(services.GatePolicyValidationError):
            services.validate_gate_schema(schema)

    def test_publish_rejects_invalid_schema_and_leaves_version_draft(self, china_org, platform_admin):
        _grant_org_publish(platform_admin, china_org)
        policy = services.create_gate_policy(organization=china_org, code="p5", name="P5", actor=platform_admin)
        bad_schema = _valid_schema(A2={"overridable": True})
        version = GatePolicyVersion.objects.create(policy=policy, version_number=1, status="draft", gate_schema=bad_schema)

        with pytest.raises(services.GatePolicyValidationError):
            services.publish_policy_version(version, platform_admin)

        version.refresh_from_db()
        assert version.status == GatePolicyVersion.Status.DRAFT

    def test_publication_supersedes_chain_and_old_version_remains_valid(self, china_org, platform_admin, package_a):
        _grant_org_publish(platform_admin, china_org)
        policy = services.create_gate_policy(organization=china_org, code="p6", name="P6", actor=platform_admin)
        v1 = services.publish_policy_version(
            services.create_draft_policy_version(policy=policy, actor=platform_admin, gate_schema=_valid_schema()),
            platform_admin,
        )
        _assign(package_a, platform_admin, v1)

        v2 = services.publish_policy_version(
            services.create_draft_policy_version(policy=policy, actor=platform_admin, gate_schema=_valid_schema(), supersedes=v1),
            platform_admin,
        )
        assert v2.supersedes_id == v1.pk
        assert v2.version_number == 2

        # Old version remains valid/enforceable for the package already pinned to it.
        assignment = PackagePolicyAssignment.objects.get(package=package_a)
        assert assignment.policy_version_id == v1.pk
        v1.refresh_from_db()
        assert v1.status == GatePolicyVersion.Status.PUBLISHED


# ---------------------------------------------------------------------------
# Canonical default (Charter tests 7, 7a, 7b, 8)
# ---------------------------------------------------------------------------


class TestCanonicalDefault:
    def test_exactly_one_canonical_default_resolves(self):
        policy = services.seed_canonical_policy()
        resolved = policy.versions.get(version_number=1)
        assert resolved.policy_id == policy.pk

    def test_second_canonical_default_rejected_by_constraint(self, platform_admin):
        services.seed_canonical_policy()
        second = GatePolicy.objects.create(organization=None, code="second-canonical", name="Second", is_active=True)

        with pytest.raises(services.GatePolicyStateError):
            services.set_canonical_default(second, platform_admin)

    def test_organization_owned_policy_cannot_be_canonical_service_layer(self, china_org, platform_admin):
        policy = GatePolicy.objects.create(organization=china_org, code="org-owned", name="Org owned")
        with pytest.raises(services.GatePolicyValidationError):
            services.set_canonical_default(policy, platform_admin)

    def test_organization_owned_policy_cannot_be_canonical_model_layer(self, china_org):
        policy = GatePolicy.objects.create(organization=china_org, code="org-owned-2", name="Org owned 2")
        policy.is_canonical_default = True
        with pytest.raises(ValidationError):
            policy.save()

    def test_organization_policy_selected_over_canonical_default(self, china_org, platform_admin):
        services.seed_canonical_policy()
        _grant_org_publish(platform_admin, china_org)
        org_policy = services.create_gate_policy(organization=china_org, code="china-own", name="China own", actor=platform_admin)
        org_version = services.publish_policy_version(
            services.create_draft_policy_version(policy=org_policy, actor=platform_admin, gate_schema=_valid_schema()),
            platform_admin,
        )
        resolved = services._resolve_policy_version_for_organization(organization=china_org)
        assert resolved.pk == org_version.pk

    def test_withdrawing_only_canonical_published_version_rejected(self, platform_admin):
        policy = services.seed_canonical_policy()
        version = policy.versions.get(version_number=1)
        with pytest.raises(services.GatePolicyStateError):
            services.withdraw_policy_version(version, platform_admin, "attempting to remove the only canonical version")

    def test_withdrawing_canonical_version_with_replacement_succeeds(self, platform_admin):
        policy = services.seed_canonical_policy()
        v1 = policy.versions.get(version_number=1)
        v2 = services.publish_policy_version(
            services.create_draft_policy_version(policy=policy, actor=platform_admin, gate_schema=_valid_schema(), supersedes=v1),
            platform_admin,
        )
        withdrawn = services.withdraw_policy_version(v1, platform_admin, "superseded by v2")
        assert withdrawn.status == GatePolicyVersion.Status.WITHDRAWN
        assert withdrawn.withdrawal_reason == "superseded by v2"
        v2.refresh_from_db()
        assert v2.status == GatePolicyVersion.Status.PUBLISHED

    def test_withdrawal_requires_written_reason(self, platform_admin):
        policy = services.seed_canonical_policy()
        v1 = policy.versions.get(version_number=1)
        services.publish_policy_version(
            services.create_draft_policy_version(policy=policy, actor=platform_admin, gate_schema=_valid_schema(), supersedes=v1),
            platform_admin,
        )
        with pytest.raises(services.GatePolicyValidationError):
            services.withdraw_policy_version(v1, platform_admin, "")


# ---------------------------------------------------------------------------
# Package pinning (Charter test 9, plus assignment/idempotency rules)
# ---------------------------------------------------------------------------


class TestPackagePinning:
    def test_pinning_is_permanent_repin_returns_same_assignment(self, package_a, harrison):
        services.seed_canonical_policy()
        first = _assign(package_a, harrison)
        second = services.assign_policy_to_package(harrison, package_a.pk)
        assert first.pk == second.pk
        assert PackagePolicyAssignment.objects.filter(package=package_a).count() == 1

    def test_new_package_may_use_newer_version_without_changing_old_assignment(self, china_org, platform_admin, package_a, package_b):
        _grant_org_publish(platform_admin, china_org)
        policy = services.create_gate_policy(organization=china_org, code="two-versions", name="Two versions", actor=platform_admin)
        v1 = services.publish_policy_version(
            services.create_draft_policy_version(policy=policy, actor=platform_admin, gate_schema=_valid_schema()), platform_admin,
        )
        assignment_a = _assign(package_a, platform_admin, v1)

        v2 = services.publish_policy_version(
            services.create_draft_policy_version(policy=policy, actor=platform_admin, gate_schema=_valid_schema(), supersedes=v1),
            platform_admin,
        )
        assignment_b = _assign(package_b, platform_admin, v2)

        assignment_a.refresh_from_db()
        assert assignment_a.policy_version_id == v1.pk
        assert assignment_b.policy_version_id == v2.pk

    def test_assignment_rejects_unpublished_version(self, china_org, platform_admin, package_a):
        _grant_org_publish(platform_admin, china_org)
        policy = services.create_gate_policy(organization=china_org, code="draft-only", name="Draft only", actor=platform_admin)
        draft = services.create_draft_policy_version(policy=policy, actor=platform_admin, gate_schema=_valid_schema())

        with pytest.raises(services.GatePolicyValidationError):
            _assign(package_a, platform_admin, draft)

    def test_repeated_assignment_call_is_idempotent(self, package_a, harrison):
        services.seed_canonical_policy()
        _grant_assignment(harrison, package_a)
        for _ in range(3):
            services.assign_policy_to_package(harrison, package_a.pk)
        assert PackagePolicyAssignment.objects.filter(package=package_a).count() == 1

    def test_duplicate_active_assignment_blocked_by_db_constraint(self, package_a):
        canonical = services.seed_canonical_policy()
        version = canonical.versions.get(version_number=1)
        _historical_assignment(
            package=package_a, policy_version=version, pinned_at=services.timezone.now(), is_active=True,
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                _historical_assignment(
                    package=package_a, policy_version=version, pinned_at=services.timezone.now(), is_active=True,
                )

    def test_package_deletion_blocked_while_assignment_exists(self, package_a, harrison):
        services.seed_canonical_policy()
        _assign(package_a, harrison)
        with pytest.raises(ProtectedError):
            package_a.delete()


# ---------------------------------------------------------------------------
# Existing-package migration (Charter tests 11, 12, 13)
# ---------------------------------------------------------------------------


class TestExistingPackageMigration:
    def test_empty_database_seed_creates_exactly_one_canonical_policy_and_version(self):
        # A freshly migrated database (this test's own DB, with no
        # packages created yet) already proves the empty-database case:
        # migration 0002 seeds exactly one canonical policy + published
        # version and pins nothing, because nothing exists to pin.
        assert GatePolicy.objects.filter(is_canonical_default=True).count() == 1
        assert GatePolicyVersion.objects.filter(status=GatePolicyVersion.Status.PUBLISHED).count() == 1
        assert PackagePolicyAssignment.objects.count() == 0

        # Idempotent: calling the seed function again creates nothing new.
        services.seed_canonical_policy()
        assert GatePolicy.objects.filter(is_canonical_default=True).count() == 1
        assert GatePolicyVersion.objects.count() == 1

    def test_populated_migration_pins_every_package_across_all_statuses(self, china_org):
        packages = {
            status: ProcurementPackage.objects.create(organization=china_org, code=f"status-{status}", name=status, status=status)
            for status in ["draft", "active", "frozen", "closed"]
        }
        packages["frozen"].is_frozen = True
        packages["frozen"].frozen_snapshot = {"incoterm": "FOB"}
        packages["frozen"].save()

        assigned_count = services.assign_canonical_policy_to_existing_packages()
        assert assigned_count == 4

        for status, package in packages.items():
            assignment = PackagePolicyAssignment.objects.get(package=package)
            assert assignment.pinned_by is None
            assert assignment.is_active is True
            assert assignment.gate_progression_exempt is False
            package.refresh_from_db()
            assert package.status == status

        # Preserved exactly, untouched by the migration.
        packages["frozen"].refresh_from_db()
        assert packages["frozen"].is_frozen is True
        assert packages["frozen"].frozen_snapshot == {"incoterm": "FOB"}

    def test_migration_creates_no_fabricated_gate_history(self, china_org):
        ProcurementPackage.objects.create(organization=china_org, code="no-fab", name="No fabrication", status="closed")
        services.assign_canonical_policy_to_existing_packages()
        # No gate-execution action (GateAttempt/GateEvaluation/GateDecision)
        # is defined in this increment at all -- the migration's own audit
        # trail contains only policy-creation/publication/pinning events.
        fired_actions = set(AuditEvent.objects.values_list("action", flat=True))
        assert fired_actions.issubset({"gate_policy_created", "gate_policy_version_published", "gate_policy_pinned"})

    def test_rerunning_migration_creates_no_duplicate_assignments(self, china_org):
        ProcurementPackage.objects.create(organization=china_org, code="idempotent-pkg", name="Idempotent", status="active")
        first_run = services.assign_canonical_policy_to_existing_packages()
        second_run = services.assign_canonical_policy_to_existing_packages()
        assert first_run == 1
        assert second_run == 0
        assert PackagePolicyAssignment.objects.count() == 1


# ---------------------------------------------------------------------------
# Authorization (Charter Section 13, tests 27c/27c-1/27c-2 family, adapted
# to this increment's actual PUBLISH_GATE_POLICY consumer)
# ---------------------------------------------------------------------------


class TestOrganizationScopedHasCapability:
    def test_succeeds_for_pure_organization_scoped_grant(self, china_org, harrison):
        _grant_org_publish(harrison, china_org)
        assert has_capability(harrison, "PUBLISH_GATE_POLICY", organization=china_org) is True

    def test_fails_for_wrong_organization(self, china_org, other_org, harrison):
        _grant_org_publish(harrison, china_org)
        assert has_capability(harrison, "PUBLISH_GATE_POLICY", organization=other_org) is False

    def test_fails_for_unrelated_package_scoped_grant(self, china_org, package_a, harrison):
        governance_services.grant_capability("PUBLISH_GATE_POLICY", granted_to_user=harrison, package=package_a)
        assert has_capability(harrison, "PUBLISH_GATE_POLICY", organization=china_org) is False

    def test_fails_for_hybrid_grant_with_organization_and_package_both_set(self, china_org, package_a, harrison):
        from apps.governance.models import CapabilityGrant

        CapabilityGrant.objects.create(
            user=harrison, capability_code="PUBLISH_GATE_POLICY", organization=china_org, package=package_a, is_active=True,
        )
        assert has_capability(harrison, "PUBLISH_GATE_POLICY", organization=china_org) is False

    def test_fails_for_inactive_grant(self, china_org, harrison):
        grant = _grant_org_publish(harrison, china_org)
        grant.is_active = False
        grant.save()
        assert has_capability(harrison, "PUBLISH_GATE_POLICY", organization=china_org) is False

    def test_fails_for_superuser_with_no_grant(self, china_org, django_user_model):
        superuser = django_user_model.objects.create_superuser(username="gates-super", password="testpass123", email="s@example.com")
        assert has_capability(superuser, "PUBLISH_GATE_POLICY", organization=china_org) is False

    def test_fails_for_mere_organization_membership_with_no_grant(self, china_org, harrison):
        assert has_capability(harrison, "PUBLISH_GATE_POLICY", organization=china_org) is False

    def test_package_and_organization_are_mutually_exclusive(self, china_org, package_a, harrison):
        with pytest.raises(AuthorizationConfigurationError):
            has_capability(harrison, "PUBLISH_GATE_POLICY", package=package_a, organization=china_org)

    def test_existing_package_scoped_caller_behavior_unchanged(self, china_org, package_a, harrison):
        governance_services.grant_capability("VIEW_FACTORY_QUOTE", granted_to_user=harrison, package=package_a)
        assert has_capability(harrison, "VIEW_FACTORY_QUOTE", package=package_a) is True
        assert has_capability(harrison, "VIEW_FACTORY_QUOTE") is True  # unscoped legacy call, unaffected

    def test_no_procurement_gates_call_site_invokes_has_capability_unscoped(self):
        import apps.procurement_gates.services as gates_services_module

        source = open(gates_services_module.__file__).read()
        for match in re.finditer(r"has_capability\(([^)]*)\)", source):
            call_args = match.group(1)
            assert "package=" in call_args or "organization=" in call_args, (
                f"Unscoped has_capability call found: has_capability({call_args})"
            )


class TestPolicyAdministrationAuthorization:
    def test_publish_denied_without_capability_and_version_stays_draft(self, china_org, harrison):
        policy = GatePolicy.objects.create(organization=china_org, code="unauthorized-test", name="Unauthorized")
        version = GatePolicyVersion.objects.create(
            policy=policy, version_number=1, status="draft", gate_schema=_valid_schema(),
        )
        with pytest.raises(AuthorizationDenied):
            services.publish_policy_version(version, harrison)
        version.refresh_from_db()
        assert version.status == GatePolicyVersion.Status.DRAFT

    def test_denial_is_audited(self, china_org, harrison):
        policy = GatePolicy.objects.create(organization=china_org, code="audited-denial", name="Audited denial")
        version = GatePolicyVersion.objects.create(
            policy=policy, version_number=1, status="draft", gate_schema=_valid_schema(),
        )
        with pytest.raises(AuthorizationDenied):
            services.publish_policy_version(version, harrison)
        assert AuditEvent.objects.filter(action="privileged_access_denied").exists()

    def test_platform_scoped_policy_requires_platform_grant_not_org_grant(self, china_org, harrison):
        _grant_org_publish(harrison, china_org)  # org-scoped only, never platform-scoped
        canonical = services.seed_canonical_policy()
        v2_draft = GatePolicyVersion.objects.create(
            policy=canonical, version_number=2, status="draft", gate_schema=_valid_schema(),
        )
        with pytest.raises(AuthorizationDenied):
            services.publish_policy_version(v2_draft, harrison)

    def test_cross_organization_administration_denied(self, china_org, other_org, harrison):
        _grant_org_publish(harrison, china_org)
        other_org_policy = GatePolicy.objects.create(organization=other_org, code="other-org-policy", name="Other org")
        with pytest.raises(AuthorizationDenied):
            services.create_draft_policy_version(policy=other_org_policy, actor=harrison, gate_schema=_valid_schema())


# ---------------------------------------------------------------------------
# Audit events (Charter Section 10.1 -- safe metadata only)
# ---------------------------------------------------------------------------


class TestAuditEventSafety:
    _SAFE_KEYS = {
        "actor_id", "organization_id", "package_id", "policy_version_id", "gate_code",
        "attempt_id", "prior_state", "resulting_state", "reason", "policy_id", "assignment_id",
        "capability_code",
    }

    def test_pinning_audit_event_metadata_is_safe(self, package_a, harrison):
        services.seed_canonical_policy()
        _assign(package_a, harrison)
        event = AuditEvent.objects.get(action="gate_policy_pinned")
        assert set(event.metadata.keys()).issubset(self._SAFE_KEYS)
        assert "gate_schema" not in event.metadata

    def test_publication_audit_event_metadata_is_safe(self, china_org, platform_admin):
        _grant_org_publish(platform_admin, china_org)
        policy = services.create_gate_policy(organization=china_org, code="audit-safe", name="Audit safe", actor=platform_admin)
        version = services.create_draft_policy_version(policy=policy, actor=platform_admin, gate_schema=_valid_schema())
        services.publish_policy_version(version, platform_admin)
        event = AuditEvent.objects.get(action="gate_policy_version_published", object_id=version.pk)
        assert set(event.metadata.keys()).issubset(self._SAFE_KEYS)
        assert "gate_schema" not in event.metadata


# ---------------------------------------------------------------------------
# Gate progression exemption (Charter Section 4.4)
# ---------------------------------------------------------------------------


class TestGateProgressionExemption:
    def test_requires_written_reason(self, package_a, harrison):
        services.seed_canonical_policy()
        _assign(package_a, harrison)
        with pytest.raises(services.GatePolicyValidationError):
            services.grant_gate_progression_exemption(harrison, package_a.pk, "")

    def test_requires_capability(self, package_a, harrison):
        services.seed_canonical_policy()
        _assign(package_a, harrison)
        with pytest.raises(AuthorizationDenied):
            services.grant_gate_progression_exemption(harrison, package_a.pk, "package closed before Milestone 1")

    def test_grant_succeeds_and_is_audited_distinctly_from_a_pass(self, package_a, harrison):
        services.seed_canonical_policy()
        assignment = _assign(package_a, harrison)
        governance_services.grant_capability(
            "EXEMPT_PACKAGE_FROM_PROCUREMENT_GATES", granted_to_user=harrison, package=package_a
        )
        updated = services.grant_gate_progression_exemption(
            harrison, package_a.pk, "package closed before Milestone 1"
        )
        assert updated.gate_progression_exempt is True
        assert updated.exemption_reason == "package closed before Milestone 1"
        event = AuditEvent.objects.get(action="gate_progression_exemption_granted")
        assert "pass" not in event.summary.lower()


# ---------------------------------------------------------------------------
# Architecture preservation
# ---------------------------------------------------------------------------


class TestArchitecturePreservation:
    def test_procurement_gates_never_references_workflow_app(self):
        import apps.procurement_gates.models as models_module
        import apps.procurement_gates.services as services_module

        for module in (models_module, services_module):
            source = open(module.__file__).read()
            assert re.search(r"(^|\s)(from|import)\s+apps\.workflow\b", source) is None
            assert re.search(r"\bapps\.workflow\.\w", source) is None

    def test_pinning_a_package_does_not_change_its_status(self, china_org, harrison):
        package = ProcurementPackage.objects.create(organization=china_org, code="status-independent", name="Status independent", status="active")
        services.seed_canonical_policy()
        _assign(package, harrison)
        package.refresh_from_db()
        assert package.status == "active"

    def test_no_admin_registration_for_procurement_gates_models(self):
        registered_models = set(admin.site._registry.keys())
        assert GatePolicy not in registered_models
        assert GatePolicyVersion not in registered_models
        assert PackagePolicyAssignment not in registered_models


# ---------------------------------------------------------------------------
# Increment 1 Codex correction adversarial coverage (CX-I1-001..009)
# ---------------------------------------------------------------------------


class TestPermanentAssignmentGuards:
    def test_direct_assignment_creation_bypasses_no_public_boundary(self, package_a):
        version = services.seed_canonical_policy().versions.get(version_number=1)
        with pytest.raises(ImmutableGateHistoryError):
            PackagePolicyAssignment.objects.create(
                package=package_a, policy_version=version, pinned_at=services.timezone.now(),
            )

    def test_inactive_assignment_still_blocks_second_row(self, package_a):
        version = services.seed_canonical_policy().versions.get(version_number=1)
        original = _historical_assignment(
            package=package_a, policy_version=version, pinned_at=services.timezone.now(), is_active=False,
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                _historical_assignment(
                    package=package_a, policy_version=version, pinned_at=services.timezone.now(), is_active=True,
                )
        assert PackagePolicyAssignment.objects.get(package=package_a).pk == original.pk

    def test_existing_inactive_assignment_is_returned_unchanged(self, package_a, harrison):
        version = services.seed_canonical_policy().versions.get(version_number=1)
        original = _historical_assignment(
            package=package_a, policy_version=version, pinned_at=services.timezone.now(), is_active=False,
        )
        _grant_assignment(harrison, package_a)
        returned = services.assign_policy_to_package(harrison, package_a.pk)
        assert returned.pk == original.pk
        assert returned.is_active is False
        assert returned.policy_version_id == version.pk

    def test_instance_mutation_rejected(self, package_a):
        version = services.seed_canonical_policy().versions.get(version_number=1)
        assignment = _historical_assignment(
            package=package_a, policy_version=version, pinned_at=services.timezone.now(),
        )
        assignment.is_active = False
        with pytest.raises(ImmutableGateHistoryError):
            assignment.save()

    def test_queryset_and_bulk_update_rejected(self, package_a):
        version = services.seed_canonical_policy().versions.get(version_number=1)
        assignment = _historical_assignment(
            package=package_a, policy_version=version, pinned_at=services.timezone.now(),
        )
        with pytest.raises(ImmutableGateHistoryError):
            PackagePolicyAssignment.objects.filter(pk=assignment.pk).update(is_active=False)
        assignment.is_active = False
        with pytest.raises(ImmutableGateHistoryError):
            PackagePolicyAssignment.objects.bulk_update([assignment], ["is_active"])
        with pytest.raises(ImmutableGateHistoryError):
            PackagePolicyAssignment._base_manager.filter(pk=assignment.pk).update(is_active=False)

    def test_instance_and_queryset_delete_rejected(self, package_a):
        version = services.seed_canonical_policy().versions.get(version_number=1)
        assignment = _historical_assignment(
            package=package_a, policy_version=version, pinned_at=services.timezone.now(),
        )
        with pytest.raises(ImmutableGateHistoryError):
            assignment.delete()
        with pytest.raises(ImmutableGateHistoryError):
            PackagePolicyAssignment.objects.filter(pk=assignment.pk).delete()
        assert PackagePolicyAssignment.objects.filter(pk=assignment.pk).exists()

    def test_actor_deletion_preserves_assignment_and_nulls_attribution(
        self, package_a, django_user_model
    ):
        actor = django_user_model.objects.create_user(username="pin-actor", password="testpass123")
        version = services.seed_canonical_policy().versions.get(version_number=1)
        assignment = _historical_assignment(
            package=package_a, policy_version=version, pinned_at=services.timezone.now(),
            pinned_by=actor, created_by=actor,
        )
        actor.delete()
        assignment.refresh_from_db()
        assert assignment.pinned_by_id is None
        assert assignment.created_by_id is None


class TestVersionLifecycleGuards:
    def _published(self, china_org, platform_admin, code="guarded"):
        _grant_org_publish(platform_admin, china_org)
        policy = services.create_gate_policy(organization=china_org, code=code, name=code, actor=platform_admin)
        draft = services.create_draft_policy_version(
            policy=policy, actor=platform_admin, gate_schema=_valid_schema(),
        )
        return policy, services.publish_policy_version(draft, platform_admin)

    def test_published_to_draft_and_direct_withdrawal_rejected(self, china_org, platform_admin):
        _, version = self._published(china_org, platform_admin)
        for status in (GatePolicyVersion.Status.DRAFT, GatePolicyVersion.Status.WITHDRAWN):
            version.status = status
            with pytest.raises(ImmutableGateHistoryError):
                version.save()
            version.refresh_from_db()

    @pytest.mark.parametrize("status", [GatePolicyVersion.Status.PUBLISHED, GatePolicyVersion.Status.WITHDRAWN])
    def test_terminal_version_cannot_be_created_outside_lifecycle_service(self, china_org, status):
        policy = GatePolicy.objects.create(organization=china_org, code=f"direct-{status}", name=status)
        with pytest.raises(ImmutableGateHistoryError):
            GatePolicyVersion.objects.create(
                policy=policy, version_number=1, status=status, gate_schema=_valid_schema(),
            )

    def test_queryset_and_bulk_schema_rewrite_rejected(self, china_org, platform_admin):
        _, version = self._published(china_org, platform_admin)
        with pytest.raises(ImmutableGateHistoryError):
            GatePolicyVersion.objects.filter(pk=version.pk).update(gate_schema={"bad": {}})
        version.gate_schema = {"bad": {}}
        with pytest.raises(ImmutableGateHistoryError):
            GatePolicyVersion.objects.bulk_update([version], ["gate_schema"])
        with pytest.raises(ImmutableGateHistoryError):
            GatePolicyVersion._base_manager.filter(pk=version.pk).update(gate_schema={"bad": {}})

    @pytest.mark.parametrize("field,value", [("version_number", 99), ("policy_id", None)])
    def test_version_number_and_policy_rewrite_rejected(self, china_org, platform_admin, field, value):
        _, version = self._published(china_org, platform_admin, code=f"guarded-{field}")
        setattr(version, field, value)
        with pytest.raises(ImmutableGateHistoryError):
            version.save()

    def test_unpinned_published_and_withdrawn_delete_rejected(self, china_org, platform_admin):
        policy, published = self._published(china_org, platform_admin)
        with pytest.raises(ImmutableGateHistoryError):
            published.delete()
        replacement = services.publish_policy_version(
            services.create_draft_policy_version(
                policy=policy, actor=platform_admin, gate_schema=_valid_schema(), supersedes=published,
            ), platform_admin,
        )
        withdrawn = services.withdraw_policy_version(published, platform_admin, "superseded")
        assert replacement.status == GatePolicyVersion.Status.PUBLISHED
        with pytest.raises(ImmutableGateHistoryError):
            GatePolicyVersion.objects.filter(pk=withdrawn.pk).delete()

    def test_failed_replacement_rolls_back_publication_audit(self, platform_admin):
        policy = services.seed_canonical_policy()
        prior = policy.versions.get(version_number=1)
        bad = services.create_draft_policy_version(
            policy=policy, actor=platform_admin, gate_schema={"bad": {}}, supersedes=prior,
        )
        before = AuditEvent.objects.filter(action="gate_policy_version_published", object_id=bad.pk).count()
        with pytest.raises(services.GatePolicyValidationError):
            services.replace_canonical_policy_version(
                replacement_policy_version=bad, actor=platform_admin,
                prior_policy_version=prior, withdrawal_reason="replace",
            )
        bad.refresh_from_db()
        assert bad.status == GatePolicyVersion.Status.DRAFT
        assert AuditEvent.objects.filter(action="gate_policy_version_published", object_id=bad.pk).count() == before


class TestCanonicalCorrection:
    def test_canonical_identity_creation_is_service_controlled(self):
        with pytest.raises(ImmutableGateHistoryError):
            GatePolicy.objects.create(
                organization=None, code="direct-canonical", name="Direct canonical",
                is_canonical_default=True,
            )

    def test_queryset_and_bulk_canonical_mutation_rejected(self, china_org):
        policy = GatePolicy.objects.create(organization=china_org, code="org-policy", name="Org policy")
        with pytest.raises(ImmutableGateHistoryError):
            GatePolicy.objects.filter(pk=policy.pk).update(is_canonical_default=True)
        policy.is_canonical_default = True
        with pytest.raises(ImmutableGateHistoryError):
            GatePolicy.objects.bulk_update([policy], ["is_canonical_default"])
        with pytest.raises(ImmutableGateHistoryError):
            GatePolicy._base_manager.filter(pk=policy.pk).update(is_canonical_default=True)

    def test_database_check_rejects_organization_owned_canonical(self, china_org):
        policy = GatePolicy.objects.create(organization=china_org, code="db-check", name="DB check")
        canonical = GatePolicy.objects.get(is_canonical_default=True)
        table = GatePolicy._meta.db_table
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(f'UPDATE "{table}" SET is_canonical_default = 0 WHERE id = %s', [canonical.pk.hex])
                    cursor.execute(f'UPDATE "{table}" SET is_canonical_default = 1 WHERE id = %s', [policy.pk.hex])

    def test_atomic_canonical_version_replacement(self, platform_admin):
        policy = services.seed_canonical_policy()
        prior = policy.versions.get(version_number=1)
        replacement = services.create_draft_policy_version(
            policy=policy, actor=platform_admin, gate_schema=_valid_schema(), supersedes=prior,
        )
        result = services.replace_canonical_policy_version(
            replacement_policy_version=replacement, actor=platform_admin,
            prior_policy_version=prior, withdrawal_reason="replaced",
        )
        prior.refresh_from_db()
        assert result.status == GatePolicyVersion.Status.PUBLISHED
        assert prior.status == GatePolicyVersion.Status.WITHDRAWN
        assert policy.versions.filter(status=GatePolicyVersion.Status.PUBLISHED).count() == 1

    def test_canonical_family_switch_preserves_unique_marker(self, platform_admin):
        original = services.seed_canonical_policy()
        replacement = services.create_gate_policy(
            organization=None, code="replacement-family", name="Replacement family",
            actor=platform_admin,
        )
        services.publish_policy_version(
            services.create_draft_policy_version(
                policy=replacement, actor=platform_admin, gate_schema=_valid_schema(),
            ),
            platform_admin,
        )
        switched = services.set_canonical_default(replacement, platform_admin)
        original.refresh_from_db()
        assert switched.is_canonical_default is True
        assert original.is_canonical_default is False
        assert GatePolicy.objects.filter(is_canonical_default=True).count() == 1


class TestCapabilitySchemaCorrection:
    @pytest.mark.parametrize("field", ["decision_capability", "attempt_creation_capability"])
    @pytest.mark.parametrize("value", ["UNKNOWN", "Aprobar gate", "approve_gate", "", None, 7])
    def test_capability_values_fail_closed(self, field, value):
        with pytest.raises(services.GatePolicyValidationError):
            services.validate_gate_schema(_valid_schema(A1={field: value}))

    def test_canonical_capabilities_are_registered(self):
        services.validate_gate_schema(services.canonical_gate_schema())


class TestTenantSafeAssignment:
    def test_no_grant_wrong_package_unscoped_and_inactive_denied(self, package_a, package_b, harrison):
        services.seed_canonical_policy()
        for grant_kwargs in (
            {},
            {"package": package_b},
            {},
            {"package": package_a, "is_active": False},
        ):
            if grant_kwargs or AuditEvent.objects.filter(action="privileged_access_denied").count() == 2:
                from apps.governance.models import CapabilityGrant
                CapabilityGrant.objects.create(
                    user=harrison, capability_code="ASSIGN_GATE_POLICY", **grant_kwargs
                )
            with pytest.raises(AuthorizationDenied):
                services.assign_policy_to_package(harrison, package_a.pk)

    def test_cross_org_draft_withdrawn_and_stale_explicit_fail(self, china_org, other_org, package_a, platform_admin):
        services.seed_canonical_policy()
        _grant_assignment(platform_admin, package_a)
        _grant_org_publish(platform_admin, other_org)
        foreign = services.create_gate_policy(
            organization=other_org, code="foreign", name="Foreign", actor=platform_admin,
        )
        foreign_draft = services.create_draft_policy_version(
            policy=foreign, actor=platform_admin, gate_schema=_valid_schema(),
        )
        foreign_published = services.publish_policy_version(foreign_draft, platform_admin)
        with pytest.raises(services.GatePolicyValidationError):
            services.assign_policy_to_package(
                platform_admin, package_a.pk, explicit_policy_version_id=foreign_published.pk,
            )
        _grant_org_publish(platform_admin, china_org)
        own = services.create_gate_policy(organization=china_org, code="own", name="Own", actor=platform_admin)
        draft = services.create_draft_policy_version(policy=own, actor=platform_admin, gate_schema=_valid_schema())
        with pytest.raises(services.GatePolicyValidationError):
            services.assign_policy_to_package(platform_admin, package_a.pk, explicit_policy_version_id=draft.pk)
        published = services.publish_policy_version(draft, platform_admin)
        replacement = services.publish_policy_version(
            services.create_draft_policy_version(
                policy=own, actor=platform_admin, gate_schema=_valid_schema(), supersedes=published,
            ), platform_admin,
        )
        services.withdraw_policy_version(published, platform_admin, "withdrawn")
        with pytest.raises(services.GatePolicyValidationError):
            services.assign_policy_to_package(platform_admin, package_a.pk, explicit_policy_version_id=published.pk)
        assert replacement.status == GatePolicyVersion.Status.PUBLISHED

    def test_ambiguity_fails_closed_and_explicit_disambiguates(self, china_org, package_a, platform_admin):
        services.seed_canonical_policy()
        _grant_org_publish(platform_admin, china_org)
        _grant_assignment(platform_admin, package_a)
        versions = []
        for code in ("one", "two"):
            policy = services.create_gate_policy(organization=china_org, code=code, name=code, actor=platform_admin)
            versions.append(services.publish_policy_version(
                services.create_draft_policy_version(
                    policy=policy, actor=platform_admin, gate_schema=_valid_schema(),
                ), platform_admin,
            ))
        with pytest.raises(services.PolicyResolutionAmbiguity):
            services.assign_policy_to_package(platform_admin, package_a.pk)
        assigned = services.assign_policy_to_package(
            platform_admin, package_a.pk, explicit_policy_version_id=versions[0].pk,
        )
        assert assigned.policy_version_id == versions[0].pk

    def test_denial_audit_contains_only_safe_identifiers(self, package_a, harrison):
        package_a.name = "SENTINEL-PROTECTED-PACKAGE-NAME"
        package_a.save(update_fields=["name"])
        with pytest.raises(AuthorizationDenied):
            services.assign_policy_to_package(harrison, package_a.pk)
        event = AuditEvent.objects.filter(action="privileged_access_denied").latest("occurred_at")
        serialized = str(event.metadata)
        assert "SENTINEL" not in serialized
        assert event.metadata["package_id"] == str(package_a.pk)
        assert event.metadata["capability_code"] == "ASSIGN_GATE_POLICY"

    def test_denial_occurs_before_policy_resolution(self, package_a, harrison, monkeypatch):
        def protected_resolution(**kwargs):
            raise AssertionError("protected policy resolution was reached before authorization")

        monkeypatch.setattr(services, "_resolve_policy_version_for_organization", protected_resolution)
        with pytest.raises(AuthorizationDenied):
            services.assign_policy_to_package(harrison, package_a.pk)

    def test_caller_cannot_supply_organization_or_model_object(self, package_a, harrison):
        _grant_assignment(harrison, package_a)
        with pytest.raises(TypeError):
            services.assign_policy_to_package(
                harrison, package_a.pk, organization=package_a.organization,
            )
        with pytest.raises((TypeError, ValueError, ValidationError)):
            services.assign_policy_to_package(harrison, package_a)

    def test_one_family_uses_highest_version_and_zero_falls_back(self, china_org, package_a, package_b, platform_admin):
        canonical = services.seed_canonical_policy().versions.get(version_number=1)
        _grant_assignment(platform_admin, package_a)
        _grant_assignment(platform_admin, package_b)
        fallback = services.assign_policy_to_package(platform_admin, package_a.pk)
        assert fallback.policy_version_id == canonical.pk
        _grant_org_publish(platform_admin, china_org)
        policy = services.create_gate_policy(organization=china_org, code="single", name="Single", actor=platform_admin)
        v1 = services.publish_policy_version(
            services.create_draft_policy_version(policy=policy, actor=platform_admin, gate_schema=_valid_schema()),
            platform_admin,
        )
        v2 = services.publish_policy_version(
            services.create_draft_policy_version(
                policy=policy, actor=platform_admin, gate_schema=_valid_schema(), supersedes=v1,
            ), platform_admin,
        )
        selected = services.assign_policy_to_package(platform_admin, package_b.pk)
        assert selected.policy_version_id == v2.pk

    def test_system_initializer_is_narrow_and_not_actor_callable(self, package_a, harrison):
        version = services.seed_canonical_policy().versions.get(version_number=1)
        with pytest.raises(TypeError):
            services._assign_policy_to_package_system(
                package_a.pk, policy_version_id=version.pk, actor=harrison,
            )


class TestDedicatedExemptionCapability:
    def test_approve_gate_alone_does_not_exempt(self, package_a, harrison):
        _assign(package_a, harrison)
        governance_services.grant_capability("APPROVE_GATE", granted_to_user=harrison, package=package_a)
        with pytest.raises(AuthorizationDenied):
            services.grant_gate_progression_exemption(harrison, package_a.pk, "historical")

    def test_success_metadata_is_complete_and_safe(self, package_a, harrison):
        assignment = _assign(package_a, harrison)
        governance_services.grant_capability(
            "EXEMPT_PACKAGE_FROM_PROCUREMENT_GATES", granted_to_user=harrison, package=package_a,
        )
        services.grant_gate_progression_exemption(harrison, package_a.pk, "SENTINEL-PRIVATE-REASON")
        event = AuditEvent.objects.get(action="gate_progression_exemption_granted")
        assert event.metadata["package_id"] == str(package_a.pk)
        assert event.metadata["organization_id"] == str(package_a.organization_id)
        assert event.metadata["assignment_id"] == str(assignment.pk)
        assert event.metadata["policy_version_id"] == str(assignment.policy_version_id)
        assert event.metadata["exempt"] is True
        assert "SENTINEL" not in str(event.metadata)
        assert not AuditEvent.objects.filter(action__in=["gate_passed", "gate_evaluated"]).exists()

    def test_wrong_package_unscoped_inactive_and_superuser_are_denied(
        self, package_a, package_b, harrison, django_user_model
    ):
        _assign(package_a, harrison)
        from apps.governance.models import CapabilityGrant

        CapabilityGrant.objects.create(
            user=harrison, capability_code="EXEMPT_PACKAGE_FROM_PROCUREMENT_GATES", package=package_b,
        )
        CapabilityGrant.objects.create(
            user=harrison, capability_code="EXEMPT_PACKAGE_FROM_PROCUREMENT_GATES",
        )
        CapabilityGrant.objects.create(
            user=harrison, capability_code="EXEMPT_PACKAGE_FROM_PROCUREMENT_GATES",
            package=package_a, is_active=False,
        )
        with pytest.raises(AuthorizationDenied):
            services.grant_gate_progression_exemption(harrison, package_a.pk, "historical")
        superuser = django_user_model.objects.create_superuser(
            username="exemption-superuser", password="testpass123", email="exempt@example.com",
        )
        with pytest.raises(AuthorizationDenied):
            services.grant_gate_progression_exemption(superuser, package_a.pk, "historical")

    def test_exemption_denial_precedes_assignment_retrieval(self, package_a, harrison, monkeypatch):
        def protected_assignment_retrieval(*args, **kwargs):
            raise AssertionError("assignment was retrieved before authorization")

        monkeypatch.setattr(
            PackagePolicyAssignment.objects, "select_for_update", protected_assignment_retrieval,
        )
        with pytest.raises(AuthorizationDenied):
            services.grant_gate_progression_exemption(harrison, package_a.pk, "historical")
