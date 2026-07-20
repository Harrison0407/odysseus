"""Pure MarketMatch Operational Context Kernel V1 contracts.

The kernel models canonical operational subjects and their graph.  It performs
no authentication, persistence, I/O, localization, workflow evaluation, or
model invocation.  Authority decisions and Evidence records remain governed by
their existing kernels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Mapping, NoReturn, Sequence

from src.marketmatch_authority import (
    AuthorityContractError,
    AuthorityReason,
    AuthorityScope,
    AuthorizationDecision,
    DerivedArtifactKind,
    DerivedVisibility,
    ProjectionBehavior,
    ResourceClassification,
    SourceVisibility,
    inherit_derived_visibility,
    project_authorized_fields,
)
from src.marketmatch_evidence import (
    EvidenceContractError,
    EvidenceRecord,
    evidence_metadata,
    project_evidence_metadata,
    validate_evidence_id,
)


OPERATIONAL_CONTEXT_CONTRACT_VERSION = "marketmatch-operational-context-v1"
OPERATIONAL_CONTEXT_POLICY_VERSION = "marketmatch-operational-context-policy-v1"
MAX_CONTEXTS = 2048
MAX_RELATIONSHIPS = 8192
MAX_ALIASES_PER_CONTEXT = 64
MAX_EVIDENCE_ASSOCIATIONS = 8192
MAX_ANCESTRY_DEPTH = 512

_ID_RE = re.compile(r"[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*\Z", re.ASCII)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_ALIAS_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._:#-]*\Z", re.ASCII)
_ERROR_RE = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*\Z", re.ASCII)
_SECRET_RE = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:password|passwd|secret|token|cookie|session)\s*[:=])"
)
_MAX_CONTEXT_ID_BYTES = 160
_MAX_CODE_BYTES = 128
_MAX_ALIAS_CHARS = 160
_MAX_NAME_CHARS = 240
_SENSITIVE_ID_PARTS = frozenset(
    {
        "address", "avenue", "cookie", "cost", "credential", "database",
        "factory", "margin", "markup", "password", "row", "secret",
        "session", "street", "supplier", "token",
    }
)
_CONTEXT_MARKER = object()
_ALIAS_MARKER = object()
_RELATION_MARKER = object()
_ASSOCIATION_MARKER = object()
_GRAPH_MARKER = object()


class ContextErrorCode(str, Enum):
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    INVALID_TYPE = "INVALID_TYPE"
    INVALID_SCOPE = "INVALID_SCOPE"
    INVALID_RECORD = "INVALID_RECORD"
    INVALID_ALIAS = "INVALID_ALIAS"
    ALIAS_COLLISION = "ALIAS_COLLISION"
    INVALID_HIERARCHY = "INVALID_HIERARCHY"
    MISSING_PARENT = "MISSING_PARENT"
    HIERARCHY_CYCLE = "HIERARCHY_CYCLE"
    GRAPH_LIMIT_EXCEEDED = "GRAPH_LIMIT_EXCEEDED"
    DUPLICATE_CONTEXT = "DUPLICATE_CONTEXT"
    INVALID_RELATIONSHIP = "INVALID_RELATIONSHIP"
    RELATIONSHIP_CONFLICT = "RELATIONSHIP_CONFLICT"
    INVALID_ANCESTRY = "INVALID_ANCESTRY"
    INVALID_EVIDENCE_ASSOCIATION = "INVALID_EVIDENCE_ASSOCIATION"
    EVIDENCE_SCOPE_CONFLICT = "EVIDENCE_SCOPE_CONFLICT"
    EVIDENCE_POLICY_CONFLICT = "EVIDENCE_POLICY_CONFLICT"
    EVIDENCE_NOT_AUTHORIZED = "EVIDENCE_NOT_AUTHORIZED"
    INVALID_AUTHORITY_DECISION = "INVALID_AUTHORITY_DECISION"
    INVALID_PROJECTION = "INVALID_PROJECTION"
    INVALID_AUDIT = "INVALID_AUDIT"
    INVALID_VERSION = "INVALID_VERSION"
    INVALID_REVISION = "INVALID_REVISION"
    SOURCE_NOT_AUTHORIZED = "SOURCE_NOT_AUTHORIZED"
    SOURCE_SCOPE_CONFLICT = "SOURCE_SCOPE_CONFLICT"
    SOURCE_POLICY_CONFLICT = "SOURCE_POLICY_CONFLICT"


class OperationalContextError(ValueError):
    """Fixed-code error that never serializes rejected values."""

    def __init__(self, code: ContextErrorCode | str):
        value = code.value if type(code) is ContextErrorCode else code
        self.code = value if type(value) is str and _ERROR_RE.fullmatch(value) else "INVALID_CONTEXT"
        super().__init__(self.code)


class ContextType(str, Enum):
    """Small V1 set proven by the committed Authority scope contract."""

    PRODUCT = "PRODUCT"
    WORKSPACE = "WORKSPACE"
    PROJECT = "PROJECT"


class AliasNamespace(str, Enum):
    LEGACY_RESOURCE_ID = "LEGACY_RESOURCE_ID"
    EXTERNAL_SYSTEM_ID = "EXTERNAL_SYSTEM_ID"
    PROJECT_CODE = "PROJECT_CODE"


class ContextRelationshipType(str, Enum):
    ASSOCIATED_WITH = "ASSOCIATED_WITH"
    REPLACES = "REPLACES"
    SUPERSEDES = "SUPERSEDES"


class EvidenceAssociationPurpose(str, Enum):
    ASSOCIATED_WITH = "ASSOCIATED_WITH"


def _fail(code: ContextErrorCode) -> NoReturn:
    raise OperationalContextError(code) from None


def _parts(value: str) -> frozenset[str]:
    return frozenset(part for part in re.split(r"[._:-]+", value.lower()) if part)


def _valid_code(value: object, *, maximum: int = _MAX_CODE_BYTES, sensitive: bool = True) -> bool:
    if type(value) is not str or not value or _ID_RE.fullmatch(value) is None:
        return False
    try:
        if len(value.encode("ascii")) > maximum:
            return False
    except UnicodeError:
        return False
    return not sensitive or not (_parts(value) & _SENSITIVE_ID_PARTS)


def _require_code(
    value: object,
    error: ContextErrorCode,
    *,
    maximum: int = _MAX_CODE_BYTES,
    sensitive: bool = True,
) -> str:
    if not _valid_code(value, maximum=maximum, sensitive=sensitive):
        _fail(error)
    return value


def validate_context_id(value: object) -> str:
    opaque = value.split(":", 2)[2] if type(value) is str and value.count(":") >= 2 else ""
    if (
        not _valid_code(value, maximum=_MAX_CONTEXT_ID_BYTES)
        or not value.startswith("ctx1:")
        or value.count(":") < 2
        or value.startswith("ev1:")
        or not any("a" <= char <= "z" for char in opaque)
        or _SHA256_RE.fullmatch(opaque) is not None
    ):
        _fail(ContextErrorCode.INVALID_IDENTIFIER)
    return value


def _utc(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(ContextErrorCode.INVALID_RECORD)
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        _fail(ContextErrorCode.INVALID_RECORD)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_name(value: object) -> str:
    if type(value) is not str or not value or len(value) > _MAX_NAME_CHARS:
        _fail(ContextErrorCode.INVALID_RECORD)
    if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        _fail(ContextErrorCode.INVALID_RECORD)
    if _SECRET_RE.search(value):
        _fail(ContextErrorCode.INVALID_RECORD)
    return value


def _scope_tuple(scope: AuthorityScope) -> tuple[object, ...]:
    return (
        scope.organization_id,
        scope.product_id,
        scope.workspace_id,
        scope.project_id,
        scope.resource_id,
        scope.owner_party_id,
        scope.global_scope,
    )


def _scope_probe(scope: object, resource_id: str) -> bool:
    if type(scope) is not AuthorityScope or scope.global_scope is not False:
        return False
    if scope.organization_id is None or scope.product_id is None or scope.resource_id != resource_id:
        return False
    try:
        probe = inherit_derived_visibility(
            (
                SourceVisibility(
                    source_resource_id=resource_id,
                    classification=ResourceClassification.RESTRICTED,
                    visible_fields=frozenset(),
                    required_scopes=(scope,),
                    authorized=True,
                    policy_id="operational.context.scope",
                    policy_version="v1",
                ),
            ),
            artifact_kind=DerivedArtifactKind.ANALYSIS,
        )
        return probe.allowed is True
    except Exception:
        return False


def _scope_shape(context_type: ContextType, scope: AuthorityScope) -> bool:
    if context_type is ContextType.PRODUCT:
        return scope.workspace_id is None and scope.project_id is None
    if context_type is ContextType.WORKSPACE:
        return scope.workspace_id is not None and scope.project_id is None
    if context_type is ContextType.PROJECT:
        return scope.project_id is not None
    return False


def _same_exact_operational_scope(left: AuthorityScope, right: AuthorityScope) -> bool:
    return all(
        getattr(left, name) == getattr(right, name)
        for name in (
            "organization_id", "product_id", "workspace_id", "project_id", "owner_party_id"
        )
    )


def _parent_scope_compatible(parent: "OperationalContextRecord", child: "OperationalContextRecord") -> bool:
    ps, cs = parent.scope, child.scope
    if ps.organization_id != cs.organization_id or ps.product_id != cs.product_id:
        return False
    if ps.owner_party_id != cs.owner_party_id:
        return False
    if parent.context_type is ContextType.PRODUCT:
        return ps.workspace_id is None and ps.project_id is None
    if parent.context_type is ContextType.WORKSPACE:
        return ps.workspace_id == cs.workspace_id and ps.project_id is None
    if parent.context_type is ContextType.PROJECT:
        return ps.workspace_id == cs.workspace_id and ps.project_id == cs.project_id
    return False


@dataclass(frozen=True, slots=True)
class ContextAlias:
    namespace: AliasNamespace
    value: str = field(repr=False)
    sensitive: bool = True
    _marker: object = field(init=False, repr=False, compare=False)
    _integrity: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.namespace) is not AliasNamespace or type(self.sensitive) is not bool:
            _fail(ContextErrorCode.INVALID_ALIAS)
        if (
            type(self.value) is not str
            or not 1 <= len(self.value) <= _MAX_ALIAS_CHARS
            or self.value != self.value.strip()
            or _ALIAS_RE.fullmatch(self.value) is None
            or ".." in self.value
            or _SECRET_RE.search(self.value)
        ):
            _fail(ContextErrorCode.INVALID_ALIAS)
        object.__setattr__(self, "_marker", _ALIAS_MARKER)
        object.__setattr__(self, "_integrity", _alias_tuple(self))

    @property
    def collision_key(self) -> tuple[str, str]:
        return (self.namespace.value, self.value.casefold())


def _alias_tuple(value: ContextAlias) -> tuple[object, ...]:
    return (value.namespace, value.value, value.sensitive)


def _valid_alias(value: object) -> bool:
    try:
        return (
            type(value) is ContextAlias
            and value._marker is _ALIAS_MARKER
            and value._integrity == _alias_tuple(value)
        )
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class OperationalContextRecord:
    context_id: str
    context_type: ContextType
    scope: AuthorityScope
    created_at: datetime
    classification: ResourceClassification
    visibility_policy_id: str
    visibility_policy_version: str
    parent_context_id: str | None = None
    owner_party_ref: str | None = field(default=None, repr=False)
    display_name: str | None = field(default=None, repr=False)
    aliases: tuple[ContextAlias, ...] = field(default=(), repr=False)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    revision: int = 1
    previous_revision: int | None = None
    contract_version: str = OPERATIONAL_CONTEXT_CONTRACT_VERSION
    _marker: object = field(init=False, repr=False, compare=False)
    _integrity: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        validate_context_id(self.context_id)
        if type(self.context_type) is not ContextType:
            _fail(ContextErrorCode.INVALID_TYPE)
        if not _scope_probe(self.scope, self.context_id) or not _scope_shape(self.context_type, self.scope):
            _fail(ContextErrorCode.INVALID_SCOPE)
        if type(self.classification) is not ResourceClassification:
            _fail(ContextErrorCode.INVALID_RECORD)
        if self.contract_version != OPERATIONAL_CONTEXT_CONTRACT_VERSION:
            _fail(ContextErrorCode.INVALID_VERSION)
        _require_code(self.visibility_policy_id, ContextErrorCode.INVALID_RECORD)
        _require_code(
            self.visibility_policy_version,
            ContextErrorCode.INVALID_RECORD,
            sensitive=False,
        )
        if self.parent_context_id is not None:
            validate_context_id(self.parent_context_id)
            if self.parent_context_id == self.context_id:
                _fail(ContextErrorCode.INVALID_HIERARCHY)
        if self.owner_party_ref is not None:
            _require_code(self.owner_party_ref, ContextErrorCode.INVALID_RECORD)
        if self.scope.owner_party_id is not None and self.scope.owner_party_id != self.owner_party_ref:
            _fail(ContextErrorCode.INVALID_SCOPE)
        if self.display_name is not None:
            _safe_name(self.display_name)
            if self.display_name == self.context_id:
                _fail(ContextErrorCode.INVALID_IDENTIFIER)
        if type(self.aliases) is not tuple or len(self.aliases) > MAX_ALIASES_PER_CONTEXT:
            _fail(ContextErrorCode.INVALID_ALIAS)
        if any(not _valid_alias(alias) for alias in self.aliases):
            _fail(ContextErrorCode.INVALID_ALIAS)
        ordered_aliases = tuple(sorted(set(self.aliases), key=lambda item: item.collision_key))
        if len({alias.collision_key for alias in ordered_aliases}) != len(ordered_aliases):
            _fail(ContextErrorCode.ALIAS_COLLISION)
        if any(alias.value == self.context_id for alias in ordered_aliases):
            _fail(ContextErrorCode.INVALID_IDENTIFIER)
        object.__setattr__(self, "aliases", ordered_aliases)
        created = _utc(self.created_at)
        start = created if self.valid_from is None else _utc(self.valid_from)
        end = None if self.valid_until is None else _utc(self.valid_until)
        if end is not None and end <= start:
            _fail(ContextErrorCode.INVALID_RECORD)
        if created > start:
            _fail(ContextErrorCode.INVALID_RECORD)
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "valid_from", start)
        object.__setattr__(self, "valid_until", end)
        if type(self.revision) is not int or self.revision < 1:
            _fail(ContextErrorCode.INVALID_REVISION)
        if self.revision == 1:
            if self.previous_revision is not None:
                _fail(ContextErrorCode.INVALID_REVISION)
        elif type(self.previous_revision) is not int or self.previous_revision != self.revision - 1:
            _fail(ContextErrorCode.INVALID_REVISION)
        object.__setattr__(self, "_marker", _CONTEXT_MARKER)
        object.__setattr__(self, "_integrity", _record_tuple(self))


def _record_tuple(value: OperationalContextRecord) -> tuple[object, ...]:
    return (
        value.context_id,
        value.context_type,
        _scope_tuple(value.scope),
        value.created_at,
        value.classification,
        value.visibility_policy_id,
        value.visibility_policy_version,
        value.parent_context_id,
        value.owner_party_ref,
        value.display_name,
        tuple(alias._integrity for alias in value.aliases),
        value.valid_from,
        value.valid_until,
        value.revision,
        value.previous_revision,
        value.contract_version,
    )


def _valid_record(value: object) -> bool:
    try:
        return (
            type(value) is OperationalContextRecord
            and value._marker is _CONTEXT_MARKER
            and all(_valid_alias(alias) for alias in value.aliases)
            and value._integrity == _record_tuple(value)
        )
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class HierarchyPolicy:
    policy_id: str
    version: str
    allowed_parent_child_types: frozenset[tuple[ContextType, ContextType]]

    def __post_init__(self) -> None:
        _require_code(self.policy_id, ContextErrorCode.INVALID_HIERARCHY)
        _require_code(self.version, ContextErrorCode.INVALID_HIERARCHY, sensitive=False)
        if type(self.allowed_parent_child_types) is not frozenset or any(
            type(pair) is not tuple
            or len(pair) != 2
            or type(pair[0]) is not ContextType
            or type(pair[1]) is not ContextType
            for pair in self.allowed_parent_child_types
        ):
            _fail(ContextErrorCode.INVALID_HIERARCHY)


def _hierarchy_policy_tuple(value: HierarchyPolicy) -> tuple[object, ...]:
    return (
        value.policy_id,
        value.version,
        tuple(sorted((parent.value, child.value) for parent, child in value.allowed_parent_child_types)),
    )


def _valid_hierarchy_policy(value: object) -> bool:
    try:
        return (
            type(value) is HierarchyPolicy
            and _valid_code(value.policy_id)
            and _valid_code(value.version, sensitive=False)
            and type(value.allowed_parent_child_types) is frozenset
            and all(
                type(pair) is tuple
                and len(pair) == 2
                and type(pair[0]) is ContextType
                and type(pair[1]) is ContextType
                for pair in value.allowed_parent_child_types
            )
        )
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class ContextRelationship:
    source_context_id: str
    target_context_id: str
    relationship_type: ContextRelationshipType
    created_at: datetime
    creator_party_ref: str | None = field(default=None, repr=False)
    reason_code: str | None = None
    policy_version: str = OPERATIONAL_CONTEXT_POLICY_VERSION
    contract_version: str = OPERATIONAL_CONTEXT_CONTRACT_VERSION
    _marker: object = field(init=False, repr=False, compare=False)
    _integrity: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        validate_context_id(self.source_context_id)
        validate_context_id(self.target_context_id)
        if self.source_context_id == self.target_context_id:
            _fail(ContextErrorCode.INVALID_RELATIONSHIP)
        if type(self.relationship_type) is not ContextRelationshipType:
            _fail(ContextErrorCode.INVALID_RELATIONSHIP)
        if self.relationship_type is ContextRelationshipType.ASSOCIATED_WITH:
            source, target = sorted((self.source_context_id, self.target_context_id))
            object.__setattr__(self, "source_context_id", source)
            object.__setattr__(self, "target_context_id", target)
        object.__setattr__(self, "created_at", _utc(self.created_at))
        if self.creator_party_ref is not None:
            _require_code(self.creator_party_ref, ContextErrorCode.INVALID_RELATIONSHIP)
        if self.reason_code is not None:
            _require_code(self.reason_code, ContextErrorCode.INVALID_RELATIONSHIP, sensitive=False)
        if self.policy_version != OPERATIONAL_CONTEXT_POLICY_VERSION:
            _fail(ContextErrorCode.INVALID_VERSION)
        if self.contract_version != OPERATIONAL_CONTEXT_CONTRACT_VERSION:
            _fail(ContextErrorCode.INVALID_VERSION)
        object.__setattr__(self, "_marker", _RELATION_MARKER)
        object.__setattr__(self, "_integrity", _relationship_tuple(self))


def _relationship_tuple(value: ContextRelationship) -> tuple[object, ...]:
    return (
        value.source_context_id,
        value.target_context_id,
        value.relationship_type,
        value.created_at,
        value.creator_party_ref,
        value.reason_code,
        value.policy_version,
        value.contract_version,
    )


def _valid_relationship(value: object) -> bool:
    try:
        return (
            type(value) is ContextRelationship
            and value._marker is _RELATION_MARKER
            and value._integrity == _relationship_tuple(value)
        )
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class EvidenceAssociation:
    context_id: str
    evidence_id: str
    purpose: EvidenceAssociationPurpose
    created_at: datetime
    creator_party_ref: str | None = field(default=None, repr=False)
    policy_version: str = OPERATIONAL_CONTEXT_POLICY_VERSION
    contract_version: str = OPERATIONAL_CONTEXT_CONTRACT_VERSION
    _marker: object = field(init=False, repr=False, compare=False)
    _integrity: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        validate_context_id(self.context_id)
        try:
            validate_evidence_id(self.evidence_id)
        except EvidenceContractError:
            _fail(ContextErrorCode.INVALID_EVIDENCE_ASSOCIATION)
        if type(self.purpose) is not EvidenceAssociationPurpose:
            _fail(ContextErrorCode.INVALID_EVIDENCE_ASSOCIATION)
        object.__setattr__(self, "created_at", _utc(self.created_at))
        if self.creator_party_ref is not None:
            _require_code(self.creator_party_ref, ContextErrorCode.INVALID_EVIDENCE_ASSOCIATION)
        if self.policy_version != OPERATIONAL_CONTEXT_POLICY_VERSION:
            _fail(ContextErrorCode.INVALID_VERSION)
        if self.contract_version != OPERATIONAL_CONTEXT_CONTRACT_VERSION:
            _fail(ContextErrorCode.INVALID_VERSION)
        object.__setattr__(self, "_marker", _ASSOCIATION_MARKER)
        object.__setattr__(self, "_integrity", _association_tuple(self))


def _association_tuple(value: EvidenceAssociation) -> tuple[object, ...]:
    return (
        value.context_id,
        value.evidence_id,
        value.purpose,
        value.created_at,
        value.creator_party_ref,
        value.policy_version,
        value.contract_version,
    )


def _valid_association(value: object) -> bool:
    try:
        return (
            type(value) is EvidenceAssociation
            and value._marker is _ASSOCIATION_MARKER
            and value._integrity == _association_tuple(value)
        )
    except Exception:
        return False


@dataclass(frozen=True, slots=True, init=False)
class ContextGraph:
    contexts: tuple[OperationalContextRecord, ...]
    relationships: tuple[ContextRelationship, ...]
    hierarchy_policy: HierarchyPolicy
    _marker: object = field(repr=False, compare=False)
    _integrity: tuple[object, ...] = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs
        _fail(ContextErrorCode.INVALID_HIERARCHY)


def _graph_tuple(value: ContextGraph) -> tuple[object, ...]:
    return (
        tuple(record._integrity for record in value.contexts),
        tuple(relation._integrity for relation in value.relationships),
        _hierarchy_policy_tuple(value.hierarchy_policy),
    )


def _valid_graph(value: object) -> bool:
    try:
        return (
            type(value) is ContextGraph
            and value._marker is _GRAPH_MARKER
            and all(_valid_record(item) for item in value.contexts)
            and all(_valid_relationship(item) for item in value.relationships)
            and _valid_hierarchy_policy(value.hierarchy_policy)
            and value._integrity == _graph_tuple(value)
        )
    except Exception:
        return False


def _assert_acyclic(edges: Mapping[str, set[str]], error: ContextErrorCode) -> None:
    state: dict[str, int] = {}
    for start in sorted(edges):
        if state.get(start) == 2:
            continue
        stack: list[tuple[str, bool]] = [(start, False)]
        while stack:
            node, exiting = stack.pop()
            if exiting:
                state[node] = 2
                continue
            if state.get(node) == 1:
                _fail(error)
            if state.get(node) == 2:
                continue
            state[node] = 1
            stack.append((node, True))
            for target in sorted(edges.get(node, ()), reverse=True):
                if state.get(target) == 1:
                    _fail(error)
                if state.get(target) != 2:
                    stack.append((target, False))


def validate_context_graph(
    contexts: Sequence[OperationalContextRecord],
    relationships: Sequence[ContextRelationship],
    *,
    hierarchy_policy: HierarchyPolicy,
) -> ContextGraph:
    if type(contexts) not in (list, tuple) or not contexts:
        _fail(ContextErrorCode.INVALID_HIERARCHY)
    if len(contexts) > MAX_CONTEXTS:
        _fail(ContextErrorCode.GRAPH_LIMIT_EXCEEDED)
    if type(relationships) not in (list, tuple) or len(relationships) > MAX_RELATIONSHIPS:
        _fail(ContextErrorCode.GRAPH_LIMIT_EXCEEDED)
    if not _valid_hierarchy_policy(hierarchy_policy):
        _fail(ContextErrorCode.INVALID_HIERARCHY)
    if any(not _valid_record(item) for item in contexts):
        _fail(ContextErrorCode.INVALID_RECORD)
    ids = [item.context_id for item in contexts]
    if len(set(ids)) != len(ids):
        _fail(ContextErrorCode.DUPLICATE_CONTEXT)
    records = {item.context_id: item for item in contexts}

    alias_keys: set[tuple[object, ...]] = set()
    parent_edges: dict[str, set[str]] = {}
    for child in contexts:
        boundary = (
            child.scope.organization_id,
            child.scope.product_id,
            child.scope.workspace_id,
            child.scope.project_id,
        )
        for alias in child.aliases:
            key = boundary + alias.collision_key
            if key in alias_keys:
                _fail(ContextErrorCode.ALIAS_COLLISION)
            alias_keys.add(key)
        if child.parent_context_id is None:
            continue
        parent = records.get(child.parent_context_id)
        if parent is None:
            _fail(ContextErrorCode.MISSING_PARENT)
        if (parent.context_type, child.context_type) not in hierarchy_policy.allowed_parent_child_types:
            _fail(ContextErrorCode.INVALID_HIERARCHY)
        if not _parent_scope_compatible(parent, child):
            _fail(ContextErrorCode.INVALID_SCOPE)
        parent_edges.setdefault(child.context_id, set()).add(parent.context_id)
    _assert_acyclic(parent_edges, ContextErrorCode.HIERARCHY_CYCLE)

    unique: dict[tuple[object, ...], ContextRelationship] = {}
    directional_pairs: dict[tuple[str, str], ContextRelationshipType] = {}
    supersession_edges: dict[str, set[str]] = {}
    for relationship in relationships:
        if not _valid_relationship(relationship):
            _fail(ContextErrorCode.INVALID_RELATIONSHIP)
        source = records.get(relationship.source_context_id)
        target = records.get(relationship.target_context_id)
        if source is None or target is None:
            _fail(ContextErrorCode.INVALID_RELATIONSHIP)
        if not _same_exact_operational_scope(source.scope, target.scope):
            _fail(ContextErrorCode.INVALID_SCOPE)
        if relationship.created_at < source.created_at or relationship.created_at < target.created_at:
            _fail(ContextErrorCode.INVALID_RELATIONSHIP)
        key = (
            relationship.source_context_id,
            relationship.target_context_id,
            relationship.relationship_type,
            relationship.created_at,
            relationship.creator_party_ref,
            relationship.reason_code,
        )
        unique[key] = relationship
        if relationship.relationship_type in {
            ContextRelationshipType.REPLACES,
            ContextRelationshipType.SUPERSEDES,
        }:
            pair = (relationship.source_context_id, relationship.target_context_id)
            previous_type = directional_pairs.setdefault(pair, relationship.relationship_type)
            if previous_type is not relationship.relationship_type:
                _fail(ContextErrorCode.RELATIONSHIP_CONFLICT)
            supersession_edges.setdefault(relationship.source_context_id, set()).add(
                relationship.target_context_id
            )
    _assert_acyclic(supersession_edges, ContextErrorCode.RELATIONSHIP_CONFLICT)
    ordered_contexts = tuple(sorted(contexts, key=lambda item: item.context_id))
    ordered_relationships = tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.source_context_id,
                item.target_context_id,
                item.relationship_type.value,
                item.created_at.isoformat(),
            ),
        )
    )
    graph = object.__new__(ContextGraph)
    object.__setattr__(graph, "contexts", ordered_contexts)
    object.__setattr__(graph, "relationships", ordered_relationships)
    object.__setattr__(graph, "hierarchy_policy", hierarchy_policy)
    object.__setattr__(graph, "_marker", _GRAPH_MARKER)
    object.__setattr__(graph, "_integrity", _graph_tuple(graph))
    return graph


def context_ancestry(graph: ContextGraph, context_id: str) -> tuple[str, ...]:
    if not _valid_graph(graph):
        _fail(ContextErrorCode.INVALID_ANCESTRY)
    validate_context_id(context_id)
    records = {record.context_id: record for record in graph.contexts}
    if context_id not in records:
        _fail(ContextErrorCode.INVALID_ANCESTRY)
    path: list[str] = []
    seen: set[str] = set()
    current: str | None = context_id
    while current is not None:
        if current in seen or len(path) >= MAX_ANCESTRY_DEPTH:
            _fail(ContextErrorCode.INVALID_ANCESTRY)
        seen.add(current)
        path.append(current)
        record = records.get(current)
        if record is None:
            _fail(ContextErrorCode.INVALID_ANCESTRY)
        current = record.parent_context_id
    path.reverse()
    return tuple(path)


def validate_revision_transition(
    previous: OperationalContextRecord,
    current: OperationalContextRecord,
) -> None:
    if not _valid_record(previous) or not _valid_record(current):
        _fail(ContextErrorCode.INVALID_REVISION)
    if (
        previous.context_id != current.context_id
        or previous.context_type is not current.context_type
        or previous.scope != current.scope
        or current.revision != previous.revision + 1
        or current.previous_revision != previous.revision
        or current.created_at < previous.created_at
        or current.valid_from < previous.valid_from
        or (
            previous.valid_until is not None
            and current.valid_from < previous.valid_until
        )
    ):
        _fail(ContextErrorCode.INVALID_REVISION)


def validate_evidence_associations(
    contexts: Sequence[OperationalContextRecord],
    evidence_records: Sequence[EvidenceRecord],
    associations: Sequence[EvidenceAssociation],
) -> tuple[EvidenceAssociation, ...]:
    if (
        type(contexts) not in (list, tuple)
        or type(evidence_records) not in (list, tuple)
        or type(associations) not in (list, tuple)
        or len(associations) > MAX_EVIDENCE_ASSOCIATIONS
    ):
        _fail(ContextErrorCode.INVALID_EVIDENCE_ASSOCIATION)
    if any(not _valid_record(item) for item in contexts):
        _fail(ContextErrorCode.INVALID_RECORD)
    context_by_id = {item.context_id: item for item in contexts}
    if len(context_by_id) != len(contexts):
        _fail(ContextErrorCode.DUPLICATE_CONTEXT)
    evidence_by_id: dict[str, EvidenceRecord] = {}
    for evidence in evidence_records:
        try:
            evidence_metadata(evidence)
        except Exception:
            _fail(ContextErrorCode.INVALID_EVIDENCE_ASSOCIATION)
        if evidence.evidence_id in evidence_by_id:
            _fail(ContextErrorCode.INVALID_EVIDENCE_ASSOCIATION)
        evidence_by_id[evidence.evidence_id] = evidence
    unique: dict[tuple[str, str, EvidenceAssociationPurpose], EvidenceAssociation] = {}
    for association in associations:
        if not _valid_association(association):
            _fail(ContextErrorCode.INVALID_EVIDENCE_ASSOCIATION)
        context = context_by_id.get(association.context_id)
        evidence = evidence_by_id.get(association.evidence_id)
        if context is None or evidence is None:
            _fail(ContextErrorCode.INVALID_EVIDENCE_ASSOCIATION)
        if association.created_at < context.created_at or association.created_at < evidence.created_at:
            _fail(ContextErrorCode.INVALID_EVIDENCE_ASSOCIATION)
        if not _same_exact_operational_scope(context.scope, evidence.scope):
            _fail(ContextErrorCode.EVIDENCE_SCOPE_CONFLICT)
        if (
            context.visibility_policy_id != evidence.visibility_policy_id
            or context.visibility_policy_version != evidence.visibility_policy_version
        ):
            _fail(ContextErrorCode.EVIDENCE_POLICY_CONFLICT)
        unique[(association.context_id, association.evidence_id, association.purpose)] = association
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (item.context_id, item.evidence_id, item.purpose.value),
        )
    )


def _validate_context_decision(
    decision: AuthorizationDecision,
    record: OperationalContextRecord,
) -> None:
    try:
        project_authorized_fields({}, decision, behavior=ProjectionBehavior.OMIT)
    except AuthorityContractError:
        _fail(ContextErrorCode.INVALID_AUTHORITY_DECISION)
    if (
        decision.resource_type != "operational_context"
        or decision.resource_id != record.context_id
        or decision.policy_id != record.visibility_policy_id
        or decision.policy_version != record.visibility_policy_version
        or (decision.allowed and decision.effective_scope != record.scope)
    ):
        _fail(ContextErrorCode.INVALID_AUTHORITY_DECISION)


def context_metadata(record: OperationalContextRecord) -> dict[str, Any]:
    if not _valid_record(record):
        _fail(ContextErrorCode.INVALID_RECORD)
    return {
        "contract_version": record.contract_version,
        "context_id": record.context_id,
        "context_type": record.context_type.value,
        "parent_context_id": record.parent_context_id,
        "owner_party_ref": record.owner_party_ref,
        "display_name": record.display_name,
        "created_at": _iso(record.created_at),
        "valid_from": _iso(record.valid_from),
        "valid_until": _iso(record.valid_until),
        "revision": record.revision,
        "previous_revision": record.previous_revision,
        "classification": record.classification.value,
        "visibility_policy_id": record.visibility_policy_id,
        "visibility_policy_version": record.visibility_policy_version,
        "alias_count": len(record.aliases),
        "scope_organization_id": record.scope.organization_id,
        "scope_product_id": record.scope.product_id,
        "scope_workspace_id": record.scope.workspace_id,
        "scope_project_id": record.scope.project_id,
        "scope_resource_id": record.scope.resource_id,
        "scope_owner_party_id": record.scope.owner_party_id,
        "scope_global": record.scope.global_scope,
    }


_PROJECTION_FIELDS = frozenset(
    {
        "contract_version", "context_id", "context_type", "parent_context_id",
        "owner_party_ref", "display_name", "created_at", "valid_from", "valid_until",
        "revision", "previous_revision", "classification", "visibility_policy_id",
        "visibility_policy_version", "alias_count",
        "scope_organization_id", "scope_product_id", "scope_workspace_id",
        "scope_project_id", "scope_resource_id", "scope_owner_party_id", "scope_global",
    }
)
_REQUIRED_MAPPING_FIELDS = frozenset(
    {
        "context_id", "context_type", "visibility_policy_id", "visibility_policy_version",
        "scope_organization_id", "scope_product_id", "scope_workspace_id",
        "scope_project_id", "scope_resource_id", "scope_owner_party_id", "scope_global",
    }
)


def project_context_metadata(
    record: OperationalContextRecord | Mapping[str, Any],
    decision: AuthorizationDecision,
) -> dict[str, Any]:
    try:
        if type(record) is OperationalContextRecord:
            if not _valid_record(record):
                _fail(ContextErrorCode.INVALID_PROJECTION)
            source = context_metadata(record)
            expected_id = record.context_id
            expected_scope = record.scope
            expected_policy = (record.visibility_policy_id, record.visibility_policy_version)
        elif isinstance(record, Mapping):
            source = {
                key: value
                for key, value in record.items()
                if type(key) is str and key in _PROJECTION_FIELDS
            }
            if not _REQUIRED_MAPPING_FIELDS <= source.keys():
                _fail(ContextErrorCode.INVALID_PROJECTION)
            expected_id = validate_context_id(source["context_id"])
            if source["context_type"] not in {item.value for item in ContextType}:
                _fail(ContextErrorCode.INVALID_PROJECTION)
            expected_scope = AuthorityScope(
                organization_id=source["scope_organization_id"],
                product_id=source["scope_product_id"],
                workspace_id=source["scope_workspace_id"],
                project_id=source["scope_project_id"],
                resource_id=source["scope_resource_id"],
                owner_party_id=source["scope_owner_party_id"],
                global_scope=source["scope_global"],
            )
            if not _scope_probe(expected_scope, expected_id):
                _fail(ContextErrorCode.INVALID_PROJECTION)
            expected_policy = (
                source["visibility_policy_id"],
                source["visibility_policy_version"],
            )
        else:
            _fail(ContextErrorCode.INVALID_PROJECTION)
        try:
            projected = project_authorized_fields(
                source,
                decision,
                behavior=ProjectionBehavior.OMIT,
            )
        except AuthorityContractError:
            _fail(ContextErrorCode.INVALID_PROJECTION)
        if (
            decision.resource_type != "operational_context"
            or decision.resource_id != expected_id
            or (decision.policy_id, decision.policy_version) != expected_policy
            or (decision.allowed and decision.effective_scope != expected_scope)
        ):
            _fail(ContextErrorCode.INVALID_PROJECTION)
        return projected
    except OperationalContextError:
        raise
    except Exception:
        _fail(ContextErrorCode.INVALID_PROJECTION)


def linked_evidence_count(
    context: OperationalContextRecord,
    context_decision: AuthorizationDecision,
    associations: Sequence[EvidenceAssociation],
    evidence_records: Sequence[EvidenceRecord],
    evidence_decisions: Sequence[AuthorizationDecision],
) -> int | None:
    if not _valid_record(context):
        _fail(ContextErrorCode.INVALID_EVIDENCE_ASSOCIATION)
    _validate_context_decision(context_decision, context)
    if context_decision.allowed is not True or "linked_evidence_count" not in context_decision.visible_fields:
        return None
    if type(evidence_decisions) not in (list, tuple) or len(evidence_records) != len(evidence_decisions):
        _fail(ContextErrorCode.INVALID_EVIDENCE_ASSOCIATION)
    validated = validate_evidence_associations((context,), evidence_records, associations)
    decisions = {record.evidence_id: decision for record, decision in zip(evidence_records, evidence_decisions)}
    if len(decisions) != len(evidence_records):
        _fail(ContextErrorCode.INVALID_EVIDENCE_ASSOCIATION)
    for record in evidence_records:
        try:
            project_evidence_metadata(record, decisions[record.evidence_id])
        except EvidenceContractError:
            _fail(ContextErrorCode.EVIDENCE_NOT_AUTHORIZED)
    linked_ids = {item.evidence_id for item in validated if item.context_id == context.context_id}
    if linked_ids != set(decisions):
        _fail(ContextErrorCode.INVALID_EVIDENCE_ASSOCIATION)
    return len(linked_ids)


def inherit_context_visibility(
    records: Sequence[OperationalContextRecord],
    decisions: Sequence[AuthorizationDecision],
    *,
    artifact_kind: DerivedArtifactKind,
) -> DerivedVisibility:
    if (
        type(records) not in (list, tuple)
        or type(decisions) not in (list, tuple)
        or not records
        or len(records) != len(decisions)
        or type(artifact_kind) is not DerivedArtifactKind
    ):
        _fail(ContextErrorCode.SOURCE_NOT_AUTHORIZED)
    if len({record.context_id for record in records if _valid_record(record)}) != len(records):
        _fail(ContextErrorCode.SOURCE_NOT_AUTHORIZED)
    sources: list[SourceVisibility] = []
    baseline_scope: AuthorityScope | None = None
    for record, decision in zip(records, decisions):
        if not _valid_record(record):
            _fail(ContextErrorCode.SOURCE_NOT_AUTHORIZED)
        _validate_context_decision(decision, record)
        if decision.allowed is not True:
            _fail(ContextErrorCode.SOURCE_NOT_AUTHORIZED)
        if baseline_scope is None:
            baseline_scope = record.scope
        elif not _same_exact_operational_scope(baseline_scope, record.scope):
            _fail(ContextErrorCode.SOURCE_SCOPE_CONFLICT)
        sources.append(
            SourceVisibility(
                source_resource_id=record.context_id,
                classification=record.classification,
                visible_fields=decision.visible_fields,
                required_scopes=(record.scope,),
                authorized=True,
                policy_id=decision.policy_id,
                policy_version=decision.policy_version,
            )
        )
    inherited = inherit_derived_visibility(tuple(sources), artifact_kind=artifact_kind)
    if inherited.reason_code is AuthorityReason.SOURCE_SCOPE_CONFLICT:
        _fail(ContextErrorCode.SOURCE_SCOPE_CONFLICT)
    if inherited.reason_code is AuthorityReason.SOURCE_POLICY_CONFLICT:
        _fail(ContextErrorCode.SOURCE_POLICY_CONFLICT)
    if not inherited.allowed:
        _fail(ContextErrorCode.SOURCE_NOT_AUTHORIZED)
    return inherited


@dataclass(frozen=True, slots=True)
class ContextAuditSummary:
    context_id: str
    context_type: str
    action: str
    allowed: bool
    reason_code: str
    policy_id: str
    policy_version: str
    scope_reference: str
    timestamp: str
    correlation_id: str | None


def build_safe_context_audit_summary(
    record: OperationalContextRecord,
    decision: AuthorizationDecision,
    *,
    action: str,
    timestamp: datetime,
    correlation_id: str | None = None,
) -> ContextAuditSummary:
    if not _valid_record(record):
        _fail(ContextErrorCode.INVALID_AUDIT)
    try:
        _validate_context_decision(decision, record)
    except OperationalContextError:
        _fail(ContextErrorCode.INVALID_AUDIT)
    if action != decision.action_code or not _valid_code(action):
        _fail(ContextErrorCode.INVALID_AUDIT)
    safe_correlation = None
    if correlation_id is not None:
        safe_correlation = _require_code(
            correlation_id,
            ContextErrorCode.INVALID_AUDIT,
            maximum=128,
        )
    try:
        safe_timestamp = _iso(_utc(timestamp)) or ""
    except OperationalContextError:
        _fail(ContextErrorCode.INVALID_AUDIT)
    return ContextAuditSummary(
        context_id=record.context_id,
        context_type=record.context_type.value,
        action=action,
        allowed=decision.allowed,
        reason_code=decision.reason_code.value,
        policy_id=decision.policy_id,
        policy_version=decision.policy_version,
        scope_reference=record.context_id,
        timestamp=safe_timestamp,
        correlation_id=safe_correlation,
    )


__all__ = (
    "AliasNamespace",
    "ContextAlias",
    "ContextAuditSummary",
    "ContextErrorCode",
    "ContextGraph",
    "ContextRelationship",
    "ContextRelationshipType",
    "ContextType",
    "EvidenceAssociation",
    "EvidenceAssociationPurpose",
    "HierarchyPolicy",
    "MAX_ALIASES_PER_CONTEXT",
    "MAX_ANCESTRY_DEPTH",
    "MAX_CONTEXTS",
    "MAX_EVIDENCE_ASSOCIATIONS",
    "MAX_RELATIONSHIPS",
    "OPERATIONAL_CONTEXT_CONTRACT_VERSION",
    "OPERATIONAL_CONTEXT_POLICY_VERSION",
    "OperationalContextError",
    "OperationalContextRecord",
    "build_safe_context_audit_summary",
    "context_ancestry",
    "context_metadata",
    "inherit_context_visibility",
    "linked_evidence_count",
    "project_context_metadata",
    "validate_context_graph",
    "validate_context_id",
    "validate_evidence_associations",
    "validate_revision_transition",
)
