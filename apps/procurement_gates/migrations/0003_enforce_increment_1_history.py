from django.db import migrations, models
from django.db.models import Count, Q


def reject_duplicate_assignments(apps, schema_editor):
    Assignment = apps.get_model("procurement_gates", "PackagePolicyAssignment")
    duplicates = list(
        Assignment.objects.values("package_id")
        .annotate(row_count=Count("id"))
        .filter(row_count__gt=1)
        .values_list("package_id", flat=True)
    )
    if duplicates:
        raise RuntimeError(
            "Cannot enforce permanent package-policy assignment uniqueness; "
            f"duplicate package assignments exist for package IDs: {duplicates}"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("procurement_gates", "0002_seed_canonical_policy_and_assign_packages"),
    ]

    operations = [
        migrations.RunPython(reject_duplicate_assignments, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="packagepolicyassignment",
            name="unique_active_package_policy_assignment",
        ),
        migrations.AddConstraint(
            model_name="packagepolicyassignment",
            constraint=models.UniqueConstraint(
                fields=("package",), name="unique_package_policy_assignment"
            ),
        ),
        migrations.AddConstraint(
            model_name="gatepolicy",
            constraint=models.CheckConstraint(
                check=Q(is_canonical_default=False) | Q(organization__isnull=True),
                name="canonical_gate_policy_must_be_platform_scoped",
            ),
        ),
    ]
