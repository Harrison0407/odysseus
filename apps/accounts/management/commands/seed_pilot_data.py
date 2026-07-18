import os
import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Department, Organization, Role, UserProfile, UserRole
from apps.documents.models import DocumentType, DocumentTypeAlias
from apps.items.models import ProductCategory, UnitOfMeasure

User = get_user_model()

# Named pilot users are configuration, never hard-coded business logic
# (spec section 7). Roles are assigned here as seed data; no view or model
# branches on any of these usernames.
PILOT_USERS = [
    {
        "username": "harrison",
        "first_name": "Harrison",
        "last_name": "",
        "roles": ["direccion", "china_origin", "management"],
        "department": "direccion",
    },
    {
        "username": "edison",
        "first_name": "Edison",
        "last_name": "",
        "roles": ["china_origin"],
        "department": "china_origin",
    },
    {
        "username": "markeris",
        "first_name": "Markeris",
        "last_name": "",
        "roles": ["compras"],
        "department": "compras",
    },
    {
        "username": "lucia",
        "first_name": "Lucía",
        "last_name": "",
        "roles": ["finanzas"],
        "department": "finanzas",
    },
    {
        "username": "manuel",
        "first_name": "Manuel",
        "last_name": "",
        "roles": ["almacen", "recepcion"],
        "department": "almacen",
    },
    {
        "username": "oscar",
        "first_name": "Óscar",
        "last_name": "",
        "roles": ["recepcion"],
        "department": "almacen",
    },
    {
        "username": "miguel",
        "first_name": "Miguel",
        "last_name": "",
        "roles": ["obra"],
        "department": "obra",
    },
    {
        "username": "marialuisa",
        "first_name": "María Luisa",
        "last_name": "",
        "roles": ["direccion", "management"],
        "department": "direccion",
    },
]

DEPARTMENTS = [
    ("compras", "Compras"),
    ("china_origin", "China / Origen"),
    ("finanzas", "Finanzas"),
    ("almacen", "Almacén"),
    ("obra", "Obra"),
    ("direccion", "Dirección"),
    ("aduanas_logistica", "Aduanas y Logística"),
]

ROLES = [
    ("compras", "Compras", False),
    ("china_origin", "China / Origen", False),
    ("finanzas", "Finanzas", False),
    ("almacen", "Almacén / Custodia", False),
    ("recepcion", "Recepción", False),
    ("obra", "Obra", False),
    ("direccion", "Dirección Ejecutiva", True),
    ("management", "Gerencia (solo lectura)", True),
    ("read_only", "Solo lectura", True),
    ("customs_logistics", "Aduanas y Logística", False),
    ("system_admin", "Administrador del sistema", True),
]

DOCUMENT_TYPES = [
    ("approved_requirement", "Requerimiento Aprobado", False, []),
    ("quotation", "Cotización", False, ["PI", "Proforma Invoice"]),
    ("purchase_order", "Orden de Compra", False, ["PO", "OC"]),
    ("purchase_order_revision", "Revisión de Orden de Compra", False, []),
    ("proforma_invoice", "Factura Proforma", False, ["PI"]),
    ("supplier_invoice", "Factura del Proveedor", False, ["CI", "Commercial Invoice"]),
    ("customer_invoice", "Factura al Cliente", False, ["CIAL"]),
    ("commercial_invoice", "Factura Comercial", False, ["CI"]),
    ("packing_list", "Lista de Empaque", False, ["PL"]),
    ("technical_packing_list", "Lista de Empaque Técnica", False, []),
    ("internal_operational_manifest", "Manifiesto Operativo Interno", False, []),
    ("detailed_receiving_manifest", "Manifiesto Detallado de Recepción", False, []),
    ("official_customs_cargo_set", "Expediente Oficial de Aduanas", True, []),
    ("manifest_amendment", "Enmienda de Manifiesto", False, []),
    ("loading_confirmation", "Confirmación de Carga", False, []),
    ("carryover_allocation", "Asignación de Saldo (Carryover)", False, []),
    ("replacement_or_warranty_note", "Nota de Reemplazo o Garantía", False, []),
    ("prior_fulfillment_evidence", "Evidencia de Cumplimiento Previo", False, []),
    ("bill_of_lading", "Conocimiento de Embarque", True, ["BL", "B/L"]),
    ("sea_waybill", "Sea Waybill", True, []),
    ("shipping_instruction", "Instrucción de Embarque", False, []),
    ("booking_confirmation", "Confirmación de Reserva", False, []),
    ("container_loading_list", "Lista de Carga del Contenedor", False, []),
    ("freight_quotation", "Cotización de Flete", False, []),
    ("freight_invoice", "Factura de Flete", False, []),
    ("payment_evidence", "Evidencia de Pago", False, []),
    ("certificate_of_origin", "Certificado de Origen", True, []),
    ("insurance_certificate", "Certificado de Seguro", False, []),
    ("customs_declaration", "Declaración de Aduanas", True, []),
    ("customs_liquidation", "Liquidación Aduanal", True, []),
    ("confotur_list", "Listado CONFOTUR", True, []),
    ("confotur_approval", "Aprobación CONFOTUR", True, []),
    ("arrival_notice", "Aviso de Llegada", False, []),
    ("delivery_note", "Nota de Entrega / Conduce", False, []),
    ("receipt_evidence", "Evidencia de Recepción", False, []),
    ("inspection_report", "Reporte de Inspección", False, []),
    ("claim_document", "Documento de Reclamo", False, []),
    ("other_supporting_document", "Otro Documento de Soporte", False, []),
]

