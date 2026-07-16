#!/usr/bin/env python3
"""Validate a sealed synthetic Chroma provenance-contract fixture.

The validator is permanently fixture-only. It performs no package discovery,
network access, application imports, database access, Docker inspection, or
real-source capture. Version 1 has an empty compatibility allowlist, so every
valid fixture remains ineligible for real capture.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Sequence, TextIO


SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parent
CONTRACT_PATH = (
    SCRIPT_DIR
    / "offline_package_contracts"
    / "chroma_version_deployment_provenance_v1.json"
)
CONTRACT_SHA256 = "84e7ca2e7fffc797b929872a173281514e89e5bbc6cc9ae965550872fec1044b"
TEST_LAUNCHER_SHA256 = "28ee566dce4acfc5795abaafe01b816231e69d53d07325fbea1023f6521c64b3"

SOURCE_CLASSIFICATION = "SYNTHETIC_DISPOSABLE_FIXTURE"
EXPECTED_FILES = frozenset(
    {
        "provenance-manifest.json",
        "compatibility-subject.json",
        "operator-attestation.json",
        "reviewer-attestation.json",
    }
)
HASHED_FILES = frozenset(
    {
        "compatibility-subject.json",
        "operator-attestation.json",
        "reviewer-attestation.json",
    }
)
PROVENANCE_GROUPS = (
    "contract",
    "source_code",
    "client",
    "server",
    "connection_identity",
    "persistence",
    "collections",
    "quiescence",
    "privacy",
    "adapter",
    "review",
    "decision",
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
    "TMPDIR",
    "TMP",
    "TEMP",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_RUNTIME_DIR",
    "XDG_STATE_HOME",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "DYLD_FALLBACK_LIBRARY_PATH",
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "COMPOSE_FILE",
    "COMPOSE_PROJECT_NAME",
    "CONTAINER_HOST",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_CONFIG_FILE",
    "AWS_SHARED_CREDENTIALS_FILE",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "CLOUDSDK_CONFIG",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_TENANT_ID",
    "AZURE_SUBSCRIPTION_ID",
    "KUBECONFIG",
    "AUTH_ENABLED",
    "LOCALHOST_BYPASS",
    "ODYSSEUS_ADMIN_USER",
    "ODYSSEUS_ADMIN_PASSWORD",
    "PYTHONPATH",
    "PYTHONHOME",
    "VIRTUAL_ENV",
)
GATE_ENVIRONMENT = frozenset(
    {
        "MARKETMATCH_CHROMA_PROVENANCE_TEST_ROOT",
        "MARKETMATCH_CHROMA_PROVENANCE_SENTINEL",
        "MARKETMATCH_CHROMA_PROVENANCE_SENTINEL_FD",
        "MARKETMATCH_CHROMA_PROVENANCE_LAUNCHER_FD",
        "MARKETMATCH_CHROMA_PROVENANCE_LAUNCHER_PID",
        "MARKETMATCH_CHROMA_PROVENANCE_TEMP_BOUNDARY",
    }
)
SAFE_REPORT_BASE = {
    "real_capture_authorized": False,
    "remote_capture_supported": False,
    "supported_combination_count": 0,
    "compatibility_allowlist_empty": True,
    "network_access_performed": False,
    "runtime_discovery_performed": False,
    "real_environment_inspected": False,
    "real_chroma_access_performed": False,
    "application_imports_performed": False,
}
EXPECTED_STATUS_CODES = frozenset(
    {
        "SUPPORTED_EXACT",
        "SUPPORTED_REVIEWED_VARIANT",
        "CLIENT_SERVER_MISMATCH",
        "UNPINNED_CLIENT",
        "UNPINNED_SERVER",
        "UNKNOWN_SERVER_BUILD",
        "UNKNOWN_TENANT_DATABASE",
        "UNSUPPORTED_DEPLOYMENT_MODE",
        "PERSISTENCE_PROVENANCE_MISSING",
        "REMOTE_CAPTURE_UNSUPPORTED",
        "UNKNOWN_OR_UNSUPPORTED",
        "INVALID_ATTESTATION",
        "PRIVACY_POLICY_VIOLATION",
        "READ_ERROR",
    }
)
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
FIXTURE_ID = re.compile(r"^fixture_[0-9a-f]{32}$")
LAUNCHER_ROOT_NAME = re.compile(r"^marketmatch-chroma-provenance\.[A-Za-z0-9]{6}$")
TIMESTAMP_SHAPE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
POSIX_ABSOLUTE = re.compile(r"^/")
WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
UNC_PATH = re.compile(r"^(?:\\\\|//)[^/\\]")
URL_VALUE = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]*://|\bwww\.)")
EMAIL_VALUE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
IPV4_VALUE = re.compile(
    r"(?<![0-9])(?:25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})"
    r"(?:\.(?:25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})){3}(?![0-9])"
)
HOSTNAME_VALUE = re.compile(
    r"(?i)\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,63}\b"
)
DOCKER_ID_VALUE = re.compile(r"^[0-9a-f]{12,64}$")
CREDENTIAL_VALUE = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:bearer\s+|sk-[a-z0-9_-]+|"
    r"gh[pousr]_[a-z0-9]{20,}|akia[0-9a-z]{16}|aiza[0-9a-z_-]{30,}|"
    r"token\s*[=:]|api[_-]?key\s*[=:]|password\s*[=:]|secret\s*[=:])"
)
CLOUD_VALUE = re.compile(
    r"(?i)(?:^arn:|^s3://|^gs://|^azure://|/subscriptions/|"
    r"/resourcegroups/|^projects/[a-z0-9_-]+)"
)
SAFE_HASH_KEYS = frozenset(
    {
        "artifact_sha256",
        "installed_tree_or_record_sha256",
        "dependency_lock_sha256",
        "image_digest",
        "platform_digest",
        "binary_or_package_sha256",
        "complete_tree_digest",
        "inventory_before_sha256",
        "inventory_after_sha256",
        "sanitized_metadata_digest",
        "evidence_sha256",
        "policy_sha256",
        "adapter_contract_sha256",
        "contract_sha256",
        "contract_group_sha256",
        "source_code_sha256",
        "client_sha256",
        "server_sha256",
        "connection_identity_sha256",
        "persistence_sha256",
        "collections_sha256",
        "quiescence_sha256",
        "privacy_sha256",
        "adapter_sha256",
        "review_sha256",
        "decision_sha256",
        "subject_sha256",
        "canonical_sha256",
        "sha256",
        "tree_sha256",
        "launcher_attestation",
    }
)
SAFE_COMMIT_KEYS = frozenset(
    {
        "upstream_commit",
        "marketmatch_checkpoint",
        "integration_baseline",
        "repository_commit",
        "application_deployment_commit",
        "capture_adapter_commit",
        "adapter_commit",
    }
)
EXPECTED_DECISION_PRECEDENCE = (
    "PRIVACY_POLICY_VIOLATION",
    "READ_ERROR",
    "INVALID_ATTESTATION",
    "REMOTE_CAPTURE_UNSUPPORTED",
    "UNSUPPORTED_DEPLOYMENT_MODE",
    "UNPINNED_CLIENT",
    "UNPINNED_SERVER",
    "UNKNOWN_SERVER_BUILD",
    "UNKNOWN_TENANT_DATABASE",
    "PERSISTENCE_PROVENANCE_MISSING",
    "CLIENT_SERVER_MISMATCH",
    "UNKNOWN_OR_UNSUPPORTED",
    "SUPPORTED_REVIEWED_VARIANT",
    "SUPPORTED_EXACT",
)


class ContractError(RuntimeError):
    """A fail-closed error whose fixed code is safe to report."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise ContractError("READ_ERROR")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _canonical_digest(value: dict[str, Any]) -> str:
    payload = copy.deepcopy(value)
    payload.pop("canonical_sha256", None)
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _value_digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _tree_digest(entries: list[dict[str, Any]]) -> str:
    ordered = sorted(entries, key=lambda item: item["path"])
    return _value_digest(ordered)


