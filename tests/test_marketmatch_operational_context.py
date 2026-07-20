from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

import pytest

from src.marketmatch_authority import (
    AuthorityReason,
    AuthorityScope,
    AuthorizationRequest,
    DerivedArtifactKind,
    PartyKind,
    PartyReference,
    PrincipalContext,
    ResourceClassification,
    ResourceContext,
    ScopedCapabilityGrant,
    VisibilityMode,
    VisibilityPolicy,
    evaluate_authorization,
)
from src.marketmatch_evidence import (
    AcquisitionMethod,
    ContentIntegrityDescriptor,
    EvidenceKind,
    EvidenceRecord,
    MediaKind,
    ProvenanceContext,
    SourceKind,
)
from src.marketmatch_operational_context import (
    AliasNamespace,
    ContextAlias,
    ContextErrorCode,
    ContextRelationship,
    ContextRelationshipType,
    ContextType,
    EvidenceAssociation,
    EvidenceAssociationPurpose,
    HierarchyPolicy,
    MAX_ANCESTRY_DEPTH,
    MAX_CONTEXTS,
    OPERATIONAL_CONTEXT_CONTRACT_VERSION,
    OperationalContextError,
    OperationalContextRecord,
    build_safe_context_audit_summary,
    context_ancestry,
    context_metadata,
    inherit_context_visibility,
    linked_evidence_count,
    project_context_metadata,
    validate_context_graph,
    validate_context_id,
    validate_evidence_associations,
    validate_revision_transition,
)


NOW = datetime(2026, 7, 20, 18, 0, tzinfo=timezone.utc)
POLICY_ID = "marketmatch.context"
POLICY_VERSION = "v1"
VISIBLE_FIELDS = frozenset(
    {
        "contract_version",
        "context_id",
        "context_type",
        "parent_context_id",
        "created_at",
        "classification",
        "revision",
        "linked_evidence_count",
    }
)
ALL_FIELDS = VISIBLE_FIELDS | frozenset(
    {
        "display_name",
        "owner_party_ref",
        "alias_count",
        "visibility_policy_id",
        "visibility_policy_version",
        "valid_from",
        "valid_until",
        "previous_revision",
        "scope_organization_id",
        "scope_product_id",
        "scope_workspace_id",
        "scope_project_id",
        "scope_resource_id",
        "scope_owner_party_id",
        "scope_global",
    }
)


def assert_code(code: ContextErrorCode, call) -> None:
    with pytest.raises(OperationalContextError) as caught:
        call()
    assert caught.value.code == code.value
    assert str(caught.value) == code.value


def scope_for(
    context_id: str,
    context_type: ContextType = ContextType.PROJECT,
    *,
    organization: str = "org:alpha",
    product: str = "marketmatch",
    workspace: str | None = "workspace:alpha",
    project: str | None = "project:alpha",
    owner: str | None = "party:alpha",
) -> AuthorityScope:
    if context_type is ContextType.PRODUCT:
        workspace = None
        project = None
    elif context_type is ContextType.WORKSPACE:
        project = None
    return AuthorityScope(
        organization_id=organization,
        product_id=product,
        workspace_id=workspace,
        project_id=project,
        resource_id=context_id,
        owner_party_id=owner,
    )


def context_record(
    context_id: str = "ctx1:project:alpha",
    *,
    context_type: ContextType = ContextType.PROJECT,
    record_scope: AuthorityScope | None = None,
    parent: str | None = None,
    owner: str | None = "party:alpha",
    display_name: str | None = "Fictional Project Alpha",
    aliases: tuple[ContextAlias, ...] = (),
    created_at: datetime = NOW,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    revision: int = 1,
    previous_revision: int | None = None,
    classification: ResourceClassification = ResourceClassification.CONFIDENTIAL,
    policy_id: str = POLICY_ID,
    policy_version: str = POLICY_VERSION,
    contract_version: str = OPERATIONAL_CONTEXT_CONTRACT_VERSION,
) -> OperationalContextRecord:
    return OperationalContextRecord(
        context_id=context_id,
        context_type=context_type,
        scope=record_scope or scope_for(context_id, context_type, owner=owner),
        parent_context_id=parent,
        owner_party_ref=owner,
        created_at=created_at,
        valid_from=valid_from,
        valid_until=valid_until,
        display_name=display_name,
        aliases=aliases,
        classification=classification,
        visibility_policy_id=policy_id,
        visibility_policy_version=policy_version,
        revision=revision,
        previous_revision=previous_revision,
        contract_version=contract_version,
    )


def hierarchy_policy(*pairs: tuple[ContextType, ContextType]) -> HierarchyPolicy:
    return HierarchyPolicy("marketmatch.context.hierarchy", "v1", frozenset(pairs))


