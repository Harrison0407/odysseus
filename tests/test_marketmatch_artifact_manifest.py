from __future__ import annotations

import ast
import copy
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError, fields
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import traceback
import unittest
from unittest import mock

from src import marketmatch_artifact_manifest as manifest


CALLS_OPERATION = "mmop-calls-" + "a" * 26
VIDEOS_OPERATION = "mmop-videos-" + "b" * 26
PAYLOAD_SHA = "1" * 64


CALLS_TRANSCRIPT = manifest.RolePolicy(
    manifest.ManifestDomain.CALLS,
    "transcript",
    ("transcript",),
)
CALLS_SUMMARY = manifest.RolePolicy(
    manifest.ManifestDomain.CALLS,
    "summary",
    ("summary",),
)
CALLS_RAG = manifest.RolePolicy(
    manifest.ManifestDomain.CALLS,
    "rag_ready",
    ("rag_ready",),
)
VIDEOS_TRANSCRIPT = manifest.RolePolicy(
    manifest.ManifestDomain.VIDEOS,
    "transcript",
    ("transcript",),
)
VIDEOS_AUDIO = manifest.RolePolicy(
    manifest.ManifestDomain.VIDEOS,
    "audio_extraction",
    ("extracted_audio",),
)
VIDEOS_CLIP_METADATA = manifest.RolePolicy(
    manifest.ManifestDomain.VIDEOS,
    "clip_metadata",
    ("clip_metadata",),
)
VIDEOS_CLIP_EXPORT = manifest.RolePolicy(
    manifest.ManifestDomain.VIDEOS,
    "clip_export",
    ("clip_export",),
    ("clip_metadata",),
)


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def payload(role: str, marker: str = "a", *, byte_size: int = 7, sha256: str = PAYLOAD_SHA):
    return {
        "role": role,
        "relative_name": "mma-" + marker * 32,
        "byte_size": byte_size,
        "sha256": sha256,
    }


def signed_manifest(
    policy: manifest.RolePolicy,
    payloads: list[dict[str, object]] | None = None,
    *,
    operation_id: str | None = None,
) -> dict[str, object]:
    domain = policy.domain.value
    if payloads is None:
        payloads = [payload(policy.required_roles[0])]
    subject = {
        "schema": manifest.SCHEMA_VERSION,
        "domain": domain,
        "operation_id": operation_id or (
            CALLS_OPERATION if policy.domain is manifest.ManifestDomain.CALLS else VIDEOS_OPERATION
        ),
        "stage": policy.stage,
        "payloads": payloads,
    }
    return {
        **subject,
        "manifest_digest": hashlib.sha256(canonical(subject)).hexdigest(),
    }


def resign(value: dict[str, object]) -> dict[str, object]:
    subject = {key: copy.deepcopy(item) for key, item in value.items() if key != "manifest_digest"}
    value["manifest_digest"] = hashlib.sha256(canonical(subject)).hexdigest()
    return value


def as_bytes(value: dict[str, object]) -> bytes:
    return canonical(value)


