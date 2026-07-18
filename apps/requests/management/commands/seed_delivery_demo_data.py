from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import transaction

from django.contrib.auth import get_user_model

from apps.accounts.models import Department, Organization, UserProjectAccess
from apps.inventory.models import (
    InventoryLot,
    InventoryMovement,
    MovementType,
    StorageSite,
    WarehouseLocation,
    WarehouseZone,
)
from apps.items.models import Item, ProductCategory, UnitOfMeasure
from apps.projects.models import Building, Project
from apps.requests.models import MaterialRequest, MaterialRequestLine
from apps.workflow.models import GateDefinition, WorkflowStage


class Command(BaseCommand):
    """Seeds a demonstrable Delivery -> Installation -> Inspection ->
    Final Acceptance starting point: a project, a warehouse location with
    real posted stock, and an approved material request ready to be
    reserved/dispatched through the new UI. Run after seed_pilot_data.

    This does not touch or duplicate the live-container fixture
    (import_live_container_fixture) — it is a separate, additive demo
    dataset scoped to this milestone's new screens.
    """

    help = "Seeds a project/stock/material-request starting point for the delivery-installation-acceptance demo."

    def add_arguments(self, parser):
        parser.add_argument("--org-name", default="DT Beach")

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            org = Organization.objects.get(name=options["org_name"])
        except Organization.DoesNotExist:
            raise SystemExit("Organización no encontrada — ejecute primero seed_pilot_data.")

        department_obra = Department.objects.get(organization=org, code="obra")
        department_direccion = Department.objects.get(organization=org, code="direccion")

        project, _ = Project.objects.get_or_create(organization=org, code="sole-26", defaults={"name": "Sole-26"})
        building, _ = Building.objects.get_or_create(project=project, code="ed-a", defaults={"name": "Edificio A"})

        category, _ = ProductCategory.objects.get_or_create(organization=org, name="Cuarzo y piedra")
        uom, _ = UnitOfMeasure.objects.get_or_create(organization=org, code="PC", defaults={"name": "Pieza"})
        item, _ = Item.objects.get_or_create(
            organization=org, name="Encimera de cuarzo — cocina tipo A", defaults={"category": category, "base_unit": uom}
        )

        site, _ = StorageSite.objects.get_or_create(
            organization=org, name="Almacén Central Demo", defaults={"site_type": StorageSite.SiteType.CENTRAL_WAREHOUSE}
        )
        zone_seco, _ = WarehouseZone.objects.get_or_create(site=site, code="seco", defaults={"name": "Seco y sensible"})
        location, _ = WarehouseLocation.objects.get_or_create(zone=zone_seco, code="A-1")
        zone_cuarentena, _ = WarehouseZone.objects.get_or_create(site=site, code="cuarentena", defaults={"name": "Cuarentena"})
        WarehouseLocation.objects.get_or_create(zone=zone_cuarentena, code="Q-1")

        lot, created_lot = InventoryLot.objects.get_or_create(item=item, lot_code="DEMO-DIA-1")
        if created_lot:
            InventoryMovement.objects.create(
                lot=lot, movement_type=MovementType.RECEIPT, quantity=Decimal("12"),
                unit_of_measure=uom, to_location=location, reason="Stock demo para instalación en Sole-26.",
            )

        mr, created_mr = MaterialRequest.objects.get_or_create(
            project=project, building=building, purpose="Demo: entrega, instalación, inspección y aceptación final.",
        )
        if created_mr:
            MaterialRequestLine.objects.create(
                request=mr, item=item, quantity_requested=Decimal("10"), quantity_approved=Decimal("10")
            )
            mr.status = MaterialRequest.Status.APPROVED
            mr.save(update_fields=["status"])

        # The 3 gates this milestone's screens use must exist for a fresh DB
        # that only ran seed_pilot_data before this milestone's migrations.
        stage_project, _ = WorkflowStage.objects.get_or_create(
            organization=org, code="project", defaults={"name": "Obra", "department": department_obra, "sequence": 6}
        )
        stage_installation, _ = WorkflowStage.objects.get_or_create(
            organization=org, code="installation", defaults={"name": "Instalación", "department": department_obra, "sequence": 7}
        )
        stage_inspection, _ = WorkflowStage.objects.get_or_create(
            organization=org, code="inspection", defaults={"name": "Inspección", "department": department_obra, "sequence": 8}
        )
        stage_acceptance, _ = WorkflowStage.objects.get_or_create(
            organization=org, code="acceptance", defaults={"name": "Aceptación", "department": department_direccion, "sequence": 9}
        )

        from apps.requests.models import Delivery, InstallationRecord

        GateDefinition.objects.get_or_create(
            organization=org, code="project_delivery_to_installation",
            defaults={
                "name": "Entrega a Proyecto → Instalación", "sequence": 6,
                "from_stage": stage_project, "to_stage": stage_installation,
                "from_department": department_obra, "to_department": department_obra,
                "target_content_type": ContentType.objects.get_for_model(Delivery),
            },
        )
        GateDefinition.objects.get_or_create(
            organization=org, code="installation_to_inspection",
            defaults={
                "name": "Instalación → Inspección", "sequence": 7,
                "from_stage": stage_installation, "to_stage": stage_inspection,
                "from_department": department_obra, "to_department": department_obra,
                "target_content_type": ContentType.objects.get_for_model(InstallationRecord),
            },
        )
        GateDefinition.objects.get_or_create(
            organization=org, code="inspection_to_acceptance",
            defaults={
                "name": "Inspección → Aceptación", "sequence": 8,
                "from_stage": stage_inspection, "to_stage": stage_acceptance,
                "from_department": department_obra, "to_department": department_direccion,
                "target_content_type": ContentType.objects.get_for_model(InstallationRecord),
            },
        )

        User = get_user_model()
        for username in ("miguel", "harrison", "marialuisa"):
            user = User.objects.filter(username=username).first()
            if user is not None:
                UserProjectAccess.objects.get_or_create(user=user, project=project)

        self.stdout.write(self.style.SUCCESS(
            f"Listo: proyecto {project} / solicitud {mr.id} aprobada con 10 unidades de {item}, "
            f"stock disponible en {location}."
        ))