def context_decision(
    record: OperationalContextRecord,
    *,
    grant: bool = True,
    visible_fields: frozenset[str] = VISIBLE_FIELDS,
    locale: str = "es",
    display_timezone: str = "America/Santo_Domingo",
):
    principal = PrincipalContext(
        principal_ref="principal:reviewer",
        party=PartyReference("party:reviewer", PartyKind.PERSON),
        capability_grants=(
            ScopedCapabilityGrant("context.read", record.scope, "test:grant"),
        ) if grant else (),
    )
    policy = VisibilityPolicy(
        policy_id=record.visibility_policy_id,
        version=record.visibility_policy_version,
        mode=VisibilityMode.CONTROLLED_CONFIDENTIALITY,
        capabilities=frozenset({"context.read"}),
        visible_fields=visible_fields,
        allowed_classifications=frozenset(ResourceClassification),
    )
    return evaluate_authorization(
        AuthorizationRequest(
            principal=principal,
            capability_code="context.read",
            resource=ResourceContext(
                resource_type="operational_context",
                resource_id=record.context_id,
                scope=record.scope,
                classification=record.classification,
                available_fields=ALL_FIELDS,
            ),
            requested_fields=ALL_FIELDS,
            presentation_locale=locale,
            display_timezone=display_timezone,
        ),
        policy,
    )


def evidence_record(
    evidence_id: str = "ev1:document:alpha",
    *,
    context: OperationalContextRecord | None = None,
    organization: str = "org:alpha",
    project: str = "project:alpha",
    policy_id: str = POLICY_ID,
    policy_version: str = POLICY_VERSION,
) -> EvidenceRecord:
    item = context or context_record()
    evidence_scope = AuthorityScope(
        organization_id=organization,
        product_id=item.scope.product_id,
        workspace_id=item.scope.workspace_id,
        project_id=project,
        resource_id=evidence_id,
        owner_party_id=item.scope.owner_party_id,
    )
    return EvidenceRecord(
        evidence_id=evidence_id,
        evidence_kind=EvidenceKind.DOCUMENT,
        media_kind=MediaKind.TEXT,
        scope=evidence_scope,
        owner_party_ref=item.owner_party_ref,
        integrity=ContentIntegrityDescriptor.from_bytes(b"fictional evidence"),
        provenance=ProvenanceContext(
            source_kind=SourceKind.SYSTEM,
            acquisition_method=AcquisitionMethod.SYSTEM_OBSERVATION,
            ingestion_timestamp=NOW - timedelta(minutes=1),
        ),
        created_at=NOW,
        classification=item.classification,
        visibility_policy_id=policy_id,
        visibility_policy_version=policy_version,
    )


def evidence_decision(record: EvidenceRecord, *, grant: bool = False):
    principal = PrincipalContext(
        principal_ref="principal:reviewer",
        party=PartyReference("party:reviewer", PartyKind.PERSON),
        capability_grants=(
            ScopedCapabilityGrant("evidence.read", record.scope, "test:evidence"),
        ) if grant else (),
    )
    return evaluate_authorization(
        AuthorizationRequest(
            principal=principal,
            capability_code="evidence.read",
            resource=ResourceContext(
                resource_type="evidence",
                resource_id=record.evidence_id,
                scope=record.scope,
                classification=record.classification,
                available_fields=frozenset({"evidence_id", "evidence_kind"}),
            ),
            requested_fields=frozenset({"evidence_id", "evidence_kind"}),
        ),
        VisibilityPolicy(
            policy_id=record.visibility_policy_id,
            version=record.visibility_policy_version,
            mode=VisibilityMode.CONTROLLED_CONFIDENTIALITY,
            capabilities=frozenset({"evidence.read"}),
            visible_fields=frozenset({"evidence_id", "evidence_kind"}),
            allowed_classifications=frozenset(ResourceClassification),
        ),
    )


def association(context: OperationalContextRecord, evidence: EvidenceRecord) -> EvidenceAssociation:
    return EvidenceAssociation(
        context.context_id,
        evidence.evidence_id,
        EvidenceAssociationPurpose.ASSOCIATED_WITH,
        NOW,
    )


@pytest.mark.parametrize(
    "invalid",
    [
        "project-alpha",
        "ctx1:project:alpha beta",
        "ctx1:project:../alpha",
        "ctx1:project:alpha\n",
        "ctx1:project:origin-cost",
        "ctx1:project:session-token",
        "ev1:document:alpha",
        "ctx1:legacy:12345",
        "ctx1:digest:" + "a" * 64,
        "ctx1:project:" + "a" * 150,
    ],
)
def test_context_identity_rejects_invalid_path_control_sensitive_evidence_and_oversize(invalid):
    assert_code(ContextErrorCode.INVALID_IDENTIFIER, lambda: validate_context_id(invalid))


def test_identity_is_distinct_from_name_alias_revision_and_locale():
    alias = ContextAlias(AliasNamespace.PROJECT_CODE, "ALPHA-01", sensitive=False)
    item = context_record(aliases=(alias,))
    assert item.context_id != item.display_name != alias.value
    assert replace(item, display_name="Proyecto Alfa").context_id == item.context_id
    assert replace(item, revision=2, previous_revision=1).context_id == item.context_id