def _read_fd(descriptor: int, size: int) -> bytes:
    if size < 0:
        raise ContractError("READ_ERROR")
    chunks: list[bytes] = []
    offset = 0
    while offset < size:
        try:
            chunk = os.pread(descriptor, min(1024 * 1024, size - offset), offset)
        except OSError as exc:
            raise ContractError("READ_ERROR") from exc
        if not chunk:
            raise ContractError("READ_ERROR")
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
            raise ContractError("READ_ERROR") from exc
        if not chunk:
            raise ContractError("READ_ERROR")
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
        raise ContractError("READ_ERROR") from exc
    current = boundary
    for part in relative.parts:
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise ContractError("READ_ERROR") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise ContractError("READ_ERROR")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ContractError("READ_ERROR")
        value[key] = child
    return value


def _reject_non_rfc8259_number(value: str) -> Any:
    del value
    raise ContractError("READ_ERROR")


def _load_json_bytes(payload: bytes) -> Any:
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_non_rfc8259_number,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("READ_ERROR") from exc


def _strict_object(value: Any, expected: Sequence[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ContractError(code)
    return value


def _closed_group(value: Any, expected: Sequence[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("READ_ERROR")
    if set(value) - set(expected):
        raise ContractError("READ_ERROR")
    return value


def _valid_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(set(value)) > 1
        and bool(HEX_64.fullmatch(value))
    )


def _valid_commit(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(set(value)) > 1
        and bool(HEX_40.fullmatch(value))
    )


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not TIMESTAMP_SHAPE.fullmatch(value):
        return False
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return parsed.replace(tzinfo=timezone.utc).isoformat().endswith("+00:00")


def _is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip("[]"))
    except ValueError:
        return False
    return True


def _privacy_scan(value: Any, prohibited_keys: frozenset[str], key: str = "") -> None:
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            if not isinstance(child_key, str):
                raise ContractError("PRIVACY_POLICY_VIOLATION")
            if child_key.lower() in prohibited_keys:
                if (
                    child_key == "path"
                    and isinstance(child_value, str)
                    and child_value in HASHED_FILES
                ):
                    continue
                raise ContractError("PRIVACY_POLICY_VIOLATION")
            _privacy_scan(child_value, prohibited_keys, child_key)
        return
    if isinstance(value, list):
        for item in value:
            _privacy_scan(item, prohibited_keys, key)
        return
    if not isinstance(value, str) or not value:
        return
    if key in SAFE_HASH_KEYS or key in SAFE_COMMIT_KEYS:
        return
    if key in {"exact_version", "timestamp", "acquisition_window_start", "acquisition_window_end"}:
        return
    if value.startswith(("synthetic_tenant_", "synthetic_database_", "synthetic_host_", "synthetic_store_", "synthetic_snapshot_", "synthetic_operator_", "synthetic_reviewer_")):
        return
    if (
        POSIX_ABSOLUTE.search(value)
        or WINDOWS_DRIVE.search(value)
        or UNC_PATH.search(value)
        or URL_VALUE.search(value)
        or EMAIL_VALUE.search(value)
        or IPV4_VALUE.search(value)
        or _is_ip_address(value)
        or HOSTNAME_VALUE.search(value)
        or DOCKER_ID_VALUE.fullmatch(value)
        or CREDENTIAL_VALUE.search(value)
        or CLOUD_VALUE.search(value)
        or value.lower() in {"localhost", "host.docker.internal"}
    ):
        raise ContractError("PRIVACY_POLICY_VIOLATION")


def _load_contract() -> dict[str, Any]:
    try:
        raw = CONTRACT_PATH.read_bytes()
        contract = _load_json_bytes(raw)
    except (OSError, ContractError) as exc:
        raise ContractError("READ_ERROR") from exc
    if not isinstance(contract, dict):
        raise ContractError("READ_ERROR")
    if contract.get("canonical_sha256") != CONTRACT_SHA256:
        raise ContractError("READ_ERROR")
    if not hmac.compare_digest(_canonical_digest(contract), CONTRACT_SHA256):
        raise ContractError("READ_ERROR")
    if (
        contract.get("contract_name")
        != "marketmatch-synthetic-chroma-version-deployment-provenance-v1"
        or type(contract.get("contract_version")) is not int
        or contract["contract_version"] != 1
    ):
        raise ContractError("READ_ERROR")
    commits = contract.get("authoritative_commits")
    if commits != {
        "upstream": "9844a2f9a1996b8c8135a9e7bbde6a72f41df5ed",
        "marketmatch_checkpoint": "6197984ccec995b630006253cf6eb62c43908d5f",
        "current_integration_baseline": "966ffcb30765755e3378a63f60f201f940a87daf",
    }:
        raise ContractError("READ_ERROR")
    invariants = contract.get("invariants")
    if invariants != {
        "application_imports_permitted": False,
        "compatibility_allowlist_empty": True,
        "network_access_permitted": False,
        "real_capture_authorized": False,
        "remote_capture_supported": False,
        "runtime_discovery_permitted": False,
        "supported_combination_count": 0,
        "unknown_defaults_permitted": False,
    }:
        raise ContractError("READ_ERROR")
    if contract.get("supported_combinations") != []:
        raise ContractError("READ_ERROR")
    if frozenset(contract.get("status_codes", [])) != EXPECTED_STATUS_CODES:
        raise ContractError("READ_ERROR")
    if tuple(contract.get("decision_precedence", [])) != EXPECTED_DECISION_PRECEDENCE:
        raise ContractError("READ_ERROR")
    gate = contract.get("fixture_gate")
    if not isinstance(gate, dict) or gate.get("required_launcher_sha256") != TEST_LAUNCHER_SHA256:
        raise ContractError("READ_ERROR")
    manifest = contract.get("manifest_contract")
    if not isinstance(manifest, dict) or frozenset(manifest.get("allowed_files", [])) != EXPECTED_FILES:
        raise ContractError("READ_ERROR")
    if tuple(manifest.get("required_provenance_groups", [])) != PROVENANCE_GROUPS:
        raise ContractError("READ_ERROR")
    return contract


def _fixture_root() -> tuple[Path, str]:
    for variable in BLOCKED_ENVIRONMENT:
        if variable in os.environ:
            raise ContractError("READ_ERROR")
    raw_root = os.environ.get("MARKETMATCH_CHROMA_PROVENANCE_TEST_ROOT")
    sentinel = os.environ.get("MARKETMATCH_CHROMA_PROVENANCE_SENTINEL")
    sentinel_fd_text = os.environ.get("MARKETMATCH_CHROMA_PROVENANCE_SENTINEL_FD")
    launcher_fd_text = os.environ.get("MARKETMATCH_CHROMA_PROVENANCE_LAUNCHER_FD")
    launcher_pid_text = os.environ.get("MARKETMATCH_CHROMA_PROVENANCE_LAUNCHER_PID")
    raw_boundary = os.environ.get("MARKETMATCH_CHROMA_PROVENANCE_TEMP_BOUNDARY")
    if (
        not raw_root
        or not raw_boundary
        or not sentinel
        or not HEX_64.fullmatch(sentinel)
    ):
        raise ContractError("READ_ERROR")
    try:
        sentinel_fd = int(sentinel_fd_text or "", 10)
        launcher_fd = int(launcher_fd_text or "", 10)
        launcher_pid = int(launcher_pid_text or "", 10)
    except ValueError as exc:
        raise ContractError("READ_ERROR") from exc
    if sentinel_fd < 3 or launcher_fd < 3 or launcher_pid != os.getppid():
        raise ContractError("READ_ERROR")
    try:
        sentinel_flags = fcntl.fcntl(sentinel_fd, fcntl.F_GETFL)
        sentinel_metadata = os.fstat(sentinel_fd)
        sentinel_value = _read_fd(sentinel_fd, sentinel_metadata.st_size).decode("ascii").strip()
    except (OSError, UnicodeError, ContractError) as exc:
        raise ContractError("READ_ERROR") from exc
    if (sentinel_flags & os.O_ACCMODE) != os.O_RDONLY:
        raise ContractError("READ_ERROR")
    if not stat.S_ISREG(sentinel_metadata.st_mode) or sentinel_metadata.st_nlink != 1:
        raise ContractError("READ_ERROR")
    if not hmac.compare_digest(sentinel_value, sentinel):
        raise ContractError("READ_ERROR")

    expected_launcher = REPOSITORY_ROOT / "tests" / "run_offline_chroma_provenance_tests.sh"
    try:
        launcher_flags = fcntl.fcntl(launcher_fd, fcntl.F_GETFL)
        launcher_metadata = os.fstat(launcher_fd)
        expected_metadata = expected_launcher.lstat()
        launcher_digest = _sha256_fd(launcher_fd, launcher_metadata.st_size)
    except (OSError, ContractError) as exc:
        raise ContractError("READ_ERROR") from exc
    if (
        (launcher_flags & os.O_ACCMODE) != os.O_RDONLY
        or not stat.S_ISREG(launcher_metadata.st_mode)
        or launcher_metadata.st_nlink != 1
        or (launcher_metadata.st_dev, launcher_metadata.st_ino)
        != (expected_metadata.st_dev, expected_metadata.st_ino)
        or not hmac.compare_digest(launcher_digest, TEST_LAUNCHER_SHA256)
    ):
        raise ContractError("READ_ERROR")

    root_input = Path(raw_root)
    boundary_input = Path(raw_boundary)
    if (
        not root_input.is_absolute()
        or not boundary_input.is_absolute()
        or any(part in {".", ".."} for part in root_input.parts)
        or any(part in {".", ".."} for part in boundary_input.parts)
        or not LAUNCHER_ROOT_NAME.fullmatch(root_input.name)
    ):
        raise ContractError("READ_ERROR")
    try:
        _ensure_no_symlink_components(root_input, Path(root_input.anchor))
        _ensure_no_symlink_components(boundary_input, Path(boundary_input.anchor))
        root = root_input.resolve(strict=True)
        boundary = boundary_input.resolve(strict=True)
        repository = REPOSITORY_ROOT.resolve(strict=True)
    except OSError as exc:
        raise ContractError("READ_ERROR") from exc
    if root.parent != boundary:
        raise ContractError("READ_ERROR")
    if _is_relative_to(root, repository):
        raise ContractError("READ_ERROR")
    sentinel_path = root / ".launcher-chroma-provenance-sentinel"
    _ensure_no_symlink_components(sentinel_path, root)
    try:
        sentinel_path_metadata = sentinel_path.lstat()
    except OSError as exc:
        raise ContractError("READ_ERROR") from exc
    if (
        not stat.S_ISREG(sentinel_path_metadata.st_mode)
        or sentinel_path_metadata.st_nlink != 1
        or (sentinel_path_metadata.st_dev, sentinel_path_metadata.st_ino)
        != (sentinel_metadata.st_dev, sentinel_metadata.st_ino)
    ):
        raise ContractError("READ_ERROR")
    return root, sentinel


def _approved_fixture_path(value: str) -> tuple[Path, str]:
    root, sentinel = _fixture_root()
    candidate = Path(value)
    if candidate.is_absolute() or candidate.parts[:1] != ("fixtures",):
        raise ContractError("READ_ERROR")
    if len(candidate.parts) != 2 or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ContractError("READ_ERROR")
    path = root / candidate
    _ensure_no_symlink_components(path, root)
    try:
        resolved = path.resolve(strict=True)
        fixtures_root = (root / "fixtures").resolve(strict=True)
    except OSError as exc:
        raise ContractError("READ_ERROR") from exc
    if not _is_relative_to(resolved, fixtures_root) or resolved == fixtures_root:
        raise ContractError("READ_ERROR")
    return resolved, sentinel


class FixtureDirectory:
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
            raise ContractError("READ_ERROR") from exc
        if not stat.S_ISDIR(opened_metadata.st_mode) or (
            namespace_metadata.st_dev,
            namespace_metadata.st_ino,
        ) != (opened_metadata.st_dev, opened_metadata.st_ino):
            os.close(descriptor)
            raise ContractError("READ_ERROR")
        self.descriptor = descriptor
        self.identity = (opened_metadata.st_dev, opened_metadata.st_ino)
        if stat.S_IMODE(opened_metadata.st_mode) & 0o222:
            os.close(descriptor)
            raise ContractError("READ_ERROR")

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
            raise ContractError("READ_ERROR")
        try:
            names = sorted(entry.name for entry in os.scandir(self.descriptor))
        except OSError as exc:
            raise ContractError("READ_ERROR") from exc
        if set(names) != EXPECTED_FILES:
            raise ContractError("READ_ERROR")
        files: dict[str, Any] = {}
        for name in names:
            try:
                metadata = os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)
            except OSError as exc:
                raise ContractError("READ_ERROR") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise ContractError("READ_ERROR")
            if not stat.S_ISREG(metadata.st_mode):
                raise ContractError("READ_ERROR")
            if metadata.st_nlink != 1:
                raise ContractError("READ_ERROR")
            if stat.S_IMODE(metadata.st_mode) & 0o222:
                raise ContractError("READ_ERROR")
            file_descriptor = -1
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
                    raise ContractError("READ_ERROR")
                digest = _sha256_fd(file_descriptor, opened.st_size)
            except OSError as exc:
                raise ContractError("READ_ERROR") from exc
            finally:
                if file_descriptor >= 0:
                    os.close(file_descriptor)
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
            raise ContractError("READ_ERROR")
        descriptor = -1
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
                raise ContractError("READ_ERROR")
            payload = _read_fd(descriptor, metadata.st_size)
            if not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), expected["sha256"]):
                raise ContractError("READ_ERROR")
            return payload
        except OSError as exc:
            raise ContractError("READ_ERROR") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)


