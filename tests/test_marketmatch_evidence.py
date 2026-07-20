from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping
from unittest import mock

import pytest

from src.marketmatch_authority import (
    AuthorityReason,
    AuthorityScope,
    AuthorizationRequest,
    PartyKind,
    PartyReference,
    PrincipalContext,
    ProjectionBehavior,
    ResourceClassification,
    ResourceContext,
    ScopedCapabilityGrant,
    VisibilityMode,
    VisibilityPolicy,
    evaluate_authorization,
    project_authorized_fields,
)
from src.marketmatch_evidence import (
    AcquisitionMethod,
    AttestationMethod,
    AttestationTargetKind,
    AttestationType,
    BundlePurpose,
    ContentIntegrityDescriptor,
    DigestAlgorithm,
    EVIDENCE_CONTRACT_VERSION,
    EvidenceAttestation,
    EvidenceBundle,
    EvidenceContractError,
    EvidenceErrorCode,
    EvidenceKind,
    EvidenceRecord,
    EvidenceRelation,
    GeneratorKind,
    IntegrityBasis,
    MediaKind,
    ProvenanceContext,
    RelationType,
    SourceKind,
    TransformationType,
    VerificationStatus,
    authorize_bundle_members,
    build_safe_evidence_audit_summary,
    canonicalize_relations,
    create_evidence_derivation,
    evidence_metadata,
    inherit_evidence_visibility,
    project_evidence_metadata,
    serialize_evidence_record,
    validate_bundle_members,
    validate_evidence_id,
)
from src.marketmatch_evidence_compat import (
    capture_document_to_evidence,
    document_to_evidence,
    media_attestation_to_integrity,
    media_observation_to_integrity,
    upload_metadata_to_evidence,
)
from src.marketmatch_media_attestation import (
    AttestationAuthority,
    MediaDomain,
    OriginalMediaRole,
    ValidatedOriginalMediaAttestation,
)
from src.marketmatch_original_media_observation import OriginalMediaObservation


NOW = datetime(2026, 7, 20, 15, 0, tzinfo=timezone.utc)
VISIBLE_FIELDS = frozenset(
    {
        "contract_version",
        "evidence_id",
        "evidence_kind",
        "media_kind",
        "classification",
        "created_at",
        "integrity_algorithm",
        "integrity_digest",
        "integrity_basis",
        "verification_status",
        "evidence_summary",
    }
)
ALL_FIELDS = VISIBLE_FIELDS | frozenset(
    {
        "title",
        "description",
        "owner_party_ref",
        "visibility_policy_id",
        "visibility_policy_version",
        "integrity_byte_length",
        "mime_type",
        "original_filename",
        "source_kind",
        "acquisition_method",
        "source_system",
        "capturing_party_ref",
        "capture_timestamp",
        "received_timestamp",
        "ingestion_timestamp",
        "external_reference",
        "device_reference",
    }
)


def assert_code(code: EvidenceErrorCode, call) -> None:
    with pytest.raises(EvidenceContractError) as caught:
        call()
    assert caught.value.code is code
    assert str(caught.value) == code.value


def scope(
    evidence_id: str,
    *,
    organization: str = "org:alpha",
    product: str = "marketmatch",
    workspace: str = "workspace:alpha",
    project: str = "project:alpha",
    owner: str = "party:alpha",
) -> AuthorityScope:
    return AuthorityScope(
        organization_id=organization,
        product_id=product,
        workspace_id=workspace,
        project_id=project,
        resource_id=evidence_id,
        owner_party_id=owner,
    )


def provenance(
    *,
    source_kind: SourceKind = SourceKind.USER,
    acquisition: AcquisitionMethod = AcquisitionMethod.UPLOAD,
    captured: datetime | None = NOW - timedelta(minutes=3),
    received: datetime | None = NOW - timedelta(minutes=2),
    ingested: datetime | None = NOW - timedelta(minutes=1),
    filename: str | None = "inspection.txt",
) -> ProvenanceContext:
    return ProvenanceContext(
        source_kind=source_kind,
        acquisition_method=acquisition,
        source_system="marketmatch.capture",
        capturing_party_ref="party:alpha",
        capture_timestamp=captured,
        received_timestamp=received,
        ingestion_timestamp=ingested,
        original_filename=filename,
        device_reference="device:alpha",
    )


def record(
    evidence_id: str = "ev1:document:alpha",
    *,
    payload: bytes = b"same bytes",
    record_scope: AuthorityScope | None = None,
    owner: str = "party:alpha",
    policy_id: str = "marketmatch.evidence",
    policy_version: str = "v1",
    classification: ResourceClassification = ResourceClassification.CONFIDENTIAL,
    record_provenance: ProvenanceContext | None = None,
    evidence_kind: EvidenceKind = EvidenceKind.DOCUMENT,
    media_kind: MediaKind = MediaKind.TEXT,
    created_at: datetime = NOW,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        evidence_kind=evidence_kind,
        media_kind=media_kind,
        scope=record_scope or scope(evidence_id, owner=owner),
        owner_party_ref=owner,
        integrity=ContentIntegrityDescriptor.from_bytes(
            payload,
            mime_type="text/plain",
            original_filename="inspection.txt",
        ),
        provenance=record_provenance or provenance(),
        created_at=created_at,
        classification=classification,
        visibility_policy_id=policy_id,
        visibility_policy_version=policy_version,
        title="Inspection note",
        description="Fictional bounded evidence metadata.",
    )


