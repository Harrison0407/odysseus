"""Dedicated pure-data tests for original-media attestation validation."""

from __future__ import annotations

import ast
import copy
from dataclasses import FrozenInstanceError, fields, replace
import hashlib
import io
import json
from pathlib import Path
import subprocess
import traceback
import unittest
from unittest import mock

from src.marketmatch_media_attestation import (
    AttestationAuthority,
    MAX_ATTESTATION_BYTES,
    MAX_ORIGINAL_MEDIA_BYTES,
    MediaAttestationCode,
    MediaAttestationError,
    MediaDomain,
    ORIGINAL_MEDIA_ATTESTATION_SCHEMA,
    OriginalMediaRole,
    Phase3PExpectedMetadata,
    ValidatedOriginalMediaAttestation,
    bind_attestation_for_phase3p,
    validate_original_media_attestation,
)


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_PATH = ROOT / "src" / "marketmatch_media_attestation.py"
CALLS_OPERATION_ID = "mmop-calls-" + "a" * 26
VIDEOS_OPERATION_ID = "mmop-videos-" + "b" * 26
CALLS_MEDIA_ID = "mmmedia-calls-" + "c" * 26
VIDEOS_MEDIA_ID = "mmmedia-videos-" + "d" * 26
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _canonical(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _subject(
    *,
    domain: str = "calls",
    operation_id: str | None = None,
    media_id: str | None = None,
    media_role: str | None = None,
    byte_size: object = 7,
    sha256: object = "1" * 64,
    authority: object = "verified_ingest",
    schema: object = ORIGINAL_MEDIA_ATTESTATION_SCHEMA,
) -> dict[str, object]:
    calls = domain == "calls"
    return {
        "authority": authority,
        "byte_size": byte_size,
        "domain": domain,
        "media_id": media_id if media_id is not None else (CALLS_MEDIA_ID if calls else VIDEOS_MEDIA_ID),
        "media_role": media_role if media_role is not None else ("original_audio" if calls else "original_video"),
        "operation_id": operation_id if operation_id is not None else (CALLS_OPERATION_ID if calls else VIDEOS_OPERATION_ID),
        "schema": schema,
        "sha256": sha256,
    }


def _attestation(**changes: object) -> dict[str, object]:
    subject = _subject(**changes)
    result = dict(subject)
    result["attestation_digest"] = hashlib.sha256(_canonical(subject)).hexdigest()
    return result


def _canonical_attestation(**changes: object) -> bytes:
    return _canonical(_attestation(**changes))


def _assert_code(
    test: unittest.TestCase,
    code: MediaAttestationCode,
    value: object,
) -> MediaAttestationError:
    with test.assertRaises(MediaAttestationError) as caught:
        validate_original_media_attestation(value)  # type: ignore[arg-type]
    test.assertIs(caught.exception.code, code)
    return caught.exception


def _validated_calls(**changes: object) -> ValidatedOriginalMediaAttestation:
    return validate_original_media_attestation(_attestation(**changes))


class MediaAttestationValidationTests(unittest.TestCase):
    def test_valid_calls_original_audio_attestation(self) -> None:
        result = validate_original_media_attestation(_attestation())
        self.assertIs(result.domain, MediaDomain.CALLS)
        self.assertIs(result.media_role, OriginalMediaRole.ORIGINAL_AUDIO)

    def test_valid_videos_original_video_attestation(self) -> None:
        result = validate_original_media_attestation(_attestation(domain="videos"))
        self.assertIs(result.domain, MediaDomain.VIDEOS)
        self.assertIs(result.media_role, OriginalMediaRole.ORIGINAL_VIDEO)

    def test_valid_canonical_bytes(self) -> None:
        raw = _canonical_attestation()
        result = validate_original_media_attestation(raw)
        self.assertEqual(result.canonical_bytes, raw)

    def test_valid_parsed_builtin_mapping(self) -> None:
        result = validate_original_media_attestation(_attestation())
        self.assertEqual(result.schema, ORIGINAL_MEDIA_ATTESTATION_SCHEMA)

    def test_unknown_schema(self) -> None:
        _assert_code(self, MediaAttestationCode.UNKNOWN_SCHEMA, _attestation(schema="marketmatch-original-media-attestation-v2"))

    def test_missing_schema(self) -> None:
        value = _attestation()
        del value["schema"]
        _assert_code(self, MediaAttestationCode.MISSING_FIELD, value)

    def test_unknown_top_level_field(self) -> None:
        value = _attestation()
        value["extra"] = "rejected"
        _assert_code(self, MediaAttestationCode.UNKNOWN_FIELD, value)

    def test_missing_required_field(self) -> None:
        value = _attestation()
        del value["media_id"]
        _assert_code(self, MediaAttestationCode.MISSING_FIELD, value)

    def test_invalid_domain(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_DOMAIN, _attestation(domain="documents"))

    def test_calls_video_role_confusion(self) -> None:
        _assert_code(self, MediaAttestationCode.DOMAIN_ROLE_MISMATCH, _attestation(media_role="original_video"))

    def test_videos_audio_role_confusion(self) -> None:
        _assert_code(self, MediaAttestationCode.DOMAIN_ROLE_MISMATCH, _attestation(domain="videos", media_role="original_audio"))

    def test_invalid_operation_id(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_OPERATION_ID, _attestation(operation_id="not-an-operation"))

    def test_operation_domain_prefix_mismatch(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_OPERATION_ID, _attestation(operation_id=VIDEOS_OPERATION_ID))

    def test_operation_id_unicode_rejected(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_OPERATION_ID, _attestation(operation_id="mmop-calls-" + "a" * 25 + "é"))

    def test_operation_id_path_semantics_rejected(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_OPERATION_ID, _attestation(operation_id="../../private/media"))

    def test_invalid_media_id(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_MEDIA_ID, _attestation(media_id="not-media"))

    def test_media_domain_prefix_mismatch(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_MEDIA_ID, _attestation(media_id=VIDEOS_MEDIA_ID))

    def test_media_id_unicode_rejected(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_MEDIA_ID, _attestation(media_id="mmmedia-calls-" + "c" * 25 + "é"))

    def test_empty_media_role(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_MEDIA_ROLE, _attestation(media_role=""))

    def test_unknown_media_role(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_MEDIA_ROLE, _attestation(media_role="transcript"))

    def test_negative_byte_size(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_BYTE_SIZE, _attestation(byte_size=-1))

    def test_boolean_byte_size(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_BYTE_SIZE, _attestation(byte_size=True))

    def test_float_byte_size(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_BYTE_SIZE, _attestation(byte_size=1.0))

    def test_string_byte_size(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_BYTE_SIZE, _attestation(byte_size="7"))

    def test_byte_size_above_100_mib(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_BYTE_SIZE, _attestation(byte_size=MAX_ORIGINAL_MEDIA_BYTES + 1))

    def test_zero_byte_size_accepted(self) -> None:
        result = validate_original_media_attestation(_attestation(byte_size=0, sha256=EMPTY_SHA256))
        self.assertEqual(result.byte_size, 0)

    def test_exact_maximum_byte_size_accepted(self) -> None:
        result = validate_original_media_attestation(_attestation(byte_size=MAX_ORIGINAL_MEDIA_BYTES))
        self.assertEqual(result.byte_size, MAX_ORIGINAL_MEDIA_BYTES)

    def test_valid_lowercase_sha256(self) -> None:
        result = validate_original_media_attestation(_attestation())
        self.assertEqual(result.sha256, "1" * 64)

    def test_uppercase_sha256_rejected(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_SHA256, _attestation(sha256="A" * 64))

    def test_short_sha256_rejected(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_SHA256, _attestation(sha256="a" * 63))

    def test_prefixed_sha256_rejected(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_SHA256, _attestation(sha256="sha256:" + "a" * 64))

    def test_nonhex_sha256_rejected(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_SHA256, _attestation(sha256="g" * 64))

    def test_missing_attestation_digest(self) -> None:
        value = _attestation()
        del value["attestation_digest"]
        _assert_code(self, MediaAttestationCode.ATTESTATION_DIGEST_MISSING, value)

    def test_attestation_digest_mismatch(self) -> None:
        value = _attestation()
        value["attestation_digest"] = "0" * 64
        _assert_code(self, MediaAttestationCode.ATTESTATION_DIGEST_MISMATCH, value)

    def test_malformed_attestation_digest_rejected(self) -> None:
        value = _attestation()
        value["attestation_digest"] = "sha256:" + "0" * 64
        _assert_code(self, MediaAttestationCode.ATTESTATION_DIGEST_MISMATCH, value)

    def test_digest_excludes_only_attestation_digest(self) -> None:
        value = _attestation()
        subject = {key: item for key, item in value.items() if key != "attestation_digest"}
        expected = hashlib.sha256(_canonical(subject)).hexdigest()
        self.assertEqual(value["attestation_digest"], expected)
        self.assertNotEqual(hashlib.sha256(_canonical(value)).hexdigest(), expected)

    def test_any_subject_change_alters_digest(self) -> None:
        first = _attestation(byte_size=7)
        second = _attestation(byte_size=8)
        self.assertNotEqual(first["attestation_digest"], second["attestation_digest"])
        validate_original_media_attestation(first)
        validate_original_media_attestation(second)

    def test_each_subject_field_change_alters_digest(self) -> None:
        original = _subject()
        original_digest = hashlib.sha256(_canonical(original)).hexdigest()
        changes = {
            "authority": "verified_ingesu",
            "byte_size": 8,
            "domain": "videos",
            "media_id": "mmmedia-calls-" + "e" * 26,
            "media_role": "original_video",
            "operation_id": "mmop-calls-" + "f" * 26,
            "schema": "marketmatch-original-media-attestation-v2",
            "sha256": "2" * 64,
        }
        self.assertEqual(set(changes), set(original))
        for field_name, replacement in changes.items():
            with self.subTest(field_name=field_name):
                changed = dict(original)
                changed[field_name] = replacement
                self.assertNotEqual(hashlib.sha256(_canonical(changed)).hexdigest(), original_digest)

    def test_invalid_authority(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_AUTHORITY, _attestation(authority="unknown"))

    def test_client_claim_rejected(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_AUTHORITY, _attestation(authority="client_claim"))

    def test_filesystem_stat_only_rejected(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_AUTHORITY, _attestation(authority="filesystem_stat_only"))

    def test_database_row_only_rejected(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_AUTHORITY, _attestation(authority="database_row_only"))

    def test_all_other_disallowed_authorities_rejected(self) -> None:
        for authority in (
            "filename_metadata",
            "decoder_claim",
            "manifest_claim",
            "user_input",
            "imported_legacy_path",
        ):
            with self.subTest(authority=authority):
                _assert_code(self, MediaAttestationCode.INVALID_AUTHORITY, _attestation(authority=authority))


class MediaAttestationCanonicalJSONTests(unittest.TestCase):
    def test_duplicate_json_key_rejected(self) -> None:
        raw = b'{"schema":"one","schema":"two"}'
        _assert_code(self, MediaAttestationCode.DUPLICATE_JSON_KEY, raw)

    def test_bom_rejected(self) -> None:
        _assert_code(self, MediaAttestationCode.NON_CANONICAL_JSON, b"\xef\xbb\xbf" + _canonical_attestation())

    def test_trailing_json_rejected(self) -> None:
        _assert_code(self, MediaAttestationCode.NON_CANONICAL_JSON, _canonical_attestation() + b"{}")

    def test_trailing_whitespace_rejected(self) -> None:
        _assert_code(self, MediaAttestationCode.NON_CANONICAL_JSON, _canonical_attestation() + b" ")

    def test_noncanonical_internal_whitespace_rejected(self) -> None:
        raw = _canonical_attestation().replace(b",", b", ", 1)
        _assert_code(self, MediaAttestationCode.NON_CANONICAL_JSON, raw)

    def test_noncanonical_key_order_rejected(self) -> None:
        value = _attestation()
        raw = json.dumps(value, separators=(",", ":"), sort_keys=False).encode("ascii")
        self.assertNotEqual(raw, _canonical(value))
        _assert_code(self, MediaAttestationCode.NON_CANONICAL_JSON, raw)

    def test_unicode_json_rejected(self) -> None:
        _assert_code(self, MediaAttestationCode.NON_CANONICAL_JSON, b'{"x":"\xc3\xa9"}')

    def test_unicode_escape_rejected_after_parsing(self) -> None:
        raw = _canonical_attestation().replace(b"verified_ingest", b"verified_inges\\u0074")
        _assert_code(self, MediaAttestationCode.NON_CANONICAL_JSON, raw)

    def test_float_json_rejected(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_JSON, b'{"byte_size":1.0}')

    def test_nan_json_rejected(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_JSON, b'{"byte_size":NaN}')

    def test_infinity_json_rejected(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_JSON, b'{"byte_size":Infinity}')

    def test_empty_json_rejected(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_JSON, b"")

    def test_oversized_json_rejected_before_decode(self) -> None:
        raw = b"{" + b"x" * MAX_ATTESTATION_BYTES + b"}"
        _assert_code(self, MediaAttestationCode.INVALID_JSON, raw)

    def test_non_object_json_rejected(self) -> None:
        _assert_code(self, MediaAttestationCode.INVALID_INPUT_TYPE, b"[]")


class Phase3PBindingTests(unittest.TestCase):
    def test_valid_phase3p_metadata_binding(self) -> None:
        attestation = _validated_calls(byte_size=7, sha256="2" * 64)
        result = bind_attestation_for_phase3p(
            attestation,
            expected_domain=MediaDomain.CALLS,
            expected_operation_id=CALLS_OPERATION_ID,
            byte_limit=10,
        )
        self.assertEqual(result.expected_byte_size, 7)
        self.assertEqual(result.expected_sha256, "2" * 64)

    def test_valid_video_binding(self) -> None:
        attestation = validate_original_media_attestation(_attestation(domain="videos"))
        result = bind_attestation_for_phase3p(
            attestation,
            expected_domain=MediaDomain.VIDEOS,
            expected_operation_id=VIDEOS_OPERATION_ID,
            byte_limit=10,
        )
        self.assertEqual(result.expected_byte_size, 7)

    def test_expected_domain_mismatch(self) -> None:
        with self.assertRaises(MediaAttestationError) as caught:
            bind_attestation_for_phase3p(
                _validated_calls(),
                expected_domain=MediaDomain.VIDEOS,
                expected_operation_id=VIDEOS_OPERATION_ID,
                byte_limit=10,
            )
        self.assertIs(caught.exception.code, MediaAttestationCode.EXPECTED_DOMAIN_MISMATCH)

    def test_invalid_expected_domain_type(self) -> None:
        with self.assertRaises(MediaAttestationError) as caught:
            bind_attestation_for_phase3p(
                _validated_calls(),
                expected_domain="calls",  # type: ignore[arg-type]
                expected_operation_id=CALLS_OPERATION_ID,
                byte_limit=10,
            )
        self.assertIs(caught.exception.code, MediaAttestationCode.EXPECTED_DOMAIN_MISMATCH)

    def test_expected_operation_mismatch(self) -> None:
        other = "mmop-calls-" + "z" * 26
        with self.assertRaises(MediaAttestationError) as caught:
            bind_attestation_for_phase3p(
                _validated_calls(),
                expected_domain=MediaDomain.CALLS,
                expected_operation_id=other,
                byte_limit=10,
            )
        self.assertIs(caught.exception.code, MediaAttestationCode.EXPECTED_OPERATION_MISMATCH)

    def test_invalid_expected_operation(self) -> None:
        with self.assertRaises(MediaAttestationError) as caught:
            bind_attestation_for_phase3p(
                _validated_calls(),
                expected_domain=MediaDomain.CALLS,
                expected_operation_id="../../operation",
                byte_limit=10,
            )
        self.assertIs(caught.exception.code, MediaAttestationCode.EXPECTED_OPERATION_MISMATCH)

    def test_invalid_caller_limit_zero(self) -> None:
        self._assert_invalid_limit(0)

    def test_invalid_caller_limit_negative(self) -> None:
        self._assert_invalid_limit(-1)

    def test_invalid_caller_limit_boolean(self) -> None:
        self._assert_invalid_limit(True)

    def test_invalid_caller_limit_float(self) -> None:
        self._assert_invalid_limit(10.0)

    def test_caller_limit_above_100_mib(self) -> None:
        self._assert_invalid_limit(MAX_ORIGINAL_MEDIA_BYTES + 1)

    def _assert_invalid_limit(self, limit: object) -> None:
        with self.assertRaises(MediaAttestationError) as caught:
            bind_attestation_for_phase3p(
                _validated_calls(),
                expected_domain=MediaDomain.CALLS,
                expected_operation_id=CALLS_OPERATION_ID,
                byte_limit=limit,  # type: ignore[arg-type]
            )
        self.assertIs(caught.exception.code, MediaAttestationCode.INVALID_BYTE_LIMIT)

    def test_attested_size_above_caller_limit(self) -> None:
        with self.assertRaises(MediaAttestationError) as caught:
            bind_attestation_for_phase3p(
                _validated_calls(byte_size=11),
                expected_domain=MediaDomain.CALLS,
                expected_operation_id=CALLS_OPERATION_ID,
                byte_limit=10,
            )
        self.assertIs(caught.exception.code, MediaAttestationCode.INPUT_LIMIT_EXCEEDED)

    def test_invalid_attestation_object_rejected(self) -> None:
        with self.assertRaises(MediaAttestationError) as caught:
            bind_attestation_for_phase3p(
                object(),  # type: ignore[arg-type]
                expected_domain=MediaDomain.CALLS,
                expected_operation_id=CALLS_OPERATION_ID,
                byte_limit=10,
            )
        self.assertIs(caught.exception.code, MediaAttestationCode.INVALID_INPUT_TYPE)

    def test_manually_altered_validated_object_rejected(self) -> None:
        altered = replace(_validated_calls(), byte_size=6)
        with self.assertRaises(MediaAttestationError) as caught:
            bind_attestation_for_phase3p(
                altered,
                expected_domain=MediaDomain.CALLS,
                expected_operation_id=CALLS_OPERATION_ID,
                byte_limit=10,
            )
        self.assertIs(caught.exception.code, MediaAttestationCode.INVALID_INPUT_TYPE)

    def test_forged_unicode_fields_return_only_fixed_errors(self) -> None:
        original = _validated_calls()
        cases = (
            ("operation_id", CALLS_OPERATION_ID[:-1] + "é", MediaAttestationCode.INVALID_OPERATION_ID),
            ("media_id", CALLS_MEDIA_ID[:-1] + "é", MediaAttestationCode.INVALID_MEDIA_ID),
            ("sha256", "1" * 63 + "é", MediaAttestationCode.INVALID_SHA256),
            ("attestation_digest", "1" * 63 + "é", MediaAttestationCode.ATTESTATION_DIGEST_MISMATCH),
        )
        for field_name, replacement, expected_code in cases:
            with self.subTest(field_name=field_name):
                forged = replace(original, **{field_name: replacement})
                with self.assertRaises(MediaAttestationError) as caught:
                    bind_attestation_for_phase3p(
                        forged,
                        expected_domain=MediaDomain.CALLS,
                        expected_operation_id=CALLS_OPERATION_ID,
                        byte_limit=10,
                    )
                self.assertIs(caught.exception.code, expected_code)
                self.assertNotIn("é", str(caught.exception) + repr(caught.exception))

    def test_forged_oversized_fields_return_only_fixed_errors(self) -> None:
        original = _validated_calls()
        canary = "OVERSIZED_PRIVATE_CANARY" * 10_000
        cases = (
            ("operation_id", canary, MediaAttestationCode.INVALID_OPERATION_ID),
            ("media_id", canary, MediaAttestationCode.INVALID_MEDIA_ID),
            ("sha256", canary, MediaAttestationCode.INVALID_SHA256),
            ("attestation_digest", canary, MediaAttestationCode.ATTESTATION_DIGEST_MISMATCH),
        )
        for field_name, replacement, expected_code in cases:
            with self.subTest(field_name=field_name):
                forged = replace(original, **{field_name: replacement})
                with self.assertRaises(MediaAttestationError) as caught:
                    bind_attestation_for_phase3p(
                        forged,
                        expected_domain=MediaDomain.CALLS,
                        expected_operation_id=CALLS_OPERATION_ID,
                        byte_limit=10,
                    )
                self.assertIs(caught.exception.code, expected_code)
                self.assertNotIn("OVERSIZED_PRIVATE_CANARY", str(caught.exception) + repr(caught.exception))

    def test_phase3p_metadata_has_only_two_fields(self) -> None:
        self.assertEqual(
            tuple(item.name for item in fields(Phase3PExpectedMetadata)),
            ("expected_byte_size", "expected_sha256"),
        )

    def test_phase3p_metadata_is_immutable_and_opaque_in_repr(self) -> None:
        result = bind_attestation_for_phase3p(
            _validated_calls(),
            expected_domain=MediaDomain.CALLS,
            expected_operation_id=CALLS_OPERATION_ID,
            byte_limit=10,
        )
        with self.assertRaises(FrozenInstanceError):
            result.expected_byte_size = 8  # type: ignore[misc]
        self.assertNotIn("1" * 64, repr(result))


class MediaAttestationImmutabilityPrivacyTests(unittest.TestCase):
    def test_oversized_builtin_mapping_rejected_before_copy(self) -> None:
        value = {f"unknown_{index}": index for index in range(10_000)}
        _assert_code(self, MediaAttestationCode.UNKNOWN_FIELD, value)
        self.assertEqual(len(value), 10_000)

    def test_caller_mapping_remains_unchanged(self) -> None:
        value = _attestation()
        before = copy.deepcopy(value)
        validate_original_media_attestation(value)
        self.assertEqual(value, before)

    def test_result_does_not_retain_caller_mapping(self) -> None:
        value = _attestation()
        result = validate_original_media_attestation(value)
        value["operation_id"] = "changed"
        self.assertEqual(result.operation_id, CALLS_OPERATION_ID)

    def test_returned_object_is_frozen_slotted_and_exact(self) -> None:
        result = _validated_calls()
        with self.assertRaises(FrozenInstanceError):
            result.byte_size = 9  # type: ignore[misc]
        self.assertFalse(hasattr(result, "__dict__"))
        self.assertEqual(
            tuple(item.name for item in fields(ValidatedOriginalMediaAttestation)),
            (
                "schema", "domain", "operation_id", "media_id", "media_role",
                "byte_size", "sha256", "authority", "attestation_digest",
                "canonical_bytes",
            ),
        )

    def test_canonical_bytes_are_deterministic(self) -> None:
        first = validate_original_media_attestation(_attestation())
        second = validate_original_media_attestation(_attestation())
        self.assertEqual(first.canonical_bytes, second.canonical_bytes)
        self.assertEqual(first, second)

    def test_sensitive_metadata_excluded_from_repr(self) -> None:
        result = _validated_calls()
        rendered = repr(result)
        for canary in (CALLS_OPERATION_ID, CALLS_MEDIA_ID, "1" * 64, str(result.byte_size)):
            self.assertNotIn(canary, rendered)

    def test_exception_string_and_repr_are_fixed(self) -> None:
        canary = "PRIVATE" + "_ATTESTATION_CANARY"
        error = _assert_code(self, MediaAttestationCode.INVALID_OPERATION_ID, _attestation(operation_id=canary))
        self.assertEqual(str(error), MediaAttestationCode.INVALID_OPERATION_ID.value)
        self.assertNotIn(canary, str(error) + repr(error))

    def test_privacy_canaries_rejected_without_echo(self) -> None:
        canaries = (
            "/home/private/original.wav",
            "C:\\Users\\private\\video.mp4",
            "\\\\server\\share\\media",
            "https://private.invalid/media",
            "owner@example.invalid",
            "192.0.2.10",
            "tenant.database.internal",
            "bucket-secret-token",
        )
        for canary in canaries:
            with self.subTest(canary=canary):
                error = _assert_code(self, MediaAttestationCode.INVALID_MEDIA_ID, _attestation(media_id=canary))
                self.assertNotIn(canary, str(error) + repr(error))

    def test_uncaught_traceback_does_not_echo_canary(self) -> None:
        canary = "TRACEBACK" + "_MEDIA_PRIVATE_CANARY"
        try:
            validate_original_media_attestation(_attestation(media_id=canary))
        except MediaAttestationError as error:
            rendered = "".join(traceback.TracebackException.from_exception(error).format())
        else:  # pragma: no cover
            self.fail("validation unexpectedly succeeded")
        self.assertNotIn(canary, rendered)

    def test_stdout_and_stderr_remain_empty(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            validate_original_media_attestation(_attestation())
            _assert_code(self, MediaAttestationCode.INVALID_AUTHORITY, _attestation(authority="client_claim"))
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_hostile_mapping_subclass_does_not_leak(self) -> None:
        class HostileMapping(dict):
            def keys(self):
                raise RuntimeError("HOSTILE_MAPPING_PRIVATE_CANARY")

            def __repr__(self):
                raise RuntimeError("HOSTILE_MAPPING_PRIVATE_CANARY")

        error = _assert_code(self, MediaAttestationCode.INVALID_INPUT_TYPE, HostileMapping(_attestation()))
        self.assertNotIn("HOSTILE_MAPPING_PRIVATE_CANARY", str(error) + repr(error))

    def test_hostile_string_subclass_does_not_leak(self) -> None:
        class HostileString(str):
            def __str__(self):
                raise RuntimeError("HOSTILE_STRING_PRIVATE_CANARY")

            def __repr__(self):
                raise RuntimeError("HOSTILE_STRING_PRIVATE_CANARY")

        value = _attestation()
        value["operation_id"] = HostileString(CALLS_OPERATION_ID)
        error = _assert_code(self, MediaAttestationCode.INVALID_OPERATION_ID, value)
        self.assertNotIn("HOSTILE_STRING_PRIVATE_CANARY", str(error) + repr(error))

    def test_hostile_bytes_subclass_does_not_leak(self) -> None:
        class HostileBytes(bytes):
            def __repr__(self):
                raise RuntimeError("HOSTILE_BYTES_PRIVATE_CANARY")

        error = _assert_code(self, MediaAttestationCode.INVALID_INPUT_TYPE, HostileBytes(_canonical_attestation()))
        self.assertNotIn("HOSTILE_BYTES_PRIVATE_CANARY", str(error) + repr(error))

    def test_parse_error_has_no_exception_chain(self) -> None:
        error = _assert_code(self, MediaAttestationCode.INVALID_JSON, b"{")
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)


class MediaAttestationStaticPurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = PRODUCTION_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename=str(PRODUCTION_PATH))

    def test_standard_library_only_imports(self) -> None:
        allowed = {
            "__future__", "dataclasses", "enum", "hashlib", "hmac", "json",
            "re", "typing",
        }
        roots: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        self.assertTrue(roots <= allowed)

    def test_no_prohibited_imports(self) -> None:
        prohibited = {
            "os", "pathlib", "tempfile", "shutil", "socket", "subprocess",
            "logging", "time", "datetime", "random", "secrets", "uuid",
            "sqlalchemy", "requests", "numpy", "torch", "av",
        }
        roots: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0].lower() for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0].lower())
        self.assertTrue(roots.isdisjoint(prohibited))

    def test_no_application_database_upload_model_or_worker_imports(self) -> None:
        modules: list[str] = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name.lower() for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module.lower())
        forbidden_tokens = (
            "src.", "app", "route", "core", "database", "upload", "worker",
            "whisper", "ffmpeg", "chroma", "rag", "sqlalchemy", "numpy",
            "torch", "av",
        )
        self.assertFalse(any(any(token in name for token in forbidden_tokens) for name in modules))

    def test_no_open_print_logging_or_dynamic_execution_calls(self) -> None:
        forbidden = {"open", "print", "exec", "eval", "compile", "__import__"}
        calls = {
            node.func.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue(calls.isdisjoint(forbidden))

    def test_no_filesystem_environment_network_process_or_sql_attributes(self) -> None:
        forbidden = {
            "environ", "getenv", "getcwd", "open", "read", "write", "unlink",
            "remove", "mkdir", "makedirs", "connect", "send", "recv", "run",
            "Popen", "system", "execute", "commit", "rollback", "publish",
        }
        attributes = {node.attr for node in ast.walk(self.tree) if isinstance(node, ast.Attribute)}
        self.assertTrue(attributes.isdisjoint(forbidden))

    def test_no_time_randomness_or_generation(self) -> None:
        forbidden = {"now", "today", "time", "sleep", "random", "token_hex", "uuid4"}
        attributes = {node.attr for node in ast.walk(self.tree) if isinstance(node, ast.Attribute)}
        names = {node.id for node in ast.walk(self.tree) if isinstance(node, ast.Name)}
        self.assertTrue(attributes.isdisjoint(forbidden))
        self.assertTrue(names.isdisjoint(forbidden))

    def test_public_functions_do_not_mutate_parameters(self) -> None:
        targets = {
            node.name: node
            for node in self.tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"validate_original_media_attestation", "bind_attestation_for_phase3p"}
        }
        self.assertEqual(set(targets), {"validate_original_media_attestation", "bind_attestation_for_phase3p"})
        for function in targets.values():
            parameters = {arg.arg for arg in function.args.args + function.args.kwonlyargs}
            for node in ast.walk(function):
                assigned: list[ast.expr] = []
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    if isinstance(node, ast.Assign):
                        assigned.extend(node.targets)
                    else:
                        assigned.append(node.target)
                elif isinstance(node, ast.Delete):
                    assigned.extend(node.targets)
                for target in assigned:
                    if isinstance(target, ast.Name):
                        self.assertNotIn(target.id, parameters)
                    if isinstance(target, (ast.Attribute, ast.Subscript)) and isinstance(target.value, ast.Name):
                        self.assertNotIn(target.value.id, parameters)

    def test_mapping_field_count_is_checked_before_defensive_copy(self) -> None:
        target = next(
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "validate_original_media_attestation"
        )
        dict_copy_line = min(
            node.lineno
            for node in ast.walk(target)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "dict"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "attestation"
        )
        length_guard_line = min(
            node.lineno
            for node in ast.walk(target)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "len"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "attestation"
        )
        self.assertLess(length_guard_line, dict_copy_line)

    def test_no_mutating_method_calls_on_public_parameters(self) -> None:
        mutators = {"append", "extend", "insert", "pop", "remove", "clear", "update", "setdefault", "sort", "reverse"}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id in {"attestation"}:
                    self.assertNotIn(node.func.attr, mutators)

    def test_schema_and_limits_are_exact(self) -> None:
        self.assertEqual(ORIGINAL_MEDIA_ATTESTATION_SCHEMA, "marketmatch-original-media-attestation-v1")
        self.assertEqual(MAX_ORIGINAL_MEDIA_BYTES, 104_857_600)
        self.assertLessEqual(MAX_ATTESTATION_BYTES, 4_096)

    def test_canonical_json_configuration_is_explicit(self) -> None:
        dumps_calls = [
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "dumps"
        ]
        self.assertEqual(len(dumps_calls), 1)
        keyword_names = {item.arg for item in dumps_calls[0].keywords}
        self.assertTrue({"ensure_ascii", "allow_nan", "sort_keys", "separators"} <= keyword_names)

    def test_digest_uses_sha256_and_compare_digest(self) -> None:
        attributes = {node.attr for node in ast.walk(self.tree) if isinstance(node, ast.Attribute)}
        self.assertIn("sha256", attributes)
        self.assertIn("compare_digest", attributes)

    def test_source_compiles_without_execution(self) -> None:
        compile(self.source, str(PRODUCTION_PATH), "exec")

    def test_exactly_two_approved_files_changed(self) -> None:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        changed = {line[3:] for line in completed.stdout.splitlines() if line}
        self.assertEqual(
            changed,
            {
                "src/marketmatch_media_attestation.py",
                "tests/test_marketmatch_media_attestation.py",
            },
        )


if __name__ == "__main__":
    unittest.main()
