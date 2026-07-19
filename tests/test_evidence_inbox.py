"""Tests for the Unclassified Evidence Inbox (one-shot release). Every
upload preserves uploader/timestamp/filename/checksum via the same
Document/DocumentVersion mechanism used everywhere else; classification
is a separate, reassignable, generic pointer layered on top and never
modifies the original upload record.
"""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.documents.models import DocumentType
from apps.evidenceinbox import services
from apps.evidenceinbox.models import EvidenceClassification, UnclassifiedEvidence
from apps.projects.models import Building, BuildingFamily, Project

pytestmark = pytest.mark.django_db


@pytest.fixture
def project(organization):
    return Project.objects.create(organization=organization, name="Arena", code="arena-inbox-test")


@pytest.fixture
def family(project):
    return BuildingFamily.objects.create(project=project, code="arena-t1-inbox-test", display_name="ARENA T1")


@pytest.fixture
def building(project, family):
    return Building.objects.create(project=project, code="b11-inbox-test", name="Edificio 11", family=family, building_number="11")


@pytest.fixture
def doc_type(organization):
    return DocumentType.objects.create(organization=organization, code="inbox-evidence-test", name="Evidencia de inbox")


def _upload(organization, user, doc_type, building=None, filename="foto_historica.jpg", content=b"fake-jpeg-bytes"):
    return services.upload_unclassified_evidence(
        user, organization, document_type=doc_type, title="Foto histórica Lawson",
        uploaded_file=SimpleUploadedFile(filename, content, content_type="image/jpeg"),
        building=building, notes="Foto encontrada en el teléfono de Lawson",
    )


class TestUpload:
    def test_upload_preserves_provenance_immediately(self, organization, harrison, doc_type):
        evidence, is_duplicate = _upload(organization, harrison, doc_type)
        assert not is_duplicate
        assert evidence.classification_status == UnclassifiedEvidence.ClassificationStatus.UNCLASSIFIED
        assert evidence.created_by == harrison
        assert evidence.created_at is not None
        version = evidence.document.versions.get()
        assert version.original_filename == "foto_historica.jpg"
        assert version.sha256
        assert version.uploaded_by == harrison

    def test_duplicate_content_detected(self, organization, harrison, doc_type):
        _upload(organization, harrison, doc_type, content=b"identical-bytes")
        _, is_duplicate = _upload(organization, harrison, doc_type, filename="otra_copia.jpg", content=b"identical-bytes")
        assert is_duplicate

    def test_upload_without_building_or_project_allowed(self, organization, harrison, doc_type):
        evidence, _ = _upload(organization, harrison, doc_type, building=None)
        assert evidence.building is None
        assert evidence.classification_status == UnclassifiedEvidence.ClassificationStatus.UNCLASSIFIED


class TestClassification:
    def test_classify_to_building_marks_partial(self, organization, harrison, doc_type, building):
        evidence, _ = _upload(organization, harrison, doc_type)
        classification = services.classify_evidence(evidence, harrison, target=building)
        evidence.refresh_from_db()
        assert evidence.classification_status == UnclassifiedEvidence.ClassificationStatus.PARTIALLY_CLASSIFIED
        assert classification.content_type == ContentType.objects.get_for_model(building)
        assert classification.object_id == building.pk
        assert classification.is_active

    def test_classify_to_leaf_target_marks_classified(self, organization, harrison, doc_type, building):
        from apps.projects.models import Floor, Unit

        floor = Floor.objects.create(building=building, name="Piso 1", level=1)
        unit = Unit.objects.create(floor=floor, building=building, name="Apto 3", apartment_number="3")
        evidence, _ = _upload(organization, harrison, doc_type)
        services.classify_evidence(evidence, harrison, target=unit)
        evidence.refresh_from_db()
        assert evidence.classification_status == UnclassifiedEvidence.ClassificationStatus.CLASSIFIED

    def test_classification_never_modifies_original_upload(self, organization, harrison, doc_type, building):
        evidence, _ = _upload(organization, harrison, doc_type)
        original_version = evidence.document.versions.get()
        services.classify_evidence(evidence, harrison, target=building)
        original_version.refresh_from_db()
        assert original_version.original_filename == "foto_historica.jpg"
        assert evidence.document.versions.count() == 1

    def test_batch_classify(self, organization, harrison, doc_type, building):
        evidence1, _ = _upload(organization, harrison, doc_type, filename="a.jpg")
        evidence2, _ = _upload(organization, harrison, doc_type, filename="b.jpg")
        queryset = UnclassifiedEvidence.objects.filter(pk__in=[evidence1.pk, evidence2.pk])
        results = services.batch_classify(queryset, harrison, target=building, reason="Lote de fotos de Lawson")
        assert len(results) == 2
        evidence1.refresh_from_db()
        evidence2.refresh_from_db()
        assert evidence1.classification_status == UnclassifiedEvidence.ClassificationStatus.PARTIALLY_CLASSIFIED
        assert evidence2.classification_status == UnclassifiedEvidence.ClassificationStatus.PARTIALLY_CLASSIFIED