def policy(
    *,
    policy_id: str = "marketmatch.evidence",
    version: str = "v1",
    visible_fields: frozenset[str] = VISIBLE_FIELDS,
) -> VisibilityPolicy:
    return VisibilityPolicy(
        policy_id=policy_id,
        version=version,
        mode=VisibilityMode.CONTROLLED_CONFIDENTIALITY,
        capabilities=frozenset({"evidence.read"}),
        visible_fields=visible_fields,
        allowed_classifications=frozenset(ResourceClassification),
    )


def decision_for(
    item: EvidenceRecord,
    *,
    visible_fields: frozenset[str] = VISIBLE_FIELDS,
    grant: bool = True,
    locale: str = "es",
    display_timezone: str = "UTC",
):
    principal = PrincipalContext(
        principal_ref="principal:reviewer",
        party=PartyReference("party:reviewer", PartyKind.PERSON),
        capability_grants=(
            ScopedCapabilityGrant("evidence.read", item.scope, "test:grant"),
        ) if grant else (),
    )
    return evaluate_authorization(
        AuthorizationRequest(
            principal=principal,
            capability_code="evidence.read",
            resource=ResourceContext(
                resource_type="evidence",
                resource_id=item.evidence_id,
                scope=item.scope,
                classification=item.classification,
                available_fields=ALL_FIELDS,
            ),
            requested_fields=ALL_FIELDS,
            presentation_locale=locale,
            display_timezone=display_timezone,
        ),
        policy(
            policy_id=item.visibility_policy_id,
            version=item.visibility_policy_version,
            visible_fields=visible_fields,
        ),
    )


def generic_decision(resource_type: str, resource_id: str, item_scope: AuthorityScope, *, grant=True):
    principal = PrincipalContext(
        principal_ref="principal:reviewer",
        party=PartyReference("party:reviewer", PartyKind.PERSON),
        capability_grants=(
            ScopedCapabilityGrant("evidence.read", item_scope, "test:grant"),
        ) if grant else (),
    )
    return evaluate_authorization(
        AuthorizationRequest(
            principal=principal,
            capability_code="evidence.read",
            resource=ResourceContext(
                resource_type=resource_type,
                resource_id=resource_id,
                scope=item_scope,
                classification=ResourceClassification.CONFIDENTIAL,
                available_fields=frozenset(),
            ),
        ),
        policy(visible_fields=frozenset()),
    )


def generated_record(
    source: EvidenceRecord,
    evidence_id: str = "ev1:document:translation-alpha",
    *,
    classification: ResourceClassification | None = None,
    owner: str | None = None,
    project: str | None = None,
    policy_id: str | None = None,
) -> EvidenceRecord:
    inherited_owner = owner or source.owner_party_ref
    generated_scope = AuthorityScope(
        organization_id=source.scope.organization_id,
        product_id=source.scope.product_id,
        workspace_id=source.scope.workspace_id,
        project_id=project or source.scope.project_id,
        resource_id=evidence_id,
        owner_party_id=inherited_owner,
    )
    generated_provenance = ProvenanceContext(
        source_kind=SourceKind.SYSTEM,
        acquisition_method=AcquisitionMethod.GENERATED,
        source_system="marketmatch.translation",
        ingestion_timestamp=NOW,
    )
    return EvidenceRecord(
        evidence_id=evidence_id,
        evidence_kind=EvidenceKind.DOCUMENT,
        media_kind=MediaKind.TEXT,
        scope=generated_scope,
        owner_party_ref=inherited_owner,
        integrity=ContentIntegrityDescriptor.from_bytes(b"translated artifact", mime_type="text/plain"),
        provenance=generated_provenance,
        created_at=NOW,
        classification=classification or source.classification,
        visibility_policy_id=policy_id or source.visibility_policy_id,
        visibility_policy_version=source.visibility_policy_version,
        title="Translated inspection note",
    )


def test_evidence_identity_is_not_digest_and_shared_content_keeps_distinct_records():
    first = record("ev1:document:alpha")
    second = record(
        "ev1:document:beta",
        record_scope=scope("ev1:document:beta"),
        record_provenance=provenance(captured=NOW - timedelta(hours=1)),
    )
    assert first.evidence_id != first.integrity.digest
    assert first.evidence_id != second.evidence_id
    assert first.integrity.digest == second.integrity.digest
    assert first.provenance != second.provenance


@pytest.mark.parametrize(
    "value",
    [
        "", " ev1:document:alpha", "ev1:document:alpha ", "ev1:document:../alpha",
        "ev1:document:alpha\nlog", "ev1:document:token:value", "a" * 161,
        hashlib.sha256(b"x").hexdigest(), "evidence-alpha", "EV1:document:alpha",
    ],
)
def test_invalid_or_confidential_evidence_identifiers_reject(value):
    assert_code(EvidenceErrorCode.INVALID_IDENTIFIER, lambda: validate_evidence_id(value))


def test_identity_and_digest_are_locale_independent():
    item = record()
    values = [(item.evidence_id, item.integrity.digest) for _ in ("es", "en", "zh-Hans")]
    assert values[0] == values[1] == values[2]


def test_sha256_bytes_integrity_normalization_and_content_sensitivity():
    lower = ContentIntegrityDescriptor.metadata_only(
        algorithm=DigestAlgorithm.SHA256,
        digest="A" * 64,
        byte_length=3,
    )
    first = ContentIntegrityDescriptor.from_bytes(b"abc", mime_type="text/plain", original_filename="a.txt")
    renamed = ContentIntegrityDescriptor.from_bytes(b"abc", mime_type="application/octet-stream", original_filename="b.bin")
    changed = ContentIntegrityDescriptor.from_bytes(b"abd")
    assert lower.digest == "a" * 64
    assert first.basis is IntegrityBasis.BYTES_VERIFIED
    assert first.digest == renamed.digest == hashlib.sha256(b"abc").hexdigest()
    assert changed.digest != first.digest


