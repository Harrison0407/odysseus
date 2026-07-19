from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Organization

from ...services import import_physical_property_master

User = get_user_model()


class Command(BaseCommand):
    help = (
        "Idempotent import of the physical building/floor/unit master data "
        "(imports/buildings/DT Beach Building Apartments.xlsx.pdf + "
        "Buildings Plans Main.pdf). Safe to run repeatedly."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Validate and report counts without committing any changes.",
        )
        parser.add_argument(
            "--organization", default=None,
            help="Organization name to import into (defaults to the first Organization row — "
            "matches every other bootstrap command's convention, but explicit is safer once "
            "more than one organization exists in a shared dev database).",
        )

    def handle(self, *args, **options):
        if options["organization"]:
            try:
                org = Organization.objects.get(name=options["organization"])
            except Organization.DoesNotExist:
                raise CommandError(f"No existe una organización llamada '{options['organization']}'.")
        else:
            org = Organization.objects.first()
        if org is None:
            raise CommandError("Run `manage.py seed_pilot_data` first.")

        result = import_physical_property_master(org, dry_run=options["dry_run"])

        self.stdout.write(self.style.WARNING("DRY RUN — no changes committed") if result.dry_run else "")
        self.stdout.write(f"Familias de edificio creadas: {result.families_created}")
        self.stdout.write(f"Edificios físicos creados: {result.buildings_created}")
        self.stdout.write(f"Pisos creados: {result.floors_created}")
        self.stdout.write(f"Unidades creadas: {result.units_created}")
        self.stdout.write(f"Unidades actualizadas: {result.units_updated}")
        self.stdout.write(f"Unidades sin cambios (omitidas): {result.units_skipped_unchanged}")
        if result.rejected:
            self.stdout.write(self.style.ERROR(f"Filas rechazadas: {len(result.rejected)}"))
            for row in result.rejected:
                self.stdout.write(f"  - {row}")
        if result.uncertain:
            self.stdout.write(self.style.WARNING(f"Filas con incertidumbre: {len(result.uncertain)}"))
            for row in result.uncertain:
                self.stdout.write(f"  - {row}")
        self.stdout.write(self.style.SUCCESS("Importación completa."))
