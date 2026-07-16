#!/usr/bin/env python3
"""Fail-closed, import-free inspection of a controlled offline SQLite fixture.

This module deliberately uses only the Python standard library.  The checked-in
contract blocks use against real databases: it is an inspection implementation
and disposable-fixture test vehicle, not authorization to inspect production or
user data.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import stat
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote


OUTCOMES = (
    "EXACT_MATCH",
    "TABLE_ABSENT",
    "COMPATIBLE_ADDITIVE_DIFFERENCE",
    "INCOMPATIBLE_COLUMNS",
    "INCOMPATIBLE_INDEXES",
    "INCOMPATIBLE_CONSTRAINTS",
    "UNKNOWN_OR_UNSUPPORTED",
    "READ_ERROR",
)
PRECEDENCE = {
    "EXACT_MATCH": 0,
    "TABLE_ABSENT": 1,
    "COMPATIBLE_ADDITIVE_DIFFERENCE": 2,
    "INCOMPATIBLE_INDEXES": 3,
    "INCOMPATIBLE_COLUMNS": 4,
    "INCOMPATIBLE_CONSTRAINTS": 5,
    "UNKNOWN_OR_UNSUPPORTED": 6,
    "READ_ERROR": 7,
}
MANIFEST_BASENAME = "combined_9844a2f_6197984.json"
EXPECTED_MANIFEST_SHA256 = "bcdfdb9120a805212e7ee3cd87a9708dd52bfac20681ad7f75cd36f185618ef0"
EXPECTED_UPSTREAM_COMMIT = "9844a2f9a1996b8c8135a9e7bbde6a72f41df5ed"
EXPECTED_MARKETMATCH_COMMIT = "6197984ccec995b630006253cf6eb62c43908d5f"
EXPECTED_CONTRACT_NAME = "odysseus-1.0.2-marketmatch-offline-fixture-contract"
MINIMUM_IMMUTABLE_SQLITE_VERSION = (3, 22, 0)
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
FORBIDDEN_INPUT_KEYS = {
    "api_key",
    "api_token",
    "body",
    "content",
    "credential",
    "credentials",
    "document_body",
    "email_body",
    "media",
    "model_response",
    "password",
    "password_hash",
    "prompt",
    "raw_response",
    "secret",
    "summary",
    "summary_text",
    "token",
    "token_hash",
    "transcript",
    "transcript_text",
}
OWNER_REGISTRY_KEYS = {"active", "retired", "legacy_approved", "internal"}
PINNED_INTERNAL_OWNERS = frozenset({"internal-tool", "api", "demo", "system"})
UPLOAD_KEYS = {"upload_id", "owner", "approved_relative_path_token"}
RAG_KEYS = {
    "owner",
    "source_path_token",
    "call_or_video_id",
    "document_ids",
    "upload_id",
    "chunk_id",
    "collection_or_lane_identifier",
}
GRAPH_PATH_COLUMNS = {
    "marketmatch_calls": (
        "stored_audio_path",
        "transcript_path",
        "summary_path",
        "rag_markdown_path",
    ),
    "marketmatch_videos": (
        "stored_video_path",
        "extracted_audio_path",
        "transcript_path",
        "summary_path",
        "rag_markdown_path",
        "clips_json_path",
    ),
}
GRAPH_REFERENCE_COLUMNS = (
    "id",
    "owner",
    "upload_id",
    "document_id",
    "summary_document_id",
)
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


class InspectionError(Exception):
    """A fail-closed input, read, or non-mutation error."""

    def __init__(self, message: str, code: str = "inspection_error") -> None:
        super().__init__(message)
        self.code = code


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise InspectionError("invalid command-line arguments", "invalid_arguments")


class Reporter:
    """Build a report that cannot expose raw owners or sensitive paths."""

    def __init__(self) -> None:
        self._key = secrets.token_bytes(32)
        self.findings: list[dict[str, Any]] = []
        self.information: list[dict[str, Any]] = []

    @property
    def report_id(self) -> str:
        return hashlib.sha256(self._key).hexdigest()[:16]

    def private_token(self, namespace: str, value: object) -> str:
        payload = f"{namespace}\0{value}".encode("utf-8", "surrogatepass")
        digest = hmac.new(self._key, payload, hashlib.sha256).hexdigest()
        return f"hmac:{namespace}:{digest[:24]}"

    def add(
        self,
        outcome: str,
        code: str,
        subject: str,
        **safe_details: Any,
    ) -> None:
        if outcome not in PRECEDENCE:
            raise AssertionError(f"unsupported outcome {outcome}")
        self.findings.append(
            {
                "outcome": outcome,
                "code": code,
                "subject": subject,
                "details": safe_details,
            }
        )

    def info(self, code: str, subject: str, **safe_details: Any) -> None:
        self.information.append(
            {"code": code, "subject": subject, "details": safe_details}
        )

    def aggregate(self) -> str:
        if not self.findings:
            return "EXACT_MATCH"
        return max(
            (item["outcome"] for item in self.findings),
            key=lambda value: PRECEDENCE[value],
        )

    def document(self, manifest: Mapping[str, Any] | None = None) -> dict[str, Any]:
        result = {
            "report_version": 1,
            "report_id": self.report_id,
            "aggregate_outcome": self.aggregate(),
            "findings": self.findings,
            "information": self.information,
            "privacy": {
                "owner_and_sensitive_path_identifiers": "report-specific keyed HMAC",
                "hmac_key_disclosed": False,
                "access_time_unchanged_claimed": False,
            },
        }
        if manifest:
            result["contract"] = {
                "name": manifest["contract_name"],
                "manifest_version": manifest["manifest_version"],
                "canonical_sha256": manifest["canonical_sha256"],
                "real_database_use": manifest["usage_policy"]["real_database_use"],
            }
        return result


def canonical_manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    canonical = dict(manifest)
    canonical.pop("canonical_sha256", None)
    return (
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _absolute_without_resolution(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def reject_symlink_chain(path: Path, *, require_leaf: bool = True) -> None:
    """Reject every symlink component without resolving through it."""

    absolute = _absolute_without_resolution(path)
    parts = absolute.parts
    current = Path(parts[0])
    for part in parts[1:]:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            if not require_leaf:
                return
            raise InspectionError("required path does not exist", "missing_path")
        if stat.S_ISLNK(metadata.st_mode):
            raise InspectionError("symlinked paths are prohibited", "symlink_rejected")


def approved_root(value: str) -> Path:
    root = _absolute_without_resolution(Path(value))
    reject_symlink_chain(root)
    metadata = root.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise InspectionError("offline root is not a directory", "invalid_root")
    return root


def relative_under(root: Path, value: str, *, label: str, must_exist: bool = True) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        raise InspectionError(f"{label} must be relative", "absolute_path_rejected")
    if not candidate.parts or any(part in ("", ".", "..") for part in candidate.parts):
        raise InspectionError(f"{label} contains traversal or an empty component", "traversal_rejected")
    joined = _absolute_without_resolution(root / candidate)
    try:
        confined = os.path.commonpath((os.fspath(root), os.fspath(joined))) == os.fspath(root)
    except ValueError:
        confined = False
    if not confined:
        raise InspectionError(f"{label} escapes the approved root", "traversal_rejected")
    reject_symlink_chain(joined, require_leaf=must_exist)
    return joined


def load_json_file(path: Path, *, label: str) -> Any:
    reject_symlink_chain(path)
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise InspectionError(f"{label} must be a single-link regular file", "invalid_metadata_file")
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InspectionError(f"unable to read {label}: {type(exc).__name__}", "metadata_read_error") from exc


def metadata_file_snapshot(path: Path) -> dict[str, Any]:
    reject_symlink_chain(path)
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise InspectionError("inspected metadata must be a single-link regular file", "invalid_metadata_file")
    parent = path.parent.lstat()
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "file_type": stat.S_IFMT(metadata.st_mode),
        "link_count": metadata.st_nlink,
        "size": metadata.st_size,
        "mode": stat.S_IMODE(metadata.st_mode),
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
        "birthtime_ns": getattr(metadata, "st_birthtime_ns", None),
        "atime_ns_observed": metadata.st_atime_ns,
        "sha256": sha256_file(path),
        "directory_device": parent.st_dev,
        "directory_inode": parent.st_ino,
        "directory_mtime_ns": parent.st_mtime_ns,
        "directory_ctime_ns": parent.st_ctime_ns,
        "directory_entries": tuple(sorted(os.listdir(path.parent))),
    }


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = load_json_file(path, label="contract manifest")
    if not isinstance(manifest, dict):
        raise InspectionError("contract manifest is not an object", "invalid_manifest")
    required = {
        "manifest_version",
        "contract_name",
        "source_snapshots",
        "usage_policy",
        "canonical_form",
        "canonical_sha256",
        "expected_upstream_tables",
        "expected_marketmatch_tables",
        "allowed_object_names",
        "tables",
        "fts_contract",
        "owner_inventory",
    }
    if set(manifest) != required:
        raise InspectionError("contract manifest keys do not match the versioned contract", "invalid_manifest")
    expected_digest = hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()
    if not hmac.compare_digest(expected_digest, str(manifest["canonical_sha256"])):
        raise InspectionError("contract manifest canonical digest mismatch", "manifest_digest_mismatch")
    if not hmac.compare_digest(expected_digest, EXPECTED_MANIFEST_SHA256):
        raise InspectionError("contract manifest is not the pinned checked-in contract", "manifest_not_pinned")
    if manifest["manifest_version"] != 1:
        raise InspectionError("unsupported manifest version", "invalid_manifest")
    if manifest["contract_name"] != EXPECTED_CONTRACT_NAME or manifest["source_snapshots"] != {
        "upstream": EXPECTED_UPSTREAM_COMMIT,
        "marketmatch_checkpoint": EXPECTED_MARKETMATCH_COMMIT,
    }:
        raise InspectionError("contract manifest source identity mismatch", "invalid_manifest")
    if manifest["usage_policy"].get("real_database_use") != "BLOCKED_PENDING_RAW_SQL_REVIEW":
        raise InspectionError("this inspector only accepts the blocked fixture contract", "invalid_manifest_policy")
    upstream = set(manifest["expected_upstream_tables"])
    marketmatch = set(manifest["expected_marketmatch_tables"])
    tables = set(manifest["tables"])
    if upstream | marketmatch != tables or upstream & marketmatch:
        raise InspectionError("manifest table sets are inconsistent", "invalid_manifest")
    for table_name, contract in manifest["tables"].items():
        _require_identifier(table_name)
        if not contract.get("raw_sql_variants"):
            raise InspectionError("every table requires an explicit raw SQL variant", "invalid_manifest")
    return manifest


def _reject_sensitive_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str) or key.lower() in FORBIDDEN_INPUT_KEYS:
                raise InspectionError("sanitized input contains a prohibited key", "sensitive_key_rejected")
            _reject_sensitive_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_sensitive_keys(nested)


def _string(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise InspectionError(f"{label} must be a non-empty string", "invalid_sanitized_manifest")
    return value


def load_owner_registry(path: Path | None) -> dict[str, list[str]]:
    if path is None:
        return {key: [] for key in OWNER_REGISTRY_KEYS}
    data = load_json_file(path, label="sanitized owner registry")
    _reject_sensitive_keys(data)
    if not isinstance(data, dict) or set(data) != OWNER_REGISTRY_KEYS:
        raise InspectionError("owner registry keys are not allowed", "unexpected_key")
    result: dict[str, list[str]] = {}
    for category, values in data.items():
        if not isinstance(values, list):
            raise InspectionError("owner registry values must be lists", "invalid_sanitized_manifest")
        result[category] = [_string(value, f"{category} owner") for value in values]
    return result


def validate_owner_registry(
    registry: Mapping[str, Sequence[str]], manifest: Mapping[str, Any]
) -> dict[str, list[str]]:
    approved_internal = {
        normalize_owner(value)
        for value in manifest["owner_inventory"]["internal_owner_values"]
    }
    if approved_internal != PINNED_INTERNAL_OWNERS:
        raise InspectionError(
            "contract manifest internal owner policy is not pinned",
            "invalid_manifest_policy",
        )
    supplied_internal = {normalize_owner(value) for value in registry["internal"]}
    if not supplied_internal <= approved_internal:
        raise InspectionError(
            "owner registry contains an unapproved internal owner sentinel",
            "unapproved_internal_owner",
        )
    misplaced_internal = approved_internal.intersection(
        normalize_owner(value)
        for category in ("active", "retired", "legacy_approved")
        for value in registry[category]
    )
    if misplaced_internal:
        raise InspectionError(
            "reserved internal owners may appear only in the internal category",
            "internal_owner_category_mismatch",
        )
    resolved = {category: list(values) for category, values in registry.items()}
    # Internal sentinels are a pinned application policy, not operator input.
    # Deriving the complete set prevents an omitted sentinel from becoming an
    # apparently active, legacy, or unknown private MarketMatch owner.
    resolved["internal"] = sorted(PINNED_INTERNAL_OWNERS)
    return resolved


def _load_record_list(path: Path | None, label: str, keys: set[str]) -> list[dict[str, Any]]:
    if path is None:
        return []
    data = load_json_file(path, label=label)
    _reject_sensitive_keys(data)
    if not isinstance(data, list):
        raise InspectionError(f"{label} must be a list", "invalid_sanitized_manifest")
    records: list[dict[str, Any]] = []
    for record in data:
        if not isinstance(record, dict) or set(record) != keys:
            raise InspectionError(f"{label} contains unexpected keys", "unexpected_key")
        records.append(record)
    return records


def require_sanitized_filename(path: Path | None, label: str) -> Path | None:
    if path is not None and not path.name.endswith(".sanitized.json"):
        raise InspectionError(
            f"{label} must use the explicit .sanitized.json suffix",
            "unsanitized_filename_rejected",
        )
    return path


def load_upload_manifest(path: Path | None) -> list[dict[str, str]]:
    records = _load_record_list(path, "sanitized upload manifest", UPLOAD_KEYS)
    result = []
    for record in records:
        result.append(
            {
                "upload_id": _string(record["upload_id"], "upload_id"),
                "owner": _string(record["owner"], "upload owner", allow_empty=True),
                "approved_relative_path_token": _string(
                    record["approved_relative_path_token"], "approved relative path token"
                ),
            }
        )
    return result


def load_rag_manifest(path: Path | None) -> list[dict[str, Any]]:
    records = _load_record_list(path, "sanitized RAG manifest", RAG_KEYS)
    result = []
    for record in records:
        document_ids = record["document_ids"]
        if not isinstance(document_ids, list):
            raise InspectionError("RAG document_ids must be a list", "invalid_sanitized_manifest")
        result.append(
            {
                "owner": _string(record["owner"], "RAG owner", allow_empty=True),
                "source_path_token": _string(record["source_path_token"], "RAG source path token"),
                "call_or_video_id": _string(record["call_or_video_id"], "RAG workflow id"),
                "document_ids": [_string(value, "RAG document id") for value in document_ids],
                "upload_id": _string(record["upload_id"], "RAG upload id"),
                "chunk_id": _string(record["chunk_id"], "RAG chunk id"),
                "collection_or_lane_identifier": _string(
                    record["collection_or_lane_identifier"], "RAG lane"
                ),
            }
        )
    return result


def _require_identifier(value: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise InspectionError("manifest contains an invalid SQL identifier", "invalid_manifest")
    return value


def _quote_identifier(value: str) -> str:
    return '"' + _require_identifier(value) + '"'


def database_snapshot(path: Path) -> dict[str, Any]:
    reject_symlink_chain(path)
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise InspectionError("database is not a regular file", "invalid_database_file")
    if metadata.st_nlink != 1:
        raise InspectionError("database hard links are prohibited", "hardlink_rejected")
    sidecars = []
    for suffix in SIDECAR_SUFFIXES:
        candidate = Path(os.fspath(path) + suffix)
        if candidate.exists() or candidate.is_symlink():
            sidecars.append(suffix)
    if sidecars:
        raise InspectionError("SQLite sidecar files are prohibited", "sidecar_rejected")
    parent_metadata = path.parent.lstat()
    entries = tuple(sorted(os.listdir(path.parent)))
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "file_type": stat.S_IFMT(metadata.st_mode),
        "size": metadata.st_size,
        "mode": stat.S_IMODE(metadata.st_mode),
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
        "birthtime_ns": getattr(metadata, "st_birthtime_ns", None),
        "atime_ns_observed": metadata.st_atime_ns,
        "sha256": sha256_file(path),
        "directory_device": parent_metadata.st_dev,
        "directory_inode": parent_metadata.st_ino,
        "directory_mtime_ns": parent_metadata.st_mtime_ns,
        "directory_ctime_ns": parent_metadata.st_ctime_ns,
        "directory_entries": entries,
        "sidecars": tuple(sidecars),
    }


def mutation_relevant_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in snapshot.items() if key != "atime_ns_observed"}


def artifact_path_snapshot(path: Path) -> dict[str, Any]:
    """Record metadata only; never open or hash an artifact."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        ancestor = path.parent
        while True:
            try:
                ancestor_metadata = ancestor.lstat()
                break
            except FileNotFoundError:
                if ancestor == ancestor.parent:
                    raise InspectionError("artifact ancestor is unreadable", "artifact_snapshot_error")
                ancestor = ancestor.parent
        return {
            "exists": False,
            "nearest_ancestor_device": ancestor_metadata.st_dev,
            "nearest_ancestor_inode": ancestor_metadata.st_ino,
            "nearest_ancestor_mode": stat.S_IMODE(ancestor_metadata.st_mode),
            "nearest_ancestor_mtime_ns": ancestor_metadata.st_mtime_ns,
            "nearest_ancestor_ctime_ns": ancestor_metadata.st_ctime_ns,
            "nearest_ancestor_entries": tuple(sorted(os.listdir(ancestor)))
            if stat.S_ISDIR(ancestor_metadata.st_mode)
            else None,
        }
    parent = path.parent.lstat()
    return {
        "exists": True,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "file_type": stat.S_IFMT(metadata.st_mode),
        "link_count": metadata.st_nlink,
        "size": metadata.st_size,
        "mode": stat.S_IMODE(metadata.st_mode),
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
        "birthtime_ns": getattr(metadata, "st_birthtime_ns", None),
        "atime_ns_observed": metadata.st_atime_ns,
        "parent_device": parent.st_dev,
        "parent_inode": parent.st_ino,
        "parent_mtime_ns": parent.st_mtime_ns,
        "parent_ctime_ns": parent.st_ctime_ns,
        "parent_entries": tuple(sorted(os.listdir(path.parent))),
    }


