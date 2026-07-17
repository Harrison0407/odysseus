"""Synthetic tests for the pure MarketMatch ingest-reservation contract."""

from __future__ import annotations

import ast
import contextlib
from dataclasses import FrozenInstanceError, fields
import io
from pathlib import Path
import subprocess
import traceback
import unittest

from src.marketmatch_ingest_reservation import (
    IDEMPOTENCY_KEY_PREFIX,
    IDEMPOTENCY_KEY_SUFFIX_LENGTH,
    MAX_GENERATION,
    MAX_OWNER_LENGTH,
    IngestReservation,
    IngestReservationCode,
    IngestReservationDomain,
    IngestReservationError,
    IngestReservationState,
    create_ingest_reservation,
    transition_ingest_reservation,
    validate_ingest_reservation_declaration,
)


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_PATH = ROOT / "src" / "marketmatch_ingest_reservation.py"
APPROVED_PATHS = {
    "src/marketmatch_ingest_reservation.py",
    "tests/test_marketmatch_ingest_reservation.py",
}

CALLS_OPERATION_ID = "mmop-calls-" + "a" * 26
VIDEOS_OPERATION_ID = "mmop-videos-" + "b" * 26
CALLS_MEDIA_ID = "mmmedia-calls-" + "c" * 26
VIDEOS_MEDIA_ID = "mmmedia-videos-" + "d" * 26
VALID_IDEMPOTENCY_KEY = IDEMPOTENCY_KEY_PREFIX + "a1" * 16


def _values(
    *,
    domain: object = IngestReservationDomain.CALLS,
    owner: object = "alice",
    operation_id: object | None = None,
    media_id: object | None = None,
    idempotency_key: object = VALID_IDEMPOTENCY_KEY,
    state: object = IngestReservationState.RESERVED,
    generation: object = 1,
) -> dict[str, object]:
    videos = domain is IngestReservationDomain.VIDEOS
    return {
        "owner": owner,
        "domain": domain,
        "operation_id": operation_id
        if operation_id is not None
        else (VIDEOS_OPERATION_ID if videos else CALLS_OPERATION_ID),
        "media_id": media_id
        if media_id is not None
        else (VIDEOS_MEDIA_ID if videos else CALLS_MEDIA_ID),
        "idempotency_key": idempotency_key,
        "state": state,
        "generation": generation,
    }


def _initial(**changes: object) -> IngestReservation:
    return create_ingest_reservation(**_values(**changes))


def _declaration(**changes: object) -> IngestReservation:
    return validate_ingest_reservation_declaration(**_values(**changes))


def _assert_initial_code(
    test: unittest.TestCase,
    code: IngestReservationCode,
    **changes: object,
) -> IngestReservationError:
    with test.assertRaises(IngestReservationError) as caught:
        _initial(**changes)
    test.assertIs(caught.exception.code, code)
    return caught.exception


def _assert_declaration_code(
    test: unittest.TestCase,
    code: IngestReservationCode,
    **changes: object,
) -> IngestReservationError:
    with test.assertRaises(IngestReservationError) as caught:
        _declaration(**changes)
    test.assertIs(caught.exception.code, code)
    return caught.exception


def _assert_transition_code(
    test: unittest.TestCase,
    code: IngestReservationCode,
    reservation: object,
    *,
    expected_generation: object,
    next_state: object,
) -> IngestReservationError:
    with test.assertRaises(IngestReservationError) as caught:
        transition_ingest_reservation(
            reservation,
            expected_generation=expected_generation,
            next_state=next_state,
        )
    test.assertIs(caught.exception.code, code)
    return caught.exception


def _opaque_key_with_text(text: str) -> str:
    if len(text) > IDEMPOTENCY_KEY_SUFFIX_LENGTH:
        raise AssertionError("synthetic test token is too long")
    return IDEMPOTENCY_KEY_PREFIX + text + "q" * (
        IDEMPOTENCY_KEY_SUFFIX_LENGTH - len(text)
    )


