#!/usr/bin/env python3
"""Validate a sealed synthetic MarketMatch metadata snapshot fixture.

This validator is deliberately fixture-only.  It reads four sanitized JSON
files beneath the dedicated test launcher's temporary root, writes only a
fixed-schema report to stdout, and cannot capture or inspect a real source.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, Sequence, TextIO


SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parent
CONTRACT_PATH = (
    SCRIPT_DIR / "offline_package_contracts" / "chroma_metadata_export_v1.json"
)
CONTRACT_SHA256 = "2cf74c5a3955bc972deb5f3099233c13459ee9b999e11ad5baaf7550c65ae0aa"
TEST_LAUNCHER_SHA256 = "eef5f3361a6682cf0463a38056012460400a47b5b9b59cf4f01e17c0925aacf0"

SOURCE_CLASSIFICATION = "SYNTHETIC_DISPOSABLE_FIXTURE"
PROOF_SCOPE = "OBSERVED_PHYSICAL_METADATA_ROWS_ONLY"
CAPTURE_STATUS = "COMPLETE_SYNTHETIC_FIXTURE"
EXPECTED_FILES = frozenset(
    {
        "snapshot-manifest.json",
        "collections.json",
        "records.jsonl",
        "capture-attestation.json",
    }
)
HASHED_FILES = frozenset(
    {"capture-attestation.json", "collections.json", "records.jsonl"}
)
BLOCKED_ENVIRONMENT = (
    "DATABASE_URL",
    "ODYSSEUS_DATA_DIR",
    "APP_DATA_DIR",
    "DATA_DIR",
    "ODYSSEUS_MAIL_ATTACHMENTS_DIR",
    "FASTEMBED_CACHE_PATH",
    "AUTH_FILE",
    "MEMORY_FILE",
    "USER_PREFS_FILE",
    "SETTINGS_FILE",
    "UPLOAD_DIR",
    "UPLOAD_FOLDER",
    "CHROMA_DIR",
    "CHROMA_PATH",
    "CHROMA_DB_PATH",
    "CHROMA_HOST",
    "CHROMA_PORT",
    "CHROMADB_HOST",
    "CHROMADB_PORT",
    "CHROMADB_CONNECT_TIMEOUT",
    "CHROMA_TENANT",
    "CHROMA_DATABASE",
    "CHROMA_SERVER_AUTH_CREDENTIALS",
    "RAG_DIR",
    "RAG_DB_PATH",
    "RAG_ENDPOINT",
    "EMAIL_CACHE_DB",
    "SQLITE_TMPDIR",
)
SAFE_REPORT_BASE = {
    "approved": False,
    "complete": False,
    "logical_chunk_completeness": False,
    "proof_scope": PROOF_SCOPE,
    "real_chroma_access_performed": False,
    "real_source_capture_authorized": False,
}
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
FIXTURE_ID = re.compile(r"^fixture_[0-9a-f]{32}$")


class SnapshotError(RuntimeError):
    """A fail-closed validation error whose fixed code is safe to report."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise SnapshotError("ARGUMENT_ERROR")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _canonical_digest(value: dict[str, Any]) -> str:
    payload = copy.deepcopy(value)
    payload.pop("canonical_sha256", None)
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _tree_digest(entries: list[dict[str, Any]]) -> str:
    ordered = sorted(entries, key=lambda item: item["path"])
    return hashlib.sha256(_canonical_bytes(ordered)).hexdigest()