def _authorizer(manifest: Mapping[str, Any]):
    contract_tables = set(manifest["tables"])
    contract_indexes = {
        index["name"]
        for table in manifest["tables"].values()
        for index in table["indexes"]
    }
    readable: dict[str, set[str]] = {
        "sqlite_master": {"type", "name", "tbl_name", "rootpage", "sql"},
        "sqlite_schema": {"type", "name", "tbl_name", "rootpage", "sql"},
        "pragma_table_list": {"schema", "name", "type", "ncol", "wr", "strict"},
        "pragma_table_xinfo": {
            "cid",
            "name",
            "type",
            "notnull",
            "dflt_value",
            "pk",
            "hidden",
        },
        "pragma_foreign_key_list": {
            "id",
            "seq",
            "table",
            "from",
            "to",
            "on_update",
            "on_delete",
            "match",
        },
        "pragma_index_list": {"seq", "name", "unique", "origin", "partial"},
        "pragma_index_xinfo": {
            "seqno",
            "cid",
            "name",
            "desc",
            "coll",
            "key",
        },
    }
    for table, columns in manifest["owner_inventory"]["read_columns"].items():
        readable[table] = set(columns)
    pragma_functions = {
        "pragma_table_list",
        "pragma_table_xinfo",
        "pragma_foreign_key_list",
        "pragma_index_list",
        "pragma_index_xinfo",
    }
    scalar_pragmas = {
        "query_only": {None, "ON", "1"},
        "database_list": {None},
        "schema_version": {None},
    }

    def authorize(action: int, arg1: str | None, arg2: str | None, database: str | None, source: str | None) -> int:
        del source
        if action in (sqlite3.SQLITE_SELECT, getattr(sqlite3, "SQLITE_RECURSIVE", -999)):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            table = arg1 or ""
            column = arg2 or ""
            if database in ("main", None) and table in readable and column in readable[table]:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            function_name = (arg2 or arg1 or "").lower()
            return sqlite3.SQLITE_OK if function_name in pragma_functions else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA:
            pragma = (arg1 or "").lower()
            normalized_arg = arg2.upper() if isinstance(arg2, str) else None
            values = scalar_pragmas.get(pragma)
            if values is not None and normalized_arg in values:
                return sqlite3.SQLITE_OK
            if pragma == "table_list" and arg2 is None:
                return sqlite3.SQLITE_OK
            if pragma in {"table_xinfo", "foreign_key_list", "index_list"} and arg2 in contract_tables:
                return sqlite3.SQLITE_OK
            if pragma == "index_xinfo" and arg2 in contract_indexes:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    return authorize