def test_name_or_alias_cannot_be_reused_as_canonical_identity():
    canonical = "ctx1:project:alpha"
    assert_code(
        ContextErrorCode.INVALID_IDENTIFIER,
        lambda: context_record(display_name=canonical),
    )
    assert_code(
        ContextErrorCode.INVALID_IDENTIFIER,
        lambda: context_record(
            aliases=(ContextAlias(AliasNamespace.EXTERNAL_SYSTEM_ID, canonical),)
        ),
    )


@pytest.mark.parametrize("bad_type", ["PROJECT", "PROYECTO", "ORDERED", PartyKind.ORGANIZATION, EvidenceKind.DOCUMENT])
def test_type_contract_rejects_strings_translation_workflow_party_and_evidence_kinds(bad_type):
    assert_code(ContextErrorCode.INVALID_TYPE, lambda: context_record(context_type=bad_type))


def test_closed_types_are_only_repository_justified_scope_subjects():
    assert {item.value for item in ContextType} == {"PRODUCT", "WORKSPACE", "PROJECT"}


def test_scope_reuses_exact_authority_contract_and_requires_organization_product_and_resource():
    item = context_record()
    assert type(item.scope) is AuthorityScope
    for field_name in ("organization_id", "product_id", "resource_id"):
        broken = replace(item.scope, **{field_name: None})
        assert_code(
            ContextErrorCode.INVALID_SCOPE,
            lambda broken=broken: context_record(record_scope=broken),
        )
    assert_code(
        ContextErrorCode.INVALID_SCOPE,
        lambda: context_record(record_scope=AuthorityScope(global_scope=True)),
    )


def test_scope_shape_is_type_specific_and_missing_dimensions_are_not_wildcards():
    assert context_record(context_type=ContextType.PRODUCT).scope.project_id is None
    assert context_record(
        "ctx1:workspace:alpha", context_type=ContextType.WORKSPACE
    ).scope.workspace_id == "workspace:alpha"
    assert_code(
        ContextErrorCode.INVALID_SCOPE,
        lambda: context_record(
            "ctx1:workspace:alpha",
            context_type=ContextType.WORKSPACE,
            record_scope=scope_for(
                "ctx1:workspace:alpha", ContextType.WORKSPACE, workspace=None
            ),
        ),
    )


def test_record_is_frozen_canonical_and_has_no_raw_evidence_or_metadata_dictionary():
    item = context_record()
    with pytest.raises(FrozenInstanceError):
        item.display_name = "Changed"
    assert item.created_at.tzinfo is timezone.utc
    assert "evidence" not in item.__dataclass_fields__
    assert "metadata" not in item.__dataclass_fields__
    assert "workflow_state" not in item.__dataclass_fields__


def test_record_validity_interval_and_revision_rules_fail_closed():
    assert_code(
        ContextErrorCode.INVALID_RECORD,
        lambda: context_record(valid_until=NOW),
    )
    assert_code(
        ContextErrorCode.INVALID_REVISION,
        lambda: context_record(revision=True),
    )
    assert_code(
        ContextErrorCode.INVALID_REVISION,
        lambda: context_record(revision=2, previous_revision=None),
    )
    assert_code(
        ContextErrorCode.INVALID_VERSION,
        lambda: context_record(contract_version="marketmatch-operational-context-v2"),
    )


def test_valid_product_workspace_project_hierarchy_and_deterministic_ancestry():
    product = context_record("ctx1:product:alpha", context_type=ContextType.PRODUCT)
    workspace = context_record(
        "ctx1:workspace:alpha",
        context_type=ContextType.WORKSPACE,
        parent=product.context_id,
    )
    project = context_record(parent=workspace.context_id)
    graph = validate_context_graph(
        [project, product, workspace],
        [],
        hierarchy_policy=hierarchy_policy(
            (ContextType.PRODUCT, ContextType.WORKSPACE),
            (ContextType.WORKSPACE, ContextType.PROJECT),
        ),
    )
    assert tuple(item.context_id for item in graph.contexts) == tuple(sorted(
        (project.context_id, product.context_id, workspace.context_id)
    ))
    assert context_ancestry(graph, project.context_id) == (
        product.context_id,
        workspace.context_id,
        project.context_id,
    )


def test_missing_parent_self_parent_and_unknown_parent_child_combination_reject():
    orphan = context_record(parent="ctx1:workspace:missing")
    assert_code(
        ContextErrorCode.MISSING_PARENT,
        lambda: validate_context_graph([orphan], [], hierarchy_policy=hierarchy_policy()),
    )
    assert_code(
        ContextErrorCode.INVALID_HIERARCHY,
        lambda: context_record(parent="ctx1:project:alpha"),
    )
    product = context_record("ctx1:product:alpha", context_type=ContextType.PRODUCT)
    child = context_record(parent=product.context_id)
    assert_code(
        ContextErrorCode.INVALID_HIERARCHY,
        lambda: validate_context_graph([product, child], [], hierarchy_policy=hierarchy_policy()),
    )