class TestReclassification:
    def test_reclassify_requires_reason(self, organization, harrison, doc_type, building):
        from apps.projects.models import Floor, Unit

        floor = Floor.objects.create(building=building, name="Piso 1", level=1)
        unit = Unit.objects.create(floor=floor, building=building, name="Apto 4", apartment_number="4")
        evidence, _ = _upload(organization, harrison, doc_type)
        classification = services.classify_evidence(evidence, harrison, target=building)
        with pytest.raises(services.EvidenceInboxError):
            services.reclassify_evidence(classification, harrison, target=unit, reason="")

    def test_reclassify_preserves_history(self, organization, harrison, doc_type, building):
        from apps.projects.models import Floor, Unit

        floor = Floor.objects.create(building=building, name="Piso 1", level=1)
        unit = Unit.objects.create(floor=floor, building=building, name="Apto 5", apartment_number="5")
        evidence, _ = _upload(organization, harrison, doc_type)
        old_classification = services.classify_evidence(evidence, harrison, target=building)
        new_classification = services.reclassify_evidence(
            old_classification, harrison, target=unit, reason="Se identificó el apartamento correcto",
        )
        old_classification.refresh_from_db()
        assert old_classification.is_active is False
        assert old_classification.superseded_by == new_classification
        assert new_classification.is_active
        assert EvidenceClassification.objects.filter(evidence=evidence).count() == 2

    def test_cannot_reclassify_already_superseded(self, organization, harrison, doc_type, building):
        from apps.projects.models import Floor, Unit

        floor = Floor.objects.create(building=building, name="Piso 1", level=1)
        unit = Unit.objects.create(floor=floor, building=building, name="Apto 6", apartment_number="6")
        evidence, _ = _upload(organization, harrison, doc_type)
        old_classification = services.classify_evidence(evidence, harrison, target=building)
        services.reclassify_evidence(old_classification, harrison, target=unit, reason="Motivo 1")
        old_classification.refresh_from_db()
        with pytest.raises(services.EvidenceInboxError):
            services.reclassify_evidence(old_classification, harrison, target=unit, reason="Motivo 2")


class TestOrganizationIsolation:
    def test_evidence_scoped_to_organization(self, organization, harrison, doc_type, other_org_user):
        evidence, _ = _upload(organization, harrison, doc_type)
        assert not UnclassifiedEvidence.objects.filter(pk=evidence.pk, organization=other_org_user.profile.organization).exists()

    def test_cannot_view_other_org_evidence_via_http(self, client, organization, harrison, doc_type, other_org_user):
        evidence, _ = _upload(organization, harrison, doc_type)
        client.force_login(other_org_user)
        response = client.get(reverse("evidenceinbox:detail", args=[evidence.pk]))
        assert response.status_code == 404

    def test_cannot_classify_evidence_to_other_organizations_building(self, client, organization, harrison, doc_type, other_org_user):
        from apps.projects.models import Project as ProjectModel

        other_project = ProjectModel.objects.create(organization=other_org_user.profile.organization, name="Other", code="other-proj-inbox")
        other_family = BuildingFamily.objects.create(project=other_project, code="other-family-inbox", display_name="OTHER")
        other_building = Building.objects.create(project=other_project, code="other-b-inbox", name="Otro Edificio", family=other_family, building_number="1")

        evidence, _ = _upload(organization, harrison, doc_type)
        client.force_login(harrison)
        response = client.post(reverse("evidenceinbox:classify", args=[evidence.pk]), {
            "app_label": "projects", "model": "building", "object_id": str(other_building.pk),
        })
        assert response.status_code == 302
        evidence.refresh_from_db()
        assert evidence.classification_status == UnclassifiedEvidence.ClassificationStatus.UNCLASSIFIED
        assert not EvidenceClassification.objects.filter(evidence=evidence).exists()


class TestHTTPWorkflow:
    def test_full_http_upload_classify_reclassify_workflow(self, client, organization, harrison, doc_type, building):
        from apps.projects.models import Floor, Unit

        floor = Floor.objects.create(building=building, name="Piso 1", level=1)
        unit = Unit.objects.create(floor=floor, building=building, name="Apto 7", apartment_number="7")

        client.force_login(harrison)
        response = client.post(reverse("evidenceinbox:upload"), {
            "document_type": doc_type.pk, "title": "Foto histórica HTTP", "file": SimpleUploadedFile("foto_http.jpg", b"http-bytes", content_type="image/jpeg"),
            "building": building.pk,
        })
        assert response.status_code == 302
        evidence = UnclassifiedEvidence.objects.get(document__title="Foto histórica HTTP")
        assert evidence.classification_status == UnclassifiedEvidence.ClassificationStatus.UNCLASSIFIED

        response = client.post(reverse("evidenceinbox:classify", args=[evidence.pk]), {
            "app_label": "projects", "model": "unit", "object_id": str(unit.pk), "reason": "Identificado como Apto 7",
        })
        assert response.status_code == 302
        evidence.refresh_from_db()
        assert evidence.classification_status == UnclassifiedEvidence.ClassificationStatus.CLASSIFIED
        classification = evidence.classifications.get(is_active=True)

        other_unit = Unit.objects.create(floor=floor, building=building, name="Apto 8", apartment_number="8")
        response = client.post(reverse("evidenceinbox:reclassify", args=[classification.pk]), {
            "app_label": "projects", "model": "unit", "object_id": str(other_unit.pk), "reason": "Corrección: era el Apto 8",
        })
        assert response.status_code == 302
        classification.refresh_from_db()
        assert classification.is_active is False
        assert classification.superseded_by is not None

        response = client.get(reverse("evidenceinbox:detail", args=[evidence.pk]))
        assert response.status_code == 200
        assert "Foto histórica HTTP" in response.content.decode()

        response = client.get(reverse("evidenceinbox:list"))
        assert response.status_code == 200
