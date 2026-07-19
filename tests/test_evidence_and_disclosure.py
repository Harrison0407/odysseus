"""Tests for Evidence Bundles (uploader/verifier separation, missing
requirements), Verification Assertions, and Disclosure Grants (partial
field-scoped disclosure, expiration, revocation).
"""

import datetime

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.accounts.models import Organization
from apps.audit import services as audit_services
from apps.audit.models import EvidenceBundle
from apps.documents.models import Document, DocumentType, DocumentVersion
from apps.governance import services as governance_services
from apps.governance.models import Party, PartyMembership
from apps.procurement import services as procurement_services
from apps.procurement.models import ProcurementPackage, Supplier

pytestmark = pytest.mark.django_db


@pytest.fixture
def china_org():
    return Organization.objects.create(name="China Trading Co Evidence Test", default_currency="USD")


@pytest.fixture
def package(china_org):
    return procurement_services.create_package(china_org, "pkg-evidence-test", "Evidence Test Package", None)


@pytest.fixture
def doc_type(china_org):
    return DocumentType.objects.create(organization=china_org, code="production-photo-test", name="Foto de producción")


def _make_document(china_org, user, doc_type, filename="photo.jpg"):
    from apps.core.storage import document_storage

    stored = document_storage.save(SimpleUploadedFile(filename, b"fake-bytes"), filename)
    document = Document.objects.create(organization=china_org, document_type=doc_type, title=filename, created_by=user)
    DocumentVersion.objects.create(
        document=document, version_number=1, stored_name=stored["stored_name"], original_filename=stored["original_filename"],
        sha256=stored["sha256"], size_bytes=stored["size_bytes"], mime_type="image/jpeg", uploaded_by=user, created_by=user,
    )
    return document


@pytest.fixture
def china_ops_user(china_org, package, harrison):
    party = Party.objects.create(
        party_type=Party.PartyType.ORGANIZATION, display_name="China Trading Co", organization=china_org,
        hosting_organization=china_org,
    )
    PartyMembership.objects.create(party=party, user=harrison)
    procurement_services.assign_package_role(package, party, "china_procurement_operator", harrison)
    return harrison


class TestEvidenceBundleUploadNotVerification:
    def test_upload_does_not_verify(self, package, china_org, china_ops_user, doc_type, manuel):
        bundle = audit_services.create_evidence_bundle(package, EvidenceBundle.BundleType.PRODUCTION_VERIFICATION, china_ops_user, minimum_count=1)
        document = _make_document(china_org, manuel, doc_type)
        item = audit_services.add_evidence_item(bundle, manuel, document=document, evidence_type="photo")
        assert item.review_status == "pending"
        bundle.refresh_from_db()
        assert bundle.status != EvidenceBundle.Status.VERIFIED

    def test_bundle_missing_requirements(self, package, china_org, china_ops_user, doc_type, manuel):
        bundle = audit_services.create_evidence_bundle(
            package, EvidenceBundle.BundleType.QUALITY_CONTROL, china_ops_user, minimum_count=2,
        )
        document = _make_document(china_org, manuel, doc_type)
        audit_services.add_evidence_item(bundle, manuel, document=document, evidence_type="photo")
        missing = audit_services.bundle_missing_requirements(bundle)
        assert any("al menos 2" in m for m in missing)

    def test_uploader_cannot_verify_own_evidence(self, package, china_org, china_ops_user, doc_type):
        bundle = audit_services.create_evidence_bundle(package, EvidenceBundle.BundleType.PRODUCTION_VERIFICATION, china_ops_user, minimum_count=1)
        document = _make_document(china_org, china_ops_user, doc_type)
        item = audit_services.add_evidence_item(bundle, china_ops_user, document=document, evidence_type="photo")
        with pytest.raises(audit_services.EvidenceBundleError):
            audit_services.verify_evidence_item(item, china_ops_user, package=package)

    def test_verifier_requires_capability(self, package, china_org, china_ops_user, doc_type, manuel):
        bundle = audit_services.create_evidence_bundle(package, EvidenceBundle.BundleType.PRODUCTION_VERIFICATION, china_ops_user, minimum_count=1)
        document = _make_document(china_org, manuel, doc_type)
        item = audit_services.add_evidence_item(bundle, manuel, document=document, evidence_type="photo")
        # manuel has no capability at all in this package
        with pytest.raises(audit_services.EvidenceBundleError):
            audit_services.verify_evidence_item(item, manuel, package=package)

    def test_authorized_independent_verifier_succeeds_and_bundle_becomes_verified(self, package, china_org, china_ops_user, doc_type, manuel):
        bundle = audit_services.create_evidence_bundle(package, EvidenceBundle.BundleType.PRODUCTION_VERIFICATION, china_ops_user, minimum_count=1)
        document = _make_document(china_org, manuel, doc_type)
        item = audit_services.add_evidence_item(bundle, manuel, document=document, evidence_type="photo")
        # china_ops_user holds VIEW/CREATE capabilities by role default,
        # including implicit VERIFY via explicit grant for this test.
        assignment = package.role_assignments.filter(role_code="china_procurement_operator").first()
        governance_services.grant_capability("VERIFY_EVIDENCE", user=china_ops_user, role_assignment=assignment, package=package)
        verified_item = audit_services.verify_evidence_item(item, china_ops_user, verification_basis="Visual check on site", package=package)
        assert verified_item.review_status == "verified"
        bundle.refresh_from_db()
        assert bundle.status == EvidenceBundle.Status.VERIFIED