class ReservationConstructionTests(unittest.TestCase):
    def test_valid_calls_initial_reservation(self) -> None:
        result = _initial()
        self.assertIs(result.domain, IngestReservationDomain.CALLS)
        self.assertEqual(result.operation_id, CALLS_OPERATION_ID)
        self.assertEqual(result.media_id, CALLS_MEDIA_ID)

    def test_valid_videos_initial_reservation(self) -> None:
        result = _initial(domain=IngestReservationDomain.VIDEOS, owner="bob")
        self.assertIs(result.domain, IngestReservationDomain.VIDEOS)
        self.assertEqual(result.operation_id, VIDEOS_OPERATION_ID)
        self.assertEqual(result.media_id, VIDEOS_MEDIA_ID)

    def test_initial_state_is_reserved(self) -> None:
        self.assertIs(_initial().state, IngestReservationState.RESERVED)

    def test_initial_generation_is_one(self) -> None:
        self.assertEqual(_initial().generation, 1)

    def test_non_reserved_initial_state_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_INITIAL_STATE,
            state=IngestReservationState.OBSERVING,
        )

    def test_string_initial_state_rejected_without_coercion(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_STATE,
            state="reserved",
        )

    def test_initial_generation_two_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_INITIAL_GENERATION,
            generation=2,
        )

    def test_boolean_initial_generation_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_INITIAL_GENERATION,
            generation=True,
        )

    def test_initial_constructor_requires_all_identity_values(self) -> None:
        with self.assertRaises(TypeError):
            create_ingest_reservation(  # type: ignore[call-arg]
                owner="alice",
                domain=IngestReservationDomain.CALLS,
                operation_id=CALLS_OPERATION_ID,
                media_id=CALLS_MEDIA_ID,
                state=IngestReservationState.RESERVED,
                generation=1,
            )

    def test_record_cannot_be_constructed_directly(self) -> None:
        with self.assertRaises(IngestReservationError) as caught:
            IngestReservation()
        self.assertIs(
            caught.exception.code,
            IngestReservationCode.INVALID_RESERVATION_OBJECT,
        )

    def test_later_declaration_validator_accepts_every_state(self) -> None:
        for state in IngestReservationState:
            with self.subTest(state=state):
                result = _declaration(state=state, generation=7)
                self.assertIs(result.state, state)
                self.assertEqual(result.generation, 7)

    def test_later_declaration_name_does_not_imply_durability(self) -> None:
        result = _declaration(state=IngestReservationState.BOUND, generation=5)
        self.assertEqual(repr(result), "IngestReservation()")
        self.assertFalse(hasattr(result, "durable"))


class ReservationOwnerTests(unittest.TestCase):
    def test_valid_owner_grammar(self) -> None:
        valid = (
            "a",
            "alice",
            "alice2",
            "alice_smith",
            "alice-smith",
            "alice_smith.team",
        )
        for owner in valid:
            with self.subTest(owner=owner):
                self.assertEqual(_initial(owner=owner).owner, owner)

    def test_empty_owner_rejected(self) -> None:
        _assert_initial_code(self, IngestReservationCode.INVALID_OWNER, owner="")

    def test_unicode_owner_rejected(self) -> None:
        _assert_initial_code(self, IngestReservationCode.INVALID_OWNER, owner="álîce")

    def test_uppercase_owner_rejected(self) -> None:
        _assert_initial_code(self, IngestReservationCode.INVALID_OWNER, owner="Alice")

    def test_owner_slash_rejected(self) -> None:
        _assert_initial_code(self, IngestReservationCode.INVALID_OWNER, owner="home/alice")

    def test_owner_backslash_rejected(self) -> None:
        _assert_initial_code(self, IngestReservationCode.INVALID_OWNER, owner="home\\alice")

    def test_owner_whitespace_rejected(self) -> None:
        _assert_initial_code(self, IngestReservationCode.INVALID_OWNER, owner="alice smith")

    def test_email_like_owner_rejected(self) -> None:
        _assert_initial_code(self, IngestReservationCode.INVALID_OWNER, owner="alice@example.com")

    def test_url_like_owner_rejected(self) -> None:
        _assert_initial_code(self, IngestReservationCode.INVALID_OWNER, owner="https://example.com")

    def test_path_like_owner_rejected(self) -> None:
        _assert_initial_code(self, IngestReservationCode.INVALID_OWNER, owner="..\\private")

    def test_leading_separator_owner_rejected(self) -> None:
        for owner in (".alice", "-alice", "_alice"):
            with self.subTest(owner=owner):
                _assert_initial_code(self, IngestReservationCode.INVALID_OWNER, owner=owner)

    def test_trailing_separator_owner_rejected(self) -> None:
        for owner in ("alice.", "alice-", "alice_"):
            with self.subTest(owner=owner):
                _assert_initial_code(self, IngestReservationCode.INVALID_OWNER, owner=owner)

    def test_repeated_dot_owner_rejected(self) -> None:
        _assert_initial_code(self, IngestReservationCode.INVALID_OWNER, owner="alice..smith")

    def test_ambiguous_dot_separator_owner_rejected(self) -> None:
        for owner in ("alice.-smith", "alice-.smith", "alice._smith", "alice_.smith"):
            with self.subTest(owner=owner):
                _assert_initial_code(self, IngestReservationCode.INVALID_OWNER, owner=owner)

    def test_excessive_owner_length_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_OWNER,
            owner="a" * (MAX_OWNER_LENGTH + 1),
        )

    def test_exact_owner_length_accepted(self) -> None:
        owner = "a" * MAX_OWNER_LENGTH
        self.assertEqual(_initial(owner=owner).owner, owner)

    def test_ipv4_owner_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_OWNER,
            owner="192.168.1.2",
        )

    def test_hostname_like_owner_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_OWNER,
            owner="www.example.com",
        )

    def test_two_label_hostname_like_owner_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_OWNER,
            owner="alice.smith",
        )

    def test_reserved_internal_tool_owner_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.RESERVED_OWNER,
            owner="internal-tool",
        )

    def test_synthetic_api_owner_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.RESERVED_OWNER,
            owner="api",
        )

    def test_credential_like_owner_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_OWNER,
            owner="token-alice",
        )