def test_cross_organization_product_and_project_hierarchy_rejects():
    parent = context_record("ctx1:project:parent")
    for field_name, value in (
        ("organization_id", "org:beta"),
        ("product_id", "other-product"),
        ("project_id", "project:beta"),
    ):
        child_id = f"ctx1:project:child-{field_name.replace('_id', '')}"
        child_scope = replace(scope_for(child_id), **{field_name: value})
        child = context_record(child_id, record_scope=child_scope, parent=parent.context_id)
        assert_code(
            ContextErrorCode.INVALID_SCOPE,
            lambda child=child: validate_context_graph(
                [parent, child],
                [],
                hierarchy_policy=hierarchy_policy((ContextType.PROJECT, ContextType.PROJECT)),
            ),
        )

    unowned_parent = context_record("ctx1:project:unowned", owner=None)
    owned_child = context_record("ctx1:project:owned", parent=unowned_parent.context_id)
    assert_code(
        ContextErrorCode.INVALID_SCOPE,
        lambda: validate_context_graph(
            [unowned_parent, owned_child],
            [],
            hierarchy_policy=hierarchy_policy((ContextType.PROJECT, ContextType.PROJECT)),
        ),
    )


def test_direct_multinode_and_deep_hierarchy_cycles_are_detected_iteratively():
    one = context_record("ctx1:project:one", parent="ctx1:project:two")
    two = context_record("ctx1:project:two", parent=one.context_id)
    policy = hierarchy_policy((ContextType.PROJECT, ContextType.PROJECT))
    assert_code(
        ContextErrorCode.HIERARCHY_CYCLE,
        lambda: validate_context_graph([one, two], [], hierarchy_policy=policy),
    )
    chain = []
    for index in range(MAX_ANCESTRY_DEPTH + 1):
        context_id = f"ctx1:project:deep-{index}"
        parent = None if index == 0 else f"ctx1:project:deep-{index - 1}"
        chain.append(context_record(context_id, parent=parent))
    graph = validate_context_graph(chain, [], hierarchy_policy=policy)
    assert_code(
        ContextErrorCode.INVALID_ANCESTRY,
        lambda: context_ancestry(graph, chain[-1].context_id),
    )
    deep_cycle = list(chain)
    deep_cycle[0] = replace(deep_cycle[0], parent_context_id=deep_cycle[-1].context_id)
    assert_code(
        ContextErrorCode.HIERARCHY_CYCLE,
        lambda: validate_context_graph(deep_cycle, [], hierarchy_policy=policy),
    )


def test_graph_duplicate_ids_size_limit_and_missing_ancestry_reject():
    item = context_record()
    assert_code(
        ContextErrorCode.DUPLICATE_CONTEXT,
        lambda: validate_context_graph([item, item], [], hierarchy_policy=hierarchy_policy()),
    )
    assert_code(
        ContextErrorCode.GRAPH_LIMIT_EXCEEDED,
        lambda: validate_context_graph(
            [item] * (MAX_CONTEXTS + 1), [], hierarchy_policy=hierarchy_policy()
        ),
    )
    graph = validate_context_graph([item], [], hierarchy_policy=hierarchy_policy())
    assert_code(
        ContextErrorCode.INVALID_ANCESTRY,
        lambda: context_ancestry(graph, "ctx1:project:missing"),
    )
    object.__setattr__(graph.hierarchy_policy, "version", "v2")
    assert_code(
        ContextErrorCode.INVALID_ANCESTRY,
        lambda: context_ancestry(graph, item.context_id),
    )


def test_omitted_parent_is_an_explicit_root_not_public_authority():
    root = context_record(parent=None)
    graph = validate_context_graph([root], [], hierarchy_policy=hierarchy_policy())
    assert context_ancestry(graph, root.context_id) == (root.context_id,)
    denied = context_decision(root, grant=False)
    assert denied.allowed is False
    assert project_context_metadata(root, denied) == {}


def test_relationship_directionality_symmetric_normalization_and_duplicate_collapse():
    one = context_record("ctx1:project:one")
    two = context_record("ctx1:project:two")
    forward = ContextRelationship(
        two.context_id, one.context_id, ContextRelationshipType.ASSOCIATED_WITH, NOW
    )
    reverse = ContextRelationship(
        one.context_id, two.context_id, ContextRelationshipType.ASSOCIATED_WITH, NOW
    )
    assert forward.source_context_id == reverse.source_context_id == one.context_id
    supersedes = ContextRelationship(
        two.context_id, one.context_id, ContextRelationshipType.SUPERSEDES, NOW
    )
    assert supersedes.source_context_id == two.context_id
    graph = validate_context_graph(
        [one, two], [forward, reverse, supersedes], hierarchy_policy=hierarchy_policy()
    )
    assert len(graph.relationships) == 2


