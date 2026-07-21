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
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.accounts.models import Organization
from apps.audit.models import AuditEvent
from apps.governance import services as governance_services
from apps.governance.services import AuthorizationConfigurationError, AuthorizationDenied, has_capability
from apps.procurement.models import ProcurementPackage
from apps.procurement_gates import services
from apps.procurement_gates.models import GATE_CODES, GatePolicy, GatePolicyVersion, PackagePolicyAssignment

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
        services.assign_policy_to_package(package=package_a, policy_version=published, pinned_by=platform_admin)

        with pytest.raises(ProtectedError):
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
        services.assign_policy_to_package(package=package_a, policy_version=v1, pinned_by=platform_admin)

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
        resolved = services.resolve_policy_version_for_package(organization=None)
        assert resolved.policy_id == policy.pk

    def test_second_canonical_default_rejected_by_constraint(self, platform_admin):
        services.seed_canonical_policy()
        second = GatePolicy.objects.create(organization=None, code="second-canonical", name="Second", is_active=True)

        with pytest.raises(IntegrityError):
            with transaction.atomic():
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
        resolved = services.resolve_policy_version_for_package(organization=china_org)
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
    def test_pinning_is_permanent_repin_returns_same_assignment(self, package_a):
        services.seed_canonical_policy()
        first = services.assign_policy_to_package(package=package_a)
        second = services.assign_policy_to_package(package=package_a)
        assert first.pk == second.pk
        assert PackagePolicyAssignment.objects.filter(package=package_a).count() == 1

    def test_new_package_may_use_newer_version_without_changing_old_assignment(self, china_org, platform_admin, package_a, package_b):
        _grant_org_publish(platform_admin, china_org)
        policy = services.create_gate_policy(organization=china_org, code="two-versions", name="Two versions", actor=platform_admin)
        v1 = services.publish_policy_version(
            services.create_draft_policy_version(policy=policy, actor=platform_admin, gate_schema=_valid_schema()), platform_admin,
        )
        assignment_a = services.assign_policy_to_package(package=package_a, policy_version=v1, pinned_by=platform_admin)

        v2 = services.publish_policy_version(
            services.create_draft_policy_version(policy=policy, actor=platform_admin, gate_schema=_valid_schema(), supersedes=v1),
            platform_admin,
        )
        assignment_b = services.assign_policy_to_package(package=package_b, policy_version=v2, pinned_by=platform_admin)

        assignment_a.refresh_from_db()
        assert assignment_a.policy_version_id == v1.pk
        assert assignment_b.policy_version_id == v2.pk

    def test_assignment_rejects_unpublished_version(self, china_org, platform_admin, package_a):
        _grant_org_publish(platform_admin, china_org)
        policy = services.create_gate_policy(organization=china_org, code="draft-only", name="Draft only", actor=platform_admin)
        draft = services.create_draft_policy_version(policy=policy, actor=platform_admin, gate_schema=_valid_schema())

        with pytest.raises(services.GatePolicyValidationError):
            services.assign_policy_to_package(package=package_a, policy_version=draft)

    def test_repeated_assignment_call_is_idempotent(self, package_a):
        services.seed_canonical_policy()
        for _ in range(3):
            services.assign_policy_to_package(package=package_a)
        assert PackagePolicyAssignment.objects.filter(package=package_a).count() == 1

    def test_duplicate_active_assignment_blocked_by_db_constraint(self, package_a):
        canonical = services.seed_canonical_policy()
        version = canonical.versions.get(version_number=1)
        PackagePolicyAssignment.objects.create(
            package=package_a, policy_version=version, pinned_at=services.timezone.now(), is_active=True,
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                PackagePolicyAssignment.objects.create(
                    package=package_a, policy_version=version, pinned_at=services.timezone.now(), is_active=True,
                )

    def test_package_deletion_blocked_while_assignment_exists(self, package_a):
        services.seed_canonical_policy()
        services.assign_policy_to_package(package=package_a)
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
        "attempt_id", "prior_state", "resulting_state", "reason",
    }

    def test_pinning_audit_event_metadata_is_safe(self, package_a):
        services.seed_canonical_policy()
        services.assign_policy_to_package(package=package_a)
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
        assignment = services.assign_policy_to_package(package=package_a)
        governance_services.grant_capability("APPROVE_GATE", granted_to_user=harrison, package=package_a)
        with pytest.raises(services.GatePolicyValidationError):
            services.grant_gate_progression_exemption(assignment, harrison, "")

    def test_requires_capability(self, package_a, harrison):
        services.seed_canonical_policy()
        assignment = services.assign_policy_to_package(package=package_a)
        with pytest.raises(AuthorizationDenied):
            services.grant_gate_progression_exemption(assignment, harrison, "package closed before Milestone 1")

    def test_grant_succeeds_and_is_audited_distinctly_from_a_pass(self, package_a, harrison):
        services.seed_canonical_policy()
        assignment = services.assign_policy_to_package(package=package_a)
        governance_services.grant_capability("APPROVE_GATE", granted_to_user=harrison, package=package_a)
        updated = services.grant_gate_progression_exemption(assignment, harrison, "package closed before Milestone 1")
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

    def test_pinning_a_package_does_not_change_its_status(self, china_org):
        package = ProcurementPackage.objects.create(organization=china_org, code="status-independent", name="Status independent", status="active")
        services.seed_canonical_policy()
        services.assign_policy_to_package(package=package)
        package.refresh_from_db()
        assert package.status == "active"

    def test_no_admin_registration_for_procurement_gates_models(self):
        registered_models = set(admin.site._registry.keys())
        assert GatePolicy not in registered_models
        assert GatePolicyVersion not in registered_models
        assert PackagePolicyAssignment not in registered_models