def open_read_only(path: Path, manifest: Mapping[str, Any]) -> sqlite3.Connection:
    if sqlite3.sqlite_version_info < MINIMUM_IMMUTABLE_SQLITE_VERSION:
        raise InspectionError(
            "SQLite is too old to guarantee immutable URI support",
            "sqlite_immutable_unsupported",
        )
    uri_path = quote(os.fspath(path), safe="/")
    connection = sqlite3.connect(
        f"file:{uri_path}?mode=ro&immutable=1",
        uri=True,
        isolation_level=None,
    )
    try:
        connection.enable_load_extension(False)
        connection.set_authorizer(_authorizer(manifest))
        connection.execute("PRAGMA query_only=ON")
        query_only = connection.execute("PRAGMA query_only").fetchone()
        if query_only != (1,):
            raise InspectionError("SQLite query_only could not be verified", "query_only_failed")
        databases = connection.execute("PRAGMA database_list").fetchall()
        if len(databases) != 1 or databases[0][1] != "main":
            raise InspectionError("SQLite connection contains a non-main database", "attached_database_rejected")
        opened_path = databases[0][2]
        if not isinstance(opened_path, str) or not opened_path:
            raise InspectionError("SQLite did not report the opened main database path", "database_identity_error")
        if _absolute_without_resolution(Path(opened_path)) != _absolute_without_resolution(path):
            raise InspectionError("SQLite main database path differs from the approved path", "database_identity_error")
        return connection
    except BaseException:
        connection.close()
        raise