def test_relationship_unknown_self_missing_endpoint_cross_scope_and_supersession_cycle_reject():
    one = context_record("ctx1:project:one")
    two = context_record("ctx1:project:two")
    assert_code(
        ContextErrorCode.INVALID_RELATIONSHIP,
        lambda: ContextRelationship(one.context_id, one.context_id, ContextRelationshipType.REPLACES, NOW),
    )
    assert_code(
        ContextErrorCode.INVALID_RELATIONSHIP,
        lambda: ContextRelationship(one.context_id, two.context_id, "REEMPLAZA", NOW),
    )
    missing = ContextRelationship(
        one.context_id, "ctx1:project:missing", ContextRelationshipType.REPLACES, NOW
    )
    assert_code(
        ContextErrorCode.INVALID_RELATIONSHIP,
        lambda: validate_context_graph([one], [missing], hierarchy_policy=hierarchy_policy()),
    )
    cross = context_record(
        "ctx1:project:cross",
        record_scope=scope_for("ctx1:project:cross", project="project:beta"),
    )
    relation = ContextRelationship(
        one.context_id, cross.context_id, ContextRelationshipType.ASSOCIATED_WITH, NOW
    )
    assert_code(
        ContextErrorCode.INVALID_SCOPE,
        lambda: validate_context_graph([one, cross], [relation], hierarchy_policy=hierarchy_policy()),
    )
    forward = ContextRelationship(
        one.context_id, two.context_id, ContextRelationshipType.SUPERSEDES, NOW
    )
    reverse = ContextRelationship(
        two.context_id, one.context_id, ContextRelationshipType.SUPERSEDES, NOW
    )
    assert_code(
        ContextErrorCode.RELATIONSHIP_CONFLICT,
        lambda: validate_context_graph([one, two], [forward, reverse], hierarchy_policy=hierarchy_policy()),
    )
    assert_code(
        ContextErrorCode.INVALID_VERSION,
        lambda: ContextRelationship(
            one.context_id,
            two.context_id,
            ContextRelationshipType.REPLACES,
            NOW,
            policy_version="marketmatch-operational-context-policy-v2",
        ),
    )
    future = context_record("ctx1:project:future", created_at=NOW + timedelta(minutes=1))
    premature = ContextRelationship(
        future.context_id, one.context_id, ContextRelationshipType.REPLACES, NOW
    )
    assert_code(
        ContextErrorCode.INVALID_RELATIONSHIP,
        lambda: validate_context_graph(
            [one, future], [premature], hierarchy_policy=hierarchy_policy()
        ),
    )


def test_relationships_are_not_workflow_completion_or_legal_conclusions():
    assert {item.value for item in ContextRelationshipType} == {
        "ASSOCIATED_WITH", "REPLACES", "SUPERSEDES"
    }
    fields = ContextRelationship.__dataclass_fields__
    assert "accepted" not in fields and "liable" not in fields and "completed" not in fields


def test_aliases_are_bounded_preserved_not_translated_and_collision_checked_by_scope_namespace():
    alias = ContextAlias(AliasNamespace.PROJECT_CODE, "BLDG-Alpha-01", sensitive=False)
    item = context_record(aliases=(alias,))
    assert item.aliases[0].value == "BLDG-Alpha-01"
    assert replace(item, display_name="Proyecto ficticio").aliases == item.aliases
    with pytest.raises(FrozenInstanceError):
        alias.value = "translated"
    assert_code(
        ContextErrorCode.INVALID_ALIAS,
        lambda: ContextAlias(AliasNamespace.PROJECT_CODE, "../alpha"),
    )
    assert_code(
        ContextErrorCode.INVALID_ALIAS,
        lambda: ContextAlias(AliasNamespace.PROJECT_CODE, "token=private"),
    )


def test_alias_collision_boundary_and_distinct_namespaces_are_deterministic():
    first = context_record(
        "ctx1:project:one",
        aliases=(ContextAlias(AliasNamespace.PROJECT_CODE, "ALPHA-01"),),
    )
    second = context_record(
        "ctx1:project:two",
        aliases=(ContextAlias(AliasNamespace.PROJECT_CODE, "alpha-01"),),
    )
    assert_code(
        ContextErrorCode.ALIAS_COLLISION,
        lambda: validate_context_graph([first, second], [], hierarchy_policy=hierarchy_policy()),
    )
    other_namespace = replace(
        second,
        aliases=(ContextAlias(AliasNamespace.EXTERNAL_SYSTEM_ID, "alpha-01"),),
    )
    graph = validate_context_graph([first, other_namespace], [], hierarchy_policy=hierarchy_policy())
    assert len(graph.contexts) == 2


