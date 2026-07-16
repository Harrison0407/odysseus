#!/usr/bin/env python3
"""Build an incomplete, fixture-only MarketMatch sanitized package.

This module is deliberately unusable outside the dedicated disposable-fixture
launcher.  It never discovers sources, copies SQLite pages, or reads artifact
content.  Real and sanitized real-world inputs remain unauthorized.
"""

from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import sys
import tempfile
import unicodedata
from urllib.parse import quote
from typing import Any, Iterable, Sequence, TextIO


SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parent
PACKAGE_CONTRACT_PATH = SCRIPT_DIR / "offline_package_contracts" / "package_v1.json"
TRANSFORM_CONTRACT_PATH = (
    SCRIPT_DIR / "offline_package_contracts" / "sql_transform_9844a2f_6197984.json"
)
SCHEMA_CONTRACT_PATH = SCRIPT_DIR / "schema_contracts" / "combined_9844a2f_6197984.json"

PACKAGE_CONTRACT_SHA256 = "7bb8f02a5387ba038b36ad9c70f7516dd114f9ff50f05c24b74a26ecf97189fc"
TRANSFORM_CONTRACT_SHA256 = "706c3a8d341ab53ae8b61d973928a3181b200824f1c03a42ea7fc21f1fbae86c"
SCHEMA_CONTRACT_SHA256 = "bcdfdb9120a805212e7ee3cd87a9708dd52bfac20681ad7f75cd36f185618ef0"
TEST_LAUNCHER_SHA256 = "7308b92389d867f6353c4811e5a7c38d7227a00dbe92b8f8a7668a3702845dd8"

SOURCE_CLASSIFICATION = "SYNTHETIC_DISPOSABLE_FIXTURE"
RAG_STATUS = "RAG_EXPORT_NOT_AVAILABLE"
AUTHORIZATION = "NOT_AUTHORIZED_FOR_REAL_INSPECTION"
PINNED_INTERNAL_OWNERS = ("internal-tool", "api", "demo", "system")
ALLOWED_ACTIONS = frozenset(
    {
        "COPY_METADATA",
        "ALIAS_OWNER",
        "ALIAS_IDENTIFIER",
        "PRESERVE_NULL",
        "CONSTANT_REDACTED",
        "OMIT",
        "REJECT_IF_PRESENT",
        "GENERALIZE",
        "DERIVE_APPROVED_REFERENCE",
    }
)
ALIAS_PREFIXES = {
    "owner": "owner",
    "workflow": "workflow",
    "upload": "upload",
    "document": "document",
    "rag-record": "rag",
    "path": "path",
    "generic-record": "record",
}
TRANSFORM_TABLE_ORDER = (
    "sessions",
    "documents",
    "marketmatch_calls",
    "marketmatch_videos",
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
    "RAG_DIR",
    "RAG_DB_PATH",
    "EMAIL_CACHE_DB",
    "HF_HOME",
    "HUGGINGFACE_HUB_CACHE",
    "SQLITE_TMPDIR",
)
SOURCE_PRAGMAS = frozenset(
    {
        "database_list",
        "foreign_key_list",
        "freelist_count",
        "index_list",
        "index_xinfo",
        "query_only",
        "schema_version",
        "table_xinfo",
    }
)