class TestVerificationAssertion:
    def test_assertion_requires_verified_bundle(self, package, china_org, china_ops_user, doc_type, manuel):
        bundle = audit_services.create_evidence_bundle(package, EvidenceBundle.BundleType.PRODUCTION_VERIFICATION, china_ops_user, minimum_count=1)
        document = _make_document(china_org, manuel, doc_type)
        audit_services.add_evidence_item(bundle, manuel, document=document, evidence_type="photo")
        with pytest.raises(procurement_services.PackageError):
            procurement_services.create_verification_assertion(
                package, "production_verified", "Production verified at an authorized site.", china_ops_user,
                source_evidence_bundle=bundle,
            )

    def test_assertion_created_after_verification(self, package, china_org, china_ops_user, doc_type, manuel):
        bundle = audit_services.create_evidence_bundle(package, EvidenceBundle.BundleType.PRODUCTION_VERIFICATION, china_ops_user, minimum_count=1)
        document = _make_document(china_org, manuel, doc_type)
        item = audit_services.add_evidence_item(bundle, manuel, document=document, evidence_type="photo")
        assignment = package.role_assignments.filter(role_code="china_procurement_operator").first()
        governance_services.grant_capability("VERIFY_EVIDENCE", user=china_ops_user, role_assignment=assignment, package=package)
        audit_services.verify_evidence_item(item, china_ops_user, package=package)

        assertion = procurement_services.create_verification_assertion(
            package, "production_verified", "Production verified at an authorized site.", china_ops_user,
            source_evidence_bundle=bundle,
        )
        assert assertion.client_visible_wording == "Production verified at an authorized site."
        assert "factory" not in assertion.client_visible_wording.lower()

    def test_client_safe_site_alias_never_reveals_party_name(self, package, china_org, china_ops_user):
        factory_supplier = Supplier.objects.create(organization=china_org, name="Very Secret Factory Name", country="China")
        factory_party = Party.objects.create(
            party_type=Party.PartyType.ORGANIZATION, display_name="Very Secret Factory Name",
            supplier=factory_supplier, hosting_organization=china_org, is_hidden_by_default=True,
        )
        procurement_services.assign_package_role(package, factory_party, "production_factory", china_ops_user)
        alias = procurement_services.client_safe_site_alias(factory_party, package)
        assert "Secret" not in alias
        assert alias == "Verified Production Site 1"