@pytest.mark.parametrize("byte_length", [-1, True, "3", 1.5, 1 << 63])
def test_invalid_byte_lengths_reject(byte_length):
    assert_code(
        EvidenceErrorCode.INVALID_INTEGRITY,
        lambda: ContentIntegrityDescriptor.metadata_only(byte_length=byte_length),
    )


@pytest.mark.parametrize("digest", ["f" * 63, "f" * 65, "g" * 64, "not-hex"])
def test_malformed_digests_reject(digest):
    assert_code(
        EvidenceErrorCode.INVALID_INTEGRITY,
        lambda: ContentIntegrityDescriptor.metadata_only(
            algorithm=DigestAlgorithm.SHA256, digest=digest
        ),
    )


def test_unknown_and_weak_algorithms_and_direct_byte_verified_claim_reject():
    for algorithm in ("md5", "sha1", "sha512"):
        assert_code(
            EvidenceErrorCode.INVALID_INTEGRITY,
            lambda algorithm=algorithm: ContentIntegrityDescriptor.metadata_only(
                algorithm=algorithm, digest="0" * 64
            ),
        )
    assert_code(
        EvidenceErrorCode.INVALID_INTEGRITY,
        lambda: ContentIntegrityDescriptor(
            DigestAlgorithm.SHA256, "0" * 64, 1, IntegrityBasis.BYTES_VERIFIED, None, None
        ),
    )


def test_non_byte_bases_cannot_carry_a_claimed_digest_or_length():
    for factory in (
        ContentIntegrityDescriptor.external_reference,
        ContentIntegrityDescriptor.not_available,
    ):
        value = factory()
        assert value.algorithm is None and value.digest is None and value.byte_length is None
        assert value.basis is not IntegrityBasis.BYTES_VERIFIED


def test_provenance_keeps_optional_and_distinct_timestamps_honestly():
    empty = ProvenanceContext(SourceKind.SYSTEM, AcquisitionMethod.SYSTEM_OBSERVATION)
    assert empty.capture_timestamp is empty.received_timestamp is empty.ingestion_timestamp is None
    item = provenance()
    assert item.capture_timestamp < item.received_timestamp < item.ingestion_timestamp


@pytest.mark.parametrize("name", ["capture_timestamp", "received_timestamp", "ingestion_timestamp"])
def test_naive_provenance_timestamps_reject(name):
    values = dict(captured=NOW, received=NOW, ingested=NOW)
    values[{"capture_timestamp": "captured", "received_timestamp": "received", "ingestion_timestamp": "ingested"}[name]] = NOW.replace(tzinfo=None)
    assert_code(EvidenceErrorCode.INVALID_TIMESTAMP, lambda: provenance(**values))


def test_provenance_internal_ordering_rejects_capture_or_receipt_after_ingestion():
    assert_code(
        EvidenceErrorCode.INVALID_PROVENANCE,
        lambda: provenance(captured=NOW, ingested=NOW - timedelta(seconds=1)),
    )
    assert_code(
        EvidenceErrorCode.INVALID_PROVENANCE,
        lambda: provenance(received=NOW, ingested=NOW - timedelta(seconds=1)),
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"external_reference": "https://user:password@example.test/source"},
        {"external_reference": "https://example.test/source?token=secret"},
        {"original_filename": "../secret.txt"},
        {"original_filename": "line\nbreak.txt"},
        {"original_filename": "delete\x7f.txt"},
        {"capturing_party_ref": "principal:session:token"},
        {"safe_note": "password=correct-horse"},
        {"safe_note": "Bearer correct-horse"},
        {"device_reference": "device:cookie:value"},
    ],
)
def test_provenance_rejects_credentials_controls_paths_and_secret_bearing_references(kwargs):
    base = dict(source_kind=SourceKind.EXTERNAL_SYSTEM, acquisition_method=AcquisitionMethod.IMPORT)
    base.update(kwargs)
    assert_code(EvidenceErrorCode.INVALID_PROVENANCE, lambda: ProvenanceContext(**base))


def test_external_reference_requires_safe_credential_free_url():
    value = ProvenanceContext(
        SourceKind.EXTERNAL_SYSTEM,
        AcquisitionMethod.EXTERNAL_REFERENCE,
        external_reference="https://example.test/evidence/alpha",
    )
    assert value.external_reference.endswith("/alpha")
    assert_code(
        EvidenceErrorCode.INVALID_PROVENANCE,
        lambda: ProvenanceContext(SourceKind.EXTERNAL_SYSTEM, AcquisitionMethod.EXTERNAL_REFERENCE),
    )


def test_timezone_representation_does_not_change_canonical_provenance():
    minus_four = timezone(timedelta(hours=-4))
    first = provenance(captured=NOW, received=NOW, ingested=NOW)
    second = provenance(captured=NOW.astimezone(minus_four), received=NOW, ingested=NOW)
    assert first.capture_timestamp == second.capture_timestamp == NOW


def test_record_is_immutable_deterministic_and_contains_no_raw_bytes():
    item = record()
    before = serialize_evidence_record(item)
    assert before == serialize_evidence_record(record())
    assert b"same bytes" not in before
    assert "raw_bytes" not in evidence_metadata(item)
    with pytest.raises(FrozenInstanceError):
        item.title = "mutated"
    assert serialize_evidence_record(item) == before


