"""Existing-package initialization (Charter Section 4). Deterministic and
idempotent: safe on an empty database (seeds the canonical policy and
nothing else, per Section 4.5) and safe on a populated database (pins
every existing ProcurementPackage exactly once, per Section 4.2/4.3).
Creates no GateAttempt/GateEvaluation/GateDecision and fabricates no
PASSED result. Read-only with respect to ProcurementPackage.Status,
is_frozen, frozen_at, frozen_by, frozen_snapshot, and is_on_hold.

Uses `apps.get_model` for every schema-bound model, per standard Django
migration practice, so a later schema change to these models cannot
retroactively break this migration when replayed on a fresh database.
`canonical_gate_schema`/`validate_gate_schema` are imported directly from
`apps.procurement_gates.services` because they are pure functions over
plain constants (GATE_CODES, ALL_CAPABILITY_CODES) -- they perform no ORM
access and carry no schema-drift risk.
"""

from django.db import migrations, transaction
from django.utils import timezone

from apps.procurement_gates.services import canonical_gate_schema, validate_gate_schema

CANONICAL_POLICY_CODE = "canonical-a1-a6"


def seed_and_assign(apps, schema_editor):
    GatePolicy = apps.get_model("procurement_gates", "GatePolicy")
    GatePolicyVersion = apps.get_model("procurement_gates", "GatePolicyVersion")
    PackagePolicyAssignment = apps.get_model("procurement_gates", "PackagePolicyAssignment")
    ProcurementPackage = apps.get_model("procurement", "ProcurementPackage")
    AuditEvent = apps.get_model("audit", "AuditEvent")
    ContentType = apps.get_model("contenttypes", "ContentType")

    def log(action, instance, summary, **metadata):
        AuditEvent.objects.create(
            action=action,
            actor=None,
            content_type=ContentType.objects.get_for_model(instance),
            object_id=instance.pk,
            summary=summary,
            metadata=metadata,
        )

    with transaction.atomic():
        # --- Step 1: seed the canonical policy + initial published version ---
        policy = GatePolicy.objects.filter(is_canonical_default=True).first()
        if policy is None:
            policy = GatePolicy.objects.create(
                organization=None, code=CANONICAL_POLICY_CODE, name="Canonical A1-A6 Procurement Gate Policy",
                is_active=True, is_canonical_default=True,
            )
            log(
                "gate_policy_created", policy, "Canonical default gate policy seeded",
                organization_id=None, prior_state=None, resulting_state="CREATED",
            )

        version = GatePolicyVersion.objects.filter(policy=policy, version_number=1).first()
        if version is None:
            gate_schema = canonical_gate_schema()
            validate_gate_schema(gate_schema)
            version = GatePolicyVersion.objects.create(
                policy=policy, version_number=1, status="draft", gate_schema=gate_schema,
            )

        if version.status == "draft":
            validate_gate_schema(version.gate_schema)
            version.status = "published"
            version.published_at = timezone.now()
            version.published_by = None
            version.save(update_fields=["status", "published_at", "published_by", "updated_at"])
            log(
                "gate_policy_version_published", version,
                "Canonical gate policy version 1 published (system seed)",
                policy_version_id=str(version.pk), prior_state="DRAFT", resulting_state="PUBLISHED",
            )

        # --- Step 2: pin every existing package that has no active assignment ---
        already_pinned_package_ids = set(
            PackagePolicyAssignment.objects.filter(is_active=True).values_list("package_id", flat=True)
        )
        for package in ProcurementPackage.objects.all():
            if package.pk in already_pinned_package_ids:
                continue
            assignment = PackagePolicyAssignment.objects.create(
                package=package, policy_version=version, pinned_at=timezone.now(),
                pinned_by=None, is_active=True,
            )
            log(
                "gate_policy_pinned", assignment, f"Gate policy version pinned to package {package.code}",
                organization_id=str(package.organization_id), package_id=str(package.pk),
                policy_version_id=str(version.pk), prior_state=None, resulting_state="PINNED",
            )


class Migration(migrations.Migration):

    dependencies = [
        ("procurement_gates", "0001_initial"),
        ("procurement", "0004_purchaseorder_classification_purchaseorder_po_kind_and_more"),
        ("audit", "0004_add_gate_policy_action_codes"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(seed_and_assign, migrations.RunPython.noop),
    ]