class TestDisclosureGrant:
    def test_partial_disclosure_exposes_only_approved_fields(self, package, china_org, china_ops_user):
        factory_party = Party.objects.create(
            party_type=Party.PartyType.ORGANIZATION, display_name="Hidden Factory Disclosure Test",
            hosting_organization=china_org, is_hidden_by_default=True,
        )
        client_org = Organization.objects.create(name="DT Beach Disclosure Test", default_currency="USD")
        assignment = package.role_assignments.filter(role_code="china_procurement_operator").first()
        governance_services.grant_capability("AUTHORIZE_DISCLOSURE", user=china_ops_user, role_assignment=assignment, package=package)

        grant = governance_services.create_disclosure_grant(
            factory_party, package, client_org, ["manufacturer_name"], "Client requested manufacturer name for warranty.",
            china_ops_user,
        )
        fields = governance_services.disclosed_fields(package, client_org)
        assert fields == {"manufacturer_name"}
        assert "address" not in fields
        assert "source_cost" not in fields

    def test_disclosure_requires_capability(self, package, china_org, harrison):
        factory_party = Party.objects.create(
            party_type=Party.PartyType.ORGANIZATION, display_name="Hidden Factory No Cap Test", hosting_organization=china_org,
        )
        client_org = Organization.objects.create(name="DT Beach No Cap Test", default_currency="USD")
        with pytest.raises(governance_services.AuthorizationDenied):
            governance_services.create_disclosure_grant(
                factory_party, package, client_org, ["manufacturer_name"], "reason", harrison,
            )

    def test_disclosure_expiration(self, package, china_org, china_ops_user):
        factory_party = Party.objects.create(
            party_type=Party.PartyType.ORGANIZATION, display_name="Hidden Factory Expiry Test", hosting_organization=china_org,
        )
        client_org = Organization.objects.create(name="DT Beach Expiry Test", default_currency="USD")
        assignment = package.role_assignments.filter(role_code="china_procurement_operator").first()
        governance_services.grant_capability("AUTHORIZE_DISCLOSURE", user=china_ops_user, role_assignment=assignment, package=package)
        grant = governance_services.create_disclosure_grant(
            factory_party, package, client_org, ["manufacturer_name"], "reason", china_ops_user,
            expires_at=timezone.now() - datetime.timedelta(days=1),
        )
        assert not grant.is_currently_active()
        assert governance_services.disclosed_fields(package, client_org) == set()

    def test_disclosure_revocation(self, package, china_org, china_ops_user):
        factory_party = Party.objects.create(
            party_type=Party.PartyType.ORGANIZATION, display_name="Hidden Factory Revoke Test", hosting_organization=china_org,
        )
        client_org = Organization.objects.create(name="DT Beach Revoke Test", default_currency="USD")
        assignment = package.role_assignments.filter(role_code="china_procurement_operator").first()
        governance_services.grant_capability("AUTHORIZE_DISCLOSURE", user=china_ops_user, role_assignment=assignment, package=package)
        grant = governance_services.create_disclosure_grant(
            factory_party, package, client_org, ["manufacturer_name"], "reason", china_ops_user,
        )
        assert governance_services.disclosed_fields(package, client_org) == {"manufacturer_name"}
        governance_services.revoke_disclosure_grant(grant, china_ops_user)
        assert governance_services.disclosed_fields(package, client_org) == set()
        # revocation never deletes the historical row
        from apps.governance.models import DisclosureGrant
        assert DisclosureGrant.objects.filter(pk=grant.pk).exists()

    def test_cannot_revoke_twice(self, package, china_org, china_ops_user):
        factory_party = Party.objects.create(
            party_type=Party.PartyType.ORGANIZATION, display_name="Hidden Factory Double Revoke Test", hosting_organization=china_org,
        )
        client_org = Organization.objects.create(name="DT Beach Double Revoke Test", default_currency="USD")
        assignment = package.role_assignments.filter(role_code="china_procurement_operator").first()
        governance_services.grant_capability("AUTHORIZE_DISCLOSURE", user=china_ops_user, role_assignment=assignment, package=package)
        grant = governance_services.create_disclosure_grant(
            factory_party, package, client_org, ["manufacturer_name"], "reason", china_ops_user,
        )
        governance_services.revoke_disclosure_grant(grant, china_ops_user)
        with pytest.raises(governance_services.AuthorizationDenied):
            governance_services.revoke_disclosure_grant(grant, china_ops_user)