def _validate_manifest_envelope(
    manifest: Any,
    contract: dict[str, Any],
    before: dict[str, Any],
    sentinel: str,
) -> str:
    definition = contract["manifest_contract"]
    value = _strict_object(manifest, definition["exact_keys"], "READ_ERROR")
    if (
        type(value["manifest_version"]) is not int
        or value["manifest_version"] != 1
        or value["contract_name"] != contract["contract_name"]
        or value["contract_sha256"] != CONTRACT_SHA256
        or value["fixture_provenance"] != SOURCE_CLASSIFICATION
        or value["sealed"] is not True
    ):
        raise ContractError("INVALID_ATTESTATION")
    fixture_id = value["fixture_id"]
    if not isinstance(fixture_id, str) or not FIXTURE_ID.fullmatch(fixture_id):
        raise ContractError("INVALID_ATTESTATION")
    files = value["files"]
    if not isinstance(files, list) or len(files) != len(HASHED_FILES):
        raise ContractError("READ_ERROR")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in files:
        row = _strict_object(raw, definition["file_record_keys"], "READ_ERROR")
        name = row["path"]
        if name not in HASHED_FILES or name in seen:
            raise ContractError("READ_ERROR")
        seen.add(name)
        if (
            isinstance(row["size"], bool)
            or not isinstance(row["size"], int)
            or row["size"] < 0
            or not _valid_digest(row["sha256"])
        ):
            raise ContractError("READ_ERROR")
        actual = before["files"][name]
        if row["size"] != actual["size"] or not hmac.compare_digest(
            row["sha256"], actual["sha256"]
        ):
            raise ContractError("READ_ERROR")
        entries.append(row)
    if seen != HASHED_FILES:
        raise ContractError("READ_ERROR")
    tree_sha256 = _tree_digest(entries)
    if not hmac.compare_digest(value["tree_sha256"], tree_sha256):
        raise ContractError("READ_ERROR")
    if not _valid_digest(value["canonical_sha256"]) or not hmac.compare_digest(
        value["canonical_sha256"], _canonical_digest(value)
    ):
        raise ContractError("READ_ERROR")
    groups = {name: value[name] for name in PROVENANCE_GROUPS}
    proof_payload = (
        "marketmatch-chroma-provenance-v1\0"
        + fixture_id
        + "\0"
        + CONTRACT_SHA256
        + "\0"
        + tree_sha256
        + "\0"
        + before["files"]["compatibility-subject.json"]["sha256"]
        + "\0"
        + _value_digest(groups)
    ).encode("utf-8")
    expected_proof = hmac.new(
        sentinel.encode("ascii"), proof_payload, hashlib.sha256
    ).hexdigest()
    supplied_proof = value["launcher_attestation"]
    if not isinstance(supplied_proof, str) or not hmac.compare_digest(
        supplied_proof, expected_proof
    ):
        raise ContractError("INVALID_ATTESTATION")
    return fixture_id