def test_revision_transition_is_explicit_and_does_not_mutate_previous():
    previous = context_record(display_name="Old name")
    current = context_record(
        display_name="New name",
        created_at=NOW + timedelta(minutes=1),
        revision=2,
        previous_revision=1,
    )
    validate_revision_transition(previous, current)
    assert previous.display_name == "Old name"
    assert current.context_id == previous.context_id
    assert_code(
        ContextErrorCode.INVALID_REVISION,
        lambda: validate_revision_transition(previous, replace(current, revision=3, previous_revision=2)),
    )
    bounded_previous = context_record(valid_until=NOW + timedelta(minutes=5))
    overlapping_current = context_record(
        created_at=NOW + timedelta(minutes=1),
        revision=2,
        previous_revision=1,
    )
    assert_code(
        ContextErrorCode.INVALID_REVISION,
        lambda: validate_revision_transition(bounded_previous, overlapping_current),
    )


def test_replacement_uses_distinct_id_and_never_silently_merges_identity():
    old = context_record("ctx1:project:old")
    new = context_record("ctx1:project:new")
    relation = ContextRelationship(
        new.context_id, old.context_id, ContextRelationshipType.REPLACES, NOW
    )
    graph = validate_context_graph([old, new], [relation], hierarchy_policy=hierarchy_policy())
    assert len({item.context_id for item in graph.contexts}) == 2
    assert old.context_id == "ctx1:project:old"


def test_evidence_association_requires_existing_endpoints_exact_scope_and_policy():
    context = context_record()
    evidence = evidence_record(context=context)
    item = association(context, evidence)
    assert validate_evidence_associations([context], [evidence], [item]) == (item,)
    missing = EvidenceAssociation(
        context.context_id,
        "ev1:document:missing",
        EvidenceAssociationPurpose.ASSOCIATED_WITH,
        NOW,
    )
    assert_code(
        ContextErrorCode.INVALID_EVIDENCE_ASSOCIATION,
        lambda: validate_evidence_associations([context], [evidence], [missing]),
    )
    cross_org = evidence_record("ev1:document:cross-org", context=context, organization="org:beta")
    assert_code(
        ContextErrorCode.EVIDENCE_SCOPE_CONFLICT,
        lambda: validate_evidence_associations(
            [context], [cross_org], [association(context, cross_org)]
        ),
    )
    cross_project = evidence_record(
        "ev1:document:cross-project", context=context, project="project:beta"
    )
    assert_code(
        ContextErrorCode.EVIDENCE_SCOPE_CONFLICT,
        lambda: validate_evidence_associations(
            [context], [cross_project], [association(context, cross_project)]
        ),
    )
    other_policy = evidence_record(
        "ev1:document:policy", context=context, policy_id="marketmatch.other"
    )
    assert_code(
        ContextErrorCode.EVIDENCE_POLICY_CONFLICT,
        lambda: validate_evidence_associations(
            [context], [other_policy], [association(context, other_policy)]
        ),
    )
    assert_code(
        ContextErrorCode.INVALID_VERSION,
        lambda: EvidenceAssociation(
            context.context_id,
            evidence.evidence_id,
            EvidenceAssociationPurpose.ASSOCIATED_WITH,
            NOW,
            policy_version="marketmatch-operational-context-policy-v2",
        ),
    )


def test_evidence_association_does_not_mutate_or_authorize_evidence_and_removal_does_not_delete():
    context = context_record()
    evidence = evidence_record(context=context)
    before = evidence_metadata_snapshot = evidence_metadata(evidence)
    item = association(context, evidence)
    validate_evidence_associations([context], [evidence], [item])
    assert evidence_metadata(evidence) == evidence_metadata_snapshot == before
    denied = evidence_decision(evidence, grant=False)
    assert denied.allowed is False
    assert linked_evidence_count(
        context, context_decision(context), [item], [evidence], [denied]
    ) == 1
    assert validate_evidence_associations([context], [evidence], []) == ()
    assert evidence.evidence_id == "ev1:document:alpha"


def test_visible_context_does_not_reveal_evidence_without_explicit_count_field():
    context = context_record()
    evidence = evidence_record(context=context)
    decision = context_decision(context, visible_fields=frozenset({"context_id"}))
    assert linked_evidence_count(
        context, decision, [association(context, evidence)], [evidence], [evidence_decision(evidence)]
    ) is None
    assert project_context_metadata(context, decision) == {"context_id": context.context_id}
    hostile_mapping = context_metadata(context)
    hostile_mapping["linked_evidence_count"] = 999
    with_count_decision = context_decision(context)
    assert "linked_evidence_count" not in project_context_metadata(
        hostile_mapping, with_count_decision
    )


