"""Pure declarations for stable MarketMatch principal identity.

Version 1 separates an immutable opaque ``principal_id`` from mutable login or
bearer-owner aliases.  This module validates only supplied logical data.  It
does not generate or reserve identifiers, authenticate an alias, inspect an
authentication store, authorize a principal, persist a mapping, migrate an
account, or establish ownership of any reservation.

Alias normalization deliberately mirrors the repository's current
``strip().lower()`` model.  It does not case-fold or Unicode-normalize, so
canonically equivalent Unicode spellings remain distinct declarations.  A
future approved migration must resolve such ambiguity using authoritative
account evidence; this pure contract never guesses.

An alias declaration has exactly three public fields.  Its exact private,
fieldless concrete class carries the supplied principal-status snapshot so a
finite declaration tuple can be resolved without global state or storage I/O.
That class marker is ordinary caller-supplied logical data: it is not an
unforgeable capability, can become stale, and proves nothing about current
authentication state.

A successful resolution proves only that the supplied finite tuple contains
one structurally valid declaration for the requested normalized alias and
mode, and that its supplied status snapshot is active.  It does not prove
authentication, authorization, account existence, production uniqueness,
cookie validity, bearer scope, impersonation, durable identity issuance,
migration, persistence, ownership, or reservation binding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import NoReturn
import unicodedata


PRINCIPAL_ID_PREFIX = "mmprincipal-"
PRINCIPAL_ID_SUFFIX_LENGTH = 26
MAX_ALIAS_UTF8_BYTES = 256
MAX_ALIAS_DECLARATIONS = 1_024

_PRINCIPAL_ID_RE = re.compile(
    rf"{PRINCIPAL_ID_PREFIX}[a-z0-9]{{{PRINCIPAL_ID_SUFFIX_LENGTH}}}\Z",
    re.ASCII,
)
_RESERVED_ALIASES = frozenset(
    {"internal-tool", "api", "anonymous", "demo", "system"}
)


class PrincipalStatus(str, Enum):
    """Closed logical status declarations for a principal."""

    ACTIVE = "active"
    DISABLED = "disabled"
    DELETED = "deleted"


class PrincipalAliasType(str, Enum):
    """Closed authentication-origin labels for mutable aliases."""

    USERNAME = "username"
    BEARER_OWNER = "bearer_owner"


class PrincipalIdentityCode(str, Enum):
    """Fixed privacy-preserving contract failure codes."""

    INVALID_PRINCIPAL_ID = "INVALID_PRINCIPAL_ID"
    INVALID_PRINCIPAL_STATUS = "INVALID_PRINCIPAL_STATUS"
    INVALID_PRINCIPAL_OBJECT = "INVALID_PRINCIPAL_OBJECT"
    INVALID_STATUS_TRANSITION = "INVALID_STATUS_TRANSITION"
    TERMINAL_PRINCIPAL = "TERMINAL_PRINCIPAL"
    INVALID_ALIAS = "INVALID_ALIAS"
    INVALID_ALIAS_TYPE = "INVALID_ALIAS_TYPE"
    RESERVED_ALIAS = "RESERVED_ALIAS"
    INVALID_ALIAS_DECLARATION = "INVALID_ALIAS_DECLARATION"
    INVALID_ALIAS_SET = "INVALID_ALIAS_SET"
    ALIAS_NOT_FOUND = "ALIAS_NOT_FOUND"
    ALIAS_CONFLICT = "ALIAS_CONFLICT"
    PRINCIPAL_DISABLED = "PRINCIPAL_DISABLED"
    PRINCIPAL_DELETED = "PRINCIPAL_DELETED"
    PRIVACY_POLICY_VIOLATION = "PRIVACY_POLICY_VIOLATION"


class PrincipalIdentityError(Exception):
    """A fixed-code error that never embeds rejected identity data."""

    def __init__(self, code: PrincipalIdentityCode):
        if type(code) is not PrincipalIdentityCode:
            code = PrincipalIdentityCode.INVALID_PRINCIPAL_OBJECT
        self.code = code
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r})"


@dataclass(frozen=True, slots=True, init=False, repr=False)
class PrincipalIdentity:
    """Immutable schema-valid principal declaration with no storage claim."""

    principal_id: str = field(repr=False)
    status: PrincipalStatus = field(repr=False)

    def __new__(cls):
        del cls
        raise PrincipalIdentityError(
            PrincipalIdentityCode.INVALID_PRINCIPAL_OBJECT
        ) from None

    def __repr__(self) -> str:
        return "PrincipalIdentity()"


@dataclass(frozen=True, slots=True, init=False, repr=False)
class PrincipalAliasDeclaration:
    """Immutable normalized alias declaration with exactly three fields.

    The fieldless concrete subtype records the supplied principal status at the
    moment this logical declaration was constructed.  It neither consults nor
    represents durable authentication state.
    """

    principal_id: str = field(repr=False)
    alias: str = field(repr=False)
    alias_type: PrincipalAliasType = field(repr=False)

    def __new__(cls):
        del cls
        raise PrincipalIdentityError(
            PrincipalIdentityCode.INVALID_ALIAS_DECLARATION
        ) from None

    def __repr__(self) -> str:
        return "PrincipalAliasDeclaration()"


class _PrincipalAliasActive(PrincipalAliasDeclaration):
    __slots__ = ()


class _PrincipalAliasDisabled(PrincipalAliasDeclaration):
    __slots__ = ()


class _PrincipalAliasDeleted(PrincipalAliasDeclaration):
    __slots__ = ()


_ALLOWED_STATUS_TRANSITIONS = frozenset(
    {
        (PrincipalStatus.ACTIVE, PrincipalStatus.DISABLED),
        (PrincipalStatus.ACTIVE, PrincipalStatus.DELETED),
        (PrincipalStatus.DISABLED, PrincipalStatus.ACTIVE),
        (PrincipalStatus.DISABLED, PrincipalStatus.DELETED),
    }
)


def _fail(code: PrincipalIdentityCode) -> NoReturn:
    raise PrincipalIdentityError(code) from None


def _validate_principal_id(value: object) -> str:
    if (
        type(value) is not str
        or not value.isascii()
        or _PRINCIPAL_ID_RE.fullmatch(value) is None
    ):
        _fail(PrincipalIdentityCode.INVALID_PRINCIPAL_ID)
    return value


def _validate_status(value: object) -> PrincipalStatus:
    if type(value) is not PrincipalStatus:
        _fail(PrincipalIdentityCode.INVALID_PRINCIPAL_STATUS)
    return value


def _validate_alias_type(value: object) -> PrincipalAliasType:
    if type(value) is not PrincipalAliasType:
        _fail(PrincipalIdentityCode.INVALID_ALIAS_TYPE)
    return value


def _normalize_alias(value: object) -> str:
    if type(value) is not str:
        _fail(PrincipalIdentityCode.INVALID_ALIAS)

    failed = False
    normalized = ""
    encoded = b""
    try:
        normalized = value.strip().lower()
        encoded = normalized.encode("utf-8")
    except (UnicodeError, ValueError):
        failed = True
    if (
        failed
        or not normalized
        or len(encoded) > MAX_ALIAS_UTF8_BYTES
        or any(unicodedata.category(char).startswith("C") for char in normalized)
    ):
        _fail(PrincipalIdentityCode.INVALID_ALIAS)
    if normalized in _RESERVED_ALIASES:
        _fail(PrincipalIdentityCode.RESERVED_ALIAS)
    return normalized


def _validate_explicit_normalized_alias(value: object) -> str:
    normalized = _normalize_alias(value)
    if normalized != value:
        _fail(PrincipalIdentityCode.INVALID_ALIAS)
    return normalized


def _new_identity(
    *,
    principal_id: str,
    status: PrincipalStatus,
) -> PrincipalIdentity:
    identity = object.__new__(PrincipalIdentity)
    object.__setattr__(identity, "principal_id", principal_id)
    object.__setattr__(identity, "status", status)
    return identity


def _validated_identity_fields(
    *,
    principal_id: object,
    status: object,
) -> PrincipalIdentity:
    return _new_identity(
        principal_id=_validate_principal_id(principal_id),
        status=_validate_status(status),
    )


def _validate_identity_object(value: object) -> PrincipalIdentity:
    if type(value) is not PrincipalIdentity:
        _fail(PrincipalIdentityCode.INVALID_PRINCIPAL_OBJECT)
    fields_failed = False
    principal_id: object = None
    status: object = None
    try:
        principal_id = value.principal_id
        status = value.status
    except Exception:
        fields_failed = True
    if fields_failed:
        _fail(PrincipalIdentityCode.INVALID_PRINCIPAL_OBJECT)
    try:
        return _validated_identity_fields(
            principal_id=principal_id,
            status=status,
        )
    except PrincipalIdentityError:
        _fail(PrincipalIdentityCode.INVALID_PRINCIPAL_OBJECT)


def create_principal_identity(
    *,
    principal_id: object,
    status: object = PrincipalStatus.ACTIVE,
) -> PrincipalIdentity:
    """Validate one explicit principal declaration without generating it."""

    return _validated_identity_fields(principal_id=principal_id, status=status)


def transition_principal_status(
    identity: object,
    *,
    next_status: object,
) -> PrincipalIdentity:
    """Validate one logical status transition without persisting it."""

    valid = _validate_identity_object(identity)
    target = _validate_status(next_status)
    if valid.status is PrincipalStatus.DELETED:
        _fail(PrincipalIdentityCode.TERMINAL_PRINCIPAL)
    if (valid.status, target) not in _ALLOWED_STATUS_TRANSITIONS:
        _fail(PrincipalIdentityCode.INVALID_STATUS_TRANSITION)
    return _new_identity(principal_id=valid.principal_id, status=target)


def _alias_class_for_status(status: PrincipalStatus) -> type[PrincipalAliasDeclaration]:
    if status is PrincipalStatus.ACTIVE:
        return _PrincipalAliasActive
    if status is PrincipalStatus.DISABLED:
        return _PrincipalAliasDisabled
    if status is PrincipalStatus.DELETED:
        return _PrincipalAliasDeleted
    _fail(PrincipalIdentityCode.INVALID_PRINCIPAL_STATUS)


def _status_for_alias_class(value: object) -> PrincipalStatus:
    if type(value) is _PrincipalAliasActive:
        return PrincipalStatus.ACTIVE
    if type(value) is _PrincipalAliasDisabled:
        return PrincipalStatus.DISABLED
    if type(value) is _PrincipalAliasDeleted:
        return PrincipalStatus.DELETED
    _fail(PrincipalIdentityCode.INVALID_ALIAS_DECLARATION)


def _new_alias_declaration(
    *,
    principal_id: str,
    alias: str,
    alias_type: PrincipalAliasType,
    status: PrincipalStatus,
) -> PrincipalAliasDeclaration:
    alias_class = _alias_class_for_status(status)
    declaration = object.__new__(alias_class)
    object.__setattr__(declaration, "principal_id", principal_id)
    object.__setattr__(declaration, "alias", alias)
    object.__setattr__(declaration, "alias_type", alias_type)
    return declaration


def create_principal_alias_declaration(
    identity: object,
    *,
    alias: object,
    alias_type: object,
) -> PrincipalAliasDeclaration:
    """Bind a normalized mutable alias to a supplied principal snapshot.

    The returned object stores no identity object or mutable caller value.  A
    later username rename is represented by a new declaration carrying the
    same principal ID.  Multiple historical declarations acquire authority
    only through a future approved migration and durable registry.
    """

    valid_identity = _validate_identity_object(identity)
    return _new_alias_declaration(
        principal_id=valid_identity.principal_id,
        alias=_normalize_alias(alias),
        alias_type=_validate_alias_type(alias_type),
        status=valid_identity.status,
    )


def _validate_alias_declaration_object(
    value: object,
) -> tuple[PrincipalAliasDeclaration, PrincipalStatus]:
    status = _status_for_alias_class(value)
    fields_failed = False
    principal_id: object = None
    alias: object = None
    alias_type: object = None
    try:
        principal_id = value.principal_id
        alias = value.alias
        alias_type = value.alias_type
    except Exception:
        fields_failed = True
    if fields_failed:
        _fail(PrincipalIdentityCode.INVALID_ALIAS_DECLARATION)

    try:
        valid_principal_id = _validate_principal_id(principal_id)
        valid_alias = _validate_explicit_normalized_alias(alias)
        valid_alias_type = _validate_alias_type(alias_type)
    except PrincipalIdentityError:
        _fail(PrincipalIdentityCode.INVALID_ALIAS_DECLARATION)
    return (
        _new_alias_declaration(
            principal_id=valid_principal_id,
            alias=valid_alias,
            alias_type=valid_alias_type,
            status=status,
        ),
        status,
    )


def resolve_principal_alias(
    normalized_alias: object,
    *,
    alias_type: object,
    declarations: object,
) -> PrincipalIdentity:
    """Resolve exactly one active match from a finite built-in tuple.

    The requested alias must already equal the repository-compatible
    ``strip().lower()`` form.  The entire tuple is validated before matching;
    duplicate declarations, conflicting principal mappings, or contradictory
    status snapshots for one stable principal fail closed.  The same alias may
    appear under both authentication modes only when both declarations bind
    the same principal and coherent status.
    """

    requested_alias = _validate_explicit_normalized_alias(normalized_alias)
    requested_type = _validate_alias_type(alias_type)
    if (
        type(declarations) is not tuple
        or len(declarations) > MAX_ALIAS_DECLARATIONS
    ):
        _fail(PrincipalIdentityCode.INVALID_ALIAS_SET)

    validated: list[tuple[PrincipalAliasDeclaration, PrincipalStatus]] = []
    seen: set[tuple[str, str, PrincipalAliasType, PrincipalStatus]] = set()
    principal_statuses: dict[str, PrincipalStatus] = {}
    alias_principals: dict[str, str] = {}

    for supplied in declarations:
        declaration, status = _validate_alias_declaration_object(supplied)
        key = (
            declaration.principal_id,
            declaration.alias,
            declaration.alias_type,
            status,
        )
        if key in seen:
            _fail(PrincipalIdentityCode.ALIAS_CONFLICT)
        seen.add(key)

        prior_status = principal_statuses.get(declaration.principal_id)
        if prior_status is not None and prior_status is not status:
            _fail(PrincipalIdentityCode.ALIAS_CONFLICT)
        principal_statuses[declaration.principal_id] = status

        prior_principal = alias_principals.get(declaration.alias)
        if (
            prior_principal is not None
            and prior_principal != declaration.principal_id
        ):
            _fail(PrincipalIdentityCode.ALIAS_CONFLICT)
        alias_principals[declaration.alias] = declaration.principal_id
        validated.append((declaration, status))

    alias_seen = False
    matches: list[tuple[PrincipalAliasDeclaration, PrincipalStatus]] = []
    for declaration, status in validated:
        if declaration.alias != requested_alias:
            continue
        alias_seen = True
        if declaration.alias_type is requested_type:
            matches.append((declaration, status))

    if alias_seen and not matches:
        _fail(PrincipalIdentityCode.ALIAS_CONFLICT)
    if not matches:
        _fail(PrincipalIdentityCode.ALIAS_NOT_FOUND)
    if len(matches) != 1:
        _fail(PrincipalIdentityCode.ALIAS_CONFLICT)

    match, status = matches[0]
    if status is PrincipalStatus.DISABLED:
        _fail(PrincipalIdentityCode.PRINCIPAL_DISABLED)
    if status is PrincipalStatus.DELETED:
        _fail(PrincipalIdentityCode.PRINCIPAL_DELETED)
    if status is not PrincipalStatus.ACTIVE:
        _fail(PrincipalIdentityCode.ALIAS_CONFLICT)
    return _new_identity(principal_id=match.principal_id, status=status)


__all__ = (
    "MAX_ALIAS_DECLARATIONS",
    "MAX_ALIAS_UTF8_BYTES",
    "PRINCIPAL_ID_PREFIX",
    "PRINCIPAL_ID_SUFFIX_LENGTH",
    "PrincipalAliasDeclaration",
    "PrincipalAliasType",
    "PrincipalIdentity",
    "PrincipalIdentityCode",
    "PrincipalIdentityError",
    "PrincipalStatus",
    "create_principal_alias_declaration",
    "create_principal_identity",
    "resolve_principal_alias",
    "transition_principal_status",
)
