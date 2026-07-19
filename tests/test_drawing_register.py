"""Tests for the drawing and floor-plan register (one-shot release,
section 2). A new revision is always a new Drawing row — the prior one
is marked SUPERSEDED but never edited, so any historical FK elsewhere
keeps pointing at the exact revision it originally referenced.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.documents.models import Document, DocumentType, DocumentVersion
from apps.drawings import services
from apps.drawings.models import Drawing
from apps.projects.models import Building, BuildingFamily, Project

pytestmark = pytest.mark.django_db


@pytest.fixture
def project(organization):
    return Project.objects.create(organization=organization, name="Arena", code="arena-drawing-test")


@pytest.fixture
def family(project):
    return BuildingFamily.objects.create(project=project, code="arena-t1-drawing-test", display_name="ARENA T1")


@pytest.fixture
def building(project, family):
    return Building.objects.create(project=project, code="arena-t1-b11-drawing-test", name="Edificio 11", family=family, building_number="11")


@pytest.fixture
def doc_type(organization):
    return DocumentType.objects.create(organization=organization, code="architectural-drawing-test", name="Plano")


def _make_document(organization, doc_type, user, title="Plano de prueba"):
    document = Document.objects.create(organization=organization, document_type=doc_type, title=title, created_by=user)
    DocumentVersion.objects.create(
        document=document, version_number=1, stored_name="x.pdf", original_filename="x.pdf",
        sha256="0" * 64, size_bytes=100, uploaded_by=user, created_by=user,
    )
    return document


class TestRegisterDrawing:
    def test_register_creates_drawing(self, organization, doc_type, project, building, harrison):
        document = _make_document(organization, doc_type, harrison)
        drawing = services.register_drawing(
            document, harrison, project=project, drawing_type=Drawing.DrawingType.BUILDING_PLAN,
            title="Plano de edificio 11", building=building,
        )
        assert drawing.revision == 1
        assert drawing.is_current
        assert drawing.status == Drawing.Status.DRAFT


class TestApproveDrawing:
    def test_approve_requires_permission(self, organization, doc_type, project, building, manuel):
        document = _make_document(organization, doc_type, manuel)
        drawing = services.register_drawing(document, manuel, project=project, drawing_type=Drawing.DrawingType.BUILDING_PLAN, title="X", building=building)
        with pytest.raises(services.DrawingError):
            services.approve_drawing(drawing, manuel)

    def test_approve_succeeds_for_authorized_user(self, organization, doc_type, project, building, harrison):
        document = _make_document(organization, doc_type, harrison)
        drawing = services.register_drawing(document, harrison, project=project, drawing_type=Drawing.DrawingType.BUILDING_PLAN, title="X", building=building)
        services.approve_drawing(drawing, harrison)
        drawing.refresh_from_db()
        assert drawing.status == Drawing.Status.APPROVED
        assert drawing.approver == harrison

    def test_cannot_approve_superseded_drawing(self, organization, doc_type, project, building, harrison):
        document = _make_document(organization, doc_type, harrison)
        drawing = services.register_drawing(document, harrison, project=project, drawing_type=Drawing.DrawingType.BUILDING_PLAN, title="X", building=building)
        new_document = _make_document(organization, doc_type, harrison, title="Revisión 2")
        services.supersede_drawing(drawing, harrison, new_source_document=new_document)
        drawing.refresh_from_db()
        with pytest.raises(services.DrawingError):
            services.approve_drawing(drawing, harrison)


class TestSupersedeDrawing:
    def test_supersede_creates_new_revision_and_preserves_old(self, organization, doc_type, project, building, harrison):
        document = _make_document(organization, doc_type, harrison)
        original = services.register_drawing(document, harrison, project=project, drawing_type=Drawing.DrawingType.BUILDING_PLAN, title="X", building=building)
        original_id = original.id
        new_document = _make_document(organization, doc_type, harrison, title="Revisión 2")
        new_drawing = services.supersede_drawing(original, harrison, new_source_document=new_document)

        original.refresh_from_db()
        assert original.status == Drawing.Status.SUPERSEDED
        assert not original.is_current
        assert new_drawing.revision == 2
        assert new_drawing.supersedes_id == original_id
        # the old row's own source_document FK is untouched — never overwritten
        assert original.source_document_id == document.id

    def test_cannot_supersede_an_already_superseded_drawing(self, organization, doc_type, project, building, harrison):
        document = _make_document(organization, doc_type, harrison)
        original = services.register_drawing(document, harrison, project=project, drawing_type=Drawing.DrawingType.BUILDING_PLAN, title="X", building=building)
        doc2 = _make_document(organization, doc_type, harrison, title="Rev 2")
        services.supersede_drawing(original, harrison, new_source_document=doc2)
        original.refresh_from_db()
        doc3 = _make_document(organization, doc_type, harrison, title="Rev 3 attempt")
        with pytest.raises(services.DrawingError):
            services.supersede_drawing(original, harrison, new_source_document=doc3)

    def test_historical_reference_to_old_drawing_is_never_silently_replaced(self, organization, doc_type, project, building, harrison):
        """An order/installation/walkthrough/issue FK pointing at a
        specific Drawing must keep pointing at that exact row."""
        document = _make_document(organization, doc_type, harrison)
        original = services.register_drawing(document, harrison, project=project, drawing_type=Drawing.DrawingType.BUILDING_PLAN, title="X", building=building)
        referenced_pk = original.pk
        new_document = _make_document(organization, doc_type, harrison, title="Rev 2")
        services.supersede_drawing(original, harrison, new_source_document=new_document)
        # simulate an external FK (e.g. an order allocation) holding onto the original pk
        still_there = Drawing.objects.get(pk=referenced_pk)
        assert still_there.source_document_id == document.id
        assert still_there.revision == 1


class TestDrawingHTTP:
    def test_drawing_list_and_detail(self, client, organization, doc_type, project, building, harrison):
        document = _make_document(organization, doc_type, harrison)
        drawing = services.register_drawing(document, harrison, project=project, drawing_type=Drawing.DrawingType.BUILDING_PLAN, title="Plano HTTP", building=building)
        client.force_login(harrison)
        response = client.get(reverse("drawings:list"))
        assert response.status_code == 200
        response = client.get(reverse("drawings:detail", args=[drawing.pk]))
        assert response.status_code == 200
        assert "Plano HTTP" in response.content.decode()

    def test_supersede_via_http_creates_new_revision(self, client, organization, doc_type, project, building, harrison):
        document = _make_document(organization, doc_type, harrison)
        drawing = services.register_drawing(document, harrison, project=project, drawing_type=Drawing.DrawingType.BUILDING_PLAN, title="Plano HTTP2", building=building)
        client.force_login(harrison)
        response = client.post(reverse("drawings:supersede", args=[drawing.pk]), {
            "document_type": doc_type.pk, "title": "Plano HTTP2 rev2",
            "file": SimpleUploadedFile("plano2.pdf", b"fake-pdf-bytes", content_type="application/pdf"),
            "notes": "Actualización de prueba",
        })
        assert response.status_code == 302
        drawing.refresh_from_db()
        assert drawing.status == Drawing.Status.SUPERSEDED
        assert Drawing.objects.filter(supersedes=drawing, revision=2).exists()

    def test_cross_organization_drawing_access_denied(self, client, organization, doc_type, project, building, harrison):
        from django.contrib.auth import get_user_model

        from apps.accounts.models import Organization, UserProfile

        document = _make_document(organization, doc_type, harrison)
        drawing = services.register_drawing(document, harrison, project=project, drawing_type=Drawing.DrawingType.BUILDING_PLAN, title="X", building=building)

        User = get_user_model()
        other_org = Organization.objects.create(name="Other Org Drawing Test")
        outsider = User.objects.create_user(username="outsider_drawing", password="testpass123")
        UserProfile.objects.create(user=outsider, organization=other_org)

        client.force_login(outsider)
        response = client.get(reverse("drawings:detail", args=[drawing.pk]))
        assert response.status_code == 404

    def test_user_without_project_access_denied(self, client, organization, doc_type, project, building, miguel):
        document = _make_document(organization, doc_type, miguel)
        drawing = services.register_drawing(document, miguel, project=project, drawing_type=Drawing.DrawingType.BUILDING_PLAN, title="X", building=building)
        client.force_login(miguel)
        response = client.get(reverse("drawings:detail", args=[drawing.pk]))
        assert response.status_code == 404

    def test_building_detail_shows_linked_drawing(self, client, organization, doc_type, project, building, harrison):
        document = _make_document(organization, doc_type, harrison)
        services.register_drawing(document, harrison, project=project, drawing_type=Drawing.DrawingType.BUILDING_PLAN, title="Plano vinculado", building=building)
        client.force_login(harrison)
        response = client.get(reverse("projects:building-detail", args=[building.pk]))
        assert response.status_code == 200
        assert "Plano vinculado" in response.content.decode()