def test_linked_count_blocks_missing_malformed_or_mismatched_sources_instead_of_omitting():
    context = context_record()
    evidence = evidence_record(context=context)
    item = association(context, evidence)
    assert_code(
        ContextErrorCode.INVALID_EVIDENCE_ASSOCIATION,
        lambda: linked_evidence_count(
            context, context_decision(context), [item], [], []
        ),
    )
    wrong = evidence_record("ev1:document:wrong", context=context)
    assert_code(
        ContextErrorCode.INVALID_EVIDENCE_ASSOCIATION,
        lambda: linked_evidence_count(
            context,
            context_decision(context),
            [item],
            [wrong],
            [evidence_decision(wrong)],
        ),
    )


def test_projection_requires_authentic_exact_id_scope_policy_and_returns_new_flat_scalars():
    context = context_record(
        aliases=(ContextAlias(AliasNamespace.PROJECT_CODE, "ALPHA-01"),)
    )
    decision = context_decision(context)
    projected = project_context_metadata(context, decision)
    assert projected is not context
    assert projected["context_id"] == context.context_id
    assert all(value is None or type(value) in (str, bool, int, float) for value in projected.values())
    assert "aliases" not in projected and "owner_party_ref" not in projected
    wrong = context_record("ctx1:project:wrong")
    assert_code(
        ContextErrorCode.INVALID_PROJECTION,
        lambda: project_context_metadata(wrong, decision),
    )
    mutated = context_decision(context)
    object.__setattr__(mutated, "resource_id", "ctx1:project:wrong")
    assert_code(
        ContextErrorCode.INVALID_PROJECTION,
        lambda: project_context_metadata(context, mutated),
    )


def test_projection_record_mutation_and_nested_aliasing_fail_closed():
    context = context_record()
    decision = context_decision(context, visible_fields=ALL_FIELDS)
    object.__setattr__(context, "display_name", "Changed after decision")
    assert_code(
        ContextErrorCode.INVALID_PROJECTION,
        lambda: project_context_metadata(context, decision),
    )
    clean = context_record()
    mapping = context_metadata(clean)
    nested = {"protected": ["supplier"]}
    mapping["unknown_nested"] = nested
    mapping["display_name"] = nested
    output = project_context_metadata(mapping, context_decision(clean, visible_fields=ALL_FIELDS))
    assert "display_name" not in output and "unknown_nested" not in output
    nested["protected"].append("factory")
    assert output == project_context_metadata(mapping, context_decision(clean, visible_fields=ALL_FIELDS))


def test_hostile_mapping_exception_is_fixed_and_value_free():
    class Hostile(Mapping):
        def __iter__(self):
            raise RuntimeError("supplier-secret-address")

        def __len__(self):
            return 1

        def __getitem__(self, key):
            raise RuntimeError("origin-cost-margin")

    context = context_record()
    with pytest.raises(OperationalContextError) as caught:
        project_context_metadata(Hostile(), context_decision(context))
    assert str(caught.value) == ContextErrorCode.INVALID_PROJECTION.value
    assert "supplier" not in str(caught.value)


def test_locale_and_timezone_do_not_change_identity_hierarchy_projection_or_authority():
    context = context_record()
    decisions = [
        context_decision(context, locale=locale, display_timezone=zone)
        for locale, zone in (
            ("es", "America/Santo_Domingo"),
            ("en", "UTC"),
            ("zh-Hans", "Asia/Shanghai"),
        )
    ]
    assert all(decision.allowed for decision in decisions)
    assert len({decision.reason_code for decision in decisions}) == 1
    assert len({tuple(sorted(project_context_metadata(context, decision).items())) for decision in decisions}) == 1
    assert context.created_at == NOW


def test_derived_context_visibility_intersects_fields_and_preserves_restriction():
    one = context_record("ctx1:project:one", classification=ResourceClassification.INTERNAL)
    two = context_record("ctx1:project:two", classification=ResourceClassification.RESTRICTED)
    one_decision = context_decision(one, visible_fields=frozenset({"context_id", "display_name"}))
    two_decision = context_decision(two, visible_fields=frozenset({"context_id"}))
    inherited = inherit_context_visibility(
        [one, two], [one_decision, two_decision], artifact_kind=DerivedArtifactKind.SUMMARY
    )
    assert inherited.allowed is True
    assert inherited.visible_fields == frozenset({"context_id"})
    assert inherited.classification is ResourceClassification.RESTRICTED


def test_derived_context_visibility_denied_source_scope_and_policy_conflicts_fail_closed():
    one = context_record("ctx1:project:one")
    two = context_record("ctx1:project:two")
    assert_code(
        ContextErrorCode.SOURCE_NOT_AUTHORIZED,
        lambda: inherit_context_visibility(
            [one, two],
            [context_decision(one), context_decision(two, grant=False)],
            artifact_kind=DerivedArtifactKind.REPORT,
        ),
    )
    cross = context_record(
        "ctx1:project:cross",
        record_scope=scope_for("ctx1:project:cross", project="project:beta"),
    )
    assert_code(
        ContextErrorCode.SOURCE_SCOPE_CONFLICT,
        lambda: inherit_context_visibility(
            [one, cross],
            [context_decision(one), context_decision(cross)],
            artifact_kind=DerivedArtifactKind.REPORT,
        ),
    )
    other_policy = context_record(
        "ctx1:project:other-policy", policy_id="marketmatch.other"
    )
    assert_code(
        ContextErrorCode.SOURCE_POLICY_CONFLICT,
        lambda: inherit_context_visibility(
            [one, other_policy],
            [context_decision(one), context_decision(other_policy)],
            artifact_kind=DerivedArtifactKind.REPORT,
        ),
    )