OBJECT_SQL = "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"
TABLE_LIST_SQL = "SELECT schema, name, type, ncol, wr, strict FROM pragma_table_list ORDER BY schema, name"
COLUMN_SQL = 'SELECT cid, name, type, "notnull", dflt_value, pk, hidden FROM pragma_table_xinfo(?) ORDER BY cid'
FOREIGN_KEY_SQL = (
    'SELECT id, seq, "table", "from", "to", on_update, on_delete, match '
    "FROM pragma_foreign_key_list(?) ORDER BY id, seq"
)
INDEX_LIST_SQL = (
    'SELECT seq, name, "unique", origin, partial FROM pragma_index_list(?) ORDER BY name'
)
INDEX_XINFO_SQL = (
    "SELECT seqno, cid, name, desc, coll, key FROM pragma_index_xinfo(?) ORDER BY seqno"
)


def partial_index_predicate(raw_sql: object) -> str | None:
    if not isinstance(raw_sql, str):
        return None
    match = re.search(r"\bWHERE\b(?P<predicate>.*)\Z", raw_sql, re.IGNORECASE | re.DOTALL)
    return match.group("predicate").strip() if match else None


def object_inventory(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {"type": row[0], "name": row[1], "table": row[2], "sql": row[3]}
        for row in connection.execute(OBJECT_SQL)
    ]


def schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA schema_version").fetchone()
    if not row or not isinstance(row[0], int):
        raise InspectionError("SQLite schema_version is unreadable", "schema_version_error")
    return row[0]


def collect_schema(
    connection: sqlite3.Connection, manifest: Mapping[str, Any]
) -> dict[str, Any]:
    objects = object_inventory(connection)
    object_by_name = {item["name"]: item for item in objects}
    table_list = [tuple(row) for row in connection.execute(TABLE_LIST_SQL)]
    tables: dict[str, Any] = {}
    for item in objects:
        if item["type"] != "table" or item["name"] not in manifest["tables"]:
            continue
        name = item["name"]
        columns = [
            {
                "cid": row[0],
                "name": row[1],
                "type": row[2],
                "notnull": row[3],
                "default": row[4],
                "pk": row[5],
                "hidden": row[6],
            }
            for row in connection.execute(COLUMN_SQL, (name,))
        ]
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
            for row in connection.execute(FOREIGN_KEY_SQL, (name,))
        ]
        indexes = []
        for row in connection.execute(INDEX_LIST_SQL, (name,)):
            index_name = row[1]
            expected_index_names = {
                index["name"] for index in manifest["tables"][name]["indexes"]
            }
            terms = []
            if index_name in expected_index_names:
                terms = [
                    {
                        "seqno": detail[0],
                        "cid": detail[1],
                        "name": detail[2],
                        "desc": detail[3],
                        "collation": detail[4],
                        "key": detail[5],
                    }
                    for detail in connection.execute(INDEX_XINFO_SQL, (index_name,))
                ]
            indexes.append(
                {
                    "name": index_name,
                    "unique": row[2],
                    "origin": row[3],
                    "partial": row[4],
                    "terms": terms,
                    "sql": object_by_name.get(index_name, {}).get("sql"),
                    "predicate": partial_index_predicate(
                        object_by_name.get(index_name, {}).get("sql")
                    ),
                }
            )
        tables[name] = {
            "raw_sql": item["sql"],
            "columns": columns,
            "foreign_keys": foreign_keys,
            "indexes": indexes,
        }
    return {"objects": objects, "table_list": table_list, "tables": tables}


def _column_signature(column: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        column["name"],
        column["type"],
        int(column["notnull"]),
        column.get("default"),
        int(column["pk"]),
        int(column.get("hidden", 0)),
    )


def _fk_signature(foreign_key: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        foreign_key["id"],
        foreign_key["seq"],
        foreign_key["table"],
        foreign_key["from"],
        foreign_key["to"],
        foreign_key["on_update"],
        foreign_key["on_delete"],
        foreign_key["match"],
    )


def _term_signature(term: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        term["seqno"],
        term["cid"],
        term.get("name"),
        int(term["desc"]),
        term.get("collation"),
        int(term["key"]),
    )