def test_record_requires_valid_kind_scope_owner_integrity_and_version():
    base = record()
    for field_name, value, code in (
        ("evidence_kind", "DOCUMENT", EvidenceErrorCode.INVALID_ENUM),
        ("scope", AuthorityScope(), EvidenceErrorCode.INVALID_RECORD),
        ("owner_party_ref", None, EvidenceErrorCode.INVALID_RECORD),
        ("integrity", object(), EvidenceErrorCode.INVALID_RECORD),
        ("contract_version", "marketmatch-evidence-v2", EvidenceErrorCode.INVALID_VERSION),
    ):
        values = {name: getattr(base, name) for name in base.__dataclass_fields__ if not name.startswith("_")}
        values[field_name] = value
        assert_code(code, lambda values=values: EvidenceRecord(**values))


def test_relation_direction_duplicate_handling_and_locale_independence():
    first = EvidenceRelation(
        "ev1:document:translation", "ev1:document:original",
        RelationType.TRANSLATED_FROM, NOW,
    )
    reversed_relation = EvidenceRelation(
        "ev1:document:original", "ev1:document:translation",
        RelationType.TRANSLATED_FROM, NOW,
    )
    result = canonicalize_relations([first, first])
    assert result == (first,)
    assert reversed_relation != first
    assert first.relation_type.value == "TRANSLATED_FROM"


def test_long_acyclic_lineage_is_bounded_without_recursive_failure():
    links = [
        EvidenceRelation(f"ev1:document:n{index}", f"ev1:document:n{index + 1}", RelationType.DERIVED_FROM, NOW)
        for index in range(1_200)
    ]
    assert len(canonicalize_relations(links)) == 1_200


@pytest.mark.parametrize("relation", [RelationType.DERIVED_FROM, RelationType.TRANSCRIBED_FROM, RelationType.TRANSLATED_FROM])
def test_lineage_cycles_deny(relation):
    links = [
        EvidenceRelation("ev1:document:a", "ev1:document:b", relation, NOW),
        EvidenceRelation("ev1:document:b", "ev1:document:a", relation, NOW),
    ]
    assert_code(EvidenceErrorCode.LINEAGE_CYCLE, lambda: canonicalize_relations(links))


def test_unknown_and_self_relations_reject_and_supersession_does_not_mutate_original():
    item = record()
    before = serialize_evidence_record(item)
    assert_code(
        EvidenceErrorCode.INVALID_RELATION,
        lambda: EvidenceRelation(item.evidence_id, item.evidence_id, RelationType.SUPERSEDES, NOW),
    )
    assert_code(
        EvidenceErrorCode.INVALID_RELATION,
        lambda: EvidenceRelation("ev1:document:new", item.evidence_id, "SUPERSEDES", NOW),
    )
    EvidenceRelation("ev1:document:new", item.evidence_id, RelationType.SUPERSEDES, NOW)
    assert serialize_evidence_record(item) == before
    relation = EvidenceRelation("ev1:document:new", item.evidence_id, RelationType.SUPERSEDES, NOW)
    object.__setattr__(relation, "target_evidence_id", "ev1:document:address:private")
    assert_code(EvidenceErrorCode.INVALID_RELATION, lambda: canonicalize_relations([relation]))


def test_support_and_contradict_relations_are_structural_not_conclusions():
    for kind in (RelationType.SUPPORTS, RelationType.CONTRADICTS):
        relation = EvidenceRelation("ev1:document:a", "ev1:document:b", kind, NOW)
        assert not hasattr(relation, "truth")
        assert not hasattr(relation, "liability")


def test_bundle_membership_is_explicit_nonempty_sorted_and_deduplicated():
    bundle = EvidenceBundle(
        "bundle1:review:alpha",
        BundlePurpose.REVIEW,
        ("ev1:document:b", "ev1:document:a", "ev1:document:b"),
        AuthorityScope(product_id="marketmatch", owner_party_id="party:alpha"),
        "party:alpha",
        NOW,
    )
    assert bundle.member_evidence_ids == ("ev1:document:a", "ev1:document:b")
    assert_code(
        EvidenceErrorCode.INVALID_BUNDLE,
        lambda: EvidenceBundle(
            "bundle1:review:empty", BundlePurpose.REVIEW, (),
            AuthorityScope(product_id="marketmatch"), None, NOW,
        ),
    )


def test_bundle_validation_fails_unknown_and_cross_scope_members_without_mutation():
    first = record("ev1:document:a", record_scope=scope("ev1:document:a"))
    second = record(
        "ev1:document:b",
        record_scope=scope("ev1:document:b", organization="org:beta"),
    )
    bundle = EvidenceBundle(
        "bundle1:review:alpha", BundlePurpose.REVIEW,
        (first.evidence_id, second.evidence_id),
        AuthorityScope(product_id="marketmatch", owner_party_id="party:alpha"),
        "party:alpha", NOW,
    )
    before = serialize_evidence_record(first)
    assert_code(EvidenceErrorCode.BUNDLE_SCOPE_CONFLICT, lambda: validate_bundle_members(bundle, [first, second]))
    assert serialize_evidence_record(first) == before
    one_member = EvidenceBundle(
        "bundle1:review:one", BundlePurpose.REVIEW,
        (first.evidence_id,), first.scope, first.owner_party_ref, NOW,
    )
    assert_code(EvidenceErrorCode.UNKNOWN_BUNDLE_MEMBER, lambda: validate_bundle_members(one_member, []))