@pytest.mark.parametrize(
    "unsafe_id",
    [
        "ctx1:project:street-address",
        "ctx1:project:origin-cost",
        "ctx1:project:gross-margin",
        "ctx1:project:session-token",
        "ctx1:project:newline\nvalue",
    ],
)
def test_audit_identifier_rejects_addresses_commercial_values_sessions_and_controls(unsafe_id):
    assert_code(ContextErrorCode.INVALID_IDENTIFIER, lambda: validate_context_id(unsafe_id))


def test_safe_audit_is_bounded_value_free_and_rejects_mutation_or_bad_correlation():
    context = context_record(
        display_name="Hidden Factory Address",
        aliases=(ContextAlias(AliasNamespace.EXTERNAL_SYSTEM_ID, "SUPPLIER-PRIVATE"),),
    )
    decision = context_decision(context)
    audit = build_safe_context_audit_summary(
        context,
        decision,
        action="context.read",
        timestamp=NOW,
        correlation_id="request:alpha",
    )
    text = repr(audit).lower()
    for forbidden in (
        "factory", "supplier", "address", "alias", "cost", "margin", "cookie", "session", "evidence"
    ):
        assert forbidden not in text
    assert audit.scope_reference == context.context_id
    assert_code(
        ContextErrorCode.INVALID_AUDIT,
        lambda: build_safe_context_audit_summary(
            context, decision, action="context.read", timestamp=NOW, correlation_id="token:secret"
        ),
    )
    object.__setattr__(context, "display_name", "mutated")
    assert_code(
        ContextErrorCode.INVALID_AUDIT,
        lambda: build_safe_context_audit_summary(
            context, decision, action="context.read", timestamp=NOW
        ),
    )


def test_contract_imports_authority_and_evidence_without_duplicate_scope_evaluator():
    source = Path("src/marketmatch_operational_context.py").read_text()
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert {"AuthorityScope", "inherit_derived_visibility", "EvidenceRecord", "validate_evidence_id"} <= imported
    assert "class AuthorityScope" not in source
    assert "def evaluate_authorization" not in source


def test_pure_contract_has_no_io_persistence_remote_model_browser_or_mutable_registry():
    source = Path("src/marketmatch_operational_context.py").read_text()
    tree = ast.parse(source)
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden_imports = {"requests", "httpx", "aiohttp", "sqlalchemy", "sqlite3", "openai", "anthropic"}
    assert not (imports & forbidden_imports)
    for forbidden in (
        "open(", "Path(", "requests.", "httpx.", "localStorage", "sessionStorage",
        "indexedDB", "SessionLocal", "engine.execute", "model.invoke", "subprocess",
    ):
        assert forbidden not in source
    assert "REGISTRY =" not in source


def test_no_production_route_or_existing_endpoint_imports_operational_context():
    for root in (Path("routes"), Path("static")):
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".js", ".html"}:
                assert "marketmatch_operational_context" not in path.read_text(errors="ignore")
    assert "marketmatch_operational_context" not in Path("app.py").read_text()


def test_no_migration_runtime_data_or_compatibility_adapter_was_added():
    changed = set(
        path
        for path in (
            "src/marketmatch_operational_context.py",
            "tests/test_marketmatch_operational_context.py",
            "docs/adr/0003-marketmatch-operational-context-kernel-v1.md",
        )
        if Path(path).exists()
    )
    assert changed == {
        "src/marketmatch_operational_context.py",
        "tests/test_marketmatch_operational_context.py",
        "docs/adr/0003-marketmatch-operational-context-kernel-v1.md",
    }
    assert not Path("src/marketmatch_operational_context_compat.py").exists()


def test_adr_documents_required_contract_boundaries_and_glossary():
    text = Path("docs/adr/0003-marketmatch-operational-context-kernel-v1.md").read_text()
    required = (
        "identity versus name and alias",
        "Context is not Party",
        "Context is not Evidence",
        "type is not workflow state",
        "Hierarchy and association",
        "cycle",
        "Authority before projection",
        "Evidence association",
        "locale",
        "timezone",
        "Current state versus target state",
        "Compatibility adapters",
        "Persistence and integration deferral",
        "Stable-code glossary",
        "Limitations",
        "Asset Passport",
    )
    for phrase in required:
        assert phrase.lower() in text.lower()


# Imported late so helper code remains visually focused on context construction.
from src.marketmatch_evidence import evidence_metadata