class ReservationIdentityTests(unittest.TestCase):
    def test_valid_calls_operation_id(self) -> None:
        self.assertEqual(_initial().operation_id, CALLS_OPERATION_ID)

    def test_valid_videos_operation_id(self) -> None:
        result = _initial(domain=IngestReservationDomain.VIDEOS, owner="bob")
        self.assertEqual(result.operation_id, VIDEOS_OPERATION_ID)

    def test_operation_domain_mismatch_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.DOMAIN_IDENTITY_MISMATCH,
            operation_id=VIDEOS_OPERATION_ID,
        )

    def test_invalid_operation_id_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_OPERATION_ID,
            operation_id="not-an-operation",
        )

    def test_operation_id_uppercase_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_OPERATION_ID,
            operation_id="mmop-calls-" + "A" * 26,
        )

    def test_operation_id_unicode_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_OPERATION_ID,
            operation_id="mmop-calls-" + "a" * 25 + "é",
        )

    def test_operation_id_string_subclass_rejected(self) -> None:
        class Identifier(str):
            pass

        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_OPERATION_ID,
            operation_id=Identifier(CALLS_OPERATION_ID),
        )

    def test_valid_calls_media_id(self) -> None:
        self.assertEqual(_initial().media_id, CALLS_MEDIA_ID)

    def test_valid_videos_media_id(self) -> None:
        result = _initial(domain=IngestReservationDomain.VIDEOS, owner="bob")
        self.assertEqual(result.media_id, VIDEOS_MEDIA_ID)

    def test_media_domain_mismatch_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.DOMAIN_IDENTITY_MISMATCH,
            media_id=VIDEOS_MEDIA_ID,
        )

    def test_invalid_media_id_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_MEDIA_ID,
            media_id="not-media",
        )

    def test_invalid_domain_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_DOMAIN,
            domain="calls",
        )

    def test_cross_domain_declarations_are_not_interchangeable(self) -> None:
        calls = _initial()
        videos = _initial(domain=IngestReservationDomain.VIDEOS, owner="bob")
        self.assertIsNot(calls.domain, videos.domain)
        self.assertNotEqual(calls.operation_id, videos.operation_id)
        self.assertNotEqual(calls.media_id, videos.media_id)


class ReservationIdempotencyTests(unittest.TestCase):
    def test_valid_idempotency_key(self) -> None:
        self.assertEqual(_initial().idempotency_key, VALID_IDEMPOTENCY_KEY)

    def test_key_prefix_is_exact(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_IDEMPOTENCY_KEY,
            idempotency_key="idem-" + "a1" * 16,
        )

    def test_short_idempotency_key_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_IDEMPOTENCY_KEY,
            idempotency_key=IDEMPOTENCY_KEY_PREFIX + "a" * 31,
        )

    def test_long_idempotency_key_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_IDEMPOTENCY_KEY,
            idempotency_key=IDEMPOTENCY_KEY_PREFIX + "a" * 33,
        )

    def test_uppercase_idempotency_key_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_IDEMPOTENCY_KEY,
            idempotency_key=IDEMPOTENCY_KEY_PREFIX + "A" * 32,
        )

    def test_unicode_idempotency_key_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_IDEMPOTENCY_KEY,
            idempotency_key=IDEMPOTENCY_KEY_PREFIX + "a" * 31 + "é",
        )

    def test_separator_in_idempotency_suffix_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_IDEMPOTENCY_KEY,
            idempotency_key=IDEMPOTENCY_KEY_PREFIX + "a" * 15 + "-" + "b" * 16,
        )

    def test_semantic_key_with_separator_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_IDEMPOTENCY_KEY,
            idempotency_key=_opaque_key_with_text("owner-alice"),
        )

    def test_privacy_key_with_path_syntax_rejected(self) -> None:
        _assert_initial_code(
            self,
            IngestReservationCode.INVALID_IDEMPOTENCY_KEY,
            idempotency_key=_opaque_key_with_text("home/alice"),
        )

    def test_syntax_valid_owner_text_is_treated_as_opaque(self) -> None:
        key = _opaque_key_with_text("alice")
        self.assertEqual(_initial(idempotency_key=key).idempotency_key, key)

    def test_syntax_valid_domain_text_is_treated_as_opaque(self) -> None:
        key = _opaque_key_with_text("calls")
        self.assertEqual(_initial(idempotency_key=key).idempotency_key, key)

    def test_syntax_valid_identifier_body_is_treated_as_opaque(self) -> None:
        for text in ("a" * 26, "c" * 26):
            with self.subTest(text=text):
                key = _opaque_key_with_text(text)
                self.assertEqual(_initial(idempotency_key=key).idempotency_key, key)

    def test_validated_key_makes_no_issuance_or_privacy_claim(self) -> None:
        result = _initial()
        for field_name in (
            "server_generated",
            "random",
            "opaque",
            "private",
            "unique",
            "persisted",
        ):
            self.assertFalse(hasattr(result, field_name))

    def test_idempotency_key_is_not_generated(self) -> None:
        first = _initial()
        second = _initial()
        self.assertEqual(first.idempotency_key, VALID_IDEMPOTENCY_KEY)
        self.assertEqual(second.idempotency_key, VALID_IDEMPOTENCY_KEY)