class BuilderError(RuntimeError):
    """A fail-closed error whose code is safe to report."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise BuilderError("INVALID_ARGUMENTS")


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    value = copy.deepcopy(payload)
    value.pop("canonical_sha256", None)
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def canonical_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _open_nofollow(path: Path, *, directory: bool = False) -> int:
    """Open an absolute path without following any symlink component."""
    if not path.is_absolute() or not path.parts:
        raise BuilderError("PATH_VALIDATION_ERROR")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    anchor = Path(path.anchor)
    try:
        descriptor = os.open(anchor, flags | directory_flag)
        for part in path.parts[1:-1]:
            next_descriptor = os.open(
                part, flags | directory_flag, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = next_descriptor
        final_flags = flags | (directory_flag if directory else 0)
        final_descriptor = os.open(path.parts[-1], final_flags, dir_fd=descriptor)
        os.close(descriptor)
        return final_descriptor
    except OSError as exc:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        raise BuilderError("PATH_VALIDATION_ERROR") from exc


def _open_regular_nofollow(path: Path) -> tuple[int, os.stat_result]:
    descriptor = _open_nofollow(path)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise BuilderError("FIXTURE_METADATA_NOT_REGULAR")
    if metadata.st_nlink != 1:
        os.close(descriptor)
        raise BuilderError("HARD_LINK_REJECTED")
    return descriptor, metadata


def _read_fd(descriptor: int, size: int) -> bytes:
    chunks = []
    offset = 0
    while offset < size:
        chunk = os.pread(descriptor, min(1024 * 1024, size - offset), offset)
        if not chunk:
            raise BuilderError("FIXTURE_METADATA_READ_ERROR")
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def _sha256_fd(descriptor: int, size: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        chunk = os.pread(descriptor, min(1024 * 1024, size - offset), offset)
        if not chunk:
            raise BuilderError("FIXTURE_METADATA_READ_ERROR")
        digest.update(chunk)
        offset += len(chunk)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    descriptor, metadata = _open_regular_nofollow(path)
    try:
        return _sha256_fd(descriptor, metadata.st_size)
    finally:
        os.close(descriptor)


def _exact_keys(value: Any, expected: Iterable[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(expected):
        raise BuilderError(code)
    return value


def _load_json(path: Path, *, maximum_bytes: int = 1024 * 1024) -> Any:
    try:
        descriptor, metadata = _open_regular_nofollow(path)
        try:
            if metadata.st_size > maximum_bytes:
                raise BuilderError("FIXTURE_METADATA_TOO_LARGE")
            encoded = _read_fd(descriptor, metadata.st_size)
        finally:
            os.close(descriptor)
        return json.loads(encoded.decode("utf-8"))
    except BuilderError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuilderError("FIXTURE_METADATA_READ_ERROR") from exc


def _load_contract(path: Path, expected_digest: str) -> dict[str, Any]:
    payload = _load_json(path, maximum_bytes=4 * 1024 * 1024)
    if not isinstance(payload, dict):
        raise BuilderError("CONTRACT_INVALID")
    if payload.get("canonical_sha256") != expected_digest:
        raise BuilderError("CONTRACT_DIGEST_MISMATCH")
    if canonical_digest(payload) != expected_digest:
        raise BuilderError("CONTRACT_DIGEST_MISMATCH")
    return payload


def load_schema_contract() -> dict[str, Any]:
    return _load_contract(SCHEMA_CONTRACT_PATH, SCHEMA_CONTRACT_SHA256)


def load_transform_contract() -> dict[str, Any]:
    return _load_contract(TRANSFORM_CONTRACT_PATH, TRANSFORM_CONTRACT_SHA256)


def load_package_contract() -> dict[str, Any]:
    return _load_contract(PACKAGE_CONTRACT_PATH, PACKAGE_CONTRACT_SHA256)


def validate_transform_contract(
    transform: dict[str, Any], schema: dict[str, Any]
) -> None:
    if set(transform.get("allowed_actions", ())) != ALLOWED_ACTIONS:
        raise BuilderError("UNKNOWN_TRANSFORM_ACTION")
    if set(transform.get("alias_domains", ())) != set(ALIAS_PREFIXES):
        raise BuilderError("TRANSFORM_CONTRACT_INVALID")
    tables = transform.get("tables")
    if not isinstance(tables, dict) or set(tables) != set(schema["tables"]):
        raise BuilderError("NONEXHAUSTIVE_TRANSFORM_CONTRACT")
    for table_name, table_schema in schema["tables"].items():
        table_transform = tables.get(table_name)
        if not isinstance(table_transform, dict):
            raise BuilderError("NONEXHAUSTIVE_TRANSFORM_CONTRACT")
        if table_transform.get("row_policy") not in {"OMIT_ALL_ROWS", "TRANSFORM_ROWS"}:
            raise BuilderError("TRANSFORM_CONTRACT_INVALID")
        expected_columns = [column["name"] for column in table_schema["columns"]]
        if table_transform.get("column_order") != expected_columns:
            raise BuilderError("NONEXHAUSTIVE_TRANSFORM_CONTRACT")
        columns = table_transform.get("columns")
        if not isinstance(columns, dict) or set(columns) != set(expected_columns):
            raise BuilderError("NONEXHAUSTIVE_TRANSFORM_CONTRACT")
        for specification in columns.values():
            if not isinstance(specification, dict):
                raise BuilderError("TRANSFORM_CONTRACT_INVALID")
            action = specification.get("action")
            if action not in ALLOWED_ACTIONS:
                raise BuilderError("UNKNOWN_TRANSFORM_ACTION")
            domain = specification.get("domain")
            if action in {"ALIAS_IDENTIFIER", "DERIVE_APPROVED_REFERENCE"}:
                if domain not in ALIAS_PREFIXES:
                    raise BuilderError("TRANSFORM_CONTRACT_INVALID")
            if action == "DERIVE_APPROVED_REFERENCE":
                if domain != "path":
                    raise BuilderError("TRANSFORM_CONTRACT_INVALID")
                source_column = specification.get("source_column")
                if source_column not in expected_columns:
                    raise BuilderError("TRANSFORM_CONTRACT_INVALID")


def _normalize_owner(value: str) -> str:
    if not isinstance(value, str):
        raise BuilderError("INVALID_OWNER")
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    if not normalized or len(normalized) > 256:
        raise BuilderError("INVALID_OWNER")
    return normalized


def _ensure_no_symlink_components(path: Path, stop: Path) -> None:
    current = path
    while True:
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise BuilderError("PATH_VALIDATION_ERROR") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise BuilderError("SYMLINK_REJECTED")
        if current == stop:
            return
        if current.parent == current:
            raise BuilderError("PATH_OUTSIDE_FIXTURE_ROOT")
        current = current.parent


def _relative_argument(value: str, expected_parent: str) -> Path:
    path = Path(value)
    if path.is_absolute() or not path.parts or path.parts[0] != expected_parent:
        raise BuilderError("PATH_OUTSIDE_FIXTURE_ROOT")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise BuilderError("PATH_OUTSIDE_FIXTURE_ROOT")
    return path


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class DestinationTree:
    """A destination tree addressed only through no-follow directory descriptors."""

    def __init__(
        self,
        *,
        parent_fd: int,
        root_fd: int,
        name: str,
        identity: tuple[int, int],
    ) -> None:
        self.parent_fd = parent_fd
        self.root_fd = root_fd
        self.name = name
        self.identity = identity

    @classmethod
    def create(
        cls, destination: Path, *, expected_parent_identity: tuple[int, int]
    ) -> "DestinationTree":
        parent_fd = _open_nofollow(destination.parent, directory=True)
        root_fd: int | None = None
        try:
            parent_metadata = os.fstat(parent_fd)
            if (parent_metadata.st_dev, parent_metadata.st_ino) != expected_parent_identity:
                raise BuilderError("DESTINATION_PARENT_REPLACED")
            try:
                os.mkdir(destination.name, mode=0o700, dir_fd=parent_fd)
            except FileExistsError as exc:
                raise BuilderError("DESTINATION_EXISTS") from exc
            root_fd = os.open(
                destination.name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
            metadata = os.fstat(root_fd)
            if not stat.S_ISDIR(metadata.st_mode):
                raise BuilderError("DESTINATION_CREATE_FAILED")
            return cls(
                parent_fd=parent_fd,
                root_fd=root_fd,
                name=destination.name,
                identity=(metadata.st_dev, metadata.st_ino),
            )
        except BuilderError:
            if root_fd is not None:
                os.close(root_fd)
            os.close(parent_fd)
            raise
        except OSError as exc:
            if root_fd is not None:
                os.close(root_fd)
            os.close(parent_fd)
            raise BuilderError("DESTINATION_CREATE_FAILED") from exc

    @classmethod
    def open_existing(cls, package: Path) -> "DestinationTree":
        if not package.is_absolute() or not package.name:
            raise BuilderError("PATH_VALIDATION_ERROR")
        parent_fd = _open_nofollow(package.parent, directory=True)
        root_fd: int | None = None
        try:
            root_fd = os.open(
                package.name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
            metadata = os.fstat(root_fd)
            if not stat.S_ISDIR(metadata.st_mode):
                raise BuilderError("PATH_VALIDATION_ERROR")
            return cls(
                parent_fd=parent_fd,
                root_fd=root_fd,
                name=package.name,
                identity=(metadata.st_dev, metadata.st_ino),
            )
        except BuilderError:
            if root_fd is not None:
                os.close(root_fd)
            os.close(parent_fd)
            raise
        except OSError as exc:
            if root_fd is not None:
                os.close(root_fd)
            os.close(parent_fd)
            raise BuilderError("PATH_VALIDATION_ERROR") from exc

    @staticmethod
    def _parts(relative: str) -> tuple[str, ...]:
        path = Path(relative)
        if (
            path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise BuilderError("PATH_VALIDATION_ERROR")
        return path.parts

    def _open_directory(self, parts: Sequence[str]) -> int:
        descriptor = os.dup(self.root_fd)
        try:
            for part in parts:
                next_descriptor = os.open(
                    part,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = next_descriptor
            return descriptor
        except OSError as exc:
            os.close(descriptor)
            raise BuilderError("DESTINATION_PATH_ERROR") from exc

    def mkdir(self, relative: str) -> None:
        parts = self._parts(relative)
        parent = self._open_directory(parts[:-1])
        try:
            os.mkdir(parts[-1], mode=0o700, dir_fd=parent)
        except OSError as exc:
            raise BuilderError("DESTINATION_PATH_ERROR") from exc
        finally:
            os.close(parent)

    def write_bytes(self, relative: str, value: bytes, *, mode: int = 0o600) -> None:
        parts = self._parts(relative)
        parent = self._open_directory(parts[:-1])
        descriptor: int | None = None
        try:
            descriptor = os.open(
                parts[-1],
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                mode,
                dir_fd=parent,
            )
            offset = 0
            while offset < len(value):
                written = os.write(descriptor, value[offset:])
                if written <= 0:
                    raise BuilderError("DESTINATION_WRITE_ERROR")
                offset += written
            os.fsync(descriptor)
        except BuilderError:
            raise
        except OSError as exc:
            raise BuilderError("DESTINATION_WRITE_ERROR") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)

    def write_json(self, relative: str, payload: Any) -> None:
        encoded = (
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "\n"
        ).encode("utf-8")
        self.write_bytes(relative, encoded)

    def read_bytes(self, relative: str, *, maximum_bytes: int) -> bytes:
        parts = self._parts(relative)
        parent = self._open_directory(parts[:-1])
        descriptor: int | None = None
        try:
            descriptor = os.open(
                parts[-1],
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent,
            )
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size > maximum_bytes
            ):
                raise BuilderError("PACKAGE_POLICY_MISMATCH")
            return _read_fd(descriptor, metadata.st_size)
        except BuilderError:
            raise
        except OSError as exc:
            raise BuilderError("PACKAGE_POLICY_MISMATCH") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)

    def read_json(self, relative: str, *, maximum_bytes: int) -> Any:
        try:
            return json.loads(self.read_bytes(relative, maximum_bytes=maximum_bytes))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise BuilderError("PACKAGE_POLICY_MISMATCH") from exc

    def stat(self, relative: str) -> os.stat_result:
        parts = self._parts(relative)
        parent = self._open_directory(parts[:-1])
        try:
            return os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        except OSError as exc:
            raise BuilderError("DESTINATION_PATH_ERROR") from exc
        finally:
            os.close(parent)

    def namespace_matches(self) -> bool:
        try:
            metadata = os.stat(
                self.name, dir_fd=self.parent_fd, follow_symlinks=False
            )
        except OSError:
            return False
        return stat.S_ISDIR(metadata.st_mode) and (
            metadata.st_dev,
            metadata.st_ino,
        ) == self.identity

    def invalidate(self) -> None:
        try:
            os.unlink("package-manifest.json", dir_fd=self.root_fd)
        except FileNotFoundError:
            pass
        except OSError:
            pass
        try:
            descriptor = os.open(
                "INVALID-FIXTURE-PACKAGE",
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o400,
                dir_fd=self.root_fd,
            )
            try:
                os.write(descriptor, b"INVALID\n")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass

    def entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []

        def walk(directory_fd: int, prefix: str) -> None:
            try:
                children = sorted(os.scandir(directory_fd), key=lambda entry: entry.name)
            except OSError as exc:
                raise BuilderError("DESTINATION_PATH_ERROR") from exc
            for child in children:
                relative = f"{prefix}/{child.name}" if prefix else child.name
                try:
                    metadata = child.stat(follow_symlinks=False)
                except OSError as exc:
                    raise BuilderError("DESTINATION_PATH_ERROR") from exc
                if stat.S_ISLNK(metadata.st_mode):
                    raise BuilderError("SYMLINK_REJECTED")
                if stat.S_ISDIR(metadata.st_mode):
                    child_fd = os.open(
                        child.name,
                        os.O_RDONLY
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_CLOEXEC", 0),
                        dir_fd=directory_fd,
                    )
                    try:
                        opened = os.fstat(child_fd)
                        entries.append(
                            {
                                "path": relative,
                                "type": "directory",
                                "mode": stat.S_IMODE(opened.st_mode),
                            }
                        )
                        walk(child_fd, relative)
                    finally:
                        os.close(child_fd)
                elif stat.S_ISREG(metadata.st_mode):
                    file_fd = os.open(
                        child.name,
                        os.O_RDONLY
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_CLOEXEC", 0),
                        dir_fd=directory_fd,
                    )
                    try:
                        opened = os.fstat(file_fd)
                        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                            raise BuilderError("HARD_LINK_REJECTED")
                        entries.append(
                            {
                                "path": relative,
                                "type": "file",
                                "mode": stat.S_IMODE(opened.st_mode),
                                "size": opened.st_size,
                                "sha256": _sha256_fd(file_fd, opened.st_size),
                            }
                        )
                    finally:
                        os.close(file_fd)
                else:
                    raise BuilderError("SPECIAL_FILE_REJECTED")

        walk(self.root_fd, "")
        return sorted(entries, key=lambda entry: entry["path"])

    def close(self) -> None:
        os.close(self.root_fd)
        os.close(self.parent_fd)


def _fixture_root() -> tuple[Path, str]:
    for variable in BLOCKED_ENVIRONMENT:
        if variable in os.environ:
            raise BuilderError("RUNTIME_ENVIRONMENT_PRESENT")
    raw_root = os.environ.get("MARKETMATCH_OFFLINE_PACKAGE_TEST_ROOT")
    sentinel = os.environ.get("MARKETMATCH_OFFLINE_PACKAGE_SENTINEL")
    sentinel_fd_text = os.environ.get("MARKETMATCH_OFFLINE_PACKAGE_SENTINEL_FD")
    launcher_fd_text = os.environ.get("MARKETMATCH_OFFLINE_PACKAGE_LAUNCHER_FD")
    launcher_pid_text = os.environ.get("MARKETMATCH_OFFLINE_PACKAGE_LAUNCHER_PID")
    if not raw_root or not sentinel or len(sentinel) != 64:
        raise BuilderError("FIXTURE_LAUNCHER_REQUIRED")
    try:
        sentinel_fd = int(sentinel_fd_text or "", 10)
        launcher_fd = int(launcher_fd_text or "", 10)
        launcher_pid = int(launcher_pid_text or "", 10)
    except ValueError as exc:
        raise BuilderError("FIXTURE_LAUNCHER_REQUIRED") from exc
    if sentinel_fd < 3 or launcher_fd < 3 or launcher_pid != os.getppid():
        raise BuilderError("FIXTURE_LAUNCHER_REQUIRED")
    try:
        descriptor_flags = fcntl.fcntl(sentinel_fd, fcntl.F_GETFL)
        descriptor_metadata = os.fstat(sentinel_fd)
        descriptor_value = _read_fd(sentinel_fd, descriptor_metadata.st_size).decode("ascii").strip()
    except (OSError, UnicodeError, BuilderError) as exc:
        raise BuilderError("FIXTURE_LAUNCHER_REQUIRED") from exc
    if descriptor_flags & os.O_ACCMODE != os.O_RDONLY:
        raise BuilderError("FIXTURE_LAUNCHER_REQUIRED")
    if not stat.S_ISREG(descriptor_metadata.st_mode) or descriptor_metadata.st_nlink != 1:
        raise BuilderError("FIXTURE_LAUNCHER_REQUIRED")
    if not hmac.compare_digest(descriptor_value, sentinel):
        raise BuilderError("FIXTURE_LAUNCHER_REQUIRED")
    expected_launcher = REPOSITORY_ROOT / "tests" / "run_offline_package_tests.sh"
    try:
        launcher_flags = fcntl.fcntl(launcher_fd, fcntl.F_GETFL)
        launcher_metadata = os.fstat(launcher_fd)
        expected_launcher_metadata = expected_launcher.lstat()
        launcher_digest = _sha256_fd(launcher_fd, launcher_metadata.st_size)
    except (OSError, BuilderError) as exc:
        raise BuilderError("FIXTURE_LAUNCHER_REQUIRED") from exc
    if (
        launcher_flags & os.O_ACCMODE != os.O_RDONLY
        or not stat.S_ISREG(launcher_metadata.st_mode)
        or launcher_metadata.st_nlink != 1
        or (launcher_metadata.st_dev, launcher_metadata.st_ino)
        != (expected_launcher_metadata.st_dev, expected_launcher_metadata.st_ino)
        or not hmac.compare_digest(launcher_digest, TEST_LAUNCHER_SHA256)
    ):
        raise BuilderError("FIXTURE_LAUNCHER_REQUIRED")
    root_input = Path(raw_root)
    if not root_input.is_absolute():
        raise BuilderError("FIXTURE_LAUNCHER_REQUIRED")
    if any(part in {".", ".."} for part in root_input.parts):
        raise BuilderError("FIXTURE_LAUNCHER_REQUIRED")
    _ensure_no_symlink_components(root_input, Path(root_input.anchor))
    root = root_input.resolve(strict=True)
    temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
    if not _is_relative_to(root, temp_root) or root == temp_root:
        raise BuilderError("FIXTURE_ROOT_NOT_TEMPORARY")
    repository = REPOSITORY_ROOT.resolve(strict=True)
    home = Path.home().resolve(strict=True)
    if _is_relative_to(root, repository) or _is_relative_to(root, home):
        raise BuilderError("PERSISTENT_PATH_REJECTED")
    launcher_sentinel = root / ".launcher-fixture-sentinel"
    _ensure_no_symlink_components(launcher_sentinel, root)
    metadata = launcher_sentinel.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise BuilderError("FIXTURE_LAUNCHER_REQUIRED")
    if (metadata.st_dev, metadata.st_ino) != (
        descriptor_metadata.st_dev,
        descriptor_metadata.st_ino,
    ):
        raise BuilderError("FIXTURE_LAUNCHER_REQUIRED")
    return root, sentinel


def _approved_paths(
    source: str, destination: str
) -> tuple[Path, Path, Path, str, tuple[int, int]]:
    root, launcher_sentinel = _fixture_root()
    source_relative = _relative_argument(source, "sources")
    destination_relative = _relative_argument(destination, "destinations")
    source_path = root / source_relative
    destination_path = root / destination_relative
    if not source_path.is_dir():
        raise BuilderError("FIXTURE_SOURCE_MISSING")
    _ensure_no_symlink_components(source_path, root)
    source_resolved = source_path.resolve(strict=True)
    destination_parent = destination_path.parent
    if not destination_parent.is_dir():
        raise BuilderError("DESTINATION_PARENT_MISSING")
    _ensure_no_symlink_components(destination_parent, root)
    destination_parent_metadata = destination_parent.lstat()
    destination_resolved = destination_parent.resolve(strict=True) / destination_path.name
    if destination_path.exists() or destination_path.is_symlink():
        raise BuilderError("DESTINATION_EXISTS")
    if _is_relative_to(destination_resolved, source_resolved) or _is_relative_to(
        source_resolved, destination_resolved
    ):
        raise BuilderError("SOURCE_DESTINATION_OVERLAP")
    return (
        root,
        source_resolved,
        destination_resolved,
        launcher_sentinel,
        (destination_parent_metadata.st_dev, destination_parent_metadata.st_ino),
    )


def reject_special_mode(mode: int) -> None:
    if stat.S_ISLNK(mode):
        raise BuilderError("SYMLINK_REJECTED")
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise BuilderError("SPECIAL_FILE_REJECTED")


def _validate_source_tree(source: Path) -> None:
    allowed_top_level = {
        ".fixture-sentinel.json",
        "provenance.json",
        "owners.synthetic.json",
        "uploads.synthetic.json",
        "database",
        "artifacts",
    }
    for child in source.iterdir():
        if child.name not in allowed_top_level:
            raise BuilderError("UNEXPECTED_SOURCE_FILE")
    for path in sorted(source.rglob("*")):
        if path.name.endswith(("-wal", "-shm", "-journal")):
            raise BuilderError("SOURCE_SIDECAR_PRESENT")
        metadata = path.lstat()
        reject_special_mode(metadata.st_mode)
        if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
            raise BuilderError("HARD_LINK_REJECTED")
    database_files = list((source / "database").iterdir())
    if [path.name for path in database_files] != ["source.sqlite"]:
        raise BuilderError("UNEXPECTED_SOURCE_FILE")


def _source_provenance(source: Path, launcher_sentinel: str) -> dict[str, Any]:
    sentinel = _exact_keys(
        _load_json(source / ".fixture-sentinel.json"),
        ("classification", "fixture_id", "launcher_sentinel"),
        "FIXTURE_SENTINEL_INVALID",
    )
    provenance = _exact_keys(
        _load_json(source / "provenance.json"),
        ("classification", "fixture_id", "launcher_sentinel", "database"),
        "FIXTURE_PROVENANCE_INVALID",
    )
    database = _exact_keys(
        provenance["database"], ("relative_path", "sha256"), "FIXTURE_PROVENANCE_INVALID"
    )
    for value in (sentinel, provenance):
        if value["classification"] != SOURCE_CLASSIFICATION:
            raise BuilderError("NON_SYNTHETIC_SOURCE_REJECTED")
        if value["launcher_sentinel"] != launcher_sentinel:
            raise BuilderError("FIXTURE_SENTINEL_INVALID")
    if sentinel["fixture_id"] != provenance["fixture_id"]:
        raise BuilderError("FIXTURE_SENTINEL_INVALID")
    if database["relative_path"] != "database/source.sqlite":
        raise BuilderError("FIXTURE_PROVENANCE_INVALID")
    if not isinstance(database["sha256"], str) or len(database["sha256"]) != 64:
        raise BuilderError("FIXTURE_PROVENANCE_INVALID")
    return provenance


def _reject_sidecars(database: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(database) + suffix).exists() or Path(str(database) + suffix).is_symlink():
            raise BuilderError("SOURCE_SIDECAR_PRESENT")


def _source_authorizer_for(
    readable: dict[str, set[str]],
    *,
    allowed_functions: frozenset[str] = frozenset(),
):
    def authorize(
        action: int,
        first: str | None,
        second: str | None,
        database: str | None,
        trigger: str | None,
    ) -> int:
        del trigger
        if action in {sqlite3.SQLITE_SELECT, getattr(sqlite3, "SQLITE_RECURSIVE", -999)}:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            return (
                sqlite3.SQLITE_OK
                if (second or first or "").casefold() in allowed_functions
                else sqlite3.SQLITE_DENY
            )
        if action == sqlite3.SQLITE_READ:
            if database not in {"main", None}:
                return sqlite3.SQLITE_DENY
            table_columns = readable.get(first or "")
            return (
                sqlite3.SQLITE_OK
                if table_columns is not None and (second or "") in table_columns
                else sqlite3.SQLITE_DENY
            )
        if action == sqlite3.SQLITE_PRAGMA and (first or "").casefold() in SOURCE_PRAGMAS:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    return authorize


@contextmanager
def _open_source(
    database: Path,
    *,
    readable: dict[str, set[str]],
    allowed_functions: frozenset[str] = frozenset(),
    expected_sha256: str | None = None,
):
    _reject_sidecars(database)
    descriptor: int | None = None
    connection: sqlite3.Connection | None = None
    try:
        descriptor, metadata = _open_regular_nofollow(database)
        digest = _sha256_fd(descriptor, metadata.st_size)
        if expected_sha256 is not None and not hmac.compare_digest(digest, expected_sha256):
            raise BuilderError("SOURCE_HASH_MISMATCH")
        descriptor_path = Path("/dev/fd") / str(descriptor)
        if not descriptor_path.exists():
            raise BuilderError("SAFE_SOURCE_OPEN_UNSUPPORTED")
        uri = f"file:{quote(str(descriptor_path), safe='/')}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        connection.enable_load_extension(False)
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise BuilderError("SOURCE_QUERY_ONLY_FAILED")
        database_list = connection.execute("PRAGMA database_list").fetchall()
        if len(database_list) != 1 or database_list[0][1] != "main":
            raise BuilderError("SOURCE_DATABASE_ATTACH_REJECTED")
        connection.set_authorizer(
            _source_authorizer_for(readable, allowed_functions=allowed_functions)
        )
        yield connection, metadata, digest
        _reject_sidecars(database)
    except BuilderError:
        raise
    except (OSError, sqlite3.DatabaseError) as exc:
        raise BuilderError("SOURCE_DATABASE_READ_ERROR") from exc
    finally:
        if connection is not None:
            connection.close()
        if descriptor is not None:
            os.close(descriptor)


def _schema_read_allowlist() -> dict[str, set[str]]:
    columns = {"type", "name", "tbl_name", "sql"}
    return {"sqlite_master": set(columns), "sqlite_schema": set(columns)}


def _count_read_allowlist(schema: dict[str, Any]) -> dict[str, set[str]]:
    readable = _schema_read_allowlist()
    for table_name in schema["tables"]:
        readable[table_name] = {""}
    return readable


def _quoted_identifier(value: str) -> str:
    if not value or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for character in value):
        raise BuilderError("CONTRACT_INVALID")
    return '"' + value + '"'


def _object_inventory(connection: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema "
        "WHERE name <> 'sqlite_schema' ORDER BY type,name"
    ).fetchall()


def _table_counts(connection: sqlite3.Connection, schema: dict[str, Any]) -> tuple[tuple[str, int], ...]:
    counts = []
    available = {row[1] for row in _object_inventory(connection) if row[0] == "table"}
    names = list(schema["tables"])
    names.extend(schema["fts_contract"]["object_names"])
    for name in names:
        if name in available and not name.startswith("chat_messages_fts"):
            value = connection.execute(
                f"SELECT count(*) FROM {_quoted_identifier(name)}"
            ).fetchone()[0]
            counts.append((name, value))
    return tuple(counts)


def _directory_inventory(source: Path) -> tuple[tuple[Any, ...], ...]:
    records = []
    for path in [source, *sorted(source.rglob("*"))]:
        metadata = path.lstat()
        relative = "." if path == source else path.relative_to(source).as_posix()
        record: list[Any] = [
            relative,
            stat.S_IFMT(metadata.st_mode),
            stat.S_IMODE(metadata.st_mode),
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_nlink,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        ]
        if (
            stat.S_ISREG(metadata.st_mode)
            and not _is_relative_to(path, source / "artifacts")
            and path.name != "source.sqlite"
        ):
            record.append(_sha256_file(path))
        records.append(tuple(record))
    return tuple(records)


def _snapshot_fixture_source_unchecked(
    source: Path,
    schema: dict[str, Any],
    expected_sha256: str,
) -> dict[str, Any]:
    database = source / "database" / "source.sqlite"
    _reject_sidecars(database)
    with _open_source(
        database,
        readable=_count_read_allowlist(schema),
        allowed_functions=frozenset({"count"}),
        expected_sha256=expected_sha256,
    ) as (connection, metadata, database_digest):
        schema_version = connection.execute("PRAGMA schema_version").fetchone()[0]
        objects = tuple(_object_inventory(connection))
        counts = _table_counts(connection, schema)
    _reject_sidecars(database)
    return {
        "database": (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_nlink,
            metadata.st_size,
            stat.S_IFMT(metadata.st_mode),
            stat.S_IMODE(metadata.st_mode),
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
            database_digest,
        ),
        "schema_version": schema_version,
        "objects": objects,
        "row_counts": counts,
        "directory": _directory_inventory(source),
        "sidecars_absent": True,
    }


def snapshot_fixture_source(source: Path) -> dict[str, Any]:
    root, launcher_sentinel = _fixture_root()
    source = source.resolve(strict=True)
    if not _is_relative_to(source, root / "sources"):
        raise BuilderError("PATH_OUTSIDE_FIXTURE_ROOT")
    provenance = _source_provenance(source, launcher_sentinel)
    _validate_source_tree(source)
    schema = load_schema_contract()
    with _open_source(
        source / "database" / "source.sqlite",
        readable=_schema_read_allowlist(),
        expected_sha256=provenance["database"]["sha256"],
    ) as (connection, _metadata, _digest):
        _validate_source_schema(connection, schema)
    _verify_synthetic_profile(
        source / "database" / "source.sqlite",
        schema,
        provenance,
        launcher_sentinel,
    )
    return _snapshot_fixture_source_unchecked(
        source, schema, provenance["database"]["sha256"]
    )


def _validate_source_schema(connection: sqlite3.Connection, schema: dict[str, Any]) -> bool:
    objects = _object_inventory(connection)
    object_map = {row[1]: row for row in objects}
    base_names = set(schema["allowed_object_names"]["always"])
    fts_names = set(schema["fts_contract"]["object_names"])
    present_fts = fts_names.intersection(object_map)
    if present_fts and present_fts != fts_names:
        raise BuilderError("INCOMPATIBLE_FTS_GROUP")
    allowed = base_names | (fts_names if present_fts else set())
    unknown = set(object_map) - allowed
    if unknown:
        if any(object_map[name][0] == "table" for name in unknown):
            raise BuilderError("UNKNOWN_SOURCE_TABLE")
        raise BuilderError("UNKNOWN_SCHEMA_OBJECT")

    expected_tables = set(schema["tables"])
    actual_tables = {
        row[1]
        for row in objects
        if row[0] == "table" and row[1] not in fts_names and not row[1].startswith("sqlite_")
    }
    extra_tables = actual_tables - expected_tables
    if extra_tables:
        raise BuilderError("UNKNOWN_SOURCE_TABLE")
    if actual_tables != expected_tables:
        raise BuilderError("SCHEMA_VARIANT_REJECTED")

    for table_name, expected in schema["tables"].items():
        table_row = object_map.get(table_name)
        if table_row is None or table_row[0] != "table":
            raise BuilderError("SCHEMA_VARIANT_REJECTED")
        columns = connection.execute(
            f"PRAGMA table_xinfo({_quoted_identifier(table_name)})"
        ).fetchall()
        expected_names = [item["name"] for item in expected["columns"]]
        actual_names = [row[1] for row in columns]
        if set(actual_names) - set(expected_names):
            raise BuilderError("UNKNOWN_SOURCE_COLUMN")
        if actual_names != expected_names:
            raise BuilderError("SCHEMA_VARIANT_REJECTED")
        normalized_columns = [
            {
                "name": row[1],
                "type": row[2],
                "notnull": row[3],
                "default": row[4],
                "pk": row[5],
                "hidden": row[6],
            }
            for row in columns
        ]
        if normalized_columns != expected["columns"]:
            raise BuilderError("SCHEMA_VARIANT_REJECTED")
        if table_row[3] not in expected["raw_sql_variants"]:
            raise BuilderError("SCHEMA_VARIANT_REJECTED")

        foreign_keys = [
            {
                "id": row[0],
                "seq": row[1],
                "table": row[2],
                "from": row[3],
                "to": row[4],
                "on_update": row[5],
                "on_delete": row[6],
                "match": row[7],
            }
            for row in connection.execute(
                f"PRAGMA foreign_key_list({_quoted_identifier(table_name)})"
            ).fetchall()
        ]
        if foreign_keys != expected["foreign_keys"]:
            raise BuilderError("SCHEMA_VARIANT_REJECTED")

        actual_indexes = {
            row[1]: row
            for row in connection.execute(
                f"PRAGMA index_list({_quoted_identifier(table_name)})"
            ).fetchall()
        }
        expected_indexes = {item["name"]: item for item in expected["indexes"]}
        if set(actual_indexes) != set(expected_indexes):
            raise BuilderError("SCHEMA_VARIANT_REJECTED")
        for index_name, expected_index in expected_indexes.items():
            row = actual_indexes[index_name]
            if (row[2], row[3], row[4]) != (
                expected_index["unique"],
                expected_index["origin"],
                expected_index["partial"],
            ):
                raise BuilderError("SCHEMA_VARIANT_REJECTED")
            raw_sql = object_map.get(index_name, (None, None, None, None))[3]
            if raw_sql not in expected_index["raw_sql_variants"]:
                raise BuilderError("SCHEMA_VARIANT_REJECTED")
            terms = [
                {
                    "seqno": term[0],
                    "cid": term[1],
                    "name": term[2],
                    "desc": term[3],
                    "collation": term[4],
                    "key": term[5],
                }
                for term in connection.execute(
                    f"PRAGMA index_xinfo({_quoted_identifier(index_name)})"
                ).fetchall()
            ]
            if terms != expected_index["terms"]:
                raise BuilderError("SCHEMA_VARIANT_REJECTED")

    if present_fts:
        for name, specification in schema["fts_contract"]["objects"].items():
            row = object_map.get(name)
            if row is None or row[0] != specification["type"]:
                raise BuilderError("INCOMPATIBLE_FTS_GROUP")
            if row[3] not in specification["raw_sql_variants"]:
                raise BuilderError("UNKNOWN_SCHEMA_OBJECT")
    return bool(present_fts)


def _synthetic_fixture_proof(launcher_sentinel: str, fixture_id: str) -> str:
    digest = hashlib.sha256(
        ("phase3c\x00" + launcher_sentinel + "\x00" + fixture_id).encode("utf-8")
    ).hexdigest()
    return f"SYNTHETIC_DISPOSABLE_FIXTURE:{digest}"


def _verify_synthetic_profile(
    database: Path,
    schema: dict[str, Any],
    provenance: dict[str, Any],
    launcher_sentinel: str,
) -> None:
    package_contract = load_package_contract()
    profile = package_contract.get("synthetic_fixture_profile")
    expected_counts = profile.get("table_row_counts") if isinstance(profile, dict) else None
    if not isinstance(expected_counts, dict) or set(expected_counts) != set(schema["tables"]):
        raise BuilderError("CONTRACT_INVALID")
    readable = _count_read_allowlist(schema)
    readable["sessions"].update({"id", "mode"})
    with _open_source(
        database,
        readable=readable,
        allowed_functions=frozenset({"count"}),
        expected_sha256=provenance["database"]["sha256"],
    ) as (connection, _metadata, _digest):
        counts = dict(_table_counts(connection, schema))
        if counts != expected_counts:
            raise BuilderError("SYNTHETIC_FIXTURE_PROFILE_MISMATCH")
        proof_rows = connection.execute("SELECT id,mode FROM sessions").fetchall()
    expected_proof = _synthetic_fixture_proof(
        launcher_sentinel, provenance["fixture_id"]
    )
    if len(proof_rows) != 1 or not hmac.compare_digest(
        str(proof_rows[0][1]), expected_proof
    ):
        raise BuilderError("SYNTHETIC_FIXTURE_PROOF_INVALID")


class Aliaser:
    def __init__(self) -> None:
        self._secret = secrets.token_bytes(32)
        self._cache: dict[tuple[str, str], str] = {}
        self._reverse: dict[str, tuple[str, str]] = {}

    def alias(self, domain: str, value: Any, *, nullable: bool = False) -> str | None:
        if value is None:
            if nullable:
                return None
            raise BuilderError("NULL_IDENTIFIER_REJECTED")
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise BuilderError("INVALID_IDENTIFIER")
        if domain not in ALIAS_PREFIXES:
            raise BuilderError("TRANSFORM_CONTRACT_INVALID")
        normalized = _normalize_owner(value) if domain == "owner" else value
        if domain == "owner" and normalized in PINNED_INTERNAL_OWNERS:
            return normalized
        key = (domain, normalized)
        if key not in self._cache:
            digest = hmac.new(
                self._secret,
                domain.encode("ascii") + b"\x00" + normalized.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()[:32]
            alias = f"{ALIAS_PREFIXES[domain]}_{digest}"
            previous = self._reverse.get(alias)
            if previous is not None and previous != key:
                raise BuilderError("ALIAS_COLLISION")
            self._reverse[alias] = key
            self._cache[key] = alias
        return self._cache[key]


def _load_owner_registry(source: Path, aliaser: Aliaser) -> tuple[dict[str, list[str]], dict[str, str]]:
    registry = _exact_keys(
        _load_json(source / "owners.synthetic.json"),
        ("active", "retired", "legacy_approved", "internal"),
        "OWNER_REGISTRY_INVALID",
    )
    if registry["internal"] != list(PINNED_INTERNAL_OWNERS):
        raise BuilderError("INTERNAL_OWNER_POLICY_INVALID")
    classifications: dict[str, str] = {}
    output: dict[str, list[str]] = {}
    for category in ("active", "retired", "legacy_approved"):
        values = registry[category]
        if not isinstance(values, list):
            raise BuilderError("OWNER_REGISTRY_INVALID")
        aliases = []
        for owner in values:
            normalized = _normalize_owner(owner)
            if normalized in PINNED_INTERNAL_OWNERS:
                raise BuilderError("RESERVED_INTERNAL_OWNER")
            if normalized in classifications:
                raise BuilderError("OWNER_REGISTRY_COLLISION")
            classifications[normalized] = category
            aliases.append(aliaser.alias("owner", owner))
        output[category] = sorted(aliases)
    output["internal"] = list(PINNED_INTERNAL_OWNERS)
    classifications.update({owner: "internal" for owner in PINNED_INTERNAL_OWNERS})
    return output, classifications


def _source_artifact(source: Path, relative_value: str) -> tuple[Path, os.stat_result]:
    if not isinstance(relative_value, str):
        raise BuilderError("UPLOAD_MANIFEST_INVALID")
    relative = Path(relative_value)
    if relative.is_absolute() or not relative.parts or relative.parts[0] != "artifacts":
        raise BuilderError("ARTIFACT_PATH_REJECTED")
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise BuilderError("ARTIFACT_PATH_REJECTED")
    path = source / relative
    _ensure_no_symlink_components(path, source)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        raise BuilderError("SYMLINK_REJECTED")
    if not stat.S_ISREG(metadata.st_mode):
        raise BuilderError("SPECIAL_FILE_REJECTED")
    if metadata.st_nlink != 1:
        raise BuilderError("HARD_LINK_REJECTED")
    return path, metadata


def _load_uploads(
    source: Path, aliaser: Aliaser, classifications: dict[str, str]
) -> tuple[list[dict[str, str]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    payload = _load_json(source / "uploads.synthetic.json")
    if not isinstance(payload, list):
        raise BuilderError("UPLOAD_MANIFEST_INVALID")
    output_uploads = []
    artifacts = []
    by_source_id: dict[str, dict[str, Any]] = {}
    paths: set[Path] = set()
    inodes: set[tuple[int, int]] = set()
    for record in payload:
        record = _exact_keys(
            record,
            ("upload_id", "owner", "source_relative_path", "category"),
            "UPLOAD_MANIFEST_INVALID",
        )
        upload_id = record["upload_id"]
        owner = record["owner"]
        category = record["category"]
        if not isinstance(upload_id, str) or not upload_id or upload_id in by_source_id:
            raise BuilderError("DUPLICATE_UPLOAD_IDENTIFIER")
        if category not in {"call-audio", "video-media"}:
            raise BuilderError("UPLOAD_MANIFEST_INVALID")
        normalized_owner = _normalize_owner(owner)
        owner_alias = aliaser.alias("owner", owner)
        upload_alias = aliaser.alias("upload", upload_id)
        path_alias = aliaser.alias("path", upload_id)
        token = f"artifacts/{category}/{path_alias}.placeholder"
        source_path, metadata = _source_artifact(source, record["source_relative_path"])
        inode = (metadata.st_dev, metadata.st_ino)
        if source_path in paths or inode in inodes:
            raise BuilderError("CONFLICTING_ARTIFACT_REUSE")
        paths.add(source_path)
        inodes.add(inode)
        classification = classifications.get(normalized_owner, "unknown")
        output_uploads.append(
            {
                "upload_id": upload_alias,
                "owner": owner_alias,
                "approved_relative_path_token": token,
            }
        )
        item = {
            "approved_relative_path_token": token,
            "artifact_category": category,
            "sanitized_linkage_identifier": upload_alias,
            "source_status": "SYNTHETIC_SOURCE_OBSERVED",
            "observed_source_size": metadata.st_size,
            "destination_size": 0,
            "root_category": "fixture-artifact",
        }
        artifacts.append(item)
        by_source_id[upload_id] = {
            "owner_normalized": normalized_owner,
            "owner_alias": owner_alias,
            "owner_classification": classification,
            "token": token,
            "artifact": item,
        }
    return output_uploads, artifacts, by_source_id


def _source_columns_for_transform(table: dict[str, Any]) -> list[str]:
    columns = []
    for name in table["column_order"]:
        specification = table["columns"][name]
        action = specification["action"]
        if action in {
            "COPY_METADATA",
            "ALIAS_OWNER",
            "ALIAS_IDENTIFIER",
            "PRESERVE_NULL",
            "REJECT_IF_PRESENT",
            "GENERALIZE",
        }:
            columns.append(name)
        elif action == "DERIVE_APPROVED_REFERENCE":
            columns.append(specification["source_column"])
    return list(dict.fromkeys(columns))


def _transform_read_allowlist(transform: dict[str, Any]) -> dict[str, set[str]]:
    return {
        table_name: set(_source_columns_for_transform(transform["tables"][table_name]))
        for table_name in TRANSFORM_TABLE_ORDER
    }


def _copy_metadata(value: Any, specification: dict[str, Any]) -> Any:
    if "allowed_values" in specification:
        if value not in specification["allowed_values"]:
            raise BuilderError("UNAPPROVED_METADATA_VALUE")
        return value
    if specification.get("value_type") == "nonnegative_integer_or_null":
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise BuilderError("UNAPPROVED_METADATA_VALUE")
        return value
    raise BuilderError("TRANSFORM_CONTRACT_INVALID")


def _transform_value(
    column_name: str,
    specification: dict[str, Any],
    row: dict[str, Any],
    aliaser: Aliaser,
    uploads: dict[str, dict[str, Any]],
) -> Any:
    action = specification["action"]
    value = row.get(column_name)
    if action == "COPY_METADATA":
        return _copy_metadata(value, specification)
    if action == "ALIAS_OWNER":
        return aliaser.alias("owner", value, nullable=True)
    if action == "ALIAS_IDENTIFIER":
        return aliaser.alias(
            specification["domain"], value, nullable=bool(specification.get("nullable"))
        )
    if action == "PRESERVE_NULL":
        if value is not None:
            raise BuilderError("PRESERVE_NULL_VIOLATION")
        return None
    if action == "CONSTANT_REDACTED":
        return specification.get("value")
    if action == "REJECT_IF_PRESENT":
        if value is not None:
            raise BuilderError("REJECTED_VALUE_PRESENT")
        return None
    if action == "GENERALIZE":
        if value is None:
            return None
        if not isinstance(value, str):
            raise BuilderError("UNSUPPORTED_NESTED_JSON")
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise BuilderError("UNSUPPORTED_NESTED_JSON") from exc
        if not isinstance(decoded, dict) or not set(decoded).issubset(specification["allowed_keys"]):
            raise BuilderError("UNSUPPORTED_NESTED_JSON")
        return json.dumps(specification["output"], sort_keys=True, separators=(",", ":"))
    if action == "DERIVE_APPROVED_REFERENCE":
        upload_id = row.get(specification["source_column"])
        if upload_id is None:
            raise BuilderError("MISSING_UPLOAD_REFERENCE")
        upload = uploads.get(upload_id)
        if upload is None or upload["artifact"]["artifact_category"] != specification["category"]:
            raise BuilderError("MISSING_UPLOAD_REFERENCE")
        return upload["token"]
    if action == "OMIT":
        raise BuilderError("TRANSFORM_CONTRACT_INVALID")
    raise BuilderError("UNKNOWN_TRANSFORM_ACTION")


def _create_destination_schema(
    connection: sqlite3.Connection, schema: dict[str, Any], include_fts: bool
) -> None:
    for table in schema["tables"].values():
        connection.execute(table["raw_sql_variants"][0])
    for table in schema["tables"].values():
        for index in table["indexes"]:
            raw_sql = index["raw_sql_variants"][0]
            if raw_sql is not None:
                connection.execute(raw_sql)
    if include_fts:
        fts = schema["fts_contract"]["objects"]
        connection.execute(fts["chat_messages_fts"]["raw_sql_variants"][0])
        for name in ("chat_messages_fts_ai", "chat_messages_fts_ad", "chat_messages_fts_au"):
            connection.execute(fts[name]["raw_sql_variants"][0])


def _transform_database(
    source_connection: sqlite3.Connection,
    destination_connection: sqlite3.Connection,
    transform: dict[str, Any],
    aliaser: Aliaser,
    uploads: dict[str, dict[str, Any]],
    classifications: dict[str, str],
) -> dict[str, int]:
    counts = {category: 0 for category in ("active", "retired", "legacy_approved", "internal", "unknown")}
    session_owners: dict[str, str] = {}
    document_owners: dict[str, str] = {}
    workflow_identifiers: set[str] = set()
    transformed_names = {
        name
        for name, table in transform["tables"].items()
        if table["row_policy"] == "TRANSFORM_ROWS"
    }
    if transformed_names != set(TRANSFORM_TABLE_ORDER):
        raise BuilderError("TRANSFORM_CONTRACT_INVALID")
    for table_name in TRANSFORM_TABLE_ORDER:
        table = transform["tables"][table_name]
        source_columns = _source_columns_for_transform(table)
        select_list = ",".join(_quoted_identifier(name) for name in source_columns)
        rows = source_connection.execute(
            f"SELECT {select_list} FROM {_quoted_identifier(table_name)}"
        ).fetchall()
        insert_columns = [
            name
            for name in table["column_order"]
            if table["columns"][name]["action"] != "OMIT"
        ]
        placeholders = ",".join("?" for _ in insert_columns)
        insert_sql = (
            f"INSERT INTO {_quoted_identifier(table_name)} "
            f"({','.join(_quoted_identifier(name) for name in insert_columns)}) VALUES ({placeholders})"
        )
        for values in rows:
            source_row = dict(zip(source_columns, values))
            owner_value = source_row.get("owner")
            normalized: str | None = None
            if owner_value is not None:
                normalized = _normalize_owner(owner_value)
                classification = classifications.get(normalized, "unknown")
                counts[classification] += 1
                upload_id = source_row.get("upload_id")
                if upload_id is not None and upload_id in uploads:
                    if uploads[upload_id]["owner_normalized"] != normalized:
                        raise BuilderError("CROSS_OWNER_UPLOAD_REFERENCE")
            record_id = source_row.get("id")
            if not isinstance(record_id, str) or not record_id:
                raise BuilderError("INVALID_IDENTIFIER")
            if table_name == "sessions":
                if normalized is None:
                    raise BuilderError("EMPTY_GRAPH_OWNER")
                session_owners[record_id] = normalized
            elif table_name == "documents":
                if normalized is None:
                    raise BuilderError("EMPTY_GRAPH_OWNER")
                session_id = source_row.get("session_id")
                if session_id is not None:
                    if session_id not in session_owners:
                        raise BuilderError("MISSING_DOCUMENT_SESSION_REFERENCE")
                    if session_owners[session_id] != normalized:
                        raise BuilderError("CROSS_OWNER_DOCUMENT_SESSION")
                document_owners[record_id] = normalized
            elif table_name in {"marketmatch_calls", "marketmatch_videos"}:
                if normalized is None:
                    raise BuilderError("EMPTY_WORKFLOW_OWNER")
                identifiers = [record_id]
                if table_name == "marketmatch_calls" and source_row.get("workflow_id") is not None:
                    identifiers.append(source_row["workflow_id"])
                for identifier in identifiers:
                    if not isinstance(identifier, str) or not identifier:
                        raise BuilderError("INVALID_IDENTIFIER")
                    if identifier in workflow_identifiers:
                        raise BuilderError("DUPLICATE_WORKFLOW_IDENTIFIER")
                    workflow_identifiers.add(identifier)
                for reference_name in ("document_id", "summary_document_id"):
                    reference = source_row.get(reference_name)
                    if reference is None:
                        continue
                    if reference not in document_owners:
                        raise BuilderError("MISSING_DOCUMENT_REFERENCE")
                    if document_owners[reference] != normalized:
                        raise BuilderError("CROSS_OWNER_DOCUMENT_REFERENCE")
            transformed = [
                _transform_value(name, table["columns"][name], source_row, aliaser, uploads)
                for name in insert_columns
            ]
            try:
                destination_connection.execute(insert_sql, transformed)
            except sqlite3.IntegrityError as exc:
                raise BuilderError(
                    "DESTINATION_CONSTRAINT_" + table_name.upper()
                ) from exc
    return counts


def _create_placeholders(staging: DestinationTree, artifacts: list[dict[str, Any]]) -> None:
    categories: set[str] = set()
    for record in artifacts:
        category = record["artifact_category"]
        if category not in categories:
            staging.mkdir(f"artifacts/{category}")
            categories.add(category)
        token = record["approved_relative_path_token"]
        staging.write_bytes(token, b"", mode=0o400)
        metadata = staging.stat(token)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size != 0:
            raise BuilderError("PLACEHOLDER_CREATION_FAILED")
        record["destination_device"] = metadata.st_dev
        record["destination_inode"] = metadata.st_ino
        record["hard_link_count"] = metadata.st_nlink


def _tree_digest(entries: list[dict[str, Any]]) -> str:
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _expected_stage_paths(artifacts: list[dict[str, Any]]) -> set[str]:
    paths = {
        "database",
        "database/main.sqlite",
        "manifests",
        "manifests/owners.sanitized.json",
        "manifests/uploads.sanitized.json",
        "manifests/artifacts.sanitized.json",
        "manifests/auxiliary-stores.sanitized.json",
        "manifests/transform-attestation.json",
        "provenance",
        "provenance/provenance.json",
        "artifacts",
    }
    for artifact in artifacts:
        token = artifact["approved_relative_path_token"]
        path = Path(token)
        expected = Path()
        for part in path.parts:
            expected /= part
            paths.add(expected.as_posix())
    return paths


def _assert_expected_tree(
    staging: DestinationTree,
    artifacts: list[dict[str, Any]],
    *,
    sealed: bool = False,
) -> None:
    actual = {entry["path"] for entry in staging.entries()}
    expected = _expected_stage_paths(artifacts)
    if sealed:
        expected.add("package-manifest.json")
    if actual != expected:
        raise BuilderError("UNEXPECTED_DESTINATION_FILE")


def _sealed_artifacts(package: DestinationTree) -> list[dict[str, Any]]:
    payload = package.read_json(
        "manifests/artifacts.sanitized.json",
        maximum_bytes=4 * 1024 * 1024,
    )
    if not isinstance(payload, list):
        raise BuilderError("PACKAGE_POLICY_MISMATCH")
    expected_keys = {
        "approved_relative_path_token",
        "artifact_category",
        "sanitized_linkage_identifier",
        "source_status",
        "observed_source_size",
        "destination_size",
        "destination_device",
        "destination_inode",
        "hard_link_count",
        "root_category",
    }
    tokens: set[str] = set()
    linkages: set[str] = set()
    for record in payload:
        if not isinstance(record, dict) or set(record) != expected_keys:
            raise BuilderError("PACKAGE_POLICY_MISMATCH")
        token = record.get("approved_relative_path_token")
        token_path = Path(token) if isinstance(token, str) else Path()
        if (
            token_path.is_absolute()
            or not token_path.parts
            or token_path.parts[0] != "artifacts"
            or any(part in {"", ".", ".."} for part in token_path.parts)
        ):
            raise BuilderError("PACKAGE_POLICY_MISMATCH")
        linkage = record.get("sanitized_linkage_identifier")
        policy_matches = (
            token not in tokens
            and isinstance(linkage, str)
            and linkage.startswith("upload_")
            and linkage not in linkages
            and record.get("artifact_category") in {"call-audio", "video-media"}
            and record.get("source_status") == "SYNTHETIC_SOURCE_OBSERVED"
            and isinstance(record.get("observed_source_size"), int)
            and record["observed_source_size"] >= 0
            and record.get("destination_size") == 0
            and isinstance(record.get("destination_device"), int)
            and isinstance(record.get("destination_inode"), int)
            and record.get("hard_link_count") == 1
            and record.get("root_category") == "fixture-artifact"
        )
        if not policy_matches:
            raise BuilderError("PACKAGE_POLICY_MISMATCH")
        tokens.add(token)
        linkages.add(linkage)
    return payload


def _verify_sealed_tree(
    package: DestinationTree, *, expected_manifest_sha256: str
) -> dict[str, Any]:
    if not isinstance(expected_manifest_sha256, str) or len(expected_manifest_sha256) != 64:
        raise BuilderError("TRUSTED_MANIFEST_DIGEST_REQUIRED")
    manifest = package.read_json(
        "package-manifest.json", maximum_bytes=4 * 1024 * 1024
    )
    manifest = _exact_keys(
        manifest,
        (
            "manifest_version",
            "package_id",
            "source_classification",
            "authorization",
            "rag_status",
            "complete",
            "package_contract_sha256",
            "transform_contract_sha256",
            "schema_contract_sha256",
            "tree_entries",
            "package_tree_sha256",
            "canonical_sha256",
        ),
        "PACKAGE_POLICY_MISMATCH",
    )
    if canonical_digest(manifest) != manifest.get("canonical_sha256"):
        raise BuilderError("PACKAGE_MANIFEST_DIGEST_MISMATCH")
    if not hmac.compare_digest(
        manifest["canonical_sha256"], expected_manifest_sha256
    ):
        raise BuilderError("PACKAGE_MANIFEST_DIGEST_MISMATCH")
    package_id = manifest.get("package_id")
    try:
        valid_package_id = (
            isinstance(package_id, str)
            and len(package_id) == 32
            and len(bytes.fromhex(package_id)) == 16
        )
    except ValueError:
        valid_package_id = False
    policy_matches = (
        manifest["manifest_version"] == 1
        and valid_package_id
        and manifest["source_classification"] == SOURCE_CLASSIFICATION
        and manifest["authorization"] == AUTHORIZATION
        and manifest["rag_status"] == RAG_STATUS
        and manifest["complete"] is False
        and manifest["package_contract_sha256"] == PACKAGE_CONTRACT_SHA256
        and manifest["transform_contract_sha256"] == TRANSFORM_CONTRACT_SHA256
        and manifest["schema_contract_sha256"] == SCHEMA_CONTRACT_SHA256
    )
    if not policy_matches:
        raise BuilderError("PACKAGE_POLICY_MISMATCH")
    entries = package.entries()
    artifacts = _sealed_artifacts(package)
    if {entry["path"] for entry in entries} != (
        _expected_stage_paths(artifacts) | {"package-manifest.json"}
    ):
        raise BuilderError("UNEXPECTED_DESTINATION_FILE")
    entries_without_manifest = [
        entry for entry in entries if entry["path"] != "package-manifest.json"
    ]
    if entries_without_manifest != manifest.get("tree_entries"):
        raise BuilderError("PACKAGE_TREE_DIGEST_MISMATCH")
    if _tree_digest(entries_without_manifest) != manifest.get("package_tree_sha256"):
        raise BuilderError("PACKAGE_TREE_DIGEST_MISMATCH")
    return {
        "verified": True,
        "package_id": package_id,
        "package_manifest_sha256": manifest["canonical_sha256"],
        "package_tree_sha256": manifest["package_tree_sha256"],
    }


def verify_sealed_package(
    package: Path, *, expected_manifest_sha256: str
) -> dict[str, Any]:
    tree = DestinationTree.open_existing(package)
    try:
        return _verify_sealed_tree(
            tree, expected_manifest_sha256=expected_manifest_sha256
        )
    finally:
        tree.close()


def _build_staging(
    staging: DestinationTree,
    source: Path,
    schema: dict[str, Any],
    transform: dict[str, Any],
    include_fts: bool,
    aliaser: Aliaser,
    owners: dict[str, list[str]],
    classifications: dict[str, str],
    uploads_output: list[dict[str, str]],
    artifacts: list[dict[str, Any]],
    uploads_by_source: dict[str, dict[str, Any]],
    provenance: dict[str, Any],
    source_database_size: int,
) -> tuple[str, dict[str, int]]:
    for directory in ("database", "manifests", "provenance", "artifacts"):
        staging.mkdir(directory)
    destination_database_bytes: bytes
    with _open_source(
        source / "database" / "source.sqlite",
        readable=_transform_read_allowlist(transform),
        expected_sha256=provenance["database"]["sha256"],
    ) as (source_connection, _source_metadata, _source_digest):
        destination_connection = sqlite3.connect(":memory:")
        try:
            destination_connection.execute("PRAGMA foreign_keys=ON")
            _create_destination_schema(destination_connection, schema, include_fts)
            counts = _transform_database(
                source_connection,
                destination_connection,
                transform,
                aliaser,
                uploads_by_source,
                classifications,
            )
            destination_connection.commit()
            if destination_connection.execute("PRAGMA freelist_count").fetchone()[0] != 0:
                raise BuilderError("DESTINATION_FREELIST_PRESENT")
            destination_database_bytes = destination_connection.serialize()
        except BaseException:
            destination_connection.rollback()
            raise
        finally:
            destination_connection.close()
    staging.write_bytes("database/main.sqlite", destination_database_bytes)
    _create_placeholders(staging, artifacts)
    staging.write_json("manifests/owners.sanitized.json", owners)
    staging.write_json("manifests/uploads.sanitized.json", uploads_output)
    staging.write_json("manifests/artifacts.sanitized.json", artifacts)
    staging.write_json(
        "manifests/auxiliary-stores.sanitized.json",
        {
            "status": "NOT_INCLUDED_FIXTURE_ONLY",
            "rag_status": RAG_STATUS,
            "direct_chroma_access": False,
        },
    )
    staging.write_json(
        "manifests/transform-attestation.json",
        {
            "contract_sha256": TRANSFORM_CONTRACT_SHA256,
            "owner_classification_counts": counts,
            "source_pages_copied": False,
            "artifact_contents_copied": False,
            "rag_status": RAG_STATUS,
        },
    )
    package_id = secrets.token_hex(16)
    fixture_alias = aliaser.alias("generic-record", provenance["fixture_id"])
    staging.write_json(
        "provenance/provenance.json",
        {
            "package_id": package_id,
            "source_classification": SOURCE_CLASSIFICATION,
            "source_fixture_alias": fixture_alias,
            "source_database_size": source_database_size,
            "source_database_sha256": provenance["database"]["sha256"],
            "schema_contract_sha256": SCHEMA_CONTRACT_SHA256,
            "transform_contract_sha256": TRANSFORM_CONTRACT_SHA256,
            "package_contract_sha256": PACKAGE_CONTRACT_SHA256,
            "repository_commit": "f5da9a4dfef91e3ad6c96c8c218005b72dfa0c46",
            "copy_mechanism": "SELECT_ALLOWLIST_TO_FRESH_FIXTURE_DATABASE",
            "source_pages_copied": False,
            "source_mutation_relevant_fields_unchanged": True,
            "access_time_compared": False,
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "authorization": AUTHORIZATION,
        },
    )
    _assert_expected_tree(staging, artifacts)
    return package_id, counts


def build_fixture_package(
    *, source: str, destination: str, fixture_only: bool
) -> dict[str, Any]:
    if not fixture_only:
        raise BuilderError("FIXTURE_ONLY_FLAG_REQUIRED")
    (
        root,
        source_path,
        destination_path,
        launcher_sentinel,
        destination_parent_identity,
    ) = _approved_paths(source, destination)
    del root
    package_contract = load_package_contract()
    schema = load_schema_contract()
    transform = load_transform_contract()
    if package_contract["usage_policy"]["real_sources"] != "NOT_AUTHORIZED":
        raise BuilderError("CONTRACT_INVALID")
    validate_transform_contract(transform, schema)
    _validate_source_tree(source_path)
    provenance = _source_provenance(source_path, launcher_sentinel)
    database = source_path / "database" / "source.sqlite"
    _reject_sidecars(database)
    with _open_source(
        database,
        readable=_schema_read_allowlist(),
        expected_sha256=provenance["database"]["sha256"],
    ) as (source_connection, _source_metadata, _source_digest):
        include_fts = _validate_source_schema(source_connection, schema)
    _verify_synthetic_profile(
        database, schema, provenance, launcher_sentinel
    )
    before = _snapshot_fixture_source_unchecked(
        source_path, schema, provenance["database"]["sha256"]
    )

    aliaser = Aliaser()
    owners, classifications = _load_owner_registry(source_path, aliaser)
    uploads_output, artifacts, uploads_by_source = _load_uploads(
        source_path, aliaser, classifications
    )
    destination_tree = DestinationTree.create(
        destination_path, expected_parent_identity=destination_parent_identity
    )
    try:
        package_id, _counts = _build_staging(
            destination_tree,
            source_path,
            schema,
            transform,
            include_fts,
            aliaser,
            owners,
            classifications,
            uploads_output,
            artifacts,
            uploads_by_source,
            provenance,
            before["database"][3],
        )
        after = _snapshot_fixture_source_unchecked(
            source_path, schema, provenance["database"]["sha256"]
        )
        if before != after:
            raise BuilderError("SOURCE_CHANGED_DURING_BUILD")
        _assert_expected_tree(destination_tree, artifacts)
        entries = destination_tree.entries()
        package_manifest = {
            "manifest_version": 1,
            "package_id": package_id,
            "source_classification": SOURCE_CLASSIFICATION,
            "authorization": AUTHORIZATION,
            "rag_status": RAG_STATUS,
            "complete": False,
            "package_contract_sha256": PACKAGE_CONTRACT_SHA256,
            "transform_contract_sha256": TRANSFORM_CONTRACT_SHA256,
            "schema_contract_sha256": SCHEMA_CONTRACT_SHA256,
            "tree_entries": entries,
            "package_tree_sha256": _tree_digest(entries),
        }
        package_manifest["canonical_sha256"] = canonical_digest(package_manifest)
        destination_tree.write_json("package-manifest.json", package_manifest)
        _assert_expected_tree(destination_tree, artifacts, sealed=True)
        final_source = _snapshot_fixture_source_unchecked(
            source_path, schema, provenance["database"]["sha256"]
        )
        if before != final_source:
            raise BuilderError("SOURCE_CHANGED_DURING_BUILD")
        if not destination_tree.namespace_matches():
            raise BuilderError("DESTINATION_REPLACED")
        verification = _verify_sealed_tree(
            destination_tree,
            expected_manifest_sha256=package_manifest["canonical_sha256"],
        )
    except BaseException:
        destination_tree.invalidate()
        destination_tree.close()
        raise
    destination_tree.close()
    return {
        "status": "FIXTURE_PACKAGE_CREATED",
        "package_id": package_id,
        "authorization": AUTHORIZATION,
        "rag_status": RAG_STATUS,
        "complete": False,
        "package_manifest_sha256": verification["package_manifest_sha256"],
        "package_tree_sha256": verification["package_tree_sha256"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("--fixture-only", action="store_true")
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    return parser


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
        result = build_fixture_package(
            source=arguments.source,
            destination=arguments.destination,
            fixture_only=arguments.fixture_only,
        )
    except BuilderError as exc:
        print(json.dumps({"status": "ERROR", "code": exc.code}, sort_keys=True), file=stderr)
        return 1
    except Exception:
        print(
            json.dumps({"status": "ERROR", "code": "INTERNAL_BUILD_ERROR"}, sort_keys=True),
            file=stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True), file=stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
