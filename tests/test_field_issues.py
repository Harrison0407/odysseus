"""Tests for field issue reporting and corrective-action tracking
(one-shot release, section 4). The worker never automatically gains
closure authority; a corrective issue cannot be verified and closed
without before/after evidence, a corrective description, a responsible
party, and a completion timestamp.
"""

from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.documents.models import DocumentType
from apps.fieldissues import services
from apps.fieldissues.models import FieldIssue, FieldIssueEvidence
from apps.projects.models import Building, BuildingFamily, Project

pytestmark = pytest.mark.django_db


@pytest.fixture
def project(organization):
    return Project.objects.create(organization=organization, name="Arena", code="arena-issue-test")


@pytest.fixture
def family(project):
    return BuildingFamily.objects.create(project=project, code="arena-t1-issue-test", display_name="ARENA T1")


@pytest.fixture
def building(project, family):
    return Building.objects.create(project=project, code="b11-issue-test", name="Edificio 11", family=family, building_number="11")


@pytest.fixture
def doc_type(organization):
    return DocumentType.objects.create(organization=organization, code="field-issue-photo-test", name="Foto de incidencia")


def _attach(issue, user, doc_type, stage, filename="foto.jpg"):
    return services.add_evidence(
        issue, user, document_type=doc_type, title=f"Evidencia {stage}",
        uploaded_file=SimpleUploadedFile(filename, b"fake-jpeg-bytes", content_type="image/jpeg"), stage=stage,
    )


class TestReportIssue:
    def test_mobile_field_issue_creation_requires_only_building(self, building, miguel):
        issue = services.report_issue(building, miguel, title="Ventana rayada")
        assert issue.building == building
        assert issue.floor is None
        assert issue.unit is None
        assert issue.status == FieldIssue.Status.REPORTED

    def test_duplicate_click_returns_existing_issue(self, building, miguel):
        first = services.report_issue(building, miguel, title="Puerta desalineada")
        second = services.report_issue(building, miguel, title="Puerta desalineada")
        assert first.id == second.id
        assert FieldIssue.objects.filter(building=building, title="Puerta desalineada").count() == 1

    def test_blank_title_rejected(self, building, miguel):
        with pytest.raises(services.FieldIssueError):
            services.report_issue(building, miguel, title="   ")


class TestEvidenceProvenance:
    def test_photo_evidence_creates_real_document_with_provenance(self, building, miguel, doc_type):
        issue = services.report_issue(building, miguel, title="Vidrio roto")
        evidence, is_duplicate = _attach(issue, miguel, doc_type, FieldIssueEvidence.Stage.BEFORE)
        assert not is_duplicate
        assert evidence.document.versions.first().sha256
        assert evidence.stage == FieldIssueEvidence.Stage.BEFORE

    def test_duplicate_photo_content_detected(self, building, miguel, doc_type):
        issue = services.report_issue(building, miguel, title="Vidrio roto 2")
        _attach(issue, miguel, doc_type, FieldIssueEvidence.Stage.BEFORE, filename="a.jpg")
        _, is_duplicate = _attach(issue, miguel, doc_type, FieldIssueEvidence.Stage.BEFORE, filename="a.jpg")
        assert is_duplicate


class TestAssignment:
    def test_assign_to_user(self, building, miguel, harrison):
        issue = services.report_issue(building, miguel, title="Falta sellado")
        services.assign_issue(issue, miguel, responsible_user=harrison)
        issue.refresh_from_db()
        assert issue.responsible_user == harrison
        assert issue.status == FieldIssue.Status.ASSIGNED

    def test_assign_requires_a_target(self, building, miguel):
        issue = services.report_issue(building, miguel, title="Sin responsable")
        with pytest.raises(services.FieldIssueError):
            services.assign_issue(issue, miguel)

    def test_reassignment_updates_responsible_party(self, building, miguel, harrison, manuel):
        issue = services.report_issue(building, miguel, title="Reasignar")
        services.assign_issue(issue, miguel, responsible_user=harrison)
        services.assign_issue(issue, miguel, responsible_user=manuel)
        issue.refresh_from_db()
        assert issue.responsible_user == manuel