def _validate_contract_group(group: Any, contract: dict[str, Any]) -> bool:
    row = _closed_group(group, contract["contract_group_keys"])
    commits = contract["authoritative_commits"]
    expected = {
        "contract_name": contract["contract_name"],
        "contract_version": 1,
        "contract_sha256": CONTRACT_SHA256,
        "upstream_commit": commits["upstream"],
        "marketmatch_checkpoint": commits["marketmatch_checkpoint"],
        "integration_baseline": commits["current_integration_baseline"],
        "supported_combination_count": 0,
        "compatibility_allowlist_empty": True,
        "real_capture_authorized": False,
        "remote_capture_supported": False,
        "unknown_defaults_permitted": False,
    }
    if (
        type(row.get("contract_version")) is not int
        or type(row.get("supported_combination_count")) is not int
        or row != expected
    ):
        raise ContractError("INVALID_ATTESTATION")
    return False


def _validate_source_code(group: Any, contract: dict[str, Any]) -> bool:
    row = _closed_group(group, contract["group_contracts"]["source_code"])
    invalid = False
    for key in ("repository_commit", "application_deployment_commit", "capture_adapter_commit"):
        if not _valid_commit(row.get(key)):
            invalid = True
    if row.get("source_classification") != SOURCE_CLASSIFICATION:
        raise ContractError("INVALID_ATTESTATION")
    if row.get("repository_commit") != contract["authoritative_commits"]["current_integration_baseline"]:
        invalid = True
    return invalid


