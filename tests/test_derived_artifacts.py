"""Tests for Derived Artifacts and the authorization-before-transformation
boundary. The required order is always: authorize -> permitted
retrieval -> permitted projection -> optional transformation — never the
reverse. These tests use a mocked translation/AI service boundary
(`mock_translate`) that only ever receives the already-authorized
projection dict, never the full source object, to prove the forbidden
order ("retrieve full confidential resource -> send to AI -> hide
restricted content afterward") is structurally impossible here.
"""

import pytest

from apps.accounts.models import Organization
from apps.governance import services as governance_services
from apps.governance.models import Classification, DerivedArtifact, Party, PartyMembership
from apps.procurement import services as procurement_services

pytestmark = pytest.mark.django_db


@pytest.fixture
def china_org():
    return Organization.objects.create(name="China Trading Co Derived Test", default_currency="USD")


@pytest.fixture
def package(china_org):
    return procurement_services.create_package(china_org, "pkg-derived-test", "Derived Artifact Test Package", None)


@pytest.fixture
def china_ops_user(china_org, package, harrison):
    party = Party.objects.create(
        party_type=Party.PartyType.ORGANIZATION, display_name="China Trading Co", organization=china_org,
        hosting_organization=china_org,
    )
    PartyMembership.objects.create(party=party, user=harrison)
    procurement_services.assign_package_role(package, party, "china_procurement_operator", harrison)
    return harrison


@pytest.fixture
def factory_quote(package, china_org, china_ops_user):
    from apps.procurement.models import Supplier

    supplier = Supplier.objects.create(organization=china_org, name="Hidden Factory Derived Test", country="China", address="Secret Address")
    factory_party = Party.objects.create(
        party_type=Party.PartyType.ORGANIZATION, display_name="Hidden Factory Derived Test", supplier=supplier,
        hosting_organization=china_org, is_hidden_by_default=True,
    )
    return procurement_services.submit_factory_quote(
        package, factory_party, supplier, china_ops_user, reference="FQ-DERIVED-TEST", total_amount=5000,
    )


FIELD_CAPABILITY_MAP = {"reference": None, "total_amount": "VIEW_FACTORY_QUOTE", "currency": None}


def mock_translate(projection: dict) -> str:
    """Stands in for a real translation/AI provider — the whole point
    of this test double is that it only ever receives the authorized
    projection dict, never the full source Quotation object."""
    return " | ".join(f"{k}={v}" for k, v in sorted(projection.items()))


captured_calls = []


def spying_transform(projection: dict) -> str:
    captured_calls.append(projection)
    return mock_translate(projection)