def _exact_keys(value: Any, expected: Sequence[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(expected):
        raise SnapshotError(code)
    return value


def _string_list(
    value: Any,
    *,
    code: str,
    allowed: frozenset[str] | None = None,
    maximum: int = 10000,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise SnapshotError(code)
    if any(not isinstance(item, str) for item in value):
        raise SnapshotError(code)
    if len(set(value)) != len(value) or value != sorted(value):
        raise SnapshotError(code)
    if allowed is not None and not set(value).issubset(allowed):
        raise SnapshotError(code)
    return value


def _read_fd(descriptor: int, size: int) -> bytes:
    if size < 0:
        raise SnapshotError("READ_ERROR")
    chunks: list[bytes] = []
    offset = 0
    while offset < size:
        try:
            chunk = os.pread(descriptor, min(1024 * 1024, size - offset), offset)
        except OSError as exc:
            raise SnapshotError("READ_ERROR") from exc
        if not chunk:
            raise SnapshotError("READ_ERROR")
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def _sha256_fd(descriptor: int, size: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        try:
            chunk = os.pread(descriptor, min(1024 * 1024, size - offset), offset)
        except OSError as exc:
            raise SnapshotError("READ_ERROR") from exc
        if not chunk:
            raise SnapshotError("READ_ERROR")
        digest.update(chunk)
        offset += len(chunk)
    return digest.hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _ensure_no_symlink_components(path: Path, boundary: Path) -> None:
    try:
        relative = path.relative_to(boundary)
    except ValueError as exc:
        raise SnapshotError("SNAPSHOT_PATH_INVALID") from exc
    current = boundary
    for part in relative.parts:
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise SnapshotError("SNAPSHOT_PATH_INVALID") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise SnapshotError("SYMLINK_REJECTED")


def _load_contract() -> dict[str, Any]:
    try:
        raw = CONTRACT_PATH.read_bytes()
        contract = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SnapshotError("CONTRACT_INVALID") from exc
    if not isinstance(contract, dict):
        raise SnapshotError("CONTRACT_INVALID")
    if contract.get("canonical_sha256") != CONTRACT_SHA256:
        raise SnapshotError("CONTRACT_INVALID")
    if not hmac.compare_digest(_canonical_digest(contract), CONTRACT_SHA256):
        raise SnapshotError("CONTRACT_INVALID")
    if contract.get("contract_name") != "marketmatch-synthetic-chroma-metadata-snapshot-v1":
        raise SnapshotError("CONTRACT_INVALID")
    gate = contract.get("fixture_gate")
    if not isinstance(gate, dict) or gate.get("required_launcher_sha256") != TEST_LAUNCHER_SHA256:
        raise SnapshotError("CONTRACT_INVALID")
    policy = contract.get("usage_policy")
    if not isinstance(policy, dict) or policy.get("real_source_capture") != "NOT_AUTHORIZED":
        raise SnapshotError("CONTRACT_INVALID")
    if policy.get("chroma_access") != "PROHIBITED" or policy.get("network_access") != "PROHIBITED":
        raise SnapshotError("CONTRACT_INVALID")
    return contract


def _fixture_root() -> tuple[Path, str]:
    for variable in BLOCKED_ENVIRONMENT:
        if variable in os.environ:
            raise SnapshotError("RUNTIME_ENVIRONMENT_PRESENT")
    raw_root = os.environ.get("MARKETMATCH_CHROMA_SNAPSHOT_TEST_ROOT")
    sentinel = os.environ.get("MARKETMATCH_CHROMA_SNAPSHOT_SENTINEL")
    sentinel_fd_text = os.environ.get("MARKETMATCH_CHROMA_SNAPSHOT_SENTINEL_FD")
    launcher_fd_text = os.environ.get("MARKETMATCH_CHROMA_SNAPSHOT_LAUNCHER_FD")
    launcher_pid_text = os.environ.get("MARKETMATCH_CHROMA_SNAPSHOT_LAUNCHER_PID")
    if not raw_root or not sentinel or not HEX_64.fullmatch(sentinel):
        raise SnapshotError("FIXTURE_LAUNCHER_REQUIRED")
    try:
        sentinel_fd = int(sentinel_fd_text or "", 10)
        launcher_fd = int(launcher_fd_text or "", 10)
        launcher_pid = int(launcher_pid_text or "", 10)
    except ValueError as exc:
        raise SnapshotError("FIXTURE_LAUNCHER_REQUIRED") from exc
    if sentinel_fd < 3 or launcher_fd < 3 or launcher_pid != os.getppid():
        raise SnapshotError("FIXTURE_LAUNCHER_REQUIRED")
    try:
        sentinel_flags = fcntl.fcntl(sentinel_fd, fcntl.F_GETFL)
        sentinel_metadata = os.fstat(sentinel_fd)
        sentinel_value = _read_fd(sentinel_fd, sentinel_metadata.st_size).decode("ascii").strip()
    except (OSError, UnicodeError, SnapshotError) as exc:
        raise SnapshotError("FIXTURE_LAUNCHER_REQUIRED") from exc
    if (sentinel_flags & os.O_ACCMODE) != os.O_RDONLY:
        raise SnapshotError("FIXTURE_LAUNCHER_REQUIRED")
    if not stat.S_ISREG(sentinel_metadata.st_mode) or sentinel_metadata.st_nlink != 1:
        raise SnapshotError("FIXTURE_LAUNCHER_REQUIRED")
    if not hmac.compare_digest(sentinel_value, sentinel):
        raise SnapshotError("FIXTURE_LAUNCHER_REQUIRED")

    expected_launcher = REPOSITORY_ROOT / "tests" / "run_offline_chroma_export_tests.sh"
    try:
        launcher_flags = fcntl.fcntl(launcher_fd, fcntl.F_GETFL)
        launcher_metadata = os.fstat(launcher_fd)
        expected_metadata = expected_launcher.lstat()
        launcher_digest = _sha256_fd(launcher_fd, launcher_metadata.st_size)
    except (OSError, SnapshotError) as exc:
        raise SnapshotError("FIXTURE_LAUNCHER_REQUIRED") from exc
    if (
        (launcher_flags & os.O_ACCMODE) != os.O_RDONLY
        or not stat.S_ISREG(launcher_metadata.st_mode)
        or launcher_metadata.st_nlink != 1
        or (launcher_metadata.st_dev, launcher_metadata.st_ino)
        != (expected_metadata.st_dev, expected_metadata.st_ino)
        or not hmac.compare_digest(launcher_digest, TEST_LAUNCHER_SHA256)
    ):
        raise SnapshotError("FIXTURE_LAUNCHER_REQUIRED")

    root_input = Path(raw_root)
    if not root_input.is_absolute() or any(part in {".", ".."} for part in root_input.parts):
        raise SnapshotError("FIXTURE_LAUNCHER_REQUIRED")
    try:
        _ensure_no_symlink_components(root_input, Path(root_input.anchor))
        root = root_input.resolve(strict=True)
        temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
        repository = REPOSITORY_ROOT.resolve(strict=True)
        home = Path.home().resolve(strict=True)
    except OSError as exc:
        raise SnapshotError("FIXTURE_LAUNCHER_REQUIRED") from exc
    if not _is_relative_to(root, temporary_root) or root == temporary_root:
        raise SnapshotError("FIXTURE_ROOT_NOT_TEMPORARY")
    if _is_relative_to(root, repository) or _is_relative_to(root, home):
        raise SnapshotError("PERSISTENT_PATH_REJECTED")
    sentinel_path = root / ".launcher-chroma-snapshot-sentinel"
    _ensure_no_symlink_components(sentinel_path, root)
    try:
        sentinel_path_metadata = sentinel_path.lstat()
    except OSError as exc:
        raise SnapshotError("FIXTURE_LAUNCHER_REQUIRED") from exc
    if (
        not stat.S_ISREG(sentinel_path_metadata.st_mode)
        or sentinel_path_metadata.st_nlink != 1
        or (sentinel_path_metadata.st_dev, sentinel_path_metadata.st_ino)
        != (sentinel_metadata.st_dev, sentinel_metadata.st_ino)
    ):
        raise SnapshotError("FIXTURE_LAUNCHER_REQUIRED")
    return root, sentinel


def _approved_snapshot_path(value: str) -> tuple[Path, str]:
    root, sentinel = _fixture_root()
    candidate = Path(value)
    if candidate.is_absolute() or candidate.parts[:1] != ("snapshots",):
        raise SnapshotError("SNAPSHOT_PATH_INVALID")
    if len(candidate.parts) != 2 or any(part in {"", ".", ".."} for part in candidate.parts):
        raise SnapshotError("SNAPSHOT_PATH_INVALID")
    path = root / candidate
    _ensure_no_symlink_components(path, root)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SnapshotError("SNAPSHOT_PATH_INVALID") from exc
    if not _is_relative_to(resolved, root / "snapshots") or resolved == root / "snapshots":
        raise SnapshotError("SNAPSHOT_PATH_INVALID")
    return resolved, sentinel


class SnapshotDirectory:
    def __init__(self, path: Path):
        self.path = path
        try:
            namespace_metadata = path.lstat()
            descriptor = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            opened_metadata = os.fstat(descriptor)
        except OSError as exc:
            raise SnapshotError("SNAPSHOT_PATH_INVALID") from exc
        if not stat.S_ISDIR(opened_metadata.st_mode) or (
            namespace_metadata.st_dev,
            namespace_metadata.st_ino,
        ) != (opened_metadata.st_dev, opened_metadata.st_ino):
            os.close(descriptor)
            raise SnapshotError("SNAPSHOT_REPLACED")
        self.descriptor = descriptor
        self.identity = (opened_metadata.st_dev, opened_metadata.st_ino)
        if stat.S_IMODE(opened_metadata.st_mode) & 0o222:
            os.close(descriptor)
            raise SnapshotError("SNAPSHOT_NOT_READ_ONLY")

    def close(self) -> None:
        os.close(self.descriptor)

    def namespace_matches(self) -> bool:
        try:
            metadata = self.path.lstat()
        except OSError:
            return False
        return stat.S_ISDIR(metadata.st_mode) and (
            metadata.st_dev,
            metadata.st_ino,
        ) == self.identity

    def inventory(self) -> dict[str, Any]:
        if not self.namespace_matches():
            raise SnapshotError("SNAPSHOT_REPLACED")
        try:
            names = sorted(entry.name for entry in os.scandir(self.descriptor))
        except OSError as exc:
            raise SnapshotError("READ_ERROR") from exc
        sidecars = [
            name for name in names if name.endswith(("-wal", "-shm", "-journal"))
        ]
        if sidecars:
            raise SnapshotError("SIDECAR_REJECTED")
        if set(names) != EXPECTED_FILES:
            raise SnapshotError("UNEXPECTED_FILE")
        files: dict[str, Any] = {}
        for name in names:
            try:
                metadata = os.stat(
                    name, dir_fd=self.descriptor, follow_symlinks=False
                )
            except OSError as exc:
                raise SnapshotError("READ_ERROR") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise SnapshotError("SYMLINK_REJECTED")
            if not stat.S_ISREG(metadata.st_mode):
                raise SnapshotError("SPECIAL_FILE_REJECTED")
            if metadata.st_nlink != 1:
                raise SnapshotError("HARD_LINK_REJECTED")
            if stat.S_IMODE(metadata.st_mode) & 0o222:
                raise SnapshotError("SNAPSHOT_NOT_READ_ONLY")
            try:
                file_descriptor = os.open(
                    name,
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=self.descriptor,
                )
                opened = os.fstat(file_descriptor)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or opened.st_nlink != 1
                    or (opened.st_dev, opened.st_ino)
                    != (metadata.st_dev, metadata.st_ino)
                ):
                    raise SnapshotError("SNAPSHOT_REPLACED")
                digest = _sha256_fd(file_descriptor, opened.st_size)
            except OSError as exc:
                raise SnapshotError("READ_ERROR") from exc
            finally:
                if "file_descriptor" in locals():
                    os.close(file_descriptor)
                    del file_descriptor
            files[name] = {
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
                "type": stat.S_IFMT(metadata.st_mode),
                "mode": stat.S_IMODE(metadata.st_mode),
                "link_count": metadata.st_nlink,
                "size": metadata.st_size,
                "sha256": digest,
            }
        root_metadata = os.fstat(self.descriptor)
        return {
            "root": {
                "device": root_metadata.st_dev,
                "inode": root_metadata.st_ino,
                "type": stat.S_IFMT(root_metadata.st_mode),
                "mode": stat.S_IMODE(root_metadata.st_mode),
                "link_count": root_metadata.st_nlink,
                "size": root_metadata.st_size,
            },
            "entries": names,
            "files": files,
        }

    def read(self, name: str, before: dict[str, Any], maximum: int) -> bytes:
        expected = before["files"].get(name)
        if expected is None or expected["size"] > maximum:
            raise SnapshotError("INPUT_SIZE_INVALID")
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=self.descriptor,
            )
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or (metadata.st_dev, metadata.st_ino)
                != (expected["device"], expected["inode"])
                or metadata.st_size != expected["size"]
            ):
                raise SnapshotError("SNAPSHOT_REPLACED")
            payload = _read_fd(descriptor, metadata.st_size)
            if not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), expected["sha256"]):
                raise SnapshotError("SOURCE_CHANGED_DURING_VALIDATION")
            return payload
        except OSError as exc:
            raise SnapshotError("READ_ERROR") from exc
        finally:
            if "descriptor" in locals():
                os.close(descriptor)


