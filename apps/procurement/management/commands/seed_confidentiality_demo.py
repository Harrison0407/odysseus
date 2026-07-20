"""Seeds the live-validation scenario for the Controlled Transparency /
Controlled Confidentiality release: a DT Beach buyer organization, a
China trading company, Edison as an authorized China procurement user,
a hidden China tile factory, and a client DT Beach user — plus one
ProcurementPackage in CONTROLLED_CONFIDENTIALITY mode with Buyer/Seller
of Record/China Procurement Operator/Production Factory role
assignments already in place.

Idempotent — safe to re-run.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Organization
from apps.governance import services as governance_services
from apps.governance.models import CapabilityGrant, Party, PartyMembership
from apps.procurement import services
from apps.procurement.models import ProcurementPackage, Supplier

User = get_user_model()


class Command(BaseCommand):
    help = "Seeds the Controlled Transparency/Confidentiality live-validation scenario."

    def handle(self, *args, **options):
        dt_beach_org, _ = Organization.objects.get_or_create(name="DT Beach", defaults={"default_currency": "USD"})
        china_org, _ = Organization.objects.get_or_create(
            name="China Trading Co", defaults={"default_currency": "USD"},
        )

        edison = User.objects.filter(username="edison").first()
        if edison is None:
            raise CommandError("Run `manage.py seed_pilot_data` first — user 'edison' not found.")
        harrison = User.objects.filter(username="harrison").first()
        client_user, created = User.objects.get_or_create(username="dtbeach_client", defaults={"first_name": "Cliente", "last_name": "DT Beach"})
        if created:
            client_user.set_password("LiveTest123!")
            client_user.save()
        from apps.accounts.models import UserProfile

        UserProfile.objects.get_or_create(user=client_user, defaults={"organization": dt_beach_org})

        dt_beach_party, _ = Party.objects.get_or_create(
            display_name="DT Beach", hosting_organization=china_org,
            defaults={"party_type": Party.PartyType.ORGANIZATION, "organization": dt_beach_org},
        )
        PartyMembership.objects.get_or_create(party=dt_beach_party, user=client_user)

        trading_co_party, _ = Party.objects.get_or_create(
            display_name="China Trading Co", hosting_organization=china_org,
            defaults={"party_type": Party.PartyType.ORGANIZATION, "organization": china_org},
        )
        PartyMembership.objects.get_or_create(party=trading_co_party, user=edison)
        if harrison is not None:
            PartyMembership.objects.get_or_create(party=trading_co_party, user=harrison)

        hidden_factory_supplier, _ = Supplier.objects.get_or_create(
            organization=china_org, name="Foshan Hidden Tile Works",
            defaults={"country": "China", "address": "Unit 7, Ceramics Industrial Park, Foshan, Guangdong (CONFIDENTIAL)"},
        )
        factory_party, _ = Party.objects.get_or_create(
            display_name="Foshan Hidden Tile Works", hosting_organization=china_org,
            defaults={"party_type": Party.PartyType.ORGANIZATION, "supplier": hidden_factory_supplier, "is_hidden_by_default": True},
        )

        package, package_created = ProcurementPackage.objects.get_or_create(
            organization=china_org, code="dtbeach-tile-2026",
            defaults={"name": "DT Beach Ceramic Tile — 2026 Program"},
        )

        if package_created or not package.role_assignments.exists():
            services.assign_package_role(package, dt_beach_party, "buyer", edison, client_visible=True)
            services.assign_package_role(package, trading_co_party, "seller_of_record", edison, client_visible=True)
            services.assign_package_role(package, trading_co_party, "china_procurement_operator", edison)
            services.assign_package_role(package, factory_party, "production_factory", edison)

        # CTCF-AUDIT-017: privileged-audit access now requires an explicit
        # VIEW_PRIVILEGED_AUDIT CapabilityGrant, scoped to this demo's own
        # China Trading Co organization — never a bare can_override_gates
        # check, and never platform-wide.
        if harrison is not None and not CapabilityGrant.objects.filter(
            user=harrison, capability_code="VIEW_PRIVILEGED_AUDIT", organization=china_org, is_active=True,
        ).exists():
            governance_services.grant_capability(
                "VIEW_PRIVILEGED_AUDIT", user=harrison, granted_to_user=harrison, organization=china_org,
            )

        self.stdout.write(self.style.SUCCESS(
            f"Escenario listo. Paquete: {package.code} ({package.pk}). "
            f"DT Beach org: {dt_beach_org.pk}. China Trading Co org: {china_org.pk}. "
            f"Fábrica oculta (Supplier): {hidden_factory_supplier.pk}. Party fábrica: {factory_party.pk}. "
            f"Party comercializadora: {trading_co_party.pk}. Cliente: dtbeach_client / LiveTest123!."
        ))
