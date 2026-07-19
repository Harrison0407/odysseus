import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Organization
from apps.core.storage import document_storage
from apps.documents.models import Document, DocumentType, DocumentVersion
from apps.drawings.models import Drawing
from apps.drawings.services import register_drawing
from apps.projects.models import Building, Project

SOURCE_DIR = settings.BASE_DIR / "imports" / "buildings"

# (filename, title, drawing_type, discipline, status, issue_date, project_codes, building_number_by_project)
SOURCE_FILES = [
    {
        "filename": "Buildings Plans Main.pdf",
        "title": "Plano de conjunto — sitio The Beach at City Place",
        "drawing_type": Drawing.DrawingType.SITE_PLAN,
        "discipline": Drawing.Discipline.ARCHITECTURAL,
        "status": Drawing.Status.REFERENCE,
        "issue_date": None,
        "project_codes": ["arena", "mare", "sole", "sole-26", "palmera"],
        "building_number_by_project": {},
        "notes": "Plano maestro de conjunto con la numeración de edificios físicos por familia — fuente "
        "usada para determinar qué números de edificio pertenecen a cada familia en la importación de "
        "unidades (apps.projects.building_source_data).",
    },
    {
        "filename": "DT Beach Building Apartments.xlsx.pdf",
        "title": "Tabla de referencia de tipologías de apartamento por edificio",
        "drawing_type": Drawing.DrawingType.UNIT_TYPOLOGY,
        "discipline": Drawing.Discipline.ARCHITECTURAL,
        "status": Drawing.Status.REFERENCE,
        "issue_date": None,
        "project_codes": ["arena", "mare", "sole", "sole-26", "palmera"],
        "building_number_by_project": {},
        "notes": "Tabla fuente (planilla) usada para importar las unidades físicas — "
        "apps.projects.building_source_data la transcribe fielmente.",
    },
    {
        "filename": "PALMERA - PLANOS 13.11.2025.pdf",
        "title": "Planos arquitectónicos — Palmera (sótano y niveles)",
        "drawing_type": Drawing.DrawingType.BUILDING_PLAN,
        "discipline": Drawing.Discipline.ARCHITECTURAL,
        "status": Drawing.Status.DRAFT,
        "issue_date": datetime.date(2025, 11, 13),
        "project_codes": ["palmera"],
        "building_number_by_project": {"palmera": "1"},
        "notes": 'Conjunto multi-hoja; hoja H-02 (sótano) trae la nota "PLANOS AUN EN PROCESO, CONFIRMAR '
        'CUALQUIER TALLER NUEVO CON EL DEPARTAMENTO DE ARQUITECTURA" — registrado como Borrador, no Aprobado.',
    },
]


class Command(BaseCommand):
    help = "Registers the 3 supplied source PDFs (imports/buildings/) in the drawing register."

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

        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.filter(username="harrison").first() or User.objects.filter(profile__organization=org).first()

        doc_type, _ = DocumentType.objects.get_or_create(
            organization=org, code="architectural-drawing", defaults={"name": "Plano arquitectónico"},
        )

        registered = 0
        skipped = 0
        for spec in SOURCE_FILES:
            path = SOURCE_DIR / spec["filename"]
            if not path.exists():
                self.stdout.write(self.style.WARNING(f"No encontrado, omitido: {path}"))
                skipped += 1
                continue

            with open(path, "rb") as fh:
                stored = document_storage.save(fh, spec["filename"])
            duplicate = DocumentVersion.objects.filter(sha256=stored["sha256"]).exclude(document__organization=None).first()
            document = Document.objects.create(
                organization=org, document_type=doc_type, title=spec["title"], created_by=user,
            )
            DocumentVersion.objects.create(
                document=document, version_number=1, stored_name=stored["stored_name"],
                original_filename=stored["original_filename"], sha256=stored["sha256"],
                size_bytes=stored["size_bytes"], mime_type="application/pdf", uploaded_by=user,
                is_duplicate_of=duplicate, created_by=user,
            )

            for project_code in spec["project_codes"]:
                project = Project.objects.filter(organization=org, code=project_code).first()
                if project is None:
                    continue
                building = None
                building_number = spec["building_number_by_project"].get(project_code)
                if building_number:
                    building = Building.objects.filter(project=project, building_number=building_number).first()
                register_drawing(
                    document, user, project=project, drawing_type=spec["drawing_type"], title=spec["title"],
                    discipline=spec["discipline"], building=building, issue_date=spec["issue_date"],
                    status=spec["status"], notes=spec["notes"],
                )
                registered += 1

        self.stdout.write(self.style.SUCCESS(f"Planos registrados: {registered}. Archivos omitidos: {skipped}."))
