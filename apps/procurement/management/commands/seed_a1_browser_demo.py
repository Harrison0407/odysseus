"""Create a strictly synthetic, DEBUG-only browser walkthrough for A1."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import Organization, UserProfile
from apps.governance import services as governance
from apps.governance.models import CapabilityGrant, Party, RoleAssignment
from apps.procurement.models import ProcurementPackage
from apps.procurement_gates import services as gates
from apps.procurement_gates.models import PackagePolicyAssignment
from apps.workflow.models import Handoff


User = get_user_model()


class Command(BaseCommand):
    help = "Create idempotent synthetic users, grants, roles, package, and Handoff for the A1 browser walkthrough."

    PASSWORD = "SyntheticA1Demo!2026"
    ROLE_CODES = (
        "buyer",
        "seller_of_record",
        "china_procurement_operator",
        "production_factory",
        "logistics_operator",
        "quality_operator",
        "buyer_approver",
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--package-code",
            default="synthetic-a1-browser-demo",
            help="Explicit synthetic package code; each code is independently idempotent.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_a1_browser_demo refuses to run unless DEBUG=True.")

        organization, _ = Organization.objects.get_or_create(
            name="SYNTHETIC A1 Browser Demo Organization",
            defaults={"default_currency": "USD"},
        )
        operator = self._user("a1_demo_operator", "Synthetic", "Operator", organization)
        approver = self._user("a1_demo_approver", "Synthetic", "Approver", organization)

        package_code = options["package_code"]
        if not package_code.startswith("synthetic-a1-"):
            raise CommandError("--package-code must begin with 'synthetic-a1-'.")
        package, _ = ProcurementPackage.objects.get_or_create(
            organization=organization,
            code=package_code,
            defaults={
                "name": f"SYNTHETIC A1 Browser Package — {package_code}",
                "notes": "CONFIDENTIAL_PACKAGE_NOTES_SENTINEL_A1_BROWSER",
            },
        )

        self._grant(operator, gates.ASSIGN_GATE_POLICY, package=package)
        if not PackagePolicyAssignment.objects.filter(package=package).exists():
            gates.assign_policy_to_package(operator, package.pk)

        for role_code in self.ROLE_CODES:
            party, _ = Party.objects.get_or_create(
                hosting_organization=organization,
                display_name=f"CONFIDENTIAL_PARTY_SENTINEL_{role_code.upper()}",
                defaults={
                    "legal_name": f"CONFIDENTIAL_LEGAL_SENTINEL_{role_code.upper()}",
                    "is_hidden_by_default": role_code == "production_factory",
                },
            )
            RoleAssignment.objects.get_or_create(
                party=party,
                role_code=role_code,
                organization_context=organization,
                package=package,
                defaults={"effective_from": timezone.now().date()},
            )
        self._grant(
            operator,
            gates.CREATE_PROCUREMENT_GATE_ATTEMPT,
            organization=organization,
        )
        for capability in (
            gates.VIEW_PROCUREMENT_GATE_STATE,
            gates.CREATE_PROCUREMENT_GATE_ATTEMPT,
            gates.EVALUATE_PROCUREMENT_GATE,
            gates.REQUEST_PROCUREMENT_GATE_REVIEW,
            "APPROVE_GATE",
        ):
            self._grant(operator, capability, package=package)
        for capability in (gates.VIEW_PROCUREMENT_GATE_STATE, "APPROVE_GATE"):
            self._grant(approver, capability, package=package)

        content_type = ContentType.objects.get_for_model(ProcurementPackage)
        Handoff.objects.get_or_create(
            content_type=content_type,
            object_id=package.pk,
            gate_definition=None,
            defaults={
                "organization": organization,
                "status": "submitted",
                "readiness_snapshot": {"synthetic_preservation_sentinel": True},
            },
        )

        attempt_count = package.gate_attempts.count()
        self.stdout.write(self.style.SUCCESS(
            "Synthetic A1 browser demo ready.\n"
            f"URL: /compras/paquetes/{package.pk}/\n"
            f"Operator: a1_demo_operator / {self.PASSWORD}\n"
            f"Approver: a1_demo_approver / {self.PASSWORD}\n"
            "Operator grants: VIEW, initialize, evaluate, request review, open re-attempt, and a deliberate "
            "APPROVE_GATE grant for the self-approval denial check.\n"
            "Approver grants: VIEW and package-scoped APPROVE_GATE.\n"
            f"Existing A1 attempts: {attempt_count}. Re-running never rewrites immutable gate history."
        ))

    def _user(self, username, first_name, last_name, organization):
        user, _ = User.objects.get_or_create(
            username=username,
            defaults={"first_name": first_name, "last_name": last_name},
        )
        user.first_name = first_name
        user.last_name = last_name
        user.set_password(self.PASSWORD)
        user.save(update_fields=["first_name", "last_name", "password"])
        UserProfile.objects.update_or_create(user=user, defaults={"organization": organization})
        return user

    @staticmethod
    def _grant(user, capability, *, package=None, organization=None):
        if not CapabilityGrant.objects.filter(
            user=user,
            capability_code=capability,
            package=package,
            organization=organization,
            role_assignment__isnull=True,
            is_active=True,
        ).exists():
            governance.grant_capability(
                capability,
                user=user,
                granted_to_user=user,
                package=package,
                organization=organization,
            )