class TestAuthorizationBeforeTransformation:
    def test_unauthorized_user_cannot_generate_artifact(self, factory_quote, manuel):
        with pytest.raises(governance_services.AuthorizationDenied):
            governance_services.create_derived_artifact(
                factory_quote, manuel, artifact_type=DerivedArtifact.ArtifactType.TRANSLATION,
                field_capability_map=FIELD_CAPABILITY_MAP, transform_fn=mock_translate, package=factory_quote.package,
            )

    def test_transform_function_never_receives_restricted_field_without_capability(self, factory_quote, manuel, package):
        # manuel has VIEW_CLIENT_QUOTE-style low capability only, granted
        # directly, but not VIEW_FACTORY_QUOTE — total_amount must never
        # reach the transform function.
        captured_calls.clear()
        party = Party.objects.create(party_type=Party.PartyType.INDIVIDUAL, display_name="Limited viewer", hosting_organization=package.organization)
        PartyMembership.objects.create(party=party, user=manuel)
        assignment = procurement_services.assign_package_role(package, party, "buyer", manuel)
        # Grant just enough capability to pass the classification gate for SOURCE_PRIVATE?
        # Deliberately do NOT grant VIEW_FACTORY_QUOTE — expect a denial.
        with pytest.raises(governance_services.AuthorizationDenied):
            governance_services.create_derived_artifact(
                factory_quote, manuel, artifact_type=DerivedArtifact.ArtifactType.TRANSLATION,
                field_capability_map=FIELD_CAPABILITY_MAP, transform_fn=spying_transform, package=package,
            )
        assert captured_calls == []

    def test_authorized_user_generates_artifact_with_only_projected_fields(self, factory_quote, china_ops_user, package):
        captured_calls.clear()
        artifact = governance_services.create_derived_artifact(
            factory_quote, china_ops_user, artifact_type=DerivedArtifact.ArtifactType.TRANSLATION,
            field_capability_map=FIELD_CAPABILITY_MAP, transform_fn=spying_transform, language="en", package=package,
        )
        assert len(captured_calls) == 1
        assert set(captured_calls[0].keys()) == {"reference", "total_amount", "currency"}
        assert artifact.source_classification == "source_private"
        assert artifact.classification == "source_private"
        assert artifact.authorized_source_projection == captured_calls[0]
        assert artifact.source_hash

    def test_artifact_cannot_become_less_restrictive_without_disclosure_capability(self, factory_quote, china_ops_user, package):
        with pytest.raises(governance_services.AuthorizationDenied):
            governance_services.create_derived_artifact(
                factory_quote, china_ops_user, artifact_type=DerivedArtifact.ArtifactType.CLIENT_SAFE_DOCUMENT,
                field_capability_map=FIELD_CAPABILITY_MAP, transform_fn=mock_translate, package=package,
                target_classification=Classification.CLIENT_SHARED,
            )

    def test_artifact_can_become_less_restrictive_with_disclosure_capability(self, factory_quote, china_ops_user, package):
        assignment = package.role_assignments.filter(role_code="china_procurement_operator").first()
        governance_services.grant_capability("AUTHORIZE_DISCLOSURE", user=china_ops_user, role_assignment=assignment, package=package)
        artifact = governance_services.create_derived_artifact(
            factory_quote, china_ops_user, artifact_type=DerivedArtifact.ArtifactType.CLIENT_SAFE_DOCUMENT,
            field_capability_map=FIELD_CAPABILITY_MAP, transform_fn=mock_translate, package=package,
            target_classification=Classification.CLIENT_SHARED,
        )
        assert artifact.classification == Classification.CLIENT_SHARED
        assert artifact.source_classification == "source_private"

    def test_language_change_does_not_alter_permissions(self, factory_quote, china_ops_user, package, manuel):
        artifact_en = governance_services.create_derived_artifact(
            factory_quote, china_ops_user, artifact_type=DerivedArtifact.ArtifactType.TRANSLATION,
            field_capability_map=FIELD_CAPABILITY_MAP, transform_fn=mock_translate, language="en", package=package,
        )
        assert artifact_en.classification == "source_private"
        # An unauthorized user requesting a different language still gets denied —
        # locale never changes the underlying authorization decision.
        with pytest.raises(governance_services.AuthorizationDenied):
            governance_services.create_derived_artifact(
                factory_quote, manuel, artifact_type=DerivedArtifact.ArtifactType.TRANSLATION,
                field_capability_map=FIELD_CAPABILITY_MAP, transform_fn=mock_translate, language="zh", package=package,
            )

    def test_stale_marking_when_source_changes(self, factory_quote, china_ops_user, package):
        artifact = governance_services.create_derived_artifact(
            factory_quote, china_ops_user, artifact_type=DerivedArtifact.ArtifactType.SUMMARY,
            field_capability_map=FIELD_CAPABILITY_MAP, transform_fn=mock_translate, package=package,
        )
        assert not artifact.is_stale
        factory_quote.total_amount = 9999
        factory_quote.save()
        governance_services.mark_stale_if_source_changed(artifact, factory_quote)
        artifact.refresh_from_db()
        assert artifact.is_stale

    def test_supersede_never_edits_original_artifact(self, factory_quote, china_ops_user, package):
        original = governance_services.create_derived_artifact(
            factory_quote, china_ops_user, artifact_type=DerivedArtifact.ArtifactType.SUMMARY,
            field_capability_map=FIELD_CAPABILITY_MAP, transform_fn=mock_translate, package=package,
        )
        original_body = original.body_text
        new_artifact = governance_services.supersede_derived_artifact(
            original, china_ops_user, body_text="updated", source_obj=factory_quote,
            field_capability_map=FIELD_CAPABILITY_MAP, transform_fn=mock_translate, package=package,
        )
        original.refresh_from_db()
        assert original.body_text == original_body
        assert new_artifact.supersedes_id == original.id


class TestGateOverrideHardening:
    def test_override_expiration(self, china_org):
        from apps.workflow.models import GateOverride

        override = GateOverride(expires_at=None)
        assert override.is_currently_active()

    def test_override_revocation_marks_inactive(self):
        import datetime

        from django.utils import timezone

        from apps.workflow.models import GateOverride

        override = GateOverride(revoked_at=timezone.now())
        assert not override.is_currently_active()

    def test_override_with_future_expiration_still_active(self):
        import datetime

        from django.utils import timezone

        from apps.workflow.models import GateOverride

        override = GateOverride(expires_at=timezone.now() + datetime.timedelta(days=1))
        assert override.is_currently_active()

    def test_override_with_past_expiration_inactive(self):
        import datetime

        from django.utils import timezone

        from apps.workflow.models import GateOverride

        override = GateOverride(expires_at=timezone.now() - datetime.timedelta(days=1))
        assert not override.is_currently_active()
