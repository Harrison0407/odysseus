from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
class Command(BaseCommand):
    help = "Print the isolated synthetic procurement prototype URL; never persists production records."
    def handle(self, *args, **kwargs):
        if not settings.DEBUG or not settings.PROCUREMENT_PROTOTYPE_ENABLED:
            raise CommandError("Prototype seed is refused when DEBUG/prototype flag is disabled.")
        self.stdout.write(self.style.SUCCESS("Synthetic scenario ready (session-backed; idempotent, no database writes)."))
        self.stdout.write("http://127.0.0.1:8000/prototype/procurement/")
        self.stdout.write("Credentials: use any existing local development account; no synthetic credentials are created.")