def test_bundle_access_never_implies_member_access_or_verification():
    first = record("ev1:document:a", record_scope=scope("ev1:document:a"))
    second = record("ev1:document:b", record_scope=scope("ev1:document:b"))
    bundle_scope = AuthorityScope(product_id="marketmatch", owner_party_id="party:alpha")
    bundle = EvidenceBundle(
        "bundle1:review:alpha", BundlePurpose.REVIEW,
        (first.evidence_id, second.evidence_id), bundle_scope, "party:alpha", NOW,
    )
    bundle_allowed = generic_decision("evidence_bundle", bundle.bundle_id, bundle_scope)
    assert_code(
        EvidenceErrorCode.MEMBER_NOT_AUTHORIZED,
        lambda: authorize_bundle_members(
            bundle, bundle_allowed, [decision_for(first), decision_for(second, grant=False)]
        ),
    )
    assert not hasattr(bundle, "verification_status")
    assert_code(
        EvidenceErrorCode.MEMBER_NOT_AUTHORIZED,
        lambda: authorize_bundle_members(
            bundle, bundle_allowed, [decision_for(first), decision_for(first)]
        ),
    )
    object.__setattr__(bundle, "member_evidence_ids", bundle.member_evidence_ids + ("ev1:document:private",))
    assert_code(
        EvidenceErrorCode.MEMBER_NOT_AUTHORIZED,
        lambda: authorize_bundle_members(bundle, bundle_allowed, [decision_for(first), decision_for(second)]),
    )


def test_attestations_are_separate_bounded_immutable_and_can_contradict():
    item = record()
    before = serialize_evidence_record(item)
    verified = EvidenceAttestation(
        "att1:review:one", AttestationTargetKind.EVIDENCE, item.evidence_id,
        AttestationType.VERIFICATION, VerificationStatus.VERIFIED, "party:reviewer",
        AttestationMethod.MANUAL_REVIEW, NOW, safe_note="Reviewed against source.",
    )
    rejected = EvidenceAttestation(
        "att1:review:two", AttestationTargetKind.EVIDENCE, item.evidence_id,
        AttestationType.VERIFICATION, VerificationStatus.REJECTED, "party:second-reviewer",
        AttestationMethod.MANUAL_REVIEW, NOW + timedelta(minutes=1),
    )
    assert verified.status is VerificationStatus.VERIFIED
    assert rejected.status is VerificationStatus.REJECTED
    assert verified != rejected
    assert serialize_evidence_record(item) == before
    assert "party:reviewer" not in repr(verified)
    assert not hasattr(verified, "legal_acceptance")
    assert not hasattr(verified, "blame")


def test_attestation_requires_literal_status_verifier_method_timestamp_and_safe_note():
    args = dict(
        attestation_id="att1:review:one",
        target_kind=AttestationTargetKind.EVIDENCE,
        target_id="ev1:document:a",
        attestation_type=AttestationType.REVIEW,
        status=VerificationStatus.INCONCLUSIVE,
        verifier_party_ref="party:reviewer",
        method=AttestationMethod.MANUAL_REVIEW,
        timestamp=NOW,
    )
    for key, value, code in (
        ("status", "VERIFIED", EvidenceErrorCode.INVALID_ATTESTATION),
        ("verifier_party_ref", "", EvidenceErrorCode.INVALID_ATTESTATION),
        ("method", "manual", EvidenceErrorCode.INVALID_ATTESTATION),
        ("timestamp", NOW.replace(tzinfo=None), EvidenceErrorCode.INVALID_TIMESTAMP),
        ("safe_note", "token=hidden", EvidenceErrorCode.INVALID_PROVENANCE),
    ):
        changed = {**args, key: value}
        assert_code(code, lambda changed=changed: EvidenceAttestation(**changed))


def test_one_and_multiple_source_derivations_retain_integrity_policy_and_visibility():
    first = record("ev1:document:a", record_scope=scope("ev1:document:a"))
    second = record("ev1:document:b", record_scope=scope("ev1:document:b"), payload=b"other")
    first_decision = decision_for(first, visible_fields=frozenset({"evidence_id", "evidence_kind"}))
    second_decision = decision_for(second, visible_fields=frozenset({"evidence_id"}))
    inherited = inherit_evidence_visibility(
        [first, second], [first_decision, second_decision], TransformationType.SUMMARY
    )
    assert inherited.visible_fields == frozenset({"evidence_id"})
    derived = generated_record(first)
    result = create_evidence_derivation(
        derived, [first, second], [first_decision, second_decision],
        transformation_type=TransformationType.SUMMARY,
        generator_kind=GeneratorKind.SYSTEM,
        generation_timestamp=NOW,
    )
    assert result.source_evidence_ids == (first.evidence_id, second.evidence_id)
    assert [ref.digest for ref in result.source_integrity_references] == [
        first.integrity.digest, second.integrity.digest
    ]
    assert result.source_policy_references[0].policy_version == "v1"


def test_empty_denied_malformed_or_omitted_source_blocks_derivation():
    item = record()
    assert_code(
        EvidenceErrorCode.INVALID_DERIVATION,
        lambda: inherit_evidence_visibility([], [], TransformationType.ANALYSIS),
    )
    assert_code(
        EvidenceErrorCode.SOURCE_NOT_AUTHORIZED,
        lambda: inherit_evidence_visibility([item], [decision_for(item, grant=False)], TransformationType.ANALYSIS),
    )
    assert_code(
        EvidenceErrorCode.SOURCE_NOT_AUTHORIZED,
        lambda: inherit_evidence_visibility([item], [], TransformationType.ANALYSIS),
    )
    assert_code(
        EvidenceErrorCode.INVALID_DERIVATION,
        lambda: inherit_evidence_visibility([object()], [decision_for(item)], TransformationType.ANALYSIS),
    )


