"""Synthetic-only tests for the MarketMatch artifact path foundation."""

from __future__ import annotations

import ast
from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from src import marketmatch_artifacts as artifacts
from src.marketmatch_artifacts import (
    ArtifactDomain,
    ArtifactPathCode,
    ArtifactPathError,
    ArtifactRoot,
    construct_output_path,
    resolve_absolute_artifact_for_read,
    resolve_artifact_for_read,
    revalidate_artifact_for_read,
)


class MarketMatchArtifactConfinementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="marketmatch-artifacts-synthetic-")
        self.base = Path(self.temp.name)
        self.calls = self.base / "calls"
        self.videos = self.base / "videos"
        self.calls.mkdir()
        self.videos.mkdir()
        self.calls_root = ArtifactRoot(ArtifactDomain.CALLS, self.calls)
        self.videos_root = ArtifactRoot(ArtifactDomain.VIDEOS, self.videos)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def assert_code(self, expected: ArtifactPathCode, operation) -> ArtifactPathError:
        with self.assertRaises(ArtifactPathError) as caught:
            operation()
        self.assertIs(caught.exception.code, expected)
        self.assertEqual(str(caught.exception), expected.value)
        return caught.exception

    def require_symlinks(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are unsupported")
        probe_target = self.base / "symlink-probe-target"
        probe_link = self.base / "symlink-probe-link"
        probe_target.write_text("probe", encoding="utf-8")
        try:
            os.symlink(probe_target, probe_link)
        except (NotImplementedError, OSError):
            self.skipTest("symlinks are unavailable to the test process")
        else:
            probe_link.unlink()
            probe_target.unlink()

    def require_hardlinks(self) -> None:
        if not hasattr(os, "link"):
            self.skipTest("hard links are unsupported")

    def resolve_calls(self, candidate):
        return resolve_artifact_for_read(
            self.calls_root,
            candidate,
            expected_domain=ArtifactDomain.CALLS,
        )

    def output_calls(self, identifier="call-123", extension=".txt", allowlist=(".txt",)):
        return construct_output_path(
            self.calls_root,
            identifier,
            extension,
            allowlist,
            expected_domain=ArtifactDomain.CALLS,
        )

    def test_valid_relative_file_beneath_root(self) -> None:
        artifact = self.calls / "transcripts" / "call.txt"
        artifact.parent.mkdir()
        artifact.write_text("synthetic transcript", encoding="utf-8")

        result = self.resolve_calls("transcripts/call.txt")

        self.assertEqual(result.path, artifact.resolve(strict=True))
        self.assertEqual(os.fspath(result), os.fspath(result.path))
        self.assertIs(result.domain, ArtifactDomain.CALLS)

    def test_validated_result_can_be_immediately_revalidated(self) -> None:
        artifact = self.calls / "call.txt"
        artifact.write_text("synthetic", encoding="utf-8")
        result = self.resolve_calls("call.txt")

        revalidated = revalidate_artifact_for_read(result)

        self.assertEqual(revalidated.path, result.path)

    def test_missing_root(self) -> None:
        root = ArtifactRoot(ArtifactDomain.CALLS, self.base / "private-canary-missing-root")
        self.assert_code(
            ArtifactPathCode.INVALID_ROOT,
            lambda: resolve_artifact_for_read(
                root,
                "artifact.txt",
                expected_domain=ArtifactDomain.CALLS,
            ),
        )

    def test_relative_root_is_rejected_without_using_current_directory(self) -> None:
        root = ArtifactRoot(ArtifactDomain.CALLS, Path("calls"))
        self.assert_code(
            ArtifactPathCode.INVALID_ROOT,
            lambda: resolve_artifact_for_read(
                root,
                "artifact.txt",
                expected_domain=ArtifactDomain.CALLS,
            ),
        )

    def test_root_is_a_file(self) -> None:
        root_file = self.base / "root-file"
        root_file.write_text("synthetic", encoding="utf-8")
        root = ArtifactRoot(ArtifactDomain.CALLS, root_file)
        self.assert_code(
            ArtifactPathCode.ROOT_NOT_DIRECTORY,
            lambda: resolve_artifact_for_read(
                root,
                "artifact.txt",
                expected_domain=ArtifactDomain.CALLS,
            ),
        )

    def test_symlinked_root(self) -> None:
        self.require_symlinks()
        link = self.base / "calls-link"
        os.symlink(self.calls, link)
        root = ArtifactRoot(ArtifactDomain.CALLS, link)
        self.assert_code(
            ArtifactPathCode.ROOT_SYMLINK,
            lambda: resolve_artifact_for_read(
                root,
                "artifact.txt",
                expected_domain=ArtifactDomain.CALLS,
            ),
        )

    def test_empty_candidate(self) -> None:
        self.assert_code(ArtifactPathCode.INVALID_CANDIDATE, lambda: self.resolve_calls(""))

    def test_nul_candidate(self) -> None:
        self.assert_code(
            ArtifactPathCode.INVALID_CANDIDATE,
            lambda: self.resolve_calls("secret\x00.txt"),
        )

    def test_bytes_candidate_is_rejected_as_encoding_ambiguous(self) -> None:
        self.assert_code(
            ArtifactPathCode.INVALID_CANDIDATE,
            lambda: self.resolve_calls(b"artifact.txt"),
        )

    def test_non_normalized_unicode_candidate_is_rejected(self) -> None:
        self.assert_code(
            ArtifactPathCode.INVALID_CANDIDATE,
            lambda: self.resolve_calls("e\u0301.txt"),
        )

    def test_portable_drive_relative_candidate_is_rejected(self) -> None:
        self.assert_code(
            ArtifactPathCode.INVALID_CANDIDATE,
            lambda: self.resolve_calls("C:artifact.txt"),
        )

    def test_relative_traversal(self) -> None:
        self.assert_code(ArtifactPathCode.PATH_ESCAPE, lambda: self.resolve_calls("../outside.txt"))

    def test_nested_traversal(self) -> None:
        self.assert_code(
            ArtifactPathCode.PATH_ESCAPE,
            lambda: self.resolve_calls("nested/deeper/../../../outside.txt"),
        )

    def test_absolute_path_rejected_by_default(self) -> None:
        artifact = self.calls / "artifact.txt"
        artifact.write_text("synthetic", encoding="utf-8")
        self.assert_code(
            ArtifactPathCode.ABSOLUTE_PATH_NOT_ALLOWED,
            lambda: self.resolve_calls(str(artifact.resolve(strict=True))),
        )

    def test_absolute_path_inside_root_requires_compatibility_function(self) -> None:
        artifact = self.calls / "artifact.txt"
        artifact.write_text("synthetic", encoding="utf-8")

        result = resolve_absolute_artifact_for_read(
            self.calls_root,
            str(artifact.resolve(strict=True)),
            expected_domain=ArtifactDomain.CALLS,
        )

        self.assertEqual(result.path, artifact.resolve(strict=True))

    def test_relative_path_rejected_by_absolute_compatibility_function(self) -> None:
        artifact = self.calls / "artifact.txt"
        artifact.write_text("synthetic", encoding="utf-8")
        self.assert_code(
            ArtifactPathCode.INVALID_CANDIDATE,
            lambda: resolve_absolute_artifact_for_read(
                self.calls_root,
                "artifact.txt",
                expected_domain=ArtifactDomain.CALLS,
            ),
        )

    def test_absolute_path_outside_root_rejected(self) -> None:
        outside = self.base / "outside.txt"
        outside.write_text("synthetic", encoding="utf-8")
        self.assert_code(
            ArtifactPathCode.PATH_ESCAPE,
            lambda: resolve_absolute_artifact_for_read(
                self.calls_root,
                str(outside.resolve(strict=True)),
                expected_domain=ArtifactDomain.CALLS,
            ),
        )

    def test_double_separator_absolute_path_is_rejected_as_ambiguous(self) -> None:
        candidate = "//" + os.fspath(self.calls).lstrip("/") + "/artifact.txt"
        self.assert_code(
            ArtifactPathCode.INVALID_CANDIDATE,
            lambda: resolve_absolute_artifact_for_read(
                self.calls_root,
                candidate,
                expected_domain=ArtifactDomain.CALLS,
            ),
        )

    def test_symlink_file_escape(self) -> None:
        self.require_symlinks()
        outside = self.base / "outside.txt"
        outside.write_text("synthetic", encoding="utf-8")
        os.symlink(outside, self.calls / "linked.txt")
        self.assert_code(
            ArtifactPathCode.PATH_COMPONENT_SYMLINK,
            lambda: self.resolve_calls("linked.txt"),
        )

    def test_symlink_directory_component_escape(self) -> None:
        self.require_symlinks()
        outside_dir = self.base / "outside-dir"
        outside_dir.mkdir()
        (outside_dir / "artifact.txt").write_text("synthetic", encoding="utf-8")
        os.symlink(outside_dir, self.calls / "linked-dir")
        self.assert_code(
            ArtifactPathCode.PATH_COMPONENT_SYMLINK,
            lambda: self.resolve_calls("linked-dir/artifact.txt"),
        )

    def test_symlink_pointing_inside_root_is_still_rejected(self) -> None:
        self.require_symlinks()
        target = self.calls / "target.txt"
        target.write_text("synthetic", encoding="utf-8")
        os.symlink(target, self.calls / "inside-link.txt")
        self.assert_code(
            ArtifactPathCode.PATH_COMPONENT_SYMLINK,
            lambda: self.resolve_calls("inside-link.txt"),
        )

    def test_broken_symlink_is_rejected(self) -> None:
        self.require_symlinks()
        os.symlink(self.base / "does-not-exist", self.calls / "broken-link.txt")
        self.assert_code(
            ArtifactPathCode.PATH_COMPONENT_SYMLINK,
            lambda: self.resolve_calls("broken-link.txt"),
        )

    def test_hard_linked_file_is_rejected(self) -> None:
        self.require_hardlinks()
        original = self.calls / "original.txt"
        linked = self.calls / "linked.txt"
        original.write_text("synthetic", encoding="utf-8")
        try:
            os.link(original, linked)
        except OSError:
            self.skipTest("hard links are unavailable to the test process")
        self.assert_code(
            ArtifactPathCode.ARTIFACT_HARDLINKED,
            lambda: self.resolve_calls("linked.txt"),
        )

    def test_fifo_is_rejected_where_supported(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFOs are unsupported")
        fifo = self.calls / "artifact.fifo"
        try:
            os.mkfifo(fifo)
        except (NotImplementedError, OSError):
            self.skipTest("FIFOs are unavailable to the test process")
        self.assert_code(
            ArtifactPathCode.ARTIFACT_NOT_REGULAR,
            lambda: self.resolve_calls("artifact.fifo"),
        )

    def test_directory_candidate_is_rejected(self) -> None:
        (self.calls / "directory").mkdir()
        self.assert_code(
            ArtifactPathCode.ARTIFACT_NOT_REGULAR,
            lambda: self.resolve_calls("directory"),
        )

    def test_socket_is_rejected_where_supported(self) -> None:
        if not hasattr(socket, "AF_UNIX"):
            self.skipTest("Unix sockets are unsupported")
        with tempfile.TemporaryDirectory(prefix="mm-s-", dir="/tmp") as short_temp:
            short_root_path = Path(short_temp) / "calls"
            short_root_path.mkdir()
            short_root = ArtifactRoot(ArtifactDomain.CALLS, short_root_path)
            path = short_root_path / "s"
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                server.bind(os.fspath(path))
                self.assert_code(
                    ArtifactPathCode.ARTIFACT_NOT_REGULAR,
                    lambda: resolve_artifact_for_read(
                        short_root,
                        "s",
                        expected_domain=ArtifactDomain.CALLS,
                    ),
                )
            finally:
                server.close()

    def test_device_rejection_is_explicitly_skipped_without_safe_fixture_support(self) -> None:
        self.skipTest("unprivileged synthetic device-node creation is unsupported")

    def test_missing_artifact(self) -> None:
        self.assert_code(
            ArtifactPathCode.ARTIFACT_NOT_FOUND,
            lambda: self.resolve_calls("missing.txt"),
        )

    def test_candidate_disappearing_during_validation(self) -> None:
        artifact = self.calls / "artifact.txt"
        held = self.calls / "artifact-held.txt"
        artifact.write_text("synthetic", encoding="utf-8")

        def race(stage: str) -> None:
            if stage == "candidate_recorded":
                artifact.rename(held)

        with mock.patch.object(artifacts, "_validation_checkpoint", side_effect=race):
            self.assert_code(
                ArtifactPathCode.IDENTITY_CHANGED,
                lambda: self.resolve_calls("artifact.txt"),
            )

    def test_candidate_inode_replacement_during_validation(self) -> None:
        artifact = self.calls / "artifact.txt"
        held = self.calls / "artifact-held.txt"
        artifact.write_text("original", encoding="utf-8")

        def race(stage: str) -> None:
            if stage == "candidate_recorded":
                artifact.rename(held)
                artifact.write_text("replacement", encoding="utf-8")

        with mock.patch.object(artifacts, "_validation_checkpoint", side_effect=race):
            self.assert_code(
                ArtifactPathCode.IDENTITY_CHANGED,
                lambda: self.resolve_calls("artifact.txt"),
            )

    def test_candidate_symlink_replacement_during_validation(self) -> None:
        self.require_symlinks()
        artifact = self.calls / "artifact.txt"
        held = self.calls / "artifact-held.txt"
        outside = self.base / "private-canary-outside.txt"
        artifact.write_text("original", encoding="utf-8")
        outside.write_text("outside", encoding="utf-8")

        def race(stage: str) -> None:
            if stage == "candidate_recorded":
                artifact.rename(held)
                os.symlink(outside, artifact)

        with mock.patch.object(artifacts, "_validation_checkpoint", side_effect=race):
            self.assert_code(
                ArtifactPathCode.IDENTITY_CHANGED,
                lambda: self.resolve_calls("artifact.txt"),
            )

    def test_root_inode_replacement_during_validation(self) -> None:
        artifact = self.calls / "artifact.txt"
        artifact.write_text("synthetic", encoding="utf-8")
        held_root = self.base / "calls-held"

        def race(stage: str) -> None:
            if stage == "root_recorded":
                self.calls.rename(held_root)
                self.calls.mkdir()

        try:
            with mock.patch.object(artifacts, "_validation_checkpoint", side_effect=race):
                self.assert_code(
                    ArtifactPathCode.IDENTITY_CHANGED,
                    lambda: self.resolve_calls("artifact.txt"),
                )
        finally:
            if held_root.exists():
                if self.calls.exists():
                    self.calls.rmdir()
                held_root.rename(self.calls)

    def test_revalidation_rejects_replaced_artifact(self) -> None:
        artifact = self.calls / "artifact.txt"
        held = self.calls / "artifact-held.txt"
        artifact.write_text("original", encoding="utf-8")
        validated = self.resolve_calls("artifact.txt")
        artifact.rename(held)
        artifact.write_text("replacement", encoding="utf-8")

        self.assert_code(
            ArtifactPathCode.IDENTITY_CHANGED,
            lambda: revalidate_artifact_for_read(validated),
        )

    def test_valid_safe_output_path(self) -> None:
        destination = self.output_calls()
        self.assertEqual(destination, self.calls.resolve(strict=True) / "call-123.txt")
        self.assertFalse(destination.exists())

    def test_empty_output_identifier(self) -> None:
        self.assert_code(ArtifactPathCode.INVALID_IDENTIFIER, lambda: self.output_calls(identifier=""))

    def test_identifier_containing_slash(self) -> None:
        self.assert_code(
            ArtifactPathCode.INVALID_IDENTIFIER,
            lambda: self.output_calls(identifier="nested/call"),
        )

    def test_identifier_containing_backslash(self) -> None:
        self.assert_code(
            ArtifactPathCode.INVALID_IDENTIFIER,
            lambda: self.output_calls(identifier="nested\\call"),
        )

    def test_identifier_containing_traversal_token(self) -> None:
        self.assert_code(
            ArtifactPathCode.INVALID_IDENTIFIER,
            lambda: self.output_calls(identifier="call..escape"),
        )

    def test_identifier_containing_nul(self) -> None:
        self.assert_code(
            ArtifactPathCode.INVALID_IDENTIFIER,
            lambda: self.output_calls(identifier="call\x00escape"),
        )

    def test_identifier_rejects_ambiguous_unicode(self) -> None:
        self.assert_code(
            ArtifactPathCode.INVALID_IDENTIFIER,
            lambda: self.output_calls(identifier="c\u00e1ll"),
        )

    def test_invalid_extension_not_in_allowlist(self) -> None:
        self.assert_code(
            ArtifactPathCode.INVALID_EXTENSION,
            lambda: self.output_calls(extension=".md", allowlist=(".txt",)),
        )

    def test_extension_containing_path_separator(self) -> None:
        self.assert_code(
            ArtifactPathCode.INVALID_EXTENSION,
            lambda: self.output_calls(extension="/txt", allowlist=("/txt",)),
        )

    def test_extension_containing_backslash(self) -> None:
        self.assert_code(
            ArtifactPathCode.INVALID_EXTENSION,
            lambda: self.output_calls(extension=".bad\\txt", allowlist=(".bad\\txt",)),
        )

    def test_empty_extension_allowlist(self) -> None:
        self.assert_code(
            ArtifactPathCode.INVALID_EXTENSION,
            lambda: self.output_calls(allowlist=()),
        )

    def test_destination_already_exists(self) -> None:
        (self.calls / "call-123.txt").write_text("synthetic", encoding="utf-8")
        self.assert_code(ArtifactPathCode.DESTINATION_EXISTS, self.output_calls)

    def test_destination_symlink_exists(self) -> None:
        self.require_symlinks()
        target = self.base / "outside.txt"
        target.write_text("synthetic", encoding="utf-8")
        os.symlink(target, self.calls / "call-123.txt")
        self.assert_code(ArtifactPathCode.DESTINATION_EXISTS, self.output_calls)

    def test_destination_broken_symlink_exists(self) -> None:
        self.require_symlinks()
        os.symlink(self.base / "missing.txt", self.calls / "call-123.txt")
        self.assert_code(ArtifactPathCode.DESTINATION_EXISTS, self.output_calls)

    def test_destination_hard_link_exists(self) -> None:
        self.require_hardlinks()
        source = self.calls / "source.txt"
        source.write_text("synthetic", encoding="utf-8")
        try:
            os.link(source, self.calls / "call-123.txt")
        except OSError:
            self.skipTest("hard links are unavailable to the test process")
        self.assert_code(ArtifactPathCode.DESTINATION_EXISTS, self.output_calls)

    def test_destination_appearing_during_validation_is_rejected(self) -> None:
        destination = self.calls / "call-123.txt"

        def race(stage: str) -> None:
            if stage == "destination_checked":
                destination.write_text("raced", encoding="utf-8")

        with mock.patch.object(artifacts, "_validation_checkpoint", side_effect=race):
            self.assert_code(ArtifactPathCode.DESTINATION_EXISTS, self.output_calls)

    def test_symlinked_output_parent_is_rejected(self) -> None:
        self.require_symlinks()
        linked_root = self.base / "output-root-link"
        os.symlink(self.calls, linked_root)
        root = ArtifactRoot(ArtifactDomain.CALLS, linked_root)
        self.assert_code(
            ArtifactPathCode.ROOT_SYMLINK,
            lambda: construct_output_path(
                root,
                "call-123",
                ".txt",
                (".txt",),
                expected_domain=ArtifactDomain.CALLS,
            ),
        )

    def test_calls_root_cannot_be_used_as_video_root(self) -> None:
        artifact = self.calls / "artifact.txt"
        artifact.write_text("synthetic", encoding="utf-8")
        self.assert_code(
            ArtifactPathCode.ROOT_DOMAIN_MISMATCH,
            lambda: resolve_artifact_for_read(
                self.calls_root,
                "artifact.txt",
                expected_domain=ArtifactDomain.VIDEOS,
            ),
        )

    def test_video_root_cannot_be_used_for_calls_output(self) -> None:
        self.assert_code(
            ArtifactPathCode.ROOT_DOMAIN_MISMATCH,
            lambda: construct_output_path(
                self.videos_root,
                "call-123",
                ".txt",
                (".txt",),
                expected_domain=ArtifactDomain.CALLS,
            ),
        )

    def test_validation_creates_no_file_or_directory(self) -> None:
        artifact = self.calls / "artifact.txt"
        artifact.write_text("synthetic", encoding="utf-8")
        before = sorted(str(path.relative_to(self.base)) for path in self.base.rglob("*"))

        self.resolve_calls("artifact.txt")
        destination = self.output_calls()

        after = sorted(str(path.relative_to(self.base)) for path in self.base.rglob("*"))
        self.assertEqual(after, before)
        self.assertFalse(destination.exists())

    def test_validation_does_not_modify_file(self) -> None:
        artifact = self.calls / "artifact.txt"
        artifact.write_text("synthetic-content", encoding="utf-8")
        before = artifact.stat()
        before_content = artifact.read_bytes()

        self.resolve_calls("artifact.txt")

        after = artifact.stat()
        self.assertEqual(artifact.read_bytes(), before_content)
        self.assertEqual(after.st_dev, before.st_dev)
        self.assertEqual(after.st_ino, before.st_ino)
        self.assertEqual(after.st_size, before.st_size)
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(after.st_mode, before.st_mode)

    def test_error_privacy_canaries_absent_from_errors_and_streams(self) -> None:
        canary = "PRIVATE-CANARY-USERNAME-HOME-ARTIFACT-ID"

        class HostilePathLike:
            def __fspath__(self):
                raise OSError(canary)

        operations = (
            lambda: resolve_artifact_for_read(
                ArtifactRoot(ArtifactDomain.CALLS, self.base / canary),
                canary,
                expected_domain=ArtifactDomain.CALLS,
            ),
            lambda: self.resolve_calls(f"../{canary}"),
            lambda: self.output_calls(identifier=f"bad/{canary}"),
            lambda: self.output_calls(extension=f".{canary}/txt", allowlist=(".txt",)),
            lambda: self.resolve_calls(HostilePathLike()),
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            for operation in operations:
                with self.assertRaises(ArtifactPathError) as caught:
                    operation()
                self.assertNotIn(canary, str(caught.exception))
                self.assertNotIn(canary, repr(caught.exception))
        self.assertNotIn(canary, stdout.getvalue())
        self.assertNotIn(canary, stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_opaque_reprs_hide_paths(self) -> None:
        artifact = self.calls / "private-filename-canary.txt"
        artifact.write_text("synthetic", encoding="utf-8")
        result = self.resolve_calls("private-filename-canary.txt")
        self.assertNotIn(os.fspath(self.calls), repr(self.calls_root))
        self.assertNotIn("private-filename-canary", repr(result))


class MarketMatchArtifactStaticBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo = Path(__file__).resolve().parents[1]
        cls.production = cls.repo / "src" / "marketmatch_artifacts.py"
        cls.source = cls.production.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_production_module_uses_standard_library_only(self) -> None:
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        imported.discard("__future__")
        self.assertTrue(imported)
        self.assertEqual(imported - sys.stdlib_module_names, set())

    def test_no_prohibited_imports(self) -> None:
        prohibited = {
            "app", "routes", "core", "database", "sqlalchemy", "chromadb",
            "rag", "stt", "upload_handler", "ffmpeg", "src",
        }
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        self.assertEqual(imported & prohibited, set())

    def test_no_environment_variable_reads(self) -> None:
        self.assertNotIn("os.getenv", self.source)
        self.assertNotIn("os.environ", self.source)
        self.assertNotIn("getenv(", self.source)

    def test_no_implicit_current_working_directory_root(self) -> None:
        forbidden = ("os.getcwd", "Path.cwd", "expanduser", "Path.home", "os.path.abspath")
        for text in forbidden:
            self.assertNotIn(text, self.source)

    def test_production_has_no_content_read_or_write_calls(self) -> None:
        forbidden_names = {"open", "read", "write", "read_text", "read_bytes", "write_text", "write_bytes"}
        calls = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                calls.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                # os.open is used only for no-follow directory descriptors.
                if node.func.attr != "open":
                    calls.append(node.func.attr)
        self.assertEqual(set(calls) & forbidden_names, set())

    def test_production_has_no_mutating_filesystem_calls(self) -> None:
        forbidden = {
            "chmod", "chown", "link", "makedirs", "mkdir", "remove", "rename",
            "replace", "rmdir", "symlink", "truncate", "unlink",
        }
        attributes = set()
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            # String normalization uses str.replace; os.replace remains forbidden.
            if node.func.attr != "replace" or (
                isinstance(node.func.value, ast.Name) and node.func.value.id == "os"
            ):
                attributes.add(node.func.attr)
        self.assertEqual(attributes & forbidden, set())

    def test_production_does_not_log_or_print(self) -> None:
        self.assertNotIn("logging", self.source)
        names = {
            node.func.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertNotIn("print", names)

    def test_source_tree_changes_are_limited_to_approved_files(self) -> None:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        changed = {
            line[3:]
            for line in result.stdout.splitlines()
            if len(line) >= 4
        }
        allowed = {
            "src/marketmatch_artifacts.py",
            "tests/test_marketmatch_artifact_confinement.py",
        }
        self.assertTrue(changed <= allowed, changed - allowed)


if __name__ == "__main__":
    unittest.main()