def compare_schema(schema: Mapping[str, Any], manifest: Mapping[str, Any], reporter: Reporter) -> None:
    objects = {item["name"]: item for item in schema["objects"]}
    allowed = set(manifest["allowed_object_names"]["always"])
    fts_names = set(manifest["allowed_object_names"]["fts_optional_group"])
    expected_types = {name: "table" for name in manifest["tables"]}
    for contract in manifest["tables"].values():
        expected_types.update({item["name"]: "index" for item in contract["indexes"]})
    expected_types.update(
        {name: item["type"] for name, item in manifest["fts_contract"]["objects"].items()}
    )
    present_fts = fts_names & set(objects)
    if present_fts:
        allowed |= fts_names
    for name, item in objects.items():
        if name not in allowed:
            reporter.add(
                "UNKNOWN_OR_UNSUPPORTED",
                "unexpected_sqlite_object",
                reporter.private_token("sqlite-object", name),
                object_type=item["type"],
            )
        elif name in expected_types and item["type"] != expected_types[name]:
            reporter.add(
                "UNKNOWN_OR_UNSUPPORTED",
                "sqlite_namespace_collision",
                reporter.private_token("sqlite-object", name),
                expected_type=expected_types[name],
                actual_type=item["type"],
            )
    table_names = set(manifest["tables"])
    for table_name in sorted(table_names):
        contract = manifest["tables"][table_name]
        collision = objects.get(table_name)
        if collision is None:
            reporter.add("TABLE_ABSENT", "table_absent", table_name, origin=contract["origin"])
            continue
        if collision["type"] != "table":
            reporter.add(
                "UNKNOWN_OR_UNSUPPORTED",
                "table_namespace_collision",
                table_name,
                actual_type=collision["type"],
            )
            continue
        actual = schema["tables"].get(table_name)
        if actual is None:
            reporter.add("UNKNOWN_OR_UNSUPPORTED", "virtual_table_collision", table_name)
            continue
        if actual["raw_sql"] not in contract["raw_sql_variants"]:
            reporter.add("UNKNOWN_OR_UNSUPPORTED", "unknown_raw_table_sql", table_name)
        expected_columns = [_column_signature(item) for item in contract["columns"]]
        actual_columns = [_column_signature(item) for item in actual["columns"]]
        if actual_columns != expected_columns:
            reporter.add(
                "INCOMPATIBLE_COLUMNS",
                "column_contract_difference",
                table_name,
                expected_count=len(expected_columns),
                actual_count=len(actual_columns),
            )
        expected_fks = [_fk_signature(item) for item in contract["foreign_keys"]]
        actual_fks = [_fk_signature(item) for item in actual["foreign_keys"]]
        if actual_fks != expected_fks:
            reporter.add("INCOMPATIBLE_CONSTRAINTS", "foreign_key_difference", table_name)
        _compare_indexes(table_name, contract["indexes"], actual["indexes"], reporter)
    _compare_fts(objects, manifest["fts_contract"], reporter)

    upstream = set(manifest["expected_upstream_tables"])
    marketmatch = set(manifest["expected_marketmatch_tables"])
    actual_tables = set(schema["tables"])
    upstream_ready = upstream <= actual_tables and not any(
        item["subject"] in upstream
        and item["outcome"] not in ("EXACT_MATCH", "TABLE_ABSENT")
        for item in reporter.findings
    )
    mm_present = marketmatch & actual_tables
    if upstream_ready and mm_present != marketmatch:
        reporter.add(
            "COMPATIBLE_ADDITIVE_DIFFERENCE",
            "marketmatch_additive_candidate",
            "marketmatch_tables",
            present_count=len(mm_present),
            absent_count=len(marketmatch - mm_present),
        )


def _compare_indexes(
    table_name: str,
    expected_indexes: Sequence[Mapping[str, Any]],
    actual_indexes: Sequence[Mapping[str, Any]],
    reporter: Reporter,
) -> None:
    expected = {item["name"]: item for item in expected_indexes}
    actual = {item["name"]: item for item in actual_indexes}
    for name in sorted(set(expected) - set(actual)):
        constraint_index = expected[name]["origin"] in {"pk", "u"}
        reporter.add(
            "INCOMPATIBLE_CONSTRAINTS" if constraint_index else "INCOMPATIBLE_INDEXES",
            "missing_constraint_index" if constraint_index else "missing_index",
            table_name,
            index=name,
        )
    for name in sorted(set(actual) - set(expected)):
        if actual[name]["origin"] in {"pk", "u"}:
            reporter.add(
                "INCOMPATIBLE_CONSTRAINTS",
                "unexpected_constraint_index",
                table_name,
                index=reporter.private_token("index", name),
            )
        reporter.add(
            "UNKNOWN_OR_UNSUPPORTED",
            "unexpected_index",
            table_name,
            index=reporter.private_token("index", name),
        )
    for name in sorted(set(expected) & set(actual)):
        wanted = expected[name]
        found = actual[name]
        if int(wanted["unique"]) != int(found["unique"]):
            reporter.add("INCOMPATIBLE_CONSTRAINTS", "index_uniqueness_difference", table_name, index=name)
        if wanted["origin"] != found["origin"]:
            reporter.add("INCOMPATIBLE_CONSTRAINTS", "index_origin_difference", table_name, index=name)
        if int(wanted["partial"]) != int(found["partial"]):
            reporter.add("INCOMPATIBLE_INDEXES", "partial_index_difference", table_name, index=name)
        expected_predicates = {
            partial_index_predicate(raw_sql)
            for raw_sql in wanted["raw_sql_variants"]
        }
        if found["predicate"] not in expected_predicates:
            reporter.add(
                "INCOMPATIBLE_INDEXES",
                "partial_index_predicate_difference",
                table_name,
                index=name,
            )
        if [_term_signature(item) for item in wanted["terms"]] != [
            _term_signature(item) for item in found["terms"]
        ]:
            reporter.add("INCOMPATIBLE_INDEXES", "index_term_difference", table_name, index=name)
        if found["sql"] not in wanted["raw_sql_variants"]:
            reporter.add("UNKNOWN_OR_UNSUPPORTED", "unknown_raw_index_sql", table_name, index=name)


def _compare_fts(objects: Mapping[str, Mapping[str, Any]], contract: Mapping[str, Any], reporter: Reporter) -> None:
    group = set(contract["object_names"])
    present = group & set(objects)
    if not present:
        reporter.info("fts_group_absent", "chat_messages_fts", state="fully_absent")
        return
    if present != group:
        reporter.add(
            "INCOMPATIBLE_CONSTRAINTS",
            "partial_fts_group",
            "chat_messages_fts",
            present_count=len(present),
            expected_count=len(group),
        )
        return
    expected = contract["objects"]
    for name in sorted(group):
        item = objects[name]
        wanted = expected[name]
        if item["type"] != wanted["type"]:
            reporter.add("UNKNOWN_OR_UNSUPPORTED", "unknown_fts_object_type", name)
        if item["sql"] not in wanted["raw_sql_variants"]:
            reporter.add("UNKNOWN_OR_UNSUPPORTED", "unknown_fts_raw_sql", name)
    reporter.info("fts_group_present", "chat_messages_fts", state="fully_present")