@pytest.mark.parametrize(
    "dimension,other",
    [
        ("organization", "org:beta"),
        ("product", "product:beta"),
        ("workspace", "workspace:beta"),
        ("project", "project:beta"),
        ("owner", "party:beta"),
    ],
)
def test_incompatible_source_dimensions_block_derivation(dimension, other):
    first = record("ev1:document:a", record_scope=scope("ev1:document:a"))
    values = dict(organization="org:alpha", product="marketmatch", workspace="workspace:alpha", project="project:alpha", owner="party:alpha")
    values[dimension] = other
    second = record(
        "ev1:document:b",
        record_scope=scope("ev1:document:b", **values),
        owner=values["owner"],
        record_provenance=ProvenanceContext(
            SourceKind.USER, AcquisitionMethod.UPLOAD,
            capturing_party_ref=values["owner"], ingestion_timestamp=NOW - timedelta(minutes=1),
        ),
    )
    assert_code(
        EvidenceErrorCode.SOURCE_SCOPE_CONFLICT,
        lambda: inherit_evidence_visibility(
            [first, second], [decision_for(first), decision_for(second)], TransformationType.REPORT
        ),
    )


def test_incompatible_policies_and_broader_derived_visibility_block():
    first = record("ev1:document:a", record_scope=scope("ev1:document:a"))
    second = record(
        "ev1:document:b", record_scope=scope("ev1:document:b"), policy_id="marketmatch.other"
    )
    assert_code(
        EvidenceErrorCode.SOURCE_POLICY_CONFLICT,
        lambda: inherit_evidence_visibility(
            [first, second], [decision_for(first), decision_for(second)], TransformationType.REPORT
        ),
    )
    public_translation = generated_record(first, classification=ResourceClassification.PUBLIC)
    assert_code(
        EvidenceErrorCode.INVALID_DERIVATION,
        lambda: create_evidence_derivation(
            public_translation, [first], [decision_for(first)],
            transformation_type=TransformationType.TRANSLATION,
            generator_kind=GeneratorKind.HUMAN,
            generation_timestamp=NOW,
        ),
    )


def test_translation_transcript_summary_analysis_and_redaction_never_replace_sources():
    source = record()
    before = serialize_evidence_record(source)
    for transformation in (
        TransformationType.TRANSLATION,
        TransformationType.TRANSCRIPTION,
        TransformationType.SUMMARY,
        TransformationType.ANALYSIS,
        TransformationType.REDACTION,
    ):
        derived = generated_record(source, f"ev1:document:{transformation.value.lower()}")
        result = create_evidence_derivation(
            derived, [source], [decision_for(source)],
            transformation_type=transformation,
            generator_kind=GeneratorKind.SYSTEM,
            generation_timestamp=NOW,
        )
        assert result.derived_evidence_id != source.evidence_id
    assert serialize_evidence_record(source) == before


def test_model_identifier_is_recorded_only_for_real_model_generator():
    source = record()
    derived = generated_record(source)
    result = create_evidence_derivation(
        derived, [source], [decision_for(source)],
        transformation_type=TransformationType.TRANSLATION,
        generator_kind=GeneratorKind.LOCAL_MODEL,
        generator_identifier="model:qwen",
        generator_version="v3",
        generation_timestamp=NOW,
    )
    assert result.generator_identifier == "model:qwen"
    assert not hasattr(result, "model_confidence")
    system_result = create_evidence_derivation(
        derived, [source], [decision_for(source)],
        transformation_type=TransformationType.TRANSLATION,
        generator_kind=GeneratorKind.SYSTEM,
        generator_identifier="generator:translator",
        generator_version="v2",
        generation_timestamp=NOW,
    )
    assert system_result.generator_identifier == "generator:translator"
    assert_code(
        EvidenceErrorCode.INVALID_DERIVATION,
        lambda: create_evidence_derivation(
            derived, [source], [decision_for(source)],
            transformation_type=TransformationType.TRANSLATION,
            generator_kind=GeneratorKind.HUMAN,
            generator_identifier="model:qwen",
            generator_version="v3",
            generation_timestamp=NOW,
        ),
    )
    assert_code(
        EvidenceErrorCode.INVALID_DERIVATION,
        lambda: __import__("src.marketmatch_evidence", fromlist=["EvidenceDerivation"]).EvidenceDerivation(),
    )


def test_authority_results_are_locale_timezone_independent_and_decisions_resist_mutation():
    item = record()
    decisions = [
        decision_for(item, locale=locale, display_timezone=zone)
        for locale, zone in (("es", "UTC"), ("en", "America/New_York"), ("zh-Hans", "Asia/Shanghai"))
    ]
    assert decisions[0] == decisions[1] == decisions[2]
    with pytest.raises(FrozenInstanceError):
        decisions[0].visible_fields = ALL_FIELDS
    assert project_evidence_metadata(item, decisions[0]) == project_evidence_metadata(item, decisions[1])


