"""Pure logical contract for MarketMatch original-media ingest reservations.

An :class:`IngestReservation` is only a schema-valid declaration.  It does
not authenticate or authorize an owner, reserve either identifier, persist an
idempotency key, or prove that a durable compare-and-set occurred.  The
transition function validates only the closed version-1 state graph and models
the expected-generation arithmetic that a future coordinator must enforce.
The idempotency-key grammar does not prove that a key was server-generated,
random, opaque, private, unique, reserved, or bound by a durable coordinator.

This module performs no I/O and has no application dependencies.  In
particular, it does not observe media, issue an original-media attestation,
open a database, publish an artifact, or claim transport, persistence,
durability, reachability, STT, or workflow completion.  Even ``bound`` means
only that the supplied declaration is logically in that state; a future
approved durable coordinator must establish the real fact.  Likewise, a valid
owner is only a canonical identifier-shaped string; it proves neither account
existence nor ownership of any operation or media.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import NoReturn


MAX_OWNER_LENGTH = 64
MAX_GENERATION = 1_000_000
IDEMPOTENCY_KEY_PREFIX = "mmidem-"
IDEMPOTENCY_KEY_SUFFIX_LENGTH = 32

_OWNER_RE = re.compile(
    rf"[a-z0-9](?:[a-z0-9_.-]{{0,{MAX_OWNER_LENGTH - 2}}}[a-z0-9])?\Z",
    re.ASCII,
)
_OPERATION_ID_RE = re.compile(r"mmop-(calls|videos)-[a-z0-9]{26}\Z", re.ASCII)
_MEDIA_ID_RE = re.compile(r"mmmedia-(calls|videos)-[a-z0-9]{26}\Z", re.ASCII)
_IDEMPOTENCY_KEY_RE = re.compile(
    rf"{IDEMPOTENCY_KEY_PREFIX}[a-z0-9]{{{IDEMPOTENCY_KEY_SUFFIX_LENGTH}}}\Z",
    re.ASCII,
)

_HOSTNAME_RE = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?\Z",
    re.ASCII,
)
_CREDENTIAL_OWNER_RE = re.compile(
    r"(?:api[-_.]?key|bearer|credential|passwd|password|secret|token)"
    r"(?:[-_.][a-z0-9][a-z0-9_.-]*)?\Z",
    re.ASCII,
)

_RESERVED_OWNER_VALUES = frozenset({"internal-tool", "api", "demo", "system"})


class IngestReservationDomain(str, Enum):
    """Closed MarketMatch original-media domains."""

    CALLS = "calls"
    VIDEOS = "videos"


class IngestReservationState(str, Enum):
    """Closed logical version-1 reservation states."""

    RESERVED = "reserved"
    OBSERVING = "observing"
    OBSERVED = "observed"
    ATTESTED = "attested"
    BOUND = "bound"
    FAILED = "failed"
    ABANDONED = "abandoned"


class IngestReservationCode(str, Enum):
    """Fixed privacy-preserving contract failure codes."""

    INVALID_OWNER = "INVALID_OWNER"
    INVALID_DOMAIN = "INVALID_DOMAIN"
    INVALID_OPERATION_ID = "INVALID_OPERATION_ID"
    INVALID_MEDIA_ID = "INVALID_MEDIA_ID"
    INVALID_IDEMPOTENCY_KEY = "INVALID_IDEMPOTENCY_KEY"
    INVALID_STATE = "INVALID_STATE"
    INVALID_GENERATION = "INVALID_GENERATION"
    INVALID_INITIAL_STATE = "INVALID_INITIAL_STATE"
    INVALID_INITIAL_GENERATION = "INVALID_INITIAL_GENERATION"
    INVALID_RESERVATION_OBJECT = "INVALID_RESERVATION_OBJECT"
    GENERATION_CONFLICT = "GENERATION_CONFLICT"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    TERMINAL_STATE = "TERMINAL_STATE"
    DOMAIN_IDENTITY_MISMATCH = "DOMAIN_IDENTITY_MISMATCH"
    RESERVED_OWNER = "RESERVED_OWNER"


class IngestReservationError(Exception):
    """A fixed-code error that never embeds rejected input."""

    def __init__(self, code: IngestReservationCode):
        if type(code) is not IngestReservationCode:
            code = IngestReservationCode.INVALID_RESERVATION_OBJECT
        self.code = code
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r})"


@dataclass(frozen=True, slots=True, init=False, repr=False)
class IngestReservation:
    """Immutable validated logical declaration with no durability claim."""

    owner: str = field(repr=False)
    domain: IngestReservationDomain = field(repr=False)
    operation_id: str = field(repr=False)
    media_id: str = field(repr=False)
    idempotency_key: str = field(repr=False)
    state: IngestReservationState = field(repr=False)
    generation: int = field(repr=False)

    def __new__(cls):
        del cls
        raise IngestReservationError(
            IngestReservationCode.INVALID_RESERVATION_OBJECT
        ) from None

    def __repr__(self) -> str:
        return "IngestReservation()"


_ALLOWED_TRANSITIONS = frozenset(
    {
        (IngestReservationState.RESERVED, IngestReservationState.OBSERVING),
        (IngestReservationState.RESERVED, IngestReservationState.ABANDONED),
        (IngestReservationState.OBSERVING, IngestReservationState.OBSERVED),
        (IngestReservationState.OBSERVING, IngestReservationState.FAILED),
        (IngestReservationState.OBSERVED, IngestReservationState.ATTESTED),
        (IngestReservationState.OBSERVED, IngestReservationState.FAILED),
        (IngestReservationState.ATTESTED, IngestReservationState.BOUND),
        (IngestReservationState.ATTESTED, IngestReservationState.FAILED),
    }
)
_TERMINAL_STATES = frozenset(
    {
        IngestReservationState.BOUND,
        IngestReservationState.FAILED,
        IngestReservationState.ABANDONED,
    }
)


def _fail(code: IngestReservationCode) -> NoReturn:
    raise IngestReservationError(code) from None


def _looks_like_ipv4(owner: str) -> bool:
    parts = owner.split(".")
    if len(parts) != 4 or not all(part.isdigit() for part in parts):
        return False
    return all(0 <= int(part, 10) <= 255 for part in parts)


def _validate_owner(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > MAX_OWNER_LENGTH
        or not value.isascii()
        or _OWNER_RE.fullmatch(value) is None
        or ".." in value
        or any(pair in value for pair in (".-", "-.", "._", "_."))
    ):
        _fail(IngestReservationCode.INVALID_OWNER)
    if value in _RESERVED_OWNER_VALUES:
        _fail(IngestReservationCode.RESERVED_OWNER)
    if (
        _looks_like_ipv4(value)
        or value == "localhost"
        or _HOSTNAME_RE.fullmatch(value) is not None
        or _CREDENTIAL_OWNER_RE.fullmatch(value) is not None
    ):
        _fail(IngestReservationCode.INVALID_OWNER)
    return value


def _validate_domain(value: object) -> IngestReservationDomain:
    if type(value) is not IngestReservationDomain:
        _fail(IngestReservationCode.INVALID_DOMAIN)
    return value


def _validate_operation_id(
    value: object,
    domain: IngestReservationDomain,
) -> str:
    if type(value) is not str:
        _fail(IngestReservationCode.INVALID_OPERATION_ID)
    match = _OPERATION_ID_RE.fullmatch(value)
    if match is None:
        _fail(IngestReservationCode.INVALID_OPERATION_ID)
    if match.group(1) != domain.value:
        _fail(IngestReservationCode.DOMAIN_IDENTITY_MISMATCH)
    return value


def _validate_media_id(
    value: object,
    domain: IngestReservationDomain,
) -> str:
    if type(value) is not str:
        _fail(IngestReservationCode.INVALID_MEDIA_ID)
    match = _MEDIA_ID_RE.fullmatch(value)
    if match is None:
        _fail(IngestReservationCode.INVALID_MEDIA_ID)
    if match.group(1) != domain.value:
        _fail(IngestReservationCode.DOMAIN_IDENTITY_MISMATCH)
    return value


def _validate_idempotency_key(value: object) -> str:
    """Validate syntax only; issuance and opacity are external guarantees."""

    if type(value) is not str or _IDEMPOTENCY_KEY_RE.fullmatch(value) is None:
        _fail(IngestReservationCode.INVALID_IDEMPOTENCY_KEY)
    return value


def _validate_state(value: object) -> IngestReservationState:
    if type(value) is not IngestReservationState:
        _fail(IngestReservationCode.INVALID_STATE)
    return value


def _validate_generation(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_GENERATION:
        _fail(IngestReservationCode.INVALID_GENERATION)
    return value


def _new_reservation(
    *,
    owner: str,
    domain: IngestReservationDomain,
    operation_id: str,
    media_id: str,
    idempotency_key: str,
    state: IngestReservationState,
    generation: int,
) -> IngestReservation:
    reservation = object.__new__(IngestReservation)
    object.__setattr__(reservation, "owner", owner)
    object.__setattr__(reservation, "domain", domain)
    object.__setattr__(reservation, "operation_id", operation_id)
    object.__setattr__(reservation, "media_id", media_id)
    object.__setattr__(reservation, "idempotency_key", idempotency_key)
    object.__setattr__(reservation, "state", state)
    object.__setattr__(reservation, "generation", generation)
    return reservation


def _validated_declaration(
    *,
    owner: object,
    domain: object,
    operation_id: object,
    media_id: object,
    idempotency_key: object,
    state: object,
    generation: object,
) -> IngestReservation:
    valid_owner = _validate_owner(owner)
    valid_domain = _validate_domain(domain)
    valid_operation_id = _validate_operation_id(operation_id, valid_domain)
    valid_media_id = _validate_media_id(media_id, valid_domain)
    valid_key = _validate_idempotency_key(idempotency_key)
    valid_state = _validate_state(state)
    valid_generation = _validate_generation(generation)
    return _new_reservation(
        owner=valid_owner,
        domain=valid_domain,
        operation_id=valid_operation_id,
        media_id=valid_media_id,
        idempotency_key=valid_key,
        state=valid_state,
        generation=valid_generation,
    )


def create_ingest_reservation(
    *,
    owner: object,
    domain: object,
    operation_id: object,
    media_id: object,
    idempotency_key: object,
    state: object,
    generation: object,
) -> IngestReservation:
    """Validate one initial logical declaration.

    All identity and generation values are explicit inputs.  Only
    ``state=RESERVED`` and ``generation=1`` are valid here.  The result does
    not prove that a durable reservation or authorization exists.
    """

    if type(state) is IngestReservationState:
        if state is not IngestReservationState.RESERVED:
            _fail(IngestReservationCode.INVALID_INITIAL_STATE)
    else:
        _fail(IngestReservationCode.INVALID_STATE)
    if type(generation) is not int:
        _fail(IngestReservationCode.INVALID_INITIAL_GENERATION)
    if generation != 1:
        _fail(IngestReservationCode.INVALID_INITIAL_GENERATION)
    return _validated_declaration(
        owner=owner,
        domain=domain,
        operation_id=operation_id,
        media_id=media_id,
        idempotency_key=idempotency_key,
        state=state,
        generation=generation,
    )


def validate_ingest_reservation_declaration(
    *,
    owner: object,
    domain: object,
    operation_id: object,
    media_id: object,
    idempotency_key: object,
    state: object,
    generation: object,
) -> IngestReservation:
    """Reconstruct and validate a logical declaration without trusting it.

    This separately named function permits every closed version-1 state for
    synthetic testing and future parsing of already supplied declarations.
    It does not prove provenance, authorization, persistence, or durability.
    """

    return _validated_declaration(
        owner=owner,
        domain=domain,
        operation_id=operation_id,
        media_id=media_id,
        idempotency_key=idempotency_key,
        state=state,
        generation=generation,
    )


def _validate_reservation_object(value: object) -> IngestReservation:
    if type(value) is not IngestReservation:
        _fail(IngestReservationCode.INVALID_RESERVATION_OBJECT)
    fields_failed = False
    owner: object = None
    domain: object = None
    operation_id: object = None
    media_id: object = None
    idempotency_key: object = None
    state: object = None
    generation: object = None
    try:
        owner = value.owner
        domain = value.domain
        operation_id = value.operation_id
        media_id = value.media_id
        idempotency_key = value.idempotency_key
        state = value.state
        generation = value.generation
    except Exception:
        fields_failed = True
    if fields_failed:
        _fail(IngestReservationCode.INVALID_RESERVATION_OBJECT)
    try:
        return _validated_declaration(
            owner=owner,
            domain=domain,
            operation_id=operation_id,
            media_id=media_id,
            idempotency_key=idempotency_key,
            state=state,
            generation=generation,
        )
    except IngestReservationError:
        _fail(IngestReservationCode.INVALID_RESERVATION_OBJECT)


def transition_ingest_reservation(
    reservation: object,
    *,
    expected_generation: object,
    next_state: object,
) -> IngestReservation:
    """Validate one logical state transition and expected generation.

    A successful result increments the supplied declaration's generation by
    exactly one and preserves every identity field.  This models the inputs
    and result expected around a future durable compare-and-set; it does not
    execute or prove that compare-and-set.
    """

    valid = _validate_reservation_object(reservation)
    valid_expected_generation = _validate_generation(expected_generation)
    valid_next_state = _validate_state(next_state)

    if valid_expected_generation != valid.generation:
        _fail(IngestReservationCode.GENERATION_CONFLICT)
    if valid.state in _TERMINAL_STATES:
        _fail(IngestReservationCode.TERMINAL_STATE)
    if (valid.state, valid_next_state) not in _ALLOWED_TRANSITIONS:
        _fail(IngestReservationCode.INVALID_TRANSITION)
    if valid.generation >= MAX_GENERATION:
        _fail(IngestReservationCode.INVALID_GENERATION)

    return _new_reservation(
        owner=valid.owner,
        domain=valid.domain,
        operation_id=valid.operation_id,
        media_id=valid.media_id,
        idempotency_key=valid.idempotency_key,
        state=valid_next_state,
        generation=valid.generation + 1,
    )


__all__ = (
    "IDEMPOTENCY_KEY_PREFIX",
    "IDEMPOTENCY_KEY_SUFFIX_LENGTH",
    "MAX_GENERATION",
    "MAX_OWNER_LENGTH",
    "IngestReservation",
    "IngestReservationCode",
    "IngestReservationDomain",
    "IngestReservationError",
    "IngestReservationState",
    "create_ingest_reservation",
    "transition_ingest_reservation",
    "validate_ingest_reservation_declaration",
)