class ReservationGenerationTests(unittest.TestCase):
    def test_boolean_generation_rejected(self) -> None:
        _assert_declaration_code(
            self,
            IngestReservationCode.INVALID_GENERATION,
            generation=True,
        )

    def test_zero_generation_rejected(self) -> None:
        _assert_declaration_code(
            self,
            IngestReservationCode.INVALID_GENERATION,
            generation=0,
        )

    def test_negative_generation_rejected(self) -> None:
        _assert_declaration_code(
            self,
            IngestReservationCode.INVALID_GENERATION,
            generation=-1,
        )

    def test_float_generation_rejected(self) -> None:
        _assert_declaration_code(
            self,
            IngestReservationCode.INVALID_GENERATION,
            generation=1.0,
        )

    def test_string_generation_rejected(self) -> None:
        _assert_declaration_code(
            self,
            IngestReservationCode.INVALID_GENERATION,
            generation="1",
        )

    def test_maximum_generation_accepted_for_declaration(self) -> None:
        result = _declaration(generation=MAX_GENERATION)
        self.assertEqual(result.generation, MAX_GENERATION)

    def test_excessive_generation_rejected(self) -> None:
        _assert_declaration_code(
            self,
            IngestReservationCode.INVALID_GENERATION,
            generation=MAX_GENERATION + 1,
        )


