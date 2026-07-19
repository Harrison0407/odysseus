"""Confirms the Controlled Transparency / Confidentiality release did
not replace or merge any pre-existing configurable workflow template —
the DT Beach operational supply chain and the field-issue lifecycle
remain their own distinct state machines, and the new
ProcurementPackage/ChangeRequest layer is additive, not a universal
A1-A6 replacement.
"""

import pytest

from apps.fieldissues.models import FieldIssue
from apps.procurement.models import ProcurementPackage

pytestmark = pytest.mark.django_db


def test_dt_beach_supply_workflow_statuses_preserved():
    from apps.requests.models import InstallationRecord

    # The property-management supply chain's own field names/behavior
    # (installed_at, supervisor_confirmed_by, ...) are untouched by this
    # release — asserting the fields still exist confirms no merge/rename.
    field_names = {f.name for f in InstallationRecord._meta.get_fields()}
    assert {"installed_at", "supervisor_confirmed_by", "installer_acknowledged_at"}.issubset(field_names)


def test_field_issue_lifecycle_statuses_preserved():
    expected = {"reported", "assigned", "in_progress", "correction_completed", "ready_for_verification", "verified_closed"}
    actual = {choice for choice, _ in FieldIssue.Status.choices}
    assert expected.issubset(actual)


def test_procurement_package_status_is_its_own_distinct_template():
    # ProcurementPackage's own lifecycle (draft/active/frozen/closed) is
    # deliberately distinct from — and never replaces — the DT Beach
    # supply chain or field-issue templates above.
    package_statuses = {choice for choice, _ in ProcurementPackage.Status.choices}
    field_issue_statuses = {choice for choice, _ in FieldIssue.Status.choices}
    assert package_statuses.isdisjoint(field_issue_statuses)