def _validate_client(group: Any, contract: dict[str, Any], blockers: set[str]) -> bool:
    definition = contract["client_contract"]
    row = _closed_group(group, definition["exact_keys"])
    invalid = False
    if row.get("distribution_name") not in definition["allowed_distributions"]:
        blockers.add("UNKNOWN_OR_UNSUPPORTED")
        invalid = True
    version = row.get("exact_version")
    version_pattern = re.compile(definition["exact_version_grammar"])
    if (
        not isinstance(version, str)
        or not version_pattern.fullmatch(version)
        or any(token in version.lower() for token in ("latest", "unknown", "*", ">", "<", "=", "~", "^"))
    ):
        blockers.add("UNPINNED_CLIENT")
        invalid = True
    for key in (
        "artifact_sha256",
        "installed_tree_or_record_sha256",
        "dependency_lock_sha256",
    ):
        if not _valid_digest(row.get(key)):
            blockers.add("UNPINNED_CLIENT")
            invalid = True
    if not isinstance(row.get("python_abi"), str) or not re.fullmatch(
        definition["python_abi_grammar"], row["python_abi"]
    ):
        blockers.add("UNKNOWN_OR_UNSUPPORTED")
        invalid = True
    if row.get("platform") not in definition["allowed_platforms"]:
        blockers.add("UNKNOWN_OR_UNSUPPORTED")
        invalid = True
    return invalid


def _validate_server(group: Any, contract: dict[str, Any], blockers: set[str]) -> bool:
    definition = contract["server_contract"]
    row = _closed_group(group, definition["exact_keys"])
    invalid = False
    source_type = row.get("source_type")
    if source_type not in definition["allowed_source_types"]:
        blockers.add("UNKNOWN_SERVER_BUILD")
        invalid = True
    build = row.get("exact_build")
    if (
        not isinstance(build, str)
        or not re.fullmatch(definition["exact_build_grammar"], build)
        or build.lower() in {"unknown", "default", "implicit", "inherited", "latest"}
    ):
        blockers.add("UNKNOWN_SERVER_BUILD")
        invalid = True
    image_tag = row.get("image_tag")
    if (
        not isinstance(image_tag, str)
        or not image_tag
        or image_tag.lower() in definition["moving_tags"]
        or any(token in image_tag for token in ("*", ">", "<", "~", "^"))
    ):
        blockers.add("UNPINNED_SERVER")
        invalid = True
    if not _valid_digest(row.get("image_digest")) or not _valid_digest(
        row.get("platform_digest")
    ):
        blockers.add("UNPINNED_SERVER")
        invalid = True
    if not _valid_digest(row.get("binary_or_package_sha256")):
        blockers.add("UNKNOWN_SERVER_BUILD")
        invalid = True
    return invalid


def _synthetic_alias(value: Any, pattern: str, *, allow_default_blocker: bool = False) -> bool:
    if value in {None, "", "default", "implicit", "inherited", "unknown", "client_default", "server_default"}:
        return False
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        if allow_default_blocker:
            raise ContractError("PRIVACY_POLICY_VIOLATION")
        return False
    return True


def _validate_connection(
    group: Any, contract: dict[str, Any], blockers: set[str]
) -> tuple[bool, str | None]:
    definition = contract["connection_identity_contract"]
    row = _closed_group(group, definition["exact_keys"])
    invalid = False
    mode = row.get("deployment_mode")
    recognized = contract["deployment_modes"]["recognized"]
    if mode not in recognized:
        blockers.add("UNSUPPORTED_DEPLOYMENT_MODE")
        invalid = True
    elif mode not in contract["deployment_modes"]["valid_fixture_input"]:
        blockers.add("UNSUPPORTED_DEPLOYMENT_MODE")
        invalid = True
    if mode == "remote_server":
        blockers.add("REMOTE_CAPTURE_UNSUPPORTED")
    tenant = row.get("tenant")
    database = row.get("database")
    if tenant in {None, "", "default", "implicit", "inherited", "unknown", "client_default", "server_default"}:
        blockers.add("UNKNOWN_TENANT_DATABASE")
        invalid = True
    elif not _synthetic_alias(tenant, definition["tenant_alias_grammar"], allow_default_blocker=True):
        blockers.add("UNKNOWN_TENANT_DATABASE")
        invalid = True
    if database in {None, "", "default", "implicit", "inherited", "unknown", "client_default", "server_default"}:
        blockers.add("UNKNOWN_TENANT_DATABASE")
        invalid = True
    elif not _synthetic_alias(database, definition["database_alias_grammar"], allow_default_blocker=True):
        blockers.add("UNKNOWN_TENANT_DATABASE")
        invalid = True
    if row.get("verification_method") != definition["verification_method"]:
        blockers.add("UNKNOWN_TENANT_DATABASE")
        invalid = True
    if row.get("host_classification") not in definition["allowed_host_classifications"]:
        blockers.add("UNKNOWN_OR_UNSUPPORTED")
        invalid = True
    if not _synthetic_alias(row.get("host_alias"), definition["host_alias_grammar"]):
        raise ContractError("PRIVACY_POLICY_VIOLATION")
    if row.get("authentication_mode") not in definition["allowed_authentication_modes"]:
        blockers.add("UNKNOWN_OR_UNSUPPORTED")
        invalid = True
    if row.get("tls_mode") not in definition["allowed_tls_modes"]:
        blockers.add("UNKNOWN_OR_UNSUPPORTED")
        invalid = True
    if row.get("header_names") != []:
        raise ContractError("PRIVACY_POLICY_VIOLATION")
    return invalid, mode if isinstance(mode, str) else None


