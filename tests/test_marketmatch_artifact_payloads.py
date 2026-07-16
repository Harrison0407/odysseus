"""Dedicated pure-data tests for MarketMatch artifact payload verification."""

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

from src.marketmatch_artifact_manifest import (
    ManifestDomain,
    RolePolicy,
    SCHEMA_VERSION,
    ValidatedManifest,
    validate_manifest,
)
from src.marketmatch_artifact_payloads import (
    MAX_AGGREGATE_PAYLOAD_BYTES,
    PayloadVerificationCode,
    PayloadVerificationError,
    VerifiedPayload,
    VerifiedPayloadSet,
    verify_payloads,
)
import src.marketmatch_artifact_payloads as payload_module


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_PATH = ROOT / "src" / "marketmatch_artifact_payloads.py"


def _policy(
    domain: ManifestDomain,
    stage: str,
    roles: tuple[str, ...],
) -> RolePolicy:
    return RolePolicy(domain=domain, stage=stage, required_roles=tuple(sorted(roles)))


def _manifest(
    domain: ManifestDomain,
    stage: str,
    contents: dict[str, bytes],
) -> ValidatedManifest:
    entries = []
    for index, role in enumerate(sorted(contents)):
        content = contents[role]
        entries.append(
            {
                "byte_size": len(content),
                "relative_name": f"mma-{chr(97 + index) * 32}",
                "role": role,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    subject = {
        "domain": domain.value,
        "operation_id": f"mmop-{domain.value}-{'a' * 26}",
        "payloads": entries,
        "schema": SCHEMA_VERSION,
        "stage": stage,
    }
    subject_bytes = json.dumps(
        subject,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    complete = {**subject, "manifest_digest": hashlib.sha256(subject_bytes).hexdigest()}
    canonical = json.dumps(
        complete,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return validate_manifest(
        canonical,
        expected_domain=domain,
        role_policy=_policy(domain, stage, tuple(contents)),
    )


def _assert_code(
    test: unittest.TestCase,
    code: PayloadVerificationCode,
    manifest: object,
    payloads: object,
) -> PayloadVerificationError:
    with test.assertRaises(PayloadVerificationError) as caught:
        verify_payloads(manifest, payloads)  # type: ignore[arg-type]
    test.assertIs(caught.exception.code, code)
    return caught.exception


class PayloadVerificationSuccessTests(unittest.TestCase):
    def test_valid_single_calls_transcript_payload(self) -> None:
        content = b"synthetic calls transcript"
        manifest = _manifest(ManifestDomain.CALLS, "transcript", {"transcript": content})

        result = verify_payloads(manifest, {"transcript": content})

        self.assertEqual(result.payloads[0].role, "transcript")
        self.assertEqual(result.payloads[0].payload_bytes, content)

    def test_valid_calls_summary_payload(self) -> None:
        content = b"synthetic summary"
        manifest = _manifest(ManifestDomain.CALLS, "summary", {"summary": content})
        result = verify_payloads(manifest, {"summary": content})
        self.assertEqual(result.payloads[0].sha256, hashlib.sha256(content).hexdigest())

    def test_valid_videos_transcript_payload(self) -> None:
        content = b"synthetic video transcript"
        manifest = _manifest(ManifestDomain.VIDEOS, "transcript", {"transcript": content})
        self.assertEqual(verify_payloads(manifest, {"transcript": content}).payloads[0].byte_size, len(content))

    def test_valid_videos_clip_metadata_payload(self) -> None:
        content = b'{"clips":[]}'
        manifest = _manifest(
            ManifestDomain.VIDEOS,
            "clip_metadata",
            {"clip_metadata": content},
        )
        self.assertEqual(
            verify_payloads(manifest, {"clip_metadata": content}).payloads[0].relative_name,
            "mma-" + "a" * 32,
        )

    def test_valid_multiple_payload_stage(self) -> None:
        contents = {"clip_export": b"clip", "clip_metadata": b"metadata"}
        manifest = _manifest(ManifestDomain.VIDEOS, "clip_export", contents)

        result = verify_payloads(manifest, dict(reversed(tuple(contents.items()))))

        self.assertEqual(tuple(item.role for item in result.payloads), ("clip_export", "clip_metadata"))

    def test_empty_payload_is_accepted_when_declared(self) -> None:
        manifest = _manifest(ManifestDomain.CALLS, "transcript", {"transcript": b""})
        result = verify_payloads(manifest, {"transcript": b""})
        self.assertEqual(result.payloads[0].byte_size, 0)

    def test_read_only_memoryview_is_copied_and_accepted(self) -> None:
        content = b"read-only view"
        view = memoryview(content)
        manifest = _manifest(ManifestDomain.CALLS, "transcript", {"transcript": content})
        result = verify_payloads(manifest, {"transcript": view})
        self.assertEqual(result.payloads[0].payload_bytes, content)
        self.assertIs(type(result.payloads[0].payload_bytes), bytes)

    def test_read_only_view_of_mutable_buffer_is_rejected(self) -> None:
        source = bytearray(b"mutable source")
        view = memoryview(source).toreadonly()
        expected = bytes(source)
        manifest = _manifest(ManifestDomain.CALLS, "transcript", {"transcript": expected})

        _assert_code(
            self,
            PayloadVerificationCode.INVALID_PAYLOAD_TYPE,
            manifest,
            {"transcript": view},
        )
        source[:] = b"changed source"

        self.assertNotEqual(bytes(source), expected)

    def test_caller_mapping_remains_unchanged(self) -> None:
        content = b"mapping"
        supplied = {"transcript": content}
        before = copy.copy(supplied)
        manifest = _manifest(ManifestDomain.CALLS, "transcript", before)
        verify_payloads(manifest, supplied)
        self.assertEqual(supplied, before)

    def test_caller_bytes_remain_unchanged(self) -> None:
        content = b"immutable input"
        before = bytes(content)
        manifest = _manifest(ManifestDomain.CALLS, "transcript", {"transcript": content})
        verify_payloads(manifest, {"transcript": content})
        self.assertEqual(content, before)

    def test_returned_records_are_frozen_and_slotted(self) -> None:
        content = b"frozen"
        manifest = _manifest(ManifestDomain.CALLS, "transcript", {"transcript": content})
        result = verify_payloads(manifest, {"transcript": content})
        with self.assertRaises(FrozenInstanceError):
            result.payloads = ()  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            result.payloads[0].role = "summary"  # type: ignore[misc]
        self.assertFalse(hasattr(result, "__dict__"))
        self.assertFalse(hasattr(result.payloads[0], "__dict__"))

    def test_return_model_exposes_only_approved_fields(self) -> None:
        self.assertEqual(tuple(field.name for field in fields(VerifiedPayloadSet)), ("payloads",))
        self.assertEqual(
            tuple(field.name for field in fields(VerifiedPayload)),
            ("role", "relative_name", "byte_size", "sha256", "payload_bytes"),
        )

    def test_result_is_deterministic(self) -> None:
        content = b"deterministic"
        manifest = _manifest(ManifestDomain.CALLS, "transcript", {"transcript": content})
        self.assertEqual(
            verify_payloads(manifest, {"transcript": content}),
            verify_payloads(manifest, {"transcript": content}),
        )

    def test_same_bytes_produce_same_digest(self) -> None:
        content = b"same"
        first = _manifest(ManifestDomain.CALLS, "transcript", {"transcript": content})
        second = _manifest(ManifestDomain.CALLS, "transcript", {"transcript": bytes(content)})
        self.assertEqual(
            verify_payloads(first, {"transcript": content}).payloads[0].sha256,
            verify_payloads(second, {"transcript": bytes(content)}).payloads[0].sha256,
        )

    def test_one_byte_change_changes_digest(self) -> None:
        first_content = b"abc"
        second_content = b"abd"
        first = _manifest(ManifestDomain.CALLS, "transcript", {"transcript": first_content})
        second = _manifest(ManifestDomain.CALLS, "transcript", {"transcript": second_content})
        self.assertNotEqual(
            verify_payloads(first, {"transcript": first_content}).payloads[0].sha256,
            verify_payloads(second, {"transcript": second_content}).payloads[0].sha256,
        )

    def test_manifest_names_and_digests_are_authoritative(self) -> None:
        content = b"authority"
        manifest = _manifest(ManifestDomain.CALLS, "transcript", {"transcript": content})
        result = verify_payloads(manifest, {"transcript": content})
        self.assertEqual(result.payloads[0].relative_name, manifest.payloads[0].relative_name)
        self.assertEqual(result.payloads[0].sha256, manifest.payloads[0].sha256)


class PayloadVerificationFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = b"declared transcript"
        self.manifest = _manifest(
            ManifestDomain.CALLS,
            "transcript",
            {"transcript": self.content},
        )

    def test_missing_payload(self) -> None:
        _assert_code(self, PayloadVerificationCode.MISSING_PAYLOAD, self.manifest, {})

    def test_extra_payload(self) -> None:
        _assert_code(
            self,
            PayloadVerificationCode.UNDECLARED_PAYLOAD,
            self.manifest,
            {"transcript": self.content, "summary": b"extra"},
        )

    def test_only_undeclared_role_is_not_accepted(self) -> None:
        _assert_code(
            self,
            PayloadVerificationCode.UNDECLARED_PAYLOAD,
            self.manifest,
            {"summary": self.content},
        )

    def test_ambiguous_string_subclass_role_is_rejected(self) -> None:
        class AmbiguousRole(str):
            pass

        _assert_code(
            self,
            PayloadVerificationCode.INVALID_PAYLOAD_ROLE,
            self.manifest,
            {AmbiguousRole("transcript"): self.content},
        )

    def test_duplicate_declared_manifest_role_is_rejected(self) -> None:
        forged = replace(self.manifest, payloads=self.manifest.payloads * 2)
        _assert_code(
            self,
            PayloadVerificationCode.DUPLICATE_PAYLOAD,
            forged,
            {"transcript": self.content},
        )

    def test_duplicate_bearing_custom_mapping_is_rejected_as_container(self) -> None:
        class DuplicateMapping(dict):
            def items(self):  # type: ignore[override]
                return (("transcript", self.content), ("transcript", self.content))

        hostile = DuplicateMapping()
        hostile.content = self.content
        _assert_code(
            self,
            PayloadVerificationCode.INVALID_PAYLOAD_CONTAINER,
            self.manifest,
            hostile,
        )

    def test_wrong_payload_types_are_rejected(self) -> None:
        for value in ("text", 1, object(), io.BytesIO(self.content), iter((self.content,))):
            with self.subTest(type=type(value).__name__):
                _assert_code(
                    self,
                    PayloadVerificationCode.INVALID_PAYLOAD_TYPE,
                    self.manifest,
                    {"transcript": value},
                )

    def test_bytearray_is_rejected_without_retention(self) -> None:
        source = bytearray(self.content)
        _assert_code(
            self,
            PayloadVerificationCode.INVALID_PAYLOAD_TYPE,
            self.manifest,
            {"transcript": source},
        )
        self.assertEqual(source, bytearray(self.content))

    def test_writable_memoryview_is_rejected(self) -> None:
        _assert_code(
            self,
            PayloadVerificationCode.INVALID_PAYLOAD_TYPE,
            self.manifest,
            {"transcript": memoryview(bytearray(self.content))},
        )

    def test_noncontiguous_memoryview_is_rejected(self) -> None:
        view = memoryview(b"x" + self.content + b"y")[1:-1:2]
        _assert_code(
            self,
            PayloadVerificationCode.INVALID_PAYLOAD_TYPE,
            self.manifest,
            {"transcript": view},
        )

    def test_released_memoryview_is_rejected_without_underlying_error(self) -> None:
        view = memoryview(self.content)
        view.release()
        error = _assert_code(
            self,
            PayloadVerificationCode.INVALID_PAYLOAD_TYPE,
            self.manifest,
            {"transcript": view},
        )
        self.assertIsNone(error.__cause__)

    def test_size_mismatch(self) -> None:
        _assert_code(
            self,
            PayloadVerificationCode.PAYLOAD_SIZE_MISMATCH,
            self.manifest,
            {"transcript": self.content + b"x"},
        )

    def test_digest_mismatch_with_same_size(self) -> None:
        replacement = b"x" * len(self.content)
        _assert_code(
            self,
            PayloadVerificationCode.PAYLOAD_DIGEST_MISMATCH,
            self.manifest,
            {"transcript": replacement},
        )

    def test_per_payload_limit_is_enforced_without_large_allocation(self) -> None:
        content = b"four"
        manifest = _manifest(ManifestDomain.CALLS, "transcript", {"transcript": content})
        with mock.patch.object(payload_module, "MAX_PAYLOAD_BYTES", 3):
            _assert_code(
                self,
                PayloadVerificationCode.PAYLOAD_LIMIT_EXCEEDED,
                manifest,
                {"transcript": content},
            )

    def test_aggregate_limit_is_lower_than_manifest_payload_limit(self) -> None:
        self.assertLess(MAX_AGGREGATE_PAYLOAD_BYTES, payload_module.MAX_PAYLOAD_BYTES)

    def test_aggregate_size_limit_is_enforced_before_normalization(self) -> None:
        contents = {"clip_export": b"abc", "clip_metadata": b"def"}
        manifest = _manifest(ManifestDomain.VIDEOS, "clip_export", contents)
        supplied = {role: memoryview(content) for role, content in contents.items()}
        with mock.patch.object(payload_module, "MAX_AGGREGATE_PAYLOAD_BYTES", 5):
            _assert_code(
                self,
                PayloadVerificationCode.PAYLOAD_LIMIT_EXCEEDED,
                manifest,
                supplied,
            )

    def test_payload_count_over_limit_is_rejected_before_values(self) -> None:
        class HostileValue:
            def __bytes__(self) -> bytes:
                raise RuntimeError("COUNT_LIMIT_PRIVACY_CANARY")

        supplied = {f"role{index}": HostileValue() for index in range(17)}
        error = _assert_code(
            self,
            PayloadVerificationCode.PAYLOAD_LIMIT_EXCEEDED,
            self.manifest,
            supplied,
        )
        self.assertNotIn("COUNT_LIMIT_PRIVACY_CANARY", repr(error))

    def test_payload_count_mismatch_is_classified_without_coercion(self) -> None:
        error = _assert_code(
            self,
            PayloadVerificationCode.MISSING_PAYLOAD,
            self.manifest,
            {},
        )
        self.assertNotEqual(error.code, PayloadVerificationCode.PAYLOAD_COUNT_MISMATCH)

    def test_invalid_manifest_object(self) -> None:
        _assert_code(
            self,
            PayloadVerificationCode.INVALID_MANIFEST_OBJECT,
            object(),
            {"transcript": self.content},
        )

    def test_forged_manifest_domain_is_rejected(self) -> None:
        forged = replace(self.manifest, domain=ManifestDomain.VIDEOS)
        _assert_code(
            self,
            PayloadVerificationCode.DOMAIN_STAGE_ROLE_MISMATCH,
            forged,
            {"transcript": self.content},
        )

    def test_forged_manifest_stage_is_rejected(self) -> None:
        forged = replace(self.manifest, stage="summary")
        _assert_code(
            self,
            PayloadVerificationCode.DOMAIN_STAGE_ROLE_MISMATCH,
            forged,
            {"transcript": self.content},
        )

    def test_forged_manifest_role_is_rejected(self) -> None:
        forged_payload = replace(self.manifest.payloads[0], role="summary")
        forged = replace(self.manifest, payloads=(forged_payload,))
        _assert_code(
            self,
            PayloadVerificationCode.DOMAIN_STAGE_ROLE_MISMATCH,
            forged,
            {"summary": self.content},
        )

    def test_forged_manifest_canonical_bytes_are_rejected(self) -> None:
        forged = replace(self.manifest, canonical_bytes=b"invalid")
        _assert_code(
            self,
            PayloadVerificationCode.DOMAIN_STAGE_ROLE_MISMATCH,
            forged,
            {"transcript": self.content},
        )

    def test_hostile_forged_manifest_fields_are_rejected_without_execution(self) -> None:
        class HostileRole:
            def __hash__(self) -> int:
                raise RuntimeError("HOSTILE_MANIFEST_CANARY")

            def __repr__(self) -> str:
                raise RuntimeError("HOSTILE_MANIFEST_CANARY")

        forged_payload = replace(self.manifest.payloads[0], role=HostileRole())
        forged = replace(self.manifest, payloads=(forged_payload,))
        error = _assert_code(
            self,
            PayloadVerificationCode.INVALID_MANIFEST_OBJECT,
            forged,
            {"transcript": self.content},
        )
        self.assertNotIn("HOSTILE_MANIFEST_CANARY", str(error) + repr(error))


class PayloadPrivacyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.canary = "PRIVATE_PAYLOAD_CANARY_7f42"
        self.content = self.canary.encode("ascii")
        self.manifest = _manifest(
            ManifestDomain.CALLS,
            "transcript",
            {"transcript": self.content},
        )

    def test_exception_string_and_repr_do_not_echo_input(self) -> None:
        error = _assert_code(
            self,
            PayloadVerificationCode.UNDECLARED_PAYLOAD,
            self.manifest,
            {self.canary: self.content},
        )
        self.assertNotIn(self.canary, str(error))
        self.assertNotIn(self.canary, repr(error))

    def test_payload_bytes_do_not_appear_in_errors_or_streams(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with unittest.mock.patch("sys.stdout", stdout), unittest.mock.patch("sys.stderr", stderr):
            error = _assert_code(
                self,
                PayloadVerificationCode.PAYLOAD_DIGEST_MISMATCH,
                self.manifest,
                {"transcript": b"X" * len(self.content)},
            )
        combined = str(error) + repr(error) + stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(self.canary, combined)

    def test_verified_record_repr_does_not_echo_payload_bytes(self) -> None:
        result = verify_payloads(self.manifest, {"transcript": self.content})
        rendered = repr(result) + repr(result.payloads[0])
        self.assertNotIn(self.canary, rendered)
        self.assertNotIn(repr(self.content), rendered)

    def test_hostile_mapping_does_not_execute_or_leak(self) -> None:
        canary = self.canary

        class HostileMapping(dict):
            def __len__(self) -> int:
                raise RuntimeError(canary)

            def items(self):  # type: ignore[override]
                raise RuntimeError(canary)

        error = _assert_code(
            self,
            PayloadVerificationCode.INVALID_PAYLOAD_CONTAINER,
            self.manifest,
            HostileMapping(),
        )
        self.assertNotIn(canary, str(error) + repr(error))

    def test_hostile_bytes_like_object_does_not_execute_or_leak(self) -> None:
        canary = self.canary

        class HostileBytesLike:
            def __bytes__(self) -> bytes:
                raise RuntimeError(canary)

            def __repr__(self) -> str:
                raise RuntimeError(canary)

        error = _assert_code(
            self,
            PayloadVerificationCode.INVALID_PAYLOAD_TYPE,
            self.manifest,
            {"transcript": HostileBytesLike()},
        )
        self.assertNotIn(canary, str(error) + repr(error))

    def test_formatted_uncaught_traceback_does_not_echo_rejected_input(self) -> None:
        rejected_role = self.canary
        try:
            verify_payloads(self.manifest, {rejected_role: self.content})
        except PayloadVerificationError as error:
            rendered = "".join(traceback.TracebackException.from_exception(error).format())
        else:  # pragma: no cover - defensive test failure branch
            self.fail("verification unexpectedly succeeded")
        self.assertNotIn(self.canary, rendered)

    def test_public_error_has_no_underlying_exception_chain(self) -> None:
        view = memoryview(self.content)
        view.release()
        error = _assert_code(
            self,
            PayloadVerificationCode.INVALID_PAYLOAD_TYPE,
            self.manifest,
            {"transcript": view},
        )
        self.assertIsNone(error.__cause__)
        rendered = "".join(traceback.TracebackException.from_exception(error).format())
        self.assertNotIn("released memoryview", rendered)

    def test_production_module_does_not_print_or_log(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with unittest.mock.patch("sys.stdout", stdout), unittest.mock.patch("sys.stderr", stderr):
            verify_payloads(self.manifest, {"transcript": self.content})
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")


class PayloadStaticPurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = PRODUCTION_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_production_imports_are_standard_library_only(self) -> None:
        allowed_roots = {
            "__future__",
            "dataclasses",
            "enum",
            "hashlib",
            "hmac",
            "typing",
            "src",
        }
        imported_roots = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
        self.assertLessEqual(imported_roots, allowed_roots)

    def test_only_approved_application_module_is_imported(self) -> None:
        application_imports = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src"):
                application_imports.append(node.module)
            elif isinstance(node, ast.Import):
                application_imports.extend(
                    alias.name for alias in node.names if alias.name.startswith("src")
                )
        self.assertEqual(application_imports, ["src.marketmatch_artifact_manifest"])

    def test_no_prohibited_module_imports(self) -> None:
        prohibited = {
            "os",
            "pathlib",
            "tempfile",
            "shutil",
            "socket",
            "subprocess",
            "logging",
            "time",
            "datetime",
            "random",
            "secrets",
            "uuid",
            "requests",
            "sqlalchemy",
        }
        roots = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        self.assertTrue(roots.isdisjoint(prohibited))

    def test_no_open_print_logging_or_dynamic_import_calls(self) -> None:
        forbidden_names = {"open", "print", "exec", "eval", "compile", "__import__"}
        called_names = {
            node.func.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue(called_names.isdisjoint(forbidden_names))

    def test_no_filesystem_network_process_or_time_calls(self) -> None:
        forbidden_attributes = {
            "chdir",
            "connect",
            "environ",
            "getcwd",
            "mkdir",
            "now",
            "open",
            "Popen",
            "read_text",
            "read_bytes",
            "recv",
            "run",
            "send",
            "system",
            "today",
            "unlink",
            "write_bytes",
            "write_text",
        }
        attributes = {
            node.attr
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Attribute)
        }
        self.assertTrue(attributes.isdisjoint(forbidden_attributes))

    def test_no_input_container_mutation_operations(self) -> None:
        mutation_methods = {
            "append",
            "clear",
            "extend",
            "insert",
            "pop",
            "popitem",
            "remove",
            "reverse",
            "setdefault",
            "sort",
            "update",
        }
        called_attributes = {
            node.func.attr
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertTrue(called_attributes.isdisjoint(mutation_methods))
        subscript_stores = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)
        ]
        self.assertEqual(subscript_stores, [])

    def test_no_lazy_reader_or_callback_invocation_surface(self) -> None:
        parameters = {
            argument.arg
            for node in ast.walk(self.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for argument in node.args.args
        }
        invoked_parameters = {
            node.func.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue(parameters.isdisjoint(invoked_parameters))

    def test_sha256_and_constant_time_digest_comparison_are_present(self) -> None:
        calls = {
            node.func.attr
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertIn("sha256", calls)
        self.assertIn("compare_digest", calls)

    def test_source_compiles_without_execution(self) -> None:
        compile(self.source, str(PRODUCTION_PATH), "exec")

    def test_exactly_two_approved_files_are_changed(self) -> None:
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
                "src/marketmatch_artifact_payloads.py",
                "tests/test_marketmatch_artifact_payloads.py",
            },
        )


if __name__ == "__main__":
    unittest.main()