def normalize_owner(value: object) -> str:
    return str(value or "").strip().lower()


def owner_sets(registry: Mapping[str, Sequence[str]]) -> tuple[dict[str, set[str]], set[str]]:
    normalized = {
        category: {normalize_owner(value) for value in values}
        for category, values in registry.items()
    }
    memberships: dict[str, int] = {}
    for raw_values in registry.values():
        for raw_owner in raw_values:
            owner = normalize_owner(raw_owner)
            memberships[owner] = memberships.get(owner, 0) + 1
    collisions = {owner for owner, count in memberships.items() if count > 1}
    return normalized, collisions


def classify_owner(owner: object, registry: Mapping[str, set[str]], collisions: set[str]) -> str:
    value = normalize_owner(owner)
    if not value:
        return "empty-owner"
    if value in collisions:
        return "collision"
    if value in registry["internal"]:
        return "internal-owner"
    if value in registry["active"]:
        return "active-owner"
    if value in registry["retired"]:
        return "retired-owner"
    if value in registry["legacy_approved"]:
        return "legacy-owner"
    return "unknown-owner"


def _read_rows(
    connection: sqlite3.Connection,
    table: str,
    columns: Sequence[str],
) -> list[dict[str, Any]]:
    select = ", ".join(_quote_identifier(column) for column in columns)
    sql = f"SELECT {select} FROM {_quote_identifier(table)}"
    return [dict(zip(columns, row)) for row in connection.execute(sql)]


def inventory_owners(
    connection: sqlite3.Connection,
    schema: Mapping[str, Any],
    manifest: Mapping[str, Any],
    registry: Mapping[str, Sequence[str]],
    reporter: Reporter,
) -> dict[str, list[dict[str, Any]]]:
    sets, collisions = owner_sets(registry)
    for owner in sorted(collisions):
        reporter.add(
            "INCOMPATIBLE_CONSTRAINTS",
            "owner_registry_collision",
            reporter.private_token("owner", owner),
        )
    rows_by_table: dict[str, list[dict[str, Any]]] = {}
    for table, columns in manifest["owner_inventory"]["read_columns"].items():
        actual = schema["tables"].get(table)
        if actual is None:
            continue
        present_columns = {item["name"] for item in actual["columns"]}
        if not set(columns) <= present_columns:
            reporter.add("UNKNOWN_OR_UNSUPPORTED", "owner_inventory_columns_missing", table)
            continue
        try:
            rows = _read_rows(connection, table, columns)
        except sqlite3.Error as exc:
            raise InspectionError(
                f"owner inventory read failed: {type(exc).__name__}", "owner_inventory_read_error"
            ) from exc
        rows_by_table[table] = rows
        for row in rows:
            classification = classify_owner(row.get("owner"), sets, collisions)
            if classification == "active-owner":
                continue
            token = reporter.private_token("owner", normalize_owner(row.get("owner")))
            row_token = reporter.private_token(f"{table}-row", row.get(columns[0]))
            informational = (
                classification in ("legacy-owner", "internal-owner")
                and table not in {"marketmatch_calls", "marketmatch_videos"}
            )
            if informational:
                reporter.info(
                    "owner_classification",
                    row_token,
                    table=table,
                    owner=token,
                    classification=classification,
                )
            else:
                reporter.add(
                    "INCOMPATIBLE_CONSTRAINTS",
                    "owner_classification",
                    row_token,
                    table=table,
                    owner=token,
                    classification=classification,
                )
    return rows_by_table