def _validate_persistence(
    group: Any, contract: dict[str, Any], blockers: set[str]
) -> tuple[bool, str | None]:
    definition = contract["persistence_contract"]
    row = _closed_group(group, definition["exact_keys"])
    invalid = False
    store = row.get("opaque_store_identifier")
    snapshot = row.get("snapshot_identifier")
    if store in {None, ""}:
        blockers.add("PERSISTENCE_PROVENANCE_MISSING")
        invalid = True
    elif not _synthetic_alias(store, definition["store_identifier_grammar"]):
        raise ContractError("PRIVACY_POLICY_VIOLATION")
    if snapshot in {None, ""}:
        blockers.add("PERSISTENCE_PROVENANCE_MISSING")
        invalid = True
    elif not _synthetic_alias(snapshot, definition["snapshot_identifier_grammar"]):
        raise ContractError("PRIVACY_POLICY_VIOLATION")
    if row.get("acquisition_mechanism") not in definition["allowed_acquisition_mechanisms"]:
        blockers.add("PERSISTENCE_PROVENANCE_MISSING")
        invalid = True
    if not _valid_digest(row.get("complete_tree_digest")):
        blockers.add("PERSISTENCE_PROVENANCE_MISSING")
        invalid = True
    if row.get("file_family_policy") not in definition["allowed_file_family_policies"]:
        blockers.add("PERSISTENCE_PROVENANCE_MISSING")
        invalid = True
    if row.get("source_ownership_class") not in definition["allowed_source_ownership_classes"]:
        blockers.add("PERSISTENCE_PROVENANCE_MISSING")
        invalid = True
    if row.get("source_read_only") is not True or row.get("before_after_equal") is not True:
        blockers.add("PERSISTENCE_PROVENANCE_MISSING")
        invalid = True
    return invalid, snapshot if isinstance(snapshot, str) else None


