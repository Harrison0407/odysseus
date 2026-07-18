import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.documents.models import Document, DocumentType, DocumentVersion


@pytest.fixture
def doc_type(organization):
    return DocumentType.objects.create(organization=organization, code="packing_list", name="Lista de Empaque")


@pytest.mark.django_db
def test_upload_creates_document_with_sha256(client, manuel, doc_type):
    client.force_login(manuel)
    f = SimpleUploadedFile("packing.csv", b"item,qty\nquartz,20\n", content_type="text/csv")
    response = client.post(
        reverse("documents:upload"),
        {"document_type": doc_type.pk, "title": "Packing list test", "file": f},
    )
    assert response.status_code == 302
    document = Document.objects.get(title="Packing list test")
    version = document.current_version
    assert version.sha256 and len(version.sha256) == 64
    assert version.size_bytes == len(b"item,qty\nquartz,20\n")


@pytest.mark.django_db
def test_duplicate_upload_is_detected_by_sha256(client, manuel, doc_type):
    client.force_login(manuel)
    content = b"same,bytes\n"
    client.post(
        reverse("documents:upload"),
        {"document_type": doc_type.pk, "title": "First", "file": SimpleUploadedFile("a.csv", content)},
    )
    response = client.post(
        reverse("documents:upload"),
        {"document_type": doc_type.pk, "title": "Second (same content)", "file": SimpleUploadedFile("b.csv", content)},
    )
    assert response.status_code == 302
    second = Document.objects.get(title="Second (same content)")
    assert second.current_version.is_duplicate_of is not None
    assert second.current_version.is_duplicate_of.sha256 == second.current_version.sha256


@pytest.mark.django_db
def test_rejects_disallowed_file_extension(client, manuel, doc_type):
    client.force_login(manuel)
    response = client.post(
        reverse("documents:upload"),
        {"document_type": doc_type.pk, "title": "Bad file", "file": SimpleUploadedFile("virus.exe", b"MZ...")},
    )
    assert response.status_code == 200  # re-renders form with errors
    assert not Document.objects.filter(title="Bad file").exists()


@pytest.mark.django_db
def test_unauthorized_user_cannot_download_another_orgs_document(client, manuel, doc_type, other_org_user):
    """Spec section 32, acceptance scenario 32: unauthorized document
    download must be denied."""
    client.force_login(manuel)
    f = SimpleUploadedFile("secret.csv", b"secret,data\n")
    client.post(reverse("documents:upload"), {"document_type": doc_type.pk, "title": "Secret doc", "file": f})
    document = Document.objects.get(title="Secret doc")
    version = document.current_version

    client.force_login(other_org_user)
    response = client.get(reverse("documents:download", args=[document.pk, version.pk]))
    assert response.status_code == 404