class ReservationTransitionTests(unittest.TestCase):
    def _transition(
        self,
        source: IngestReservationState,
        target: IngestReservationState,
        *,
        generation: int = 7,
    ) -> IngestReservation:
        reservation = _declaration(state=source, generation=generation)
        return transition_ingest_reservation(
            reservation,
            expected_generation=generation,
            next_state=target,
        )

    def test_reserved_to_observing(self) -> None:
        self.assertIs(
            self._transition(IngestReservationState.RESERVED, IngestReservationState.OBSERVING).state,
            IngestReservationState.OBSERVING,
        )

    def test_reserved_to_abandoned(self) -> None:
        self.assertIs(
            self._transition(IngestReservationState.RESERVED, IngestReservationState.ABANDONED).state,
            IngestReservationState.ABANDONED,
        )

    def test_observing_to_observed(self) -> None:
        self.assertIs(
            self._transition(IngestReservationState.OBSERVING, IngestReservationState.OBSERVED).state,
            IngestReservationState.OBSERVED,
        )

    def test_observing_to_failed(self) -> None:
        self.assertIs(
            self._transition(IngestReservationState.OBSERVING, IngestReservationState.FAILED).state,
            IngestReservationState.FAILED,
        )

    def test_observed_to_attested(self) -> None:
        self.assertIs(
            self._transition(IngestReservationState.OBSERVED, IngestReservationState.ATTESTED).state,
            IngestReservationState.ATTESTED,
        )

    def test_observed_to_failed(self) -> None:
        self.assertIs(
            self._transition(IngestReservationState.OBSERVED, IngestReservationState.FAILED).state,
            IngestReservationState.FAILED,
        )

    def test_attested_to_bound(self) -> None:
        self.assertIs(
            self._transition(IngestReservationState.ATTESTED, IngestReservationState.BOUND).state,
            IngestReservationState.BOUND,
        )

    def test_attested_to_failed(self) -> None:
        self.assertIs(
            self._transition(IngestReservationState.ATTESTED, IngestReservationState.FAILED).state,
            IngestReservationState.FAILED,
        )

    def test_every_disallowed_direct_transition_rejected(self) -> None:
        allowed = {
            (IngestReservationState.RESERVED, IngestReservationState.OBSERVING),
            (IngestReservationState.RESERVED, IngestReservationState.ABANDONED),
            (IngestReservationState.OBSERVING, IngestReservationState.OBSERVED),
            (IngestReservationState.OBSERVING, IngestReservationState.FAILED),
            (IngestReservationState.OBSERVED, IngestReservationState.ATTESTED),
            (IngestReservationState.OBSERVED, IngestReservationState.FAILED),
            (IngestReservationState.ATTESTED, IngestReservationState.BOUND),
            (IngestReservationState.ATTESTED, IngestReservationState.FAILED),
        }
        terminals = {
            IngestReservationState.BOUND,
            IngestReservationState.FAILED,
            IngestReservationState.ABANDONED,
        }
        for source in IngestReservationState:
            for target in IngestReservationState:
                if (source, target) in allowed:
                    continue
                with self.subTest(source=source, target=target):
                    reservation = _declaration(state=source, generation=7)
                    code = (
                        IngestReservationCode.TERMINAL_STATE
                        if source in terminals
                        else IngestReservationCode.INVALID_TRANSITION
                    )
                    _assert_transition_code(
                        self,
                        code,
                        reservation,
                        expected_generation=7,
                        next_state=target,
                    )

    def test_same_state_transition_rejected(self) -> None:
        reservation = _declaration(state=IngestReservationState.OBSERVING, generation=4)
        _assert_transition_code(
            self,
            IngestReservationCode.INVALID_TRANSITION,
            reservation,
            expected_generation=4,
            next_state=IngestReservationState.OBSERVING,
        )

    def test_backward_transition_rejected(self) -> None:
        reservation = _declaration(state=IngestReservationState.OBSERVED, generation=3)
        _assert_transition_code(
            self,
            IngestReservationCode.INVALID_TRANSITION,
            reservation,
            expected_generation=3,
            next_state=IngestReservationState.OBSERVING,
        )

    def test_skipped_transition_rejected(self) -> None:
        reservation = _declaration(state=IngestReservationState.RESERVED, generation=1)
        _assert_transition_code(
            self,
            IngestReservationCode.INVALID_TRANSITION,
            reservation,
            expected_generation=1,
            next_state=IngestReservationState.OBSERVED,
        )

    def test_bound_is_terminal(self) -> None:
        reservation = _declaration(state=IngestReservationState.BOUND, generation=5)
        _assert_transition_code(
            self,
            IngestReservationCode.TERMINAL_STATE,
            reservation,
            expected_generation=5,
            next_state=IngestReservationState.FAILED,
        )

    def test_failed_is_terminal(self) -> None:
        reservation = _declaration(state=IngestReservationState.FAILED, generation=3)
        _assert_transition_code(
            self,
            IngestReservationCode.TERMINAL_STATE,
            reservation,
            expected_generation=3,
            next_state=IngestReservationState.OBSERVING,
        )

    def test_abandoned_is_terminal(self) -> None:
        reservation = _declaration(state=IngestReservationState.ABANDONED, generation=2)
        _assert_transition_code(
            self,
            IngestReservationCode.TERMINAL_STATE,
            reservation,
            expected_generation=2,
            next_state=IngestReservationState.OBSERVING,
        )

    def test_expected_generation_match(self) -> None:
        result = self._transition(
            IngestReservationState.RESERVED,
            IngestReservationState.OBSERVING,
            generation=17,
        )
        self.assertEqual(result.generation, 18)

    def test_expected_generation_mismatch(self) -> None:
        reservation = _declaration(state=IngestReservationState.RESERVED, generation=17)
        _assert_transition_code(
            self,
            IngestReservationCode.GENERATION_CONFLICT,
            reservation,
            expected_generation=16,
            next_state=IngestReservationState.OBSERVING,
        )

    def test_boolean_expected_generation_rejected(self) -> None:
        reservation = _initial()
        _assert_transition_code(
            self,
            IngestReservationCode.INVALID_GENERATION,
            reservation,
            expected_generation=True,
            next_state=IngestReservationState.OBSERVING,
        )

    def test_zero_expected_generation_rejected(self) -> None:
        reservation = _initial()
        _assert_transition_code(
            self,
            IngestReservationCode.INVALID_GENERATION,
            reservation,
            expected_generation=0,
            next_state=IngestReservationState.OBSERVING,
        )

    def test_invalid_next_state_rejected_without_coercion(self) -> None:
        reservation = _initial()
        _assert_transition_code(
            self,
            IngestReservationCode.INVALID_STATE,
            reservation,
            expected_generation=1,
            next_state="observing",
        )

    def test_generation_increments_exactly_once(self) -> None:
        original = _declaration(generation=99)
        result = transition_ingest_reservation(
            original,
            expected_generation=99,
            next_state=IngestReservationState.OBSERVING,
        )
        self.assertEqual(result.generation, 100)
        self.assertEqual(original.generation, 99)

    def test_transition_at_maximum_generation_fails_closed(self) -> None:
        original = _declaration(generation=MAX_GENERATION)
        _assert_transition_code(
            self,
            IngestReservationCode.INVALID_GENERATION,
            original,
            expected_generation=MAX_GENERATION,
            next_state=IngestReservationState.OBSERVING,
        )

    def test_identity_fields_remain_unchanged(self) -> None:
        original = _initial(owner="alice_smith")
        result = transition_ingest_reservation(
            original,
            expected_generation=1,
            next_state=IngestReservationState.OBSERVING,
        )
        self.assertEqual(result.owner, original.owner)
        self.assertIs(result.domain, original.domain)
        self.assertEqual(result.operation_id, original.operation_id)
        self.assertEqual(result.media_id, original.media_id)
        self.assertEqual(result.idempotency_key, original.idempotency_key)

    def test_original_object_remains_unchanged(self) -> None:
        original = _initial()
        result = transition_ingest_reservation(
            original,
            expected_generation=1,
            next_state=IngestReservationState.OBSERVING,
        )
        self.assertIs(original.state, IngestReservationState.RESERVED)
        self.assertEqual(original.generation, 1)
        self.assertIsNot(result, original)