def _load_json_bytes(payload: bytes, code: str) -> Any:
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SnapshotError(code) from exc


def _validate_manifest(
    manifest: Any,
    contract: dict[str, Any],
    before: dict[str, Any],
) -> tuple[str, list[str]]:
    layout = contract["snapshot_layout"]
    value = _exact_keys(manifest, layout["manifest_exact_keys"], "MANIFEST_KEYS_INVALID")
    if (
        type(value["manifest_version"]) is not int
        or value["manifest_version"] != 1
        or value["contract_name"] != contract["contract_name"]
    ):
        raise SnapshotError("MANIFEST_CONTRACT_MISMATCH")
    if value["contract_sha256"] != CONTRACT_SHA256:
        raise SnapshotError("MANIFEST_CONTRACT_MISMATCH")
    if value["source_classification"] != SOURCE_CLASSIFICATION or value["sealed"] is not True:
        raise SnapshotError("MANIFEST_NOT_SEALED_SYNTHETIC")
    fixture_id = value["fixture_id"]
    if not isinstance(fixture_id, str) or not FIXTURE_ID.fullmatch(fixture_id):
        raise SnapshotError("FIXTURE_ID_INVALID")
    declared = _string_list(
        value["declared_collections"],
        code="DECLARED_COLLECTIONS_INVALID",
        maximum=contract["limits"]["maximum_collections"],
    )
    allowed = contract["collection_contract"]["physical_collections"]
    if any(name not in allowed for name in declared):
        raise SnapshotError("UNKNOWN_OR_UNSUPPORTED")
    files = value["files"]
    if not isinstance(files, list) or len(files) != len(HASHED_FILES):
        raise SnapshotError("MANIFEST_FILE_INVENTORY_INVALID")
    entries: list[dict[str, Any]] = []
    seen = set()
    for row in files:
        row = _exact_keys(
            row, layout["manifest_file_record_keys"], "MANIFEST_FILE_INVENTORY_INVALID"
        )
        name = row["path"]
        if name not in HASHED_FILES or name in seen:
            raise SnapshotError("MANIFEST_FILE_INVENTORY_INVALID")
        seen.add(name)
        if (
            isinstance(row["size"], bool)
            or not isinstance(row["size"], int)
            or row["size"] < 0
            or not isinstance(row["sha256"], str)
            or not HEX_64.fullmatch(row["sha256"])
        ):
            raise SnapshotError("MANIFEST_FILE_INVENTORY_INVALID")
        actual = before["files"][name]
        if row["size"] != actual["size"] or not hmac.compare_digest(
            row["sha256"], actual["sha256"]
        ):
            raise SnapshotError("SEALED_FILE_DIGEST_MISMATCH")
        entries.append(row)
    if seen != HASHED_FILES or not hmac.compare_digest(value["tree_sha256"], _tree_digest(entries)):
        raise SnapshotError("MANIFEST_TREE_DIGEST_MISMATCH")
    canonical = value["canonical_sha256"]
    if not isinstance(canonical, str) or not HEX_64.fullmatch(canonical):
        raise SnapshotError("MANIFEST_CANONICAL_DIGEST_MISMATCH")
    if not hmac.compare_digest(canonical, _canonical_digest(value)):
        raise SnapshotError("MANIFEST_CANONICAL_DIGEST_MISMATCH")
    return fixture_id, declared


