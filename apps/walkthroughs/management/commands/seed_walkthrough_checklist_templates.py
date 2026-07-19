from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Organization
from apps.walkthroughs.models import WalkthroughCategory, WalkthroughChecklistTemplateItem

# Initial configurable checklist for windows/aluminum sliding doors —
# seed data, editable/removable afterward, never a hard-coded branch
# in the service layer.
WINDOW_SLIDING_DOOR_CHECKLIST = [
    "Nivel del marco", "Plomo del marco", "Escuadra del marco", "Operación de corrediza",
    "Ajuste de rodillos", "Cerradura", "Manijas y herrajes", "Estado del vidrio", "Rayones",
    "Retiro de película protectora", "Espuma perimetral", "Sellado", "Espacios/holguras",
    "Drenaje", "Limpieza", "Operación final",
]


class Command(BaseCommand):
    help = "Seeds the initial windows/aluminum-sliding-doors walkthrough checklist template."

    def add_arguments(self, parser):
        parser.add_argument("--organization", default=None)

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

        category, _ = WalkthroughCategory.objects.get_or_create(
            organization=org, code="windows-sliding-doors", defaults={"name": "Ventanas y puertas corredizas de aluminio"},
        )
        created = 0
        for order, description in enumerate(WINDOW_SLIDING_DOOR_CHECKLIST, start=1):
            _, was_created = WalkthroughChecklistTemplateItem.objects.get_or_create(
                category=category, description=description, defaults={"sort_order": order},
            )
            if was_created:
                created += 1
        self.stdout.write(self.style.SUCCESS(f"Plantilla de checklist: {created} ítems nuevos creados (de {len(WINDOW_SLIDING_DOOR_CHECKLIST)})."))
