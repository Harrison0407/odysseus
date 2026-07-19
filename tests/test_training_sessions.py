"""Tests for Lawson training and reference-installation sessions
(one-shot release, section 5). Lawson is a configured user like any
other trainer/participant — never hard-coded into business logic;
these tests use `harrison`/`manuel`/`miguel` fixtures precisely to
prove no business logic depends on a specific name.
"""

from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.documents.models import DocumentType
from apps.fieldissues.models import FieldIssue
from apps.projects.models import Building, BuildingFamily, Project
from apps.training import services
from apps.training.models import TrainingCategory, TrainingEvidence, TrainingSession

pytestmark = pytest.mark.django_db


@pytest.fixture
def project(organization):
    return Project.objects.create(organization=organization, name="Arena", code="arena-training-test")


@pytest.fixture
def family(project):
    return BuildingFamily.objects.create(project=project, code="arena-t1-training-test", display_name="ARENA T1")


@pytest.fixture
def building_11(project, family):
    return Building.objects.create(project=project, code="b11-training-test", name="Edificio 11", family=family, building_number="11")


@pytest.fixture
def category(organization):
    return TrainingCategory.objects.create(organization=organization, code="aluminum-windows-test", name="Ventanas de aluminio")


@pytest.fixture
def doc_type(organization):
    return DocumentType.objects.create(organization=organization, code="training-evidence-test", name="Evidencia de capacitación")


def _attach(session, user, doc_type, stage, filename="foto.jpg"):
    return services.add_evidence(
        session, user, document_type=doc_type, title=f"Evidencia {stage}",
        uploaded_file=SimpleUploadedFile(filename, b"fake-jpeg-bytes", content_type="image/jpeg"), stage=stage,
    )


class TestCreateSession:
    def test_configured_trainer_not_hard_coded(self, building_11, category, harrison, manuel, miguel):
        """Any user can be the trainer/participants — nothing in the
        service layer references a specific name."""
        session = services.create_training_session(
            building_11, harrison, trainer=manuel, participants=[manuel, miguel], category=category,
        )
        assert session.trainer == manuel
        assert set(session.participants.all()) == {manuel, miguel}

    def test_session_created_with_two_participants(self, building_11, harrison, manuel, miguel):
        session = services.create_training_session(building_11, harrison, trainer=harrison, participants=[manuel, miguel])
        assert session.participants.count() == 2


class TestSessionLifecycle:
    def test_start_then_finish(self, building_11, harrison):
        session = services.create_training_session(building_11, harrison)
        services.start_session(session, harrison)
        session.refresh_from_db()
        assert session.actual_start is not None
        services.finish_session(session, harrison, digital_level_reading="0.2mm/m", used_digital_level=True)
        session.refresh_from_db()
        assert session.actual_finish is not None
        assert session.used_digital_level

    def test_cannot_finish_before_starting(self, building_11, harrison):
        session = services.create_training_session(building_11, harrison)
        with pytest.raises(services.TrainingError):
            services.finish_session(session, harrison)

    def test_cannot_start_twice(self, building_11, harrison):
        session = services.create_training_session(building_11, harrison)
        services.start_session(session, harrison)
        with pytest.raises(services.TrainingError):
            services.start_session(session, harrison)


class TestChecklistAndEvidence:
    def test_checklist_item_recorded(self, building_11, harrison):
        session = services.create_training_session(building_11, harrison)
        item = services.add_checklist_item(session, harrison, description="Nivel de marco", result="pass")
        assert item.result == "pass"

    def test_before_during_after_evidence(self, building_11, harrison, doc_type):
        session = services.create_training_session(building_11, harrison)
        _attach(session, harrison, doc_type, TrainingEvidence.Stage.BEFORE, "b.jpg")
        _attach(session, harrison, doc_type, TrainingEvidence.Stage.DURING, "d.jpg")
        _attach(session, harrison, doc_type, TrainingEvidence.Stage.AFTER, "a.jpg")
        assert session.evidence.count() == 3