def _index_unique_records(
    records: Sequence[Mapping[str, Any]],
    key: str,
    kind: str,
    reporter: Reporter,
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for record in records:
        value = str(record[key])
        if value in result:
            reporter.add(
                "INCOMPATIBLE_CONSTRAINTS",
                f"duplicate_{kind}",
                reporter.private_token(kind, value),
            )
        else:
            result[value] = record
    return result


def _owner_link(
    workflow_owner: object,
    linked_owner: object,
    link_kind: str,
    workflow_token: str,
    reporter: Reporter,
) -> None:
    if normalize_owner(workflow_owner) != normalize_owner(linked_owner):
        reporter.add(
            "INCOMPATIBLE_CONSTRAINTS",
            "cross_owner_link",
            workflow_token,
            link_kind=link_kind,
            workflow_owner=reporter.private_token("owner", normalize_owner(workflow_owner)),
            linked_owner=reporter.private_token("owner", normalize_owner(linked_owner)),
        )


def _artifact_metadata(
    offline_root: Path,
    artifact_roots: Sequence[Path],
    token: object,
    workflow_token: str,
    path_kind: str,
    reporter: Reporter,
    observations: dict[Path, dict[str, Any]],
) -> tuple[str, tuple[int, int] | None] | None:
    if token is None or token == "":
        return None
    if not isinstance(token, str):
        reporter.add("UNKNOWN_OR_UNSUPPORTED", "non_string_path_token", workflow_token, path_kind=path_kind)
        return None
    private_path = reporter.private_token("path", token)
    try:
        path = relative_under(offline_root, token, label=path_kind, must_exist=False)
    except InspectionError as exc:
        code = (
            "missing_or_unsafe_artifact"
            if exc.code == "symlink_rejected"
            else "artifact_path_outside_root"
        )
        reporter.add(
            "INCOMPATIBLE_CONSTRAINTS",
            code,
            workflow_token,
            path=private_path,
            path_kind=path_kind,
            reason=exc.code,
        )
        return (private_path, None)
    if not any(
        os.path.commonpath((os.fspath(root), os.fspath(path))) == os.fspath(root)
        for root in artifact_roots
    ):
        reporter.add(
            "INCOMPATIBLE_CONSTRAINTS",
            "artifact_path_outside_approved_artifact_roots",
            workflow_token,
            path=private_path,
            path_kind=path_kind,
        )
        return (private_path, None)
    observations.setdefault(path, artifact_path_snapshot(path))
    try:
        reject_symlink_chain(path)
        metadata = path.lstat()
    except InspectionError as exc:
        reporter.add(
            "INCOMPATIBLE_CONSTRAINTS",
            "missing_or_unsafe_artifact",
            workflow_token,
            path=private_path,
            path_kind=path_kind,
            reason=exc.code,
        )
        return (private_path, None)
    if not stat.S_ISREG(metadata.st_mode):
        reporter.add(
            "INCOMPATIBLE_CONSTRAINTS",
            "artifact_not_regular_file",
            workflow_token,
            path=private_path,
            path_kind=path_kind,
        )
        return (private_path, None)
    if metadata.st_nlink != 1:
        reporter.add(
            "INCOMPATIBLE_CONSTRAINTS",
            "artifact_hardlink_rejected",
            workflow_token,
            path=private_path,
            path_kind=path_kind,
            link_count=metadata.st_nlink,
        )
    reporter.info(
        "artifact_metadata",
        workflow_token,
        path=private_path,
        path_kind=path_kind,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        size=metadata.st_size,
        file_type=stat.S_IFMT(metadata.st_mode),
    )
    return (private_path, (metadata.st_dev, metadata.st_ino))


def graph_checks(
    rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
    uploads: Sequence[Mapping[str, str]],
    rag_records: Sequence[Mapping[str, Any]],
    owner_registry: Mapping[str, Sequence[str]],
    offline_root: Path,
    artifact_roots: Sequence[Path],
    reporter: Reporter,
) -> None:
    registry_sets, registry_collisions = owner_sets(owner_registry)

    def classify_sanitized_owner(
        owner: object, source_kind: str, source_identifier: object
    ) -> None:
        classification = classify_owner(owner, registry_sets, registry_collisions)
        if classification == "active-owner":
            return
        source_token = reporter.private_token(source_kind, source_identifier)
        details = {
            "source_kind": source_kind,
            "owner": reporter.private_token("owner", normalize_owner(owner)),
            "classification": classification,
        }
        reporter.add(
            "INCOMPATIBLE_CONSTRAINTS",
            "sanitized_owner_classification",
            source_token,
            **details,
        )

    for upload in uploads:
        classify_sanitized_owner(upload["owner"], "upload-id", upload["upload_id"])
    for rag in rag_records:
        classify_sanitized_owner(rag["owner"], "rag-chunk-id", rag["chunk_id"])

    upload_by_id = _index_unique_records(uploads, "upload_id", "upload-id", reporter)
    rag_by_chunk = _index_unique_records(rag_records, "chunk_id", "rag-chunk-id", reporter)
    del rag_by_chunk
    documents = {
        str(row["id"]): row for row in rows_by_table.get("documents", [])
    }
    workflows: dict[str, Mapping[str, Any]] = {}
    workflow_tables: dict[str, str] = {}
    rag_workflow_ids = {str(record["call_or_video_id"]) for record in rag_records}
    for table in GRAPH_PATH_COLUMNS:
        for row in rows_by_table.get(table, []):
            workflow_id = str(row["id"])
            if workflow_id in workflows:
                reporter.add(
                    "INCOMPATIBLE_CONSTRAINTS",
                    "duplicate_workflow_identifier",
                    reporter.private_token("workflow", workflow_id),
                )
            workflows[workflow_id] = row
            workflow_tables[workflow_id] = table

    path_uses: dict[str, tuple[str, str]] = {}
    inode_uses: dict[tuple[int, int], tuple[str, str]] = {}
    artifact_observations: dict[Path, dict[str, Any]] = {}

    def record_path(token: object, workflow_id: str, owner: object, kind: str) -> None:
        workflow_token = reporter.private_token("workflow", workflow_id)
        result = _artifact_metadata(
            offline_root,
            artifact_roots,
            token,
            workflow_token,
            kind,
            reporter,
            artifact_observations,
        )
        if result is None:
            return
        path_token, inode = result
        owner_value = normalize_owner(owner)
        prior = path_uses.get(path_token)
        if prior and prior != (workflow_id, owner_value):
            reporter.add(
                "INCOMPATIBLE_CONSTRAINTS",
                "conflicting_path_reuse",
                workflow_token,
                path=path_token,
                prior_workflow=reporter.private_token("workflow", prior[0]),
            )
        else:
            path_uses[path_token] = (workflow_id, owner_value)
        if inode is not None:
            prior_inode = inode_uses.get(inode)
            if prior_inode and prior_inode != (workflow_id, owner_value):
                reporter.add(
                    "INCOMPATIBLE_CONSTRAINTS",
                    "conflicting_inode_reuse",
                    workflow_token,
                    inode=reporter.private_token("inode", f"{inode[0]}:{inode[1]}"),
                    prior_workflow=reporter.private_token("workflow", prior_inode[0]),
                )
            else:
                inode_uses[inode] = (workflow_id, owner_value)

    for workflow_id, row in workflows.items():
        table = workflow_tables[workflow_id]
        workflow_token = reporter.private_token("workflow", workflow_id)
        owner = row.get("owner")
        upload_id = row.get("upload_id")
        upload = upload_by_id.get(str(upload_id)) if upload_id else None
        if upload is None:
            reporter.add(
                "INCOMPATIBLE_CONSTRAINTS",
                "missing_upload_reference",
                workflow_token,
                upload=reporter.private_token("upload-id", upload_id),
            )
        else:
            _owner_link(owner, upload["owner"], "upload", workflow_token, reporter)
            record_path(
                upload["approved_relative_path_token"], workflow_id, owner, "approved-upload-path"
            )
        for column in ("document_id", "summary_document_id"):
            document_id = row.get(column)
            if not document_id:
                reporter.add(
                    "INCOMPATIBLE_CONSTRAINTS",
                    "missing_document_reference",
                    workflow_token,
                    reference_kind=column,
                )
                continue
            document = documents.get(str(document_id))
            if document is None:
                reporter.add(
                    "INCOMPATIBLE_CONSTRAINTS",
                    "orphaned_document_reference",
                    workflow_token,
                    reference_kind=column,
                    document=reporter.private_token("document-id", document_id),
                )
            else:
                _owner_link(owner, document.get("owner"), column, workflow_token, reporter)
        for column in GRAPH_PATH_COLUMNS[table]:
            record_path(row.get(column), workflow_id, owner, column)
        if row.get("rag_markdown_path") and workflow_id not in rag_workflow_ids:
            reporter.add(
                "INCOMPATIBLE_CONSTRAINTS",
                "missing_rag_metadata",
                workflow_token,
            )

    for record in rag_records:
        workflow_id = str(record["call_or_video_id"])
        workflow = workflows.get(workflow_id)
        rag_token = reporter.private_token("rag-chunk-id", record["chunk_id"])
        if workflow is None:
            reporter.add(
                "INCOMPATIBLE_CONSTRAINTS",
                "orphaned_rag_workflow_reference",
                rag_token,
                workflow=reporter.private_token("workflow", workflow_id),
            )
            continue
        workflow_token = reporter.private_token("workflow", workflow_id)
        _owner_link(workflow.get("owner"), record["owner"], "rag", workflow_token, reporter)
        if str(workflow.get("upload_id") or "") != str(record["upload_id"]):
            reporter.add(
                "INCOMPATIBLE_CONSTRAINTS",
                "rag_upload_mismatch",
                rag_token,
                workflow=workflow_token,
            )
        expected_documents = {
            str(workflow.get("document_id") or ""),
            str(workflow.get("summary_document_id") or ""),
        } - {""}
        actual_documents = {str(value) for value in record["document_ids"]}
        if actual_documents != expected_documents:
            reporter.add(
                "INCOMPATIBLE_CONSTRAINTS",
                "rag_document_mismatch",
                rag_token,
                workflow=workflow_token,
                expected_count=len(expected_documents),
                actual_count=len(actual_documents),
            )
        record_path(record["source_path_token"], workflow_id, workflow.get("owner"), "rag-source-path")

    referenced_uploads = {str(row.get("upload_id")) for row in workflows.values() if row.get("upload_id")}
    for upload_id in set(upload_by_id) - referenced_uploads:
        reporter.add(
            "INCOMPATIBLE_CONSTRAINTS",
            "orphaned_upload_metadata",
            reporter.private_token("upload-id", upload_id),
        )

    for path, before in artifact_observations.items():
        after = artifact_path_snapshot(path)
        if mutation_relevant_snapshot(before) != mutation_relevant_snapshot(after):
            raise InspectionError(
                "artifact metadata changed during inspection",
                "concurrent_artifact_metadata_change",
            )


def inspect(
    database: Path,
    expected_sha256: str,
    manifest: Mapping[str, Any],
    owner_registry: Mapping[str, Sequence[str]],
    uploads: Sequence[Mapping[str, str]],
    rag_records: Sequence[Mapping[str, Any]],
    offline_root: Path,
    artifact_roots: Sequence[Path],
    reporter: Reporter,
) -> None:
    before = database_snapshot(database)
    if not hmac.compare_digest(before["sha256"], expected_sha256.lower()):
        raise InspectionError("database SHA-256 does not match supplied value", "hash_mismatch")
    connection = open_read_only(database, manifest)
    try:
        version_before = schema_version(connection)
        objects_before = object_inventory(connection)
        schema = collect_schema(connection, manifest)
        compare_schema(schema, manifest, reporter)
        rows_by_table = inventory_owners(
            connection, schema, manifest, owner_registry, reporter
        )
        graph_checks(
            rows_by_table,
            uploads,
            rag_records,
            owner_registry,
            offline_root,
            artifact_roots,
            reporter,
        )
        objects_after = object_inventory(connection)
        version_after = schema_version(connection)
        if version_before != version_after or objects_before != objects_after:
            raise InspectionError("SQLite schema metadata changed during inspection", "concurrent_schema_change")
    finally:
        connection.close()
    after = database_snapshot(database)
    if mutation_relevant_snapshot(before) != mutation_relevant_snapshot(after):
        raise InspectionError("database or containing directory changed during inspection", "non_mutation_check_failed")
    reporter.info(
        "non_mutation_verified",
        "offline_database",
        sha256=before["sha256"],
        size=before["size"],
        schema_version=version_before,
        object_count=len(objects_before),
        access_time_compared=False,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("--offline-root", required=True)
    parser.add_argument("--database", required=True, help="relative path below --offline-root")
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--owner-registry", help="relative sanitized JSON path below --offline-root")
    parser.add_argument("--upload-manifest", help="relative sanitized JSON path below --offline-root")
    parser.add_argument("--rag-manifest", help="relative sanitized JSON path below --offline-root")
    parser.add_argument(
        "--artifact-root",
        action="append",
        default=[],
        help="relative artifact root below --offline-root; repeat as needed",
    )
    parser.add_argument(
        "--controlled-disposable-fixture",
        action="store_true",
        help="required while the checked-in manifest blocks real-database use",
    )
    return parser


def _metadata_path(root: Path, value: str | None, label: str) -> Path | None:
    return relative_under(root, value, label=label) if value else None


def run(argv: Sequence[str] | None = None) -> tuple[int, dict[str, Any]]:
    reporter = Reporter()
    manifest: dict[str, Any] | None = None
    try:
        args = build_parser().parse_args(argv)
        if not args.controlled_disposable_fixture:
            raise InspectionError(
                "real and sanitized offline database use remains blocked pending raw-SQL review",
                "real_database_use_blocked",
            )
        if not re.fullmatch(r"[0-9a-fA-F]{64}", args.expected_sha256):
            raise InspectionError("expected SHA-256 must contain exactly 64 hexadecimal characters", "invalid_hash")
        root = approved_root(args.offline_root)
        database = relative_under(root, args.database, label="database")
        manifest_path = _absolute_without_resolution(
            Path(__file__).with_name("schema_contracts") / MANIFEST_BASENAME
        )
        owner_registry_path = require_sanitized_filename(
            _metadata_path(root, args.owner_registry, "owner registry"),
            "owner registry",
        )
        upload_manifest_path = require_sanitized_filename(
            _metadata_path(root, args.upload_manifest, "upload manifest"),
            "upload manifest",
        )
        rag_manifest_path = require_sanitized_filename(
            _metadata_path(root, args.rag_manifest, "RAG manifest"),
            "RAG manifest",
        )
        inspected_metadata_paths = [
            path
            for path in (
                manifest_path,
                owner_registry_path,
                upload_manifest_path,
                rag_manifest_path,
            )
            if path is not None
        ]
        metadata_before = {
            path: metadata_file_snapshot(path) for path in inspected_metadata_paths
        }
        manifest = load_manifest(manifest_path)
        owner_registry = validate_owner_registry(
            load_owner_registry(owner_registry_path), manifest
        )
        uploads = load_upload_manifest(upload_manifest_path)
        rag_records = load_rag_manifest(rag_manifest_path)
        artifact_roots = [
            relative_under(root, value, label="artifact root") for value in args.artifact_root
        ]
        for artifact_root in artifact_roots:
            if not stat.S_ISDIR(artifact_root.lstat().st_mode):
                raise InspectionError(
                    "approved artifact roots must be directories",
                    "artifact_root_not_directory",
                )
        artifact_roots_before = {
            path: artifact_path_snapshot(path) for path in artifact_roots
        }
        if (uploads or rag_records) and not artifact_roots:
            raise InspectionError("metadata paths require at least one approved artifact root", "missing_artifact_root")
        inspect(
            database,
            args.expected_sha256,
            manifest,
            owner_registry,
            uploads,
            rag_records,
            root,
            artifact_roots,
            reporter,
        )
        for path, before in metadata_before.items():
            after = metadata_file_snapshot(path)
            if mutation_relevant_snapshot(before) != mutation_relevant_snapshot(after):
                raise InspectionError(
                    "sanitized metadata changed during inspection",
                    "concurrent_sanitized_metadata_change",
                )
        for path, before in artifact_roots_before.items():
            after = artifact_path_snapshot(path)
            if mutation_relevant_snapshot(before) != mutation_relevant_snapshot(after):
                raise InspectionError(
                    "artifact root changed during inspection",
                    "concurrent_artifact_root_change",
                )
    except Exception as exc:
        code = exc.code if isinstance(exc, InspectionError) else "read_error"
        reporter.add("READ_ERROR", code, "inspection")
    result = reporter.document(manifest)
    successful = result["aggregate_outcome"] in {
        "EXACT_MATCH",
        "COMPATIBLE_ADDITIVE_DIFFERENCE",
    }
    return (0 if successful else 2), result


def main(argv: Sequence[str] | None = None) -> int:
    status, result = run(argv)
    json.dump(result, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