class TestCorrectionAndClosure:
    def test_correction_blocked_without_before_evidence_or_waiver(self, building, miguel, harrison):
        issue = services.report_issue(building, miguel, title="Ajuste de ventana")
        services.assign_issue(issue, miguel, responsible_user=harrison)
        services.start_progress(issue, harrison)
        with pytest.raises(services.FieldIssueError):
            services.record_correction(issue, harrison, description="Se ajustó el marco.")

    def test_correction_allowed_with_waiver_reason(self, building, miguel, harrison):
        issue = services.report_issue(building, miguel, title="Ajuste de ventana 2")
        services.assign_issue(issue, miguel, responsible_user=harrison)
        services.start_progress(issue, harrison)
        services.record_correction(
            issue, harrison, description="Se ajustó el marco.",
            before_evidence_waived_reason="No se tomó foto antes por urgencia — autorizado por Miguel.",
        )
        issue.refresh_from_db()
        assert issue.status == FieldIssue.Status.CORRECTION_COMPLETED

    def test_closure_blocked_without_after_evidence(self, building, miguel, harrison, doc_type):
        issue = services.report_issue(building, miguel, title="Cerrar sin evidencia")
        services.assign_issue(issue, miguel, responsible_user=harrison)
        services.start_progress(issue, harrison)
        _attach(issue, harrison, doc_type, FieldIssueEvidence.Stage.BEFORE)
        services.record_correction(issue, harrison, description="Corrección hecha.")
        services.mark_ready_for_verification(issue, harrison)
        with pytest.raises(services.FieldIssueError):
            services.verify_and_close_issue(issue, harrison)

    def test_unauthorized_user_cannot_verify_and_close(self, building, miguel, manuel, doc_type):
        issue = services.report_issue(building, miguel, title="Cerrar sin permiso")
        services.assign_issue(issue, miguel, responsible_user=manuel)
        services.start_progress(issue, manuel)
        _attach(issue, manuel, doc_type, FieldIssueEvidence.Stage.BEFORE)
        services.record_correction(issue, manuel, description="Corrección hecha.")
        _attach(issue, manuel, doc_type, FieldIssueEvidence.Stage.AFTER)
        services.mark_ready_for_verification(issue, manuel)
        with pytest.raises(services.FieldIssueError):
            services.verify_and_close_issue(issue, manuel)  # manuel performed the work AND lacks verify permission

    def test_full_closure_with_all_evidence_by_authorized_verifier(self, building, miguel, manuel, harrison, doc_type):
        issue = services.report_issue(building, miguel, title="Cierre completo")
        services.assign_issue(issue, miguel, responsible_user=manuel)
        services.start_progress(issue, manuel)
        _attach(issue, manuel, doc_type, FieldIssueEvidence.Stage.BEFORE)
        services.record_correction(issue, manuel, description="Corrección hecha.")
        _attach(issue, manuel, doc_type, FieldIssueEvidence.Stage.AFTER)
        services.mark_ready_for_verification(issue, manuel)
        services.verify_and_close_issue(issue, harrison, notes="Verificado en sitio.")
        issue.refresh_from_db()
        assert issue.status == FieldIssue.Status.VERIFIED_CLOSED
        assert issue.verified_by == harrison