def _validate_alias(value: Any, pattern: re.Pattern[str], code: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise SnapshotError(code)
    return value


def _compile_aliases(contract: dict[str, Any]) -> dict[str, re.Pattern[str]]:
    try:
        return {
            name: re.compile(pattern)
            for name, pattern in contract["record_contract"]["alias_grammars"].items()
        }
    except (KeyError, TypeError, re.error) as exc:
        raise SnapshotError("CONTRACT_INVALID") from exc


def _validate_owner_registry(
    value: Any, contract: dict[str, Any], aliases: dict[str, re.Pattern[str]]
) -> tuple[dict[str, frozenset[str]], frozenset[str]]:
    keys = contract["capture_attestation"]["registry_keys"]["owner"]
    registry = _exact_keys(value, keys, "OWNER_REGISTRY_INVALID")
    internal = contract["record_contract"]["pinned_internal_owners"]
    if registry["internal"] != internal:
        raise SnapshotError("OWNER_REGISTRY_INVALID")
    output: dict[str, frozenset[str]] = {}
    combined: set[str] = set()
    for category in ("active", "retired", "legacy_approved"):
        values = _string_list(registry[category], code="OWNER_REGISTRY_INVALID")
        if any(not aliases["owner"].fullmatch(owner) for owner in values):
            raise SnapshotError("OWNER_REGISTRY_INVALID")
        if combined.intersection(values):
            raise SnapshotError("OWNER_REGISTRY_COLLISION")
        combined.update(values)
        output[category] = frozenset(values)
    pinned = frozenset(internal)
    if combined.intersection(pinned):
        raise SnapshotError("OWNER_REGISTRY_COLLISION")
    output["internal"] = pinned
    return output, pinned


def _validate_attestation(
    value: Any,
    fixture_id: str,
    sentinel: str,
    contract: dict[str, Any],
    aliases: dict[str, re.Pattern[str]],
    before: dict[str, Any],
) -> dict[str, Any]:
    definition = contract["capture_attestation"]
    attestation = _exact_keys(value, definition["exact_keys"], "CAPTURE_ATTESTATION_KEYS_INVALID")
    if (
        type(attestation["attestation_version"]) is not int
        or attestation["attestation_version"] != 1
        or attestation["fixture_id"] != fixture_id
        or attestation["source_classification"] != SOURCE_CLASSIFICATION
        or attestation["capture_method"] != definition["allowed_capture_method"]
        or attestation["proof_scope"] != PROOF_SCOPE
        or attestation["logical_chunk_completeness"] is not False
        or attestation["real_source_capture_authorized"] is not False
        or attestation["real_chroma_access_performed"] is not False
    ):
        raise SnapshotError("CAPTURE_ATTESTATION_INVALID")
    owners, pinned = _validate_owner_registry(attestation["owner_registry"], contract, aliases)

    workflow_keys = definition["registry_keys"]["workflow"]
    workflows: list[dict[str, Any]] = []
    workflow_ids: set[str] = set()
    if not isinstance(attestation["workflow_registry"], list):
        raise SnapshotError("WORKFLOW_REGISTRY_INVALID")
    for raw in attestation["workflow_registry"]:
        row = _exact_keys(raw, workflow_keys, "WORKFLOW_REGISTRY_INVALID")
        workflow_id = _validate_alias(row["workflow_id"], aliases["workflow"], "WORKFLOW_REGISTRY_INVALID")
        if workflow_id in workflow_ids:
            raise SnapshotError("WORKFLOW_REGISTRY_COLLISION")
        workflow_ids.add(workflow_id)
        if row["workflow_type"] not in {"call", "video"}:
            raise SnapshotError("WORKFLOW_REGISTRY_INVALID")
        owner = _validate_alias(row["owner"], aliases["owner"], "WORKFLOW_REGISTRY_INVALID")
        documents = _string_list(
            row["document_ids"],
            code="WORKFLOW_REGISTRY_INVALID",
            maximum=contract["limits"]["maximum_document_ids_per_record"],
        )
        if any(not aliases["document"].fullmatch(item) for item in documents):
            raise SnapshotError("WORKFLOW_REGISTRY_INVALID")
        source_paths = _exact_keys(
            row["source_path_tokens"],
            definition["workflow_source_path_keys"],
            "WORKFLOW_REGISTRY_INVALID",
        )
        normalized_sources: dict[str, str | None] = {}
        for kind, source_path in source_paths.items():
            normalized_sources[kind] = _validate_alias(
                source_path,
                aliases["path"],
                "WORKFLOW_REGISTRY_INVALID",
                nullable=True,
            )
        if row["workflow_type"] == "call":
            if normalized_sources["video_transcript"] is not None or normalized_sources["video_summary"] is not None:
                raise SnapshotError("WORKFLOW_REGISTRY_INVALID")
        elif normalized_sources["call_transcript"] is not None or normalized_sources["call_summary"] is not None:
            raise SnapshotError("WORKFLOW_REGISTRY_INVALID")
        upload = _validate_alias(
            row["upload_id"], aliases["upload"], "WORKFLOW_REGISTRY_INVALID", nullable=True
        )
        status = row["upload_derivation_status"]
        reason = row["upload_derivation_failure_reason"]
        if status == "SUCCESS":
            if upload is None or reason is not None:
                raise SnapshotError("WORKFLOW_REGISTRY_INVALID")
        elif status == "FAILED":
            if reason not in contract["record_contract"]["allowed_failure_reasons"]:
                raise SnapshotError("WORKFLOW_REGISTRY_INVALID")
        else:
            raise SnapshotError("WORKFLOW_REGISTRY_INVALID")
        workflows.append(
            {
                "workflow_id": workflow_id,
                "workflow_type": row["workflow_type"],
                "owner": owner,
                "source_path_tokens": normalized_sources,
                "document_ids": documents,
                "upload_id": upload,
                "upload_derivation_status": status,
                "upload_derivation_failure_reason": reason,
            }
        )

    document_keys = definition["registry_keys"]["document"]
    documents: list[dict[str, str]] = []
    document_ids: set[str] = set()
    if not isinstance(attestation["document_registry"], list):
        raise SnapshotError("DOCUMENT_REGISTRY_INVALID")
    for raw in attestation["document_registry"]:
        row = _exact_keys(raw, document_keys, "DOCUMENT_REGISTRY_INVALID")
        document_id = _validate_alias(
            row["document_id"], aliases["document"], "DOCUMENT_REGISTRY_INVALID"
        )
        if document_id in document_ids:
            raise SnapshotError("DOCUMENT_REGISTRY_COLLISION")
        document_ids.add(document_id)
        documents.append(
            {
                "document_id": document_id,
                "owner": _validate_alias(
                    row["owner"], aliases["owner"], "DOCUMENT_REGISTRY_INVALID"
                ),
            }
        )

    upload_keys = definition["registry_keys"]["upload"]
    uploads: list[dict[str, str]] = []
    upload_ids: set[str] = set()
    if not isinstance(attestation["upload_registry"], list):
        raise SnapshotError("UPLOAD_REGISTRY_INVALID")
    for raw in attestation["upload_registry"]:
        row = _exact_keys(raw, upload_keys, "UPLOAD_REGISTRY_INVALID")
        upload_id = _validate_alias(
            row["upload_id"], aliases["upload"], "UPLOAD_REGISTRY_INVALID"
        )
        if upload_id in upload_ids:
            raise SnapshotError("UPLOAD_REGISTRY_COLLISION")
        upload_ids.add(upload_id)
        uploads.append(
            {
                "upload_id": upload_id,
                "owner": _validate_alias(
                    row["owner"], aliases["owner"], "UPLOAD_REGISTRY_INVALID"
                ),
            }
        )

    attestation_core = copy.deepcopy(attestation)
    attestation_core.pop("fixture_attestation", None)
    proof_payload = (
        "marketmatch-chroma-snapshot-v1\0"
        + fixture_id
        + "\0"
        + before["files"]["collections.json"]["sha256"]
        + "\0"
        + before["files"]["records.jsonl"]["sha256"]
        + "\0"
        + hashlib.sha256(_canonical_bytes(attestation_core)).hexdigest()
        + "\0"
        + CONTRACT_SHA256
    ).encode("utf-8")
    expected_proof = hmac.new(
        sentinel.encode("ascii"), proof_payload, hashlib.sha256
    ).hexdigest()
    supplied_proof = attestation["fixture_attestation"]
    if not isinstance(supplied_proof, str) or not hmac.compare_digest(
        supplied_proof, expected_proof
    ):
        raise SnapshotError("FIXTURE_ATTESTATION_INVALID")
    return {
        "owners": owners,
        "pinned": pinned,
        "workflows": workflows,
        "documents": documents,
        "uploads": uploads,
    }


def _validate_collections(
    value: Any,
    declared: list[str],
    contract: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    layout = contract["snapshot_layout"]
    document = _exact_keys(value, layout["collections_document_keys"], "COLLECTIONS_KEYS_INVALID")
    rows = document["collections"]
    if not isinstance(rows, list) or len(rows) > contract["limits"]["maximum_collections"]:
        raise SnapshotError("COLLECTIONS_INVALID")
    allowed = contract["collection_contract"]["physical_collections"]
    exact_keys = contract["collection_contract"]["exact_record_keys"]
    output: dict[str, dict[str, Any]] = {}
    for raw in rows:
        row = _exact_keys(raw, exact_keys, "COLLECTION_RECORD_KEYS_INVALID")
        name = row["collection_name"]
        if not isinstance(name, str) or name not in allowed:
            raise SnapshotError("UNKNOWN_OR_UNSUPPORTED")
        if name in output:
            raise SnapshotError("DUPLICATE_COLLECTION")
        expected = allowed[name]
        if row["family"] != expected["family"] or row["lane"] != expected["lane"]:
            raise SnapshotError("COLLECTION_IDENTITY_MISMATCH")
        count = row["record_count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise SnapshotError("COLLECTION_COUNT_INVALID")
        for key in ("metadata_digest", "capture_pass_1_digest", "capture_pass_2_digest"):
            if not isinstance(row[key], str) or not HEX_64.fullmatch(row[key]):
                raise SnapshotError("COLLECTION_DIGEST_INVALID")
        if not hmac.compare_digest(row["capture_pass_1_digest"], row["capture_pass_2_digest"]):
            raise SnapshotError("CAPTURE_PASS_MISMATCH")
        if row["capture_status"] != CAPTURE_STATUS:
            raise SnapshotError("CAPTURE_STATUS_INVALID")
        output[name] = row
    actual = set(output)
    declared_set = set(declared)
    if declared_set - actual:
        raise SnapshotError("DECLARED_COLLECTION_MISSING")
    if actual - declared_set:
        raise SnapshotError("COLLECTION_UNDECLARED")
    families = sorted({row["family"] for row in output.values()})
    lanes = sorted({row["lane"] for row in output.values()})
    return output, families, lanes


def _populated(row: dict[str, Any], field: str) -> bool:
    value = row[field]
    if field == "document_ids":
        return bool(value)
    return value is not None


def _failure_finding(reason: str) -> str:
    return "UPLOAD_DERIVATION_" + reason


def _record_findings(
    row: dict[str, Any],
    family: str,
    attestation: dict[str, Any],
    marketmatch_kinds: frozenset[str],
) -> list[str]:
    findings: set[str] = set()
    owner = row["owner"]
    owners = attestation["owners"]
    if owner is None:
        findings.add("EMPTY_OWNER")
    elif owner in attestation["pinned"]:
        findings.add("INTERNAL_OWNER")
    elif owner in owners["retired"]:
        findings.add("RETIRED_OWNER")
    elif owner in owners["active"] or owner in owners["legacy_approved"]:
        pass
    else:
        findings.add("UNKNOWN_OWNER")

    if family != "rag" or row["kind"] not in marketmatch_kinds:
        return sorted(findings)
    if not row["document_ids"]:
        findings.add("MISSING_DOCUMENT_IDS")
    workflow_id = row["call_or_video_id"]
    if workflow_id is None:
        findings.add("MISSING_WORKFLOW_IDENTIFIER")
        if row["upload_id"] is None:
            findings.add("UPLOAD_DERIVATION_MISSING_WORKFLOW")
        return sorted(findings)
    expected_type = "call" if row["kind"].startswith("call_") else "video"
    all_with_id = [
        workflow
        for workflow in attestation["workflows"]
        if workflow["workflow_id"] == workflow_id
    ]
    matching = [
        workflow for workflow in all_with_id if workflow["workflow_type"] == expected_type
    ]
    if len(matching) != 1 or len(all_with_id) != 1:
        if all_with_id:
            findings.add("AMBIGUOUS_WORKFLOW_IDENTIFIER")
            findings.add("UPLOAD_DERIVATION_AMBIGUOUS_WORKFLOW")
        else:
            findings.add("UPLOAD_DERIVATION_MISSING_WORKFLOW")
        return sorted(findings)
    workflow = matching[0]
    owner_mismatch = workflow["owner"] != owner
    if owner_mismatch:
        findings.add("CROSS_OWNER_WORKFLOW_LINKAGE")
    source_mismatch = (
        workflow["source_path_tokens"].get(row["kind"])
        != row["source_path_token"]
    )
    document_value_mismatch = workflow["document_ids"] != row["document_ids"]
    document_conflict = not row["document_ids"] or document_value_mismatch
    if document_value_mismatch:
        findings.add("SQL_DERIVED_VALUE_DISAGREEMENT")
    for document_id in row["document_ids"]:
        linked = [
            entry
            for entry in attestation["documents"]
            if entry["document_id"] == document_id
        ]
        if len(linked) != 1:
            document_conflict = True
        if any(entry["owner"] != owner for entry in linked):
            findings.add("CROSS_OWNER_DOCUMENT_LINKAGE")
            document_conflict = True
    candidate_upload = workflow["upload_id"]
    linked_uploads = [
        entry
        for entry in attestation["uploads"]
        if entry["upload_id"] == candidate_upload
    ] if candidate_upload is not None else []
    upload_manifest_missing = candidate_upload is not None and not linked_uploads
    upload_owner_mismatch = any(entry["owner"] != owner for entry in linked_uploads)
    if workflow["upload_derivation_status"] == "FAILED":
        if row["upload_id"] is not None:
            findings.add("SQL_DERIVED_VALUE_DISAGREEMENT")
        reason = workflow["upload_derivation_failure_reason"]
        reason_is_supported = {
            "OWNER_MISMATCH": owner_mismatch,
            "SOURCE_MISMATCH": source_mismatch,
            "DOCUMENT_LINK_CONFLICT": document_conflict,
            "UPLOAD_MISSING": candidate_upload is None,
            "UPLOAD_MANIFEST_MISSING": upload_manifest_missing,
            "UPLOAD_OWNER_MISMATCH": upload_owner_mismatch,
        }.get(reason, False)
        if not reason_is_supported:
            raise SnapshotError("UPLOAD_DERIVATION_REASON_INVALID")
        if upload_owner_mismatch:
            findings.add("CROSS_OWNER_UPLOAD_LINKAGE")
        findings.add(_failure_finding(reason))
        return sorted(findings)
    if source_mismatch or document_conflict:
        findings.add("SQL_DERIVED_VALUE_DISAGREEMENT")
    if row["upload_id"] != candidate_upload:
        findings.add("SQL_DERIVED_VALUE_DISAGREEMENT")
    if upload_manifest_missing:
        findings.add("UPLOAD_DERIVATION_UPLOAD_MANIFEST_MISSING")
    elif len(linked_uploads) != 1:
        findings.add("SQL_DERIVED_VALUE_DISAGREEMENT")
    if upload_owner_mismatch:
        findings.add("CROSS_OWNER_UPLOAD_LINKAGE")
    return sorted(findings)


def _validate_records(
    payload: bytes,
    collections: dict[str, dict[str, Any]],
    contract: dict[str, Any],
    aliases: dict[str, re.Pattern[str]],
    attestation: dict[str, Any],
) -> tuple[int, int, list[str]]:
    limits = contract["limits"]
    if payload and not payload.endswith(b"\n"):
        raise SnapshotError("RECORDS_TERMINAL_NEWLINE_MISSING")
    lines = payload.splitlines()
    if len(lines) > limits["maximum_records"]:
        raise SnapshotError("RECORD_LIMIT_EXCEEDED")
    record_contract = contract["record_contract"]
    exact_keys = record_contract["exact_keys"]
    allowed_native = frozenset(record_contract["allowed_native_fields"])
    allowed_sql = frozenset(record_contract["allowed_sql_derived_fields"])
    provenance_fields = frozenset(record_contract["provenance_fields"])
    allowed_findings = frozenset(record_contract["allowed_findings"])
    marketmatch_kinds = frozenset(record_contract["allowed_kinds"]["marketmatch"])
    non_marketmatch_kinds = frozenset(
        record_contract["allowed_kinds"]["non_marketmatch"]
    )
    pinned = frozenset(record_contract["pinned_internal_owners"])
    counts = {name: 0 for name in collections}
    aliases_seen: dict[str, str] = {}
    all_findings: set[str] = set()
    marketmatch_count = 0
    for line in lines:
        if not line or len(line) > limits["record_line_bytes"]:
            raise SnapshotError("RECORD_LINE_INVALID")
        row = _load_json_bytes(line, "RECORD_JSON_INVALID")
        row = _exact_keys(row, exact_keys, "RECORD_KEYS_INVALID")
        collection_name = row["collection_name"]
        if collection_name not in collections:
            raise SnapshotError("RECORD_COLLECTION_UNDECLARED")
        raw_alias = row["raw_record_id_alias"]
        if not isinstance(raw_alias, str):
            raise SnapshotError("RAW_RECORD_ALIAS_INVALID")
        previous = aliases_seen.get(raw_alias)
        if previous is not None:
            if previous == collection_name:
                raise SnapshotError("DUPLICATE_RECORD_ALIAS")
            raise SnapshotError("ALIAS_REUSED_ACROSS_COLLECTIONS")
        match = aliases["raw_record"].fullmatch(raw_alias)
        if not match:
            raise SnapshotError("RAW_RECORD_ALIAS_INVALID")
        identity = collections[collection_name]
        if (match.group(1), match.group(2)) != (identity["family"], identity["lane"]):
            raise SnapshotError("RAW_RECORD_ALIAS_LANE_MISMATCH")
        aliases_seen[raw_alias] = collection_name

        owner = row["owner"]
        if owner is not None and owner not in pinned and (
            not isinstance(owner, str) or not aliases["owner"].fullmatch(owner)
        ):
            raise SnapshotError("OWNER_VALUE_INVALID")
        _validate_alias(
            row["source_path_token"], aliases["path"], "SOURCE_PATH_TOKEN_INVALID", nullable=True
        )
        _validate_alias(
            row["call_or_video_id"], aliases["workflow"], "WORKFLOW_ID_INVALID", nullable=True
        )
        documents = _string_list(
            row["document_ids"],
            code="DOCUMENT_IDS_INVALID",
            maximum=limits["maximum_document_ids_per_record"],
        )
        if any(not aliases["document"].fullmatch(item) for item in documents):
            raise SnapshotError("DOCUMENT_IDS_INVALID")
        _validate_alias(row["upload_id"], aliases["upload"], "UPLOAD_ID_INVALID", nullable=True)
        chunk_id = row["chunk_id"]
        if chunk_id is not None and (
            isinstance(chunk_id, bool) or not isinstance(chunk_id, int) or chunk_id < 0
        ):
            raise SnapshotError("CHUNK_ORDINAL_INVALID")
        kind = row["kind"]
        family = identity["family"]
        if family == "rag":
            allowed_kinds = marketmatch_kinds | {"non_marketmatch_rag"}
        elif family == "memory":
            allowed_kinds = {"memory"}
        else:
            allowed_kinds = {"tool_index"}
        if kind not in allowed_kinds:
            raise SnapshotError("UNSUPPORTED_KIND")

        native = _string_list(row["native_fields"], code="NATIVE_FIELD_INVALID")
        sql_derived = _string_list(
            row["sql_derived_fields"], code="SQL_DERIVED_FIELD_INVALID"
        )
        operator = row["operator_supplied_fields"]
        if not isinstance(operator, list) or any(
            not isinstance(field, str) for field in operator
        ):
            raise SnapshotError("OPERATOR_SUPPLIED_FIELDS_REJECTED")
        if operator != []:
            raise SnapshotError("OPERATOR_SUPPLIED_FIELDS_REJECTED")
        is_marketmatch = family == "rag" and kind in marketmatch_kinds
        if not is_marketmatch and (
            sql_derived
            or row["call_or_video_id"] is not None
            or row["document_ids"]
            or row["upload_id"] is not None
        ):
            raise SnapshotError("NON_MARKETMATCH_LINKAGE_INVALID")
        categories = [set(native), set(sql_derived), set(operator)]
        if any(categories[i].intersection(categories[j]) for i in range(3) for j in range(i + 1, 3)):
            raise SnapshotError("PROVENANCE_OVERLAP")
        if "upload_id" in native:
            raise SnapshotError("UPLOAD_ID_MARKED_NATIVE")
        if not set(native).issubset(allowed_native):
            raise SnapshotError("NATIVE_FIELD_INVALID")
        if not set(sql_derived).issubset(allowed_sql):
            raise SnapshotError("SQL_DERIVED_FIELD_INVALID")
        claimed = set().union(*categories)
        populated = {field for field in provenance_fields if _populated(row, field)}
        if populated - claimed:
            raise SnapshotError("PROVENANCE_MISSING")
        if claimed - populated:
            raise SnapshotError("PROVENANCE_WITHOUT_VALUE")
        supplied_findings = _string_list(
            row["validation_findings"],
            code="VALIDATION_FINDINGS_INVALID",
            allowed=allowed_findings,
        )
        computed = _record_findings(
            row, family, attestation, marketmatch_kinds
        )
        relational_failures = {
            finding
            for finding in computed
            if finding.startswith("UPLOAD_DERIVATION_")
            or finding.startswith("CROSS_OWNER_")
            or finding
            in {
                "AMBIGUOUS_WORKFLOW_IDENTIFIER",
                "MISSING_DOCUMENT_IDS",
                "MISSING_WORKFLOW_IDENTIFIER",
                "SQL_DERIVED_VALUE_DISAGREEMENT",
            }
        }
        if "upload_id" in sql_derived and relational_failures:
            raise SnapshotError("STRICT_SQL_DERIVATION_INVALID")
        if "upload_id" in sql_derived and (
            not is_marketmatch
            or row["owner"] is None
            or row["owner"] in pinned
            or row["source_path_token"] is None
            or row["call_or_video_id"] is None
            or not row["document_ids"]
            or row["chunk_id"] is None
        ):
            raise SnapshotError("STRICT_SQL_DERIVATION_INVALID")
        if supplied_findings != computed:
            raise SnapshotError("VALIDATION_FINDINGS_MISMATCH")
        all_findings.update(computed)
        if kind in marketmatch_kinds:
            marketmatch_count += 1
        counts[collection_name] += 1

    for name, declared in collections.items():
        if counts[name] != declared["record_count"]:
            raise SnapshotError("RECORD_COUNT_MISMATCH")
    return len(lines), marketmatch_count, sorted(all_findings)


def _validate_snapshot_contents(
    directory: SnapshotDirectory,
    before: dict[str, Any],
    sentinel: str,
    contract: dict[str, Any],
) -> dict[str, Any]:
    limits = contract["limits"]
    manifest = _load_json_bytes(
        directory.read("snapshot-manifest.json", before, limits["manifest_bytes"]),
        "MANIFEST_JSON_INVALID",
    )
    fixture_id, declared = _validate_manifest(manifest, contract, before)
    aliases = _compile_aliases(contract)
    collections_document = _load_json_bytes(
        directory.read("collections.json", before, limits["collections_bytes"]),
        "COLLECTIONS_JSON_INVALID",
    )
    collections, families, lanes = _validate_collections(
        collections_document, declared, contract
    )
    attestation_document = _load_json_bytes(
        directory.read(
            "capture-attestation.json", before, limits["capture_attestation_bytes"]
        ),
        "CAPTURE_ATTESTATION_JSON_INVALID",
    )
    attestation = _validate_attestation(
        attestation_document, fixture_id, sentinel, contract, aliases, before
    )
    records_payload = directory.read(
        "records.jsonl", before, limits["records_bytes"]
    )
    record_count, marketmatch_count, findings = _validate_records(
        records_payload, collections, contract, aliases, attestation
    )
    report = {
        **SAFE_REPORT_BASE,
        "status": (
            "VALID_SYNTHETIC_SNAPSHOT_WITH_FINDINGS"
            if findings
            else "VALID_SYNTHETIC_SNAPSHOT"
        ),
        "valid": True,
        "source_classification": SOURCE_CLASSIFICATION,
        "contract_sha256": CONTRACT_SHA256,
        "collection_count": len(collections),
        "empty_collection_count": sum(
            1 for value in collections.values() if value["record_count"] == 0
        ),
        "record_count": record_count,
        "marketmatch_record_count": marketmatch_count,
        "families": families,
        "lanes": lanes,
        "findings": findings,
    }
    return report


def validate_fixture_snapshot(snapshot: str, *, fixture_only: bool) -> dict[str, Any]:
    if not fixture_only:
        raise SnapshotError("FIXTURE_ONLY_FLAG_REQUIRED")
    contract = _load_contract()
    path, sentinel = _approved_snapshot_path(snapshot)
    directory = SnapshotDirectory(path)
    before: dict[str, Any] | None = None
    caught: BaseException | None = None
    result: dict[str, Any] | None = None
    try:
        before = directory.inventory()
        result = _validate_snapshot_contents(directory, before, sentinel, contract)
    except BaseException as exc:
        caught = exc
    try:
        after = directory.inventory()
        if before is not None and before != after:
            caught = SnapshotError("SOURCE_CHANGED_DURING_VALIDATION")
        if not directory.namespace_matches():
            caught = SnapshotError("SNAPSHOT_REPLACED")
    except BaseException as exc:
        caught = exc
    finally:
        directory.close()
    if caught is not None:
        if isinstance(caught, SnapshotError):
            raise caught
        raise SnapshotError("INTERNAL_VALIDATION_ERROR") from caught
    if result is None:
        raise SnapshotError("INTERNAL_VALIDATION_ERROR")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("--fixture-only", action="store_true")
    parser.add_argument("--snapshot", required=True)
    return parser


def _error_report(code: str) -> dict[str, Any]:
    return {
        **SAFE_REPORT_BASE,
        "status": "ERROR",
        "valid": False,
        "code": code,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    try:
        arguments = build_parser().parse_args(argv)
        report = validate_fixture_snapshot(
            arguments.snapshot, fixture_only=arguments.fixture_only
        )
    except SnapshotError as exc:
        print(json.dumps(_error_report(exc.code), sort_keys=True), file=stderr)
        return 1
    except Exception:
        print(
            json.dumps(_error_report("INTERNAL_VALIDATION_ERROR"), sort_keys=True),
            file=stderr,
        )
        return 1
    print(json.dumps(report, sort_keys=True), file=stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