class ManifestContractTests(unittest.TestCase):
    def assert_code(self, code: manifest.ManifestCode, operation) -> manifest.ManifestValidationError:
        with self.assertRaises(manifest.ManifestValidationError) as caught:
            operation()
        self.assertIs(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code.value)
        return caught.exception

    def validate(self, value, policy=CALLS_TRANSCRIPT, domain=None):
        return manifest.validate_manifest(
            value,
            expected_domain=domain or policy.domain,
            role_policy=policy,
        )

    def test_valid_calls_transcript_manifest(self) -> None:
        result = self.validate(as_bytes(signed_manifest(CALLS_TRANSCRIPT)))
        self.assertEqual(result.domain, manifest.ManifestDomain.CALLS)
        self.assertEqual(result.stage, "transcript")

    def test_valid_calls_summary_manifest(self) -> None:
        result = self.validate(signed_manifest(CALLS_SUMMARY), CALLS_SUMMARY)
        self.assertEqual(result.payloads[0].role, "summary")

    def test_valid_videos_transcript_manifest(self) -> None:
        result = self.validate(signed_manifest(VIDEOS_TRANSCRIPT), VIDEOS_TRANSCRIPT)
        self.assertEqual(result.domain, manifest.ManifestDomain.VIDEOS)

    def test_valid_videos_clip_metadata_manifest(self) -> None:
        result = self.validate(signed_manifest(VIDEOS_CLIP_METADATA), VIDEOS_CLIP_METADATA)
        self.assertEqual(result.stage, "clip_metadata")

    def test_valid_optional_role_policy_with_optional_absent(self) -> None:
        result = self.validate(signed_manifest(VIDEOS_CLIP_EXPORT), VIDEOS_CLIP_EXPORT)
        self.assertEqual(tuple(item.role for item in result.payloads), ("clip_export",))

    def test_valid_optional_role_policy_with_optional_present(self) -> None:
        value = signed_manifest(
            VIDEOS_CLIP_EXPORT,
            [payload("clip_export", "a"), payload("clip_metadata", "b")],
        )
        result = self.validate(value, VIDEOS_CLIP_EXPORT)
        self.assertEqual(len(result.payloads), 2)

    def test_valid_calls_rag_ready_manifest_is_logical_only(self) -> None:
        result = self.validate(signed_manifest(CALLS_RAG), CALLS_RAG)
        self.assertEqual(result.payloads[0].role, "rag_ready")

    def test_valid_videos_audio_extraction_manifest(self) -> None:
        result = self.validate(signed_manifest(VIDEOS_AUDIO), VIDEOS_AUDIO)
        self.assertEqual(result.payloads[0].role, "extracted_audio")

    def test_missing_required_role(self) -> None:
        value = signed_manifest(VIDEOS_CLIP_EXPORT, [payload("clip_metadata")])
        self.assert_code(
            manifest.ManifestCode.MISSING_REQUIRED_ROLE,
            lambda: self.validate(value, VIDEOS_CLIP_EXPORT),
        )

    def test_unknown_role(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT, [payload("unknown")])
        self.assert_code(manifest.ManifestCode.UNKNOWN_ROLE, lambda: self.validate(value))

    def test_duplicate_role(self) -> None:
        value = signed_manifest(
            VIDEOS_CLIP_EXPORT,
            [payload("clip_export", "a"), payload("clip_export", "b")],
        )
        self.assert_code(
            manifest.ManifestCode.DUPLICATE_ROLE,
            lambda: self.validate(value, VIDEOS_CLIP_EXPORT),
        )

    def test_calls_videos_role_confusion(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT, [payload("extracted_audio")])
        self.assert_code(manifest.ManifestCode.UNKNOWN_ROLE, lambda: self.validate(value))

    def test_shared_role_still_requires_domain_bound_policy(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        self.assert_code(
            manifest.ManifestCode.INVALID_ROLE_POLICY,
            lambda: self.validate(
                value,
                VIDEOS_TRANSCRIPT,
                manifest.ManifestDomain.CALLS,
            ),
        )

    def test_stage_role_confusion(self) -> None:
        value = signed_manifest(VIDEOS_TRANSCRIPT, [payload("clip_metadata")])
        self.assert_code(
            manifest.ManifestCode.UNKNOWN_ROLE,
            lambda: self.validate(value, VIDEOS_TRANSCRIPT),
        )

    def test_role_policy_overlap_is_rejected(self) -> None:
        self.assert_code(
            manifest.ManifestCode.INVALID_ROLE_POLICY,
            lambda: manifest.RolePolicy(
                manifest.ManifestDomain.VIDEOS,
                "clip_export",
                ("clip_export",),
                ("clip_export",),
            ),
        )

    def test_role_policy_duplicate_is_rejected(self) -> None:
        self.assert_code(
            manifest.ManifestCode.INVALID_ROLE_POLICY,
            lambda: manifest.RolePolicy(
                manifest.ManifestDomain.VIDEOS,
                "clip_export",
                ("clip_export", "clip_export"),
            ),
        )

    def test_role_policy_noncanonical_order_is_rejected(self) -> None:
        self.assert_code(
            manifest.ManifestCode.INVALID_ROLE_POLICY,
            lambda: manifest.RolePolicy(
                manifest.ManifestDomain.VIDEOS,
                "clip_export",
                ("clip_metadata", "clip_export"),
            ),
        )

    def test_role_policy_list_is_rejected(self) -> None:
        self.assert_code(
            manifest.ManifestCode.INVALID_ROLE_POLICY,
            lambda: manifest.RolePolicy(  # type: ignore[arg-type]
                manifest.ManifestDomain.CALLS,
                "transcript",
                ["transcript"],
            ),
        )

    def test_role_policy_empty_required_is_rejected(self) -> None:
        self.assert_code(
            manifest.ManifestCode.INVALID_ROLE_POLICY,
            lambda: manifest.RolePolicy(
                manifest.ManifestDomain.CALLS,
                "transcript",
                (),
            ),
        )

    def test_role_policy_unknown_role_is_rejected(self) -> None:
        self.assert_code(
            manifest.ManifestCode.INVALID_ROLE_POLICY,
            lambda: manifest.RolePolicy(
                manifest.ManifestDomain.CALLS,
                "transcript",
                ("clip_export",),
            ),
        )

    def test_role_policy_unknown_stage_is_rejected(self) -> None:
        self.assert_code(
            manifest.ManifestCode.INVALID_ROLE_POLICY,
            lambda: manifest.RolePolicy(
                manifest.ManifestDomain.CALLS,
                "complete",
                ("transcript",),
            ),
        )

    def test_role_policy_must_require_stage_primary_role(self) -> None:
        self.assert_code(
            manifest.ManifestCode.INVALID_ROLE_POLICY,
            lambda: manifest.RolePolicy(
                manifest.ManifestDomain.VIDEOS,
                "clip_export",
                ("clip_metadata",),
                ("clip_export",),
            ),
        )

    def test_unknown_schema(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        value["schema"] = "marketmatch-artifact-manifest-v2"
        resign(value)
        self.assert_code(manifest.ManifestCode.UNKNOWN_SCHEMA, lambda: self.validate(value))

    def test_missing_schema(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        del value["schema"]
        self.assert_code(manifest.ManifestCode.MISSING_FIELD, lambda: self.validate(value))

    def test_unknown_top_level_key(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        value["status"] = "complete"
        self.assert_code(manifest.ManifestCode.UNKNOWN_FIELD, lambda: self.validate(value))

    def test_unknown_payload_key(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        value["payloads"][0]["algorithm"] = "sha256"  # type: ignore[index]
        self.assert_code(manifest.ManifestCode.UNKNOWN_FIELD, lambda: self.validate(value))

    def test_missing_payload_key(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        del value["payloads"][0]["sha256"]  # type: ignore[index]
        self.assert_code(manifest.ManifestCode.MISSING_FIELD, lambda: self.validate(value))

    def test_invalid_domain(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        value["domain"] = "documents"
        resign(value)
        self.assert_code(manifest.ManifestCode.INVALID_DOMAIN, lambda: self.validate(value))

    def test_expected_domain_mismatch(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        self.assert_code(
            manifest.ManifestCode.INVALID_ROLE_POLICY,
            lambda: self.validate(value, CALLS_TRANSCRIPT, manifest.ManifestDomain.VIDEOS),
        )

    def test_invalid_stage(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        value["stage"] = "publication"
        resign(value)
        self.assert_code(manifest.ManifestCode.INVALID_STAGE, lambda: self.validate(value))

    def test_stage_domain_mismatch(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        value["stage"] = "audio_extraction"
        resign(value)
        self.assert_code(manifest.ManifestCode.INVALID_STAGE, lambda: self.validate(value))

    def test_policy_stage_mismatch(self) -> None:
        value = signed_manifest(CALLS_SUMMARY)
        self.assert_code(
            manifest.ManifestCode.INVALID_ROLE_POLICY,
            lambda: self.validate(value, CALLS_TRANSCRIPT),
        )

    def test_empty_operation_id(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        value["operation_id"] = ""
        resign(value)
        self.assert_code(manifest.ManifestCode.INVALID_OPERATION_ID, lambda: self.validate(value))

    def test_unicode_operation_id(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        value["operation_id"] = "mmop-calls-" + "é" * 26
        resign(value)
        self.assert_code(manifest.ManifestCode.INVALID_OPERATION_ID, lambda: self.validate(value))

    def test_traversal_like_operation_id(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT, operation_id="mmop-calls-../../private")
        self.assert_code(manifest.ManifestCode.INVALID_OPERATION_ID, lambda: self.validate(value))

    def test_slash_operation_id(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT, operation_id="mmop-calls-" + "a" * 12 + "/" + "a" * 13)
        self.assert_code(manifest.ManifestCode.INVALID_OPERATION_ID, lambda: self.validate(value))

    def test_backslash_operation_id(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT, operation_id="mmop-calls-" + "a" * 12 + "\\" + "a" * 13)
        self.assert_code(manifest.ManifestCode.INVALID_OPERATION_ID, lambda: self.validate(value))

    def test_excessive_operation_id_length(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT, operation_id="m" * (manifest.MAX_OPERATION_ID_LENGTH + 1))
        self.assert_code(manifest.ManifestCode.INVALID_OPERATION_ID, lambda: self.validate(value))

    def test_nul_operation_id_rejected(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT, operation_id="mmop-calls-" + "a" * 25 + "\x00")
        self.assert_code(manifest.ManifestCode.INVALID_OPERATION_ID, lambda: self.validate(value))

    def test_whitespace_operation_id_rejected(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT, operation_id=" " + CALLS_OPERATION)
        self.assert_code(manifest.ManifestCode.INVALID_OPERATION_ID, lambda: self.validate(value))

    def test_operation_id_domain_prefix_mismatch(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT, operation_id=VIDEOS_OPERATION)
        self.assert_code(manifest.ManifestCode.INVALID_OPERATION_ID, lambda: self.validate(value))

    def test_hostname_like_operation_id(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT, operation_id="api.internal.example")
        self.assert_code(manifest.ManifestCode.INVALID_OPERATION_ID, lambda: self.validate(value))

    def invalid_name(self, name: object) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        value["payloads"][0]["relative_name"] = name  # type: ignore[index]
        resign(value)
        self.assert_code(manifest.ManifestCode.INVALID_RELATIVE_NAME, lambda: self.validate(value))

    def test_empty_relative_name(self) -> None:
        self.invalid_name("")

    def test_slash_relative_name(self) -> None:
        self.invalid_name("mma-" + "a" * 16 + "/" + "a" * 15)

    def test_backslash_relative_name(self) -> None:
        self.invalid_name("mma-" + "a" * 16 + "\\" + "a" * 15)

    def test_dot_relative_name(self) -> None:
        self.invalid_name(".")

    def test_dot_dot_relative_name(self) -> None:
        self.invalid_name("..")

    def test_absolute_posix_relative_name(self) -> None:
        self.invalid_name("/private/customer.wav")

    def test_windows_drive_relative_name(self) -> None:
        self.invalid_name("C:\\Users\\private.wav")

    def test_unc_relative_name(self) -> None:
        self.invalid_name("\\\\server\\share\\private.wav")

    def test_unicode_relative_name(self) -> None:
        self.invalid_name("mma-" + "é" * 32)

    def test_relative_name_leading_whitespace(self) -> None:
        self.invalid_name(" " + "mma-" + "a" * 32)

    def test_relative_name_trailing_whitespace(self) -> None:
        self.invalid_name("mma-" + "a" * 32 + " ")

    def test_excessive_relative_name_length(self) -> None:
        self.invalid_name("a" * (manifest.MAX_RELATIVE_NAME_LENGTH + 1))

    def test_nul_relative_name_rejected(self) -> None:
        self.invalid_name("mma-" + "a" * 31 + "\x00")

    def test_original_filename_privacy_canary_rejected(self) -> None:
        self.invalid_name("PRIVATE-CUSTOMER-call-recording.wav")

    def test_url_email_ip_and_home_names_rejected(self) -> None:
        for value in (
            "https://private.example/artifact",
            "owner@example.test",
            "192.0.2.10",
            "~/private.wav",
            "embedding_url",
            "token-sk-private",
        ):
            with self.subTest(value=value):
                self.invalid_name(value)

    def test_duplicate_relative_name_rejected(self) -> None:
        value = signed_manifest(
            VIDEOS_CLIP_EXPORT,
            [payload("clip_export", "a"), payload("clip_metadata", "a")],
        )
        self.assert_code(
            manifest.ManifestCode.INVALID_RELATIVE_NAME,
            lambda: self.validate(value, VIDEOS_CLIP_EXPORT),
        )

    def test_byte_size_zero_accepted(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT, [payload("transcript", byte_size=0)])
        self.assertEqual(self.validate(value).payloads[0].byte_size, 0)

    def test_byte_size_maximum_accepted(self) -> None:
        value = signed_manifest(
            CALLS_TRANSCRIPT,
            [payload("transcript", byte_size=manifest.MAX_BYTE_SIZE)],
        )
        self.assertEqual(self.validate(value).payloads[0].byte_size, manifest.MAX_BYTE_SIZE)

    def test_negative_byte_size_rejected(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT, [payload("transcript", byte_size=-1)])
        self.assert_code(manifest.ManifestCode.INVALID_BYTE_SIZE, lambda: self.validate(value))

    def test_boolean_byte_size_rejected(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT, [payload("transcript", byte_size=True)])
        self.assert_code(manifest.ManifestCode.INVALID_BYTE_SIZE, lambda: self.validate(value))

    def test_float_byte_size_rejected_in_parsed_data(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT, [payload("transcript", byte_size=1.5)])
        self.assert_code(manifest.ManifestCode.INVALID_BYTE_SIZE, lambda: self.validate(value))

    def test_float_byte_size_rejected_in_json(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT, [payload("transcript", byte_size=1.5)])
        self.assert_code(manifest.ManifestCode.INVALID_JSON, lambda: self.validate(as_bytes(value)))

    def test_oversized_byte_size_rejected(self) -> None:
        value = signed_manifest(
            CALLS_TRANSCRIPT,
            [payload("transcript", byte_size=manifest.MAX_BYTE_SIZE + 1)],
        )
        self.assert_code(manifest.ManifestCode.INVALID_BYTE_SIZE, lambda: self.validate(value))

    def test_string_byte_size_rejected(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        value["payloads"][0]["byte_size"] = "7"  # type: ignore[index]
        resign(value)
        self.assert_code(manifest.ManifestCode.INVALID_BYTE_SIZE, lambda: self.validate(value))

    def test_valid_lowercase_sha256(self) -> None:
        result = self.validate(signed_manifest(CALLS_TRANSCRIPT))
        self.assertEqual(result.payloads[0].sha256, PAYLOAD_SHA)

    def test_uppercase_payload_digest_rejected(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT, [payload("transcript", sha256="A" * 64)])
        self.assert_code(manifest.ManifestCode.INVALID_SHA256, lambda: self.validate(value))

    def test_short_payload_digest_rejected(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT, [payload("transcript", sha256="a" * 63)])
        self.assert_code(manifest.ManifestCode.INVALID_SHA256, lambda: self.validate(value))

    def test_prefixed_payload_digest_rejected(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT, [payload("transcript", sha256="sha256:" + "a" * 64)])
        self.assert_code(manifest.ManifestCode.INVALID_SHA256, lambda: self.validate(value))

    def test_nonhex_payload_digest_rejected(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT, [payload("transcript", sha256="g" * 64)])
        self.assert_code(manifest.ManifestCode.INVALID_SHA256, lambda: self.validate(value))

    def test_uppercase_manifest_digest_rejected(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        value["manifest_digest"] = str(value["manifest_digest"]).upper()
        self.assert_code(manifest.ManifestCode.INVALID_SHA256, lambda: self.validate(value))

    def test_missing_manifest_digest(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        del value["manifest_digest"]
        self.assert_code(manifest.ManifestCode.MANIFEST_DIGEST_MISSING, lambda: self.validate(value))

    def test_manifest_digest_mismatch(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        value["manifest_digest"] = "0" * 64
        self.assert_code(manifest.ManifestCode.MANIFEST_DIGEST_MISMATCH, lambda: self.validate(value))

    def test_digest_is_over_subject_excluding_manifest_digest(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        subject = {key: item for key, item in value.items() if key != "manifest_digest"}
        expected = hashlib.sha256(canonical(subject)).hexdigest()
        result = self.validate(value)
        self.assertEqual(result.manifest_digest, expected)
        self.assertNotEqual(result.manifest_digest, hashlib.sha256(canonical(value)).hexdigest())

    def test_digest_comparison_uses_hmac_compare_digest(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        with mock.patch.object(manifest.hmac, "compare_digest", wraps=manifest.hmac.compare_digest) as compared:
            self.validate(value)
        self.assertGreaterEqual(compared.call_count, 1)

    def test_duplicate_json_key_rejected(self) -> None:
        raw = b'{"schema":"one","schema":"two"}'
        self.assert_code(manifest.ManifestCode.DUPLICATE_JSON_KEY, lambda: self.validate(raw))

    def test_duplicate_payload_json_key_rejected(self) -> None:
        raw = (
            b'{"domain":"calls","manifest_digest":"' + b"0" * 64
            + b'","operation_id":"' + CALLS_OPERATION.encode("ascii")
            + b'","payloads":[{"byte_size":1,"byte_size":2}],"schema":"'
            + manifest.SCHEMA_VERSION.encode("ascii") + b'","stage":"transcript"}'
        )
        self.assert_code(manifest.ManifestCode.DUPLICATE_JSON_KEY, lambda: self.validate(raw))

    def test_trailing_json_rejected(self) -> None:
        raw = as_bytes(signed_manifest(CALLS_TRANSCRIPT)) + b"{}"
        self.assert_code(manifest.ManifestCode.TRAILING_JSON_DATA, lambda: self.validate(raw))

    def test_bom_rejected(self) -> None:
        raw = b"\xef\xbb\xbf" + as_bytes(signed_manifest(CALLS_TRANSCRIPT))
        self.assert_code(manifest.ManifestCode.INVALID_JSON, lambda: self.validate(raw))

    def test_whitespace_json_rejected(self) -> None:
        raw = json.dumps(signed_manifest(CALLS_TRANSCRIPT), sort_keys=True).encode("ascii")
        self.assert_code(manifest.ManifestCode.NON_CANONICAL_JSON, lambda: self.validate(raw))

    def test_leading_and_trailing_whitespace_rejected(self) -> None:
        raw = b" " + as_bytes(signed_manifest(CALLS_TRANSCRIPT)) + b"\n"
        self.assert_code(manifest.ManifestCode.NON_CANONICAL_JSON, lambda: self.validate(raw))

    def test_non_ascii_json_rejected(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        value["operation_id"] = "é"
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.assert_code(manifest.ManifestCode.NON_CANONICAL_JSON, lambda: self.validate(raw))

    def test_escaped_unicode_json_rejected(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        value["operation_id"] = "é"
        raw = canonical(value)
        self.assert_code(manifest.ManifestCode.INVALID_OPERATION_ID, lambda: self.validate(raw))

    def test_nan_rejected(self) -> None:
        raw = b'{"byte_size":NaN}'
        self.assert_code(manifest.ManifestCode.INVALID_JSON, lambda: self.validate(raw))

    def test_infinity_rejected(self) -> None:
        raw = b'{"byte_size":Infinity}'
        self.assert_code(manifest.ManifestCode.INVALID_JSON, lambda: self.validate(raw))

    def test_comment_rejected(self) -> None:
        raw = as_bytes(signed_manifest(CALLS_TRANSCRIPT)) + b"/*private*/"
        self.assert_code(manifest.ManifestCode.TRAILING_JSON_DATA, lambda: self.validate(raw))

    def test_excessive_manifest_size_rejected(self) -> None:
        raw = b"{" + b"a" * manifest.MAX_MANIFEST_BYTES
        self.assert_code(manifest.ManifestCode.INVALID_JSON, lambda: self.validate(raw))

    def test_excessive_payload_count_rejected(self) -> None:
        value = signed_manifest(
            CALLS_TRANSCRIPT,
            [payload("transcript", "a") for _ in range(manifest.MAX_PAYLOAD_COUNT + 1)],
        )
        self.assert_code(manifest.ManifestCode.INVALID_PAYLOAD_COUNT, lambda: self.validate(value))

    def test_excessive_nesting_rejected(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        value["payloads"] = [[[[[]]]]]
        self.assert_code(manifest.ManifestCode.INVALID_INPUT_TYPE, lambda: self.validate(value))

    def test_oversized_top_mapping_rejected_without_touching_values(self) -> None:
        canary = "PRIVATE-UNTOUCHED-VALUE-CANARY"

        class HostileValue:
            def __getattribute__(self, name):
                raise RuntimeError(canary)

        value = signed_manifest(CALLS_TRANSCRIPT)
        value["extra"] = HostileValue()
        caught = self.assert_code(manifest.ManifestCode.UNKNOWN_FIELD, lambda: self.validate(value))
        self.assertNotIn(canary, str(caught))
        self.assertNotIn(canary, repr(caught))

    def test_oversized_role_policy_rejected(self) -> None:
        roles = tuple("r" + str(index) for index in range(manifest.MAX_PAYLOAD_COUNT + 1))
        self.assert_code(
            manifest.ManifestCode.INVALID_ROLE_POLICY,
            lambda: manifest.RolePolicy(
                manifest.ManifestDomain.CALLS,
                "transcript",
                roles,
            ),
        )

    def test_empty_payload_list_rejected(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT, [])
        self.assert_code(manifest.ManifestCode.INVALID_PAYLOAD_COUNT, lambda: self.validate(value))

    def test_non_list_payloads_rejected(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        value["payloads"] = tuple(value["payloads"])  # type: ignore[arg-type]
        resign(value)
        self.assert_code(manifest.ManifestCode.INVALID_INPUT_TYPE, lambda: self.validate(value))

    def test_noncanonical_payload_order_rejected(self) -> None:
        value = signed_manifest(
            VIDEOS_CLIP_EXPORT,
            [payload("clip_metadata", "b"), payload("clip_export", "a")],
        )
        self.assert_code(
            manifest.ManifestCode.INVALID_PAYLOAD_ORDER,
            lambda: self.validate(value, VIDEOS_CLIP_EXPORT),
        )

    def test_noncanonical_top_level_key_order_rejected_for_bytes(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        raw = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        self.assertNotEqual(raw, canonical(value))
        self.assert_code(manifest.ManifestCode.NON_CANONICAL_JSON, lambda: self.validate(raw))

    def test_caller_input_object_remains_unchanged(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        before = copy.deepcopy(value)
        self.validate(value)
        self.assertEqual(value, before)

    def test_returned_manifest_is_immutable(self) -> None:
        result = self.validate(signed_manifest(CALLS_TRANSCRIPT))
        with self.assertRaises(FrozenInstanceError):
            result.stage = "summary"  # type: ignore[misc]

    def test_returned_payload_is_immutable(self) -> None:
        result = self.validate(signed_manifest(CALLS_TRANSCRIPT))
        with self.assertRaises(FrozenInstanceError):
            result.payloads[0].byte_size = 8  # type: ignore[misc]

    def test_returned_payload_collection_is_tuple(self) -> None:
        result = self.validate(signed_manifest(CALLS_TRANSCRIPT))
        self.assertIs(type(result.payloads), tuple)

    def test_returned_model_does_not_retain_caller_objects(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        result = self.validate(value)
        value["payloads"][0]["byte_size"] = 999  # type: ignore[index]
        self.assertEqual(result.payloads[0].byte_size, 7)

    def test_returned_model_exposes_only_contract_fields(self) -> None:
        self.assertEqual(
            tuple(field.name for field in fields(manifest.ValidatedManifest)),
            ("schema", "domain", "operation_id", "stage", "payloads", "manifest_digest", "canonical_bytes"),
        )

    def test_canonical_bytes_are_deterministic(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        reordered = dict(reversed(tuple(value.items())))
        first = self.validate(value)
        second = self.validate(reordered)
        self.assertEqual(first.canonical_bytes, second.canonical_bytes)

    def test_same_subject_produces_same_digest(self) -> None:
        first = self.validate(signed_manifest(CALLS_TRANSCRIPT))
        second = self.validate(copy.deepcopy(signed_manifest(CALLS_TRANSCRIPT)))
        self.assertEqual(first.manifest_digest, second.manifest_digest)

    def test_any_subject_field_change_changes_digest(self) -> None:
        original = signed_manifest(CALLS_TRANSCRIPT)
        changed = copy.deepcopy(original)
        changed["payloads"][0]["byte_size"] = 8  # type: ignore[index]
        resign(changed)
        self.assertNotEqual(original["manifest_digest"], changed["manifest_digest"])

    def test_field_change_without_resigning_is_rejected(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        value["payloads"][0]["byte_size"] = 8  # type: ignore[index]
        self.assert_code(manifest.ManifestCode.MANIFEST_DIGEST_MISMATCH, lambda: self.validate(value))

    def test_canonical_bytes_are_exact_ascii_json(self) -> None:
        value = signed_manifest(CALLS_TRANSCRIPT)
        result = self.validate(value)
        self.assertEqual(result.canonical_bytes, canonical(value))
        self.assertTrue(result.canonical_bytes.isascii())

    def test_manifest_presence_does_not_expose_completion_claim(self) -> None:
        result = self.validate(signed_manifest(CALLS_TRANSCRIPT))
        field_names = frozenset(field.name for field in fields(result))
        self.assertFalse(field_names & {"status", "published", "durable", "authorized", "indexed"})

    def test_non_dict_parsed_input_rejected(self) -> None:
        self.assert_code(manifest.ManifestCode.INVALID_INPUT_TYPE, lambda: self.validate([]))

    def test_non_bytes_binary_input_rejected(self) -> None:
        self.assert_code(
            manifest.ManifestCode.INVALID_INPUT_TYPE,
            lambda: self.validate(bytearray(as_bytes(signed_manifest(CALLS_TRANSCRIPT)))),
        )

    def test_hostile_mapping_does_not_leak(self) -> None:
        canary = "PRIVATE-HOSTILE-MAPPING-CANARY"

        class HostileDict(dict):
            def keys(self):
                raise RuntimeError(canary)

            def __str__(self):
                raise RuntimeError(canary)

        caught = self.assert_code(
            manifest.ManifestCode.INVALID_INPUT_TYPE,
            lambda: self.validate(HostileDict()),
        )
        self.assertNotIn(canary, str(caught))
        self.assertNotIn(canary, repr(caught))

    def test_hostile_string_like_does_not_leak(self) -> None:
        canary = "PRIVATE-HOSTILE-STRING-CANARY"

        class HostileString(str):
            def isascii(self):
                raise RuntimeError(canary)

            def __str__(self):
                raise RuntimeError(canary)

        value = signed_manifest(CALLS_TRANSCRIPT)
        value["operation_id"] = HostileString(CALLS_OPERATION)
        caught = self.assert_code(
            manifest.ManifestCode.INVALID_OPERATION_ID,
            lambda: self.validate(value),
        )
        self.assertNotIn(canary, str(caught))
        self.assertNotIn(canary, repr(caught))

    def test_privacy_canaries_absent_from_errors_and_streams(self) -> None:
        canaries = (
            "/Users/private-owner/secret.wav",
            "C:\\Users\\private-owner\\secret.wav",
            "private-owner@example.test",
            "sk-private-api-token",
            "embedding_url=https://private.example",
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            for canary in canaries:
                value = signed_manifest(CALLS_TRANSCRIPT)
                value["payloads"][0]["relative_name"] = canary  # type: ignore[index]
                resign(value)
                caught = self.assert_code(
                    manifest.ManifestCode.INVALID_RELATIVE_NAME,
                    lambda value=value: self.validate(value),
                )
                self.assertNotIn(canary, str(caught))
                self.assertNotIn(canary, repr(caught))
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_traceback_suppresses_rejected_value_and_inner_context(self) -> None:
        canary = "alice"
        value = signed_manifest(CALLS_TRANSCRIPT)
        value["domain"] = canary
        resign(value)
        try:
            self.validate(value)
        except manifest.ManifestValidationError as error:
            stderr = io.StringIO()
            traceback.print_exception(error, file=stderr)
        else:
            self.fail("fixed validation error was not raised")
        rendered = stderr.getvalue()
        self.assertNotIn(canary, rendered)
        self.assertNotIn("ValueError", rendered)
        self.assertIn(manifest.ManifestCode.INVALID_DOMAIN.value, rendered)


class ManifestStaticBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo = Path(__file__).resolve().parents[1]
        cls.production = cls.repo / "src" / "marketmatch_artifact_manifest.py"
        cls.source = cls.production.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def imported_roots(self) -> set[str]:
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        imported.discard("__future__")
        return imported

    def test_production_imports_standard_library_only(self) -> None:
        imported = self.imported_roots()
        self.assertTrue(imported)
        self.assertEqual(imported - sys.stdlib_module_names, set())

    def test_no_prohibited_application_imports(self) -> None:
        prohibited = {
            "app", "routes", "core", "database", "sqlalchemy", "chromadb",
            "rag", "stt", "ffmpeg", "upload_handler", "workers", "src",
        }
        self.assertEqual(self.imported_roots() & prohibited, set())

    def test_no_filesystem_environment_or_network_imports(self) -> None:
        prohibited = {
            "os", "pathlib", "tempfile", "shutil", "subprocess", "socket",
            "requests", "urllib", "http", "ftplib",
        }
        self.assertEqual(self.imported_roots() & prohibited, set())

    def test_no_time_or_randomness_imports(self) -> None:
        prohibited = {"time", "datetime", "secrets", "random", "uuid"}
        self.assertEqual(self.imported_roots() & prohibited, set())

    def test_no_logging_import_or_calls(self) -> None:
        self.assertNotIn("logging", self.imported_roots())
        logging_calls = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logging"
        ]
        self.assertEqual(logging_calls, [])

    def test_no_open_print_exec_eval_or_dynamic_import_calls(self) -> None:
        prohibited = {"open", "print", "exec", "eval", "compile", "__import__"}
        called = {
            node.func.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertEqual(called & prohibited, set())

    def test_no_filesystem_or_process_method_calls(self) -> None:
        prohibited = {
            "open", "read", "write", "unlink", "remove", "rename", "replace",
            "mkdir", "makedirs", "rmdir", "stat", "lstat", "scandir", "listdir",
            "getenv", "system", "run", "Popen", "connect", "send", "recv",
        }
        observed = {
            node.func.attr
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertEqual(observed & prohibited, set())

    def test_no_mutating_container_method_calls(self) -> None:
        prohibited = {
            "append", "clear", "extend", "insert", "pop", "popitem", "remove",
            "reverse", "setdefault", "sort", "update", "__setitem__", "__delitem__",
        }
        observed = {
            node.func.attr
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertEqual(observed & prohibited, set())

    def test_no_subscript_assignment_or_deletion(self) -> None:
        prohibited = []
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                else:
                    targets = [node.target]
                prohibited.extend(target for target in targets if isinstance(target, ast.Subscript))
            elif isinstance(node, ast.Delete):
                prohibited.extend(target for target in node.targets if isinstance(target, ast.Subscript))
        self.assertEqual(prohibited, [])

    def test_canonical_json_options_are_explicit(self) -> None:
        dumps_calls = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "json"
            and node.func.attr == "dumps"
        ]
        self.assertEqual(len(dumps_calls), 1)
        keywords = {keyword.arg for keyword in dumps_calls[0].keywords}
        self.assertTrue({"ensure_ascii", "allow_nan", "sort_keys", "separators"} <= keywords)

    def test_duplicate_key_decoder_hook_is_explicit(self) -> None:
        decoder_calls = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "json"
            and node.func.attr == "JSONDecoder"
        ]
        self.assertEqual(len(decoder_calls), 1)
        keywords = {keyword.arg for keyword in decoder_calls[0].keywords}
        self.assertTrue({"object_pairs_hook", "parse_float", "parse_int", "parse_constant", "strict"} <= keywords)

    def test_manifest_digest_uses_sha256_and_compare_digest(self) -> None:
        calls = {
            (node.func.value.id, node.func.attr)
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
        }
        self.assertIn(("hashlib", "sha256"), calls)
        self.assertIn(("hmac", "compare_digest"), calls)

    def test_no_environment_cwd_clock_or_random_references(self) -> None:
        prohibited_names = {
            "environ", "getenv", "getcwd", "cwd", "home", "now", "utcnow",
            "today", "time", "monotonic", "random", "randint", "token_hex",
            "token_urlsafe", "uuid4",
        }
        attributes = {
            node.attr for node in ast.walk(self.tree) if isinstance(node, ast.Attribute)
        }
        names = {node.id for node in ast.walk(self.tree) if isinstance(node, ast.Name)}
        self.assertEqual((attributes | names) & prohibited_names, set())

    def test_production_defines_no_filesystem_paths_or_roots(self) -> None:
        string_constants = {
            node.value
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        forbidden_prefixes = ("/", "~/", "C:\\", "\\\\")
        self.assertFalse(any(value.startswith(forbidden_prefixes) for value in string_constants))

    def test_source_scope_is_exactly_two_approved_files(self) -> None:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        changed = {line[3:] for line in result.stdout.splitlines() if len(line) >= 4}
        self.assertEqual(
            changed,
            {
                "src/marketmatch_artifact_manifest.py",
                "tests/test_marketmatch_artifact_manifest.py",
            },
        )


if __name__ == "__main__":
    unittest.main()
