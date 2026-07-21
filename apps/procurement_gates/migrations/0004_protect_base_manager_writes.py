from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("procurement_gates", "0003_enforce_increment_1_history"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="gatepolicy",
            options={
                "base_manager_name": "objects",
                "ordering": ["-created_at"],
                "verbose_name_plural": "gate policies",
            },
        ),
        migrations.AlterModelOptions(
            name="gatepolicyversion",
            options={
                "base_manager_name": "objects",
                "ordering": ["policy", "-version_number"],
            },
        ),
        migrations.AlterModelOptions(
            name="packagepolicyassignment",
            options={
                "base_manager_name": "objects",
                "ordering": ["-pinned_at"],
            },
        ),
    ]