def test_projection_is_flat_new_nonmutating_and_omits_hidden_or_unknown_values():
    item = record()
    source_before = evidence_metadata(item)
    projected = project_evidence_metadata(item, decision_for(item))
    assert projected is not source_before
    assert set(projected) <= VISIBLE_FIELDS
    assert "original_filename" not in projected
    assert "capturing_party_ref" not in projected
    assert "title" not in projected
    assert evidence_metadata(item) == source_before

    nested = {"hidden": ["supplier"]}
    mapping = evidence_metadata(item)
    mapping.update({
        "description": nested,
        "raw_bytes": b"secret",
        "unknown": "hidden",
    })
    nested_decision = decision_for(item, visible_fields=frozenset({"evidence_id", "evidence_kind", "description"}))
    output = project_evidence_metadata(mapping, nested_decision)
    assert output == {"evidence_id": item.evidence_id, "evidence_kind": item.evidence_kind.value}
    nested["hidden"].append("factory")
    assert output == {"evidence_id": item.evidence_id, "evidence_kind": item.evidence_kind.value}


def test_denied_projection_returns_empty_and_unknown_behavior_is_authority_enforced():
    item = record()
    assert project_evidence_metadata(item, decision_for(item, grant=False)) == {}
    with pytest.raises(Exception):
        project_authorized_fields(
            evidence_metadata(item), decision_for(item), behavior="include"  # type: ignore[arg-type]
        )


def test_projection_rejects_authentic_decision_for_wrong_scope_or_policy():
    item = record()
    wrong_scope_item = record(
        item.evidence_id,
        record_scope=scope(item.evidence_id, project="project:other"),
    )
    assert_code(
        EvidenceErrorCode.INVALID_PROJECTION,
        lambda: project_evidence_metadata(item, decision_for(wrong_scope_item)),
    )
    wrong_policy_item = record(
        item.evidence_id,
        policy_id="marketmatch.other",
    )
    assert_code(
        EvidenceErrorCode.INVALID_PROJECTION,
        lambda: project_evidence_metadata(item, decision_for(wrong_policy_item)),
    )


def test_hostile_mapping_failure_has_fixed_value_free_error():
    item = record()

    class Hostile(Mapping):
        def __iter__(self):
            raise RuntimeError("PRIVATE_SUPPLIER_CANARY")

        def __len__(self):
            return 1

        def __getitem__(self, key):
            raise RuntimeError("PRIVATE_SUPPLIER_CANARY")

        def items(self):
            raise RuntimeError("PRIVATE_SUPPLIER_CANARY")

    with pytest.raises(EvidenceContractError) as caught:
        project_evidence_metadata(Hostile(), decision_for(item))
    assert caught.value.code is EvidenceErrorCode.INVALID_PROJECTION
    assert "PRIVATE_SUPPLIER_CANARY" not in str(caught.value)


def test_safe_audit_contains_only_bounded_codes_and_no_protected_values():
    item = record()
    summary = build_safe_evidence_audit_summary(
        item, decision_for(item), action="evidence.read", timestamp=NOW,
        correlation_id="request:alpha",
    )
    rendered = repr(summary)
    assert summary.evidence_id == item.evidence_id
    for forbidden in (
        "inspection.txt", "Inspection note", "Fictional bounded", "supplier", "factory",
        "address", "cost", "margin", "cookie", "session token", "same bytes",
    ):
        assert forbidden not in rendered


@pytest.mark.parametrize(
    "correlation_id",
    ["request\nforged", "request:address:value", "request:cost:value", "request:margin:value", "x" * 129],
)
def test_safe_audit_rejects_injection_confidential_or_excessive_correlation_ids(correlation_id):
    item = record()
    assert_code(
        EvidenceErrorCode.INVALID_AUDIT,
        lambda: build_safe_evidence_audit_summary(
            item, decision_for(item), action="evidence.read", timestamp=NOW,
            correlation_id=correlation_id,
        ),
    )


def test_safe_audit_rejects_naive_timestamp_and_tampered_decision():
    item = record()
    assert_code(
        EvidenceErrorCode.INVALID_TIMESTAMP,
        lambda: build_safe_evidence_audit_summary(
            item, decision_for(item), action="evidence.read", timestamp=NOW.replace(tzinfo=None)
        ),
    )
    issued = decision_for(item)
    object.__setattr__(issued, "visible_fields", ALL_FIELDS)
    assert_code(
        EvidenceErrorCode.INVALID_AUDIT,
        lambda: build_safe_evidence_audit_summary(
            item, issued, action="evidence.read", timestamp=NOW
        ),
    )


def test_tampered_record_fails_projection_and_audit_without_leaking_changed_value():
    item = record()
    object.__setattr__(item, "evidence_id", "ev1:document:address:private")
    assert_code(
        EvidenceErrorCode.INVALID_RECORD,
        lambda: project_evidence_metadata(item, decision_for(record())),
    )
    with pytest.raises(EvidenceContractError) as caught:
        build_safe_evidence_audit_summary(
            item, decision_for(record()), action="evidence.read", timestamp=NOW
        )
    assert caught.value.code is EvidenceErrorCode.INVALID_AUDIT
    assert "address" not in str(caught.value)


def test_document_adapter_preserves_owner_and_avoids_content_or_digest_claims():
    class CurrentDocument:
        id = "123e4567-e89b-12d3-a456-426614174000"
        owner = "alice"
        created_at = NOW.replace(tzinfo=None)
        title = "Fictional report"
        language = "markdown"

        @property
        def current_content(self):
            raise AssertionError("generic adapter must not read content")

    result = document_to_evidence(
        CurrentDocument(), visibility_policy_id="marketmatch.evidence",
        visibility_policy_version="v1",
    )
    assert result.owner_party_ref == "legacy-user:alice"
    assert result.integrity.basis is IntegrityBasis.NOT_AVAILABLE
    assert result.integrity.digest is None


