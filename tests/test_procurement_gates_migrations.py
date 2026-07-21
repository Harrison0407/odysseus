"""MigrationExecutor coverage for the frozen Increment 1 RunPython migration."""

import importlib
import sys

import pytest
from django.apps import apps as current_apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


pytestmark = pytest.mark.django_db(transaction=True)

FROM = [("procurement_gates", "0001_initial")]
TO = [("procurement_gates", "0002_seed_canonical_policy_and_assign_packages")]
EXECUTION_FROM = [("procurement_gates", "0004_protect_base_manager_writes")]
EXECUTION_TO = [("procurement_gates", "0005_gateattempt_gateevaluation_gatedecision_and_more")]


def _prepare_prior_state():
    executor = MigrationExecutor(connection)
    executor.migrate(FROM)
    state = executor.loader.project_state(FROM)
    apps = state.apps
    Assignment = apps.get_model("procurement_gates", "PackagePolicyAssignment")
    Version = apps.get_model("procurement_gates", "GatePolicyVersion")
    Policy = apps.get_model("procurement_gates", "GatePolicy")
    Assignment.objects.all().delete()
    Version.objects.all().delete()
    Policy.objects.all().delete()
    return apps


def _restore_latest():
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


def test_runpython_empty_database_and_live_services_unavailable(monkeypatch):
    try:
        _prepare_prior_state()
        module = importlib.import_module(
            "apps.procurement_gates.migrations.0002_seed_canonical_policy_and_assign_packages"
        )
        monkeypatch.setitem(sys.modules, "apps.procurement_gates.services", None)
        module = importlib.reload(module)
        assert module.canonical_gate_schema()["A1"]["decision_capability"] == "APPROVE_GATE"

        executor = MigrationExecutor(connection)
        executor.migrate(TO)
        apps = executor.loader.project_state(TO).apps
        Policy = apps.get_model("procurement_gates", "GatePolicy")
        Version = apps.get_model("procurement_gates", "GatePolicyVersion")
        Assignment = apps.get_model("procurement_gates", "PackagePolicyAssignment")
        assert Policy.objects.filter(is_canonical_default=True).count() == 1
        assert Version.objects.filter(status="published").count() == 1
        assert Assignment.objects.count() == 0
    finally:
        _restore_latest()


def test_runpython_populated_preserves_related_data_and_is_idempotent():
    try:
        apps = _prepare_prior_state()
        Organization = apps.get_model("accounts", "Organization")
        Package = apps.get_model("procurement", "ProcurementPackage")
        org = Organization.objects.create(name="Historical Migration Org", default_currency="USD")
        packages = []
        for status in ("draft", "active", "frozen", "closed"):
            package = Package.objects.create(
                organization=org, code=f"historical-{status}", name=status, status=status,
                is_frozen=status == "frozen", frozen_snapshot={"incoterm": "FOB"} if status == "frozen" else {},
                is_on_hold=status == "frozen",
            )
            packages.append(package)

        ChangeRequest = current_apps.get_model("governance", "ChangeRequest")
        RiskFlag = current_apps.get_model("governance", "RiskFlag")
        AuditEvent = current_apps.get_model("audit", "AuditEvent")
        ContentType = current_apps.get_model("contenttypes", "ContentType")
        EvidenceBundle = current_apps.get_model("audit", "EvidenceBundle")
        Handoff = current_apps.get_model("workflow", "Handoff")
        frozen = packages[2]
        change = ChangeRequest.objects.create(package_id=frozen.pk, field_name="incoterm", reason="preserve")
        risk = RiskFlag.objects.create(package_id=frozen.pk, level="high_risk", notes="preserve")
        content_type = ContentType.objects.get_for_model(Package)
        evidence = EvidenceBundle.objects.create(
            content_type=content_type, object_id=frozen.pk, bundle_type="other",
            required_evidence_types=["photo"],
        )
        handoff = Handoff.objects.create(
            content_type=content_type, object_id=frozen.pk, organization_id=org.pk,
            status="submitted", readiness_snapshot={"preserve": True},
        )
        existing_audit = AuditEvent.objects.create(
            action="other", content_type=content_type, object_id=frozen.pk,
            summary="preserve", metadata={"preserve": True},
        )
        package_snapshot = {
            package.pk: (package.status, package.is_frozen, package.frozen_snapshot, package.is_on_hold)
            for package in packages
        }

        executor = MigrationExecutor(connection)
        executor.migrate(TO)
        target_apps = executor.loader.project_state(TO).apps
        Assignment = target_apps.get_model("procurement_gates", "PackagePolicyAssignment")
        assert Assignment.objects.count() == 4
        assert all(Assignment.objects.filter(package_id=package.pk).count() == 1 for package in packages)

        module = importlib.import_module(
            "apps.procurement_gates.migrations.0002_seed_canonical_policy_and_assign_packages"
        )
        with connection.schema_editor() as schema_editor:
            module.seed_and_assign(target_apps, schema_editor)
        assert Assignment.objects.count() == 4

        for package in packages:
            refreshed = Package.objects.get(pk=package.pk)
            assert (
                refreshed.status, refreshed.is_frozen,
                refreshed.frozen_snapshot, refreshed.is_on_hold,
            ) == package_snapshot[package.pk]
        assert ChangeRequest.objects.filter(pk=change.pk, reason="preserve").exists()
        assert RiskFlag.objects.filter(pk=risk.pk, notes="preserve").exists()
        assert EvidenceBundle.objects.filter(pk=evidence.pk, required_evidence_types=["photo"]).exists()
        assert Handoff.objects.filter(pk=handoff.pk, status="submitted").exists()
        assert AuditEvent.objects.filter(pk=existing_audit.pk, metadata={"preserve": True}).exists()
    finally:
        _restore_latest()


