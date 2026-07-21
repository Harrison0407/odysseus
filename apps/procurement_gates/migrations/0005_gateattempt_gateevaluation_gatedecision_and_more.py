import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models


GATE_CHOICES = [(code, code) for code in ("A1", "A2", "A3", "A4", "A5", "A6")]
STATE_CHOICES = [
    ("NOT_STARTED", "Not started"),
    ("BLOCKED", "Blocked"),
    ("READY", "Ready"),
    ("IN_REVIEW", "In review"),
    ("PASSED", "Passed"),
    ("FAILED", "Failed"),
    ("INVALIDATED", "Invalidated"),
    ("EXPIRED", "Expired"),
    ("OVERRIDDEN", "Overridden"),
    ("SUPERSEDED", "Superseded"),
]


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0005_alter_auditevent_action"),
        ("procurement_gates", "0004_protect_base_manager_writes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="GateAttempt",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("gate_code", models.CharField(choices=GATE_CHOICES, max_length=2)),
                ("attempt_number", models.PositiveIntegerField()),
                ("attempt_creation_capability", models.CharField(max_length=60)),
                ("decision_capability", models.CharField(max_length=60)),
                ("opened_at", models.DateTimeField()),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("closure_code", models.CharField(blank=True, default="", max_length=20)),
                ("idempotency_key", models.CharField(max_length=128, unique=True)),
                ("request_fingerprint", models.CharField(max_length=64)),
                ("review_requested_at", models.DateTimeField(blank=True, null=True)),
                ("review_idempotency_key", models.CharField(blank=True, default="", max_length=128)),
                ("assignment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="gate_attempts", to="procurement_gates.packagepolicyassignment")),
                ("closed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("opened_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("package", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="gate_attempts", to="procurement.procurementpackage")),
                ("policy_version", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="gate_attempts", to="procurement_gates.gatepolicyversion")),
                ("review_requested_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["package", "gate_code", "-attempt_number"],
                "base_manager_name": "objects",
            },
        ),
        migrations.CreateModel(
            name="GateEvaluation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("evaluated_at", models.DateTimeField()),
                ("requirement_results", models.JSONField(default=dict)),
                ("blocker_codes", models.JSONField(blank=True, default=list)),
                ("predecessor_valid", models.BooleanField()),
                ("package_on_hold_at_evaluation", models.BooleanField()),
                ("overall_ready", models.BooleanField()),
                ("result_code", models.CharField(choices=[("SATISFIED", "Satisfied"), ("BLOCKED", "Blocked")], max_length=20)),
                ("safe_summary", models.JSONField(blank=True, default=dict)),
                ("idempotency_key", models.CharField(max_length=128, unique=True)),
                ("request_fingerprint", models.CharField(max_length=64)),
                ("attempt", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="evaluations", to="procurement_gates.gateattempt")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("evaluated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("policy_version", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="gate_evaluations", to="procurement_gates.gatepolicyversion")),
            ],
            options={
                "ordering": ["attempt", "-evaluated_at", "-created_at"],
                "base_manager_name": "objects",
            },
        ),
        migrations.CreateModel(
            name="GateDecision",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("decided_at", models.DateTimeField()),
                ("outcome", models.CharField(choices=[("PASSED", "Passed"), ("FAILED", "Failed")], max_length=20)),
                ("comment", models.TextField(blank=True, default="")),
                ("prior_state", models.CharField(choices=STATE_CHOICES, max_length=20)),
                ("resulting_state", models.CharField(choices=STATE_CHOICES, max_length=20)),
                ("capability_code", models.CharField(max_length=60)),
                ("idempotency_key", models.CharField(max_length=128, unique=True)),
                ("request_fingerprint", models.CharField(max_length=64)),
                ("attempt", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="decision", to="procurement_gates.gateattempt")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("decided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("evaluation", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="decisions", to="procurement_gates.gateevaluation")),
            ],
            options={
                "ordering": ["-decided_at"],
                "base_manager_name": "objects",
            },
        ),
        migrations.AddField(
            model_name="gateattempt",
            name="review_evaluation",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="review_requests", to="procurement_gates.gateevaluation"),
        ),
        migrations.CreateModel(
            name="PackageGateState",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("gate_code", models.CharField(choices=GATE_CHOICES, max_length=2)),
                ("state", models.CharField(choices=STATE_CHOICES, max_length=20)),
                ("is_exempt", models.BooleanField(default=False)),
                ("last_recomputed_at", models.DateTimeField()),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("package", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="gate_state_cache", to="procurement.procurementpackage")),
            ],
            options={
                "ordering": ["package", "gate_code"],
                "base_manager_name": "objects",
            },
        ),
        migrations.AddIndex(
            model_name="gateevaluation",
            index=models.Index(fields=["attempt", "-evaluated_at"], name="procurement_attempt_dbcb1e_idx"),
        ),
        migrations.AddConstraint(
            model_name="gatedecision",
            constraint=models.CheckConstraint(
                check=models.Q(("outcome", "PASSED"), ("resulting_state", "PASSED"), _connector="AND")
                | models.Q(("outcome", "FAILED"), ("resulting_state", "FAILED"), _connector="AND"),
                name="gate_decision_result_consistent",
            ),
        ),
        migrations.AddConstraint(
            model_name="gatedecision",
            constraint=models.CheckConstraint(
                check=models.Q(("outcome", "PASSED")) | ~models.Q(("comment", "")),
                name="failed_gate_decision_requires_comment",
            ),
        ),
        migrations.AddIndex(
            model_name="gateattempt",
            index=models.Index(fields=["package", "gate_code", "closed_at"], name="procurement_package_e1f83c_idx"),
        ),
        migrations.AddConstraint(
            model_name="gateattempt",
            constraint=models.UniqueConstraint(fields=("package", "gate_code", "attempt_number"), name="unique_gate_attempt_number"),
        ),
        migrations.AddConstraint(
            model_name="gateattempt",
            constraint=models.UniqueConstraint(condition=models.Q(("closed_at__isnull", True)), fields=("package", "gate_code"), name="unique_open_gate_attempt"),
        ),
        migrations.AddConstraint(
            model_name="gateattempt",
            constraint=models.CheckConstraint(
                check=models.Q(("closed_at__isnull", True), ("closure_code", ""))
                | models.Q(("closed_at__isnull", False), ("closure_code__in", ["PASSED", "FAILED"])),
                name="gate_attempt_closure_consistent",
            ),
        ),
        migrations.AddConstraint(
            model_name="gateattempt",
            constraint=models.UniqueConstraint(
                condition=~models.Q(("review_idempotency_key", "")),
                fields=("review_idempotency_key",),
                name="unique_gate_review_idempotency_key",
            ),
        ),
        migrations.AddConstraint(
            model_name="packagegatestate",
            constraint=models.UniqueConstraint(fields=("package", "gate_code"), name="unique_package_gate_state"),
        ),
    ]