def test_capture_adapter_requires_current_marker_and_preserves_text_only_boundary():
    document = SimpleNamespace(
        id="123e4567-e89b-12d3-a456-426614174001",
        owner="alice",
        created_at=NOW.replace(tzinfo=None),
        title="Site walkthrough",
        language="markdown",
        current_content="# MarketMatch Capture\n\nText only",
    )
    result = capture_document_to_evidence(
        document, visibility_policy_id="marketmatch.evidence", visibility_policy_version="v1"
    )
    assert result.provenance.source_kind is SourceKind.CAPTURE
    assert result.media_kind is MediaKind.TEXT
    assert result.integrity.basis is IntegrityBasis.NOT_AVAILABLE
    document.current_content = "Not a capture"
    assert_code(
        EvidenceErrorCode.INVALID_COMPATIBILITY_RECORD,
        lambda: capture_document_to_evidence(
            document, visibility_policy_id="marketmatch.evidence", visibility_policy_version="v1"
        ),
    )


def test_upload_adapter_preserves_recorded_hash_as_metadata_only_and_requires_explicit_time():
    metadata = {
        "id": "a" * 32 + ".txt",
        "owner": "alice",
        "hash": hashlib.sha256(b"uploaded").hexdigest(),
        "size": 8,
        "mime": "text/plain",
        "name": "upload.txt",
        "original_name": "../../not-trusted.txt",
        "path": "/private/path/not-projected",
        "client_ip": "192.0.2.1",
    }
    result = upload_metadata_to_evidence(
        metadata,
        evidence_kind=EvidenceKind.DOCUMENT,
        media_kind=MediaKind.TEXT,
        ingestion_timestamp=NOW,
        visibility_policy_id="marketmatch.evidence",
        visibility_policy_version="v1",
    )
    assert result.integrity.basis is IntegrityBasis.METADATA_ONLY
    assert result.integrity.digest == metadata["hash"]
    serialized = serialize_evidence_record(result)
    assert b"/private/path" not in serialized and b"192.0.2.1" not in serialized
    assert_code(
        EvidenceErrorCode.INVALID_COMPATIBILITY_RECORD,
        lambda: upload_metadata_to_evidence(
            metadata,
            evidence_kind=EvidenceKind.DOCUMENT,
            media_kind=MediaKind.TEXT,
            ingestion_timestamp=NOW.replace(tzinfo=None),
            visibility_policy_id="marketmatch.evidence",
            visibility_policy_version="v1",
        ),
    )


def test_media_contract_adapters_do_not_upgrade_declarations_to_byte_verified():
    digest = hashlib.sha256(b"media").hexdigest()
    attestation = ValidatedOriginalMediaAttestation(
        schema="marketmatch-original-media-attestation-v1",
        domain=MediaDomain.CALLS,
        operation_id="mmop-calls-" + "a" * 26,
        media_id="mmmedia-calls-" + "b" * 26,
        media_role=OriginalMediaRole.ORIGINAL_AUDIO,
        byte_size=5,
        sha256=digest,
        authority=AttestationAuthority.VERIFIED_INGEST,
        attestation_digest="c" * 64,
        canonical_bytes=b"{}",
    )
    observation = OriginalMediaObservation(byte_size=5, sha256=digest)
    assert media_attestation_to_integrity(attestation).basis is IntegrityBasis.METADATA_ONLY
    assert media_observation_to_integrity(observation).basis is IntegrityBasis.METADATA_ONLY


def test_pure_core_has_no_remote_model_storage_database_or_filesystem_writes():
    roots = [Path("src/marketmatch_evidence.py"), Path("src/marketmatch_evidence_compat.py")]
    forbidden_import_roots = {
        "requests", "httpx", "fastapi", "sqlalchemy", "sqlite3", "subprocess", "openai",
        "anthropic", "chromadb", "localStorage", "sessionStorage",
    }
    for path in roots:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            node.names[0].name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom)) and node.names
        }
        assert not (imports & forbidden_import_roots)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        assert not any(isinstance(node.func, ast.Name) and node.func.id == "open" for node in calls)


def test_no_production_route_imports_evidence_core_and_contracts_make_no_calls_or_writes():
    for path in Path("routes").glob("*.py"):
        assert "marketmatch_evidence" not in path.read_text(encoding="utf-8")
    item = record()
    with mock.patch("builtins.open", side_effect=AssertionError("no filesystem write")):
        with mock.patch("socket.socket", side_effect=AssertionError("no remote call")):
            assert serialize_evidence_record(item)


def test_evidence_adr_documents_contract_glossary_current_target_and_deferrals():
    text = Path("docs/adr/0002-marketmatch-evidence-core-v1.md").read_text(encoding="utf-8")
    required = (
        "Evidence identity versus digest",
        "Content integrity",
        "Provenance and timestamp semantics",
        "Relations",
        "Bundles",
        "Attestations",
        "Derived artifacts",
        "Authority and visibility inheritance",
        "Safe projection",
        "Safe audit boundary",
        "Compatibility adapters",
        "Current state versus target state",
        "Explicitly deferred and limited",
        "no durable Evidence registry",
        "no public route",
        "no universal sourcing/procurement",
        "Evidence is not a conclusion",
    )
    for item in required:
        assert item in text
    for code in (
        [item.value for item in EvidenceKind]
        + [item.value for item in IntegrityBasis]
        + [item.value for item in RelationType]
        + [item.value for item in AttestationType]
        + [item.value for item in VerificationStatus]
        + [item.value for item in TransformationType]
    ):
        assert f"`{code}`" in text