UNITS = [("SET", "Juego / Set"), ("PC", "Pieza"), ("M2", "Metro cuadrado"), ("M3", "Metro cúbico"), ("KG", "Kilogramo"), ("M", "Metro")]

CATEGORIES = ["Cuarzo y piedra", "Puertas", "Cocinas", "Closets", "Vanities", "Ventanas y perfilería", "Herrajes", "Herramientas", "Consumibles"]


class Command(BaseCommand):
    help = "Seeds DT Beach organization, departments, roles, document taxonomy, and named pilot users."

    def add_arguments(self, parser):
        parser.add_argument("--org-name", default="DT Beach")

    @transaction.atomic
    def handle(self, *args, **options):
        org, _ = Organization.objects.get_or_create(
            name=options["org_name"], defaults={"legal_name": "DT Beach, S.R.L.", "default_currency": "USD"}
        )

        departments = {}
        for code, name in DEPARTMENTS:
            dept, _ = Department.objects.get_or_create(organization=org, code=code, defaults={"name": name})
            departments[code] = dept

        roles = {}
        for code, name, is_management in ROLES:
            role, _ = Role.objects.get_or_create(
                organization=org, code=code, defaults={"name": name, "is_management": is_management}
            )
            roles[code] = role

        for code, name in UNITS:
            UnitOfMeasure.objects.get_or_create(organization=org, code=code, defaults={"name": name})

        for name in CATEGORIES:
            ProductCategory.objects.get_or_create(organization=org, name=name)

        for code, name, is_official, aliases in DOCUMENT_TYPES:
            doc_type, _ = DocumentType.objects.get_or_create(
                organization=org, code=code, defaults={"name": name, "is_official_customs_document": is_official}
            )
            for alias in aliases:
                DocumentTypeAlias.objects.get_or_create(document_type=doc_type, alias_text=alias)

        self.stdout.write(self.style.SUCCESS(f"Organización: {org.name}"))
        self.stdout.write(f"Departamentos: {len(departments)} · Roles: {len(roles)} · Tipos de documento: {len(DOCUMENT_TYPES)}")

        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Credenciales generadas (guárdelas ahora, no se muestran de nuevo):"))
        for spec in PILOT_USERS:
            username = spec["username"]
            user, created = User.objects.get_or_create(
                username=username, defaults={"first_name": spec["first_name"], "last_name": spec["last_name"]}
            )
            env_password = os.environ.get(f"SEED_PASSWORD_{username.upper()}")
            if created:
                password = env_password or secrets.token_urlsafe(12)
                user.set_password(password)
                user.save()
                self.stdout.write(f"  {username}: {password}")
            elif env_password:
                user.set_password(env_password)
                user.save()

            UserProfile.objects.get_or_create(
                user=user,
                defaults={"organization": org, "primary_department": departments.get(spec["department"])},
            )
            for role_code in spec["roles"]:
                UserRole.objects.get_or_create(
                    user=user, role=roles[role_code], department=departments.get(spec["department"])
                )

        self.stdout.write(self.style.SUCCESS("Datos semilla completos."))