class ReservationObjectTests(unittest.TestCase):
    def test_result_fields_are_exactly_approved(self) -> None:
        self.assertEqual(
            tuple(item.name for item in fields(IngestReservation)),
            (
                "owner",
                "domain",
                "operation_id",
                "media_id",
                "idempotency_key",
                "state",
                "generation",
            ),
        )

    def test_result_is_frozen(self) -> None:
        result = _initial()
        with self.assertRaises(FrozenInstanceError):
            result.owner = "mallory"  # type: ignore[misc]

    def test_result_is_slotted(self) -> None:
        self.assertFalse(hasattr(_initial(), "__dict__"))

    def test_fixed_non_identifying_repr(self) -> None:
        result = _initial(owner="repr_canary")
        rendered = repr(result)
        self.assertEqual(rendered, "IngestReservation()")
        for value in (
            result.owner,
            result.operation_id,
            result.media_id,
            result.idempotency_key,
            str(result.generation),
        ):
            self.assertNotIn(value, rendered)

    def test_no_unapproved_security_or_workflow_fields(self) -> None:
        result = _initial()
        forbidden = {
            "authority",
            "attestation",
            "byte_size",
            "sha256",
            "path",
            "filename",
            "timestamp",
            "database_version",
            "payload_bytes",
            "authenticated",
            "authorized",
            "durable",
            "published",
            "workflow_complete",
        }
        self.assertTrue(all(not hasattr(result, name) for name in forbidden))

    def test_foreign_object_rejected(self) -> None:
        _assert_transition_code(
            self,
            IngestReservationCode.INVALID_RESERVATION_OBJECT,
            object(),
            expected_generation=1,
            next_state=IngestReservationState.OBSERVING,
        )

    def test_uninitialized_forged_object_rejected(self) -> None:
        forged = object.__new__(IngestReservation)
        _assert_transition_code(
            self,
            IngestReservationCode.INVALID_RESERVATION_OBJECT,
            forged,
            expected_generation=1,
            next_state=IngestReservationState.OBSERVING,
        )

    def test_forged_invalid_fields_rejected(self) -> None:
        forged = object.__new__(IngestReservation)
        object.__setattr__(forged, "owner", "FORGED_OWNER_CANARY")
        object.__setattr__(forged, "domain", IngestReservationDomain.CALLS)
        object.__setattr__(forged, "operation_id", CALLS_OPERATION_ID)
        object.__setattr__(forged, "media_id", CALLS_MEDIA_ID)
        object.__setattr__(forged, "idempotency_key", VALID_IDEMPOTENCY_KEY)
        object.__setattr__(forged, "state", IngestReservationState.RESERVED)
        object.__setattr__(forged, "generation", 1)
        error = _assert_transition_code(
            self,
            IngestReservationCode.INVALID_RESERVATION_OBJECT,
            forged,
            expected_generation=1,
            next_state=IngestReservationState.OBSERVING,
        )
        self.assertNotIn("FORGED_OWNER_CANARY", str(error) + repr(error))

    def test_subclass_object_rejected(self) -> None:
        class ForgedReservation(IngestReservation):
            pass

        forged = object.__new__(ForgedReservation)
        _assert_transition_code(
            self,
            IngestReservationCode.INVALID_RESERVATION_OBJECT,
            forged,
            expected_generation=1,
            next_state=IngestReservationState.OBSERVING,
        )