def test_corrective_migration_rejects_duplicate_assignments():
    try:
        apps = _prepare_prior_state()
        Organization = apps.get_model("accounts", "Organization")
        Package = apps.get_model("procurement", "ProcurementPackage")
        org = Organization.objects.create(name="Duplicate Migration Org", default_currency="USD")
        package = Package.objects.create(organization=org, code="duplicate", name="Duplicate")
        executor = MigrationExecutor(connection)
        executor.migrate(TO)
        target_apps = executor.loader.project_state(TO).apps
        Assignment = target_apps.get_model("procurement_gates", "PackagePolicyAssignment")
        Version = target_apps.get_model("procurement_gates", "GatePolicyVersion")
        version = Version.objects.get(version_number=1)
        Assignment.objects.create(
            package_id=package.pk, policy_version=version,
            pinned_at=version.published_at, is_active=False,
        )
        with pytest.raises(RuntimeError, match="duplicate package assignments"):
            MigrationExecutor(connection).migrate([
                ("procurement_gates", "0003_enforce_increment_1_history")
            ])
        Assignment.objects.filter(package_id=package.pk, is_active=False).delete()
    finally:
        _restore_latest()

    source = importlib.import_module(
        "apps.procurement_gates.migrations.0002_seed_canonical_policy_and_assign_packages"
    )
    source_text = open(source.__file__).read()
    assert "apps.procurement_gates.services" not in source_text
    assert "apps.procurement_gates.models" not in source_text


def test_execution_schema_migration_is_additive_and_fabricates_no_history():
    try:
        executor = MigrationExecutor(connection)
        executor.migrate(EXECUTION_FROM)
        before_apps = executor.loader.project_state(EXECUTION_FROM).apps
        Organization = before_apps.get_model("accounts", "Organization")
        Package = before_apps.get_model("procurement", "ProcurementPackage")
        Policy = before_apps.get_model("procurement_gates", "GatePolicy")
        Version = before_apps.get_model("procurement_gates", "GatePolicyVersion")
        Assignment = before_apps.get_model("procurement_gates", "PackagePolicyAssignment")

        org = Organization.objects.create(name="Increment 2 migration org", default_currency="USD")
        package = Package.objects.create(
            organization=org,
            code="increment-2-existing",
            name="Existing package",
            status="frozen",
            is_frozen=True,
            frozen_snapshot={"preserve": "yes"},
            is_on_hold=True,
        )
        policy = Policy.objects.filter(is_canonical_default=True).first()
        if policy is None:
            policy = Policy.objects.create(
                organization=None, code="migration-canonical", name="Migration canonical",
                is_active=True, is_canonical_default=True,
            )
        version = Version.objects.filter(policy=policy, status="published").first()
        if version is None:
            version = Version.objects.create(
                policy=policy, version_number=1, status="published",
                gate_schema={code: {} for code in ("A1", "A2", "A3", "A4", "A5", "A6")},
            )
        assignment = Assignment.objects.create(
            package=package, policy_version=version, pinned_at=package.created_at,
            gate_progression_exempt=False,
        )

        executor = MigrationExecutor(connection)
        executor.migrate(EXECUTION_TO)
        after_apps = executor.loader.project_state(EXECUTION_TO).apps
        PackageAfter = after_apps.get_model("procurement", "ProcurementPackage")
        AssignmentAfter = after_apps.get_model("procurement_gates", "PackagePolicyAssignment")
        GateAttempt = after_apps.get_model("procurement_gates", "GateAttempt")
        GateEvaluation = after_apps.get_model("procurement_gates", "GateEvaluation")
        GateDecision = after_apps.get_model("procurement_gates", "GateDecision")
        PackageGateState = after_apps.get_model("procurement_gates", "PackageGateState")

        refreshed = PackageAfter.objects.get(pk=package.pk)
        assert (
            refreshed.status, refreshed.is_frozen, refreshed.frozen_snapshot, refreshed.is_on_hold,
        ) == ("frozen", True, {"preserve": "yes"}, True)
        assert AssignmentAfter.objects.filter(pk=assignment.pk, policy_version_id=version.pk).exists()
        assert GateAttempt.objects.count() == 0
        assert GateEvaluation.objects.count() == 0
        assert GateDecision.objects.count() == 0
        assert PackageGateState.objects.count() == 0
    finally:
        _restore_latest()