class TestAcknowledgementAndSignOff:
    def test_only_a_real_participant_can_acknowledge(self, building_11, harrison, manuel, miguel):
        session = services.create_training_session(building_11, harrison, participants=[manuel])
        with pytest.raises(services.TrainingError):
            services.acknowledge_participation(session, miguel)

    def test_participant_can_acknowledge(self, building_11, harrison, manuel):
        session = services.create_training_session(building_11, harrison, participants=[manuel])
        ack = services.acknowledge_participation(session, manuel)
        assert ack.participant == manuel

    def test_supervisor_sign_off_requires_permission(self, building_11, harrison, manuel):
        session = services.create_training_session(building_11, harrison)
        services.start_session(session, harrison)
        services.finish_session(session, harrison)
        with pytest.raises(services.TrainingError):
            services.supervisor_sign_off(session, manuel)

    def test_supervisor_sign_off_requires_finished_session(self, building_11, harrison):
        session = services.create_training_session(building_11, harrison)
        with pytest.raises(services.TrainingError):
            services.supervisor_sign_off(session, harrison)

    def test_authorized_supervisor_can_sign_off(self, building_11, harrison):
        session = services.create_training_session(building_11, harrison)
        services.start_session(session, harrison)
        services.finish_session(session, harrison)
        services.supervisor_sign_off(session, harrison, notes="Todo correcto.")
        session.refresh_from_db()
        assert session.supervisor == harrison


class TestReferenceInstallationApproval:
    def test_cannot_approve_reference_without_sign_off(self, building_11, harrison):
        session = services.create_training_session(building_11, harrison)
        with pytest.raises(services.TrainingError):
            services.approve_as_reference_installation(session, harrison)

    def test_unauthorized_user_cannot_approve_reference(self, building_11, harrison, manuel):
        session = services.create_training_session(building_11, harrison)
        services.start_session(session, harrison)
        services.finish_session(session, harrison)
        services.supervisor_sign_off(session, harrison)
        with pytest.raises(services.TrainingError):
            services.approve_as_reference_installation(session, manuel)

    def test_approved_reference_installation_visible_in_reference_list(self, client, building_11, harrison):
        session = services.create_training_session(building_11, harrison)
        services.start_session(session, harrison)
        services.finish_session(session, harrison)
        services.supervisor_sign_off(session, harrison)
        services.approve_as_reference_installation(session, harrison)
        session.refresh_from_db()
        assert session.is_approved_reference_installation

        client.force_login(harrison)
        response = client.get(reverse("training:list"), {"reference": "1"})
        assert response.status_code == 200
        assert TrainingSession.objects.filter(pk=session.pk, is_approved_reference_installation=True).exists()


class TestIssueFromTraining:
    def test_create_issue_preserves_relationship(self, building_11, harrison):
        session = services.create_training_session(building_11, harrison)
        issue = services.create_issue_from_training(session, harrison, title="Vidrio rayado durante capacitación")
        assert issue.training_session_id == session.id
        assert issue.building_id == building_11.id
        assert isinstance(issue, FieldIssue)


class TestIsolationAndHTTP:
    def test_cross_organization_session_access_denied(self, client, building_11, harrison):
        from django.contrib.auth import get_user_model

        from apps.accounts.models import Organization, UserProfile

        session = services.create_training_session(building_11, harrison)
        User = get_user_model()
        other_org = Organization.objects.create(name="Other Org Training Test")
        outsider = User.objects.create_user(username="outsider_training", password="testpass123")
        UserProfile.objects.create(user=outsider, organization=other_org)

        client.force_login(outsider)
        response = client.get(reverse("training:detail", args=[session.pk]))
        assert response.status_code == 404

    def test_full_http_workflow(self, client, building_11, harrison, manuel, miguel, doc_type):
        client.force_login(harrison)
        response = client.post(reverse("training:create"), {
            "building": building_11.pk, "trainer": harrison.pk, "participants": [manuel.pk, miguel.pk],
        })
        assert response.status_code == 302
        session = TrainingSession.objects.get(building=building_11)

        client.post(reverse("training:start", args=[session.pk]))
        client.post(reverse("training:finish", args=[session.pk]), {"digital_level_reading": "0.1mm/m"})
        client.post(reverse("training:supervisor-sign-off", args=[session.pk]), {"notes": "OK"})
        response = client.post(reverse("training:approve-reference", args=[session.pk]))
        assert response.status_code == 302
        session.refresh_from_db()
        assert session.is_approved_reference_installation