class ReservationPrivacyTests(unittest.TestCase):
    def test_every_public_error_has_fixed_string_and_repr(self) -> None:
        for code in IngestReservationCode:
            with self.subTest(code=code):
                error = IngestReservationError(code)
                self.assertEqual(str(error), code.value)
                self.assertEqual(
                    repr(error),
                    f"IngestReservationError(code={code.value!r})",
                )

    def test_owner_canary_absent_from_error_string_and_repr(self) -> None:
        canary = "OWNER_PRIVACY_CANARY"
        error = _assert_initial_code(
            self,
            IngestReservationCode.INVALID_OWNER,
            owner=canary,
        )
        self.assertNotIn(canary, str(error) + repr(error))

    def test_idempotency_canary_absent_from_error_string_and_repr(self) -> None:
        canary = "IDEMPOTENCY_PRIVACY_CANARY"
        error = _assert_initial_code(
            self,
            IngestReservationCode.INVALID_IDEMPOTENCY_KEY,
            idempotency_key=canary,
        )
        self.assertNotIn(canary, str(error) + repr(error))

    def test_hostile_string_subclass_does_not_leak(self) -> None:
        canary = "HOSTILE_STRING_PRIVACY_CANARY"

        class HostileString(str):
            def __len__(self):
                raise RuntimeError(canary)

            def __repr__(self):
                raise RuntimeError(canary)

            def __str__(self):
                raise RuntimeError(canary)

        error = _assert_initial_code(
            self,
            IngestReservationCode.INVALID_OWNER,
            owner=HostileString("alice"),
        )
        self.assertNotIn(canary, str(error) + repr(error))

    def test_exception_chaining_is_suppressed_for_forged_object(self) -> None:
        forged = object.__new__(IngestReservation)
        error = _assert_transition_code(
            self,
            IngestReservationCode.INVALID_RESERVATION_OBJECT,
            forged,
            expected_generation=1,
            next_state=IngestReservationState.OBSERVING,
        )
        self.assertIsNone(error.__cause__)
        self.assertTrue(error.__suppress_context__)

    def test_privacy_canary_absent_from_uncaught_traceback(self) -> None:
        canary = "TRACEBACK_PRIVACY_CANARY"
        try:
            _initial(owner=canary)
        except IngestReservationError as error:
            rendered = "".join(
                traceback.TracebackException.from_exception(error).format()
            )
        else:  # pragma: no cover
            self.fail("invalid synthetic owner unexpectedly accepted")
        self.assertNotIn(canary, rendered)

    def test_privacy_canary_absent_from_stdout_and_stderr(self) -> None:
        canary = "STREAM_PRIVACY_CANARY"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                _initial(owner=canary)
            except IngestReservationError:
                pass
        self.assertNotIn(canary, stdout.getvalue())
        self.assertNotIn(canary, stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")


class ReservationStaticPurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = PRODUCTION_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def _import_roots(self) -> set[str]:
        roots: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        return roots

    def test_standard_library_only_production_imports(self) -> None:
        allowed = {"__future__", "dataclasses", "enum", "re", "typing"}
        self.assertLessEqual(self._import_roots(), allowed)

    def test_no_prohibited_production_imports(self) -> None:
        prohibited = {
            "os",
            "pathlib",
            "tempfile",
            "shutil",
            "subprocess",
            "socket",
            "logging",
            "time",
            "datetime",
            "random",
            "secrets",
            "uuid",
            "asyncio",
            "sqlalchemy",
            "numpy",
            "torch",
            "av",
            "faster_whisper",
            "chromadb",
        }
        self.assertTrue(self._import_roots().isdisjoint(prohibited))

    def test_no_application_or_contract_imports(self) -> None:
        modules: list[str] = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name.lower() for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module.lower())
        forbidden = (
            "src.",
            "app",
            "route",
            "core",
            "auth",
            "database",
            "sqlalchemy",
            "upload",
            "worker",
            "marketmatch_media_attestation",
            "marketmatch_original_media_observation",
            "marketmatch_stt_input",
            "whisper",
            "ffmpeg",
            "chroma",
            "rag",
        )
        self.assertFalse(
            any(any(token in module for token in forbidden) for module in modules)
        )

    def test_no_open_print_logging_or_dynamic_execution(self) -> None:
        forbidden = {"open", "print", "exec", "eval", "compile", "__import__"}
        calls = {
            node.func.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue(calls.isdisjoint(forbidden))

    def test_no_environment_filesystem_network_process_or_time_access(self) -> None:
        forbidden = {
            "environ",
            "getenv",
            "getcwd",
            "open",
            "read",
            "write",
            "unlink",
            "remove",
            "mkdir",
            "makedirs",
            "connect",
            "send",
            "recv",
            "Popen",
            "run",
            "system",
            "execute",
            "commit",
            "rollback",
            "publish",
            "now",
            "today",
            "sleep",
        }
        attributes = {
            node.attr for node in ast.walk(self.tree) if isinstance(node, ast.Attribute)
        }
        self.assertTrue(attributes.isdisjoint(forbidden))

    def test_no_identifier_generation_or_randomness(self) -> None:
        forbidden_calls = {
            "uuid4",
            "token_hex",
            "token_urlsafe",
            "randint",
            "choice",
            "randbytes",
        }
        calls = {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Name, ast.Attribute))
        }
        self.assertTrue(calls.isdisjoint(forbidden_calls))

    def test_no_authentication_authorization_or_sql_calls(self) -> None:
        forbidden_calls = {
            "authenticate",
            "authorize",
            "require_user",
            "require_privilege",
            "reserve",
            "flush",
            "commit",
            "execute",
            "query",
            "add",
            "delete",
        }
        calls = {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Name, ast.Attribute))
        }
        self.assertTrue(calls.isdisjoint(forbidden_calls))

    def test_no_observation_attestation_stt_or_publication_calls(self) -> None:
        forbidden = {
            "observe_original_media",
            "validate_original_media_attestation",
            "bind_attestation_for_phase3p",
            "transcribe",
            "transcribe_file",
            "publish",
            "unlink",
            "remove",
        }
        calls = {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Name, ast.Attribute))
        }
        self.assertTrue(calls.isdisjoint(forbidden))

    def test_public_functions_do_not_mutate_inputs(self) -> None:
        public_names = {
            "create_ingest_reservation",
            "validate_ingest_reservation_declaration",
            "transition_ingest_reservation",
        }
        for function in self.tree.body:
            if not isinstance(function, ast.FunctionDef) or function.name not in public_names:
                continue
            arguments = {
                arg.arg
                for arg in (
                    list(function.args.args)
                    + list(function.args.kwonlyargs)
                )
            }
            for node in ast.walk(function):
                targets: list[ast.expr] = []
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    if isinstance(node, ast.Assign):
                        targets.extend(node.targets)
                    else:
                        targets.append(node.target)
                for target in targets:
                    root = target
                    while isinstance(root, (ast.Attribute, ast.Subscript)):
                        root = root.value
                    self.assertFalse(
                        isinstance(root, ast.Name) and root.id in arguments,
                        f"{function.name} mutates input {root.id}",
                    )

    def test_transition_public_signature_is_exact(self) -> None:
        target = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "transition_ingest_reservation"
        )
        self.assertEqual(tuple(arg.arg for arg in target.args.args), ("reservation",))
        self.assertEqual(
            tuple(arg.arg for arg in target.args.kwonlyargs),
            ("expected_generation", "next_state"),
        )
        self.assertIsNone(target.args.vararg)
        self.assertIsNone(target.args.kwarg)

    def test_no_async_yield_or_await(self) -> None:
        prohibited = (ast.AsyncFunctionDef, ast.Await, ast.Yield, ast.YieldFrom)
        self.assertFalse(any(isinstance(node, prohibited) for node in ast.walk(self.tree)))

    def test_no_baseexception_catch(self) -> None:
        caught = {
            handler.type.id
            for handler in ast.walk(self.tree)
            if isinstance(handler, ast.ExceptHandler)
            and isinstance(handler.type, ast.Name)
        }
        self.assertNotIn("BaseException", caught)

    def test_reservation_dataclass_has_exact_fields(self) -> None:
        self.assertEqual(
            tuple(item.name for item in fields(IngestReservation)),
            (
                "owner",
                "domain",
                "operation_id",
                "media_id",
                "idempotency_key",
                "state",
                "generation",
            ),
        )

    def test_reservation_repr_is_fixed(self) -> None:
        target = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef) and node.name == "IngestReservation"
        )
        repr_function = next(
            node
            for node in target.body
            if isinstance(node, ast.FunctionDef) and node.name == "__repr__"
        )
        returns = [node for node in ast.walk(repr_function) if isinstance(node, ast.Return)]
        self.assertEqual(len(returns), 1)
        self.assertIsInstance(returns[0].value, ast.Constant)
        self.assertEqual(returns[0].value.value, "IngestReservation()")

    def test_source_compiles_without_execution(self) -> None:
        compile(self.source, str(PRODUCTION_PATH), "exec")

    def test_worktree_has_no_unapproved_changed_paths(self) -> None:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        changed = {line[3:] for line in completed.stdout.splitlines() if line}
        self.assertTrue(changed <= APPROVED_PATHS)
        self.assertTrue(all((ROOT / path).is_file() for path in APPROVED_PATHS))


if __name__ == "__main__":
    unittest.main()