class TestRejectionAndResubmission:
    def test_rejection_preserves_evidence_and_history(self, building, miguel, manuel, harrison, doc_type):
        issue = services.report_issue(building, miguel, title="Rechazo de prueba")
        services.assign_issue(issue, miguel, responsible_user=manuel)
        services.start_progress(issue, manuel)
        _attach(issue, manuel, doc_type, FieldIssueEvidence.Stage.BEFORE)
        services.record_correction(issue, manuel, description="Primer intento.")
        _attach(issue, manuel, doc_type, FieldIssueEvidence.Stage.AFTER, filename="after1.jpg")
        services.mark_ready_for_verification(issue, manuel)
        services.reject_correction(issue, harrison, reason="Ajuste insuficiente.")
        issue.refresh_from_db()
        assert issue.status == FieldIssue.Status.RETURNED_FOR_CORRECTION
        assert issue.evidence.filter(stage=FieldIssueEvidence.Stage.AFTER).count() == 1  # not erased

    def test_resubmission_adds_new_evidence_without_erasing_old(self, building, miguel, manuel, harrison, doc_type):
        issue = services.report_issue(building, miguel, title="Reenvío de prueba")
        services.assign_issue(issue, miguel, responsible_user=manuel)
        services.start_progress(issue, manuel)
        _attach(issue, manuel, doc_type, FieldIssueEvidence.Stage.BEFORE)
        services.record_correction(issue, manuel, description="Primer intento.")
        _attach(issue, manuel, doc_type, FieldIssueEvidence.Stage.AFTER, filename="after1.jpg")
        services.mark_ready_for_verification(issue, manuel)
        services.reject_correction(issue, harrison, reason="Ajuste insuficiente.")
        services.resubmit_correction(issue, manuel, description="Segundo intento, corregido.")
        _attach(issue, manuel, doc_type, FieldIssueEvidence.Stage.AFTER, filename="after2.jpg")
        issue.refresh_from_db()
        assert issue.status == FieldIssue.Status.RESUBMITTED
        assert issue.evidence.filter(stage=FieldIssueEvidence.Stage.AFTER).count() == 2

    def test_independent_verification_after_reinspection(self, building, miguel, manuel, harrison, doc_type):
        issue = services.report_issue(building, miguel, title="Reinspección completa")
        services.assign_issue(issue, miguel, responsible_user=manuel)
        services.start_progress(issue, manuel)
        _attach(issue, manuel, doc_type, FieldIssueEvidence.Stage.BEFORE)
        services.record_correction(issue, manuel, description="Primer intento.")
        _attach(issue, manuel, doc_type, FieldIssueEvidence.Stage.AFTER, filename="after1.jpg")
        services.mark_ready_for_verification(issue, manuel)
        services.reject_correction(issue, harrison, reason="Ajuste insuficiente.")
        services.resubmit_correction(issue, manuel, description="Segundo intento, corregido.")
        services.mark_for_reinspection(issue, harrison)
        services.verify_and_close_issue(issue, harrison, notes="Ahora sí, correcto.")
        issue.refresh_from_db()
        assert issue.status == FieldIssue.Status.VERIFIED_CLOSED


class TestIsolationAndHTTP:
    def test_cross_organization_issue_access_denied(self, client, building, miguel):
        from django.contrib.auth import get_user_model

        from apps.accounts.models import Organization, UserProfile

        issue = services.report_issue(building, miguel, title="Aislamiento")
        User = get_user_model()
        other_org = Organization.objects.create(name="Other Org Issue Test")
        outsider = User.objects.create_user(username="outsider_issue", password="testpass123")
        UserProfile.objects.create(user=outsider, organization=other_org)

        client.force_login(outsider)
        response = client.get(reverse("fieldissues:detail", args=[issue.pk]))
        assert response.status_code == 404

    def test_user_without_project_access_denied(self, client, building, miguel):
        issue = services.report_issue(building, miguel, title="Sin acceso a proyecto")
        client.force_login(miguel)
        response = client.get(reverse("fieldissues:detail", args=[issue.pk]))
        assert response.status_code == 404

    def test_full_http_report_and_list_workflow(self, client, building, harrison):
        client.force_login(harrison)
        response = client.post(reverse("fieldissues:report"), {
            "building": building.pk, "title": "Reporte vía HTTP", "priority": "high",
        })
        assert response.status_code == 302
        assert FieldIssue.objects.filter(building=building, title="Reporte vía HTTP").exists()

        response = client.get(reverse("fieldissues:list"))
        assert response.status_code == 200
        assert "Reporte vía HTTP" in response.content.decode()

    def test_http_duplicate_submission_protection(self, client, building, harrison):
        client.force_login(harrison)
        client.post(reverse("fieldissues:report"), {"building": building.pk, "title": "Doble clic", "priority": "medium"})
        client.post(reverse("fieldissues:report"), {"building": building.pk, "title": "Doble clic", "priority": "medium"})
        assert FieldIssue.objects.filter(building=building, title="Doble clic").count() == 1