def _validate_collections(
    group: Any,
    snapshot_identifier: str | None,
    contract: dict[str, Any],
    blockers: set[str],
) -> tuple[bool, int]:
    definition = contract["collection_contract"]
    row = _closed_group(group, definition["exact_group_keys"])
    invalid = False
    if not _valid_digest(row.get("inventory_before_sha256")) or not _valid_digest(
        row.get("inventory_after_sha256")
    ):
        blockers.add("PERSISTENCE_PROVENANCE_MISSING")
        invalid = True
    if row.get("before_after_equal") is not True or row.get("inventory_before_sha256") != row.get("inventory_after_sha256"):
        blockers.add("PERSISTENCE_PROVENANCE_MISSING")
        invalid = True
    items = row.get("items")
    if not isinstance(items, list) or not (1 <= len(items) <= contract["limits"]["maximum_collections"]):
        blockers.add("UNKNOWN_OR_UNSUPPORTED")
        return True, 0
    inventory_digest = _value_digest(items)
    if (
        row.get("inventory_before_sha256") != inventory_digest
        or row.get("inventory_after_sha256") != inventory_digest
    ):
        blockers.add("PERSISTENCE_PROVENANCE_MISSING")
        invalid = True
    names: list[str] = []
    allowed = definition["physical_collections"]
    statuses = definition["allowed_status_values"]
    for raw in items:
        item = _closed_group(raw, definition["exact_record_keys"])
        name = item.get("collection_name")
        if not isinstance(name, str) or name not in allowed:
            blockers.add("UNKNOWN_OR_UNSUPPORTED")
            invalid = True
        else:
            names.append(name)
            if item.get("family") != allowed[name]["family"] or item.get("lane") != allowed[name]["lane"]:
                blockers.add("UNKNOWN_OR_UNSUPPORTED")
                invalid = True
        count = item.get("record_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            blockers.add("PERSISTENCE_PROVENANCE_MISSING")
            invalid = True
        if not _valid_digest(item.get("sanitized_metadata_digest")):
            blockers.add("PERSISTENCE_PROVENANCE_MISSING")
            invalid = True
        if item.get("snapshot_identifier") != snapshot_identifier:
            blockers.add("PERSISTENCE_PROVENANCE_MISSING")
            invalid = True
        if item.get("tenant_database_binding_status") != "EXPLICIT_SYNTHETIC_BINDING":
            blockers.add("UNKNOWN_TENANT_DATABASE")
            invalid = True
        for key in (
            "collection_uuid_status",
            "embedding_fingerprint_status",
            "embedding_dimension_status",
            "distance_metric_status",
        ):
            if item.get(key) not in statuses[key]:
                blockers.add("UNKNOWN_OR_UNSUPPORTED")
                invalid = True
        if item.get("collection_uuid_status") != "SYNTHETIC_UUID_ATTESTED":
            blockers.add("UNKNOWN_OR_UNSUPPORTED")
            invalid = True
        if item.get("embedding_fingerprint_status") != "SYNTHETIC_DIGEST_ATTESTED":
            blockers.add("UNKNOWN_OR_UNSUPPORTED")
            invalid = True
        if item.get("embedding_dimension_status") != "SYNTHETIC_DIMENSION_ATTESTED":
            blockers.add("UNKNOWN_OR_UNSUPPORTED")
            invalid = True
        if item.get("distance_metric_status") != "COSINE_METADATA_ATTESTED":
            blockers.add("UNKNOWN_OR_UNSUPPORTED")
            invalid = True
    if len(names) != len(set(names)) or names != sorted(names):
        blockers.add("UNKNOWN_OR_UNSUPPORTED")
        invalid = True
    return invalid, len(items)


def _validate_quiescence(group: Any, contract: dict[str, Any], blockers: set[str]) -> bool:
    row = _closed_group(group, contract["group_contracts"]["quiescence"])
    invalid = False
    if row.get("evidence_status") != "SYNTHETIC_QUIESCENCE_ATTESTED" or not _valid_digest(
        row.get("evidence_sha256")
    ):
        blockers.add("PERSISTENCE_PROVENANCE_MISSING")
        invalid = True
    for key in (
        "application_stopped",
        "server_stopped",
        "writers_stopped",
        "inventory_before_after_equal",
    ):
        if row.get(key) is not True:
            blockers.add("PERSISTENCE_PROVENANCE_MISSING")
            invalid = True
    start = row.get("acquisition_window_start")
    end = row.get("acquisition_window_end")
    if not _valid_timestamp(start) or not _valid_timestamp(end) or start > end:
        blockers.add("PERSISTENCE_PROVENANCE_MISSING")
        invalid = True
    return invalid


def _validate_privacy_group(group: Any, contract: dict[str, Any]) -> bool:
    row = _closed_group(group, contract["group_contracts"]["privacy"])
    if (
        row.get("policy_name") != "SYNTHETIC_FIXED_CODE_PRIVACY_V1"
        or not _valid_digest(row.get("policy_sha256"))
        or row.get("raw_values_retained") is not False
        or row.get("prohibited_values_present") is not False
        or row.get("tenant_database_values_are_aliases") is not True
        or row.get("report_fixed_codes_only") is not True
    ):
        raise ContractError("PRIVACY_POLICY_VIOLATION")
    return False


def _validate_adapter(group: Any, contract: dict[str, Any], blockers: set[str]) -> bool:
    row = _closed_group(group, contract["group_contracts"]["adapter"])
    invalid = False
    if row.get("adapter_name") != "SYNTHETIC_PROVENANCE_CONTRACT_VALIDATOR":
        blockers.add("UNKNOWN_OR_UNSUPPORTED")
        invalid = True
    if not _valid_commit(row.get("adapter_commit")):
        blockers.add("UNKNOWN_OR_UNSUPPORTED")
        invalid = True
    if not _valid_digest(row.get("adapter_contract_sha256")):
        blockers.add("UNKNOWN_OR_UNSUPPORTED")
        invalid = True
    if row.get("standard_library_only") is not True:
        blockers.add("UNKNOWN_OR_UNSUPPORTED")
        invalid = True
    for key in (
        "network_capability",
        "runtime_discovery_capability",
        "application_import_capability",
        "mutation_capability",
    ):
        if row.get(key) is not False:
            blockers.add("UNKNOWN_OR_UNSUPPORTED")
            invalid = True
    return invalid


def _validate_review_group(group: Any, contract: dict[str, Any]) -> bool:
    row = _closed_group(group, contract["group_contracts"]["review"])
    if row != {
        "attestation_policy": "SYNTHETIC_PLACEHOLDERS_ONLY",
        "operator_role": "OPERATOR",
        "reviewer_role": "INDEPENDENT_REVIEWER",
        "aliases_distinct": True,
        "self_approval": False,
        "decision_approved": False,
        "detached_signatures_real": False,
    }:
        raise ContractError("INVALID_ATTESTATION")
    return False


def _validate_decision(group: Any, contract: dict[str, Any], blockers: set[str]) -> bool:
    definition = contract["decision_contract"]
    row = _closed_group(group, definition["exact_keys"])
    if (
        row.get("requested_outcome") != definition["required_requested_outcome"]
        or row.get("real_capture_authorized") is not False
        or row.get("remote_capture_supported") is not False
        or type(row.get("supported_combination_count")) is not int
        or row.get("supported_combination_count") != 0
        or row.get("compatibility_allowlist_empty") is not True
    ):
        raise ContractError("INVALID_ATTESTATION")
    claimed = row.get("claimed_blockers")
    if claimed != ["UNKNOWN_OR_UNSUPPORTED"]:
        raise ContractError("INVALID_ATTESTATION")
    compatibility = row.get("client_server_compatibility")
    if compatibility not in definition["allowed_client_server_compatibility"]:
        raise ContractError("INVALID_ATTESTATION")
    if compatibility == "MISMATCH":
        blockers.add("CLIENT_SERVER_MISMATCH")
        return True
    return False


def _validate_subject(
    subject: Any,
    manifest: dict[str, Any],
    fixture_id: str,
    contract: dict[str, Any],
) -> str:
    definition = contract["compatibility_subject_contract"]
    row = _strict_object(subject, definition["exact_keys"], "INVALID_ATTESTATION")
    if (
        type(row["subject_version"]) is not int
        or row["subject_version"] != 1
        or row["fixture_id"] != fixture_id
        or row["contract_sha256"] != CONTRACT_SHA256
    ):
        raise ContractError("INVALID_ATTESTATION")
    mapping = {
        "contract_group_sha256": "contract",
        "source_code_sha256": "source_code",
        "client_sha256": "client",
        "server_sha256": "server",
        "connection_identity_sha256": "connection_identity",
        "persistence_sha256": "persistence",
        "collections_sha256": "collections",
        "quiescence_sha256": "quiescence",
        "privacy_sha256": "privacy",
        "adapter_sha256": "adapter",
        "review_sha256": "review",
        "decision_sha256": "decision",
    }
    for digest_key, group_name in mapping.items():
        supplied = row[digest_key]
        if not _valid_digest(supplied) or not hmac.compare_digest(
            supplied, _value_digest(manifest[group_name])
        ):
            raise ContractError("INVALID_ATTESTATION")
    canonical = row["canonical_sha256"]
    if not _valid_digest(canonical) or not hmac.compare_digest(
        canonical, _canonical_digest(row)
    ):
        raise ContractError("INVALID_ATTESTATION")
    return canonical


def _validate_attestation(
    value: Any,
    *,
    expected_role: str,
    fixture_id: str,
    subject_digest: str,
    contract: dict[str, Any],
) -> str:
    definition = contract["attestation_contract"]
    row = _strict_object(value, definition["exact_keys"], "INVALID_ATTESTATION")
    if (
        type(row["attestation_version"]) is not int
        or row["attestation_version"] != 1
        or row["fixture_id"] != fixture_id
        or row["subject_sha256"] != subject_digest
        or row["role"] != expected_role
        or row["detached_signature_status"] != definition["allowed_detached_signature_status"]
        or row["decision_binding_status"] != definition["allowed_decision_binding_status"]
        or not _valid_timestamp(row["timestamp"])
    ):
        raise ContractError("INVALID_ATTESTATION")
    pattern_key = "operator_alias_grammar" if expected_role == "OPERATOR" else "reviewer_alias_grammar"
    alias = row["actor_alias"]
    if not isinstance(alias, str) or not re.fullmatch(definition[pattern_key], alias):
        raise ContractError("INVALID_ATTESTATION")
    canonical = row["canonical_sha256"]
    if not _valid_digest(canonical) or not hmac.compare_digest(
        canonical, _canonical_digest(row)
    ):
        raise ContractError("INVALID_ATTESTATION")
    return alias


def _validate_documents(
    manifest: dict[str, Any],
    subject: Any,
    operator: Any,
    reviewer: Any,
    fixture_id: str,
    contract: dict[str, Any],
) -> dict[str, Any]:
    prohibited_keys = frozenset(contract["privacy_contract"]["prohibited_input_keys"])
    for document in (manifest, subject, operator, reviewer):
        _privacy_scan(document, prohibited_keys)

    blockers: set[str] = {"UNKNOWN_OR_UNSUPPORTED"}
    semantic_invalid = False
    semantic_invalid |= _validate_contract_group(manifest["contract"], contract)
    semantic_invalid |= _validate_source_code(manifest["source_code"], contract)
    semantic_invalid |= _validate_client(manifest["client"], contract, blockers)
    semantic_invalid |= _validate_server(manifest["server"], contract, blockers)
    connection_invalid, deployment_mode = _validate_connection(
        manifest["connection_identity"], contract, blockers
    )
    semantic_invalid |= connection_invalid
    persistence_invalid, snapshot_identifier = _validate_persistence(
        manifest["persistence"], contract, blockers
    )
    semantic_invalid |= persistence_invalid
    collections_invalid, collection_count = _validate_collections(
        manifest["collections"], snapshot_identifier, contract, blockers
    )
    semantic_invalid |= collections_invalid
    semantic_invalid |= _validate_quiescence(manifest["quiescence"], contract, blockers)
    semantic_invalid |= _validate_privacy_group(manifest["privacy"], contract)
    semantic_invalid |= _validate_adapter(manifest["adapter"], contract, blockers)
    semantic_invalid |= _validate_review_group(manifest["review"], contract)
    semantic_invalid |= _validate_decision(manifest["decision"], contract, blockers)
    if (
        manifest["source_code"].get("capture_adapter_commit")
        != manifest["adapter"].get("adapter_commit")
        or manifest["adapter"].get("adapter_contract_sha256") != CONTRACT_SHA256
    ):
        blockers.add("UNKNOWN_OR_UNSUPPORTED")
        semantic_invalid = True

    subject_digest = _validate_subject(subject, manifest, fixture_id, contract)
    operator_alias = _validate_attestation(
        operator,
        expected_role="OPERATOR",
        fixture_id=fixture_id,
        subject_digest=subject_digest,
        contract=contract,
    )
    reviewer_alias = _validate_attestation(
        reviewer,
        expected_role="INDEPENDENT_REVIEWER",
        fixture_id=fixture_id,
        subject_digest=subject_digest,
        contract=contract,
    )
    if hmac.compare_digest(operator_alias, reviewer_alias):
        raise ContractError("INVALID_ATTESTATION")

    if contract["supported_combinations"] != []:
        raise ContractError("READ_ERROR")
    if {"SUPPORTED_EXACT", "SUPPORTED_REVIEWED_VARIANT"}.intersection(blockers):
        raise ContractError("READ_ERROR")
    ordered = sorted(blockers)
    valid = (
        not semantic_invalid
        and deployment_mode == "synthetic_fixture"
        and ordered == ["UNKNOWN_OR_UNSUPPORTED"]
    )
    return {
        **SAFE_REPORT_BASE,
        "status": (
            "VALID_SYNTHETIC_PROVENANCE_BLOCKED"
            if valid
            else "INVALID_SYNTHETIC_PROVENANCE_BLOCKED"
        ),
        "valid": valid,
        "fixture_provenance": SOURCE_CLASSIFICATION,
        "contract_sha256": CONTRACT_SHA256,
        "collection_count": collection_count,
        "blockers": ordered,
    }


def _validate_fixture_contents(
    directory: FixtureDirectory,
    before: dict[str, Any],
    sentinel: str,
    contract: dict[str, Any],
) -> dict[str, Any]:
    maximum = contract["limits"]["maximum_file_bytes"]
    manifest = _load_json_bytes(
        directory.read(
            "provenance-manifest.json",
            before,
            contract["limits"]["maximum_manifest_bytes"],
        )
    )
    fixture_id = _validate_manifest_envelope(manifest, contract, before, sentinel)
    subject = _load_json_bytes(
        directory.read("compatibility-subject.json", before, maximum)
    )
    operator = _load_json_bytes(
        directory.read("operator-attestation.json", before, maximum)
    )
    reviewer = _load_json_bytes(
        directory.read("reviewer-attestation.json", before, maximum)
    )
    return _validate_documents(
        manifest, subject, operator, reviewer, fixture_id, contract
    )


def validate_fixture_contract(fixture: str, *, fixture_only: bool) -> dict[str, Any]:
    if not fixture_only:
        raise ContractError("READ_ERROR")
    contract = _load_contract()
    path, sentinel = _approved_fixture_path(fixture)
    directory = FixtureDirectory(path)
    before: dict[str, Any] | None = None
    caught: BaseException | None = None
    result: dict[str, Any] | None = None
    try:
        before = directory.inventory()
        result = _validate_fixture_contents(directory, before, sentinel, contract)
    except BaseException as exc:
        caught = exc
    try:
        after = directory.inventory()
        if before is not None and before != after:
            caught = ContractError("READ_ERROR")
        if not directory.namespace_matches():
            caught = ContractError("READ_ERROR")
    except BaseException as exc:
        caught = exc
    finally:
        directory.close()
    if caught is not None:
        if isinstance(caught, ContractError):
            raise caught
        raise ContractError("READ_ERROR") from caught
    if result is None:
        raise ContractError("READ_ERROR")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--fixture-only", action="store_true")
    parser.add_argument("--fixture", required=True)
    return parser


def _error_report(code: str) -> dict[str, Any]:
    safe_code = code if code in EXPECTED_STATUS_CODES else "READ_ERROR"
    return {
        **SAFE_REPORT_BASE,
        "status": "ERROR",
        "valid": False,
        "blockers": [safe_code],
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
) -> int:
    stdout = stdout or sys.stdout
    try:
        arguments = build_parser().parse_args(argv)
        report = validate_fixture_contract(
            arguments.fixture, fixture_only=arguments.fixture_only
        )
    except ContractError as exc:
        print(json.dumps(_error_report(exc.code), sort_keys=True), file=stdout)
        return 2
    except Exception:
        print(json.dumps(_error_report("READ_ERROR"), sort_keys=True), file=stdout)
        return 2
    print(json.dumps(report, sort_keys=True), file=stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
